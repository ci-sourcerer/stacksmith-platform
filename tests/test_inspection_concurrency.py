import json
import time
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier
from types import SimpleNamespace

import pytest
from cryptography.fernet import Fernet
from test_control import _create, _read
from test_control import platform as _platform_fixture

from stacksmith_platform.database import runs
from stacksmith_platform.errors import ComposerError
from stacksmith_platform.inspection import InspectionQueue
from stacksmith_platform.worker import Worker


@pytest.fixture(name="platform")
def _inspection_platform(tmp_path, monkeypatch):
    yield from _platform_fixture.__wrapped__(tmp_path, monkeypatch)


def _enqueue(platform, identifier, **changes):
    with platform.database.transaction() as connection:
        connection.execute(
            runs.insert().values(
                **{
                    "id": identifier,
                    "repository": "inspection",
                    "environment": "inspection",
                    "revision": "0" * 40,
                    "action": "inspection",
                    "actor": "authoring",
                    "status": "queued",
                    "phase": 0,
                    "spec": {
                        "image": platform.settings.execution_image,
                        "phases": ["inspect"],
                        "_inspection": platform.database.encrypt(
                            json.dumps({"github_token": f"secret-{identifier}"})
                        ),
                    },
                    "result": {},
                    "created": time.time(),
                    "updated": time.time(),
                    "expires": time.time() + 30,
                    "dedup": identifier,
                    "lock_key": "",
                    "fence": 0,
                    **changes,
                }
            )
        )


def _set_run(platform, identifier, **changes):
    with platform.database.transaction() as connection:
        connection.execute(
            runs.update().where(runs.c.id == identifier).values(**changes)
        )


def _complete(platform, identifier, exit_code=0, diagnostics=None):
    run = _read(platform, identifier)
    platform.storage.put(
        run["execution"]["result"],
        Fernet(platform.database.decrypt(run["execution"]["key"]).encode()).encrypt(
            json.dumps(
                {
                    "exit_code": exit_code,
                    "inspection_output": f"result-{identifier}",
                    **({"diagnostics": diagnostics} if diagnostics else {}),
                }
            ).encode()
        ),
    )
    platform.driver.executions[run["execution"]["name"]]["state"] = (
        "failed" if exit_code else "succeeded"
    )


def _replacement(platform):
    return Worker(
        platform.settings,
        platform.database,
        platform.driver,
        platform.storage,
        platform.app.state.provider,
    )


def test_inspections_overlap_and_complete_out_of_order(platform):
    for identifier in "abcde":
        _enqueue(platform, identifier)
    platform.worker.tick()
    assert platform.driver.launches == 4
    assert _read(platform, "e")["status"] == "queued"
    platform.worker.tick()
    assert platform.driver.launches == 4
    _complete(platform, "c")
    platform.worker.tick()
    assert platform.driver.launches == 5
    assert _read(platform, "a")["status"] == "running"
    assert _read(platform, "e")["status"] == "running"
    for identifier in "edba":
        _complete(platform, identifier)
    platform.worker.tick()
    for identifier in "abcde":
        run = _read(platform, identifier)
        assert run["status"] == "succeeded"
        assert platform.database.decrypt(run["result"]["_inspection_output"]) == (
            f"result-{identifier}"
        )
        assert f"result-{identifier}" not in json.dumps(run["result"])
    assert not platform.driver.executions


def test_regular_tick_can_leave_inspections_for_the_separate_poll(platform):
    _enqueue(platform, "inspection")
    platform.worker.tick(inspections=False)
    assert _read(platform, "inspection")["status"] == "queued"
    assert platform.driver.launches == 0
    platform.worker._reconcile_inspections()
    assert _read(platform, "inspection")["status"] == "running"
    assert platform.driver.launches == 1


@pytest.mark.parametrize("inspection_first", [False, True])
def test_inspections_and_ordinary_runs_both_progress(platform, inspection_first):
    if inspection_first:
        _enqueue(platform, "inspection")
    identifier = _create(platform, action="plan").json()["id"]
    if not inspection_first:
        _enqueue(platform, "inspection")
    platform.worker.tick()
    assert _read(platform, identifier)["status"] == "running"
    assert _read(platform, "inspection")["status"] == "running"
    _enqueue(platform, "new-inspection")
    platform.worker.tick()
    assert _read(platform, "new-inspection")["status"] == "running"


@pytest.mark.parametrize("outcome", ["failed", "cancelled"])
def test_terminal_inspections_release_capacity(platform, outcome):
    platform.settings.inspection_concurrency = 1
    _enqueue(platform, "first")
    _enqueue(platform, "next")
    platform.worker.tick()
    if outcome == "failed":
        _complete(platform, "first", exit_code=1)
    else:
        _set_run(platform, "first", status="cancelling")
    platform.worker.tick()
    assert _read(platform, "first")["status"] == outcome
    assert _read(platform, "next")["status"] == "running"
    assert len(platform.driver.executions) == 1


