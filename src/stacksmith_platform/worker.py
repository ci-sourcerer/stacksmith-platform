import hashlib
import hmac
import json
import logging
import os
import secrets as random_secrets
import signal
import socket
import time
from contextlib import closing
from threading import Event
from uuid import uuid4

import httpx
from botocore.exceptions import ClientError
from cryptography.fernet import Fernet, InvalidToken
from pydantic import BaseModel, ConfigDict, Field, ValidationError
from sqlalchemy import and_, func, or_

from .access import require_access, require_workloads, run_workloads
from .certificates import CA_BUNDLE_MOUNT_PATH, configure_ca_bundle
from .configuration import effective_config
from .database import (
    Database,
    artifacts,
    audit,
    environments,
    execution_credential_bindings,
    execution_credential_sessions,
    locks,
    phase_claims,
    prepared_contexts,
    repositories,
    row,
    runs,
    secrets,
    workers,
)
from .errors import ComposerError
from .execution_credentials import ExecutionCredentialCatalog
from .factories import artifact_storage, execution_driver
from .github_transport import GitHubTransport
from .inspection_results import _diagnostic_text, _inspection_failure
from .integrations import (
    ArtifactNotFoundError,
    ArtifactStorage,
    ExecutionDriver,
    SecretSourceError,
)
from .lifecycle import MUTATIONS, TERMINAL, deployment_lock, mutation_phase
from .provider import GitHubProvider
from .secret_sources import SecretCatalog
from .settings import Settings

LOGGER = logging.getLogger(__name__)
_STOP = Event()


def _request_shutdown(signum, frame):
    _STOP.set()


def _serve(worker, stop):
    next_tick = time.monotonic()
    next_inspection = next_tick
    try:
        while not stop.is_set():
            if time.monotonic() >= next_tick:
                worker.tick(inspections=False)
                next_tick = time.monotonic() + 2
            if not stop.is_set() and time.monotonic() >= next_inspection:
                worker._reconcile_inspections()
                next_inspection = (
                    time.monotonic() + worker.settings.inspection_poll_interval
                )
            stop.wait(max(0, min(next_tick, next_inspection) - time.monotonic()))
    finally:
        with worker.database.transaction() as connection:
            connection.execute(
                workers.delete().where(workers.c.id == worker.identifier)
            )


class PhaseResult(BaseModel):
    """Bounded machine-readable result returned by an execution image."""

    model_config = ConfigDict(extra="ignore", strict=True)
    exit_code: int = Field(ge=0, le=255)
    revision: str = Field(pattern=r"^[0-9a-f]{40}$")
    phase: str = Field(max_length=30)
    manifest_digest: str | None = Field(default=None, max_length=128)
    plan_digest: str | None = Field(default=None, max_length=128)
    stacksmith_version: str = Field(default="", max_length=1000)
    reports: dict = Field(default_factory=dict, max_length=16)


