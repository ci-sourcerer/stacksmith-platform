import asyncio
import logging
import time
from contextlib import contextmanager

import psycopg
from sqlalchemy.engine import make_url

LOGGER = logging.getLogger(__name__)
CHANNEL = "stacksmith_configuration"


class ConfigurationEvents:
    """Fan out configuration wakeups through one dedicated listener per API.

    Args:
        database_url: Direct PostgreSQL or session-pooled listener endpoint.
        contexts: Process-owned prepared editing contexts to invalidate.
    """

    def __init__(self, database_url, contexts):
        self.database_url = database_url
        self.contexts = contexts
        self.connected = False
        self._task = None
        self._waiters = set()
        self._connections = 0
        self._notifications = 0
        self._started = time.monotonic()

    async def start(self):
        """Start notification listening before serving configuration streams.

        Returns:
            None. Reconnection also invalidates cached state.
        """
        if (
            self.database_url
            and make_url(self.database_url).get_backend_name() == "postgresql"
        ):
            self._task = asyncio.create_task(self._listen())
        else:
            self.connected = True

    async def close(self):
        """Close the dedicated connection and wake subscribers during shutdown.

        Returns:
            None.
        """
        if self._task:
            self._task.cancel()
            await asyncio.gather(self._task, return_exceptions=True)
        self.connected = False
        self.changed()

    def changed(self):
        """Coalesce wakeups and discard metadata without accepting event payloads.

        Returns:
            None. Consumers fetch current authorized state.
        """
        self.contexts.invalidate()
        for waiter in self._waiters:
            waiter.set()

    @contextmanager
    def subscribe(self):
        """Subscribe before checking configuration state.

        Yields:
            An async event shared by configuration changes affecting this stream.
        """
        changed = asyncio.Event()
        self._waiters.add(changed)
        try:
            yield changed
        finally:
            self._waiters.discard(changed)

    def metrics(self):
        """Report connection and subscriber counts without database credentials.

        Returns:
            Listener connection, reconnection, and subscriber gauges.
        """
        return {
            "connected": self.connected,
            "listener_connections": int(self.connected and self._task is not None),
            "reconnects": max(0, self._connections - 1),
            "subscribers": len(self._waiters),
            "notifications": self._notifications,
            "notifications_per_second": self._notifications
            / max(1, time.monotonic() - self._started),
        }

    async def _listen(self):
        while True:
            try:
                async with await psycopg.AsyncConnection.connect(
                    make_url(self.database_url)
                    .set(drivername="postgresql")
                    .render_as_string(hide_password=False),
                    autocommit=True,
                    application_name="stacksmith-configuration-listener",
                    connect_timeout=5,
                ) as connection:
                    await connection.execute(f"LISTEN {CHANNEL}")
                    self.connected = True
                    self._connections += 1
                    # Subscribe first, then discard state potentially changed while disconnected.
                    self.changed()
                    async for _ in connection.notifies():
                        self._notifications += 1
                        self.changed()
            except psycopg.Error, OSError:
                self.connected = False
                self.changed()
                LOGGER.warning(
                    "Configuration listener disconnected; invalidating and reconnecting"
                )
                await asyncio.sleep(1)
