"""Store encrypted phase credential leases independently of supervisor state.

Revision ID: 0005
Revises: 0004
"""

import sqlalchemy as sa
from alembic import op

revision = "0005"
down_revision = "0004"
branch_labels = None
depends_on = None


def upgrade() -> None:
    """Create phase credential sessions.

    Returns:
        None.
    """
    op.create_table(
        "execution_credential_sessions",
        sa.Column("id", sa.String(), primary_key=True),
        sa.Column("run", sa.String(), nullable=False),
        sa.Column("phase", sa.Integer(), nullable=False),
        sa.Column("fence", sa.Integer(), nullable=False),
        sa.Column("provider", sa.String(), nullable=False),
        sa.Column("provider_revision", sa.String(), nullable=False),
        sa.Column("token_digest", sa.String(), nullable=False),
        sa.Column("expires", sa.Float(), nullable=False),
        sa.Column("scope", sa.JSON(), nullable=False),
        sa.Column("lease", sa.Text()),
        sa.Column("redactions", sa.Text(), nullable=False),
    )
    op.create_index(
        "ix_execution_credential_sessions_run", "execution_credential_sessions", ["run"]
    )


def downgrade() -> None:
    """Remove phase credential sessions.

    Returns:
        None.
    """
    op.drop_index(
        "ix_execution_credential_sessions_run",
        table_name="execution_credential_sessions",
    )
    op.drop_table("execution_credential_sessions")
