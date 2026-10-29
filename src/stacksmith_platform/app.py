import asyncio
import hashlib
import json
import logging
import re
import secrets
from asyncio import to_thread
from contextlib import asynccontextmanager
from functools import partial
from pathlib import Path
from time import perf_counter
from typing import Annotated, Any, Literal
from urllib.parse import urlencode, urlparse

from fastapi import APIRouter, Depends, FastAPI, Request
from fastapi.responses import JSONResponse, RedirectResponse, StreamingResponse
from itsdangerous import BadSignature, SignatureExpired, URLSafeTimedSerializer
from pydantic import BaseModel, Field
from starlette.background import BackgroundTasks
from starlette.middleware.trustedhost import TrustedHostMiddleware

from .access import access_for, require_access
from .adapter import StacksmithAdapter
from .certificates import CA_BUNDLE_MOUNT_PATH, configure_ca_bundle
from .component_rename import rename_components, root_rename_edits
from .configuration import configuration_policy, effective_config
from .configuration_events import ConfigurationEvents
from .database import environments, row
from .database import repositories as registered_repositories
from .documents import _source_edits, load_document, source_diff
from .editing_contexts import EditingContexts, context_key
from .errors import ComposerError
from .github import GitHubClient
from .github_transport import GitHubTransport
from .graph import extract_graph, inspect_graph, validate_root_references
from .inspection_inputs import prepare_inputs
from .metrics import TimingSamples
from .platform import platform_summary
from .repositories import synchronize, synchronize_user
from .repository_filter import filtered, get_filter
from .sessions import SessionStore
from .settings import Settings

router = APIRouter(prefix="/api")
platform_router = APIRouter(tags=["Platform"])
composer_router = APIRouter(tags=["Composer"])
github_router = APIRouter(tags=["GitHub repositories"])
auth_router = APIRouter(tags=["Authentication"])


class DiagramPosition(BaseModel):
    """Persisted coordinates for one component on the Composer diagram."""

    x: float
    y: float


class DiagramRoute(BaseModel):
    """Persisted routing points for one Composer relationship."""

    source: str
    target: str
    output: str
    property: str
    path: list[str | int]
    kind: Literal["reference", "association"]
    rule: str | None = None
    points: list[DiagramPosition]
    label_position: DiagramPosition | None = None


class InspectionContextInput(BaseModel):
    """Signed stack identity and the selected inspection environment."""

    snapshot: str = Field(max_length=300_000)
    environment: str | None = Field(
        default=None, pattern=r"^[A-Za-z0-9][A-Za-z0-9_-]{0,63}$"
    )


class ComponentRename(BaseModel):
    """One ordered change to a component identifier."""

    old: str = Field(min_length=1, max_length=200)
    new: str = Field(pattern=r"^[A-Za-z0-9_-]{1,64}$")


class Draft(InspectionContextInput):
    """A proposed edit relative to a signed source snapshot."""

    components: dict[str, dict[str, Any]] = Field(max_length=200)
    disabled_associations: list[str] = Field(default_factory=list, max_length=200)
    positions: dict[str, DiagramPosition] = Field(default_factory=dict, max_length=200)
    routes: list[DiagramRoute] = Field(default_factory=list, max_length=500)
    renames: list[ComponentRename] = Field(default_factory=list, max_length=100)


class RenameDraft(Draft):
    """A draft and the component identifier to change."""

    old_name: str = Field(min_length=1, max_length=200)
    new_name: str = Field(pattern=r"^[A-Za-z0-9_-]{1,64}$")


class ResolutionDraft(Draft):
    """A draft with an optional configuration address to explain."""

    query: str | None = Field(
        default=None,
        max_length=1000,
        pattern=r"^(inputs|components|stack|config)(\.[^.\s]+)*$",
    )


def _identity(request: Request) -> tuple[str, str] | None:
    session = request.app.state.sessions.get(
        request.cookies.get("platform_session", "")
    )
    if request.app.state.settings.github_enabled and session is None:
        raise ComposerError("Sign in with GitHub to access Composer.", 401)
    if session and request.app.state.settings.execution_enabled:
        from .database import grants

        if session[0].lower() not in {
            login.lower() for login in request.app.state.settings.admin_logins
        }:
            with request.app.state.database.transaction() as connection:
                assigned = (
                    connection.execute(
                        grants.select().where(grants.c.login == session[0].lower())
                    )
                    .mappings()
                    .all()
                )
            if not assigned:
                raise ComposerError("Platform view permission is required.", 403)
    return session


def _principal(request: Request) -> str:
    session = _identity(request)
    return session[0] if session else "local-demo"


def _github_token(request: Request) -> str | None:
    session = _identity(request)
    return session[1] if session else None


def _github(request: Request) -> GitHubClient:
    session = _identity(request)
    if session is None:
        raise ComposerError(
            "Configure a GitHub App to use repository and PR features.", 503
        )
    return GitHubClient(session[1], request.app.state.github_transport)


def _signer(request: Request) -> URLSafeTimedSerializer:
    return URLSafeTimedSerializer(
        request.app.state.settings.secret_key, salt="composer-source-v1"
    )


