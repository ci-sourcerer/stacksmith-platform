import json
import time
from functools import partial
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest
from cryptography.fernet import Fernet
from kubernetes.client.exceptions import ApiException
from sqlalchemy import select
from test_control import _complete, _create, _read
from test_control import platform as _platform_fixture

from stacksmith_platform.database import locks, row, runs, scheduler_health, workers
from stacksmith_platform.scheduling import (
    KubernetesDispatcher,
    requested_supervision,
    supervisor_deadline,
    supervisor_job,
)
from stacksmith_platform.supervision import maintain, supervise
from stacksmith_platform.worker import RunReconciler


@pytest.fixture(name="platform")
def _scheduling_platform(tmp_path, monkeypatch):
    yield from _platform_fixture.__wrapped__(tmp_path, monkeypatch)


class _Batch:
    def __init__(self):
        self.jobs = {}
        self.creates = []
        self.failure = None

    def read_namespaced_job(self, name, namespace, **kwargs):
        """Return a previously created fake Job or a Kubernetes 404."""
        if self.failure:
            raise self.failure
        if name not in self.jobs:
            raise ApiException(status=404)
        return self.jobs[name]

    def create_namespaced_job(self, namespace, body, **kwargs):
        """Create one fake Job by stable name, preserving Kubernetes conflicts."""
        if self.failure:
            raise self.failure
        name = body["metadata"]["name"]
        if name in self.jobs:
            raise ApiException(status=409)
        self.creates.append(body)
        self.jobs[name] = SimpleNamespace(
            metadata=SimpleNamespace(labels=body["metadata"]["labels"]),
            status=SimpleNamespace(succeeded=0, failed=0),
        )


def _kubernetes(platform):
    platform.settings.supervision_mode = "kubernetes-jobs"
    platform.settings.supervisor_image = "platform@sha256:" + "d" * 64
    platform.settings.supervisor_config_secret = "platform-config"
    platform.settings.supervisor_database_secret = "supervisor-database"
    platform.settings.supervisor_namespace = "control"
    return KubernetesDispatcher(platform.settings, platform.database, _Batch())


def _host(platform):
    reconciler = RunReconciler(
        platform.settings,
        platform.database,
        platform.driver,
        platform.storage,
        platform.app.state.provider,
        platform.app.state.secret_catalog,
    )
    reconciler.identifier = "supervisor-test"
    return reconciler


def _set_run(platform, identifier, **changes):
    with platform.database.transaction() as connection:
        connection.execute(
            runs.update().where(runs.c.id == identifier).values(**changes)
        )


def _finish_on_sleep(platform, identifier, interval):
    if _read(platform, identifier)["status"] == "running":
        _complete(platform, identifier)


def _drive(platform, monkeypatch, identifier, generation):
    platform.worker = SimpleNamespace(tick=lambda: None)
    monkeypatch.setattr(
        "stacksmith_platform.supervision.time.sleep",
        partial(_finish_on_sleep, platform, identifier),
    )
    supervise(_host(platform), identifier, generation, interval=0)


def _payload(platform, run):
    execution = platform.driver.executions[run["execution"]["name"]]
    return json.loads(
        Fernet(execution["environment"]["PLATFORM_HANDOFF_KEY"].encode()).decrypt(
            platform.storage.get(execution["environment"]["PLATFORM_INPUT_URL"])
        )
    )


@pytest.mark.parametrize(
    ("action", "override", "expected"),
    [("inspection", None, 0.125), ("plan", None, 2), ("inspection", 0, 0)],
)
def test_supervisor_poll_interval_depends_on_action(
    platform, monkeypatch, action, override, expected
):
    dispatcher = _kubernetes(platform)
    identifier = _create(platform, action="plan").json()["id"]
    _set_run(platform, identifier, action=action)
    dispatcher.dispatch(identifier)
    host = _host(platform)
    host.settings.inspection_poll_interval = 0.125
    sleeps = []

    def sleep(interval):
        sleeps.append(interval)
        _set_run(platform, identifier, status="cancelled")

    monkeypatch.setattr("stacksmith_platform.supervision.time.sleep", sleep)
    monkeypatch.setattr(host, "reconcile_run", lambda *args: True)
    supervise(host, identifier, 1, interval=override)
    assert sleeps == [expected]


