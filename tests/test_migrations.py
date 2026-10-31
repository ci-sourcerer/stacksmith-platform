import io
import os
import shutil
import subprocess
import sys
from pathlib import Path
from uuid import uuid4

import pytest
from alembic import command
from alembic.autogenerate import compare_metadata
from alembic.runtime.migration import MigrationContext
from sqlalchemy import create_engine, inspect

from stacksmith_platform import admin, migration, migration_tools
from stacksmith_platform.database import Database, metadata


@pytest.fixture(params=["sqlite", "postgresql"])
def migration_engine(request):
    if request.param == "sqlite":
        engine = create_engine("sqlite://")
        yield engine
        engine.dispose()
        return
    if not os.environ.get("STACKSMITH_PLATFORM_TEST_DATABASE_URL"):
        pytest.skip("Disposable PostgreSQL URL not configured")
    schema = f"migration_test_{uuid4().hex}"
    owner = create_engine(os.environ["STACKSMITH_PLATFORM_TEST_DATABASE_URL"])
    with owner.begin() as connection:
        connection.exec_driver_sql(f'CREATE SCHEMA "{schema}"')
    engine = create_engine(
        os.environ["STACKSMITH_PLATFORM_TEST_DATABASE_URL"],
        connect_args={"options": f"-csearch_path={schema}"},
    )
    try:
        yield engine
    finally:
        engine.dispose()
        with owner.begin() as connection:
            connection.exec_driver_sql(f'DROP SCHEMA "{schema}" CASCADE')
        owner.dispose()


def test_upgrade_repeat_downgrade_and_metadata_agreement(migration_engine):
    migration.upgrade(migration_engine)
    with migration_engine.begin() as connection:
        connection.execute(
            metadata.tables["repositories"].insert().values(id="saved", github_id="1")
        )
    migration.upgrade(migration_engine)
    with migration_engine.begin() as connection:
        assert (
            connection.exec_driver_sql("SELECT id FROM repositories").scalar_one()
            == "saved"
        )
        assert MigrationContext.configure(connection).get_current_heads() == ("0008",)
        assert not compare_metadata(
            MigrationContext.configure(
                connection, opts={"compare_server_default": True}
            ),
            metadata,
        )
        config = migration.configuration()
        config.attributes["connection"] = connection
        command.downgrade(config, "base")
        assert set(inspect(connection).get_table_names()) == {"alembic_version"}
    migration.upgrade(migration_engine)
    with migration_engine.connect() as connection:
        assert MigrationContext.configure(connection).get_current_heads() == ("0008",)


def test_offline_sql_requires_no_database(monkeypatch):
    monkeypatch.setenv(
        "STACKSMITH_PLATFORM_DATABASE_URL", "postgresql+psycopg://unused/unused"
    )
    config = migration.configuration()
    config.output_buffer = io.StringIO()
    command.upgrade(config, "head", sql=True)
    assert "CREATE TABLE repositories" in config.output_buffer.getvalue()
    assert "repositories_github_id" in config.output_buffer.getvalue()
    assert "INSERT INTO alembic_version" in config.output_buffer.getvalue()
    assert (
        "GRANT DELETE ON artifacts, execution_credential_sessions TO platform_api"
        in config.output_buffer.getvalue()
    )


def test_audit_repository_scope_is_backfilled(migration_engine):
    with migration_engine.begin() as connection:
        config = migration.configuration()
        config.attributes["connection"] = connection
        command.upgrade(config, "0002")
        connection.execute(
            metadata.tables["runs"]
            .insert()
            .values(id="existing-run", repository="acme/service")
        )
        for identifier, run, detail in (
            ("run-event", "existing-run", {}),
            ("repository-event", "", {"repository": "acme/other"}),
            ("environment-event", "", {"environment": "acme/service:dev"}),
            ("configuration-event", "", {"scope": "acme/service"}),
            ("platform-event", "", {"scope": "platform"}),
            ("account-event", "", {"token": "identifier"}),
        ):
            connection.execute(
                metadata.tables["audits"]
                .insert()
                .values(id=identifier, run=run, detail=detail)
            )
        command.upgrade(config, "head")
        records = {
            event["id"]: event["repository"]
            for event in connection.execute(
                metadata.tables["audits"].select()
            ).mappings()
        }
        assert records == {
            "run-event": "acme/service",
            "repository-event": "acme/other",
            "environment-event": "acme/service",
            "configuration-event": "acme/service",
            "platform-event": None,
            "account-event": None,
        }


