from concurrent.futures import ThreadPoolExecutor
from functools import partial
from threading import Event, Lock
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

from stacksmith_platform.repositories import synchronize


class _ObservedLock:
    def __init__(self):
        self.lock = Lock()
        self.guard = Lock()
        self.attempts = 0
        self.second_waiter = Event()

    def __enter__(self):
        with self.guard:
            self.attempts += 1
            if self.attempts == 2:
                self.second_waiter.set()
        self.lock.acquire()
        return self

    def __exit__(self, *args):
        self.lock.release()


def _wait_for_catalog(entered, release, statement):
    assert (
        statement
        == "SELECT pg_advisory_xact_lock(hashtext('github-repository-catalog'))"
    )
    entered.set()
    assert release.wait(timeout=5), "Catalog-lock test did not release its blocker"


def test_queued_synchronization_leaves_connections_available_for_other_requests():
    entered = Event()
    release = Event()
    lock = _ObservedLock()
    database = MagicMock()
    connection = database.transaction.return_value.__enter__.return_value
    connection.dialect.name = "postgresql"
    connection.exec_driver_sql.side_effect = partial(
        _wait_for_catalog, entered, release
    )

    with (
        patch("stacksmith_platform.repositories._CATALOG_LOCK", lock),
        ThreadPoolExecutor(max_workers=2) as executor,
    ):
        first = executor.submit(synchronize, database, [])
        try:
            assert entered.wait(timeout=5)
            second = executor.submit(synchronize, database, [])
            assert lock.second_waiter.wait(timeout=5)
            # A waiter must not check out a second connection while the first
            # synchronization is blocked on another replica's advisory lock.
            assert database.transaction.call_count == 1
            with database.transaction() as available:
                available.execute("unrelated session lookup")
            assert database.transaction.call_count == 2
        finally:
            release.set()
        first.result(timeout=5)
        second.result(timeout=5)
    assert database.transaction.call_count == 3
    assert connection.exec_driver_sql.call_count == 2


def test_failed_synchronization_releases_local_lock():
    lock = Lock()
    database = MagicMock()
    database.transaction.return_value.__enter__.return_value.dialect = SimpleNamespace(
        name="sqlite"
    )
    with (
        patch("stacksmith_platform.repositories._CATALOG_LOCK", lock),
        patch(
            "stacksmith_platform.repositories._upsert",
            side_effect=ValueError("invalid catalog"),
        ),
        pytest.raises(ValueError, match="invalid catalog"),
    ):
        synchronize(database, [{}], complete=True)
    assert lock.acquire(blocking=False)
    lock.release()
