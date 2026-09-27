import base64
import hashlib
import time
from contextlib import contextmanager
from pathlib import Path
from typing import Any

from cryptography.fernet import Fernet
from sqlalchemy import (
    JSON,
    Column,
    Float,
    Index,
    Integer,
    MetaData,
    String,
    Table,
    Text,
    create_engine,
    event,
)
from sqlalchemy.pool import StaticPool

metadata = MetaData()


def _table(name, *columns):
    return Table(name, metadata, Column("id", String, primary_key=True), *columns)


sessions = _table(
    "sessions", Column("login", String), Column("token", Text), Column("expires", Float)
)
repositories = _table(
    "repositories",
    Column("github_id", String),
    Column("installation", Integer),
    Column("branch", String),
    Column("config", Text),
    Column("enabled", Integer, default=1),
    Column("deployment_enabled", Integer, default=1, server_default="1"),
)
Index("repositories_github_id", repositories.c.github_id, unique=True)

environments = _table(
    "environments",
    Column("repository", String, index=True),
    Column("name", String),
    Column("settings", JSON),
    Column("revision", Integer),
    Column("active", Integer, default=1, server_default="1", nullable=False),
)
grants = _table(
    "role_assignments",
    Column("login", String, index=True),
    Column("repository", String),
    Column("environment", String),
    Column("role", String),
    Column("workloads", JSON),
)
secrets = _table(
    "secrets",
    Column("environment", String, index=True),
    Column("name", String),
    Column("phases", JSON),
    Column("ciphertext", Text),
    Column("reference", String),
    Column("json_key", String),
)
execution_credential_bindings = _table(
    "execution_credential_bindings",
    Column("environment", String, index=True, nullable=False),
    Column("provider", String, nullable=False),
    Column("phases", JSON, nullable=False),
)
execution_credential_sessions = _table(
    "execution_credential_sessions",
    Column("run", String, index=True, nullable=False),
    Column("phase", Integer, nullable=False),
    Column("fence", Integer, nullable=False),
    Column("provider", String, nullable=False),
    Column("provider_revision", String, nullable=False),
    Column("token_digest", String, nullable=False),
    Column("expires", Float, nullable=False),
    Column("scope", JSON, nullable=False),
    Column("lease", Text),
    Column("redactions", Text, nullable=False),
)
tokens = _table(
    "tokens",
    Column("login", String),
    Column("name", String),
    Column("scope", JSON),
    Column("expires", Float),
    Column("revoked", Integer, default=0),
)
runs = _table(
    "runs",
    Column("repository", String, index=True),
    Column("environment", String),
    Column("revision", String),
    Column("action", String),
    Column("actor", String),
    Column("status", String, index=True),
    Column("phase", Integer),
    Column("spec", JSON),
    Column("result", JSON),
    Column("created", Float),
    Column("updated", Float),
    Column("expires", Float),
    Column("dedup", String, unique=True),
    Column("lock_key", String),
    Column("fence", Integer),
    Column("worker", String),
    Column("lease", Float),
    Column("execution", JSON),
    Column("supervision", JSON(none_as_null=True)),
)
audits = _table(
    "audits",
    Column("repository", String, index=True),
    Column("actor", String),
    Column("action", String),
    Column("run", String),
    Column("detail", JSON),
    Column("created", Float),
)
locks = _table("deployment_locks", Column("run", String, unique=True))
workers = _table("workers", Column("heartbeat", Float), Column("driver", String))
webhooks = _table("webhooks", Column("created", Float))
artifacts = _table(
    "artifacts",
    Column("run", String, index=True),
    Column("name", String),
    Column("key", String),
    Column("digest", String),
    Column("private", Integer),
    Column("expires", Float),
)
phase_claims = _table("phase_claims", Column("run", String), Column("fence", Integer))
configuration = _table(
    "configuration", Column("policy", JSON), Column("updated", Float)
)


scheduler_health = _table(
    "scheduler_health", Column("completed", Float), Column("error", Text)
)


class Database:
    """Provide transactional storage and encryption for platform records.

    Args:
        url: SQLAlchemy PostgreSQL URL, or SQLite URL for local authoring/tests.
        secret: Deployment-managed encryption secret.
    """

    def __init__(self, url: str, secret: str):
        self.engine = create_engine(
            url,
            **(
                {"poolclass": StaticPool, "connect_args": {"check_same_thread": False}}
                if url == "sqlite://"
                else {}
            ),
        )
        self.cipher = Fernet(
            base64.urlsafe_b64encode(hashlib.sha256(secret.encode()).digest())
        )
        if self.engine.dialect.name == "sqlite":
            event.listen(self.engine, "connect", _sqlite_foreign_keys)

    def initialize(self) -> None:
        """Upgrade the database to the latest packaged schema revision.

        Returns:
            None.

        Raises:
            ValueError: The database has an unversioned platform schema.
            sqlalchemy.exc.SQLAlchemyError: A database operation fails.
            alembic.util.CommandError: Revision history is invalid.
        """
        from .migration import upgrade

        upgrade(self.engine)

    @contextmanager
    def transaction(self):
        """Open a transaction, serializing writers in the SQLite test adapter.

        Yields:
            A database connection committed on success and rolled back on error.
        """
        with self.engine.begin() as connection:
            if self.engine.dialect.name == "sqlite":
                connection.exec_driver_sql("BEGIN IMMEDIATE")
            yield connection

    def encrypt(self, value: str) -> str:
        """Encrypt a secret value.

        Args:
            value: Plaintext secret.

        Returns:
            Authenticated ciphertext.
        """
        return self.cipher.encrypt(value.encode()).decode()

    def decrypt(self, value: str) -> str:
        """Decrypt a stored secret.

        Args:
            value: Authenticated ciphertext.

        Returns:
            Plaintext secret.
        """
        return self.cipher.decrypt(value.encode()).decode()


def local_database(path: Path, secret: str) -> Database:
    """Create the local authoring database when execution is disabled.

    Args:
        path: Local database path.
        secret: Deployment secret.

    Returns:
        Initialized local database.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    database = Database(f"sqlite:///{path}", secret)
    database.initialize()
    path.chmod(0o600)
    return database


def row(connection, table, identifier: str) -> dict[str, Any] | None:
    """Read one record by identifier.

    Args:
        connection: Active transaction connection.
        table: SQLAlchemy table.
        identifier: Primary key.

    Returns:
        A record mapping or `None`.
    """
    return (
        connection.execute(table.select().where(table.c.id == identifier))
        .mappings()
        .first()
    )


def _sqlite_foreign_keys(connection, _record):
    connection.execute("PRAGMA foreign_keys=ON")


def _audit_repository(connection, run, detail):
    if detail.get("repository"):
        return detail["repository"]
    if run:
        record = row(connection, runs, run)
        if record:
            return record["repository"]
    if detail.get("scope") and detail["scope"] != "platform":
        return detail["scope"]
    if ":" in detail.get("environment", ""):
        return detail["environment"].split(":", 1)[0]
    return None


def audit(connection, actor: str, action: str, run: str = "", **detail) -> None:
    """Append an immutable audit event inside the caller's transaction.

    Args:
        connection: Active transaction connection.
        actor: Authenticated identity.
        action: Event name.
        run: Optional run identifier.
        **detail: Non-secret event metadata.

    Returns:
        None.
    """
    from uuid import uuid4

    connection.execute(
        audits.insert().values(
            id=uuid4().hex,
            repository=_audit_repository(connection, run, detail),
            actor=actor,
            action=action,
            run=run,
            detail=detail,
            created=time.time(),
        )
    )
