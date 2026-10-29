import hashlib
import hmac
import json
import re
import secrets as random_secrets
import time
from collections.abc import Callable
from functools import partial, wraps
from typing import (
    Annotated,
    Any,
    Concatenate,
    Literal,
    TypedDict,
)
from uuid import uuid4

from fastapi import APIRouter, Query, Request
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator
from sqlalchemy import and_, case, func, select

from .access import (
    is_admin,
    require_access,
    require_workloads,
    run_workloads,
)
from .configuration import configuration_policy, effective_config, validate_references
from .database import (
    artifacts,
    audit,
    audits,
    configuration,
    environments,
    execution_credential_bindings,
    grants,
    locks,
    repositories,
    row,
    runs,
    scheduler_health,
    secrets,
    tokens,
    webhooks,
    workers,
)
from .errors import ComposerError
from .github import GitHubClient, repository_properties
from .integrations import SecretReferenceError, SecretSourceError
from .lifecycle import TERMINAL, phases
from .repositories import installation_event, synchronize, synchronize_user
from .repository_filter import FILTER_ID, filtered, get_filter
from .scheduling import requested_supervision

router = APIRouter(prefix="/api/v1")
platform_router = APIRouter(tags=["Platform"])
repositories_router = APIRouter(tags=["Repositories"])
configuration_router = APIRouter(tags=["Configuration"])
runs_router = APIRouter(tags=["Runs"])
tokens_router = APIRouter(tags=["API tokens"])
webhooks_router = APIRouter(tags=["GitHub webhooks"])
execution_router = APIRouter(tags=["Execution workers"])
RESERVED_ENV = {
    "PATH",
    "HOME",
    "PYTHONPATH",
    "PYTHONHOME",
    "LD_PRELOAD",
    "GIT_CONFIG",
    "GIT_CONFIG_GLOBAL",
    "GIT_CONFIG_SYSTEM",
}


class Input(BaseModel):
    """Reject unrecognized public API inputs."""

    model_config = ConfigDict(extra="forbid")


def _relative_gitops_root(value: str) -> str:
    value = value.strip()
    normalized = value.removeprefix("./").strip("/")
    if value.startswith("/") or ".." in normalized.split("/") or "\\" in value:
        raise ValueError("GitOps root must stay inside the repository")
    return "" if normalized == "." else normalized


class DeploymentInput(Input):
    """Administrator consent for deployment processing."""

    enabled: bool


class DeploymentDiscoveryInput(Input):
    """Repository deployment discovery options."""

    gitops_root: str | None = None
    discovery_mode: Literal["auto", "single", "folders", "flat-files", "env-files"] = (
        "auto"
    )

    @field_validator("gitops_root")
    @classmethod
    def _relative_root(cls, value):
        return _relative_gitops_root(value) if value is not None else None


class DeploymentConfigurationInput(Input):
    """Reviewed repository deployment configuration."""

    revision: str = Field(pattern=r"^[0-9a-f]{40}$")
    gitops_root: str = ""
    discovery_mode: Literal["auto", "single", "folders", "flat-files", "env-files"] = (
        "auto"
    )
    lock_key: str = Field(default="", max_length=200)
    locked: bool
    automatic: bool
    deactivate_missing: bool = False

    @field_validator("gitops_root")
    @classmethod
    def _relative_root(cls, value):
        return _relative_gitops_root(value)


class ConfigurationInput(Input):
    """Approved ordered layers and inheritance policy."""

    mode: Literal["inherit", "append", "replace"] = "inherit"
    refs: list[str] = Field(default_factory=list, max_length=32)


class RepositoryFilterInput(Input):
    """Repository properties and topics accepted by discovery."""

    properties: dict[str, str] = Field(default_factory=dict, max_length=32)
    topics: list[str] = Field(default_factory=list, max_length=32)


class EnvironmentInput(Input):
    """Environment discovery and deployment settings."""

    name: str = Field(default="default", pattern=r"^[A-Za-z0-9][A-Za-z0-9_-]{0,63}$")
    gitops_root: str = "."
    discovery_mode: Literal["auto", "single", "folders", "flat-files", "env-files"] = (
        "auto"
    )
    lock_key: str = Field(default="", max_length=200)
    locked: bool = True
    automatic: bool = True

    @model_validator(mode="after")
    def _single_deployment_name(self):
        if self.discovery_mode == "single" and self.name != "default":
            raise ValueError("Single deployment mode does not use a named environment.")
        return self

    @field_validator("gitops_root")
    @classmethod
    def _relative_root(cls, value):
        return _relative_gitops_root(value)


class GrantInput(Input):
    """Explicit repository or environment role assignment."""

    login: str = Field(pattern=r"^[A-Za-z0-9-]{1,39}(?:\[bot\])?$")
    environment: str = "*"
    role: Literal["viewer", "runner", "provisioner"]
    workloads: list[Literal["infrastructure", "operations"]] = Field(
        default_factory=list
    )

    @model_validator(mode="after")
    def _role_workloads(self):
        if (self.role == "viewer") != (not self.workloads):
            raise ValueError(
                "Viewers have no workloads; runners and provisioners require a workload."
            )
        self.workloads = sorted(set(self.workloads))
        return self


class SecretInput(Input):
    """Write-only secret or deployment-managed reference."""

    name: str = Field(pattern=r"^[A-Z][A-Z0-9_]{0,100}$")
    phases: list[
        Literal["prepare", "plan", "plan-operation", "apply", "destroy", "operation"]
    ]
    value: str | None = Field(default=None, max_length=65536)
    reference: str | None = Field(default=None, min_length=1, max_length=2048)
    json_key: str | None = Field(default=None, min_length=1, max_length=512)


class ExecutionCredentialBindingInput(Input):
    """Bind one administrator-approved provider to execution phases."""

    provider: str = Field(pattern=r"^[a-z][a-z0-9_-]{0,63}$")
    phases: list[
        Literal["prepare", "plan", "plan-operation", "apply", "destroy", "operation"]
    ] = Field(min_length=1)

    @model_validator(mode="after")
    def _normalize_phases(self):
        self.phases = sorted(set(self.phases))
        return self


class RunInput(Input):
    """A reproducible lifecycle request against a committed revision."""

    repository: str
    environment: str = "default"
    revision: str | None = Field(default=None, pattern=r"^[0-9a-f]{40}$")
    allow_local_backend: bool = False
    action: Literal[
        "plan", "apply", "destroy", "destroy-plan", "plan-operation", "apply-operation"
    ]
    include_infrastructure: bool = False
    include_operations: bool = False
    apply_after_plan: bool = False
    destroy_plan: bool = False
    operations: list[str] = Field(default_factory=list, max_length=100)
    tags: list[str] = Field(default_factory=list, max_length=100)

    @model_validator(mode="after")
    def _phase_selection(self):
        """Validate selected phases and support legacy action-only requests."""
        selections = {
            "include_infrastructure",
            "include_operations",
            "apply_after_plan",
            "destroy_plan",
        }
        if not selections.intersection(self.model_fields_set):
            legacy = {
                "plan": (True, True, False, False),
                "apply": (True, True, True, False),
                "destroy": (True, True, True, True),
                "destroy-plan": (True, True, False, True),
                "plan-operation": (False, True, False, False),
                "apply-operation": (False, True, True, False),
            }[self.action]
            (
                self.include_infrastructure,
                self.include_operations,
                self.apply_after_plan,
                self.destroy_plan,
            ) = legacy
        expected = (
            "destroy"
            if self.destroy_plan and self.apply_after_plan
            else "destroy-plan"
            if self.destroy_plan
            else "apply"
            if self.apply_after_plan and self.include_infrastructure
            else "apply-operation"
            if self.apply_after_plan
            else "plan"
            if self.include_infrastructure
            else "plan-operation"
        )
        if not (self.include_infrastructure or self.include_operations):
            raise ValueError("Select infrastructure planning or operations.")
        if self.destroy_plan and not self.include_infrastructure:
            raise ValueError("Destroy plans require infrastructure planning.")
        if self.action != expected:
            raise ValueError("Action does not match selected phases.")
        if self.operations and not self.include_operations:
            raise ValueError("Operation names require operation planning.")
        if self.tags and not self.include_infrastructure:
            raise ValueError("Component tags require infrastructure planning.")
        if self.allow_local_backend and (self.apply_after_plan or self.destroy_plan):
            raise ValueError("Local backend testing is limited to preview plans.")
        return self

    @property
    def run_options(self) -> dict[str, bool]:
        """Return the selected lifecycle phase flags."""
        return {
            "include_infrastructure": self.include_infrastructure,
            "include_operations": self.include_operations,
            "apply_after_plan": self.apply_after_plan,
            "destroy_plan": self.destroy_plan,
        }

    @field_validator("operations")
    @classmethod
    def _operation_names(cls, values):
        if any(
            not re.fullmatch(r"[A-Za-z0-9_][A-Za-z0-9_.-]{0,99}", value)
            for value in values
        ):
            raise ValueError("Invalid operation name")
        return values

    @field_validator("tags")
    @classmethod
    def _component_tags(cls, values):
        if any("," in value for value in values):
            raise ValueError("Component tags cannot contain commas.")
        return list(dict.fromkeys(value.strip() for value in values if value.strip()))


class TokenInput(Input):
    """Human-owned API token constrained to current GitHub session lifetime."""

    name: str = Field(min_length=1, max_length=100)
    repository: str
    environment: str = "*"
    permissions: list[Literal["view", "plan", "deploy"]]
    workloads: list[Literal["infrastructure", "operations"]] = Field(
        default_factory=list
    )
    expires_in: int = Field(default=3600, ge=60, le=28800)

    @model_validator(mode="after")
    def _token_scope(self):
        if "view" not in self.permissions:
            raise ValueError("Tokens must include read access.")
        if set(self.permissions) & {"plan", "deploy"} and not self.workloads:
            raise ValueError("Execution tokens require workload access.")
        self.permissions = list(dict.fromkeys(self.permissions))
        self.workloads = sorted(set(self.workloads))
        return self


def _enabled(request):
    if not request.app.state.settings.execution_enabled:
        raise ComposerError("Platform execution is disabled.", 503)


class Identity(TypedDict):
    """Authenticated browser or scoped API-token identity."""

    login: str
    github: str
    scope: dict[str, Any] | None
    browser: bool


