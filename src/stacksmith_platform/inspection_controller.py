import asyncio
import hashlib
import json
import logging
import secrets
import signal
import time
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from contextlib import closing
from functools import partial
from uuid import uuid4

from docker.errors import DockerException
from kubernetes.client.exceptions import ApiException
from requests.exceptions import RequestException
from sqlalchemy import and_, or_, text
from urllib3.exceptions import HTTPError

from .certificates import CA_BUNDLE_MOUNT_PATH, configure_ca_bundle
from .database import (
    Database,
    configuration,
    inspection_editors,
    inspection_sandboxes,
    row,
    runs,
    scheduler_health,
)
from .factories import artifact_storage, execution_driver
from .github_transport import GitHubTransport
from .inspection_notifications import InspectionNotifications, notification_hub, notify
from .inspection_results import _inspection_failure
from .lifecycle import TERMINAL
from .provider import GitHubProvider
from .settings import Settings
from .worker import RunReconciler

LOGGER = logging.getLogger(__name__)
_RUNTIME_ERRORS = (DockerException, ApiException, RequestException, HTTPError, OSError)


def _pool_lock(connection):
    if connection.dialect.name == "postgresql":
        connection.execute(
            text("SELECT pg_advisory_xact_lock(hashtext('inspection-pool'))")
        )


