"""Initial platform schema

Revision ID: 0001
Revises:
"""

import sqlalchemy as sa
from alembic import op

revision = "0001"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    """Apply this schema revision.

    Returns:
        None.
    """
    op.create_table(
        "artifacts",
        sa.Column("id", sa.String(), nullable=False),
        sa.Column("run", sa.String(), nullable=True),
        sa.Column("name", sa.String(), nullable=True),
        sa.Column("key", sa.String(), nullable=True),
        sa.Column("digest", sa.String(), nullable=True),
        sa.Column("private", sa.Integer(), nullable=True),
        sa.Column("expires", sa.Float(), nullable=True),
        sa.PrimaryKeyConstraint("id"),
    )
    with op.batch_alter_table("artifacts", schema=None) as batch_op:
        batch_op.create_index(batch_op.f("ix_artifacts_run"), ["run"], unique=False)

    op.create_table(
        "audits",
        sa.Column("id", sa.String(), nullable=False),
        sa.Column("actor", sa.String(), nullable=True),
        sa.Column("action", sa.String(), nullable=True),
        sa.Column("run", sa.String(), nullable=True),
        sa.Column("detail", sa.JSON(), nullable=True),
        sa.Column("created", sa.Float(), nullable=True),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_table(
        "configuration",
        sa.Column("id", sa.String(), nullable=False),
        sa.Column("policy", sa.JSON(), nullable=True),
        sa.Column("updated", sa.Float(), nullable=True),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_table(
        "deployment_locks",
        sa.Column("id", sa.String(), nullable=False),
        sa.Column("run", sa.String(), nullable=True),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("run"),
    )
    op.create_table(
        "environments",
        sa.Column("id", sa.String(), nullable=False),
        sa.Column("repository", sa.String(), nullable=True),
        sa.Column("name", sa.String(), nullable=True),
        sa.Column("settings", sa.JSON(), nullable=True),
        sa.Column("revision", sa.Integer(), nullable=True),
        sa.PrimaryKeyConstraint("id"),
    )
    with op.batch_alter_table("environments", schema=None) as batch_op:
        batch_op.create_index(
            batch_op.f("ix_environments_repository"), ["repository"], unique=False
        )

    op.create_table(
        "phase_claims",
        sa.Column("id", sa.String(), nullable=False),
        sa.Column("run", sa.String(), nullable=True),
        sa.Column("fence", sa.Integer(), nullable=True),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_table(
        "repositories",
        sa.Column("id", sa.String(), nullable=False),
        sa.Column("github_id", sa.String(), nullable=True),
        sa.Column("installation", sa.Integer(), nullable=True),
        sa.Column("branch", sa.String(), nullable=True),
        sa.Column("config", sa.Text(), nullable=True),
        sa.Column("enabled", sa.Integer(), nullable=True),
        sa.Column(
            "deployment_enabled", sa.Integer(), server_default="1", nullable=True
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    with op.batch_alter_table("repositories", schema=None) as batch_op:
        batch_op.create_index("repositories_github_id", ["github_id"], unique=True)

    op.create_table(
        "role_assignments",
        sa.Column("id", sa.String(), nullable=False),
        sa.Column("login", sa.String(), nullable=True),
        sa.Column("repository", sa.String(), nullable=True),
        sa.Column("environment", sa.String(), nullable=True),
        sa.Column("role", sa.String(), nullable=True),
        sa.Column("workloads", sa.JSON(), nullable=True),
        sa.PrimaryKeyConstraint("id"),
    )
    with op.batch_alter_table("role_assignments", schema=None) as batch_op:
        batch_op.create_index(
            batch_op.f("ix_role_assignments_login"), ["login"], unique=False
        )

    op.create_table(
        "runs",
        sa.Column("id", sa.String(), nullable=False),
        sa.Column("repository", sa.String(), nullable=True),
        sa.Column("environment", sa.String(), nullable=True),
        sa.Column("revision", sa.String(), nullable=True),
        sa.Column("action", sa.String(), nullable=True),
        sa.Column("actor", sa.String(), nullable=True),
        sa.Column("status", sa.String(), nullable=True),
        sa.Column("phase", sa.Integer(), nullable=True),
        sa.Column("spec", sa.JSON(), nullable=True),
        sa.Column("result", sa.JSON(), nullable=True),
        sa.Column("created", sa.Float(), nullable=True),
        sa.Column("updated", sa.Float(), nullable=True),
        sa.Column("expires", sa.Float(), nullable=True),
        sa.Column("dedup", sa.String(), nullable=True),
        sa.Column("lock_key", sa.String(), nullable=True),
        sa.Column("fence", sa.Integer(), nullable=True),
        sa.Column("worker", sa.String(), nullable=True),
        sa.Column("lease", sa.Float(), nullable=True),
        sa.Column("execution", sa.JSON(), nullable=True),
        sa.Column("supervision", sa.JSON(none_as_null=True), nullable=True),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("dedup"),
    )
    with op.batch_alter_table("runs", schema=None) as batch_op:
        batch_op.create_index(
            batch_op.f("ix_runs_repository"), ["repository"], unique=False
        )
        batch_op.create_index(batch_op.f("ix_runs_status"), ["status"], unique=False)

    op.create_table(
        "scheduler_health",
        sa.Column("id", sa.String(), nullable=False),
        sa.Column("completed", sa.Float(), nullable=True),
        sa.Column("error", sa.Text(), nullable=True),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_table(
        "secrets",
        sa.Column("id", sa.String(), nullable=False),
        sa.Column("environment", sa.String(), nullable=True),
        sa.Column("name", sa.String(), nullable=True),
        sa.Column("phases", sa.JSON(), nullable=True),
        sa.Column("ciphertext", sa.Text(), nullable=True),
        sa.Column("reference", sa.String(), nullable=True),
        sa.Column("json_key", sa.String(), nullable=True),
        sa.PrimaryKeyConstraint("id"),
    )
    with op.batch_alter_table("secrets", schema=None) as batch_op:
        batch_op.create_index(
            batch_op.f("ix_secrets_environment"), ["environment"], unique=False
        )

    op.create_table(
        "sessions",
        sa.Column("id", sa.String(), nullable=False),
        sa.Column("login", sa.String(), nullable=True),
        sa.Column("token", sa.Text(), nullable=True),
        sa.Column("expires", sa.Float(), nullable=True),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_table(
        "tokens",
        sa.Column("id", sa.String(), nullable=False),
        sa.Column("login", sa.String(), nullable=True),
        sa.Column("name", sa.String(), nullable=True),
        sa.Column("scope", sa.JSON(), nullable=True),
        sa.Column("expires", sa.Float(), nullable=True),
        sa.Column("revoked", sa.Integer(), nullable=True),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_table(
        "webhooks",
        sa.Column("id", sa.String(), nullable=False),
        sa.Column("created", sa.Float(), nullable=True),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_table(
        "workers",
        sa.Column("id", sa.String(), nullable=False),
        sa.Column("heartbeat", sa.Float(), nullable=True),
        sa.Column("driver", sa.String(), nullable=True),
        sa.PrimaryKeyConstraint("id"),
    )


def downgrade() -> None:
    """Reverse this schema revision.

    Returns:
        None.
    """
    op.drop_table("workers")
    op.drop_table("webhooks")
    op.drop_table("tokens")
    op.drop_table("sessions")
    with op.batch_alter_table("secrets", schema=None) as batch_op:
        batch_op.drop_index(batch_op.f("ix_secrets_environment"))

    op.drop_table("secrets")
    op.drop_table("scheduler_health")
    with op.batch_alter_table("runs", schema=None) as batch_op:
        batch_op.drop_index(batch_op.f("ix_runs_status"))
        batch_op.drop_index(batch_op.f("ix_runs_repository"))

    op.drop_table("runs")
    with op.batch_alter_table("role_assignments", schema=None) as batch_op:
        batch_op.drop_index(batch_op.f("ix_role_assignments_login"))

    op.drop_table("role_assignments")
    with op.batch_alter_table("repositories", schema=None) as batch_op:
        batch_op.drop_index("repositories_github_id")

    op.drop_table("repositories")
    op.drop_table("phase_claims")
    with op.batch_alter_table("environments", schema=None) as batch_op:
        batch_op.drop_index(batch_op.f("ix_environments_repository"))

    op.drop_table("environments")
    op.drop_table("deployment_locks")
    op.drop_table("configuration")
    op.drop_table("audits")
    with op.batch_alter_table("artifacts", schema=None) as batch_op:
        batch_op.drop_index(batch_op.f("ix_artifacts_run"))

    op.drop_table("artifacts")