def test_compose_worker_does_not_take_kubernetes_runs(platform):
    _kubernetes(platform)
    identifier = _create(platform, action="plan").json()["id"]
    platform.worker.tick()
    assert _read(platform, identifier)["status"] == "queued"
    assert not platform.driver.launches


def test_dispatch_is_idempotent_and_reserves_identity_before_create(platform):
    dispatcher = _kubernetes(platform)
    identifier = _create(platform, action="plan").json()["id"]
    assert _read(platform, identifier)["supervision"]["requested"]
    original = dispatcher.batch.create_namespaced_job

    def create(namespace, body, **kwargs):
        assert (
            _read(platform, identifier)["supervision"]["job"]
            == body["metadata"]["name"]
        )
        return original(namespace, body, **kwargs)

    dispatcher.batch.create_namespaced_job = create
    assert dispatcher.dispatch(identifier)
    assert not dispatcher.dispatch(identifier)
    assert len(dispatcher.batch.creates) == 1
    assert _read(platform, identifier)["supervision"]["generation"] == 1


def test_failed_create_retains_identity_for_recovery(platform):
    dispatcher = _kubernetes(platform)
    identifier = _create(platform, action="plan").json()["id"]
    dispatcher.batch.failure = ApiException(status=503, reason="private-api-details")
    assert not dispatcher.dispatch(identifier)
    failed = _read(platform, identifier)["supervision"]
    assert failed["requested"] and failed["generation"] == 1
    assert "private-api-details" not in failed["error"]
    dispatcher.batch.failure = None
    assert dispatcher.dispatch_pending() == 1
    assert _read(platform, identifier)["supervision"]["job"] == failed["job"]
    assert _read(platform, identifier)["supervision"]["generation"] == 1


def test_create_succeeded_but_response_lost_adopts_existing_job(platform):
    dispatcher = _kubernetes(platform)
    identifier = _create(platform, action="plan").json()["id"]
    original = dispatcher.batch.create_namespaced_job

    def create(namespace, body, **kwargs):
        original(namespace, body, **kwargs)
        raise ApiException(status=504)

    dispatcher.batch.create_namespaced_job = create
    assert not dispatcher.dispatch(identifier)
    assert not dispatcher.dispatch(identifier)
    assert len(dispatcher.batch.creates) == 1
    assert _read(platform, identifier)["supervision"]["generation"] == 1


def test_create_conflict_accepts_only_matching_identity(platform):
    dispatcher = _kubernetes(platform)
    identifier = _create(platform, action="plan").json()["id"]
    original = dispatcher.batch.create_namespaced_job

    def create(namespace, body, **kwargs):
        original(namespace, body, **kwargs)
        raise ApiException(status=409)

    dispatcher.batch.create_namespaced_job = create
    assert dispatcher.dispatch(identifier)


def test_wrong_identity_conflict_is_reported(platform):
    dispatcher = _kubernetes(platform)
    identifier = _create(platform, action="plan").json()["id"]
    original = dispatcher.batch.create_namespaced_job

    def create(namespace, body, **kwargs):
        original(namespace, body, **kwargs)
        dispatcher.batch.jobs[body["metadata"]["name"]].metadata.labels["run"] = (
            "other-run"
        )
        raise ApiException(status=409)

    dispatcher.batch.create_namespaced_job = create
    assert not dispatcher.dispatch(identifier)
    assert _read(platform, identifier)["supervision"]["error"]


def test_approval_and_terminal_states_do_not_dispatch(platform):
    dispatcher = _kubernetes(platform)
    identifier = _create(platform, action="plan").json()["id"]
    for status in (
        "awaiting_approval",
        "awaiting_authorization",
        "succeeded",
        "failed",
        "uncertain",
        "cancelled",
    ):
        _set_run(platform, identifier, status=status)
        assert not dispatcher.dispatch(identifier)
    assert not dispatcher.batch.creates


