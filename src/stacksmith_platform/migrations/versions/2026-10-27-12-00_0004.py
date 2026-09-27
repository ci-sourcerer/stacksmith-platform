"""Add phase-scoped execution credential provider bindings.

Revision ID: 0004
Revises: 0003
"""

import sqlalchemy as sa
from alembic import op

revision = "0004"
down_revision = "0003"
branch_labels = None
depends_on = None


def upgrade() -> None:
    """Create environment-scoped provider bindings.

    Returns:
        None.
    """
    op.create_table(
        "execution_credential_bindings",
        sa.Column("id", sa.String(), primary_key=True),
        sa.Column("environment", sa.String(), nullable=False),
        sa.Column("provider", sa.String(), nullable=False),
        sa.Column("phases", sa.JSON(), nullable=False),
    )
    op.create_index(
        "ix_execution_credential_bindings_environment",
        "execution_credential_bindings",
        ["environment"],
    )


def downgrade() -> None:
    """Remove environment-scoped provider bindings.

    Returns:
        None.
    """
    op.drop_index(
        "ix_execution_credential_bindings_environment",
        table_name="execution_credential_bindings",
    )
    op.drop_table("execution_credential_bindings")