def _configuration_references(request, repository=None):
    if not request.app.state.settings.execution_enabled:
        return [request.app.state.settings.config]
    repository = repository or request.query_params.get("repository")
    if repository:
        _authoring_access(request, repository)
    with request.app.state.database.transaction() as connection:
        return configuration_policy(
            connection,
            request.app.state.settings,
            row(connection, registered_repositories, repository)
            if repository
            else None,
        )["effective_refs"]


def _adapter(request, repository=None, context=None):
    if not request.app.state.settings.execution_enabled:
        return request.app.state.adapter
    prepared = getattr(request.state, "inspection_layers", None)
    references = (
        [reference for reference, _, _ in prepared[1]]
        if context and prepared and prepared[0] == repository
        else _configuration_references(request, repository)
    )
    return StacksmithAdapter(
        request.app.state.settings.model_copy(
            update={
                "config": Path(references[0])
                if len(references) == 1 and not str(references[0]).startswith("git+")
                else ",".join(str(reference) for reference in references)
            }
        ),
        context=context,
        database=request.app.state.database,
    )


def _inspection_context(request, snapshot, environment):
    if not snapshot.get("repository"):
        return None
    if not request.app.state.settings.execution_enabled:
        environment = None
    actor = _principal(request)
    context = {
        "mode": "deployment" if environment else "stack_preview",
        "repository": snapshot["repository"],
        "revision": snapshot["head"],
        "path": snapshot["path"],
        "environment": environment,
        "actor": actor,
        "config_revision": snapshot["config_revision"],
    }
    if not environment:
        _authoring_access(request, snapshot["repository"])
        return context
    with request.app.state.database.transaction() as connection:
        require_access(
            connection,
            request.app.state.settings,
            actor,
            snapshot["repository"],
            environment,
            "plan_infrastructure",
        )
        record = row(
            connection, environments, f"{snapshot['repository']}:{environment}"
        )
        if not record or not record["active"]:
            raise ComposerError("The selected environment is unavailable.", 404)
    return {
        **context,
        "environment_revision": record["revision"],
        "settings": record["settings"],
    }


def _context_metadata(context):
    return (
        {
            key: value
            for key, value in context.items()
            if key not in {"actor", "settings"} and not key.startswith("_")
        }
        if context
        else None
    )


def _check_inspection_context(request, context, adapter):
    if context is None:
        return
    # Recheck GitHub access after the isolated work, without reusing its preflight.
    request.state.authoring_repositories = None
    _authoring_access(request, context["repository"])
    if context["config_revision"] != _config_revision(request, context["repository"]):
        raise ComposerError(
            "Platform configuration changed. Reopen the stack before reviewing.", 409
        )
    if context.get("_input_manifest") and (
        prepare_inputs(request, context)["_input_manifest"]["digest"]
        != context["_input_manifest"]["digest"]
    ):
        raise ComposerError(
            "Inspection dependencies changed. Review the draft again.", 409
        )
    if context["environment"]:
        _check_environment_state(request, context, adapter, _principal(request))


def _check_environment_state(request, context, adapter, actor):
    with request.app.state.database.transaction() as connection:
        require_access(
            connection,
            request.app.state.settings,
            actor,
            context["repository"],
            context["environment"],
            "plan_infrastructure",
        )
        record = row(
            connection,
            environments,
            f"{context['repository']}:{context['environment']}",
        )
        repository = row(connection, registered_repositories, context["repository"])
        if (
            not record
            or record["revision"] != context["environment_revision"]
            or not repository
            or effective_config(connection, request.app.state.settings, repository)
            != str(adapter.settings.config)
        ):
            raise ComposerError(
                "Environment configuration changed. Retry inspection.", 409
            )


def _managed_layers(request, repository=None):
    layers = []
    for reference in _configuration_references(request, repository):
        if not str(reference).startswith("git+"):
            layers.append(
                (
                    Path(reference),
                    Path(reference).read_text(),
                    hashlib.sha256(Path(reference).read_bytes()).hexdigest(),
                )
            )
            continue
        match = re.fullmatch(
            r"git\+https://github\.com/([^/]+)/([^/]+)//(.+?)@([^@]+)", str(reference)
        )
        if not match:
            raise ComposerError(
                "Managed configuration must be a GitHub git file reference.", 503
            )
        if not _github_token(request):
            raise ComposerError("Sign in to read managed configuration.", 401)
        content, revision = GitHubClient(
            _github_token(request), request.app.state.github_transport
        ).repository_file(match[1], match[2].removesuffix(".git"), match[3], match[4])
        layers.append((reference, content, revision))
    return layers


def _config_revision(request, repository=None):
    layers = _managed_layers(request, repository)
    request.state.inspection_layers = (repository, layers)
    return hashlib.sha256(
        json.dumps(
            [(str(reference), revision) for reference, _, revision in layers]
        ).encode()
    ).hexdigest()


def _open_snapshot(request: Request, source: str, **metadata) -> dict[str, Any]:
    snapshot = {
        **metadata,
        "source": source,
        "principal": _principal(request),
        "config_revision": _config_revision(request, metadata.get("repository")),
    }
    result = {
        **metadata,
        "source": source,
        "snapshot": _signer(request).dumps(snapshot),
        "editable": True,
        "reason": None,
    }
    try:
        result["document"] = load_document(source)
    except ComposerError as exc:
        result.update(editable=False, reason=str(exc), document=None)
    if request.app.state.settings.execution_enabled and metadata.get("repository"):
        with request.app.state.database.transaction() as connection:
            result["can_author"] = (
                "author"
                in access_for(
                    connection,
                    request.app.state.settings,
                    snapshot["principal"],
                    metadata["repository"],
                )["capabilities"]
            )
    else:
        result["can_author"] = True
    return result