class InspectionController(RunReconciler):
    """Schedule durable inspections into globally bounded single-use sandboxes.

    Args:
        settings: Pool and execution policy.
        database: Durable jobs and sandbox assignments.
        driver: Docker or Kubernetes execution adapter.
        storage: Encrypted artifact storage.
        provider: Server-owned GitHub installation provider.
    """

    def __init__(self, settings, database, driver, storage, provider):
        super().__init__(settings, database, driver, storage, provider)
        self.identifier = f"inspection-{uuid4().hex}"
        self._launch_backoff = 0
        self._launch_error = None
        self.recovery_after = settings.inspection_recovery_interval
        self._launcher = ThreadPoolExecutor(
            max_workers=settings.inspection_start_concurrency
        )
        self._launches = set()
        self._cleaner = ThreadPoolExecutor(max_workers=4)
        self._cleanups = {}
        self._cleanup_backoff = {}

    def reconcile_run(self, run_id=None, generation=None):
        """Reconcile one inspection while bounding its ownership recovery lease.

        Args:
            run_id: Durable inspection identifier.
            generation: Optional inherited supervisor generation.

        Returns:
            Whether an inspection was owned and reconciled.

        Raises:
            sqlalchemy.exc.SQLAlchemyError: Ownership cannot be recorded.
        """
        owned = super().reconcile_run(run_id, generation)
        if owned:
            with self.database.transaction() as connection:
                connection.execute(
                    runs.update()
                    .where(runs.c.id == run_id, runs.c.worker == self.identifier)
                    .values(
                        lease=time.time() + self.settings.inspection_recovery_interval
                    )
                )
        return owned

    def step(self, identifiers=(), recovery=False):
        """Reconcile notified executions, fill free slots, and replenish standbys.

        Args:
            identifiers: Changed run or sandbox identifiers.
            recovery: Whether to reconcile all leases and runtime states.

        Returns:
            None.

        Raises:
            sqlalchemy.exc.SQLAlchemyError: Durable state cannot be reconciled.
        """
        self.recovery_after = self.settings.inspection_recovery_interval
        self._collect_cleanups()
        for pending in tuple(self._launches):
            if pending.done():
                self._launches.remove(pending)
                pending.result()
        if recovery:
            self.maintenance()
            self._reconcile_orphans()
            from .inspection_cache import expire_cache

            expire_cache(self)
            with self.database.transaction() as connection:
                connection.execute(
                    inspection_editors.delete().where(
                        inspection_editors.c.updated < time.time() - 86400
                    )
                )
                connection.execute(
                    scheduler_health.delete().where(
                        scheduler_health.c.id.like("inspection-%"),
                        scheduler_health.c.completed < time.time() - 86400,
                    )
                )
        self._resume(set(identifiers), recovery)
        self._reap()
        while identifier := self._reserve():
            self.reconcile_run(identifier)
        for identifier in self._new_sandboxes():
            pending = self._launcher.submit(self._launch_standby, identifier)
            self._launches.add(pending)
            pending.add_done_callback(partial(self._runtime_changed, identifier))
        with self.database.transaction() as connection:
            connection.execute(
                scheduler_health.delete().where(
                    scheduler_health.c.id == self.identifier
                )
            )
            connection.execute(
                scheduler_health.insert().values(
                    id=self.identifier, completed=time.time(), error=self._launch_error
                )
            )

    def _active(self, connection):
        return (
            connection.execute(
                inspection_sandboxes.select().where(
                    inspection_sandboxes.c.status.in_(
                        ["reserved", "assigned", "delivered"]
                    )
                )
            )
            .mappings()
            .all()
        )

    def _reconcile_orphans(self):
        if not hasattr(self.driver, "inspection_resources"):
            return
        with self.database.transaction() as connection:
            recorded = set(
                connection.execute(
                    inspection_sandboxes.select().with_only_columns(
                        inspection_sandboxes.c.id
                    )
                ).scalars()
            )
        for identifier in self.driver.inspection_resources():
            if identifier not in recorded:
                with self.database.transaction() as connection:
                    if row(connection, inspection_sandboxes, identifier):
                        continue
                self.driver.cleanup(identifier)

    def _pool_image(self, connection):
        target = row(connection, configuration, "inspection:pool")
        return target["policy"]["image"] if target else self.settings.execution_image

    def _reserve(self):
        with self.database.transaction() as connection:
            _pool_lock(connection)
            if self._pool_image(connection) != self.settings.execution_image:
                return None
            active = self._active(connection)
            if len(active) >= self.settings.inspection_pool_active:
                return None
            standby = (
                connection.execute(
                    inspection_sandboxes.select()
                    .where(
                        inspection_sandboxes.c.status == "ready",
                        inspection_sandboxes.c.image == self.settings.execution_image,
                        inspection_sandboxes.c.expires > time.time(),
                    )
                    .order_by(inspection_sandboxes.c.created)
                    .with_for_update(skip_locked=True)
                )
                .mappings()
                .first()
            )
            if not standby:
                return None
            active_runs = [row(connection, runs, slot["run"]) for slot in active]
            actors = Counter(run["actor"] for run in active_runs if run)
            editors = {
                run["spec"].get("inspection_editor") for run in active_runs if run
            }
            candidates = (
                connection.execute(
                    runs.select()
                    .where(
                        runs.c.action == "inspection",
                        runs.c.status == "queued",
                        runs.c.spec["inspection_controller"].as_boolean().is_(True),
                        runs.c.spec["image"].as_string()
                        == self.settings.execution_image,
                        runs.c.expires > time.time(),
                    )
                    .order_by(runs.c.created)
                    .with_for_update(skip_locked=True)
                    .limit(1000)
                )
                .mappings()
                .all()
            )
            candidates = [
                run
                for run in candidates
                if (
                    run["id"] not in {slot["run"] for slot in active}
                    and (
                        not run["spec"].get("inspection_editor")
                        or run["spec"]["inspection_editor"] not in editors
                    )
                )
            ]
            if not candidates:
                return None
            selected = min(
                candidates,
                key=lambda run: (
                    run["spec"].get("inspection_priority", 1),
                    actors[run["actor"]],
                    run["created"],
                    run["id"],
                ),
            )
            connection.execute(
                inspection_sandboxes.update()
                .where(inspection_sandboxes.c.id == standby["id"])
                .values(
                    status="reserved",
                    run=selected["id"],
                    owner=self.identifier,
                    lease=time.time() + self.settings.inspection_recovery_interval,
                    expires=selected["expires"],
                )
            )
            connection.execute(
                runs.update()
                .where(runs.c.id == selected["id"])
                .values(
                    worker=self.identifier,
                    lease=time.time() + self.settings.inspection_recovery_interval,
                )
            )
            notify(connection, standby["id"])
            return selected["id"]

    def _new_sandboxes(self):
        if time.monotonic() < self._launch_backoff:
            return []
        created = []
        with self.database.transaction() as connection:
            _pool_lock(connection)
            if self._pool_image(connection) != self.settings.execution_image:
                return []
            slots = connection.execute(inspection_sandboxes.select()).mappings().all()
            count = min(
                self.settings.inspection_pool_ready
                - sum(slot["status"] in {"ready", "launching"} for slot in slots),
                self.settings.inspection_pool_total - len(slots),
            )
            for _ in range(max(0, count)):
                identifier = f"ss-inspection-{uuid4().hex}"
                token = secrets.token_urlsafe(48)
                connection.execute(
                    inspection_sandboxes.insert().values(
                        id=identifier,
                        image=self.settings.execution_image,
                        status="launching",
                        token_digest=hashlib.sha256(token.encode()).hexdigest(),
                        token=self.database.encrypt(token),
                        created=time.time(),
                        expires=time.time() + self.settings.inspection_sandbox_ttl,
                        owner=self.identifier,
                        lease=time.time() + self.settings.inspection_recovery_interval,
                    )
                )
                created.append(identifier)
        return created

    def _launch_standby(self, identifier):
        started = time.monotonic()
        with self.database.transaction() as connection:
            standby = row(connection, inspection_sandboxes, identifier)
            if (
                not standby
                or standby["status"] != "launching"
                or standby["owner"] != self.identifier
                or standby["image"] != self._pool_image(connection)
            ):
                return
        try:
            self.driver.launch(
                identifier,
                standby["image"],
                {
                    "PLATFORM_SANDBOX_URL": (
                        self.settings.execution_callback_url or self.settings.public_url
                    )
                    + f"/api/v1/inspection-sandboxes/{identifier}/assignment",
                    "PLATFORM_SANDBOX_TOKEN": self.database.decrypt(standby["token"]),
                    "PLATFORM_SANDBOX_EXPIRES": str(standby["expires"]),
                    **(
                        {"PLATFORM_CA_BUNDLE": CA_BUNDLE_MOUNT_PATH}
                        if self.settings.ca_bundle
                        else {}
                    ),
                },
                self.settings.inspection_sandbox_ttl
                + self.settings.command_timeout
                + 300,
            )
            LOGGER.info(
                "Inspection sandbox created: id=%s create_seconds=%.3f",
                identifier,
                time.monotonic() - started,
            )
            self._launch_error = None
        except _RUNTIME_ERRORS as exc:
            # A launch may have reached the runtime; recovery removes this unique resource.
            with self.database.transaction() as connection:
                if not connection.execute(
                    inspection_sandboxes.update()
                    .where(
                        inspection_sandboxes.c.id == identifier,
                        inspection_sandboxes.c.status == "launching",
                        inspection_sandboxes.c.owner == self.identifier,
                    )
                    .values(status="retired")
                ).rowcount:
                    return
                notify(connection, identifier)
            self._launch_error = type(exc).__name__
            self._launch_backoff = time.monotonic() + 5
            LOGGER.warning(
                "Inspection sandbox launch failed; retiring %s: error=%s",
                identifier,
                type(exc).__name__,
            )

    def _start_inspection(self, run):
        from .inspection_cache import prepare_cache

        run, _ = prepare_cache(
            self, run, json.loads(self.database.decrypt(run["spec"]["_inspection"]))
        )
        super()._start_inspection(run)

    def _inspection_execution_name(self, run):
        with self.database.transaction() as connection:
            return connection.execute(
                inspection_sandboxes.select()
                .with_only_columns(inspection_sandboxes.c.id)
                .where(
                    inspection_sandboxes.c.run == run["id"],
                    inspection_sandboxes.c.owner == self.identifier,
                )
            ).scalar_one()

    def _launch(self, run, execution):
        with self.database.transaction() as connection:
            standby = (
                connection.execute(
                    inspection_sandboxes.select()
                    .where(
                        inspection_sandboxes.c.id == execution["name"],
                        inspection_sandboxes.c.run == run["id"],
                        inspection_sandboxes.c.owner == self.identifier,
                    )
                    .with_for_update()
                )
                .mappings()
                .first()
            )
            if not standby or standby["status"] != "reserved":
                return
            connection.execute(
                inspection_sandboxes.update()
                .where(inspection_sandboxes.c.id == standby["id"])
                .values(
                    status="assigned",
                    assignment=self.database.encrypt(
                        json.dumps(
                            {
                                "PLATFORM_INPUT_URL": self.storage.url(
                                    execution["input"],
                                    "get_object",
                                    self.settings.command_timeout + 300,
                                ),
                                "PLATFORM_HANDOFF_KEY": self.database.decrypt(
                                    execution["key"]
                                ),
                            }
                        )
                    ),
                )
            )
            notify(connection, standby["id"])

    def _resume(self, identifiers, recovery):
        with self.database.transaction() as connection:
            active = [dict(slot) for slot in self._active(connection)]
            launching = (
                connection.execute(
                    inspection_sandboxes.select().where(
                        inspection_sandboxes.c.status == "launching",
                        inspection_sandboxes.c.lease < time.time(),
                    )
                )
                .mappings()
                .all()
                if recovery
                else []
            )
        for slot in launching:
            state = self.driver.inspect(slot["id"])
            with self.database.transaction() as connection:
                connection.execute(
                    inspection_sandboxes.update()
                    .where(
                        inspection_sandboxes.c.id == slot["id"],
                        inspection_sandboxes.c.status == "launching",
                        inspection_sandboxes.c.lease < time.time(),
                    )
                    .values(
                        status="launching" if state == "running" else "retired",
                        owner=self.identifier,
                        lease=time.time() + self.settings.inspection_recovery_interval,
                    )
                )
        for slot in active:
            if not recovery and not identifiers.intersection(
                {slot["id"], slot["run"], f"runtime:{slot['id']}", "*"}
            ):
                continue
            if self._adopt(slot["id"]):
                self.reconcile_run(slot["run"])
        self._reconcile_idle(identifiers, recovery)

    def _reconcile_idle(self, identifiers, recovery):
        with self.database.transaction() as connection:
            idle = (
                connection.execute(
                    inspection_sandboxes.select().where(
                        inspection_sandboxes.c.status.in_(["ready", "launching"])
                    )
                )
                .mappings()
                .all()
            )
        for slot in idle:
            if not recovery and f"runtime:{slot['id']}" not in identifiers:
                continue
            state = self.driver.inspect(slot["id"])
            if state in {"failed", "succeeded"} or (
                state == "missing" and slot["status"] == "ready"
            ):
                with self.database.transaction() as connection:
                    connection.execute(
                        inspection_sandboxes.update()
                        .where(
                            inspection_sandboxes.c.id == slot["id"],
                            inspection_sandboxes.c.status == slot["status"],
                        )
                        .values(status="retired")
                    )

    def _adopt(self, identifier):
        with self.database.transaction() as connection:
            previous = row(connection, inspection_sandboxes, identifier)
            if not previous:
                return False
            run = (
                connection.execute(
                    runs.select().where(runs.c.id == previous["run"]).with_for_update()
                )
                .mappings()
                .first()
            )
            slot = (
                connection.execute(
                    inspection_sandboxes.select()
                    .where(inspection_sandboxes.c.id == identifier)
                    .with_for_update()
                )
                .mappings()
                .first()
            )
            if not slot:
                return False
            if not run or run["status"] in TERMINAL:
                connection.execute(
                    inspection_sandboxes.update()
                    .where(inspection_sandboxes.c.id == identifier)
                    .values(status="retired")
                )
                return False
            if (
                run["status"] == "queued"
                and slot["image"] != self.settings.execution_image
            ):
                return False
            if (
                slot["owner"] != self.identifier
                and max(slot["lease"] or 0, run["lease"] or 0) > time.time()
            ):
                self.recovery_after = min(
                    self.recovery_after,
                    max(slot["lease"] or 0, run["lease"] or 0) - time.time() + 0.01,
                )
                return False
            connection.execute(
                inspection_sandboxes.update()
                .where(inspection_sandboxes.c.id == identifier)
                .values(
                    owner=self.identifier,
                    lease=time.time() + self.settings.inspection_recovery_interval,
                )
            )
            connection.execute(
                runs.update()
                .where(runs.c.id == run["id"])
                .values(
                    worker=self.identifier,
                    lease=time.time() + self.settings.inspection_recovery_interval,
                )
            )
        return True

    def _reconcile(self, run):
        execution = run["execution"]
        if execution is None:
            with self.database.transaction() as connection:
                self._finish_inspection(
                    connection,
                    run,
                    _inspection_failure(
                        "inspection runtime",
                        "Inspection ended before assignment.",
                        "cancelled",
                    ),
                    "cancelled",
                )
            return
        if run["status"] == "cancelling" or run["expires"] <= time.time():
            self.driver.cleanup(execution["name"])
            with self.database.transaction() as connection:
                self._finish_inspection(
                    connection,
                    run,
                    _inspection_failure(
                        "inspection runtime",
                        "Inspection cancelled or timed out.",
                        "timeout",
                        504,
                    ),
                    "cancelled",
                )
            return
        if execution.get("result_ready"):
            self._collect_inspection(run, execution, "succeeded")
            return
        state = self.driver.inspect(execution["name"])
        if state == "running":
            return
        if state == "missing":
            with self.database.transaction() as connection:
                self._finish_inspection(
                    connection,
                    run,
                    _inspection_failure(
                        "inspection runtime",
                        "Inspection sandbox disappeared.",
                        "runtime_failed",
                    ),
                    "failed",
                )
        else:
            self._collect_inspection(run, execution, state)

    def _runtime_timings(self, run):
        for line in self.driver.logs(run["execution"]["name"]).splitlines():
            try:
                timing = json.loads(line)
            except json.JSONDecodeError:
                continue
            if not isinstance(timing, dict) or timing.get("inspection_stage") not in {
                "handoff_received",
                "claimed",
                "work_finished",
                "upload_finished",
            }:
                continue
            if all(
                isinstance(timing.get(field), (int, float))
                for field in ("seconds", "timestamp")
            ):
                LOGGER.info(
                    "Inspection runtime timing: id=%s stage=%s seconds=%.6f timestamp=%.6f",
                    run["id"],
                    timing["inspection_stage"],
                    timing["seconds"],
                    timing["timestamp"],
                )

    def _finish_inspection(self, connection, run, result, status):
        from .inspection_cache import accept_cache

        if not self._owns(row(connection, runs, run["id"]), run):
            return
        if run["spec"].get("inspection_editor"):
            editor = row(
                connection, inspection_editors, run["spec"]["inspection_editor"]
            )
            if editor and editor["generation"] != run["spec"]["inspection_generation"]:
                result = _inspection_failure(
                    "inspection queue",
                    "A newer draft replaced this inspection.",
                    "inspection_superseded",
                    409,
                )
                status = "cancelled"
        if status == "succeeded":
            accept_cache(self, connection, run, result)
        elif output := run["spec"].get("_cache_output"):
            self.storage.delete(output["key"])
        super()._finish_inspection(connection, run, result, status)
        connection.execute(
            inspection_sandboxes.update()
            .where(
                inspection_sandboxes.c.run == run["id"],
                inspection_sandboxes.c.owner == self.identifier,
            )
            .values(status="retired", assignment=None, token="")
        )

    def _reap(self):
        with self.database.transaction() as connection:
            desired_image = self._pool_image(connection)
            slots = (
                connection.execute(
                    inspection_sandboxes.select()
                    .where(
                        or_(
                            inspection_sandboxes.c.status == "retired",
                            and_(
                                inspection_sandboxes.c.status.in_(
                                    ["ready", "launching"]
                                ),
                                or_(
                                    inspection_sandboxes.c.expires <= time.time(),
                                    inspection_sandboxes.c.image != desired_image,
                                ),
                            ),
                        )
                    )
                    .with_for_update(skip_locked=True)
                )
                .mappings()
                .all()
            )
            retiring = []
            for slot in slots:
                if slot["status"] == "retired" or (
                    slot["status"] in {"ready", "launching"}
                    and (
                        slot["expires"] <= time.time() or slot["image"] != desired_image
                    )
                ):
                    connection.execute(
                        inspection_sandboxes.update()
                        .where(inspection_sandboxes.c.id == slot["id"])
                        .values(status="retired", assignment=None, token="")
                    )
                    retiring.append(slot["id"])
        for identifier in retiring:
            if self._cleanup_backoff.get(identifier, 0) > time.monotonic():
                self.recovery_after = min(
                    self.recovery_after,
                    self._cleanup_backoff[identifier] - time.monotonic(),
                )
                continue
            if identifier not in self._cleanups:
                self._cleanups[identifier] = self._cleaner.submit(
                    self._cleanup_sandbox, identifier
                )
                self._cleanups[identifier].add_done_callback(
                    partial(self._runtime_changed, identifier)
                )
        self._collect_cleanups()

    def _collect_cleanups(self):
        for identifier, pending in tuple(self._cleanups.items()):
            if not pending.done():
                continue
            del self._cleanups[identifier]
            try:
                removed = pending.result()
            except _RUNTIME_ERRORS as exc:
                self._retry_cleanup(identifier)
                LOGGER.warning(
                    "Inspection cleanup will retry: id=%s error=%s",
                    identifier,
                    type(exc).__name__,
                )
                continue
            if removed is False:
                self._retry_cleanup(identifier)
                continue
            self._cleanup_backoff.pop(identifier, None)
            with self.database.transaction() as connection:
                connection.execute(
                    inspection_sandboxes.delete().where(
                        inspection_sandboxes.c.id == identifier,
                        inspection_sandboxes.c.status == "retired",
                    )
                )

    def _retry_cleanup(self, identifier):
        self._cleanup_backoff[identifier] = time.monotonic() + 1
        self.recovery_after = min(self.recovery_after, 1)

    def _runtime_changed(self, identifier, _pending):
        if hub := notification_hub(self.database):
            hub.wake(identifier)

    def _cleanup_sandbox(self, identifier):
        started = time.monotonic()
        with self.database.transaction() as connection:
            slot = row(connection, inspection_sandboxes, identifier)
            run = row(connection, runs, slot["run"]) if slot and slot["run"] else None
        if run and run["execution"]:
            self._runtime_timings(run)
        self.driver.cleanup(identifier)
        if self.driver.inspect(identifier) != "missing":
            return False
        LOGGER.info(
            "Inspection sandbox removed: id=%s cleanup_seconds=%.3f",
            identifier,
            time.monotonic() - started,
        )
        return True