def _identity(request: Request) -> Identity:
    _enabled(request)
    authorization = request.headers.get("authorization", "")
    if authorization:
        if not authorization.startswith("Bearer "):
            raise ComposerError("Use a Bearer platform token.", 401)
        with request.app.state.database.transaction() as connection:
            token = row(
                connection,
                tokens,
                hashlib.sha256(authorization[7:].encode()).hexdigest(),
            )
        if (
            not token
            or token["revoked"]
            or token["expires"] <= time.time()
            or token["scope"].get("version") != 2
        ):
            raise ComposerError("API token expired or revoked.", 401)
        return {
            "login": token["login"],
            "github": request.app.state.database.decrypt(token["scope"]["credential"]),
            "scope": token["scope"],
            "browser": False,
        }
    session = request.app.state.sessions.get(
        request.cookies.get("platform_session", "")
    )
    if not session:
        raise ComposerError("Sign in with GitHub.", 401)
    return {"login": session[0], "github": session[1], "scope": None, "browser": True}


def _administrator(request: Request, identity: Identity) -> None:
    if not identity["browser"] or identity["login"].lower() not in {
        value.lower() for value in request.app.state.settings.admin_logins
    }:
        raise ComposerError(
            "A platform administrator browser session is required.", 403
        )


def administrator[**P, R](
    endpoint: Callable[Concatenate[Request, P], R],
) -> Callable[Concatenate[Request, P], R]:
    """Require a configured platform administrator browser session.

    Args:
        endpoint: Route handler whose first parameter is a `Request`.

    Returns:
        Route handler guarded by the platform administrator check.

    Raises:
        ComposerError: The caller is not a platform administrator.
    """

    @wraps(endpoint)
    def guarded(request: Request, *args: P.args, **kwargs: P.kwargs) -> R:
        _administrator(request, _identity(request))
        return endpoint(request, *args, **kwargs)

    return guarded