def _snapshot(
    request: Request, token: str, check_configuration: bool = True
) -> dict[str, Any]:
    try:
        snapshot = _signer(request).loads(token, max_age=86400)
    except BadSignature as exc:
        raise ComposerError(
            "This draft snapshot expired. Reopen the stack.", 409
        ) from exc
    if snapshot["principal"] != _principal(request):
        raise ComposerError("This draft belongs to a different session user.", 403)
    if snapshot.get("repository"):
        _authoring_access(request, snapshot["repository"])
    if check_configuration and snapshot["config_revision"] != _config_revision(
        request, snapshot.get("repository")
    ):
        raise ComposerError(
            "Platform configuration changed. Reopen the stack before reviewing.", 409
        )
    return snapshot


def _draft_source(
    request: Request, draft: Draft, *, check_configuration=True
) -> tuple[dict[str, Any], str]:
    snapshot, edits = _draft_edits(
        request, draft, check_configuration=check_configuration
    )
    source = request.app.state.adapter.author(
        snapshot["source"], edits, snapshot["path"]
    )["source"]
    _check_draft_source(request, snapshot, source)
    return snapshot, source


def _draft_edits(request, draft, *, check_configuration=True):
    snapshot = (
        _snapshot(request, draft.snapshot)
        if check_configuration
        else _snapshot(request, draft.snapshot, check_configuration=False)
    )
    if snapshot.get("repository") and _draft_modified(snapshot, draft):
        _authoring_write_access(request, snapshot["repository"])
    return snapshot, _source_edits(
        snapshot["source"],
        draft.components,
        draft.disabled_associations,
        {
            name: position.model_dump()
            for name, position in draft.positions.items()
            if name in draft.components
        },
        [
            route.model_dump(
                exclude={"label_position"} if route.label_position is None else set()
            )
            for route in draft.routes
        ],
    ) + (
        root_rename_edits(load_document(snapshot["source"]), draft.renames)
        if draft.renames
        else []
    )


def _check_draft_source(request, snapshot, source):
    # Enforce document limits on the edited source too.
    load_document(source)
    if source != snapshot["source"] and snapshot.get("repository"):
        _authoring_write_access(request, snapshot["repository"])


def _draft_modified(snapshot, draft):
    document = load_document(snapshot["source"])
    diagram = (document.get("metadata") or {}).get("stacksmith_composer") or {}
    return (
        bool(draft.renames)
        or draft.components != document.get("components", {})
        or draft.disabled_associations != document.get("disabled_associations", [])
        or {name: position.model_dump() for name, position in draft.positions.items()}
        != diagram.get("positions", {})
        or [
            route.model_dump(
                exclude={"label_position"} if route.label_position is None else set()
            )
            for route in draft.routes
        ]
        != diagram.get("routes", [])
    )


def _review(
    request: Request, draft: Draft, *, fresh=False
) -> tuple[dict[str, Any], dict[str, Any]]:
    started = perf_counter()
    timings = {}
    snapshot, edits = _draft_edits(request, draft)
    context = prepare_inputs(
        request, _inspection_context(request, snapshot, draft.environment)
    )
    adapter = _adapter(request, snapshot.get("repository"), context)
    timings["preflight"] = perf_counter() - started
    stage_started = perf_counter()
    result = request.app.state.editing_contexts.review(
        None
        if fresh
        else context_key(snapshot, context, request.app.state.settings.execution_image),
        snapshot["source"],
        edits,
        snapshot["path"],
        partial(_prepare_review, request, snapshot, edits, context, adapter),
    )
    timings["inspection"] = perf_counter() - stage_started
    stage_started = perf_counter()
    source = result["source"]
    _check_draft_source(request, snapshot, source)
    catalog = result["catalog"]
    associations = result["associations"]
    document = associations.pop("_document", None) or load_document(source)
    validate_root_references(document, catalog)
    if not result.pop("_context_checked", False):
        _check_inspection_context(request, context, adapter)
    timings["completion"] = perf_counter() - stage_started
    stage_started = perf_counter()
    result = {
        "source": source,
        "diff": source_diff(snapshot["source"], source, snapshot["path"]),
        "edges": inspect_graph(document.get("components", {}), catalog, associations),
        "associations": associations,
        "catalog": catalog,
        "validation": (
            f"References and associations are valid for deployment {context['environment']}. Deployment planning remains separate."
            if context and context["environment"]
            else "Stack preview references and associations are valid. Deployment inputs were excluded."
        ),
        "context": _context_metadata(context),
    }
    timings["graph"] = perf_counter() - stage_started
    request.app.state.review_timings.observe(perf_counter() - started)
    logging.getLogger(__name__).info(
        "Review completed: total_seconds=%.3f stage_seconds=%s",
        perf_counter() - started,
        timings,
    )
    return snapshot, result


def _prepare_review(request, snapshot, edits, context, adapter):
    result = adapter.review(
        snapshot["source"], edits, snapshot["path"], _github_token(request)
    )
    _check_inspection_context(request, context, adapter)
    result["_context_checked"] = True
    return result