def test_lock_blocked_runs_do_not_create_waiting_supervisors(platform):
    dispatcher = _kubernetes(platform)
    first = _create(platform, key="first").json()["id"]
    second = _create(platform, key="second").json()["id"]
    assert dispatcher.dispatch(first)
    assert not dispatcher.dispatch(second)
    with platform.database.transaction() as connection:
        connection.execute(locks.delete().where(locks.c.run == first))
    assert dispatcher.dispatch(second)


def test_active_ownership_blocks_replacement_until_lease_expiry(platform):
    dispatcher = _kubernetes(platform)
    identifier = _create(platform, action="plan").json()["id"]
    dispatcher.dispatch(identifier)
    metadata = _read(platform, identifier)["supervision"]
    dispatcher.batch.jobs[metadata["job"]].status.failed = 1
    _set_run(platform, identifier, worker="lost-owner", lease=time.time() + 120)
    assert not dispatcher.dispatch(identifier)
    _set_run(platform, identifier, lease=time.time() - 1)
    assert dispatcher.dispatch_pending() == 1
    assert _read(platform, identifier)["supervision"]["generation"] == 2


def test_generation_and_ownership_fence_supervisors(platform):
    dispatcher = _kubernetes(platform)
    identifier = _create(platform, action="plan").json()["id"]
    dispatcher.dispatch(identifier)
    host = _host(platform)
    assert not host.reconcile_run(identifier, 99)
    _set_run(platform, identifier, worker="another-owner", lease=time.time() + 120)
    supervise(host, identifier, 1, interval=0)
    assert _read(platform, identifier)["worker"] == "another-owner"
    assert not platform.driver.launches


def test_finite_supervisor_finishes_plan_without_heartbeats(platform, monkeypatch):
    dispatcher = _kubernetes(platform)
    identifier = _create(platform, action="plan").json()["id"]
    dispatcher.dispatch(identifier)
    _drive(platform, monkeypatch, identifier, 1)
    run = _read(platform, identifier)
    assert run["status"] == "succeeded"
    assert run["worker"] is None and run["lease"] is None
    assert run["supervision"]["state"] == "finished"
    assert platform.driver.launches == 3
    with platform.database.transaction() as connection:
        assert not connection.execute(select(workers)).all()


def test_supervisor_exits_at_approval_and_dispatch_resumes(platform, monkeypatch):
    dispatcher = _kubernetes(platform)
    identifier = _create(platform).json()["id"]
    dispatcher.dispatch(identifier)
    _drive(platform, monkeypatch, identifier, 1)
    assert _read(platform, identifier)["status"] == "awaiting_approval"
    assert platform.driver.launches == 3
    assert dispatcher.dispatch_pending() == 0
    dispatcher.batch.jobs[
        _read(platform, identifier)["supervision"]["job"]
    ].status.succeeded = 1
    response = platform.client.post(f"/api/v1/runs/{identifier}/approve", json={})
    assert response.status_code == 200
    assert dispatcher.dispatch(identifier)
    _drive(platform, monkeypatch, identifier, 2)
    assert _read(platform, identifier)["status"] == "succeeded"
    assert platform.driver.launches == 5


def test_replacement_adopts_running_execution_instead_of_replaying(platform):
    dispatcher = _kubernetes(platform)
    identifier = _create(platform, action="plan").json()["id"]
    dispatcher.dispatch(identifier)
    host = _host(platform)
    host.reconcile_run(identifier, 1)
    original = _read(platform, identifier)
    dispatcher.batch.jobs[original["supervision"]["job"]].status.failed = 1
    _set_run(platform, identifier, lease=time.time() - 1)
    dispatcher.dispatch(identifier)
    replacement = _host(platform)
    replacement.identifier = "replacement"
    assert replacement.reconcile_run(identifier, 2)
    assert platform.driver.launches == 1
    assert (
        _read(platform, identifier)["execution"]["name"]
        == original["execution"]["name"]
    )


