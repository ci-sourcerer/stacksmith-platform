"""Replace the inspection pool with configuration invalidation notifications.

Revision ID: 0008
Revises: 0007
"""

from importlib import import_module

import sqlalchemy as sa
from alembic import op

revision = "0008"
down_revision = "0007"
branch_labels = None
depends_on = None


def upgrade() -> None:
    """Remove unused pool state and announce committed configuration changes.

    Returns:
        None.
    """
    if op.get_bind().dialect.name == "postgresql":
        op.execute("DROP TRIGGER IF EXISTS inspection_state_notification ON runs")
    op.drop_table("inspection_sandboxes")
    op.drop_table("inspection_editors")
    op.drop_table("inspection_cache")
    op.create_table(
        "prepared_contexts",
        sa.Column("id", sa.String(), primary_key=True),
        sa.Column("payload", sa.Text(), nullable=False),
        sa.Column("expires", sa.Float(), nullable=False),
    )
    op.create_index("ix_prepared_contexts_expires", "prepared_contexts", ["expires"])
    if op.get_bind().dialect.name == "postgresql":
        op.execute("DROP FUNCTION IF EXISTS inspection_state_changed()")
        op.execute("""CREATE FUNCTION platform_configuration_changed() RETURNS trigger
        LANGUAGE plpgsql AS $$ BEGIN
          IF TG_OP = 'UPDATE' AND to_jsonb(NEW) IS NOT DISTINCT FROM to_jsonb(OLD) THEN RETURN NEW; END IF;
          PERFORM pg_notify('stacksmith_configuration', '');
          RETURN NULL;
        END $$""")
        for table in (
            "configuration",
            "repositories",
            "environments",
            "role_assignments",
        ):
            op.execute(f"""CREATE TRIGGER platform_configuration_notification
            AFTER INSERT OR UPDATE OR DELETE ON {table}
            FOR EACH ROW EXECUTE FUNCTION platform_configuration_changed()""")
        op.execute("""DO $$ BEGIN
          IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname='platform_api') THEN
            GRANT SELECT, INSERT, UPDATE, DELETE ON prepared_contexts TO platform_api;
          END IF;
          IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname='platform_worker') THEN
            GRANT SELECT, INSERT, UPDATE, DELETE ON prepared_contexts TO platform_worker;
          END IF;
        END $$""")


def downgrade() -> None:
    """Restore the prior schema without resurrecting disposable pool contents.

    Returns:
        None.

    """
    op.drop_table("prepared_contexts")
    if op.get_bind().dialect.name == "postgresql":
        for table in (
            "configuration",
            "repositories",
            "environments",
            "role_assignments",
        ):
            op.execute(f"DROP TRIGGER platform_configuration_notification ON {table}")
        op.execute("DROP FUNCTION platform_configuration_changed()")
    import_module(
        "stacksmith_platform.migrations.versions.2026-10-28-20-00_0007"
    ).upgrade()