@platform_router.get("/session")
def session_info(request: Request) -> dict[str, Any]:
    """Return connection mode and the current user's public login."""
    session = request.app.state.sessions.get(
        request.cookies.get("platform_session", "")
    )
    return {
        "mode": "github" if request.app.state.settings.github_enabled else "demo",
        "login": session[0] if session else None,
        "execution_enabled": request.app.state.settings.execution_enabled,
    }


@composer_router.get("/editor-status")
def editor_status(request: Request):
    """Report preparation, latency, notification, and database metrics to admins.

    Args:
        request: Authenticated platform administrator request.

    Returns:
        Operational counters without sources, principal identities, or credentials.

    Raises:
        ComposerError: The caller is not an administrator.
    """
    from sqlalchemy import func, text

    from .access import is_admin
    from .database import runs

    if not is_admin(request.app.state.settings, _principal(request)):
        raise ComposerError("A platform administrator session is required.", 403)
    with request.app.state.database.transaction() as connection:
        inspections = dict(
            connection.execute(
                runs.select()
                .with_only_columns(runs.c.status, func.count())
                .where(runs.c.action == "inspection")
                .group_by(runs.c.status)
            ).all()
        )
        database = (
            dict(
                connection.execute(
                    text(
                        "SELECT numbackends AS connections, current_setting('max_connections')::int AS max_connections FROM pg_stat_database WHERE datname=current_database()"
                    )
                )
                .mappings()
                .one()
            )
            if connection.dialect.name == "postgresql"
            else {}
        )
    return {
        "contexts": request.app.state.editing_contexts.metrics(),
        "notifications": request.app.state.configuration_events.metrics(),
        "database_pool": request.app.state.database.metrics(),
        "database": database,
        "inspections": inspections,
        "review_backend_latency": request.app.state.review_timings.snapshot(),
        "github_requests": request.app.state.github_transport.request_count,
        "github_conditional_hits": request.app.state.github_transport.conditional_hits,
        "github_counter_id": request.app.state.github_transport.counter_id,
    }


@composer_router.get("/configuration-events", dependencies=[Depends(_identity)])
async def configuration_events(request: Request):
    """Stream configuration wakeups without configuration or credentials.

    Args:
        request: Authenticated editor request.

    Returns:
        A reconnectable stream; clients fetch authorized state on every wakeup.
    """
    return StreamingResponse(
        _configuration_stream(request),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-store", "X-Accel-Buffering": "no"},
    )


async def _configuration_stream(request):
    with request.app.state.configuration_events.subscribe() as changed:
        # Reconnecting clients revalidate changes missed while disconnected.
        yield "event: configuration\ndata: {}\n\n"
        while not await request.is_disconnected():
            try:
                await asyncio.wait_for(changed.wait(), 15)
            except TimeoutError:
                yield ": keepalive\n\n"
            else:
                changed.clear()
                yield "event: configuration\ndata: {}\n\n"


@composer_router.post("/refresh-snapshot")
def refresh_snapshot(request: Request, body: InspectionContextInput):
    """Refresh configuration identity while preserving a draft's original base.

    Args:
        request: Authenticated editor request.
        body: Signed original snapshot and selected environment.

    Returns:
        A current signed snapshot; authored edits remain in the editor.

    Raises:
        ComposerError: The snapshot or current repository access is invalid.
    """
    snapshot = _snapshot(request, body.snapshot, check_configuration=False)
    return _open_snapshot(
        request,
        snapshot["source"],
        **{
            key: value
            for key, value in snapshot.items()
            if key not in {"source", "principal", "config_revision"}
        },
    )


@platform_router.get("/health")
def health() -> dict[str, str]:
    """Report API liveness independently of external services."""
    return {"status": "ok"}


@platform_router.get("/catalog", dependencies=[Depends(_identity)])
def catalog(request: Request) -> dict[str, Any]:
    """Return public component metadata from the separate Stacksmith installation."""
    return _adapter(request).catalog(_github_token(request))


@composer_router.post("/catalog")
def environment_catalog(
    request: Request, body: InspectionContextInput
) -> dict[str, Any]:
    """Inspect component metadata for a signed stack's inspection context.

    Args:
        request: Authenticated request carrying platform configuration.
        body: Signed stack snapshot and optional selected environment.

    Returns:
        Component catalog and inspected context identity.

    Raises:
        ComposerError: The snapshot, environment, access, or inspection is invalid.
    """
    context = _inspection_context(
        request, _snapshot(request, body.snapshot), body.environment
    )
    adapter = _adapter(request, context["repository"] if context else None, context)
    result = adapter.catalog(_github_token(request))
    _check_inspection_context(request, context, adapter)
    return {"catalog": result, "context": _context_metadata(context)}


@composer_router.get("/inspection-targets")
def inspection_targets(request: Request, repository: str) -> dict[str, Any]:
    """List visible deployment targets and explain inspection availability.

    Args:
        request: Authenticated browser request.
        repository: Repository selected for Composer inspection.

    Returns:
        Visible targets, availability, and deployment configuration capability.

    Raises:
        ComposerError: Execution is disabled or repository access is unavailable.
    """
    from .control import list_environments

    if not request.app.state.settings.execution_enabled:
        raise ComposerError("Deployment inspection is disabled.", 404)
    _authoring_access(request, repository)
    return _target_choices(
        request,
        repository,
        list_environments(request, *repository.split("/")),
        _principal(request),
    )


