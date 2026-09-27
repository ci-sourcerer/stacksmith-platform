import sqlite3

from stacksmith_platform.sessions import SessionStore


def test_tokens_are_encrypted_and_expired_sessions_are_unavailable(tmp_path):
    path = tmp_path / "sessions.sqlite3"
    store = SessionStore(path, "a" * 32)
    identifier = store.create("developer", "secret-github-token")
    assert store.get(identifier) == ("developer", "secret-github-token")
    assert b"secret-github-token" not in path.read_bytes()
    with sqlite3.connect(path) as connection:
        connection.execute("UPDATE sessions SET expires = 0")
    assert store.get(identifier) is None


def test_logout_invalidates_session(tmp_path):
    store = SessionStore(tmp_path / "sessions.sqlite3", "a" * 32)
    identifier = store.create("developer", "token")
    store.delete(identifier)
    assert store.get(identifier) is None
