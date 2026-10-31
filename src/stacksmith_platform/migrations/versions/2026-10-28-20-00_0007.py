"""Add durable inspection controllers, sandbox assignments, and cache metadata.

Revision ID: 0007
Revises: 0006
"""

import sqlalchemy as sa
from alembic import op

revision = "0007"
down_revision = "0006"
branch_labels = None
depends_on = None


def upgrade() -> None:
    """Create inspection coordination tables and committed-state notifications.

    Returns:
        None.
    """
    op.create_table(
        "inspection_editors",
        sa.Column("id", sa.String(), primary_key=True),
        sa.Column("generation", sa.Integer(), nullable=False),
        sa.Column("updated", sa.Float(), nullable=False),
    )
    op.create_table(
        "inspection_sandboxes",
        sa.Column("id", sa.String(), primary_key=True),
        sa.Column("image", sa.String(), nullable=False),
        sa.Column("status", sa.String(), nullable=False),
        sa.Column("token_digest", sa.String(), nullable=False),
        sa.Column("token", sa.Text(), nullable=False),
        sa.Column("created", sa.Float(), nullable=False),
        sa.Column("expires", sa.Float(), nullable=False),
        sa.Column("owner", sa.String()),
        sa.Column("lease", sa.Float()),
        sa.Column("run", sa.String(), unique=True),
        sa.Column("assignment", sa.Text()),
    )
    op.create_index(
        "ix_inspection_sandboxes_status", "inspection_sandboxes", ["status"]
    )
    op.create_table(
        "inspection_cache",
        sa.Column("id", sa.String(), primary_key=True),
        sa.Column("key", sa.String(), nullable=False),
        sa.Column("encryption_key", sa.Text(), nullable=False),
        sa.Column("expires", sa.Float(), nullable=False),
    )
    op.create_index("ix_inspection_cache_expires", "inspection_cache", ["expires"])
    if op.get_bind().dialect.name == "postgresql":
        _postgres_notifications()


def _postgres_notifications():
    op.execute("""CREATE FUNCTION inspection_state_changed() RETURNS trigger
    LANGUAGE plpgsql AS $$ BEGIN
      IF TG_TABLE_NAME = 'runs' THEN
        IF NEW.action <> 'inspection' THEN RETURN NEW; END IF;
      END IF;
      IF TG_OP = 'UPDATE' AND NEW.status IS NOT DISTINCT FROM OLD.status THEN RETURN NEW; END IF;
      PERFORM pg_notify('stacksmith_inspections', NEW.id);
      RETURN NEW;
    END $$""")
    for table in ("runs", "inspection_sandboxes"):
        op.execute(f"""CREATE TRIGGER inspection_state_notification
        AFTER INSERT OR UPDATE ON {table}
        FOR EACH ROW EXECUTE FUNCTION inspection_state_changed()""")
    op.execute("""DO $$ BEGIN
    IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'platform_api') THEN
      GRANT SELECT, INSERT, UPDATE, DELETE ON inspection_editors, inspection_sandboxes, inspection_cache TO platform_api;
    END IF;
    IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'platform_worker') THEN
      GRANT SELECT, INSERT, UPDATE, DELETE ON inspection_editors, inspection_sandboxes, inspection_cache TO platform_worker;
    END IF;
    END $$""")


def downgrade() -> None:
    """Remove inspection coordination after controllers have been drained.

    Returns:
        None.
    """
    if op.get_bind().dialect.name == "postgresql":
        for table in ("runs", "inspection_sandboxes"):
            op.execute(f"DROP TRIGGER inspection_state_notification ON {table}")
        op.execute("DROP FUNCTION inspection_state_changed()")
    for table in ("inspection_cache", "inspection_sandboxes", "inspection_editors"):
        op.drop_table(table)