def test_migration_url_does_not_load_application_settings(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("STACKSMITH_PLATFORM_DATABASE_URL", raising=False)
    with pytest.raises(ValueError, match="DATABASE_URL"):
        migration.database_url()
    Path(".env").write_text("STACKSMITH_PLATFORM_DATABASE_URL=sqlite:///file.sqlite\n")
    assert migration.database_url() == "sqlite:///file.sqlite"
    monkeypatch.setenv("STACKSMITH_PLATFORM_DATABASE_URL", "sqlite://")
    monkeypatch.setenv("STACKSMITH_PLATFORM_ADMIN_LOGINS", "invalid-json")
    assert migration.database_url() == "sqlite://"


def test_admin_failure_never_grants_roles(monkeypatch):
    monkeypatch.setenv(
        "STACKSMITH_PLATFORM_DATABASE_URL", "postgresql+psycopg://unused/unused"
    )
    monkeypatch.setattr(sys, "argv", ["init", "--grant-roles"])
    from unittest.mock import MagicMock

    engine = MagicMock()
    monkeypatch.setattr(admin, "create_engine", MagicMock(return_value=engine))
    monkeypatch.setattr(
        admin, "upgrade", MagicMock(side_effect=ValueError("failed upgrade"))
    )
    with pytest.raises(ValueError, match="failed upgrade"):
        admin.main()
    engine.begin.assert_not_called()


def test_admin_grants_run_deletion_permissions(monkeypatch):
    from unittest.mock import MagicMock

    monkeypatch.setenv(
        "STACKSMITH_PLATFORM_DATABASE_URL", "postgresql+psycopg://unused/unused"
    )
    monkeypatch.setattr(sys, "argv", ["init", "--grant-roles"])
    engine = MagicMock()
    monkeypatch.setattr(admin, "create_engine", MagicMock(return_value=engine))
    monkeypatch.setattr(admin, "upgrade", MagicMock())

    admin.main()

    connection = engine.begin.return_value.__enter__.return_value
    statements = [call.args[0] for call in connection.exec_driver_sql.call_args_list]
    assert "GRANT DELETE ON artifacts TO platform_api" in statements
    assert (
        "GRANT SELECT, UPDATE, DELETE ON execution_credential_sessions TO platform_api"
        in statements
    )


def test_authoring_refuses_outdated_database(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    Path("alembic.ini").write_text(
        "[alembic]\nscript_location = stacksmith_platform:migrations\n"
    )
    monkeypatch.setenv("STACKSMITH_PLATFORM_DATABASE_URL", "sqlite://")
    monkeypatch.setattr(sys, "argv", ["migration-tools", "check"])
    with pytest.raises(ValueError, match="run-db-migrations"):
        migration_tools.main()


def test_autogeneration_writes_current_source_and_detects_drift(tmp_path, monkeypatch):
    root = Path(__file__).resolve().parents[1]
    shutil.copytree(root / "src", tmp_path / "src")
    shutil.copy(root / "alembic.ini", tmp_path / "alembic.ini")
    database = Database(f"sqlite:///{tmp_path / 'database.sqlite'}", "tests")
    database.initialize()
    database.engine.dispose()
    source = tmp_path / "src/stacksmith_platform/database.py"
    source.write_text(
        source.read_text().replace(
            'Column("login", String), Column("token", Text)',
            'Column("login", String), Column("nickname", String), Column("token", Text)',
        )
    )
    environment = {
        **os.environ,
        "PYTHONPATH": str(tmp_path / "src"),
        "STACKSMITH_PLATFORM_DATABASE_URL": f"sqlite:///{tmp_path / 'database.sqlite'}",
        "PATH": f"{Path(sys.executable).parent}:{os.environ['PATH']}",
    }
    check = subprocess.run(
        [sys.executable, "-m", "stacksmith_platform.migration_tools", "check"],
        check=False,
        cwd=tmp_path,
        env=environment,
        capture_output=True,
        text=True,
        shell=False,
    )
    assert check.returncode != 0 and "nickname" in check.stderr
    subprocess.run(
        [
            sys.executable,
            "-m",
            "stacksmith_platform.migration_tools",
            "revision",
            "--message",
            "Add nickname",
        ],
        cwd=tmp_path,
        env=environment,
        check=True,
        capture_output=True,
        text=True,
        shell=False,
    )
    generated = [
        path
        for path in (tmp_path / "src/stacksmith_platform/migrations/versions").glob(
            "*.py"
        )
        if not any(
            revision in path.name
            for revision in (
                "0001",
                "0002",
                "0003",
                "0004",
                "0005",
                "0006",
                "0007",
                "0008",
            )
        )
    ]
    assert len(generated) == 1
    assert '"nickname"' in generated[0].read_text()
    assert generated[0].stat().st_uid == os.getuid()
    subprocess.run(
        [sys.executable, "-m", "alembic", "upgrade", "head"],
        cwd=tmp_path,
        env=environment,
        check=True,
        capture_output=True,
        text=True,
        shell=False,
    )
    subprocess.run(
        [sys.executable, "-m", "stacksmith_platform.migration_tools", "check"],
        cwd=tmp_path,
        env=environment,
        check=True,
        capture_output=True,
        text=True,
        shell=False,
    )
