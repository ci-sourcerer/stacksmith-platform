import asyncio
import json
import time
from concurrent.futures import Future
from types import SimpleNamespace
from unittest.mock import Mock

import pytest
from cryptography.fernet import Fernet
from docker.errors import DockerException
from test_control import _read
from test_control import platform as _platform_fixture
from test_inspection_concurrency import _complete, _set_run

from stacksmith_platform.database import Database, inspection_sandboxes, runs
from stacksmith_platform.errors import ComposerError
from stacksmith_platform.inspection import InspectionQueue
from stacksmith_platform.inspection_controller import InspectionController
from stacksmith_platform.inspection_health import status
from stacksmith_platform.inspection_notifications import InspectionNotifications, notify
from stacksmith_platform.inspection_sandboxes import _assignment


@pytest.fixture(name="pool")
def _pool(tmp_path, monkeypatch):
    monkeypatch.setattr(
        "stacksmith_platform.inspection_controller.ThreadPoolExecutor",
        _ImmediateLauncher,
    )
    for platform in _platform_fixture.__wrapped__(tmp_path, monkeypatch):
        platform.settings.inspection_controller_enabled = True
        platform.settings.inspection_pool_ready = 2
        platform.settings.inspection_pool_active = 2
        platform.settings.inspection_pool_total = 3
        platform.settings.database_url = platform.database.engine.url.render_as_string(
            hide_password=False
        )
        platform.controller = InspectionController(
            platform.settings,
            platform.database,
            platform.driver,
            platform.storage,
            platform.app.state.provider,
        )
        platform.queue = InspectionQueue(platform.settings)
        yield platform


class _ImmediateLauncher:
    def __init__(self, **kwargs):
        pass

    def submit(self, function, *args):
        result = Future()
        try:
            result.set_result(function(*args))
        except DockerException as exc:
            result.set_exception(exc)
        return result


def _submit(pool, actor="alice", editor="editor", generation=1, operation="review"):
    return pool.queue._submit(
        [operation],
        None,
        f"token-{actor}",
        {
            "version": 1,
            "source": "components: {}\n",
            "edits": [],
            "path": "stack.yaml",
        },
        None,
        {
            "actor": actor,
            "editor_id": editor,
            "generation": generation,
            "repository": "acme/service",
            "path": "stack.yaml",
        },
    )


def _slots(pool):
    with pool.database.transaction() as connection:
        return [
            dict(slot)
            for slot in connection.execute(inspection_sandboxes.select()).mappings()
        ]


def _capability(pool, identifier):
    execution = _read(pool, identifier)["execution"]
    return json.loads(
        Fernet(pool.database.decrypt(execution["key"]).encode()).decrypt(
            pool.storage.get(execution["input"])
        )
    )


def _ready(pool):
    pool.controller.step()
    for slot in _slots(pool):
        if slot["status"] == "launching":
            assert (
                _assignment(
                    SimpleNamespace(
                        app=pool.app,
                        headers={
                            "authorization": f"Bearer {pool.database.decrypt(slot['token'])}"
                        },
                    ),
                    slot["id"],
                )
                is None
            )


def test_pool_only_advertises_ready_after_sandbox_handshake(pool):
    pool.controller.step()
    assert len(_slots(pool)) == 2
    assert {slot["status"] for slot in _slots(pool)} == {"launching"}
    for execution in pool.driver.executions.values():
        assert set(execution["environment"]) == {
            "PLATFORM_SANDBOX_URL",
            "PLATFORM_SANDBOX_TOKEN",
            "PLATFORM_SANDBOX_EXPIRES",
        }
        assert "token-alice" not in json.dumps(execution)
    _ready(pool)
    assert status(pool.database, pool.settings)["ready"] == 2


def test_dead_idle_sandbox_is_replaced_before_assignment(pool):
    _ready(pool)
    identifier = _slots(pool)[0]["id"]
    pool.driver.cleanup(identifier)
    pool.controller.step([f"runtime:{identifier}"])
    assert all(slot["id"] != identifier for slot in _slots(pool))
    assert len(_slots(pool)) == 2


