import os

from alembic import command
from alembic.config import Config
from dotenv import dotenv_values
from sqlalchemy import inspect
from sqlalchemy.engine import Connection, Engine


def database_url() -> str:
    """Read migration credentials without loading application configuration.

    Returns:
        The configured SQLAlchemy URL.

    Raises:
        ValueError: No database URL is configured.
    """
    if url := os.environ.get("STACKSMITH_PLATFORM_DATABASE_URL") or dotenv_values(
        ".env"
    ).get("STACKSMITH_PLATFORM_DATABASE_URL"):
        return url
    raise ValueError("Set STACKSMITH_PLATFORM_DATABASE_URL for Alembic")


def configuration() -> Config:
    """Configure migrations from installed package resources.

    Returns:
        An Alembic configuration independent of the working directory.
    """
    config = Config()
    config.set_main_option("script_location", "stacksmith_platform:migrations")
    return config


def validate_schema(connection: Connection) -> None:
    """Reject unversioned platform databases without changing their contents.

    Args:
        connection: Connection to the database being migrated.

    Returns:
        None.

    Raises:
        ValueError: Platform tables exist without Alembic revision history.
    """
    from .database import metadata

    tables = set(inspect(connection).get_table_names())
    if "alembic_version" not in tables and tables.intersection(metadata.tables):
        raise ValueError(
            "Unversioned platform database: recreate the local SQLite file or run poe reset-db for local Compose. Back up any data you need first."
        )


def upgrade(engine: Engine) -> None:
    """Apply all packaged revisions in a database transaction.

    Args:
        engine: SQLAlchemy engine for PostgreSQL or SQLite.

    Returns:
        None.

    Raises:
        ValueError: An unversioned platform schema already exists.
        sqlalchemy.exc.SQLAlchemyError: A database operation fails.
        alembic.util.CommandError: Revision history is invalid.
    """
    with engine.begin() as connection:
        config = configuration()
        config.attributes["connection"] = connection
        command.upgrade(config, "head")