def _target_choices(request, repository, targets, actor):
    with request.app.state.database.transaction() as connection:
        return {
            "targets": [
                {"name": target["name"], "can_inspect": target["can_inspect"]}
                for target in targets
            ],
            "availability": (
                "unconfigured"
                if connection.execute(
                    environments.select().where(
                        (environments.c.repository == repository)
                        & (environments.c.active == 1)
                    )
                ).first()
                is None
                else "available"
                if any(target["can_inspect"] for target in targets)
                else "forbidden"
            ),
            "can_configure": "configure_environment"
            in access_for(
                connection,
                request.app.state.settings,
                actor,
                repository,
            )["capabilities"],
        }


@platform_router.get("/platform", dependencies=[Depends(_identity)])
def platform(request: Request) -> dict[str, Any]:
    """Return an allowlisted, descriptive view of the trusted platform config."""
    layers = [
        {
            "reference": str(reference),
            "summary": platform_summary(
                reference if isinstance(reference, Path) else content
            ),
        }
        for reference, content, _ in _managed_layers(request)
    ]
    return {**layers[-1]["summary"], "layers": layers}


@platform_router.get("/sample", dependencies=[Depends(_identity)])
def sample(request: Request) -> dict[str, Any]:
    """Open the bundled example in anonymous local demonstration mode."""
    if request.app.state.settings.github_enabled:
        raise ComposerError("The local example is disabled in GitHub mode.", 404)
    return _open_snapshot(
        request,
        request.app.state.settings.sample.read_text(),
        repository=None,
        path="stack.yaml",
        branch="local example",
        head=None,
    )


@composer_router.post("/rename-component")
def rename_component(request: Request, draft: RenameDraft) -> dict[str, Any]:
    """Rename a component and update static authored references.

    Args:
        request: Authenticated request carrying the signed source snapshot.
        draft: Current draft and the requested identifier change.

    Returns:
        Component definitions with references updated.

    Raises:
        ComposerError: The name, access, or an expression prevents a safe rename.
    """
    snapshot = _snapshot(request, draft.snapshot)
    if snapshot.get("repository"):
        _authoring_write_access(request, snapshot["repository"])
    if len(draft.renames) >= 100:
        raise ComposerError("This draft has reached the component rename limit.")
    root_rename_edits(
        load_document(snapshot["source"]),
        [*draft.renames, ComponentRename(old=draft.old_name, new=draft.new_name)],
    )
    return {
        "components": rename_components(
            draft.components, draft.old_name, draft.new_name
        )
    }


@composer_router.post("/review")
def review(request: Request, draft: Draft) -> dict[str, Any]:
    """Inspect a draft and return the exact authored source and diff for review."""
    return _review(request, draft)[1]


@composer_router.post("/source")
def draft_source(request: Request, draft: Draft) -> dict[str, str]:
    """Render authored changes without running Stacksmith inspection.

    Args:
        request: Request carrying the session and managed configuration.
        draft: Proposed edits, including diagram layout.

    Returns:
        Unvalidated authored source and its diff against the snapshot.

    Raises:
        ComposerError: If the snapshot or authored document is invalid.
    """
    snapshot, source = _draft_source(request, draft, check_configuration=False)
    return {
        "source": source,
        "diff": source_diff(snapshot["source"], source, snapshot["path"]),
    }


@composer_router.post("/graph")
def authored_graph(request: Request, draft: Draft) -> dict[str, Any]:
    """Extract the current authored graph independently of resolved inspection.

    Args:
        request: Authenticated request carrying the source signer.
        draft: Proposed authored component definitions.

    Returns:
        Authored edges and path-specific structural diagnostics.

    Raises:
        ComposerError: The source snapshot, access, or document is invalid.
    """
    snapshot = _snapshot(request, draft.snapshot, False)
    if snapshot.get("repository") and _draft_modified(snapshot, draft):
        _authoring_write_access(request, snapshot["repository"])
    load_document(
        json.dumps(
            {**load_document(snapshot["source"]), "components": draft.components}
        )
    )
    return extract_graph(draft.components)


@composer_router.post("/resolution")
def resolution(request: Request, draft: ResolutionDraft) -> dict[str, Any]:
    """Explain the current draft with resolved values and templates visible.

    Args:
        request: Request carrying the authenticated session and managed config.
        draft: Proposed source edits and optional dotted configuration address.

    Returns:
        Effective configuration or queried value, with ordered resolution events.

    Raises:
        ComposerError: If the snapshot, authored source, or resolution is invalid.
    """
    return _resolve_draft(request, draft, _draft_source(request, draft))


def _resolve_draft(request, draft, prepared):
    context = _inspection_context(request, prepared[0], draft.environment)
    adapter = _adapter(request, prepared[0].get("repository"), context)
    result = adapter.resolution(prepared[1], _github_token(request), draft.query)
    _check_inspection_context(request, context, adapter)
    return {**result, "context": _context_metadata(context)}


@github_router.get("/repositories")
def repositories(
    request: Request,
    client: Annotated[GitHubClient, Depends(_github)],
) -> list[dict[str, Any]]:
    """List accessible GitHub repositories available to Composer."""
    return _repository_discovery(request, client)["repositories"]


