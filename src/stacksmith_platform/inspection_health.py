import json
import sys
import time

from sqlalchemy import func

from .database import (
    Database,
    configuration,
    inspection_cache,
    inspection_sandboxes,
    row,
    runs,
    scheduler_health,
)
from .inspection_notifications import notify
from .inspection_results import _inspection_failure
from .settings import Settings


def register(database, settings):
    """Publish the API deployment's desired sandbox image for safe rollouts.

    Args:
        database: Shared coordination database.
        settings: Current API deployment configuration.

    Returns:
        None. Older controllers finish active work but stop replenishing.

    Raises:
        sqlalchemy.exc.SQLAlchemyError: The desired pool cannot be committed.
    """
    from .inspection_controller import _pool_lock

    with database.transaction() as connection:
        _pool_lock(connection)
        if row(connection, configuration, "inspection:pool"):
            connection.execute(
                configuration.update()
                .where(configuration.c.id == "inspection:pool")
                .values(policy={"image": settings.execution_image}, updated=time.time())
            )
        else:
            connection.execute(
                configuration.insert().values(
                    id="inspection:pool",
                    policy={"image": settings.execution_image},
                    updated=time.time(),
                )
            )
        connection.execute(
            runs.update()
            .where(
                runs.c.action == "inspection",
                runs.c.status == "queued",
                runs.c.spec["inspection_controller"].as_boolean().is_(True),
                runs.c.spec["image"].as_string() != settings.execution_image,
            )
            .values(
                status="cancelled",
                updated=time.time(),
                result={
                    "_inspection_result": database.encrypt(
                        json.dumps(
                            _inspection_failure(
                                "inspection queue",
                                "The inspection image changed. Refresh and retry.",
                                "inspection_configuration_changed",
                                409,
                            )
                        )
                    )
                },
            )
        )
        notify(connection, "*")


def status(database, settings):
    """Read pool readiness and non-sensitive operational gauges.

    Args:
        database: Durable inspection database.
        settings: Expected pool limits.

    Returns:
        Controller health and aggregate pool and queue counts.

    Raises:
        sqlalchemy.exc.SQLAlchemyError: Health cannot be read.
    """
    with database.transaction() as connection:
        states = dict(
            connection.execute(
                inspection_sandboxes.select()
                .with_only_columns(inspection_sandboxes.c.status, func.count())
                .group_by(inspection_sandboxes.c.status)
            ).all()
        )
        heartbeat = connection.execute(
            scheduler_health.select()
            .with_only_columns(func.max(scheduler_health.c.completed))
            .where(
                scheduler_health.c.id.like("inspection-%"),
                scheduler_health.c.error.is_(None),
            )
        ).scalar_one()
        pending = connection.execute(
            runs.select()
            .with_only_columns(func.count())
            .where(
                runs.c.action == "inspection",
                runs.c.status == "queued",
                runs.c.spec["inspection_controller"].as_boolean().is_(True),
            )
        ).scalar_one()
        superseded = connection.execute(
            runs.select()
            .with_only_columns(func.count())
            .where(
                runs.c.action == "inspection",
                runs.c.result["superseded"].as_boolean().is_(True),
            )
        ).scalar_one()
        caches = connection.execute(
            inspection_cache.select()
            .with_only_columns(func.count())
            .where(inspection_cache.c.expires > time.time())
        ).scalar_one()
    return {
        "healthy": bool(heartbeat and time.time() - heartbeat < 90),
        "ready": states.get("ready", 0),
        "launching": states.get("launching", 0),
        "retiring": states.get("retired", 0),
        "active": sum(
            states.get(state, 0) for state in ("reserved", "assigned", "delivered")
        ),
        "pending": pending,
        "superseded": superseded,
        "cache_entries": caches,
        "limits": {
            "active": settings.inspection_pool_active,
            "total": settings.inspection_pool_total,
            "ready": settings.inspection_pool_ready,
        },
    }


def main() -> None:
    """Check the optional controller without starting a runtime.

    Returns:
        None. Exits unsuccessfully when the controller heartbeat is stale.

    Raises:
        sqlalchemy.exc.SQLAlchemyError: The configured database cannot be read.
    """
    settings = Settings()
    database = Database(settings.database_url, settings.secret_key)
    try:
        sys.exit(0 if status(database, settings)["healthy"] else 1)
    finally:
        database.engine.dispose()


if __name__ == "__main__":
    main()