def test_controller_reports_launch_failures_as_unhealthy(pool, monkeypatch):
    launch = pool.driver.launch
    monkeypatch.setattr(
        pool.driver, "launch", Mock(side_effect=DockerException("unavailable"))
    )
    pool.controller.step()
    assert not status(pool.database, pool.settings)["healthy"]
    monkeypatch.setattr(pool.driver, "launch", launch)
    pool.controller._launch_backoff = 0
    pool.controller.step()
    assert status(pool.database, pool.settings)["healthy"]


def test_launch_response_failure_does_not_retire_an_authenticated_ready_sandbox(
    pool, monkeypatch
):
    launch = pool.driver.launch

    def ready_before_response(name, image, environment, timeout):
        launch(name, image, environment, timeout)
        _assignment(
            SimpleNamespace(
                app=pool.app,
                headers={
                    "authorization": f"Bearer {environment['PLATFORM_SANDBOX_TOKEN']}"
                },
            ),
            name,
        )
        raise DockerException("Launch response lost after readiness handshake")

    monkeypatch.setattr(pool.driver, "launch", ready_before_response)
    pool.controller.step()
    assert {slot["status"] for slot in _slots(pool)} == {"ready"}
    assert status(pool.database, pool.settings)["healthy"]


def test_replenishment_does_not_block_active_completion(pool, monkeypatch):
    from concurrent.futures import ThreadPoolExecutor
    from threading import Event

    _ready(pool)
    identifier = _submit(pool)
    blocked = Event()
    pool.controller._launcher = ThreadPoolExecutor(max_workers=1)
    monkeypatch.setattr(
        pool.controller, "_launch_standby", lambda *args: blocked.wait(5)
    )
    try:
        pool.controller.step()
        _complete(pool, identifier)
        pool.controller.step([identifier])
        assert _read(pool, identifier)["status"] == "succeeded"
        assert not blocked.is_set()
    finally:
        blocked.set()
        pool.controller._launcher.shutdown()


def test_slow_cleanup_does_not_block_results_or_release_capacity_early(
    pool, monkeypatch
):
    from concurrent.futures import ThreadPoolExecutor
    from threading import Event

    _ready(pool)
    alice = _submit(pool)
    bob = _submit(pool, actor="bob")
    pool.controller.step()
    _complete(pool, alice)
    _complete(pool, bob)
    blocked = Event()
    pool.controller._cleaner = ThreadPoolExecutor(max_workers=1)
    monkeypatch.setattr(
        pool.controller, "_cleanup_sandbox", lambda *args: blocked.wait(5)
    )
    try:
        pool.controller.step([alice, bob])
        assert _read(pool, alice)["status"] == _read(pool, bob)["status"] == "succeeded"
        assert len(_slots(pool)) == 3
        assert sum(slot["status"] == "retired" for slot in _slots(pool)) == 2
    finally:
        blocked.set()
        pool.controller._cleaner.shutdown()
    pool.controller.step()
    assert len(_slots(pool)) == 2


def test_recovery_removes_unrecorded_runtime_resources(pool, monkeypatch):
    _ready(pool)
    pool.driver.executions["orphan"] = {}
    monkeypatch.setattr(
        pool.driver,
        "inspection_resources",
        lambda: list(pool.driver.executions),
        raising=False,
    )
    pool.controller.step(recovery=True)
    assert "orphan" not in pool.driver.executions
    assert len(_slots(pool)) == 2


def test_one_assignment_is_delivered_once_and_never_reused(pool):
    _ready(pool)
    identifier = _submit(pool)
    pool.controller.step()
    slot = next(slot for slot in _slots(pool) if slot["run"] == identifier)
    request = SimpleNamespace(
        app=pool.app,
        headers={"authorization": f"Bearer {pool.database.decrypt(slot['token'])}"},
    )
    assigned = _assignment(request, slot["id"])
    assert set(assigned) == {"PLATFORM_INPUT_URL", "PLATFORM_HANDOFF_KEY"}
    with pytest.raises(ComposerError) as error:
        _assignment(request, slot["id"])
    assert error.value.status == 409
    _complete(pool, identifier)
    pool.controller.step([identifier])
    assert _read(pool, identifier)["status"] == "succeeded"
    assert slot["id"] not in pool.driver.executions
    assert all(current["id"] != slot["id"] for current in _slots(pool))


