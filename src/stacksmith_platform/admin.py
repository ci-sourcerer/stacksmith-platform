import argparse

from sqlalchemy import create_engine

from .migration import database_url, upgrade


def main() -> None:
    """Upgrade the PostgreSQL schema before API/worker startup.

    Returns:
        None.

    Raises:
        ValueError: PostgreSQL is not configured or the schema is unversioned.
        sqlalchemy.exc.SQLAlchemyError: Migration or role grants fail.
        alembic.util.CommandError: Revision history is invalid.
    """
    if not database_url().startswith("postgresql+psycopg://"):
        raise ValueError("Schema initialization requires PostgreSQL")
    parser = argparse.ArgumentParser(
        description="Initialize Stacksmith Platform storage"
    )
    parser.add_argument("--grant-roles", action="store_true")
    args = parser.parse_args()
    engine = create_engine(database_url())
    upgrade(engine)
    if args.grant_roles:
        with engine.begin() as connection:
            connection.exec_driver_sql(
                "GRANT USAGE ON SCHEMA public TO platform_api, platform_worker"
            )
            connection.exec_driver_sql(
                "GRANT SELECT, INSERT, UPDATE, DELETE ON sessions, repositories, environments, role_assignments, secrets, execution_credential_bindings, tokens, runs, deployment_locks, webhooks, phase_claims, configuration TO platform_api"
            )
            connection.exec_driver_sql(
                "GRANT SELECT ON artifacts, workers, scheduler_health TO platform_api"
            )
            connection.exec_driver_sql("GRANT DELETE ON artifacts TO platform_api")
            connection.exec_driver_sql(
                "GRANT SELECT, UPDATE, DELETE ON execution_credential_sessions TO platform_api"
            )
            connection.exec_driver_sql(
                "GRANT SELECT, INSERT, DELETE ON execution_credential_sessions TO platform_worker"
            )
            connection.exec_driver_sql(
                "GRANT SELECT ON repositories, environments, role_assignments, secrets, execution_credential_bindings, phase_claims, configuration TO platform_worker"
            )
            connection.exec_driver_sql(
                "GRANT SELECT, INSERT, UPDATE, DELETE ON runs, deployment_locks, workers, artifacts, scheduler_health TO platform_worker"
            )
            connection.exec_driver_sql(
                "GRANT SELECT, INSERT, UPDATE, DELETE ON prepared_contexts TO platform_api, platform_worker"
            )
            connection.exec_driver_sql(
                "GRANT INSERT ON audits TO platform_api, platform_worker"
            )
            connection.exec_driver_sql("GRANT SELECT ON audits TO platform_api")