def _repository_access(
    request,
    identity,
    repository,
    environment="*",
    permission="view",
    github_checked=False,
):
    if not re.fullmatch(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+", repository):
        raise ComposerError("Invalid repository name.", 422)
    if not github_checked:
        GitHubClient(
            identity["github"], request.app.state.github_transport
        )._repository(*repository.split("/"))
    if identity["scope"]:
        scope = identity["scope"]
        action, _, workload = permission.partition("_")
        token_permission = {"execute": "deploy", "plan": "plan"}.get(action, permission)
        if (
            scope["repository"] != repository
            or scope["environment"] not in {"*", environment}
            or token_permission not in scope["permissions"]
            or (workload and workload not in scope.get("workloads", []))
        ):
            raise ComposerError("API token does not grant this capability.", 403)
    with request.app.state.database.transaction() as connection:
        return require_access(
            connection,
            request.app.state.settings,
            identity["login"],
            repository,
            environment,
            permission,
        )


def _run_access(request, run_id, permission="view"):
    identity = _identity(request)
    with request.app.state.database.transaction() as connection:
        run = row(connection, runs, run_id)
    if not run:
        raise ComposerError("Run not found.", 404)
    if permission in {"plan", "deploy", "approve", "reconcile"}:
        action = "execute" if permission == "deploy" else permission
        for workload in run_workloads(run["spec"]):
            _repository_access(
                request,
                identity,
                run["repository"],
                run["environment"],
                f"{action}_{workload}",
            )
    else:
        _repository_access(
            request, identity, run["repository"], run["environment"], permission
        )
    return identity, run


def _public_run(run):
    return {
        key: {
            name: value for name, value in run[key].items() if not name.startswith("_")
        }
        if key in {"spec", "result"}
        else run[key]
        for key in (
            "id",
            "repository",
            "environment",
            "revision",
            "action",
            "actor",
            "status",
            "phase",
            "spec",
            "result",
            "created",
            "updated",
            "expires",
        )
    }


def enqueue(
    connection,
    settings,
    repository,
    environment,
    body,
    actor,
    dedup,
    event="manual",
    authorization=False,
    revision_override=False,
    requested_revision=None,
):
    """Create an immutable run request within the caller's transaction.

    Args:
        connection: Active transaction.
        settings: Deployment settings.
        repository: Approved repository record.
        environment: Approved environment record.
        body: Validated run input.
        actor: Initiating identity.
        dedup: Stable delivery or client idempotency key.
        event: Manual, push, or pull-request origin.
        authorization: Whether credentialed PR execution requires approval.
        revision_override: Whether an administrator selected an explicit commit.
        requested_revision: Requested commit before resolving the default branch.

    Returns:
        Existing identical run or newly inserted run.

    Raises:
        ComposerError: An idempotency key was reused for different inputs.
    """
    if not repository["enabled"] or not repository["deployment_enabled"]:
        raise ComposerError("Enable deployments for this repository first.", 409)
    if connection.dialect.name == "postgresql":
        connection.exec_driver_sql(
            "SELECT pg_advisory_xact_lock(hashtext(%s))", (dedup,)
        )
    existing = (
        connection.execute(runs.select().where(runs.c.dedup == dedup))
        .mappings()
        .first()
    )
    if existing:
        if (
            any(
                existing[key] != getattr(body, key)
                for key in ("action", "environment", "repository")
            )
            or (
                existing["spec"].get("_requested_revision", existing["revision"])
                != requested_revision
                if event == "manual"
                else existing["revision"] != body.revision
            )
            or existing["spec"]["operations"] != body.operations
            or existing["spec"].get("tags", []) != body.tags
            or existing["spec"].get("allow_local_backend", False)
            != body.allow_local_backend
            or existing["spec"].get("run_options", body.run_options) != body.run_options
        ):
            raise ComposerError(
                "Idempotency key was already used for different inputs.", 409
            )
        return existing
    require_workloads(
        connection,
        settings,
        actor,
        body.repository,
        body.environment,
        "execute" if body.apply_after_plan else "plan",
        run_workloads({"phases": phases(body.action, **body.run_options)}),
    )
    spec = {
        "config": effective_config(connection, settings, repository),
        "installation": repository["installation"],
        "default_branch": repository["branch"],
        "environment_revision": environment["revision"],
        "settings": environment["settings"],
        "operations": body.operations,
        "tags": body.tags,
        "run_options": body.run_options,
        "command": "destroy" if body.action == "destroy-plan" else body.action,
        "event": event,
        "image": settings.execution_image,
        "phases": phases(body.action, **body.run_options),
        "revision_override": revision_override,
        "allow_local_backend": body.allow_local_backend,
    }
    if event == "manual":
        spec["_requested_revision"] = requested_revision
    run = {
        "id": uuid4().hex,
        "repository": body.repository,
        "environment": body.environment,
        "revision": body.revision,
        "action": body.action,
        "actor": actor,
        "status": "awaiting_authorization" if authorization else "queued",
        "phase": 0,
        "spec": spec,
        "result": {},
        "created": time.time(),
        "updated": time.time(),
        "expires": time.time() + settings.approval_ttl if authorization else None,
        "dedup": dedup,
        "lock_key": environment["settings"].get("lock_key") or body.repository,
        "fence": 0,
        "worker": None,
        "lease": None,
        "execution": None,
    }
    run["supervision"] = requested_supervision(settings, run["status"])
    connection.execute(runs.insert().values(**run))
    audit(
        connection, actor, "run.created", run["id"], revision=body.revision, event=event
    )
    return run


@platform_router.get("/me")
def me(request: Request):
    """Return identity and settings navigation eligibility.

    Args:
        request: Authenticated request.

    Returns:
        Login, administrator status, and provisioner status.
    """
    identity = _identity(request)
    administrator = identity["browser"] and is_admin(
        request.app.state.settings, identity["login"]
    )
    with request.app.state.database.transaction() as connection:
        provisioner = bool(
            connection.execute(
                grants.select().where(
                    and_(
                        grants.c.login == identity["login"].lower(),
                        grants.c.role == "provisioner",
                    )
                )
            ).first()
        )
    return {
        "login": identity["login"],
        "administrator": administrator,
        "provisioner": administrator or provisioner,
    }


@repositories_router.get("/repositories")
def list_repositories(request: Request):
    """List registered repositories visible through GitHub and platform grants.

    Args:
        request: Authenticated request.

    Returns:
        Accessible registered repositories.
    """
    identity = _identity(request)
    if identity["scope"] and "view" not in identity["scope"]["permissions"]:
        return []
    discovered = GitHubClient(
        identity["github"], request.app.state.github_transport
    ).repositories()
    with request.app.state.database.transaction() as connection:
        discovered = filtered(discovered, get_filter(connection))
    synchronize_user(request.app.state.database, discovered)
    visible = {item["name"] for item in discovered}
    with request.app.state.database.transaction() as connection:
        registered = connection.execute(repositories.select()).mappings().all()
        assigned = (
            connection.execute(
                grants.select().where(grants.c.login == identity["login"].lower())
            )
            .mappings()
            .all()
        )
    return [
        dict(item)
        for item in registered
        if item["id"] in visible
        and item["enabled"]
        and (
            identity["login"].lower()
            in {value.lower() for value in request.app.state.settings.admin_logins}
            or any(grant["repository"] == item["id"] for grant in assigned)
        )
        and (not identity["scope"] or identity["scope"]["repository"] == item["id"])
    ]


@repositories_router.get("/repositories/{owner}/{repo}/access")
def repository_access(request: Request, owner: str, repo: str, environment: str = "*"):
    """Return the current user's effective scoped role access.

    Args:
        request: Authenticated request.
        owner: GitHub owner.
        repo: Repository name.
        environment: Selected environment, or shared repository context.

    Returns:
        Applicable assignments and effective capabilities.

    Raises:
        ComposerError: The user cannot view the selected context.
    """
    identity = _identity(request)
    access = _repository_access(request, identity, f"{owner}/{repo}", environment)
    if identity["scope"]:
        access["administrator"] = False
        access["capabilities"] = [
            capability
            for capability in access["capabilities"]
            if capability == "view"
            or (
                capability.partition("_")[0] in {"plan", "execute"}
                and capability.partition("_")[2] in identity["scope"]["workloads"]
                and {"plan": "plan", "execute": "deploy"}[capability.partition("_")[0]]
                in identity["scope"]["permissions"]
            )
        ]
    return access


@repositories_router.delete("/repositories/{owner}/{repo}/grants/{grant_id:path}")
@administrator
def remove_grant(request: Request, owner: str, repo: str, grant_id: str):
    """Revoke a repository role assignment.

    Args:
        request: Administrator browser request.
        owner: GitHub owner.
        repo: Repository name.
        grant_id: Assignment identifier.

    Returns:
        Revocation confirmation.

    Raises:
        ComposerError: The caller is not an administrator or the assignment is missing.
    """
    identity = _identity(request)
    _repository_access(request, identity, f"{owner}/{repo}")
    with request.app.state.database.transaction() as connection:
        grant = row(connection, grants, grant_id)
        if not grant or grant["repository"] != f"{owner}/{repo}":
            raise ComposerError("Role assignment not found.", 404)
        connection.execute(grants.delete().where(grants.c.id == grant_id))
        audit(
            connection,
            identity["login"],
            "role.revoked",
            repository=grant["repository"],
            login=grant["login"],
            environment=grant["environment"],
        )
    return {"revoked": True}


@repositories_router.post("/repositories/refresh")
@administrator
def refresh_repositories(request: Request):
    """Reconcile the complete catalog of repositories granted to this App.

    Args:
        request: Administrator browser request.

    Returns:
        Refreshed repositories visible to the administrator.

    Raises:
        ComposerError: GitHub access or platform administration is denied.
    """
    provider = request.app.state.provider
    catalog = provider.catalog()
    for item in catalog:
        if "topics" in item and "custom_properties" in item:
            continue
        metadata = provider.request(
            item["installation_id"], "GET", f"/repos/{item['full_name']}"
        )
        item["topics"] = metadata.get("topics", [])
        owner, name = item["full_name"].split("/", 1)
        item["custom_properties"] = (
            {}
            if metadata.get("owner", item.get("owner", {})).get("type") == "User"
            else repository_properties(
                partial(provider.request, item["installation_id"]), owner, name
            )
        )
    with request.app.state.database.transaction() as connection:
        filter_settings = get_filter(connection)
    synchronize(
        request.app.state.database,
        filtered(catalog, filter_settings),
        complete=True,
    )
    return list_repositories(request)


@platform_router.get("/repository-filter")
@administrator
def read_repository_filter(request: Request):
    """Return administrator-controlled repository discovery rules."""
    with request.app.state.database.transaction() as connection:
        return get_filter(connection)


@platform_router.put("/repository-filter")
@administrator
def save_repository_filter(request: Request, body: RepositoryFilterInput):
    """Save repository discovery rules for the platform."""
    identity = _identity(request)
    policy = body.model_dump()
    with request.app.state.database.transaction() as connection:
        connection.execute(
            configuration.delete().where(configuration.c.id == FILTER_ID)
        )
        connection.execute(
            configuration.insert().values(
                id=FILTER_ID, policy=policy, updated=time.time()
            )
        )
        audit(connection, identity["login"], "repository_filter.updated")
    return policy


@repositories_router.post("/repositories/{owner}/{repo}/deployment")
@administrator
def repository_deployment(
    request: Request, owner: str, repo: str, body: DeploymentInput
):
    """Enable or disable deployment processing for a discovered repository.

    Args:
        request: Administrator browser request.
        owner: GitHub repository owner.
        repo: GitHub repository name.
        body: Explicit deployment consent.

    Returns:
        Repository identifier and deployment activation state.

    Raises:
        ComposerError: Administration or current GitHub App access is denied.
    """
    identity = _identity(request)
    name = f"{owner}/{repo}"
    _repository_access(request, identity, name)
    installation = request.app.state.provider.repository_installation(name)
    with request.app.state.database.transaction() as connection:
        repository = row(connection, repositories, name)
        if not repository or not repository["enabled"]:
            raise ComposerError(
                "Discover this repository before enabling deployments.", 404
            )
        if (
            body.enabled
            and not connection.execute(
                environments.select().where(
                    and_(
                        environments.c.repository == name,
                        environments.c.active == 1,
                    )
                )
            ).first()
        ):
            raise ComposerError(
                "Configure at least one active deployment environment first.", 409
            )
        connection.execute(
            repositories.update()
            .where(repositories.c.id == name)
            .values(
                installation=installation,
                deployment_enabled=int(body.enabled),
            )
        )
        audit(
            connection,
            identity["login"],
            "repository.deployment_changed",
            repository=name,
            enabled=body.enabled,
        )
    return {"id": name, "deployment_enabled": body.enabled}


def _deployment_repository(request, name):
    provider = request.app.state.provider
    installation = provider.repository_installation(name)
    with request.app.state.database.transaction() as connection:
        repository = row(connection, repositories, name)
    if not repository or not repository["enabled"]:
        raise ComposerError("Discover this repository before configuring it.", 404)
    branch = provider.request(installation, "GET", f"/repos/{name}")["default_branch"]
    return (
        repository,
        installation,
        branch,
        provider.revision({**repository, "installation": installation}, branch)["sha"],
    )


def _inspect_deployments(request, name, revision, installation, body):
    from .inspection import InspectionQueue

    return json.loads(
        InspectionQueue(request.app.state.settings).execute(
            ["platform-deployments"],
            json.dumps(
                {
                    "repository": name,
                    "revision": revision,
                    **body.model_dump(exclude_none=True),
                }
            ),
            request.app.state.provider.installation_token(installation),
            None,
        )
    )


@repositories_router.get("/repositories/{owner}/{repo}/deployment-configuration")
def read_deployment_configuration(request: Request, owner: str, repo: str):
    """Return repository-level deployment configuration and retained environments.

    Args:
        request: Authenticated browser request.
        owner: GitHub repository owner.
        repo: GitHub repository name.

    Returns:
        Deployment activation, uniform settings, and environment states.
    """
    identity = _identity(request)
    name = f"{owner}/{repo}"
    _repository_access(request, identity, name, permission="configure_environment")
    with request.app.state.database.transaction() as connection:
        repository = row(connection, repositories, name)
        records = (
            connection.execute(
                environments.select()
                .where(environments.c.repository == name)
                .order_by(environments.c.name)
            )
            .mappings()
            .all()
        )
    if not repository:
        raise ComposerError("Repository not found.", 404)
    active_settings = [record["settings"] for record in records if record["active"]]
    return {
        "deployment_enabled": bool(repository["deployment_enabled"]),
        "settings": (
            active_settings[0]
            if active_settings
            and all(item == active_settings[0] for item in active_settings)
            else None
        ),
        "inconsistent": len(active_settings) > 1
        and any(item != active_settings[0] for item in active_settings[1:]),
        "environments": [
            {
                "name": record["name"],
                "active": bool(record["active"]),
                "revision": record["revision"],
            }
            for record in records
        ],
    }


@repositories_router.post("/repositories/{owner}/{repo}/deployment-discovery")
def discover_repository_deployments(
    request: Request,
    owner: str,
    repo: str,
    body: DeploymentDiscoveryInput,
):
    """Inspect deployment candidates at the current default-branch revision.

    Args:
        request: Authenticated browser request.
        owner: GitHub repository owner.
        repo: GitHub repository name.
        body: Optional root and discovery-mode override.

    Returns:
        Immutable revision and validated Stacksmith deployment candidates.
    """
    identity = _identity(request)
    name = f"{owner}/{repo}"
    _repository_access(request, identity, name, permission="configure_environment")
    _, installation, _, revision = _deployment_repository(request, name)
    return {
        "revision": revision,
        **_inspect_deployments(request, name, revision, installation, body),
    }


@repositories_router.put("/repositories/{owner}/{repo}/deployment-configuration")
def save_deployment_configuration(
    request: Request,
    owner: str,
    repo: str,
    body: DeploymentConfigurationInput,
):
    """Validate and atomically apply one repository deployment candidate.

    Args:
        request: Authenticated browser request.
        owner: GitHub repository owner.
        repo: GitHub repository name.
        body: Reviewed deployment configuration at an immutable revision.

    Returns:
        Applied environment changes and activation state.

    Raises:
        ComposerError: The repository changed or discovery no longer matches.
    """
    identity = _identity(request)
    name = f"{owner}/{repo}"
    _repository_access(request, identity, name, permission="configure_environment")
    _, installation, branch, revision = _deployment_repository(request, name)
    if revision != body.revision:
        raise ComposerError("Repository changed. Detect deployments again.", 409)
    discovery = _inspect_deployments(
        request,
        name,
        revision,
        installation,
        DeploymentDiscoveryInput(
            gitops_root=body.gitops_root,
            discovery_mode=body.discovery_mode,
        ),
    )
    if len(discovery["deployments"]) != 1:
        raise ComposerError("The selected deployment root is unavailable.", 422)
    candidate = discovery["deployments"][0]
    detected = set(candidate["all_environments"])
    if not detected:
        raise ComposerError("No deployment environments were discovered.", 422)
    settings = {
        "gitops_root": candidate["gitops_root"],
        "discovery_mode": body.discovery_mode,
        "resolved_discovery_mode": candidate["discovery_mode"],
        "detected_revision": revision,
        "lock_key": body.lock_key,
        "locked": body.locked,
        "automatic": body.automatic,
    }
    changes = {"added": [], "reactivated": [], "updated": [], "deactivated": []}
    with request.app.state.database.transaction() as connection:
        records = {
            record["name"]: record
            for record in connection.execute(
                environments.select().where(environments.c.repository == name)
            )
            .mappings()
            .all()
        }
        missing = sorted(
            environment_name
            for environment_name, record in records.items()
            if record["active"] and environment_name not in detected
        )
        if missing and not body.deactivate_missing:
            raise ComposerError(
                "Approve deactivation of missing environments before applying.",
                409,
            )
        for environment_name in sorted(detected):
            identifier = f"{name}:{environment_name}"
            if environment_name not in records:
                connection.execute(
                    environments.insert().values(
                        id=identifier,
                        repository=name,
                        name=environment_name,
                        settings=settings,
                        revision=1,
                        active=1,
                    )
                )
                changes["added"].append(environment_name)
                continue
            record = records[environment_name]
            if not record["active"]:
                changes["reactivated"].append(environment_name)
            elif record["settings"] != settings:
                changes["updated"].append(environment_name)
            if not record["active"] or record["settings"] != settings:
                connection.execute(
                    environments.update()
                    .where(environments.c.id == identifier)
                    .values(
                        settings=settings,
                        revision=record["revision"] + 1,
                        active=1,
                    )
                )
        for environment_name in missing:
            connection.execute(
                environments.update()
                .where(environments.c.id == records[environment_name]["id"])
                .values(
                    active=0,
                    revision=records[environment_name]["revision"] + 1,
                )
            )
            changes["deactivated"].append(environment_name)
        connection.execute(
            repositories.update()
            .where(repositories.c.id == name)
            .values(
                installation=installation,
                branch=branch,
                deployment_enabled=1,
            )
        )
        audit(
            connection,
            identity["login"],
            "deployment.configuration_applied",
            repository=name,
            revision=revision,
            root=candidate["gitops_root"],
            resolved_mode=candidate["discovery_mode"],
            **changes,
        )
    return {
        "id": name,
        "deployment_enabled": True,
        "revision": revision,
        "settings": settings,
        "environments": sorted(detected),
        **changes,
    }


def _save_configuration(request, identity, identifier, body):
    if identifier == "platform" and body.mode != "replace":
        raise ComposerError("Platform defaults require replace mode.", 422)
    if (body.mode == "inherit" and body.refs) or (
        body.mode != "inherit" and not body.refs
    ):
        raise ComposerError(
            "Inheritance has no extra layers; append and replace require at least one layer.",
            422,
        )
    validate_references(body.refs)
    with request.app.state.database.transaction() as connection:
        if identifier != "platform" and not row(connection, repositories, identifier):
            raise ComposerError("Register the repository before configuring it.", 404)
        if connection.dialect.name == "postgresql":
            connection.exec_driver_sql(
                "SELECT pg_advisory_xact_lock(hashtext(%s))",
                (f"configuration:{identifier}",),
            )
        connection.execute(
            configuration.delete().where(configuration.c.id == identifier)
        )
        connection.execute(
            configuration.insert().values(
                id=identifier, policy=body.model_dump(), updated=time.time()
            )
        )
        audit(
            connection,
            identity["login"],
            "configuration.updated",
            scope=identifier,
            mode=body.mode,
            refs=body.refs,
        )
    return {"saved": True}


@configuration_router.get("/configuration")
@administrator
def platform_configuration(request: Request):
    """Read administrator-managed platform defaults.

    Args:
        request: Authenticated administrator browser request.

    Returns:
        Ordered default configuration references and effective layers.

    Raises:
        ComposerError: Administrator access is required.
    """
    with request.app.state.database.transaction() as connection:
        return configuration_policy(connection, request.app.state.settings)


@configuration_router.post("/configuration")
@administrator
def save_platform_configuration(request: Request, body: ConfigurationInput):
    """Replace default platform configuration layers.

    Args:
        request: Authenticated administrator browser request.
        body: Approved immutable configuration references.

    Returns:
        Confirmation that the policy was stored.

    Raises:
        ComposerError: References, mode, or administrator access are invalid.
    """
    identity = _identity(request)
    return _save_configuration(request, identity, "platform", body)


@configuration_router.get("/repositories/{owner}/{repo}/configuration")
def repository_configuration(request: Request, owner: str, repo: str):
    """Read a repository's inheritance policy and effective configuration.

    Args:
        request: Authenticated request.
        owner: GitHub repository owner.
        repo: GitHub repository name.

    Returns:
        Repository policy and the effective ordered layers.

    Raises:
        ComposerError: Repository access is unavailable.
    """
    _repository_access(request, _identity(request), f"{owner}/{repo}")
    with request.app.state.database.transaction() as connection:
        repository = row(connection, repositories, f"{owner}/{repo}")
        if not repository:
            raise ComposerError("Register the repository before configuring it.", 404)
        return configuration_policy(connection, request.app.state.settings, repository)


@configuration_router.post("/repositories/{owner}/{repo}/configuration")
def save_repository_configuration(
    request: Request, owner: str, repo: str, body: ConfigurationInput
):
    """Set repository inheritance, appended layers, or replacement configuration.

    Args:
        request: Authenticated repository administrator request.
        owner: GitHub repository owner.
        repo: GitHub repository name.
        body: Policy and approved immutable layer references.

    Returns:
        Confirmation that the policy was stored.

    Raises:
        ComposerError: References, policy, or administrator access are invalid.
    """
    identity = _identity(request)
    _repository_access(
        request, identity, f"{owner}/{repo}", permission="configure_repository"
    )
    return _save_configuration(request, identity, f"{owner}/{repo}", body)


@repositories_router.get("/repositories/{owner}/{repo}/environments")
def list_environments(request: Request, owner: str, repo: str):
    """List configured environments for an accessible repository.

    Args:
        request: Authenticated request.
        owner: GitHub owner.
        repo: Repository name.

    Returns:
        Environment records visible to the current principal.
    """
    identity = _identity(request)
    with request.app.state.database.transaction() as connection:
        records = (
            connection.execute(
                environments.select().where(
                    and_(
                        environments.c.repository == f"{owner}/{repo}",
                        environments.c.active == 1,
                    )
                )
            )
            .mappings()
            .all()
        )
    visible = []
    for record in records:
        try:
            access = _repository_access(
                request, identity, f"{owner}/{repo}", record["name"]
            )
        except ComposerError as exc:
            if exc.status != 403:
                raise
        else:
            visible.append(
                {
                    **dict(record),
                    "can_inspect": "plan_infrastructure" in access["capabilities"],
                }
            )
    return visible


@repositories_router.get("/repositories/{owner}/{repo}/environments/{name}/operations")
def environment_operations(request: Request, owner: str, repo: str, name: str):
    """Inspect operation names declared in the current default-branch stacks.

    Args:
        request: Authenticated request.
        owner: GitHub owner.
        repo: Repository name.
        name: Deployment environment.

    Returns:
        Stack-local operation choices and the inspected commit revision.

    Raises:
        ComposerError: Access, repository discovery, or inspection fails.
    """
    from .inspection import InspectionQueue

    identity = _identity(request)
    _repository_access(request, identity, f"{owner}/{repo}", name)
    with request.app.state.database.transaction() as connection:
        repository = row(connection, repositories, f"{owner}/{repo}")
        environment = row(connection, environments, f"{owner}/{repo}:{name}")
    if not repository or not environment or not environment["active"]:
        raise ComposerError("Register the repository and environment first.", 404)
    provider = request.app.state.provider
    installation = provider.repository_installation(repository["id"])
    branch = provider.request(installation, "GET", f"/repos/{repository['id']}")[
        "default_branch"
    ]
    return json.loads(
        InspectionQueue(request.app.state.settings).execute(
            ["platform-operations"],
            json.dumps(
                {
                    "repository": repository["id"],
                    "revision": provider.revision(
                        {**repository, "installation": installation}, branch
                    )["sha"],
                    "environment": name,
                    "settings": environment["settings"],
                }
            ),
            provider.installation_token(installation),
            None,
        )
    )


@repositories_router.post("/repositories/{owner}/{repo}/environments")
def configure_environment(
    request: Request, owner: str, repo: str, body: EnvironmentInput
):
    """Create or replace administrator-controlled discovery settings.

    Args:
        request: Authenticated browser request.
        owner: GitHub owner.
        repo: Repository name.
        body: Environment settings.

    Returns:
        Environment identifier.
    """
    identity = _identity(request)
    _repository_access(
        request, identity, f"{owner}/{repo}", body.name, "configure_environment"
    )
    with request.app.state.database.transaction() as connection:
        identifier = f"{owner}/{repo}:{body.name}"
        previous = row(connection, environments, identifier)
        if not row(connection, repositories, f"{owner}/{repo}"):
            raise ComposerError("Register the repository first.", 409)
        connection.execute(environments.delete().where(environments.c.id == identifier))
        connection.execute(
            environments.insert().values(
                id=identifier,
                repository=f"{owner}/{repo}",
                name=body.name,
                settings=body.model_dump(exclude={"name"}),
                revision=previous["revision"] + 1 if previous else 1,
                active=1,
            )
        )
        audit(
            connection,
            identity["login"],
            "environment.configured",
            environment=identifier,
        )
    return {"id": identifier}


@repositories_router.get("/repositories/{owner}/{repo}/grants")
@administrator
def list_grants(request: Request, owner: str, repo: str):
    """List role assignments for a platform administrator.

    Args:
        request: Authenticated browser request.
        owner: GitHub owner.
        repo: Repository name.

    Returns:
        Repository role assignments.

    Raises:
        ComposerError: The user is not a platform administrator.
    """
    identity = _identity(request)
    _repository_access(request, identity, f"{owner}/{repo}")
    with request.app.state.database.transaction() as connection:
        return [
            dict(item)
            for item in connection.execute(
                grants.select().where(grants.c.repository == f"{owner}/{repo}")
            ).mappings()
        ]


@repositories_router.post("/repositories/{owner}/{repo}/grants")
@administrator
def assign_grant(request: Request, owner: str, repo: str, body: GrantInput):
    """Assign a scoped stack role.

    Args:
        request: Administrator browser request.
        owner: GitHub owner.
        repo: Repository name.
        body: Role, workload access, and environment scope.

    Returns:
        Assignment identifier.

    Raises:
        ComposerError: Administration access, repository, or environment is invalid.
    """
    identity = _identity(request)
    _repository_access(request, identity, f"{owner}/{repo}")
    with request.app.state.database.transaction() as connection:
        if not row(connection, repositories, f"{owner}/{repo}"):
            raise ComposerError("Register this repository first.", 404)
        if body.environment != "*" and not row(
            connection, environments, f"{owner}/{repo}:{body.environment}"
        ):
            raise ComposerError("Environment not found.", 404)
        identifier = f"{owner}/{repo}:{body.environment}:{body.login.lower()}"
        connection.execute(grants.delete().where(grants.c.id == identifier))
        connection.execute(
            grants.insert().values(
                id=identifier,
                login=body.login.lower(),
                repository=f"{owner}/{repo}",
                environment=body.environment,
                role=body.role,
                workloads=body.workloads,
            )
        )
        audit(
            connection,
            identity["login"],
            "role.assigned",
            repository=f"{owner}/{repo}",
            login=body.login.lower(),
            environment=body.environment,
            role=body.role,
            workloads=body.workloads,
        )
    return {"id": identifier}


@repositories_router.get("/repositories/{owner}/{repo}/environments/{name}/secrets")
def list_secrets(request: Request, owner: str, repo: str, name: str):
    """List secret binding metadata without ciphertext or values.

    Args:
        request: Authenticated administrator request.
        owner: GitHub owner.
        repo: Repository name.
        name: Environment name.

    Returns:
        Secret names, phase scopes, and reference labels.
    """
    _repository_access(
        request, _identity(request), f"{owner}/{repo}", name, "manage_credentials"
    )
    with request.app.state.database.transaction() as connection:
        return [
            dict(item)
            for item in connection.execute(
                secrets.select()
                .with_only_columns(
                    secrets.c.id,
                    secrets.c.name,
                    secrets.c.phases,
                    secrets.c.reference,
                    secrets.c.json_key,
                )
                .where(secrets.c.environment == f"{owner}/{repo}:{name}")
            ).mappings()
        ]


@repositories_router.get(
    "/repositories/{owner}/{repo}/environments/{name}/secret-references"
)
def list_secret_references(request: Request, owner: str, repo: str, name: str):
    """List configured deployment secret names without their values.

    Args:
        request: Authenticated credential manager request.
        owner: GitHub owner.
        repo: Repository name.
        name: Environment name.

    Returns:
        Sorted deployment secret reference names.

    Raises:
        ComposerError: If access is denied or the environment does not exist.
    """
    _repository_access(
        request, _identity(request), f"{owner}/{repo}", name, "manage_credentials"
    )
    with request.app.state.database.transaction() as connection:
        if not row(connection, environments, f"{owner}/{repo}:{name}"):
            raise ComposerError("Environment not found.", 404)
    try:
        return {"references": request.app.state.secret_catalog.discover()}
    except SecretSourceError:
        raise ComposerError(
            "Deployment secret discovery is unavailable. Retry or check source configuration.",
            503,
        ) from None


@repositories_router.get(
    "/repositories/{owner}/{repo}/environments/{name}/execution-credential-providers"
)
def list_execution_credential_providers(
    request: Request, owner: str, repo: str, name: str
):
    """List deployment-approved execution credential provider names.

    Args:
        request: Authenticated credential manager request.
        owner: GitHub owner.
        repo: Repository name.
        name: Environment name.

    Returns:
        Public provider names and types without configuration or secrets.

    Raises:
        ComposerError: Access is denied or the environment does not exist.
    """
    _repository_access(
        request, _identity(request), f"{owner}/{repo}", name, "manage_credentials"
    )
    with request.app.state.database.transaction() as connection:
        if not row(connection, environments, f"{owner}/{repo}:{name}"):
            raise ComposerError("Environment not found.", 404)
    return {"providers": request.app.state.execution_credential_catalog.list()}


@repositories_router.get(
    "/repositories/{owner}/{repo}/environments/{name}/execution-credential-bindings"
)
def list_execution_credential_bindings(
    request: Request, owner: str, repo: str, name: str
):
    """List an environment's execution credential provider bindings.

    Args:
        request: Authenticated credential manager request.
        owner: GitHub owner.
        repo: Repository name.
        name: Environment name.

    Returns:
        Provider names and phase scopes.

    Raises:
        ComposerError: Access is denied or the environment does not exist.
    """
    _repository_access(
        request, _identity(request), f"{owner}/{repo}", name, "manage_credentials"
    )
    with request.app.state.database.transaction() as connection:
        if not row(connection, environments, f"{owner}/{repo}:{name}"):
            raise ComposerError("Environment not found.", 404)
        return {
            "bindings": [
                {"provider": item["provider"], "phases": item["phases"]}
                for item in connection.execute(
                    execution_credential_bindings.select()
                    .where(
                        execution_credential_bindings.c.environment
                        == f"{owner}/{repo}:{name}"
                    )
                    .order_by(execution_credential_bindings.c.provider)
                ).mappings()
            ]
        }


@repositories_router.post(
    "/repositories/{owner}/{repo}/environments/{name}/execution-credential-bindings"
)
def bind_execution_credential(
    request: Request,
    owner: str,
    repo: str,
    name: str,
    body: ExecutionCredentialBindingInput,
):
    """Bind an approved provider and invalidate pending environment approvals.

    Args:
        request: Authenticated credential manager request.
        owner: GitHub owner.
        repo: Repository name.
        name: Environment name.
        body: Provider and phase scope.

    Returns:
        The stored provider and phase scope.

    Raises:
        ComposerError: Access, provider, environment, or phase scope is invalid.
    """
    identity = _identity(request)
    _repository_access(request, identity, f"{owner}/{repo}", name, "manage_credentials")
    if body.provider not in request.app.state.execution_credential_catalog.providers:
        raise ComposerError("Execution credential provider is unavailable.", 422)
    with request.app.state.database.transaction() as connection:
        environment = (
            connection.execute(
                environments.select()
                .where(environments.c.id == f"{owner}/{repo}:{name}")
                .with_for_update()
            )
            .mappings()
            .first()
        )
        if not environment:
            raise ComposerError("Environment not found.", 404)
        forbidden = request.app.state.execution_credential_catalog.providers[
            body.provider
        ].conflicting_variables
        if forbidden & set(request.app.state.settings.run_environment) or any(
            item["name"] in forbidden and set(item["phases"]) & set(body.phases)
            for item in connection.execute(
                secrets.select().where(secrets.c.environment == environment["id"])
            ).mappings()
        ):
            raise ComposerError(
                "Environment variables conflict with the execution credential provider.",
                422,
            )
        for binding in connection.execute(
            execution_credential_bindings.select().where(
                execution_credential_bindings.c.environment == environment["id"]
            )
        ).mappings():
            if binding["provider"] != body.provider and set(binding["phases"]) & set(
                body.phases
            ):
                raise ComposerError(
                    "Only one execution credential provider may be bound to a phase.",
                    422,
                )
        identifier = f"{environment['id']}:{body.provider}"
        connection.execute(
            execution_credential_bindings.delete().where(
                execution_credential_bindings.c.id == identifier
            )
        )
        connection.execute(
            execution_credential_bindings.insert().values(
                id=identifier,
                environment=environment["id"],
                provider=body.provider,
                phases=body.phases,
            )
        )
        connection.execute(
            environments.update()
            .where(environments.c.id == environment["id"])
            .values(revision=environment["revision"] + 1)
        )
        audit(
            connection,
            identity["login"],
            "execution_credential.bound",
            environment=environment["id"],
            provider=body.provider,
        )
    return {"provider": body.provider, "phases": body.phases}


@repositories_router.post(
    "/repositories/{owner}/{repo}/environments/{name}/execution-credential-bindings/{provider}/remove"
)
def remove_execution_credential(
    request: Request, owner: str, repo: str, name: str, provider: str
):
    """Revoke a provider binding and invalidate pending approvals.

    Args:
        request: Authenticated credential manager request.
        owner: GitHub owner.
        repo: Repository name.
        name: Environment name.
        provider: Bound provider name.

    Returns:
        Revocation confirmation.

    Raises:
        ComposerError: Access is denied or the environment does not exist.
    """
    identity = _identity(request)
    _repository_access(request, identity, f"{owner}/{repo}", name, "manage_credentials")
    with request.app.state.database.transaction() as connection:
        environment = (
            connection.execute(
                environments.select()
                .where(environments.c.id == f"{owner}/{repo}:{name}")
                .with_for_update()
            )
            .mappings()
            .first()
        )
        if not environment:
            raise ComposerError("Environment not found.", 404)
        removed = connection.execute(
            execution_credential_bindings.delete().where(
                execution_credential_bindings.c.id == f"{environment['id']}:{provider}"
            )
        )
        if removed.rowcount:
            connection.execute(
                environments.update()
                .where(environments.c.id == environment["id"])
                .values(revision=environment["revision"] + 1)
            )
            audit(
                connection,
                identity["login"],
                "execution_credential.revoked",
                environment=environment["id"],
                provider=provider,
            )
    return {"ok": True}


@repositories_router.post("/repositories/{owner}/{repo}/environments/{name}/secrets")
def bind_secret(request: Request, owner: str, repo: str, name: str, body: SecretInput):
    """Replace a phase-scoped write-only credential binding.

    Args:
        request: Administrator request.
        owner: GitHub owner.
        repo: Repository name.
        name: Environment name.
        body: Secret binding.

    Returns:
        Secret identifier without its value.

    Raises:
        ComposerError: The binding is unsafe or invalid.
    """
    identity = _identity(request)
    _repository_access(request, identity, f"{owner}/{repo}", name, "manage_credentials")
    if body.name in RESERVED_ENV or body.name.startswith(
        ("STACKSMITH_", "PLATFORM_", "GIT_", "PYTHON", "LD_")
    ):
        raise ComposerError("This environment variable is reserved by the runner.", 422)
    if (body.value is None) == (body.reference is None) or not body.phases:
        raise ComposerError(
            "Provide one secret value or reference and at least one phase.", 422
        )
    if body.json_key is not None and body.reference is None:
        raise ComposerError("JSON fields require a deployment secret reference.", 422)
    if body.reference is not None:
        try:
            request.app.state.secret_catalog.validate(body.reference, body.json_key)
        except SecretReferenceError:
            raise ComposerError(
                "Deployment secret reference or JSON field is unavailable.", 422
            ) from None
        except SecretSourceError:
            raise ComposerError(
                "Deployment secret source is unavailable. Retry or check source configuration.",
                503,
            ) from None
    with request.app.state.database.transaction() as connection:
        identifier = f"{owner}/{repo}:{name}:{body.name}"
        environment = (
            connection.execute(
                environments.select()
                .where(environments.c.id == f"{owner}/{repo}:{name}")
                .with_for_update()
            )
            .mappings()
            .first()
        )
        if not environment:
            raise ComposerError("Environment not found.", 404)
        for binding in connection.execute(
            execution_credential_bindings.select().where(
                execution_credential_bindings.c.environment == environment["id"]
            )
        ).mappings():
            provider = request.app.state.execution_credential_catalog.providers.get(
                binding["provider"]
            )
            if set(binding["phases"]) & set(body.phases) and (
                not provider or body.name in provider.conflicting_variables
            ):
                raise ComposerError(
                    "Environment variables conflict with the execution credential provider.",
                    422,
                )
        connection.execute(secrets.delete().where(secrets.c.id == identifier))
        connection.execute(
            secrets.insert().values(
                id=identifier,
                environment=environment["id"],
                name=body.name,
                phases=body.phases,
                ciphertext=request.app.state.database.encrypt(body.value)
                if body.value is not None
                else None,
                reference=body.reference,
                json_key=body.json_key,
            )
        )
        connection.execute(
            environments.update()
            .where(environments.c.id == environment["id"])
            .values(revision=environment["revision"] + 1)
        )
        audit(
            connection,
            identity["login"],
            "secret.bound",
            environment=environment["id"],
            name=body.name,
        )
    return {"id": identifier}


@runs_router.post("/runs")
def create_run(request: Request, body: RunInput):
    """Queue a lifecycle against a verified committed revision.

    Args:
        request: Authenticated request with an idempotency header.
        body: Lifecycle inputs.

    Returns:
        Durable run record.

    Raises:
        ComposerError: Access, revision, or deployment branch policy fails.
    """
    identity = _identity(request)
    for workload in run_workloads({"phases": phases(body.action, **body.run_options)}):
        _repository_access(
            request,
            identity,
            body.repository,
            body.environment,
            f"{'execute' if body.apply_after_plan else 'plan'}_{workload}",
        )
    if body.allow_local_backend:
        _administrator(request, identity)
        if body.apply_after_plan or body.destroy_plan:
            raise ComposerError(
                "Local backend overrides are restricted to test plans.", 422
            )
    key = request.headers.get("idempotency-key", "")
    if not 1 <= len(key) <= 200:
        raise ComposerError(
            "An Idempotency-Key header is required (1-200 characters).", 422
        )
    with request.app.state.database.transaction() as connection:
        repository = row(connection, repositories, body.repository)
        environment = row(
            connection, environments, f"{body.repository}:{body.environment}"
        )
    if not repository or not environment or not environment["active"]:
        raise ComposerError("Register this repository and environment first.", 404)
    requested_revision = body.revision
    if requested_revision is not None:
        _administrator(request, identity)
    repository = {
        **repository,
        "branch": request.app.state.provider.request(
            repository["installation"], "GET", f"/repos/{body.repository}"
        )["default_branch"],
    }
    body = body.model_copy(
        update={
            "revision": request.app.state.provider.revision(
                repository, requested_revision or repository["branch"]
            )["sha"]
        }
    )
    with request.app.state.database.transaction() as connection:
        connection.execute(
            repositories.update()
            .where(repositories.c.id == body.repository)
            .values(branch=repository["branch"])
        )
        # Lock the environment to serialize idempotent concurrent submissions.
        connection.execute(
            environments.select()
            .where(environments.c.id == environment["id"])
            .with_for_update()
        )
        return _public_run(
            enqueue(
                connection,
                request.app.state.settings,
                repository,
                environment,
                body,
                identity["login"],
                hashlib.sha256(f"{identity['login']}:{key}".encode()).hexdigest(),
                revision_override=requested_revision is not None,
                requested_revision=requested_revision,
            )
        )


def _run_history_scope(request, repository, environment):
    identity = _identity(request)
    token_environment = identity["scope"]["environment"] if identity["scope"] else "*"
    access = _repository_access(
        request,
        identity,
        repository,
        environment if environment is not None else token_environment,
    )
    scope = runs.c.repository == repository
    if environment is not None:
        return and_(scope, runs.c.environment == environment)
    if token_environment != "*":
        return and_(scope, runs.c.environment == token_environment)
    if access["administrator"] or any(
        assignment["environment"] == "*" for assignment in access["assignments"]
    ):
        return scope
    return and_(
        scope,
        runs.c.environment.in_(
            [assignment["environment"] for assignment in access["assignments"]]
        ),
    )


@runs_router.get("/runs")
def list_runs(
    request: Request,
    repository: str,
    environment: str | None = None,
    page: Annotated[int, Query(ge=1)] = 1,
    page_size: Annotated[int, Query(ge=1, le=100)] = 20,
):
    """List a page of visible runs, optionally filtered by environment.

    Args:
        request: Authenticated request.
        repository: GitHub repository.
        environment: Environment name, or `None` for all visible environments.
        page: One-based page number.
        page_size: Maximum runs returned per page.

    Returns:
        Run items and pagination metadata.

    Raises:
        ComposerError: Repository access is unavailable.
    """
    scope = _run_history_scope(request, repository, environment)
    with request.app.state.database.transaction() as connection:
        return {
            "page": page,
            "page_size": page_size,
            "total": connection.execute(
                select(func.count()).select_from(runs).where(scope)
            ).scalar_one(),
            "items": [
                dict(item)
                for item in connection.execute(
                    select(
                        runs.c.id,
                        runs.c.repository,
                        runs.c.environment,
                        runs.c.revision,
                        runs.c.action,
                        runs.c.actor,
                        runs.c.status,
                        runs.c.phase,
                        runs.c.created,
                        runs.c.updated,
                        runs.c.expires,
                    )
                    .where(scope)
                    .order_by(runs.c.created.desc(), runs.c.id.desc())
                    .offset((page - 1) * page_size)
                    .limit(page_size)
                ).mappings()
            ],
        }


@runs_router.get("/runs/{run_id}")
def get_run(request: Request, run_id: str):
    """Read one authorized run.

    Args:
        request: Authenticated request.
        run_id: Run identifier.

    Returns:
        Run details.
    """
    return _public_run(_run_access(request, run_id)[1])


@runs_router.delete("/runs/{run_id}")
@administrator
def delete_run(request: Request, run_id: str):
    """Remove a terminal run and all associated rows from run history.

    Args:
        request: Administrator browser request.
        run_id: Run identifier.

    Returns:
        Deletion confirmation.

    Raises:
        ComposerError: Run not found or not in a terminal state.
    """
    from .database import execution_credential_sessions, phase_claims

    with request.app.state.database.transaction() as connection:
        run = row(connection, runs, run_id)
        if not run:
            raise ComposerError("Run not found.", 404)
        if run["status"] not in TERMINAL:
            raise ComposerError("Only terminal runs may be deleted.", 409)
        for artifact in connection.execute(
            artifacts.select().where(artifacts.c.run == run_id)
        ).mappings():
            request.app.state.artifacts.delete(artifact["key"])
        connection.execute(artifacts.delete().where(artifacts.c.run == run_id))
        connection.execute(locks.delete().where(locks.c.run == run_id))
        connection.execute(phase_claims.delete().where(phase_claims.c.run == run_id))
        connection.execute(
            execution_credential_sessions.delete().where(
                execution_credential_sessions.c.run == run_id
            )
        )
        connection.execute(runs.delete().where(runs.c.id == run_id))
    return {"deleted": True}


def _transition(request, run_id, authorize=False, cancel=False):
    identity, run = _run_access(
        request, run_id, "approve" if authorize else "deploy" if cancel else "approve"
    )
    if not cancel and not identity["browser"]:
        raise ComposerError("Approvals require an interactive browser session.", 403)
    if not cancel and run["spec"].get("revision_override", False):
        _administrator(request, identity)
    with request.app.state.database.transaction() as connection:
        run = (
            connection.execute(
                runs.select().where(runs.c.id == run_id).with_for_update()
            )
            .mappings()
            .one()
        )
        if cancel:
            if run["status"] in TERMINAL:
                return _public_run(run)
            status = "cancelling" if run["status"] == "running" else "cancelled"
        else:
            if run["status"] != (
                "awaiting_authorization" if authorize else "awaiting_approval"
            ):
                raise ComposerError("Run is not waiting for this approval.", 409)
            if run["expires"] < time.time():
                raise ComposerError("Approval expired; request a new run.", 409)
            environment = row(
                connection, environments, f"{run['repository']}:{run['environment']}"
            )
            repository = row(connection, repositories, run["repository"])
            if (
                environment["revision"] != run["spec"]["environment_revision"]
                or effective_config(connection, request.app.state.settings, repository)
                != run["spec"]["config"]
            ):
                raise ComposerError("Environment changed; request a new run.", 409)
            if (
                not authorize
                and not run["spec"].get("revision_override", False)
                and request.app.state.provider.revision(
                    repository, repository["branch"]
                )["sha"]
                != run["revision"]
            ):
                raise ComposerError(
                    "A newer default-branch commit exists; request a new run.", 409
                )
            status = "queued"
        spec = dict(run["spec"])
        if not cancel:
            spec["_authorizer" if authorize else "_approver"] = identity["login"]
        connection.execute(
            runs.update()
            .where(runs.c.id == run_id)
            .values(
                spec=spec,
                status=status,
                supervision=requested_supervision(
                    request.app.state.settings, status, run["supervision"]
                ),
                phase=run["phase"] + (1 if not authorize and not cancel else 0),
                updated=time.time(),
                expires=None if authorize else run["expires"],
            )
        )
        if status == "cancelled":
            connection.execute(locks.delete().where(locks.c.run == run_id))
        audit(
            connection,
            identity["login"],
            "run.cancelled"
            if cancel
            else "run.authorized"
            if authorize
            else "run.approved",
            run_id,
            revision=run["revision"],
            context=run["result"].get("context_digest"),
        )
    return {"id": run_id, "status": status}


@runs_router.post("/runs/{run_id}/authorize")
def authorize_run(request: Request, run_id: str):
    """Authorize credentialed PR execution at its captured revision.

    Args:
        request: Maintainer browser request.
        run_id: Run identifier.

    Returns:
        Updated run status.
    """
    return _transition(request, run_id, authorize=True)


@runs_router.post("/runs/{run_id}/approve")
def approve_run(request: Request, run_id: str):
    """Approve execution of the captured successful preview.

    Args:
        request: Approver browser request.
        run_id: Run identifier.

    Returns:
        Updated run status.
    """
    return _transition(request, run_id)


@runs_router.post("/runs/{run_id}/cancel")
def cancel_run(request: Request, run_id: str):
    """Cancel queued work or request bounded running-phase termination.

    Args:
        request: Authorized request.
        run_id: Run identifier.

    Returns:
        Updated run status.
    """
    return _transition(request, run_id, cancel=True)


@runs_router.get("/runs/{run_id}/artifacts")
def list_artifacts(request: Request, run_id: str):
    """List authorized review artifacts, excluding private execution context.

    Args:
        request: Authenticated request.
        run_id: Run identifier.

    Returns:
        Unexpired public artifact metadata.
    """
    _run_access(request, run_id)
    with request.app.state.database.transaction() as connection:
        return [
            dict(item)
            for item in connection.execute(
                artifacts.select()
                .with_only_columns(
                    artifacts.c.id,
                    artifacts.c.name,
                    artifacts.c.digest,
                    artifacts.c.expires,
                )
                .where(
                    and_(
                        artifacts.c.run == run_id,
                        artifacts.c.private == 0,
                        artifacts.c.expires > time.time(),
                    )
                )
            ).mappings()
        ]


@runs_router.get("/runs/{run_id}/artifacts/{artifact_id}")
def download_artifact(request: Request, run_id: str, artifact_id: str):
    """Return an authenticated review artifact download URL.

    Args:
        request: Authenticated request.
        run_id: Run identifier.
        artifact_id: Artifact identifier.

    Returns:
        API URL requiring current authorization at download time.

    Raises:
        ComposerError: The artifact is private, expired, or belongs to another run.
    """
    _run_access(request, run_id)
    with request.app.state.database.transaction() as connection:
        artifact = row(connection, artifacts, artifact_id)
    if (
        not artifact
        or artifact["run"] != run_id
        or artifact["private"]
        or artifact["expires"] <= time.time()
    ):
        raise ComposerError("Artifact not found.", 404)
    return {"url": f"/api/v1/runs/{run_id}/artifacts/{artifact_id}/content"}


@runs_router.get("/runs/{run_id}/logs")
def run_logs(request: Request, run_id: str):
    """Read bounded, credential-redacted phase logs collected by the supervisor.

    Args:
        request: Authenticated request.
        run_id: Run identifier.

    Returns:
        Phase logs and completion status.
    """
    _, run = _run_access(request, run_id)
    return {
        "text": run["result"].get("logs", ""),
        "complete": run["status"] in TERMINAL,
    }


@platform_router.get("/audit")
@administrator
def list_audit(
    request: Request,
    repository: str | None = None,
    run_id: str | None = None,
    actor: str | None = None,
    action: str | None = None,
    page: Annotated[int, Query(ge=1)] = 1,
    page_size: Annotated[int, Query(ge=1, le=100)] = 20,
):
    """List platform audit events for an administrator browser session.

    Args:
        request: Authenticated administrator request.
        repository: Optional exact repository filter.
        run_id: Optional exact run identifier filter.
        actor: Optional exact actor filter.
        action: Optional exact event action filter.
        page: One-based page number.
        page_size: Maximum events returned per page.

    Returns:
        Newest-first events and pagination metadata.

    Raises:
        ComposerError: The caller is not an administrator browser session.
    """
    filters = [
        column == value
        for column, value in (
            (audits.c.repository, repository),
            (audits.c.run, run_id),
            (audits.c.actor, actor),
            (audits.c.action, action),
        )
        if value is not None
    ]
    with request.app.state.database.transaction() as connection:
        return {
            "page": page,
            "page_size": page_size,
            "total": connection.execute(
                select(func.count()).select_from(audits).where(*filters)
            ).scalar_one(),
            "items": [
                _audit_event_summary(event)
                for event in connection.execute(
                    select(audits, runs.c.spec.label("run_spec"))
                    .select_from(audits.outerjoin(runs, audits.c.run == runs.c.id))
                    .where(*filters)
                    .order_by(audits.c.created.desc(), audits.c.id.desc())
                    .offset((page - 1) * page_size)
                    .limit(page_size)
                ).mappings()
            ],
        }


def _audit_event_summary(event):
    result = dict(event)
    spec = result.pop("run_spec") or {}
    phase = (result["detail"] or {}).get("phase")
    if (
        result["action"].startswith("phase.")
        and type(phase) is int
        and 0 <= phase < len(spec.get("phases", []))
    ):
        result["detail"] = {**result["detail"], "phase_name": spec["phases"][phase]}
    return result


@runs_router.get("/runs/{run_id}/audit")
@administrator
def run_audit(request: Request, run_id: str):
    """Read audit events associated with a run as an administrator.

    Args:
        request: Authenticated request.
        run_id: Run identifier.

    Returns:
        Chronological audit events.
    """
    _run_access(request, run_id)
    with request.app.state.database.transaction() as connection:
        return [
            dict(item)
            for item in connection.execute(
                audits.select().where(audits.c.run == run_id).order_by(audits.c.created)
            ).mappings()
        ]


@tokens_router.post("/tokens")
def issue_token(request: Request, body: TokenInput):
    """Issue a scoped API token, displaying its secret exactly once.

    Args:
        request: Interactive browser session.
        body: Token scope and bounded lifetime.

    Returns:
        Token identifier, plaintext token, and expiry.
    """
    identity = _identity(request)
    if not identity["browser"]:
        raise ComposerError("Tokens require a browser session.", 403)
    for permission in body.permissions:
        if permission == "view":
            _repository_access(request, identity, body.repository, body.environment)
        else:
            if not body.workloads:
                raise ComposerError("Execution tokens require workload access.", 422)
            for workload in body.workloads:
                _repository_access(
                    request,
                    identity,
                    body.repository,
                    body.environment,
                    f"{'execute' if permission == 'deploy' else 'plan'}_{workload}",
                )
    token = random_secrets.token_urlsafe(48)
    identifier = hashlib.sha256(token.encode()).hexdigest()
    with request.app.state.database.transaction() as connection:
        from .database import sessions

        session = row(
            connection,
            sessions,
            hashlib.sha256(
                request.cookies.get("platform_session", "").encode()
            ).hexdigest(),
        )
        expires = min(session["expires"], time.time() + body.expires_in)
        connection.execute(
            tokens.insert().values(
                id=identifier,
                login=identity["login"],
                name=body.name,
                scope={
                    **body.model_dump(exclude={"name", "expires_in"}),
                    "version": 2,
                    "credential": request.app.state.database.encrypt(
                        identity["github"]
                    ),
                },
                expires=expires,
                revoked=0,
            )
        )
        audit(connection, identity["login"], "token.issued", token=identifier)
    return {"id": identifier, "token": token, "expires": expires}


@tokens_router.get("/tokens")
def list_tokens(
    request: Request,
    page: Annotated[int, Query(ge=1)] = 1,
    page_size: Annotated[int, Query(ge=1, le=100)] = 20,
    status: Literal["active", "all", "expired", "revoked"] = "active",
):
    """List owned tokens with pagination and safe metadata.

    Args:
        request: Authenticated request.
        page: One-based page number.
        page_size: Maximum tokens returned per page.
        status: Token status filter.

    Returns:
        Safe token metadata, pagination, and account-wide status counts.

    Raises:
        ComposerError: Authentication is unavailable.
    """
    identity = _identity(request)
    with request.app.state.database.transaction() as connection:
        observed = time.time()
        owned = tokens.c.login == identity["login"]
        active = and_(tokens.c.revoked == 0, tokens.c.expires > observed)
        expired = and_(tokens.c.revoked == 0, tokens.c.expires <= observed)
        counts = dict(
            connection.execute(
                select(
                    func.count().label("all"),
                    func.count().filter(active).label("active"),
                    func.count().filter(expired).label("expired"),
                    func.count().filter(tokens.c.revoked > 0).label("revoked"),
                )
                .select_from(tokens)
                .where(owned)
            )
            .mappings()
            .one()
        )
        return {
            "page": page,
            "page_size": page_size,
            "total": counts[status],
            "counts": counts,
            "items": [
                {
                    "id": item["id"],
                    "name": item["name"],
                    "expires": item["expires"],
                    "revoked": item["revoked"],
                    "repository": item["scope"]["repository"],
                    "environment": item["scope"]["environment"],
                    "permissions": item["scope"]["permissions"],
                    "workloads": item["scope"].get("workloads", []),
                }
                for item in connection.execute(
                    tokens.select()
                    .where(
                        owned,
                        {
                            "active": active,
                            "all": True,
                            "expired": expired,
                            "revoked": tokens.c.revoked > 0,
                        }[status],
                    )
                    .order_by(
                        case((active, 0), else_=1),
                        case((active, tokens.c.expires), else_=-tokens.c.expires),
                        tokens.c.name,
                        tokens.c.id,
                    )
                    .offset((page - 1) * page_size)
                    .limit(page_size)
                ).mappings()
            ],
        }


@tokens_router.post("/tokens/{token_id}/revoke")
def revoke_token(request: Request, token_id: str):
    """Revoke an owned API token.

    Args:
        request: Authenticated request.
        token_id: Token digest identifier.

    Returns:
        Revocation confirmation.
    """
    identity = _identity(request)
    with request.app.state.database.transaction() as connection:
        connection.execute(
            tokens.update()
            .where(and_(tokens.c.id == token_id, tokens.c.login == identity["login"]))
            .values(revoked=1)
        )
        audit(connection, identity["login"], "token.revoked", token=token_id)
    return {"ok": True}


@platform_router.get("/status")
@administrator
def platform_status(request: Request):
    """Report worker heartbeat and queue health to administrators.

    Args:
        request: Administrator request.

    Returns:
        Worker heartbeat and queued-run metadata.
    """
    with request.app.state.database.transaction() as connection:
        observed = time.time()
        pending = [
            dict(item)
            for item in connection.execute(
                runs.select()
                .with_only_columns(runs.c.id, runs.c.status, runs.c.created)
                .where(runs.c.status.in_(["queued", "uncertain"]))
            ).mappings()
        ]
        if request.app.state.settings.supervision_mode == "kubernetes-jobs":
            recovery = row(connection, scheduler_health, "recovery")
            return {
                "mode": "kubernetes-jobs",
                "observed_at": observed,
                "pending": pending,
                "recovery": {
                    "completed": recovery["completed"] if recovery else None,
                    "overdue": not recovery
                    or not recovery["completed"]
                    or observed - recovery["completed"] > 180,
                    "error": recovery["error"] if recovery else None,
                },
                "supervisors": [
                    {
                        "run": item["id"],
                        "status": item["status"],
                        "phase": item["spec"]["phases"][item["phase"]]
                        if item["status"] in {"queued", "running", "cancelling"}
                        else None,
                        "job": item["supervision"].get("job"),
                        "generation": item["supervision"].get("generation", 0),
                        "state": item["supervision"].get("state", "requested"),
                        "error": item["supervision"].get("error"),
                        "execution": (item["execution"] or {}).get("name"),
                        "execution_state": (item["execution"] or {}).get(
                            "observed_state"
                        ),
                    }
                    for item in connection.execute(
                        runs.select()
                        .where(runs.c.supervision.is_not(None))
                        .order_by(runs.c.updated.desc())
                        .limit(50)
                    ).mappings()
                ],
            }
        records = connection.execute(workers.select()).mappings().all()
        return {
            "mode": "worker",
            "observed_at": observed,
            "pending": pending,
            "stale_workers": sum(
                observed - item["heartbeat"] > 120 for item in records
            ),
            "workers": [
                dict(item)
                for item in sorted(
                    records, key=lambda item: item["heartbeat"], reverse=True
                )
                if observed - item["heartbeat"] <= 120
            ],
        }


router.include_router(platform_router)
router.include_router(repositories_router)
router.include_router(configuration_router)
router.include_router(runs_router)
router.include_router(tokens_router)
router.include_router(webhooks_router)
router.include_router(execution_router)


def _github_catalog_event(request, event, payload, delivery):
    with request.app.state.database.transaction() as connection:
        if row(connection, webhooks, delivery):
            return {"accepted": True, "duplicate": True}
        installation_event(connection, event, payload)
    # Fetch the current catalog instead of trusting stale or out-of-order additions.
    # Failed reconciliation leaves revocations in effect and the delivery retryable.
    synchronize(
        request.app.state.database, request.app.state.provider.catalog(), complete=True
    )
    with request.app.state.database.transaction() as connection:
        if not row(connection, webhooks, delivery):
            connection.execute(
                webhooks.insert().values(id=delivery, created=time.time())
            )
    return {"accepted": True}


@webhooks_router.post("/webhooks/github")
async def github_webhook(request: Request):
    """Verify and deduplicate GitHub push and same-repository PR events.

    Args:
        request: GitHub webhook request with HMAC signature.

    Returns:
        Accepted delivery status.

    Raises:
        ComposerError: The delivery is oversized or has an invalid signature.
    """
    _enabled(request)
    body = bytearray()
    async for chunk in request.stream():
        body.extend(chunk)
        if len(body) > 2_000_000:
            raise ComposerError("Webhook exceeds size limit.", 413)
    expected = (
        "sha256="
        + hmac.new(
            request.app.state.settings.github_webhook_secret.encode(),
            body,
            hashlib.sha256,
        ).hexdigest()
    )
    if not hmac.compare_digest(
        expected, request.headers.get("x-hub-signature-256", "")
    ):
        raise ComposerError("Invalid GitHub signature.", 401)
    delivery = request.headers.get("x-github-delivery", "")
    if not re.fullmatch(r"[A-Za-z0-9-]{1,100}", delivery):
        raise ComposerError("Invalid delivery identifier.", 422)
    try:
        payload = json.loads(body)
    except (ValueError, UnicodeDecodeError) as exc:
        raise ComposerError("Invalid webhook JSON.", 422) from exc
    event = request.headers.get("x-github-event")
    if event in {"installation", "installation_repositories", "repository"}:
        return _github_catalog_event(request, event, payload, delivery)
    with request.app.state.database.transaction() as connection:
        # Serialize ingestion on the registered repository, including redeliveries.
        repository = (
            connection.execute(
                repositories.select()
                .where(
                    repositories.c.id
                    == payload.get("repository", {}).get("full_name", "")
                )
                .with_for_update()
            )
            .mappings()
            .first()
        )
        if (
            not repository
            or not repository["enabled"]
            or not repository["deployment_enabled"]
            or repository["installation"] != payload.get("installation", {}).get("id")
        ):
            return {"accepted": False}
        if row(connection, webhooks, delivery):
            return {"accepted": True, "duplicate": True}
        connection.execute(webhooks.insert().values(id=delivery, created=time.time()))
        event = request.headers.get("x-github-event")
        if (
            event == "push"
            and not payload.get("deleted")
            and payload.get("ref") == f"refs/heads/{repository['branch']}"
        ):
            revision, action, authorization = payload.get("after"), "apply", False
        elif event == "pull_request" and payload.get("action") in {
            "opened",
            "synchronize",
            "reopened",
            "ready_for_review",
        }:
            pr = payload.get("pull_request", {})
            if pr.get("head", {}).get("repo", {}).get("full_name") != repository["id"]:
                return {"accepted": False}
            revision, action, authorization = (
                pr.get("head", {}).get("sha"),
                "plan",
                True,
            )
        else:
            return {"accepted": True, "ignored": True}
        for environment in connection.execute(
            environments.select().where(
                and_(
                    environments.c.repository == repository["id"],
                    environments.c.active == 1,
                )
            )
        ).mappings():
            if environment["settings"].get("automatic", True):
                try:
                    enqueue(
                        connection,
                        request.app.state.settings,
                        repository,
                        environment,
                        RunInput(
                            repository=repository["id"],
                            environment=environment["name"],
                            revision=revision,
                            action=action,
                        ),
                        payload.get("sender", {}).get("login", "github"),
                        f"github:{delivery}:{environment['name']}",
                        event,
                        authorization,
                    )
                except ComposerError as exc:
                    if exc.status != 403:
                        raise
                    audit(
                        connection,
                        payload.get("sender", {}).get("login", "github"),
                        "webhook.access_denied",
                        repository=repository["id"],
                        environment=environment["name"],
                        reason=str(exc),
                    )
    return {"accepted": True}


@execution_router.post("/executions/{run_id}/claim")
def claim_execution(request: Request, run_id: str):
    """Consume a single-phase execution capability before any user code runs.

    Args:
        request: Request carrying the one-use execution token.
        run_id: Run identifier.

    Returns:
        Successful claim confirmation.

    Raises:
        ComposerError: Capability is invalid, stale, or already consumed.
    """
    from .database import phase_claims

    _enabled(request)
    with request.app.state.database.transaction() as connection:
        run = (
            connection.execute(
                runs.select().where(runs.c.id == run_id).with_for_update()
            )
            .mappings()
            .first()
        )
        token = request.headers.get("authorization", "").removeprefix("Bearer ")
        if (
            not run
            or run["status"] != "running"
            or not run["execution"]
            or not hmac.compare_digest(
                hashlib.sha256(token.encode()).hexdigest(),
                run["execution"]["claim_digest"],
            )
        ):
            raise ComposerError("Invalid execution capability.", 403)
        identifier = f"{run_id}:{run['phase']}"
        if row(connection, phase_claims, identifier):
            raise ComposerError("This execution phase was already claimed.", 409)
        connection.execute(
            phase_claims.insert().values(id=identifier, run=run_id, fence=run["fence"])
        )
        audit(connection, "execution", "phase.claimed", run_id, phase=run["phase"])
    return {"claimed": True}


@execution_router.post("/executions/{run_id}/inspection-result")
def inspection_result_ready(request: Request, run_id: str):
    """Signal an uploaded encrypted inspection result without trusting its contents.

    Args:
        request: Request carrying the existing claimed phase capability.
        run_id: Inspection run identifier.

    Returns:
        An idempotent acknowledgement. Controllers reread and verify the artifact.

    Raises:
        ComposerError: The capability is expired, unclaimed, or outside its inspection.
    """
    from .inspection_notifications import notify

    _enabled(request)
    with request.app.state.database.transaction() as connection:
        run = _inspection_completion_run(connection, request, run_id)
        if run["status"] not in {"running", "cancelling"}:
            return {"accepted": False}
        if not run["execution"].get("result_ready"):
            connection.execute(
                runs.update()
                .where(runs.c.id == run_id)
                .values(execution={**run["execution"], "result_ready": True})
            )
            notify(connection, run_id)
    return {"accepted": True}


def _inspection_completion_run(connection, request, run_id):
    from .database import phase_claims

    run = (
        connection.execute(runs.select().where(runs.c.id == run_id).with_for_update())
        .mappings()
        .first()
    )
    claim = row(connection, phase_claims, f"{run_id}:{run['phase']}") if run else None
    if (
        not run
        or run["action"] != "inspection"
        or not run["spec"].get("inspection_controller")
        or run["expires"] <= time.time()
        or not run["execution"]
        or not claim
        or claim["fence"] != run["fence"]
        or not hmac.compare_digest(
            hashlib.sha256(
                request.headers.get("authorization", "")
                .removeprefix("Bearer ")
                .encode()
            ).hexdigest(),
            run["execution"]["claim_digest"],
        )
    ):
        raise ComposerError("Invalid inspection completion capability.", 403)
    return run


@execution_router.post("/executions/{run_id}/credentials")
def execution_credentials(request: Request, run_id: str):
    """Refresh credentials for an authorized execution phase.

    Args:
        request: Request bearing a phase-scoped credential capability.
        run_id: Target execution run.

    Returns:
        Short-lived credential values and expiration.

    Raises:
        ComposerError: Execution access or credential issuance fails.
    """
    from .credential_broker import refresh_credentials

    _enabled(request)
    return refresh_credentials(request, run_id)


class ResolutionInput(Input):
    """Operator acknowledgement that uncertain infrastructure was inspected."""

    note: str = Field(min_length=10, max_length=2000)


@runs_router.post("/runs/{run_id}/resolve")
def resolve_uncertain_run(request: Request, run_id: str, body: ResolutionInput):
    """Release an uncertain deployment lock after explicit operator reconciliation.

    Args:
        request: Authorized interactive deployer request.
        run_id: Uncertain run identifier.
        body: Operator's reconciliation record.

    Returns:
        Resolved failure status; execution is never resumed or replayed.

    Raises:
        ComposerError: The run is not uncertain or the request is not interactive.
    """
    identity, _ = _run_access(request, run_id, "reconcile")
    if not identity["browser"]:
        raise ComposerError("Reconciliation requires a browser session.", 403)
    with request.app.state.database.transaction() as connection:
        run = (
            connection.execute(
                runs.select().where(runs.c.id == run_id).with_for_update()
            )
            .mappings()
            .one()
        )
        if run["status"] != "uncertain":
            raise ComposerError("Run is not awaiting reconciliation.", 409)
        connection.execute(
            runs.update()
            .where(runs.c.id == run_id)
            .values(status="failed", updated=time.time())
        )
        connection.execute(locks.delete().where(locks.c.run == run_id))
        audit(connection, identity["login"], "run.reconciled", run_id, note=body.note)
    return {"status": "failed"}


@runs_router.get("/runs/{run_id}/artifacts/{artifact_id}/content")
def artifact_content(request: Request, run_id: str, artifact_id: str):
    """Proxy authorized review artifacts without exposing private storage endpoints.

    Args:
        request: Authenticated request.
        run_id: Run identifier.
        artifact_id: Public review artifact identifier.

    Returns:
        Authorized JSON review artifact.

    Raises:
        ComposerError: The requested artifact is unavailable or private.
    """
    from fastapi.responses import Response

    _run_access(request, run_id)
    with request.app.state.database.transaction() as connection:
        artifact = row(connection, artifacts, artifact_id)
    if (
        not artifact
        or artifact["run"] != run_id
        or artifact["private"]
        or artifact["expires"] <= time.time()
    ):
        raise ComposerError("Artifact not found.", 404)
    return Response(
        request.app.state.artifacts.get(artifact["key"]),
        media_type="application/json",
        headers={
            "Cache-Control": "no-store",
            "Content-Disposition": 'attachment; filename="stacksmith-report.json"',
        },
    )


@repositories_router.post(
    "/repositories/{owner}/{repo}/environments/{name}/secrets/{variable}/remove"
)
def remove_secret(request: Request, owner: str, repo: str, name: str, variable: str):
    """Revoke a secret binding and invalidate pending environment approvals.

    Args:
        request: Administrator browser request.
        owner: GitHub owner.
        repo: Repository name.
        name: Environment name.
        variable: Bound environment variable name.

    Returns:
        Revocation confirmation.

    Raises:
        ComposerError: The environment is unavailable.
    """
    identity = _identity(request)
    _repository_access(request, identity, f"{owner}/{repo}", name, "manage_credentials")
    with request.app.state.database.transaction() as connection:
        environment = row(connection, environments, f"{owner}/{repo}:{name}")
        if not environment:
            raise ComposerError("Environment not found.", 404)
        connection.execute(
            secrets.delete().where(secrets.c.id == f"{environment['id']}:{variable}")
        )
        connection.execute(
            environments.update()
            .where(environments.c.id == environment["id"])
            .values(revision=environment["revision"] + 1)
        )
        audit(
            connection,
            identity["login"],
            "secret.revoked",
            environment=environment["id"],
            name=variable,
        )
    return {"ok": True}