def test_uploaded_result_is_collected_before_runtime_shutdown(pool):
    _ready(pool)
    identifier = _submit(pool)
    pool.controller.step()
    headers = {
        "Authorization": f"Bearer {_capability(pool, identifier)['claim_token']}"
    }
    assert (
        pool.client.post(
            f"/api/v1/executions/{identifier}/claim", headers=headers
        ).status_code
        == 200
    )
    _complete(pool, identifier)
    pool.driver.executions[_read(pool, identifier)["execution"]["name"]]["state"] = (
        "running"
    )
    for _ in range(2):
        assert pool.client.post(
            f"/api/v1/executions/{identifier}/inspection-result", headers=headers
        ).json() == {"accepted": True}
    pool.controller.step([identifier])
    assert _read(pool, identifier)["status"] == "succeeded"
    assert pool.client.post(
        f"/api/v1/executions/{identifier}/inspection-result", headers=headers
    ).json() == {"accepted": False}


def test_uploaded_result_does_not_wait_for_runtime_inspection_or_logs(
    pool, monkeypatch
):
    from concurrent.futures import ThreadPoolExecutor
    from threading import Event

    _ready(pool)
    identifier = _submit(pool)
    pool.controller.step()
    _complete(pool, identifier)
    _set_run(
        pool,
        identifier,
        execution={**_read(pool, identifier)["execution"], "result_ready": True},
    )
    blocked = Event()
    pool.controller._cleaner = ThreadPoolExecutor(max_workers=1)
    monkeypatch.setattr(
        pool.controller, "_runtime_timings", lambda *args: blocked.wait(5)
    )
    monkeypatch.setattr(
        pool.driver,
        "inspect",
        Mock(return_value="missing"),
    )
    try:
        pool.controller.step([identifier])
        assert _read(pool, identifier)["status"] == "succeeded"
        pool.driver.inspect.assert_not_called()
        assert not blocked.is_set()
    finally:
        blocked.set()
        pool.controller._cleaner.shutdown()


def test_cleanup_failure_keeps_capacity_reserved_without_blocking_other_results(
    pool, monkeypatch
):
    _ready(pool)
    alice = _submit(pool)
    bob = _submit(pool, actor="bob")
    pool.controller.step()
    _complete(pool, alice)
    _complete(pool, bob)
    cleanup = pool.driver.cleanup
    monkeypatch.setattr(
        pool.driver, "cleanup", Mock(side_effect=DockerException("Removal in progress"))
    )
    pool.controller.step([alice, bob])
    assert _read(pool, alice)["status"] == _read(pool, bob)["status"] == "succeeded"
    assert sum(slot["status"] == "retired" for slot in _slots(pool)) == 2
    assert len(_slots(pool)) <= pool.settings.inspection_pool_total
    monkeypatch.setattr(pool.driver, "cleanup", cleanup)
    pool.controller._cleanup_backoff.clear()
    pool.controller.step()
    assert not any(slot["status"] == "retired" for slot in _slots(pool))


def test_asynchronous_runtime_deletion_retains_its_capacity_until_confirmed(
    pool, monkeypatch
):
    _ready(pool)
    identifier = _submit(pool)
    pool.controller.step()
    _complete(pool, identifier)
    cleanup = pool.driver.cleanup
    monkeypatch.setattr(pool.driver, "cleanup", Mock())
    pool.controller.step([identifier])
    assert _read(pool, identifier)["status"] == "succeeded"
    assert any(slot["status"] == "retired" for slot in _slots(pool))
    assert len(_slots(pool)) <= pool.settings.inspection_pool_total
    monkeypatch.setattr(pool.driver, "cleanup", cleanup)
    pool.controller._cleanup_backoff.clear()
    pool.controller.step()
    assert not any(slot["status"] == "retired" for slot in _slots(pool))


