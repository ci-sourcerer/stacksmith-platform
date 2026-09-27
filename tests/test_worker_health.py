from threading import Event
from types import SimpleNamespace

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

    def tick():
        stop.set()
        completed.append(True)

    _serve(SimpleNamespace(database=database, identifier="worker-a", tick=tick), stop)

    assert completed == [True]
    with database.transaction() as connection:
        assert row(connection, workers, "worker-a") is None