def test_missing_claimed_mutation_stays_uncertain_after_recovery(platform, monkeypatch):
    dispatcher = _kubernetes(platform)
    identifier = _create(platform).json()["id"]
    dispatcher.dispatch(identifier)
    _drive(platform, monkeypatch, identifier, 1)
    dispatcher.batch.jobs[
        _read(platform, identifier)["supervision"]["job"]
    ].status.succeeded = 1
    assert (
        platform.client.post(f"/api/v1/runs/{identifier}/approve", json={}).status_code
        == 200
    )
    dispatcher.dispatch(identifier)
    host = _host(platform)
    host.reconcile_run(identifier, 2)
    current = _read(platform, identifier)
    payload = _payload(platform, current)
    assert (
        platform.client.post(
            payload["claim_url"],
            headers={"Authorization": f"Bearer {payload['claim_token']}"},
        ).status_code
        == 200
    )
    dispatcher.batch.jobs[current["supervision"]["job"]].status.failed = 1
    platform.driver.executions.pop(current["execution"]["name"])
    _set_run(platform, identifier, lease=time.time() - 1)
    launches = platform.driver.launches
    dispatcher.dispatch(identifier)
    host.reconcile_run(identifier, 3)
    assert _read(platform, identifier)["status"] == "uncertain"
    assert platform.driver.launches == launches
    with platform.database.transaction() as connection:
        assert row(connection, locks, "acme/service")["run"] == identifier


def test_cancelled_execution_is_terminated_by_targeted_supervisor(platform):
    dispatcher = _kubernetes(platform)
    identifier = _create(platform, action="plan").json()["id"]
    dispatcher.dispatch(identifier)
    host = _host(platform)
    host.reconcile_run(identifier, 1)
    assert (
        platform.client.post(f"/api/v1/runs/{identifier}/cancel", json={}).status_code
        == 200
    )
    host.reconcile_run(identifier, 1)
    assert _read(platform, identifier)["status"] == "cancelled"


def test_cancelling_run_without_execution_finishes_without_retry_loop(platform):
    dispatcher = _kubernetes(platform)
    identifier = _create(platform, action="plan").json()["id"]
    _set_run(platform, identifier, status="cancelling", execution=None)
    dispatcher.dispatch(identifier)
    assert _host(platform).reconcile_run(identifier, 1)
    assert _read(platform, identifier)["status"] == "cancelled"


def test_recovery_performs_maintenance_without_heartbeat(platform):
    dispatcher = _kubernetes(platform)
    identifier = _create(platform, action="plan").json()["id"]
    assert maintain(_host(platform), dispatcher) == 1
    assert _read(platform, identifier)["supervision"]["dispatched_at"]
    with platform.database.transaction() as connection:
        assert row(connection, scheduler_health, "recovery")["completed"]
        assert not connection.execute(select(workers)).all()


def test_recovery_failure_retains_last_success_and_reports_sanitized_error(platform):
    dispatcher = _kubernetes(platform)
    maintain(_host(platform), dispatcher)
    with platform.database.transaction() as connection:
        completed = row(connection, scheduler_health, "recovery")["completed"]
    _create(platform, action="plan")
    dispatcher.batch.failure = ApiException(status=503, reason="private-api-details")
    maintain(_host(platform), dispatcher)
    with platform.database.transaction() as connection:
        health = row(connection, scheduler_health, "recovery")
    assert health["completed"] == completed
    assert health["error"] and "private-api-details" not in health["error"]


def test_supervisor_manifest_keeps_credentials_out_of_execution_job(platform):
    _kubernetes(platform)
    platform.settings.allow_local_execution_image = True
    run = _create(platform, action="plan").json()
    manifest = supervisor_job(
        platform.settings, run, {"job": "supervisor", "generation": 1}
    )
    spec = manifest["spec"]
    pod = spec["template"]["spec"]
    assert spec["backoffLimit"] == 0 and spec["ttlSecondsAfterFinished"] == 86400
    assert "ownerReferences" not in manifest["metadata"]
    assert pod["automountServiceAccountToken"]
    assert pod["serviceAccountName"] == platform.settings.supervisor_service_account
    assert pod["containers"][0]["envFrom"] == [
        {"secretRef": {"name": "platform-config"}},
        {"secretRef": {"name": "supervisor-database"}},
    ]
    assert {item["name"]: item["value"] for item in pod["containers"][0]["env"]}[
        "STACKSMITH_PLATFORM_ALLOW_LOCAL_EXECUTION_IMAGE"
    ] == "true"
    assert platform.settings.secret_key not in json.dumps(manifest)
    assert "STACKSMITH_PLATFORM_DATABASE_URL" not in json.dumps(
        pod["containers"][0]["env"]
    )
    assert spec["activeDeadlineSeconds"] == 300 + 3 * (
        platform.settings.phase_timeout + 300
    )