@github_router.get("/repository-discovery")
def repository_discovery(
    request: Request,
    client: Annotated[GitHubClient, Depends(_github)],
) -> dict[str, Any]:
    """Explain an empty repository list without exposing inaccessible repositories.

    Args:
        request: Authenticated browser request.
        client: GitHub client scoped to the signed-in user.

    Returns:
        Visible repositories and the access stage responsible for an empty list.

    Raises:
        ComposerError: GitHub or platform authorization fails.
    """
    return _repository_discovery(request, client)


def _repository_discovery(request, client, repository=None):
    with request.app.state.database.transaction() as connection:
        registered = (
            row(connection, registered_repositories, repository) if repository else None
        )
    discovered = (
        client.repositories(
            repository=repository,
            **({"installation_id": registered["installation"]} if registered else {}),
        )
        if repository is not None
        else client.repositories()
    )
    with request.app.state.database.transaction() as connection:
        discovered = filtered(discovered, get_filter(connection))
    if not discovered:
        return {"repositories": [], "empty_reason": "github_access"}
    if request.app.state.settings.execution_enabled:
        synchronize_user(request.app.state.database, discovered)
    return {"repositories": discovered, "empty_reason": None}


@github_router.get("/github-connection")
def github_connection(request: Request):
    """Return GitHub's installation links for the configured App.

    Args:
        request: Signed-in browser request.

    Returns:
        Installation URL and installations visible to the signed-in user.

    Raises:
        ComposerError: Authentication or GitHub App discovery fails.
    """
    client = _github(request)
    installations = list(client._pages("/user/installations", "installations"))
    slug = request.app.state.settings.github_app_slug
    if not slug and request.app.state.settings.github_private_key:
        slug = request.app.state.provider.app()["slug"]
    if not slug and installations:
        slug = installations[0].get("app_slug", "")
    return {
        "install_url": f"https://github.com/apps/{slug}/installations/new"
        if slug
        else None,
        "installations": [
            {
                "id": installation["id"],
                "account": installation.get("account", {}).get("login", ""),
                "account_type": installation.get("account", {}).get("type"),
                "management_url": installation.get("html_url"),
            }
            for installation in installations
        ],
    }


@github_router.get("/auth/setup")
def github_setup(request: Request, installation_id: int | None = None):
    """Verify an installation return and reopen repository discovery.

    Args:
        request: GitHub installation return request.
        installation_id: Untrusted installation identifier supplied by GitHub.

    Returns:
        Redirect to sign-in or GitHub connection settings.

    Raises:
        ComposerError: The installation is unavailable to the signed-in user.
    """
    if _identity(request) is None:
        return RedirectResponse(
            request.app.state.settings.public_url + "/api/auth/github"
        )
    client = _github(request)
    if installation_id is not None and installation_id not in {
        installation["id"]
        for installation in client._pages("/user/installations", "installations")
    }:
        raise ComposerError(
            "This GitHub installation is unavailable to your account.", 403
        )
    _repository_discovery(request, client)
    return RedirectResponse(
        request.app.state.settings.public_url
        + "/settings?section=github&github_setup=complete"
    )


@github_router.get("/repositories/{owner}/{repo}/stacks")
def stacks(
    request: Request,
    owner: str,
    repo: str,
    client: Annotated[GitHubClient, Depends(_github)],
):
    """Discover conventional stack paths on a repository's default branch."""
    _authoring_access(request, f"{owner}/{repo}")
    return client.stacks(owner, repo)


@github_router.get("/repositories/{owner}/{repo}/stack")
def open_stack(
    request: Request,
    owner: str,
    repo: str,
    path: str,
    client: Annotated[GitHubClient, Depends(_github)],
):
    """Capture an immutable source snapshot for a discovered GitHub stack."""
    _authoring_access(request, f"{owner}/{repo}")
    discovered = client.stacks(owner, repo)
    if path not in discovered["paths"]:
        raise ComposerError("Select a discovered stack path.", 404)
    return _open_snapshot(
        request,
        client.source(owner, repo, path, discovered["head"]),
        repository=f"{owner}/{repo}",
        path=path,
        branch=discovered["branch"],
        head=discovered["head"],
    )


@github_router.post("/pull-requests")
def pull_request(
    request: Request, draft: Draft, client: Annotated[GitHubClient, Depends(_github)]
):
    """Reinspect reviewed changes and open a PR under the user's permissions."""
    snapshot, inspected = _review(request, draft, fresh=True)
    if not snapshot["repository"] or not inspected["diff"]:
        raise ComposerError("There are no repository changes to propose.")
    _authoring_write_access(request, snapshot["repository"])
    return client.create_pr(
        snapshot,
        inspected["source"],
        draft=request.app.state.settings.draft_pull_requests,
        inspection_context=inspected["context"],
    )


@auth_router.get("/auth/github")
def github_login(request: Request) -> RedirectResponse:
    """Begin GitHub authorization on the configured public origin.

    Args:
        request: Request carrying the deployment settings.

    Returns:
        Redirect to the public origin or GitHub with a signed state cookie.

    Raises:
        ComposerError: GitHub App credentials are not configured.
    """
    settings = request.app.state.settings
    if not settings.github_enabled:
        raise ComposerError("GitHub App credentials have not been configured.", 503)
    if str(request.base_url).rstrip("/") != settings.public_url:
        return RedirectResponse(settings.public_url + "/api/auth/github")
    state = secrets.token_urlsafe(32)
    response = RedirectResponse(
        "https://github.com/login/oauth/authorize?"
        + urlencode(
            {
                "client_id": settings.github_client_id,
                "redirect_uri": settings.public_url + "/api/auth/callback",
                "state": state,
            }
        )
    )
    response.set_cookie(
        "platform_oauth",
        _signer(request).dumps(state),
        max_age=600,
        httponly=True,
        secure=settings.public_url.startswith("https:"),
        samesite="lax",
        path="/api/auth",
    )
    return response