def test_expired_and_cancelled_queued_inspections_are_not_launched(platform):
    _enqueue(platform, "expired", expires=time.time() - 1)
    _enqueue(platform, "cancelled", status="cancelling")
    _enqueue(platform, "next")
    platform.worker.tick()
    assert _read(platform, "expired")["status"] == "expired"
    assert _read(platform, "cancelled")["status"] == "cancelled"
    assert platform.driver.launches == 1


def test_restart_with_lower_limit_supervises_all_owned_inspections(platform):
    for identifier in "abcde":
        _enqueue(platform, identifier)
    platform.worker.tick()
    platform.settings.inspection_concurrency = 1
    replacement = _replacement(platform)
    replacement.identifier = platform.worker.identifier
    replacement.tick()
    assert platform.driver.launches == 4
    for identifier in "abcd":
        _complete(platform, identifier)
    replacement.tick()
    assert all(_read(platform, key)["status"] == "succeeded" for key in "abcd")
    assert _read(platform, "e")["status"] == "running"
    assert platform.driver.launches == 5


def test_abandoned_inspection_is_recovered_before_new_work(platform):
    platform.settings.inspection_concurrency = 1
    _enqueue(platform, "abandoned")
    platform.worker.tick()
    _enqueue(platform, "next")
    _set_run(platform, "abandoned", lease=time.time() - 1)
    replacement = _replacement(platform)
    replacement.tick()
    assert _read(platform, "abandoned")["worker"] == replacement.identifier
    assert _read(platform, "next")["status"] == "queued"
    assert platform.driver.launches == 1


def test_owned_queued_inspection_resumes_after_interrupted_start(platform):
    _enqueue(platform, "pending", worker=platform.worker.identifier)
    platform.worker.tick()
    assert _read(platform, "pending")["status"] == "running"


def _tick_together(worker, barrier):
    barrier.wait(timeout=5)
    worker.tick()


def test_workers_do_not_launch_the_same_inspection(platform):
    for identifier in "abcdefgh":
        _enqueue(platform, identifier)
    replacement = _replacement(platform)
    barrier = Barrier(2)
    with ThreadPoolExecutor(max_workers=2) as executor:
        futures = [
            executor.submit(_tick_together, worker, barrier)
            for worker in [platform.worker, replacement]
        ]
        for future in futures:
            future.result(timeout=10)
    platform.worker.tick()
    replacement.tick()
    assert platform.driver.launches == 8
    assert len(platform.driver.executions) == 8
    for worker in [platform.worker, replacement]:
        assert (
            sum(
                _read(platform, identifier)["worker"] == worker.identifier
                for identifier in "abcdefgh"
            )
            == 4
        )


def test_compose_does_not_claim_kubernetes_inspections(platform):
    _enqueue(platform, "kubernetes", supervision={"generation": 1})
    platform.worker.tick()
    assert _read(platform, "kubernetes")["status"] == "queued"
    assert platform.driver.launches == 0


def test_failure_diagnostics_survive_queue_and_remain_private(platform, monkeypatch):
    platform.settings.database_url = platform.database.engine.url.render_as_string(
        hide_password=False
    )
    monkeypatch.setattr(
        "stacksmith_platform.inspection.uuid4",
        lambda: SimpleNamespace(hex="failed-inspection"),
    )
    queue = InspectionQueue(platform.settings)
    with ThreadPoolExecutor(max_workers=1) as executor:
        future = executor.submit(
            queue.execute, ["info", "associations"], None, "credential-value", None
        )
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline:
            with platform.database.transaction() as connection:
                found = connection.execute(
                    runs.select().where(runs.c.id == "failed-inspection")
                ).first()
            if found:
                break
            time.sleep(0.01)
        assert found
        platform.worker.tick()
        _complete(
            platform,
            "failed-inspection",
            1,
            {
                "stage": "info associations",
                "code": "timeout",
                "status": 504,
                "exit_code": 1,
                "message": "Inspection timed out.",
                "stderr": "Missing input credential-value",
                "stdout": "partial result",
            },
        )
        platform.worker.tick()
        with pytest.raises(ComposerError) as error:
            future.result(timeout=5)
        assert error.value.status == 504
        assert error.value.diagnostics["stderr"] == "Missing input <redacted>"
        stored = _read(platform, "failed-inspection")["result"]
        assert "Missing input" not in json.dumps(stored)
        assert "partial result" not in json.dumps(stored)
    queue.database.engine.dispose()
