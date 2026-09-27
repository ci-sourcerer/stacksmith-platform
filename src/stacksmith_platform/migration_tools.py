import argparse

from alembic import command
from alembic.config import Config
from alembic.runtime.migration import MigrationContext
from alembic.script import ScriptDirectory
from sqlalchemy import create_engine

from .migration import database_url, validate_schema


def main() -> None:
    """Generate or check migrations using the mounted development checkout.

    Returns:
        None.

    Raises:
        ValueError: The database is not at the checked-out revision head.
        alembic.util.CommandError: Migration generation or drift checking fails.
        sqlalchemy.exc.SQLAlchemyError: The database operation fails.
    """
    parser = argparse.ArgumentParser(description="Author and check database migrations")
    subcommands = parser.add_subparsers(dest="command", required=True)
    revision = subcommands.add_parser("revision")
    revision.add_argument("--message", required=True)
    revision.add_argument("--empty", action="store_true")
    subcommands.add_parser("check")
    args = parser.parse_args()
    config = Config("alembic.ini")
    with create_engine(database_url()).connect() as connection:
        validate_schema(connection)
        if set(MigrationContext.configure(connection).get_current_heads()) != set(
            ScriptDirectory.from_config(config).get_heads()
        ):
            raise ValueError(
                "Database is not at the checked-out head; run poe run-db-migrations first"
            )
        config.attributes["connection"] = connection
        if args.command == "check":
            command.check(config)
        else:
            command.revision(config, message=args.message, autogenerate=not args.empty)


if __name__ == "__main__":
    main()
