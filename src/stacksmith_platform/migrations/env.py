from alembic import context
from sqlalchemy import create_engine

from stacksmith_platform.database import metadata
from stacksmith_platform.migration import database_url, validate_schema


def _run(connection):
    validate_schema(connection)
    context.configure(
        connection=connection,
        target_metadata=metadata,
        render_as_batch=connection.dialect.name == "sqlite",
        compare_server_default=True,
    )
    with context.begin_transaction():
        context.run_migrations()


if context.is_offline_mode():
    context.configure(url=database_url(), target_metadata=metadata, literal_binds=True)
    with context.begin_transaction():
        context.run_migrations()
elif context.config.attributes.get("connection") is not None:
    _run(context.config.attributes["connection"])
else:
    with create_engine(database_url()).connect() as connection:
        _run(connection)
        connection.commit()