class RunReconciler:
    """Reconcile durable phase records with isolated execution resources.

    Args:
        settings: Deployment settings.
        database: Shared control-plane database.
        driver: Docker or Kubernetes execution driver.
        storage: Artifact storage adapter.
        provider: GitHub installation provider.
        secret_catalog: Optional configured secret-source catalog.
    """

    def __init__(
        self,
        settings,
        database,
        driver: ExecutionDriver,
        storage: ArtifactStorage,
        provider,
        secret_catalog: SecretCatalog | None = None,
    ):
        self.settings = settings
        self.database = database
        self.driver = driver
        self.storage = storage
        self.provider = provider
        self.secret_catalog = secret_catalog or SecretCatalog(settings)
        self.execution_credential_catalog = ExecutionCredentialCatalog(settings)
        self.identifier = settings.worker_id or (
            socket.gethostname() if os.getpid() == 1 else uuid4().hex
        )

    def maintenance(self) -> None:
        """Expire approvals and remove retained data in a finite maintenance pass.

        Returns:
            None.
        """
        self._purge()
        with self.database.transaction() as connection:
            self._expire(connection)

    def reconcile_run(
        self, run_id: str | None = None, generation: int | None = None
    ) -> bool:
        """Claim and reconcile one runnable phase under a renewable ownership lease.

        Args:
            run_id: Restrict reconciliation to this run when provided.
            generation: Required Kubernetes supervisor generation.

        Returns:
            Whether a run was claimed for reconciliation.
        """
        return self._reconcile_matching(
            [runs.c.id == run_id] if run_id else [], generation
        )

    def _reconcile_matching(self, filters, generation=None):
        with self.database.transaction() as connection:
            conditions = [
                runs.c.status.in_(["queued", "running", "cancelling"]),
                or_(
                    runs.c.worker.is_(None),
                    runs.c.worker == self.identifier,
                    runs.c.lease < time.time(),
                ),
            ]
            conditions.extend(filters)
            if generation is None:
                conditions.append(runs.c.supervision.is_(None))
            candidates = (
                connection.execute(
                    runs.select()
                    .where(and_(*conditions))
                    .order_by(runs.c.created)
                    .with_for_update(skip_locked=True)
                    .limit(100)
                )
                .mappings()
                .all()
            )
            run = next(
                (
                    dict(candidate)
                    for candidate in candidates
                    if (
                        generation is None
                        or (candidate["supervision"] or {}).get("generation")
                        == generation
                    )
                    and (
                        candidate["action"] not in MUTATIONS
                        or self._lock(connection, candidate)
                    )
                ),
                None,
            )
            if run is None:
                return False
            connection.execute(
                runs.update()
                .where(runs.c.id == run["id"])
                .values(worker=self.identifier, lease=time.time() + 120)
            )
        if run["status"] == "queued":
            self._start(run)
        else:
            self._reconcile(run)
        return True

    def _owns(self, current, run):
        return (
            current["worker"] == self.identifier
            and current["phase"] == run["phase"]
            and (current["supervision"] or {}).get("generation")
            == (run["supervision"] or {}).get("generation")
        )

    def _lock(self, connection, run):
        return deployment_lock(connection, run)

    def _expire(self, connection):
        expired = (
            connection.execute(
                runs.select()
                .where(
                    and_(
                        runs.c.status.in_(
                            ["awaiting_authorization", "awaiting_approval", "queued"]
                        ),
                        runs.c.expires < time.time(),
                    )
                )
                .with_for_update(skip_locked=True)
            )
            .mappings()
            .all()
        )
        for run in expired:
            self._finish(connection, run, "expired", owned=False)

    def _credential_snapshot(self, run, phase):
        with self.database.transaction() as connection:
            bindings = (
                connection.execute(
                    secrets.select()
                    .where(
                        secrets.c.environment
                        == f"{run['repository']}:{run['environment']}"
                    )
                    .order_by(secrets.c.name)
                )
                .mappings()
                .all()
            )
            providers = [
                dict(binding)
                for binding in connection.execute(
                    execution_credential_bindings.select()
                    .where(
                        execution_credential_bindings.c.environment
                        == f"{run['repository']}:{run['environment']}"
                    )
                    .order_by(execution_credential_bindings.c.provider)
                ).mappings()
            ]
        for binding in providers:
            if (
                binding["provider"] not in self.settings.execution_credential_providers
                or binding["provider"]
                not in self.execution_credential_catalog.providers
            ):
                raise SecretSourceError("Execution credential provider is unavailable.")
            if phase in binding["phases"] and (
                (
                    set(self.settings.run_environment)
                    | {item["name"] for item in bindings if phase in item["phases"]}
                )
                & self.execution_credential_catalog.providers[
                    binding["provider"]
                ].conflicting_variables
            ):
                raise SecretSourceError(
                    "Environment variables conflict with the execution credential provider."
                )
        snapshot = {}
        resolved = [
            {
                **dict(binding),
                "value": self.secret_catalog.resolve(
                    binding["reference"], binding["json_key"], snapshot
                )
                if binding["reference"]
                else self.database.decrypt(binding["ciphertext"]),
            }
            for binding in bindings
        ]
        return (
            {
                **self.settings.run_environment,
                **{
                    binding["name"]: binding["value"]
                    for binding in resolved
                    if phase in binding["phases"]
                },
            },
            hmac.new(
                self.settings.secret_key.encode(),
                json.dumps(
                    {
                        "environment": self.settings.run_environment,
                        "bindings": resolved,
                        **(
                            {
                                "execution_credential_bindings": providers,
                                "execution_credential_providers": {
                                    binding[
                                        "provider"
                                    ]: self.settings.execution_credential_providers[
                                        binding["provider"]
                                    ].model_dump()
                                    for binding in providers
                                },
                            }
                            if providers
                            else {}
                        ),
                    },
                    sort_keys=True,
                ).encode(),
                hashlib.sha256,
            ).hexdigest(),
        )

    def _credential_authorization(self, run, phase, scope=None):
        scope = scope or {
            "repository": run["repository"],
            "environment": run["environment"],
            "environment_revision": run["spec"]["environment_revision"],
            "config": run["spec"]["config"],
            "actor": run["actor"],
        }
        if not scope.get("environment"):
            return None
        with self.database.transaction() as connection:
            bindings = (
                connection.execute(
                    execution_credential_bindings.select().where(
                        execution_credential_bindings.c.environment
                        == f"{scope['repository']}:{scope['environment']}"
                    )
                )
                .mappings()
                .all()
            )
        selected = [item for item in bindings if phase in item["phases"]]
        if not selected:
            return None
        if (
            len(selected) != 1
            or selected[0]["provider"]
            not in self.execution_credential_catalog.providers
        ):
            raise SecretSourceError("Execution credential provider is unavailable.")
        provider = selected[0]["provider"]
        return {
            "provider": provider,
            "provider_revision": self.execution_credential_catalog.revision(provider),
            "protocol": self.execution_credential_catalog.providers[provider].protocol,
            "url": (self.settings.execution_callback_url or self.settings.public_url)
            + f"/api/v1/executions/{run['id']}/credentials",
            "token": random_secrets.token_urlsafe(48),
            "scope": {**scope, "phase": phase},
        }

    def _create_credential_session(self, connection, run, authorization, timeout):
        if not authorization:
            return
        connection.execute(
            execution_credential_sessions.insert().values(
                id=f"{run['id']}:{run['phase']}",
                run=run["id"],
                phase=run["phase"],
                fence=run["fence"] + 1,
                provider=authorization["provider"],
                provider_revision=authorization["provider_revision"],
                token_digest=hashlib.sha256(
                    authorization["token"].encode()
                ).hexdigest(),
                expires=min(
                    time.time() + timeout, run["expires"] or time.time() + timeout
                ),
                scope=authorization["scope"],
                redactions=self.database.encrypt(json.dumps([authorization["token"]])),
            )
        )

    def _role_authorized(self, run, phase):
        with self.database.transaction() as connection:
            try:
                require_workloads(
                    connection,
                    self.settings,
                    run["actor"],
                    run["repository"],
                    run["environment"],
                    "execute" if run["action"] in MUTATIONS else "plan",
                    run_workloads(run["spec"]),
                )
                if run["spec"].get("event") == "pull_request":
                    require_workloads(
                        connection,
                        self.settings,
                        run["spec"].get("_authorizer", ""),
                        run["repository"],
                        run["environment"],
                        "approve",
                        run_workloads(run["spec"]),
                    )
                if mutation_phase(run["action"], phase):
                    require_workloads(
                        connection,
                        self.settings,
                        run["spec"].get("_approver", ""),
                        run["repository"],
                        run["environment"],
                        "approve",
                        run_workloads(run["spec"]),
                    )
            except ComposerError:
                self._finish(
                    connection,
                    run,
                    "cancelled",
                    "Role or workload access changed; remaining phases were stopped. Completed changes are retained.",
                )
                return False
        return True

    def _start(self, run):
        phase = run["spec"]["phases"][run["phase"]]
        if phase == "inspect":
            self._start_inspection(run)
            return
        if not self._role_authorized(run, phase):
            return
        if phase == "approval":
            with self.database.transaction() as connection:
                connection.execute(
                    runs.update()
                    .where(and_(runs.c.id == run["id"], runs.c.status == "queued"))
                    .values(
                        status="awaiting_approval",
                        expires=run["created"] + self.settings.approval_ttl,
                        updated=time.time(),
                    )
                )
                audit(
                    connection,
                    self.identifier,
                    "run.awaiting_approval",
                    run["id"],
                    context=run["result"].get("context_digest"),
                    plan=run["result"].get("plan_digest"),
                )
            return
        with self.database.transaction() as connection:
            environment = row(
                connection, environments, f"{run['repository']}:{run['environment']}"
            )
            repository = row(connection, repositories, run["repository"])
            if (
                not environment
                or not repository
                or not repository["enabled"]
                or not repository["deployment_enabled"]
                or environment["revision"] != run["spec"]["environment_revision"]
                or effective_config(connection, self.settings, repository)
                != run["spec"]["config"]
            ):
                self._finish(
                    connection,
                    run,
                    "failed",
                    "Environment configuration changed; request a new run",
                )
                return
        if (
            run["phase"] > 0
            and not run["spec"].get("revision_override", False)
            and run["spec"]["phases"][run["phase"] - 1] == "approval"
            and self.provider.revision(repository, repository["branch"])["sha"]
            != run["revision"]
        ):
            with self.database.transaction() as connection:
                self._finish(
                    connection,
                    run,
                    "failed",
                    "Default branch changed after approval; request a new run",
                )
            return
        try:
            credentials, credential_revision = self._credential_snapshot(run, phase)
            authorization = self._credential_authorization(run, phase)
        except SecretSourceError:
            with self.database.transaction() as connection:
                self._finish(
                    connection,
                    run,
                    "failed",
                    "Phase credentials are unavailable; verify deployment secret sources and bindings",
                )
            return
        if (
            run["result"].get("_credential_revision", credential_revision)
            != credential_revision
        ):
            with self.database.transaction() as connection:
                self._finish(
                    connection,
                    run,
                    "failed",
                    "Deployment credential references changed; request a new run",
                )
            return
        key = run["result"].get("_key") or self.database.encrypt(
            Fernet.generate_key().decode()
        )
        cipher = Fernet(self.database.decrypt(key).encode())
        try:
            github_token = self.provider.installation_token(
                self.provider.repository_installation(run["repository"])
            )
        except KeyError, ComposerError, httpx.HTTPError:
            with self.database.transaction() as connection:
                self._finish(
                    connection,
                    run,
                    "failed",
                    "Phase credentials are unavailable; verify deployment references and GitHub installation access",
                )
            return
        claim = random_secrets.token_urlsafe(48)
        prefix = f"executions/{run['id']}/{run['phase']}"
        execution = {
            "run": run["id"],
            "name": f"ss-{run['id']}-{run['phase']}",
            "input": f"{prefix}/input",
            "context": f"{prefix}/context",
            "result": f"{prefix}/result",
            "claim_digest": hashlib.sha256(claim.encode()).hexdigest(),
            "started": time.time(),
            "redactions": self.database.encrypt(
                json.dumps([github_token, claim, *credentials.values()])
            ),
            "key": key,
        }
        payload = {
            **run["spec"],
            "repository": run["repository"],
            "revision": run["revision"],
            "environment": run["environment"],
            "action": run["action"],
            "phase": phase,
            "github_token": github_token,
            "credentials": credentials,
            "credential_authorization": {
                name: authorization[name] for name in ("protocol", "url", "token")
            }
            if authorization
            else None,
            "timeout": self.settings.phase_timeout,
            "claim_url": (
                self.settings.execution_callback_url or self.settings.public_url
            )
            + f"/api/v1/executions/{run['id']}/claim",
            "claim_token": claim,
            "manifest_digest": run["result"].get("manifest_digest"),
            "context_digest": run["result"].get("context_digest"),
            "plan_digest": run["result"].get("plan_digest"),
            "context_url": self.storage.url(
                run["result"]["_context"],
                "get_object",
                self.settings.phase_timeout + 300,
            )
            if run["result"].get("_context")
            else None,
            "context_output_url": self.storage.url(
                execution["context"], "put_object", self.settings.phase_timeout + 300
            ),
            "result_output_url": self.storage.url(
                execution["result"], "put_object", self.settings.phase_timeout + 300
            ),
        }
        self.storage.put(
            execution["input"], cipher.encrypt(json.dumps(payload).encode())
        )
        with self.database.transaction() as connection:
            current = (
                connection.execute(
                    runs.select().where(runs.c.id == run["id"]).with_for_update()
                )
                .mappings()
                .one()
            )
            if current["status"] != "queued" or not self._owns(current, run):
                self.storage.delete(execution["input"])
                return
            self._create_credential_session(
                connection, run, authorization, self.settings.phase_timeout
            )
            connection.execute(
                runs.update()
                .where(runs.c.id == run["id"])
                .values(
                    status="running",
                    result={
                        **current["result"],
                        "_credential_revision": credential_revision,
                    },
                    execution=execution,
                    fence=run["fence"] + 1,
                    updated=time.time(),
                )
            )
            self._artifact(connection, run, "phase-input", execution["input"], "", True)
            self._artifact(
                connection, run, "phase-context", execution["context"], "", True
            )
            self._artifact(
                connection, run, "phase-result", execution["result"], "", True
            )
            audit(connection, self.identifier, "phase.started", run["id"], phase=phase)
        self._launch(run, execution)

    def _launch(self, run, execution):
        environment = {
            "PLATFORM_INPUT_URL": self.storage.url(
                execution["input"], "get_object", self.settings.phase_timeout + 300
            ),
            "PLATFORM_HANDOFF_KEY": self.database.decrypt(execution["key"]),
        }
        if self.settings.ca_bundle:
            environment["PLATFORM_CA_BUNDLE"] = CA_BUNDLE_MOUNT_PATH
        self.driver.launch(
            execution["name"],
            run["spec"]["image"],
            environment,
            self.settings.phase_timeout,
        )

    def _redaction_values(self, execution):
        values = json.loads(self.database.decrypt(execution["redactions"]))
        if execution.get("run"):
            with self.database.transaction() as connection:
                for session in connection.execute(
                    execution_credential_sessions.select().where(
                        execution_credential_sessions.c.run == execution["run"]
                    )
                ).mappings():
                    values.extend(
                        json.loads(self.database.decrypt(session["redactions"]))
                    )
        return sorted(set(values), key=len, reverse=True)

    def _redact(self, execution, text):
        for value in self._redaction_values(execution):
            if value:
                text = text.replace(value, "<redacted>")
        return text

    def _reconcile(self, run):
        execution = run["execution"]
        if execution is None:
            phase = run["spec"]["phases"][run["phase"]]
            with self.database.transaction() as connection:
                claimed = row(connection, phase_claims, f"{run['id']}:{run['phase']}")
                self._finish(
                    connection,
                    run,
                    "uncertain"
                    if claimed and mutation_phase(run["action"], phase)
                    else "cancelled"
                    if run["status"] == "cancelling"
                    else "failed",
                    "Phase had no execution to resume",
                )
            return
        state = self.driver.inspect(execution["name"])
        if execution.get("observed_state") != state:
            with self.database.transaction() as connection:
                connection.execute(
                    runs.update()
                    .where(
                        and_(runs.c.id == run["id"], runs.c.worker == self.identifier)
                    )
                    .values(execution={**execution, "observed_state": state})
                )
        phase = run["spec"]["phases"][run["phase"]]
        with self.database.transaction() as connection:
            claimed = row(connection, phase_claims, f"{run['id']}:{run['phase']}")
        if (
            run["status"] == "cancelling"
            or time.time() - execution["started"] > self.settings.phase_timeout + 120
        ):
            self.driver.cleanup(execution["name"])
            with self.database.transaction() as connection:
                self._finish(
                    connection,
                    run,
                    "uncertain"
                    if claimed and mutation_phase(run["action"], phase)
                    else "cancelled"
                    if run["status"] == "cancelling"
                    else "failed",
                    "Execution terminated; inspect remote state before retrying"
                    if claimed and mutation_phase(run["action"], phase)
                    else "Execution cancelled or timed out",
                )
            return
        if state == "missing":
            if not claimed:
                self._launch(run, execution)
                return
            with self.database.transaction() as connection:
                self._finish(
                    connection,
                    run,
                    "uncertain" if mutation_phase(run["action"], phase) else "failed",
                    "Execution disappeared after claiming its phase",
                )
            return
        phase_logs = {
            **run["result"].get("phase_logs", {}),
            phase: self._redact(execution, self.driver.logs(execution["name"])),
        }
        logs = "\n".join(f"[{name}]\n{text}" for name, text in phase_logs.items())
        if phase_logs != run["result"].get("phase_logs") or logs != run["result"].get(
            "logs"
        ):
            with self.database.transaction() as connection:
                connection.execute(
                    runs.update()
                    .where(
                        and_(runs.c.id == run["id"], runs.c.worker == self.identifier)
                    )
                    .values(
                        result={
                            **run["result"],
                            "phase_logs": phase_logs,
                            "logs": logs,
                        },
                        updated=time.time(),
                    )
                )
        if state == "running":
            return
        self._collect(run, execution, state, logs)
        self.driver.cleanup(execution["name"])

    def _collect(self, run, execution, state, logs):
        if run["action"] == "inspection":
            self._collect_inspection(run, execution, state)
            return
        try:
            result = PhaseResult.model_validate_json(
                Fernet(self.database.decrypt(execution["key"]).encode()).decrypt(
                    self.storage.get(execution["result"])
                )
            ).model_dump(exclude_none=True)
            context_digest = self.storage.digest(execution["context"])
        except (ArtifactNotFoundError, ClientError) as exc:
            if isinstance(exc, ClientError) and exc.response["Error"]["Code"] not in {
                "NoSuchKey",
                "404",
            }:
                raise
            with self.database.transaction() as connection:
                self._finish(
                    connection,
                    run,
                    "uncertain"
                    if mutation_phase(
                        run["action"], run["spec"]["phases"][run["phase"]]
                    )
                    else "failed",
                    "Execution ended without a complete result handoff",
                )
            return
        except InvalidToken, ValidationError, ValueError:
            with self.database.transaction() as connection:
                self._finish(
                    connection,
                    run,
                    "uncertain"
                    if mutation_phase(
                        run["action"], run["spec"]["phases"][run["phase"]]
                    )
                    else "failed",
                    "Execution returned an invalid result; inspect remote state before retrying",
                )
            return
        if (
            result.get("revision") != run["revision"]
            or result.get("phase") != run["spec"]["phases"][run["phase"]]
        ):
            with self.database.transaction() as connection:
                self._finish(
                    connection,
                    run,
                    "uncertain"
                    if mutation_phase(
                        run["action"], run["spec"]["phases"][run["phase"]]
                    )
                    else "failed",
                    "Execution result does not match its authorized phase",
                )
            return
        result = self._redact_result(execution, result)
        for key in ("manifest_digest", "plan_digest"):
            if (
                run["result"].get(key)
                and result.get(key)
                and run["result"][key] != result[key]
            ):
                with self.database.transaction() as connection:
                    self._finish(
                        connection,
                        run,
                        "failed",
                        "Prepared manifest or reviewed plan changed during execution",
                    )
                return
        with self.database.transaction() as connection:
            current = (
                connection.execute(
                    runs.select().where(runs.c.id == run["id"]).with_for_update()
                )
                .mappings()
                .one()
            )
            if current["status"] not in {"running", "cancelling"} or not self._owns(
                current, run
            ):
                return
            merged = {
                **run["result"],
                **result,
                "logs": logs,
                "phase_logs": current["result"].get("phase_logs", {}),
                "context_digest": context_digest,
                "_context": execution["context"],
                "_key": execution["key"],
            }
            connection.execute(
                runs.update()
                .where(runs.c.id == run["id"])
                .values(result=merged, updated=time.time())
            )
            for name, report in result.get("reports", {}).items():
                key = f"reports/{run['id']}/{run['phase']}/{name}"
                self._artifact(
                    connection,
                    run,
                    f"{run['spec']['phases'][run['phase']]}/{name}",
                    key,
                    self.storage.put(key, json.dumps(report).encode()),
                    False,
                )
            audit(
                connection,
                self.identifier,
                "phase.completed",
                run["id"],
                phase=result["phase"],
                exit_code=result["exit_code"],
                context=context_digest,
            )
            if result["exit_code"] or state != "succeeded":
                self._finish(
                    connection,
                    {**run, "result": merged},
                    "failed",
                    "Stacksmith phase failed; reports may describe partial infrastructure changes",
                )
            elif current["status"] == "cancelling":
                self._finish(connection, {**run, "result": merged}, "cancelled")
            elif run["phase"] + 1 == len(run["spec"]["phases"]):
                self._finish(connection, {**run, "result": merged}, "succeeded")
            else:
                connection.execute(
                    runs.update()
                    .where(runs.c.id == run["id"])
                    .values(
                        status="queued",
                        phase=run["phase"] + 1,
                        execution=None,
                        worker=None,
                        lease=None,
                    )
                )

    def _finish(self, connection, run, status, message="", owned=True):
        current = (
            connection.execute(
                runs.select().where(runs.c.id == run["id"]).with_for_update()
            )
            .mappings()
            .one()
        )
        if owned and not self._owns(current, run):
            return
        connection.execute(
            runs.update()
            .where(runs.c.id == run["id"])
            .values(
                status=status,
                updated=time.time(),
                result={**current["result"], "message": message},
                worker=None,
                lease=None,
            )
        )
        if status != "uncertain":
            connection.execute(locks.delete().where(locks.c.run == run["id"]))
        audit(connection, self.identifier, f"run.{status}", run["id"], message=message)
        LOGGER.info("run=%s status=%s", run["id"], status)

    def _redact_result(self, execution, value):
        if isinstance(value, str):
            return self._redact(execution, value)
        if isinstance(value, list):
            return [self._redact_result(execution, item) for item in value]
        if isinstance(value, dict):
            return {
                key: self._redact_result(execution, item) for key, item in value.items()
            }
        return value

    def _inspection_credentials(self, inspection):
        if not inspection.get("context"):
            return {}
        context = inspection["context"]
        if not context.get("environment"):
            self._check_preview_access(inspection)
            return {}
        with self.database.transaction() as connection:
            require_access(
                connection,
                self.settings,
                context["actor"],
                context["repository"],
                context["environment"],
                "plan_infrastructure",
            )
            environment = row(
                connection,
                environments,
                f"{context['repository']}:{context['environment']}",
            )
            repository = row(connection, repositories, context["repository"])
            if (
                not environment
                or not repository
                or environment["revision"] != context["environment_revision"]
                or effective_config(connection, self.settings, repository)
                != inspection["config"]
            ):
                raise ComposerError(
                    "Environment configuration changed. Retry inspection.", 409
                )
        return self._credential_snapshot(
            {
                "repository": context["repository"],
                "environment": context["environment"],
            },
            "prepare",
        )[0]

    def _check_preview_access(self, inspection):
        context = inspection["context"]
        with self.database.transaction() as connection:
            require_access(
                connection,
                self.settings,
                context["actor"],
                context["repository"],
                "*",
                "view",
            )
            repository = row(connection, repositories, context["repository"])
            if (
                not repository
                or effective_config(connection, self.settings, repository)
                != inspection["config"]
            ):
                raise ComposerError(
                    "Platform configuration changed. Retry inspection.", 409
                )

    def _start_inspection(self, run):
        key = Fernet.generate_key()
        claim = random_secrets.token_urlsafe(48)
        prefix = f"executions/{run['id']}/inspection"
        inspection = json.loads(self.database.decrypt(run["spec"]["_inspection"]))
        try:
            inspection["credentials"] = self._inspection_credentials(inspection)
            authorization = (
                self._credential_authorization(
                    run,
                    "prepare",
                    {**inspection["context"], "config": inspection["config"]},
                )
                if (inspection.get("context") or {}).get("environment")
                else None
            )
        except (ComposerError, SecretSourceError) as exc:
            with self.database.transaction() as connection:
                self._finish_inspection(
                    connection,
                    run,
                    _inspection_failure(
                        "environment inputs",
                        str(exc),
                        "context_failed",
                        exc.status if isinstance(exc, ComposerError) else 422,
                    ),
                    "failed",
                )
            return
        execution = {
            "run": run["id"],
            "name": f"ss-{run['id']}-0",
            "input": f"{prefix}/input",
            "result": f"{prefix}/result",
            "claim_digest": hashlib.sha256(claim.encode()).hexdigest(),
            "started": time.time(),
            "redactions": self.database.encrypt(
                json.dumps(
                    [
                        inspection["github_token"] or "",
                        claim,
                        *inspection["credentials"].values(),
                    ]
                )
            ),
            "key": self.database.encrypt(key.decode()),
        }
        payload = {
            "phase": "inspect",
            "revision": run["revision"],
            "inspection": inspection,
            "credential_authorization": {
                name: authorization[name] for name in ("protocol", "url", "token")
            }
            if authorization
            else None,
            "timeout": self.settings.command_timeout,
            "claim_url": (
                self.settings.execution_callback_url or self.settings.public_url
            )
            + f"/api/v1/executions/{run['id']}/claim",
            "claim_token": claim,
            "result_output_url": self.storage.url(
                execution["result"], "put_object", self.settings.command_timeout + 300
            ),
        }
        self.storage.put(
            execution["input"], Fernet(key).encrypt(json.dumps(payload).encode())
        )
        with self.database.transaction() as connection:
            current = (
                connection.execute(
                    runs.select().where(runs.c.id == run["id"]).with_for_update()
                )
                .mappings()
                .one()
            )
            if current["status"] != "queued" or not self._owns(current, run):
                self.storage.delete(execution["input"])
                return
            self._create_credential_session(
                connection, run, authorization, self.settings.command_timeout
            )
            connection.execute(
                runs.update()
                .where(runs.c.id == run["id"])
                .values(
                    status="running",
                    execution=execution,
                    fence=run["fence"] + 1,
                    updated=time.time(),
                )
            )
            self._artifact(
                connection, run, "inspection-input", execution["input"], "", True
            )
            self._artifact(
                connection, run, "inspection-result", execution["result"], "", True
            )
        self._launch(run, execution)

    def _collect_inspection(self, run, execution, state):
        try:
            result = json.loads(
                Fernet(self.database.decrypt(execution["key"]).encode()).decrypt(
                    self.storage.get(execution["result"])
                )
            )
        except (ArtifactNotFoundError, ClientError) as exc:
            if isinstance(exc, ClientError) and exc.response["Error"]["Code"] not in {
                "NoSuchKey",
                "404",
            }:
                raise
            result = _inspection_failure(
                "inspection runtime",
                "The inspection runtime did not return a result.",
                "missing_result",
            )
        if state != "succeeded" and not result["exit_code"]:
            result = _inspection_failure(
                "inspection runtime", "The inspection runtime failed.", "runtime_failed"
            )
        result = self._redact_result(execution, result)
        if result.get("diagnostics"):
            result["diagnostics"] = {
                name: _diagnostic_text(value, self._redaction_values(execution))
                if isinstance(value, str)
                else value
                for name, value in result["diagnostics"].items()
            }
        with self.database.transaction() as connection:
            current = (
                connection.execute(
                    runs.select().where(runs.c.id == run["id"]).with_for_update()
                )
                .mappings()
                .one()
            )
            if not self._owns(current, run):
                return
            self._finish_inspection(
                connection,
                run,
                result,
                "failed" if result["exit_code"] else "succeeded",
            )

    def _finish_inspection(self, connection, run, result, status):
        current = (
            connection.execute(
                runs.select().where(runs.c.id == run["id"]).with_for_update()
            )
            .mappings()
            .one()
        )
        if not self._owns(current, run):
            return
        connection.execute(
            runs.update()
            .where(runs.c.id == run["id"])
            .values(
                result={
                    "_inspection_result": self.database.encrypt(json.dumps(result)),
                    **(
                        {
                            "_inspection_output": self.database.encrypt(
                                result["inspection_output"]
                            )
                        }
                        if not result["exit_code"]
                        else {}
                    ),
                }
            )
        )
        self._finish(connection, run, status)

    def _artifact(self, connection, run, name, key, digest, private):
        connection.execute(
            artifacts.insert().values(
                id=uuid4().hex,
                run=run["id"],
                name=name,
                key=key,
                digest=digest,
                private=int(private),
                expires=time.time()
                + (
                    self.settings.approval_ttl
                    if private
                    else self.settings.artifact_retention
                ),
            )
        )

    def _purge(self):
        with self.database.transaction() as connection:
            connection.execute(
                prepared_contexts.delete().where(
                    prepared_contexts.c.expires <= time.time()
                )
            )
            self._expire_metadata(connection, private=True)
            self._expire_metadata(connection, private=False)
            expired = (
                connection.execute(
                    artifacts.select()
                    .where(artifacts.c.expires < time.time())
                    .with_for_update(skip_locked=True)
                    .limit(100)
                )
                .mappings()
                .all()
            )
            for artifact in expired:
                self.storage.delete(artifact["key"])
                connection.execute(
                    artifacts.delete().where(artifacts.c.id == artifact["id"])
                )

    def _expire_metadata(self, connection, private):
        marker = "_private_expired" if private else "_reports_expired"
        age = (
            self.settings.approval_ttl if private else self.settings.artifact_retention
        )
        expired = (
            connection.execute(
                runs.select()
                .where(
                    and_(
                        runs.c.status.in_(TERMINAL),
                        runs.c.updated < time.time() - age,
                        runs.c.result[marker].as_boolean().is_not(True),
                    )
                )
                .with_for_update(skip_locked=True)
                .limit(100)
            )
            .mappings()
            .all()
        )
        for run in expired:
            if private:
                connection.execute(
                    execution_credential_sessions.delete().where(
                        execution_credential_sessions.c.run == run["id"]
                    )
                )
            result = {
                key: value
                for key, value in run["result"].items()
                if (
                    not key.startswith("_")
                    if private
                    else key not in {"logs", "phase_logs", "reports"}
                )
            }
            result[marker] = True
            spec = (
                {
                    key: value
                    for key, value in run["spec"].items()
                    if key != "_inspection"
                }
                if private
                else run["spec"]
            )
            connection.execute(
                runs.update()
                .where(runs.c.id == run["id"])
                .values(
                    result=result, spec=spec, **({"execution": None} if private else {})
                )
            )


