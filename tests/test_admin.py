import sys
from unittest.mock import MagicMock

from stacksmith_platform import admin


def test_initializer_grants_prepared_context_access_to_both_roles(monkeypatch):
    engine = MagicMock()
    monkeypatch.setattr(admin, "database_url", lambda: "postgresql+psycopg://test")
    monkeypatch.setattr(admin, "create_engine", lambda _: engine)
    monkeypatch.setattr(admin, "upgrade", MagicMock())
    monkeypatch.setattr(sys, "argv", ["stacksmith-platform-init", "--grant-roles"])
    admin.main()
    connection = engine.begin.return_value.__enter__.return_value
    connection.exec_driver_sql.assert_any_call(
        "GRANT SELECT, INSERT, UPDATE, DELETE ON prepared_contexts TO platform_api, platform_worker"
    )