@pytest.mark.parametrize("invalid", ["unclaimed", "token", "expiry", "fence", "action"])
def test_result_notification_rejects_invalid_or_stale_capability(pool, invalid):
    _ready(pool)
    identifier = _submit(pool)
    pool.controller.step()
    headers = {
        "Authorization": f"Bearer {_capability(pool, identifier)['claim_token']}"
    }
    if invalid != "unclaimed":
        assert (
            pool.client.post(
                f"/api/v1/executions/{identifier}/claim", headers=headers
            ).status_code
            == 200
        )
    if invalid == "token":
        headers = {"Authorization": "Bearer wrong-token"}
    elif invalid in {"expiry", "fence", "action"}:
        with pool.database.transaction() as connection:
            connection.execute(
                runs.update()
                .where(runs.c.id == identifier)
                .values(
                    **(
                        {"expires": 0}
                        if invalid == "expiry"
                        else {"fence": 99}
                        if invalid == "fence"
                        else {"action": "plan"}
                    )
                )
            )
    assert (
        pool.client.post(
            f"/api/v1/executions/{identifier}/inspection-result", headers=headers
        ).status_code
        == 403
    )


@pytest.mark.parametrize("change", ["token", "expiry"])
def test_assignment_capability_is_checked_before_disclosure(pool, change):
    _ready(pool)
    slot = _slots(pool)[0]
    token = pool.database.decrypt(slot["token"])
    if change == "expiry":
        with pool.database.transaction() as connection:
            connection.execute(
                inspection_sandboxes.update()
                .where(inspection_sandboxes.c.id == slot["id"])
                .values(expires=0)
            )
    else:
        token = "wrong-token"
    with pytest.raises(ComposerError) as error:
        _assignment(
            SimpleNamespace(app=pool.app, headers={"authorization": f"Bearer {token}"}),
            slot["id"],
        )
    assert error.value.status == 403


def test_global_capacity_and_fairness_across_editors(pool):
    _ready(pool)
    alice = _submit(pool, editor="alice-1")
    pending = _submit(pool, editor="alice-2")
    bob = _submit(pool, actor="bob")
    pool.controller.step()
    assert _read(pool, alice)["status"] == "running"
    assert _read(pool, bob)["status"] == "running"
    assert _read(pool, pending)["status"] == "queued"
    assert len(_slots(pool)) == 3
    replacement = InspectionController(
        pool.settings, pool.database, pool.driver, pool.storage, pool.app.state.provider
    )
    replacement.step(recovery=True)
    assert len(_slots(pool)) == 3
    assert _read(pool, pending)["status"] == "queued"


def test_new_generation_replaces_pending_work_but_finishes_active_work(pool):
    _ready(pool)
    active = _submit(pool)
    pool.controller.step()
    pending = _submit(pool, generation=2)
    latest = _submit(pool, generation=3)
    assert _read(pool, pending)["status"] == "cancelled"
    pool.controller.step()
    assert _read(pool, latest)["status"] == "queued"
    _complete(pool, active)
    pool.controller.step([active])
    assert _read(pool, active)["status"] == "cancelled"
    assert _read(pool, latest)["status"] == "running"
    with pytest.raises(ComposerError) as error:
        pool.queue._result(active)
    assert error.value.diagnostics["code"] == "inspection_superseded"


def test_editor_generations_cannot_supersede_another_principal(pool):
    alice = _submit(pool)
    bob = _submit(pool, actor="bob", generation=100)
    assert _read(pool, alice)["status"] == _read(pool, bob)["status"] == "queued"
    with pytest.raises(ComposerError) as error:
        _submit(pool, generation=1)
    assert error.value.diagnostics["code"] == "inspection_superseded"


def test_restart_adopts_expired_leases_without_duplicate_execution(pool):
    _ready(pool)
    identifier = _submit(pool)
    pool.controller.step()
    execution = _read(pool, identifier)["execution"]
    with pool.database.transaction() as connection:
        connection.execute(runs.update().where(runs.c.id == identifier).values(lease=0))
        connection.execute(
            inspection_sandboxes.update()
            .where(inspection_sandboxes.c.run == identifier)
            .values(lease=0)
        )
    replacement = InspectionController(
        pool.settings, pool.database, pool.driver, pool.storage, pool.app.state.provider
    )
    replacement.step(recovery=True)
    assert _read(pool, identifier)["worker"] == replacement.identifier
    assert _read(pool, identifier)["execution"] == execution
    _complete(pool, identifier)
    replacement.step([identifier])
    replacement.step([identifier])
    assert _read(pool, identifier)["status"] == "succeeded"


