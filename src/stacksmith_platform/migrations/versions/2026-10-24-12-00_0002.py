"""Retain inactive deployment environments

Revision ID: 0002
Revises: 0001
"""

import sqlalchemy as sa
from alembic import op

revision = "0002"
down_revision = "0001"
branch_labels = None
depends_on = None


def upgrade() -> None:
    """Add active deployment-environment state.

    Returns:
        None.
    """
    with op.batch_alter_table("environments", schema=None) as batch_op:
        batch_op.add_column(
            sa.Column("active", sa.Integer(), server_default="1", nullable=False)
        )


def downgrade() -> None:
    """Remove active deployment-environment state.

    Returns:
        None.
    """
    with op.batch_alter_table("environments", schema=None) as batch_op:
        batch_op.drop_column("active")