class Worker(RunReconciler):
    """Persistent Compose host for the shared run reconciler."""

    def tick(self, inspections: bool = True) -> None:
        """Heartbeat, perform maintenance, and reconcile available Compose work.

        Args:
            inspections: Whether to also reconcile inspections in this pass.

        Returns:
            None.
        """
        self.maintenance()
        with self.database.transaction() as connection:
            connection.execute(workers.delete().where(workers.c.id == self.identifier))
            connection.execute(
                workers.insert().values(
                    id=self.identifier,
                    heartbeat=time.time(),
                    driver=self.settings.runner,
                )
            )
        if inspections:
            self._reconcile_inspections()
        self._reconcile_matching([runs.c.action != "inspection"])

    def _reconcile_inspections(self):
        """Poll owned inspections, recover abandoned work, then fill free slots."""
        for identifier in self._inspection_candidates(owned=True):
            self.reconcile_run(identifier)
        for statuses in [("running", "cancelling"), ("queued",)]:
            for identifier in self._inspection_candidates(
                statuses=statuses, limit=self._inspection_capacity()
            ):
                self.reconcile_run(identifier)

    def _inspection_capacity(self):
        with self.database.transaction() as connection:
            return max(
                0,
                self.settings.inspection_concurrency
                - connection.execute(
                    runs.select()
                    .with_only_columns(func.count())
                    .where(
                        runs.c.action == "inspection",
                        runs.c.supervision.is_(None),
                        runs.c.worker == self.identifier,
                        runs.c.status.in_(["running", "cancelling"]),
                    )
                ).scalar_one(),
            )

    def _inspection_candidates(
        self, *, owned=False, statuses=("running", "cancelling"), limit=None
    ):
        with self.database.transaction() as connection:
            return list(
                connection.execute(
                    runs.select()
                    .with_only_columns(runs.c.id)
                    .where(
                        runs.c.action == "inspection",
                        runs.c.supervision.is_(None),
                        runs.c.status.in_(statuses),
                        runs.c.worker == self.identifier
                        if owned
                        else or_(
                            runs.c.worker.is_(None),
                            and_(
                                runs.c.worker == self.identifier,
                                runs.c.status == "queued",
                            ),
                            and_(
                                runs.c.worker != self.identifier,
                                runs.c.lease < time.time(),
                            ),
                        ),
                    )
                    .order_by(runs.c.created, runs.c.id)
                    .limit(limit)
                ).scalars()
            )


def main() -> None:
    """Run the worker supervisor against an initialized platform database.

    Returns:
        None.

    Raises:
        ValueError: Execution is not configured.
    """
    settings = Settings()
    configure_ca_bundle(CA_BUNDLE_MOUNT_PATH if settings.ca_bundle else None)
    if not settings.execution_enabled:
        raise ValueError("Enable execution before starting a worker")
    if settings.supervision_mode != "worker":
        raise ValueError("Persistent workers require worker supervision mode")
    logging.basicConfig(level=logging.INFO)
    settings.worker_id = settings.worker_id or socket.gethostname()
    _STOP.clear()
    signal.signal(signal.SIGTERM, _request_shutdown)
    signal.signal(signal.SIGINT, _request_shutdown)
    with closing(GitHubTransport()) as transport:
        worker = Worker(
            settings,
            Database.from_settings(settings, pool_size=1),
            execution_driver(settings),
            artifact_storage(settings),
            GitHubProvider(settings, transport),
        )
        _serve(worker, _STOP)


if __name__ == "__main__":
    main()