def _exchange_token(
    settings: Settings, code: str, transport: GitHubTransport
) -> dict[str, Any]:
    try:
        response = transport.request(
            "POST",
            "https://github.com/login/oauth/access_token",
            headers={"Accept": "application/json"},
            data={
                "client_id": settings.github_client_id,
                "client_secret": settings.github_client_secret,
                "code": code,
                "redirect_uri": settings.public_url + "/api/auth/callback",
            },
        )
        if not 200 <= response.status_code < 300:
            raise ComposerError("GitHub rejected the authorization exchange.", 502)
        token = response.json()
    except (ComposerError, ValueError) as exc:
        raise ComposerError(
            "GitHub authorization failed. Please sign in again.", 502
        ) from exc
    if "access_token" not in token:
        raise ComposerError(
            "GitHub authorization was denied or expired. Please sign in again.", 401
        )
    return token


@auth_router.get("/auth/callback")
def github_callback(
    request: Request,
    code: str | None = None,
    state: str | None = None,
    error: str | None = None,
) -> RedirectResponse:
    """Validate GitHub authorization and create a server-side session.

    Args:
        request: Request carrying the signed state cookie and session store.
        code: Temporary authorization code supplied by GitHub.
        state: State returned by GitHub to match the sign-in cookie.
        error: Authorization denial returned by GitHub.

    Returns:
        Redirect to Settings with an opaque session cookie.

    Raises:
        ComposerError: Authorization is incomplete, denied, invalid, or expired.
    """
    if not request.cookies.get("platform_oauth"):
        raise ComposerError(
            "GitHub sign-in cookie is missing. Please start sign-in again from the platform."
            " Use the configured Composer address and allow cookies.",
            401,
        )
    try:
        expected = _signer(request).loads(
            request.cookies.get("platform_oauth", ""), max_age=600
        )
    except SignatureExpired as exc:
        raise ComposerError(
            "GitHub login expired. Please start sign-in again.", 401
        ) from exc
    except BadSignature as exc:
        raise ComposerError(
            "GitHub sign-in cookie is invalid. Please start sign-in again.", 401
        ) from exc
    if state is None or not secrets.compare_digest(expected, state):
        raise ComposerError(
            "GitHub login state did not match. Please start sign-in again.", 401
        )
    if error:
        raise ComposerError(
            "GitHub authorization was denied. Please start sign-in again.", 401
        )
    if not code:
        raise ComposerError(
            "GitHub authorization is incomplete. Please start sign-in again.", 401
        )
    settings = request.app.state.settings
    token = _exchange_token(settings, code, request.app.state.github_transport)
    request.app.state.sessions.delete(request.cookies.get("platform_session", ""))
    response = RedirectResponse(
        settings.public_url + "/settings?section=github&auth_return=1"
    )
    response.set_cookie(
        "platform_session",
        request.app.state.sessions.create(
            GitHubClient(
                token["access_token"], request.app.state.github_transport
            ).user()["login"],
            token["access_token"],
            token.get("expires_in", 28800),
        ),
        max_age=min(token.get("expires_in", 28800), 28800),
        httponly=True,
        secure=settings.public_url.startswith("https:"),
        samesite="lax",
    )
    response.delete_cookie("platform_oauth", path="/api/auth")
    return response


@auth_router.post("/auth/logout")
def logout(request: Request) -> JSONResponse:
    """Invalidate the current session and expire its cookie."""
    request.app.state.sessions.delete(request.cookies.get("platform_session", ""))
    response = JSONResponse({"ok": True})
    response.delete_cookie("platform_session")
    return response


async def _protect_mutations(request: Request, call_next):
    if request.url.path.startswith("/api/v1/") and (
        request.headers.get("authorization")
        or request.url.path == "/api/v1/webhooks/github"
    ):
        return await call_next(request)
    if request.method not in {"GET", "HEAD", "OPTIONS"}:
        origin = request.headers.get("origin")
        if request.headers.get("x-composer-request") != "1" or (
            origin and origin != request.app.state.settings.public_url
        ):
            return JSONResponse(
                {"detail": "Request origin could not be verified."}, status_code=403
            )
    return await call_next(request)


async def _composer_error(request: Request, exc: ComposerError):
    if request.url.path in {"/api/auth/github", "/api/auth/callback"}:
        response = RedirectResponse(
            request.app.state.settings.public_url
            + "/?"
            + urlencode({"auth_error": str(exc)}),
            status_code=303,
        )
        response.delete_cookie("platform_oauth", path="/api/auth")
        return response
    return JSONResponse(
        {
            "detail": str(exc),
            **({"diagnostics": exc.diagnostics} if exc.diagnostics else {}),
        },
        status_code=exc.status,
    )