def test_crash_during_preparation_uses_the_inspection_recovery_lease(pool, monkeypatch):
    _ready(pool)
    identifier = _submit(pool)
    monkeypatch.setattr(
        pool.controller, "_start", Mock(side_effect=RuntimeError("Process crashed"))
    )
    with pytest.raises(RuntimeError, match="Process crashed"):
        pool.controller.step()
    assert (
        0
        < _read(pool, identifier)["lease"] - time.time()
        <= pool.settings.inspection_recovery_interval
    )


def test_recovery_adopts_booting_sandbox_and_replaces_missing_launch(pool):
    pool.controller.step()
    original = _slots(pool)
    pool.driver.cleanup(original[0]["id"])
    with pool.database.transaction() as connection:
        connection.execute(inspection_sandboxes.update().values(lease=0))
    replacement = InspectionController(
        pool.settings, pool.database, pool.driver, pool.storage, pool.app.state.provider
    )
    replacement.step(recovery=True)
    assert original[0]["id"] not in {slot["id"] for slot in _slots(pool)}
    assert original[1]["id"] in {slot["id"] for slot in _slots(pool)}
    assert len(_slots(pool)) == pool.settings.inspection_pool_ready


def test_stale_preparation_cannot_overwrite_or_delete_a_replacement_handoff(
    pool, monkeypatch
):
    _ready(pool)
    identifier = _submit(pool)
    replacement = InspectionController(
        pool.settings, pool.database, pool.driver, pool.storage, pool.app.state.provider
    )
    put = pool.storage.put
    replaced = []

    def handover(key, content):
        if not replaced:
            replaced.append(key)
            _set_run(pool, identifier, lease=0)
            with pool.database.transaction() as connection:
                connection.execute(
                    inspection_sandboxes.update()
                    .where(inspection_sandboxes.c.run == identifier)
                    .values(lease=0)
                )
            replacement.step(recovery=True)
        return put(key, content)

    monkeypatch.setattr(pool.storage, "put", handover)
    pool.controller.step()
    run = _read(pool, identifier)
    assert run["worker"] == replacement.identifier
    assert run["execution"]["input"] != replaced[0]
    assert run["execution"]["input"] in pool.storage.objects
    assert replaced[0] not in pool.storage.objects
    assert _capability(pool, identifier)["claim_token"]


def test_idle_expiry_and_image_rollout_remove_unused_sandboxes(pool):
    _ready(pool)
    original = _slots(pool)[0]["id"]
    with pool.database.transaction() as connection:
        connection.execute(
            inspection_sandboxes.update()
            .where(inspection_sandboxes.c.id == original)
            .values(expires=0)
        )
    pool.controller.step(recovery=True)
    assert all(slot["id"] != original for slot in _slots(pool))
    pool.settings.execution_image = "execution@sha256:" + "d" * 64
    pool.controller.step(recovery=True)
    assert {slot["image"] for slot in _slots(pool)} == {pool.settings.execution_image}


def test_image_rollout_rejects_old_queued_and_new_submissions(pool):
    from stacksmith_platform.inspection_health import register

    identifier = _submit(pool)
    register(
        pool.database,
        pool.settings.model_copy(
            update={"execution_image": "execution@sha256:" + "d" * 64}
        ),
    )
    assert _read(pool, identifier)["status"] == "cancelled"
    with pytest.raises(ComposerError, match="image changed"):
        pool.queue._result(identifier)
    with pytest.raises(ComposerError, match="image changed"):
        _submit(pool, generation=2)


def test_wait_subscribes_before_read_and_handles_already_completed_job(
    pool, monkeypatch
):
    original = pool.queue._submit

    def submit(*args):
        identifier = original(*args)
        with pool.database.transaction() as connection:
            connection.execute(
                runs.update()
                .where(runs.c.id == identifier)
                .values(
                    status="succeeded",
                    result={"_inspection_output": pool.database.encrypt("{}")},
                )
            )
            notify(connection, identifier)
        return identifier

    async def inspect():
        hub = InspectionNotifications(pool.database)
        await hub.start()
        try:
            assert await pool.queue.aexecute(["review"], None, "token", None) == "{}"
            assert not hub._waiters
        finally:
            await hub.close()

    monkeypatch.setattr(pool.queue, "_submit", submit)
    asyncio.run(inspect())


