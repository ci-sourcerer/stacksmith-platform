"""Grant the API role permission to remove run-owned records.

Revision ID: 0006
Revises: 0005
"""

from alembic import op

revision = "0006"
down_revision = "0005"
branch_labels = None
depends_on = None


def upgrade() -> None:
    """Grant deletion permissions when the PostgreSQL API role exists.

    Returns:
        None.
    """
    if op.get_bind().dialect.name == "postgresql":
        op.execute(
            """DO $$ BEGIN
            IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'platform_api') THEN
                GRANT DELETE ON artifacts, execution_credential_sessions TO platform_api;
            END IF;
            END $$"""
        )


def downgrade() -> None:
    """Leave existing role permissions unchanged on downgrade.

    Returns:
        None.
    """
