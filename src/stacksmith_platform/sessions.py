import hashlib
import secrets
import time
from pathlib import Path

from .database import Database, local_database, row, sessions


class SessionStore:
    """Keep encrypted GitHub credentials behind hashed opaque session cookies.

    Args:
        database: Shared database or local authoring database path.
        secret: Encryption secret for local authoring mode.
    """

    def __init__(self, database: Database | Path, secret: str = ""):
        self.database = (
            database
            if isinstance(database, Database)
            else local_database(database, secret)
        )

    def create(self, login: str, token: str, expires_in: int = 28800) -> str:
        """Create an eight-hour maximum browser session.

        Args:
            login: GitHub login.
            token: GitHub user token.
            expires_in: Credential lifetime in seconds.

        Returns:
            Opaque session cookie.
        """
        identifier = secrets.token_urlsafe(32)
        with self.database.transaction() as connection:
            connection.execute(
                sessions.delete().where(sessions.c.expires < time.time())
            )
            connection.execute(
                sessions.insert().values(
                    id=hashlib.sha256(identifier.encode()).hexdigest(),
                    login=login,
                    token=self.database.encrypt(token),
                    expires=time.time() + min(expires_in, 28800),
                )
            )
        return identifier

    def get(self, identifier: str) -> tuple[str, str] | None:
        """Resolve an unexpired session.

        Args:
            identifier: Opaque cookie.

        Returns:
            Login and decrypted GitHub credential, or `None`.
        """
        with self.database.transaction() as connection:
            record = row(
                connection, sessions, hashlib.sha256(identifier.encode()).hexdigest()
            )
            if not record or record["expires"] <= time.time():
                return None
            return record["login"], self.database.decrypt(record["token"])

    def delete(self, identifier: str) -> None:
        """Invalidate a browser session.

        Args:
            identifier: Opaque cookie.

        Returns:
            None.
        """
        with self.database.transaction() as connection:
            connection.execute(
                sessions.delete().where(
                    sessions.c.id == hashlib.sha256(identifier.encode()).hexdigest()
                )
            )
