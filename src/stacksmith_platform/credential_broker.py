import hashlib
import hmac
import json
import time
from datetime import UTC

from fastapi import Request
from fastapi.responses import JSONResponse

from .access import require_access, require_workloads, run_workloads
from .configuration import effective_config
from .database import (
    audit,
    environments,
    execution_credential_bindings,
    execution_credential_sessions,
    phase_claims,
    repositories,
    row,
    runs,
)
from .errors import ComposerError
from .integrations import ExecutionCredentialError
from .lifecycle import MUTATIONS


def _authorize(connection, settings, catalog, run, session, token):
    if (
        not run
        or run["status"] != "running"
        or not run["execution"]
        or not session
        or session["phase"] != run["phase"]
        or session["fence"] != run["fence"]
        or session["expires"] <= time.time()
        or not hmac.compare_digest(
            session["token_digest"], hashlib.sha256(token.encode()).hexdigest()
        )
    ):
        raise ComposerError("Invalid execution credential capability.", 403)
    claim = row(connection, phase_claims, f"{run['id']}:{run['phase']}")
    if not claim or claim["fence"] != run["fence"]:
        raise ComposerError("Execution phase has not been claimed.", 403)
    scope = session["scope"]
    environment = (
        connection.execute(
            environments.select()
            .where(environments.c.id == f"{scope['repository']}:{scope['environment']}")
            .with_for_update()
        )
        .mappings()
        .first()
    )
    repository = row(connection, repositories, scope["repository"])
    binding = row(
        connection,
        execution_credential_bindings,
        f"{scope['repository']}:{scope['environment']}:{session['provider']}",
    )
    if (
        not environment
        or not environment["active"]
        or environment["revision"] != scope["environment_revision"]
        or not repository
        or not repository["enabled"]
        or not repository["deployment_enabled"]
        or effective_config(connection, settings, repository) != scope["config"]
        or not binding
        or scope["phase"] not in binding["phases"]
        or session["provider"] not in catalog.providers
        or not hmac.compare_digest(
            session["provider_revision"], catalog.revision(session["provider"])
        )
    ):
        raise ComposerError(
            "Execution credential configuration changed; request a new run.", 409
        )
    if run["action"] == "inspection":
        require_access(
            connection,
            settings,
            scope["actor"],
            scope["repository"],
            scope["environment"],
            "plan_infrastructure",
        )
    else:
        require_workloads(
            connection,
            settings,
            scope["actor"],
            scope["repository"],
            scope["environment"],
            "execute" if run["action"] in MUTATIONS else "plan",
            run_workloads(run["spec"]),
        )


def _issue(database, catalog, session):
    now = time.time()
    if session["lease"]:
        cached = json.loads(database.decrypt(session["lease"]))
        if cached["refresh_at"] > now:
            return cached
    lease = catalog.providers[session["provider"]].issue(session["id"])
    now = time.time()
    if (
        lease.expires_at.tzinfo is None
        or lease.expires_at.timestamp() <= now + 60
        or not lease.values
        or not all(
            isinstance(value, str) and value and len(value) <= 65536
            for value in lease.values.values()
        )
    ):
        raise ExecutionCredentialError(
            "Provider returned invalid execution credentials."
        )
    return {
        "values": lease.values,
        "expires_at": lease.expires_at.astimezone(UTC).isoformat(),
        "refresh_at": now + (lease.expires_at.timestamp() - now) * 0.8,
    }


def refresh_credentials(request: Request, run_id: str) -> JSONResponse:
    """Issue or refresh credentials for one claimed, active execution phase.

    Args:
        request: Request bearing a phase-scoped credential capability.
        run_id: Target execution run.

    Returns:
        Credential values and expiration with caching disabled.

    Raises:
        ComposerError: Authorization, configuration, or issuance fails.
    """
    database = request.app.state.database
    catalog = request.app.state.execution_credential_catalog
    with database.transaction() as connection:
        run = (
            connection.execute(
                runs.select().where(runs.c.id == run_id).with_for_update()
            )
            .mappings()
            .first()
        )
        session = (
            row(connection, execution_credential_sessions, f"{run_id}:{run['phase']}")
            if run
            else None
        )
        _authorize(
            connection,
            request.app.state.settings,
            catalog,
            run,
            session,
            request.headers.get("authorization", "").removeprefix("Bearer "),
        )
        try:
            lease = _issue(database, catalog, session)
        except ExecutionCredentialError:
            raise ComposerError("Execution credentials are unavailable.", 503) from None
        if session["expires"] <= time.time():
            raise ComposerError("Execution credential capability expired.", 403)
        # Commit redactions before returning any newly issued values to user code.
        connection.execute(
            execution_credential_sessions.update()
            .where(execution_credential_sessions.c.id == session["id"])
            .values(
                lease=database.encrypt(json.dumps(lease)),
                redactions=database.encrypt(
                    json.dumps(
                        sorted(
                            set(
                                json.loads(database.decrypt(session["redactions"]))
                                + list(lease["values"].values())
                            )
                        )
                    )
                ),
            )
        )
        if (
            not session["lease"]
            or json.loads(database.decrypt(session["lease"]))["expires_at"]
            != lease["expires_at"]
        ):
            audit(
                connection,
                "execution",
                "execution_credential.issued",
                run_id,
                phase=run["phase"],
                provider=session["provider"],
            )
    return JSONResponse(
        {"values": lease["values"], "expires_at": lease["expires_at"]},
        headers={"Cache-Control": "no-store", "Pragma": "no-cache"},
    )
