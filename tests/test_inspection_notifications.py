import asyncio
import os
from uuid import uuid4

import pytest
from sqlalchemy import text

from stacksmith_platform.database import Database
from stacksmith_platform.inspection_notifications import InspectionNotifications, notify

pytestmark = pytest.mark.skipif(
    not os.environ.get("STACKSMITH_PLATFORM_TEST_DATABASE_URL"),
    reason="Disposable PostgreSQL URL not configured",
)


def test_postgres_notifications_commit_and_reconnect():
    asyncio.run(_committed_notifications())


async def _committed_notifications():
    database = Database(
        os.environ["STACKSMITH_PLATFORM_TEST_DATABASE_URL"],
        "test-platform-secret-at-least-32-characters",
    )
    hub = InspectionNotifications(database)
    identifier = uuid4().hex
    await hub.start()
    try:
        with hub.subscribe(identifier) as changed:
            await asyncio.wait_for(changed.wait(), 5)
            changed.clear()
            with database.engine.connect() as connection:
                transaction = connection.begin()
                notify(connection, identifier)
                with pytest.raises(TimeoutError):
                    await asyncio.wait_for(changed.wait(), 0.05)
                transaction.commit()
                await asyncio.wait_for(changed.wait(), 5)
                changed.clear()
                with connection.begin():
                    connection.execute(
                        text(
                            "SELECT pg_terminate_backend(pid) FROM pg_stat_activity WHERE application_name='stacksmith-inspections' AND datname=current_database()"
                        )
                    )
            # A disconnect or completed LISTEN wakes waiters to rescan durable state.
            await asyncio.wait_for(changed.wait(), 5)
            changed.clear()
            async with asyncio.timeout(5):
                while not hub.connected:
                    await asyncio.sleep(0.01)
            changed.clear()
            with database.transaction() as connection:
                notify(connection, identifier)
            await asyncio.wait_for(changed.wait(), 5)
    finally:
        await hub.close()
        database.engine.dispose()
