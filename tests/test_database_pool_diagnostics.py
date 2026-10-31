import json
import logging
from concurrent.futures import ThreadPoolExecutor

import pytest
from sqlalchemy.exc import TimeoutError as PoolTimeoutError

from stacksmith_platform.database import Database


def _wait_for_connection(database):
    with database.transaction():
        pytest.fail("A second connection should not be available")


def test_pool_timeout_reports_holder_and_preserves_exception(tmp_path, caplog):
    database = Database(
        f"sqlite:///{tmp_path / 'database.sqlite'}",
        "private-secret",
        pool_size=1,
        pool_timeout=0.05,
    )
    try:
        with (
            caplog.at_level(logging.WARNING),
            database.transaction(),
            ThreadPoolExecutor(max_workers=1) as executor,
            pytest.raises(PoolTimeoutError),
        ):
            executor.submit(_wait_for_connection, database).result()
        record = next(
            record
            for record in caplog.records
            if "DATABASE_POOL_TIMEOUT " in record.getMessage()
        )
        payload = json.loads(record.getMessage().split("DATABASE_POOL_TIMEOUT ")[1])
        assert payload["waiting"].endswith("._wait_for_connection")
        assert len(payload["holders"]) == 1
        assert payload["holders"][0]["owner"].endswith(
            ".test_pool_timeout_reports_holder_and_preserves_exception"
        )
        assert payload["holders"][0]["held_seconds"] >= 0.05
        assert "private-secret" not in record.getMessage()
        assert str(tmp_path) not in record.getMessage()
        assert database._active_transactions == {}
    finally:
        database.engine.dispose()


def test_transaction_tracking_clears_after_rollback(tmp_path):
    database = Database(f"sqlite:///{tmp_path / 'database.sqlite'}", "secret")
    try:
        with pytest.raises(ValueError), database.transaction():
            assert len(database._active_transactions) == 1
            raise ValueError("failure")
        assert database._active_transactions == {}
        with database.transaction():
            assert len(database._active_transactions) == 1
        assert database._active_transactions == {}
    finally:
        database.engine.dispose()