def test_cache_context_is_scoped_and_configuration_changes_invalidate_it(pool):
    from stacksmith_platform.inspection_cache import prepare_cache

    identifier = _submit(pool)
    run = _read(pool, identifier)
    inspection = json.loads(pool.database.decrypt(run["spec"]["_inspection"]))
    inspection["context"] = {
        "repository": "acme/service",
        "path": "stack.yaml",
        "revision": "a" * 40,
        "config_revision": "first",
        "_input_manifest": {"cacheable": True, "digest": "manifest"},
    }
    first, payload = prepare_cache(pool.controller, run, inspection)
    assert payload["_cache"]["output_key"] not in json.dumps(
        first["spec"]["_cache_output"]
    )
    changed, _ = prepare_cache(
        pool.controller,
        run,
        {
            **inspection,
            "context": {**inspection["context"], "config_revision": "second"},
        },
    )
    other, _ = prepare_cache(pool.controller, {**run, "actor": "other"}, inspection)
    assert (
        len({entry["spec"]["_cache_output"]["id"] for entry in (first, changed, other)})
        == 3
    )


def test_unproven_dependencies_never_reuse_or_create_input_cache(pool):
    from stacksmith_platform.inspection_cache import prepare_cache

    run = _read(pool, _submit(pool))
    inspection = json.loads(pool.database.decrypt(run["spec"]["_inspection"]))
    inspection["context"] = {"_input_manifest": {"cacheable": False}}
    unchanged, payload = prepare_cache(pool.controller, run, inspection)
    assert unchanged == run
    assert "_cache" not in payload


def test_worker_never_claims_controller_jobs_during_rollout(pool):
    identifier = _submit(pool)
    pool.settings.inspection_controller_enabled = False
    pool.worker.tick()
    assert _read(pool, identifier)["status"] == "queued"


def test_runtime_events_only_inspect_notified_jobs_and_do_not_fetch_live_logs(
    pool, monkeypatch
):
    _ready(pool)
    identifier = _submit(pool)
    pool.controller.step()
    logs = Mock(return_value="")
    monkeypatch.setattr(pool.driver, "logs", logs)
    _complete(pool, identifier)
    pool.controller.step(["unrelated"])
    assert _read(pool, identifier)["status"] == "running"
    logs.assert_not_called()
    pool.controller.step([identifier])
    assert _read(pool, identifier)["status"] == "succeeded"
    logs.assert_called_once()


@pytest.mark.parametrize(
    "result",
    [
        None,
        [],
        {},
        {"exit_code": True},
        {"exit_code": 0, "inspection_output": {}},
        {"exit_code": 1, "diagnostics": []},
    ],
)
def test_malformed_result_fails_without_crashing_controller(pool, result):
    _ready(pool)
    identifier = _submit(pool)
    pool.controller.step()
    _complete(pool, identifier)
    execution = _read(pool, identifier)["execution"]
    pool.storage.put(
        execution["result"],
        Fernet(pool.database.decrypt(execution["key"]).encode()).encrypt(
            json.dumps(result).encode()
        )
        if result is not None
        else b"invalid-encrypted-result",
    )
    pool.controller.step([identifier])
    assert _read(pool, identifier)["status"] == "failed"
    with pytest.raises(ComposerError) as error:
        pool.queue._result(identifier)
    assert error.value.diagnostics["code"] == "invalid_result"


def test_sqlite_notifications_are_only_delivered_after_commit(tmp_path):
    database = Database(
        f"sqlite:///{tmp_path / 'notifications.sqlite'}",
        "test-platform-secret-at-least-32-characters",
    )

    async def inspect():
        hub = InspectionNotifications(database)
        await hub.start()
        try:
            with hub.subscribe("opaque") as changed:
                with (
                    pytest.raises(ValueError),
                    database.transaction() as connection,
                ):
                    notify(connection, "opaque")
                    raise ValueError("rollback")
                await asyncio.sleep(0)
                assert not changed.is_set()
                with database.transaction() as connection:
                    notify(connection, "opaque")
                await asyncio.sleep(0)
                assert changed.is_set()
        finally:
            await hub.close()

    asyncio.run(inspect())
    database.engine.dispose()