async def serve(controller, hub, stop):
    """Process database and runtime notifications with bounded recovery scans.

    Args:
        controller: Shared durable inspection controller.
        hub: Started notification listener.
        stop: Async shutdown event.

    Returns:
        None after stopping scheduling; durable assignments remain recoverable.
    """
    watcher = asyncio.create_task(_runtime_events(controller, hub, stop))
    recovery_at = 0
    try:
        with hub.subscribe() as changed:
            while not stop.is_set():
                changed.clear()
                identifiers = set(changed.identifiers)
                changed.identifiers.clear()
                recovery = time.monotonic() >= recovery_at or "*" in identifiers
                if recovery:
                    recovery_at = (
                        time.monotonic()
                        + controller.settings.inspection_recovery_interval
                    )
                try:
                    await asyncio.to_thread(controller.step, identifiers, recovery)
                    recovery_at = min(
                        recovery_at, time.monotonic() + controller.recovery_after
                    )
                except _RUNTIME_ERRORS as exc:
                    LOGGER.warning(
                        "Inspection runtime unavailable; retrying durable reconciliation: error=%s",
                        type(exc).__name__,
                    )
                    recovery_at = min(recovery_at, time.monotonic() + 1)
                try:
                    await asyncio.wait_for(
                        changed.wait(), max(0.01, recovery_at - time.monotonic())
                    )
                except TimeoutError:
                    continue
    finally:
        watcher.cancel()
        await asyncio.gather(watcher, return_exceptions=True)
        await asyncio.to_thread(controller._launcher.shutdown, wait=True)
        await asyncio.to_thread(controller._cleaner.shutdown, wait=True)


