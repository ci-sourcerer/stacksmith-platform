import asyncio
import hashlib
import json
import logging
import time
from pathlib import Path
from uuid import uuid4

from sqlalchemy import text

from .database import Database, configuration, inspection_editors, row, runs
from .errors import ComposerError
from .inspection_notifications import notification_hub, notify
from .inspection_results import _inspection_failure, _raise_inspection_failure
from .scheduling import KubernetesDispatcher, requested_supervision

LOGGER = logging.getLogger(__name__)


class InspectionQueue:
    """Submit server-selected inspection commands to the execution worker.

    Args:
        settings: Managed configuration and execution settings.
    """

    def __init__(self, settings):
        self.settings = settings
        self.database = Database(settings.database_url, settings.secret_key)
        if settings.inspection_controller_enabled and (
            hub := notification_hub(self.database)
        ):
            self.database = hub.database
        self.dispatcher = (
            KubernetesDispatcher(settings, self.database)
            if settings.supervision_mode == "kubernetes-jobs"
            else None
        )

    def execute(self, args, source, github_token, authoring_request, context=None):
        """Wait for a bounded isolated inspection result.

        Args:
            args: Backend-selected Stacksmith inspection arguments.
            source: Current draft source, when applicable.
            github_token: Current user's GitHub credential.
            authoring_request: Optional document authoring request.
            context: Optional server-authorized environment and source snapshot.

        Returns:
            Stacksmith JSON output.

        Raises:
            ComposerError: Inspection fails or exceeds its deadline.
        """
        if self.settings.inspection_controller_enabled:
            if not (hub := notification_hub(self.database)):
                raise ComposerError(
                    "Inspection notification listener is unavailable.", 503
                )
            return asyncio.run_coroutine_threadsafe(
                self.aexecute(args, source, github_token, authoring_request, context),
                hub.loop,
            ).result()
        identifier = uuid4().hex
        started = time.monotonic()
        wait_timeout = (
            self.settings.inspection_wait_timeout or self.settings.command_timeout + 60
        )
        with self.database.transaction() as connection:
            connection.execute(
                runs.insert().values(
                    id=identifier,
                    repository="inspection",
                    environment="inspection",
                    revision="0" * 40,
                    action="inspection",
                    actor="authoring",
                    status="queued",
                    phase=0,
                    supervision=requested_supervision(self.settings, "queued"),
                    spec={
                        "image": self.settings.execution_image,
                        "phases": ["inspect"],
                        "_inspection": self.database.encrypt(
                            json.dumps(
                                {
                                    "args": args,
                                    "source": source,
                                    "github_token": github_token,
                                    "authoring_request": authoring_request,
                                    "context": context,
                                    "config": str(self.settings.config),
                                    "config_source": self.settings.config.read_text()
                                    if args != ["author"]
                                    and isinstance(self.settings.config, Path)
                                    else None,
                                }
                            )
                        ),
                    },
                    result={},
                    created=time.time(),
                    updated=time.time(),
                    expires=time.time() + wait_timeout,
                    dedup=identifier,
                    lock_key="",
                    fence=0,
                )
            )
        if self.dispatcher is not None:
            self.dispatcher.dispatch(identifier)
        deadline = time.monotonic() + wait_timeout
        while time.monotonic() < deadline:
            with self.database.transaction() as connection:
                run = row(connection, runs, identifier)
            if run["status"] == "succeeded":
                LOGGER.info(
                    "Inspection %s completed: operation=%s queue_seconds=%.3f total_seconds=%.3f",
                    identifier,
                    args[0],
                    (run["execution"] or {}).get("started", run["created"])
                    - run["created"],
                    time.monotonic() - started,
                )
                return self.database.decrypt(run["result"]["_inspection_output"])
            if run["status"] in {"failed", "expired", "cancelled", "uncertain"}:
                if run["result"].get("_inspection_result"):
                    _raise_inspection_failure(
                        json.loads(
                            self.database.decrypt(run["result"]["_inspection_result"])
                        )
                    )
                raise ComposerError(
                    "Stacksmith inspection failed. Review the draft and managed configuration.",
                    422,
                )
            time.sleep(0.2)
        with self.database.transaction() as connection:
            connection.execute(
                runs.update()
                .where(runs.c.id == identifier)
                .values(
                    status="cancelling",
                    supervision=requested_supervision(self.settings, "cancelling"),
                )
            )
        if self.dispatcher is not None:
            self.dispatcher.dispatch(identifier)
        _raise_inspection_failure(
            _inspection_failure(
                "inspection queue", "Stacksmith inspection timed out.", "timeout", 504
            )
        )

    async def aexecute(
        self, args, source, github_token, authoring_request, context=None, editor=None
    ):
        """Submit an inspection and await committed notifications without a thread.

        Args:
            args: Server-selected inspection operation.
            source: Optional authored source.
            github_token: Current authorized GitHub token.
            authoring_request: Optional source-editing request.
            context: Server-verified inspection context.
            editor: Server-scoped editor identity and generation.

        Returns:
            The inspected JSON output.

        Raises:
            ComposerError: Inspection fails, is superseded, or times out.
        """
        if not (hub := notification_hub(self.database)):
            raise ComposerError("Inspection notification listener is unavailable.", 503)
        started = time.monotonic()
        identifier = await asyncio.to_thread(
            self._submit, args, source, github_token, authoring_request, context, editor
        )
        deadline = started + (
            self.settings.inspection_wait_timeout or self.settings.command_timeout + 60
        )
        with hub.subscribe(identifier) as changed:
            while time.monotonic() < deadline:
                changed.clear()
                result = await asyncio.to_thread(self._result, identifier)
                if result is not None:
                    LOGGER.info(
                        "Inspection %s completed: operation=%s queue_seconds=%.3f total_seconds=%.3f",
                        identifier,
                        args[0],
                        result[1],
                        time.monotonic() - started,
                    )
                    return result[0]
                try:
                    await asyncio.wait_for(
                        changed.wait(), min(30, max(0, deadline - time.monotonic()))
                    )
                except TimeoutError:
                    continue
        await asyncio.to_thread(self._cancel, identifier)
        _raise_inspection_failure(
            _inspection_failure(
                "inspection queue", "Stacksmith inspection timed out.", "timeout", 504
            )
        )

    def _submit(self, args, source, github_token, authoring_request, context, editor):
        identifier = uuid4().hex
        with self.database.transaction() as connection:
            target = row(connection, configuration, "inspection:pool")
            if target and target["policy"]["image"] != self.settings.execution_image:
                raise ComposerError(
                    "The inspection image changed. Refresh and retry.", 409
                )
            scope = self._supersede(connection, args, context, editor)
            connection.execute(
                runs.insert().values(
                    id=identifier,
                    repository="inspection",
                    environment="inspection",
                    revision="0" * 40,
                    action="inspection",
                    actor=(context or {}).get("actor")
                    or (editor or {}).get("actor")
                    or hashlib.sha256(
                        (github_token or "authoring").encode()
                    ).hexdigest(),
                    status="queued",
                    phase=0,
                    supervision=None,
                    spec={
                        "image": self.settings.execution_image,
                        "phases": ["inspect"],
                        "inspection_controller": True,
                        "inspection_editor": scope,
                        "inspection_generation": (editor or {}).get("generation"),
                        "inspection_priority": 0
                        if args == ["review"]
                        else 2
                        if args == ["author"]
                        else 1,
                        "_inspection": self.database.encrypt(
                            json.dumps(
                                {
                                    "args": args,
                                    "source": source,
                                    "github_token": github_token,
                                    "authoring_request": authoring_request,
                                    "context": context,
                                    "config": str(self.settings.config),
                                    "config_source": self.settings.config.read_text()
                                    if args != ["author"]
                                    and isinstance(self.settings.config, Path)
                                    else None,
                                }
                            )
                        ),
                    },
                    result={},
                    created=time.time(),
                    updated=time.time(),
                    expires=time.time()
                    + (
                        self.settings.inspection_wait_timeout
                        or self.settings.command_timeout + 60
                    ),
                    dedup=identifier,
                    lock_key="",
                    fence=0,
                )
            )
            notify(connection, identifier)
        return identifier

    def _supersede(self, connection, args, context, editor):
        if not editor or not editor.get("editor_id"):
            return None
        scope = hashlib.sha256(
            json.dumps(
                [
                    editor["actor"],
                    editor["editor_id"],
                    editor["repository"],
                    editor["path"],
                    (context or editor).get("environment"),
                    args[0],
                ]
            ).encode()
        ).hexdigest()
        if connection.dialect.name == "postgresql":
            connection.execute(
                text("SELECT pg_advisory_xact_lock(hashtext(:scope))"), {"scope": scope}
            )
        current = (
            connection.execute(
                inspection_editors.select()
                .where(inspection_editors.c.id == scope)
                .with_for_update()
            )
            .mappings()
            .first()
        )
        if current and editor["generation"] <= current["generation"]:
            _superseded()
        if current:
            connection.execute(
                inspection_editors.update()
                .where(inspection_editors.c.id == scope)
                .values(generation=editor["generation"], updated=time.time())
            )
        else:
            connection.execute(
                inspection_editors.insert().values(
                    id=scope, generation=editor["generation"], updated=time.time()
                )
            )
        for previous in (
            connection.execute(
                runs.select()
                .where(
                    runs.c.action == "inspection",
                    runs.c.status.in_(["queued", "running"]),
                    runs.c.spec["inspection_editor"].as_string() == scope,
                )
                .with_for_update()
            )
            .mappings()
            .all()
        ):
            if previous["status"] == "queued":
                connection.execute(
                    runs.update()
                    .where(runs.c.id == previous["id"])
                    .values(
                        status="cancelled",
                        updated=time.time(),
                        result={"superseded": True},
                    )
                )
            notify(connection, previous["id"])
        return scope

    def _result(self, identifier):
        with self.database.transaction() as connection:
            run = row(connection, runs, identifier)
            if run["spec"].get("inspection_editor"):
                current = row(
                    connection, inspection_editors, run["spec"]["inspection_editor"]
                )
                if (
                    current
                    and current["generation"] != run["spec"]["inspection_generation"]
                ):
                    _superseded()
        if run["result"].get("superseded"):
            _superseded()
        if run["status"] == "succeeded":
            return (
                self.database.decrypt(run["result"]["_inspection_output"]),
                (run["execution"] or {}).get("started", run["created"])
                - run["created"],
            )
        if run["status"] in {"failed", "expired", "cancelled", "uncertain"}:
            if run["result"].get("_inspection_result"):
                _raise_inspection_failure(
                    json.loads(
                        self.database.decrypt(run["result"]["_inspection_result"])
                    )
                )
            raise ComposerError(
                "Stacksmith inspection failed. Review the draft and managed configuration.",
                422,
            )
        return None

    def _cancel(self, identifier):
        with self.database.transaction() as connection:
            connection.execute(
                runs.update()
                .where(
                    runs.c.id == identifier, runs.c.status.in_(["queued", "running"])
                )
                .values(status="cancelling", updated=time.time())
            )
            notify(connection, identifier)


def _superseded():
    _raise_inspection_failure(
        _inspection_failure(
            "inspection queue",
            "A newer draft replaced this inspection.",
            "inspection_superseded",
            409,
        )
    )
