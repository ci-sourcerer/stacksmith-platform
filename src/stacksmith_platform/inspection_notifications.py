import asyncio
import logging
from collections import defaultdict
from contextlib import contextmanager
from threading import Lock

import psycopg
from sqlalchemy import text

LOGGER = logging.getLogger(__name__)
CHANNEL = "stacksmith_inspections"
_HUBS = defaultdict(set)
_LOCK = Lock()


class _NotificationEvent(asyncio.Event):
    def __init__(self):
        super().__init__()
        self.identifiers = set()


def notify(connection, identifier):
    """Publish an opaque inspection identifier when the transaction commits.

    Args:
        connection: Transaction changing inspection state.
        identifier: Run or sandbox identifier, without user data.

    Returns:
        None.

    Raises:
        sqlalchemy.exc.SQLAlchemyError: The notification cannot be recorded.
    """
    if connection.dialect.name == "postgresql":
        connection.execute(
            text("SELECT pg_notify(:channel, :identifier)"),
            {"channel": CHANNEL, "identifier": identifier},
        )
    else:
        connection.info.setdefault("inspection_notifications", set()).add(identifier)


def publish_local(url, identifiers):
    """Deliver committed SQLite test notifications to in-process listeners.

    Args:
        url: Database URL identifying the local test bus.
        identifiers: Committed opaque identifiers.

    Returns:
        None.
    """
    with _LOCK:
        listeners = tuple(_HUBS[url])
    for listener in listeners:
        for identifier in identifiers:
            listener.wake(identifier)


def notification_hub(database):
    """Find the process-owned listener for a database.

    Args:
        database: Inspection database.

    Returns:
        A started listener, or `None` outside an application lifecycle.
    """
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        loop = None
    with _LOCK:
        return next(
            (
                hub
                for hub in _HUBS[str(database.engine.url)]
                if loop is None or hub.loop is loop
            ),
            None,
        )


class InspectionNotifications:
    """Share one database listener among lightweight inspection waiters.

    Args:
        database: Durable inspection database.
    """

    def __init__(self, database):
        self.database = database
        self.loop = None
        self.connected = False
        self._waiters = defaultdict(set)
        self._task = None

    async def start(self):
        """Register the bus and start its reconnecting PostgreSQL listener.

        Returns:
            None. Callers subscribe before checking durable state.
        """
        self.loop = asyncio.get_running_loop()
        with _LOCK:
            _HUBS[str(self.database.engine.url)].add(self)
        if self.database.engine.dialect.name == "postgresql":
            self._task = asyncio.create_task(self._listen())
        else:
            self.connected = True

    async def close(self):
        """Release subscriptions and the dedicated listener connection.

        Returns:
            None.
        """
        with _LOCK:
            _HUBS[str(self.database.engine.url)].discard(self)
        if self._task:
            self._task.cancel()
            await asyncio.gather(self._task, return_exceptions=True)
        self.connected = False

    @contextmanager
    def subscribe(self, identifier="*"):
        """Subscribe before reading the corresponding durable state.

        Args:
            identifier: Opaque identifier, or `*` for controller wakeups.

        Yields:
            An event local to the listener's asyncio loop.
        """
        event = _NotificationEvent()
        self._waiters[identifier].add(event)
        try:
            yield event
        finally:
            self._waiters[identifier].discard(event)
            if not self._waiters[identifier]:
                self._waiters.pop(identifier, None)

    def wake(self, identifier="*"):
        """Wake subscriptions safely from database or runtime event threads.

        Args:
            identifier: Changed identifier, or `*` after reconnecting.

        Returns:
            None.
        """
        if self.loop and not self.loop.is_closed():
            self.loop.call_soon_threadsafe(self._deliver, identifier)

    def _deliver(self, identifier):
        for event in tuple(self._waiters.get("*", ())):
            event.identifiers.add(identifier)
            event.set()
        if identifier == "*":
            for events in tuple(self._waiters.values()):
                for event in tuple(events):
                    event.identifiers.add(identifier)
                    event.set()
        else:
            for event in tuple(self._waiters.get(identifier, ())):
                event.identifiers.add(identifier)
                event.set()

    async def _listen(self):
        while True:
            try:
                async with await psycopg.AsyncConnection.connect(
                    self.database.engine.url.set(
                        drivername="postgresql"
                    ).render_as_string(hide_password=False),
                    autocommit=True,
                    connect_timeout=5,
                    application_name="stacksmith-inspections",
                ) as connection:
                    await connection.execute(f"LISTEN {CHANNEL}")
                    self.connected = True
                    self._deliver("*")
                    while True:
                        async for notification in connection.notifies(timeout=30):
                            self._deliver(notification.payload)
                        self._deliver("*")
            except psycopg.OperationalError, OSError:
                self.connected = False
                LOGGER.warning("Inspection notification connection lost; reconnecting")
                self._deliver("*")
                await asyncio.sleep(1)
