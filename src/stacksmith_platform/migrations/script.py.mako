"""${message}

Revision ID: ${up_revision}
Revises: ${down_revision | comma,n}
"""
from alembic import op
import sqlalchemy as sa
${imports if imports else ""}

revision = ${repr(up_revision)}
down_revision = ${repr(down_revision)}
branch_labels = ${repr(branch_labels)}
depends_on = ${repr(depends_on)}


def upgrade() -> None:
    """Apply this schema revision.

    Returns:
        None.
    """
    ${upgrades if upgrades else "pass"}


def downgrade() -> None:
    """Reverse this schema revision.

    Returns:
        None.
    """
    ${downgrades if downgrades else "pass"}
