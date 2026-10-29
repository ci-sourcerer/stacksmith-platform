from threading import Event
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from stacksmith_platform.database import Database, row, workers
from stacksmith_platform.worker import _serve
from stacksmith_platform.worker_health import healthy


@pytest.fixture
def database():
    database = Database("sqlite://", "test-secret")
    database.initialize()
    yield database
    database.engine.dispose()


@pytest.mark.parametrize(
    "age, expected", [(0, True), (119, True), (120, False), (-1, False)]
)
def test_worker_health_checks_exact_identity_and_heartbeat_age(database, age, expected):
    with database.transaction() as connection:
        connection.execute(
            workers.insert().values(
                id="worker-a", heartbeat=1000 - age, driver="docker"
            )
        )
    assert healthy(database, "worker-a", 1000) is expected
    assert not healthy(database, "worker-b", 1000)


def test_shutdown_finishes_current_tick_and_removes_heartbeat(database):
    stop = Event()
    completed = []
    with database.transaction() as connection:
        connection.execute(workers.insert().values(id="worker-a", heartbeat=1000))

    def tick(inspections):
        assert inspections is False
        stop.set()
        completed.append(True)

    _serve(SimpleNamespace(database=database, identifier="worker-a", tick=tick), stop)

    assert completed == [True]
    with database.transaction() as connection:
        assert row(connection, workers, "worker-a") is None


@pytest.mark.parametrize("interval", [0.25, 0.75, 3])
def test_inspection_polling_has_an_independent_cadence(database, monkeypatch, interval):
    now = [0.0]
    ticks = []
    inspections = []
    stop = Mock()
    stop.is_set.side_effect = lambda: now[0] >= 6

    def wait(seconds):
        assert seconds > 0
        now[0] += seconds

    stop.wait.side_effect = wait
    monkeypatch.setattr("stacksmith_platform.worker.time.monotonic", lambda: now[0])
    _serve(
        SimpleNamespace(
            database=database,
            identifier="worker-a",
            settings=SimpleNamespace(inspection_poll_interval=interval),
            tick=lambda **kwargs: ticks.append((now[0], kwargs)),
            _reconcile_inspections=lambda: inspections.append(now[0]),
        ),
        stop,
    )
    assert ticks == [(when, {"inspections": False}) for when in (0, 2, 4)]
    assert inspections == pytest.approx(
        [index * interval for index in range(int(6 / interval))]
    )


def test_slow_inspection_does_not_trigger_catch_up_polling(database, monkeypatch):
    now = [0.0]
    inspections = []
    ticks = []
    stop = Mock()
    stop.is_set.side_effect = lambda: now[0] >= 4

    def wait(seconds):
        assert seconds >= 0
        now[0] += seconds

    def inspect():
        inspections.append(now[0])
        now[0] += 3

    stop.wait.side_effect = wait
    monkeypatch.setattr("stacksmith_platform.worker.time.monotonic", lambda: now[0])
    _serve(
        SimpleNamespace(
            database=database,
            identifier="worker-a",
            settings=SimpleNamespace(inspection_poll_interval=0.25),
            tick=lambda **kwargs: ticks.append(now[0]),
            _reconcile_inspections=inspect,
        ),
        stop,
    )
    assert inspections == [0, 3.25]
    assert ticks == [0, 3]