def _ready(request: Request):
    from sqlalchemy.exc import SQLAlchemyError

    from .database import grants, sessions

    if not request.app.state.configuration_events.connected:
        return JSONResponse(
            {"status": "configuration listener unavailable"}, status_code=503
        )
    try:
        with request.app.state.database.transaction() as connection:
            connection.execute(sessions.select().limit(1))
            if request.app.state.settings.execution_enabled:
                connection.execute(grants.select().limit(1))
    except SQLAlchemyError:
        return JSONResponse({"status": "database unavailable"}, status_code=503)
    return {"status": "ready"}


async def _dispatch_supervisors(request, call_next):
    response = await call_next(request)
    if (
        response.status_code < 400
        and request.method in {"POST", "PUT", "PATCH", "DELETE"}
        and ("configuration" in request.url.path or "/environments" in request.url.path)
        and request.app.state.database.engine.dialect.name != "postgresql"
    ):
        request.app.state.configuration_events.changed()
    if (
        request.method == "POST"
        and response.status_code < 400
        and request.app.state.dispatcher is not None
    ):
        response.background = BackgroundTasks(
            [response.background] if response.background else []
        )
        response.background.add_task(request.app.state.dispatcher.dispatch_pending)
    return response


router.include_router(platform_router)
router.include_router(composer_router)
router.include_router(github_router)
router.include_router(auth_router)


@asynccontextmanager
async def _lifespan(app):
    await app.state.configuration_events.start()
    try:
        if app.state.settings.execution_enabled:
            try:
                await to_thread(
                    synchronize,
                    app.state.database,
                    await to_thread(app.state.provider.catalog),
                    complete=True,
                )
            except ComposerError:
                # Keep the UI available so an administrator can retry reconciliation.
                logging.getLogger(__name__).exception(
                    "Initial GitHub discovery failed; refresh repositories to retry"
                )
        yield
    finally:
        await app.state.configuration_events.close()
        await to_thread(app.state.github_transport.close)


def create_app(settings: Settings | None = None) -> FastAPI:
    """Create the independently packaged Composer HTTP API.

    Args:
        settings: Explicit configuration for tests or embedded development.

    Returns:
        FastAPI application with a separate CLI adapter and encrypted session store.
    """
    settings = settings or Settings()
    from loguru import logger

    logger.disable("stacksmith")
    configure_ca_bundle(CA_BUNDLE_MOUNT_PATH if settings.ca_bundle else None)
    app = FastAPI(title="Stacksmith Platform", version="0.1.0", lifespan=_lifespan)
    app.state.settings = settings
    app.state.review_timings = TimingSamples()
    from .control import router as control_router
    from .database import Database, local_database
    from .execution_credentials import ExecutionCredentialCatalog
    from .factories import artifact_storage
    from .provider import GitHubProvider
    from .secret_sources import SecretCatalog

    app.state.database = (
        Database.from_settings(app.state.settings)
        if app.state.settings.database_url
        else local_database(app.state.settings.database, app.state.settings.secret_key)
    )
    app.state.editing_contexts = EditingContexts(app.state.database)
    app.state.adapter = StacksmithAdapter(
        app.state.settings, database=app.state.database
    )
    app.state.configuration_events = ConfigurationEvents(
        settings.database_listener_url or settings.database_url,
        app.state.editing_contexts,
    )
    from .scheduling import KubernetesDispatcher

    app.state.dispatcher = (
        KubernetesDispatcher(app.state.settings, app.state.database)
        if (
            app.state.settings.execution_enabled
            and app.state.settings.supervision_mode == "kubernetes-jobs"
        )
        else None
    )
    app.state.sessions = SessionStore(app.state.database)
    app.state.github_transport = GitHubTransport()
    app.state.provider = GitHubProvider(app.state.settings, app.state.github_transport)
    app.state.secret_catalog = SecretCatalog(app.state.settings)
    app.state.execution_credential_catalog = ExecutionCredentialCatalog(settings)
    if app.state.settings.execution_enabled:
        app.state.artifacts = artifact_storage(app.state.settings)
    app.include_router(control_router)
    app.add_middleware(
        TrustedHostMiddleware,
        allowed_hosts=[
            urlparse(app.state.settings.public_url).hostname,
            urlparse(app.state.settings.execution_callback_url).hostname or "localhost",
            "127.0.0.1",
            "localhost",
            "testserver",
        ],
    )
    app.middleware("http")(_protect_mutations)
    app.middleware("http")(_dispatch_supervisors)
    app.add_exception_handler(ComposerError, _composer_error)
    app.include_router(router)
    app.add_api_route("/api/ready", _ready, methods=["GET"])
    return app


def _authoring_access(request, repository):
    if not request.app.state.settings.execution_enabled:
        return
    if getattr(request.state, "authoring_repositories", None) is None:
        request.state.authoring_repositories = {}
    if repository not in request.state.authoring_repositories:
        request.state.authoring_repositories[repository] = any(
            item["name"] == repository
            for item in _repository_discovery(request, _github(request), repository)[
                "repositories"
            ]
        )
    if not request.state.authoring_repositories[repository]:
        raise ComposerError(
            "This repository is unavailable to your GitHub account and App.", 403
        )


def _authoring_write_access(request, repository):
    if not request.app.state.settings.execution_enabled:
        return
    from .control import _identity as identity
    from .control import _repository_access

    _authoring_access(request, repository)
    _repository_access(
        request, identity(request), repository, permission="author", github_checked=True
    )
