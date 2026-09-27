"""Check the local persistent worker's database heartbeat."""

import socket
import sys
import time

from .database import Database, row, workers
from .settings import Settings


def healthy(database: Database, identifier: str, now: float) -> bool:
    """Check whether this worker has a recent heartbeat.

    Args:
        database: Platform database containing worker heartbeats.
        identifier: Exact worker identity to check.
        now: Current Unix timestamp.

    Returns:
        Whether the worker heartbeated within the last 120 seconds.

    Raises:
        SQLAlchemyError: The database cannot be queried.
    """
    with database.transaction() as connection:
        worker = row(connection, workers, identifier)
    return bool(worker and 0 <= now - worker["heartbeat"] < 120)


def main() -> None:
    """Exit successfully only when this container's worker is healthy.

    Returns:
        None.

    Raises:
        SystemExit: The worker heartbeat is missing or stale.
        SQLAlchemyError: The database cannot be queried.
        ValidationError: Application settings are invalid.
    """
    settings = Settings()
    sys.exit(
        0
        if healthy(
            Database(settings.database_url, settings.secret_key),
            settings.worker_id or socket.gethostname(),
            time.time(),
        )
        else 1
    )


if __name__ == "__main__":
    main()