async def _runtime_events(controller, hub, stop):
    while not stop.is_set():
        try:
            await asyncio.to_thread(_read_runtime_events, controller, hub)
        except _RUNTIME_ERRORS:
            LOGGER.warning(
                "Inspection runtime event stream lost; reconciling and reconnecting"
            )
            await asyncio.sleep(1)
        hub.wake("*")


def _read_runtime_events(controller, hub):
    for identifier in controller.driver.inspection_events():
        hub.wake(f"runtime:{identifier}")


async def _main(settings):
    database = Database(settings.database_url, settings.secret_key)
    hub = InspectionNotifications(database)
    await hub.start()
    stop = asyncio.Event()
    for signum in (signal.SIGTERM, signal.SIGINT):
        asyncio.get_running_loop().add_signal_handler(signum, stop.set)
    with closing(GitHubTransport()) as transport:
        try:
            await serve(
                InspectionController(
                    settings,
                    database,
                    execution_driver(settings),
                    artifact_storage(settings),
                    GitHubProvider(settings, transport),
                ),
                hub,
                stop,
            )
        finally:
            await hub.close()
            database.engine.dispose()


def main() -> None:
    """Run the optional shared inspection controller.

    Returns:
        None after graceful shutdown.

    Raises:
        ValueError: Pooling is disabled or an unsupported execution driver is selected.
    """
    settings = Settings()
    if (
        not settings.inspection_controller_enabled
        or not settings.execution_enabled
        or settings.runner not in {"docker", "kubernetes"}
    ):
        raise ValueError(
            "Enable the inspection controller with a built-in Docker or Kubernetes driver"
        )
    configure_ca_bundle(CA_BUNDLE_MOUNT_PATH if settings.ca_bundle else None)
    logging.basicConfig(level=logging.INFO)
    asyncio.run(_main(settings))


if __name__ == "__main__":
    main()