def test_deadline_stops_before_approval_and_inspection_uses_command_timeout(platform):
    _kubernetes(platform)
    assert (
        supervisor_deadline(
            platform.settings,
            {"phase": 0, "spec": {"phases": ["prepare", "approval", "apply"]}},
        )
        == 600 + platform.settings.phase_timeout
    )
    assert (
        supervisor_deadline(
            platform.settings, {"phase": 0, "spec": {"phases": ["inspect"]}}
        )
        == 600 + platform.settings.command_timeout
    )


def test_kubernetes_status_reports_jobs_and_recovery_not_workers(platform):
    dispatcher = _kubernetes(platform)
    identifier = _create(platform, action="plan").json()["id"]
    maintain(_host(platform), dispatcher)
    response = platform.client.get("/api/v1/status")
    assert response.status_code == 200
    assert response.json()["mode"] == "kubernetes-jobs"
    assert "workers" not in response.json()
    assert not response.json()["recovery"]["overdue"]
    assert response.json()["supervisors"][0]["run"] == identifier
    assert platform.settings.secret_key not in response.text


def test_api_dispatch_occurs_after_transaction_commit(platform):
    _kubernetes(platform)
    dispatcher = MagicMock()
    platform.app.state.dispatcher = dispatcher
    identifier = _create(platform, action="plan").json()["id"]
    dispatcher.dispatch_pending.assert_called_once()
    assert _read(platform, identifier)["supervision"]["requested"]


def test_compose_mode_does_not_create_supervisor_metadata(platform):
    assert requested_supervision(platform.settings, "queued") is None
    identifier = _create(platform, action="plan").json()["id"]
    assert _read(platform, identifier)["supervision"] is None


def test_stale_generation_cannot_finish_or_collect_inspection(platform):
    dispatcher = _kubernetes(platform)
    identifier = _create(platform, action="plan").json()["id"]
    dispatcher.dispatch(identifier)
    host = _host(platform)
    host.reconcile_run(identifier, 1)
    stale = _read(platform, identifier)
    _set_run(
        platform, identifier, supervision={**stale["supervision"], "generation": 2}
    )
    with platform.database.transaction() as connection:
        host._finish(connection, stale, "failed")
    assert _read(platform, identifier)["status"] == "running"


def _complete_inspection(database, identifier):
    with database.transaction() as connection:
        connection.execute(
            runs.update()
            .where(runs.c.id == identifier)
            .values(
                status="succeeded",
                result={"_inspection_output": database.encrypt("inspection-result")},
            )
        )


@pytest.mark.parametrize("wait_timeout, expected_wait", [(None, 90), (180, 180)])
def test_inspection_dispatches_before_waiting_and_retains_command_timeout(
    platform, monkeypatch, wait_timeout, expected_wait
):
    from stacksmith_platform.inspection import InspectionQueue

    _kubernetes(platform)
    platform.settings.database_url = platform.database.engine.url.render_as_string(
        hide_password=False
    )
    platform.settings.inspection_wait_timeout = wait_timeout
    dispatcher = MagicMock()
    dispatcher.dispatch.side_effect = partial(_complete_inspection, platform.database)
    monkeypatch.setattr(
        "stacksmith_platform.inspection.KubernetesDispatcher", lambda *args: dispatcher
    )
    queue = InspectionQueue(platform.settings)
    assert queue.execute(["--help"], None, None, None) == "inspection-result"
    dispatcher.dispatch.assert_called_once()
    record = _read(platform, dispatcher.dispatch.call_args.args[0])
    assert record["supervision"]["requested"]
    assert record["expires"] - record["created"] == pytest.approx(expected_wait, abs=1)
    assert platform.settings.command_timeout == 30
    queue.database.engine.dispose()
