import asyncio
import os
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from stacksmith_platform.app import _configuration_stream
from stacksmith_platform.configuration_events import ConfigurationEvents
from stacksmith_platform.database import Database, configuration
from stacksmith_platform.editing_contexts import EditingContexts


async def _fanout():
    contexts = EditingContexts()
    events = ConfigurationEvents("", contexts)
    await events.start()
    with events.subscribe() as first, events.subscribe() as second:
        events.changed()
        assert first.is_set() and second.is_set()
        first.clear()
        for _ in range(100):
            events.changed()
        assert first.is_set()
        assert events.metrics()["subscribers"] == 2
    assert events.metrics()["subscribers"] == 0
    await events.close()


def test_changes_fan_out_without_per_editor_listeners():
    asyncio.run(_fanout())


async def _stream_lifecycle():
    events = ConfigurationEvents("", EditingContexts())
    request = SimpleNamespace(
        app=SimpleNamespace(state=SimpleNamespace(configuration_events=events)),
        is_disconnected=AsyncMock(return_value=False),
    )
    stream = _configuration_stream(request)
    assert await anext(stream) == "event: configuration\ndata: {}\n\n"
    events.changed()
    assert await anext(stream) == "event: configuration\ndata: {}\n\n"
    await stream.aclose()
    assert events.metrics()["subscribers"] == 0


def test_stream_reconnect_revalidates_and_disconnect_removes_subscription():
    asyncio.run(_stream_lifecycle())


def test_snapshot_refresh_retains_original_source_and_remote_revision(
    client, monkeypatch
):
    original = client.get("/api/sample").json()
    monkeypatch.setattr(
        "stacksmith_platform.app._config_revision", lambda *args: "new-settings"
    )
    response = client.post(
        "/api/refresh-snapshot", json={"snapshot": original["snapshot"]}
    )
    assert response.status_code == 200
    assert response.json()["source"] == original["source"]
    assert response.json()["snapshot"] != original["snapshot"]
    assert response.json()["document"] == original["document"]
    assert (
        client.post("/api/refresh-snapshot", json={"snapshot": "tampered"}).status_code
        == 409
    )


async def _postgres_notifications():
    database = Database(
        os.environ["STACKSMITH_PLATFORM_TEST_DATABASE_URL"],
        "test-configuration-notifications",
    )
    database.initialize()
    events = ConfigurationEvents(
        os.environ["STACKSMITH_PLATFORM_TEST_DATABASE_URL"], EditingContexts()
    )
    await events.start()
    try:
        async with asyncio.timeout(5):
            while not events.connected:
                await asyncio.sleep(0.01)
        with events.subscribe() as changed:
            with (
                pytest.raises(RuntimeError, match="rollback"),
                database.transaction() as connection,
            ):
                connection.execute(
                    configuration.insert().values(
                        id="notify-rollback", policy={"refs": []}
                    )
                )
                raise RuntimeError("rollback")
            with pytest.raises(TimeoutError):
                await asyncio.wait_for(changed.wait(), 0.1)
            with database.transaction() as connection:
                connection.execute(
                    configuration.insert().values(
                        id="notify-commit", policy={"refs": []}
                    )
                )
            await asyncio.wait_for(changed.wait(), 5)
            changed.clear()
            with database.transaction() as connection:
                connection.execute(
                    configuration.update()
                    .where(configuration.c.id == "notify-commit")
                    .values(policy={"refs": []})
                )
            with pytest.raises(TimeoutError):
                await asyncio.wait_for(changed.wait(), 0.1)
            assert events.metrics()["listener_connections"] == 1
    finally:
        await events.close()
        with database.transaction() as connection:
            connection.execute(
                configuration.delete().where(
                    configuration.c.id.in_(["notify-rollback", "notify-commit"])
                )
            )
        database.engine.dispose()


@pytest.mark.skipif(
    not os.environ.get("STACKSMITH_PLATFORM_TEST_DATABASE_URL"),
    reason="Disposable PostgreSQL is not configured",
)
def test_postgres_configuration_signals_follow_commit_and_ignore_unchanged_rows():
    asyncio.run(_postgres_notifications())


def test_source_remains_available_when_configuration_inspection_is_unavailable(
    client, monkeypatch
):
    from unittest.mock import Mock

    from stacksmith_platform.errors import ComposerError

    original = client.get("/api/sample").json()
    monkeypatch.setattr(
        "stacksmith_platform.app._config_revision",
        Mock(side_effect=ComposerError("Configuration unavailable", 503)),
    )
    response = client.post(
        "/api/source",
        json={
            "snapshot": original["snapshot"],
            "components": original["document"]["components"],
        },
    )
    assert response.status_code == 200
    assert response.json()["source"] == original["source"]
