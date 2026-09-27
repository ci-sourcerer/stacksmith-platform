"""Index repository scope on audit events.

Revision ID: 0003
Revises: 0002
"""

import sqlalchemy as sa
from alembic import op

revision = "0003"
down_revision = "0002"
branch_labels = None
depends_on = None


def _repository(event):
    detail = event["detail"] or {}
    if detail.get("repository"):
        return detail["repository"]
    if event["run_repository"]:
        return event["run_repository"]
    if detail.get("scope") and detail["scope"] != "platform":
        return detail["scope"]
    if ":" in detail.get("environment", ""):
        return detail["environment"].split(":", 1)[0]
    return None


def upgrade() -> None:
    """Add and backfill repository scope for existing audit events.

    Returns:
        None.
    """
    op.add_column("audits", sa.Column("repository", sa.String(), nullable=True))
    op.create_index("ix_audits_repository", "audits", ["repository"])
    if op.get_context().as_sql:
        return
    audits = sa.table(
        "audits",
        sa.column("id", sa.String),
        sa.column("run", sa.String),
        sa.column("detail", sa.JSON),
        sa.column("repository", sa.String),
    )
    runs = sa.table(
        "runs", sa.column("id", sa.String), sa.column("repository", sa.String)
    )
    connection = op.get_bind()
    for event in connection.execute(
        sa.select(
            audits.c.id,
            audits.c.detail,
            runs.c.repository.label("run_repository"),
        ).select_from(audits.outerjoin(runs, audits.c.run == runs.c.id))
    ).mappings():
        connection.execute(
            audits.update()
            .where(audits.c.id == event["id"])
            .values(repository=_repository(event))
        )


def downgrade() -> None:
    """Remove the audit repository index and column.

    Returns:
        None.
    """
    op.drop_index("ix_audits_repository", table_name="audits")
    op.drop_column("audits", "repository")
