import http.cookiejar
import json
import os
import shutil
import subprocess
import time
import urllib.error
import urllib.request
from pathlib import Path
from uuid import uuid4

import pytest

ROOT = Path(__file__).resolve().parents[1]
PRODUCTION = "stacksmith-platform:migration-test"
DEVELOPMENT = "stacksmith-platform:migration-test-debug"
pytestmark = pytest.mark.skipif(
    os.environ.get("STACKSMITH_TEST_MIGRATION_IMAGES") != "1",
    reason="Prepared migration images and Docker are required",
)


def _docker(*arguments, check=True):
    return subprocess.run(
        ["docker", *arguments], shell=False, check=check, capture_output=True, text=True
    )


@pytest.fixture(scope="module")
def containers():
    name = f"stacksmith-migration-test-{uuid4().hex[:12]}"
    _docker("network", "create", "--internal", name)
    _docker("network", "create", f"{name}-public")
    try:
        _docker(
            "run",
            "--detach",
            "--name",
            f"{name}-db",
            "--network",
            name,
            "--network-alias",
            "postgres",
            "-e",
            "POSTGRES_USER=platform",
            "-e",
            "POSTGRES_DB=platform",
            "-e",
            "POSTGRES_PASSWORD=test-password",
            "-e",
            "PLATFORM_API_DB_PASSWORD=api-password",
            "-e",
            "PLATFORM_WORKER_DB_PASSWORD=worker-password",
            "--mount",
            f"type=bind,src={ROOT / 'deploy/postgres/init.sql'},dst=/docker-entrypoint-initdb.d/10-roles.sql,readonly",
            "postgres:18-alpine",
        )
        for _ in range(60):
            if (
                _docker(
                    "exec",
                    f"{name}-db",
                    "pg_isready",
                    "-h",
                    "127.0.0.1",
                    "-U",
                    "platform",
                    check=False,
                ).returncode
                == 0
            ):
                break
            time.sleep(1)
        else:
            pytest.fail("Disposable PostgreSQL did not become ready")
        yield name
    finally:
        _docker(
            "rm", "--force", "--volumes", f"{name}-adminer", f"{name}-db", check=False
        )
        _docker("network", "rm", name, f"{name}-public")


def _runtime(network, *command):
    return _docker(
        "run",
        "--rm",
        "--network",
        network,
        "--read-only",
        "--tmpfs",
        "/tmp",
        "-e",
        "STACKSMITH_PLATFORM_DATABASE_URL=postgresql+psycopg://platform:test-password@postgres/platform",
        "--entrypoint",
        command[0],
        PRODUCTION,
        *command[1:],
    )


def test_installed_wheel_migrates_postgres_and_grants_roles(containers):
    _runtime(containers, "stacksmith-platform-init", "--grant-roles")
    _runtime(containers, "stacksmith-platform-init", "--grant-roles")
    _runtime(
        containers,
        "python",
        "-c",
        """
from importlib.util import find_spec
from sqlalchemy import create_engine
from alembic import command
from alembic.autogenerate import compare_metadata
from alembic.runtime.migration import MigrationContext
from stacksmith_platform.database import metadata
from stacksmith_platform.migration import configuration, database_url
assert find_spec('common_python_tasks') is None
with create_engine(database_url()).begin() as connection:
    assert MigrationContext.configure(connection).get_current_heads() == ('0006',)
    assert not compare_metadata(MigrationContext.configure(connection, opts={'compare_server_default': True}), metadata)
    assert connection.exec_driver_sql("SELECT has_table_privilege('platform_api', 'runs', 'INSERT')").scalar_one()
    assert connection.exec_driver_sql("SELECT has_table_privilege('platform_api', 'artifacts', 'DELETE')").scalar_one()
    assert connection.exec_driver_sql("SELECT has_table_privilege('platform_api', 'execution_credential_sessions', 'DELETE')").scalar_one()
    assert not connection.exec_driver_sql("SELECT has_table_privilege('platform_worker', 'sessions', 'SELECT')").scalar_one()
    config = configuration()
    config.attributes['connection'] = connection
    command.downgrade(config, '0005')
    connection.exec_driver_sql('REVOKE DELETE ON artifacts, execution_credential_sessions FROM platform_api')
    command.upgrade(config, 'head')
    assert connection.exec_driver_sql("SELECT has_table_privilege('platform_api', 'artifacts', 'DELETE')").scalar_one()
    assert connection.exec_driver_sql("SELECT has_table_privilege('platform_api', 'execution_credential_sessions', 'DELETE')").scalar_one()
    command.downgrade(config, 'base')
    command.upgrade(config, 'head')
    assert not compare_metadata(MigrationContext.configure(connection), metadata)
""",
    )
    _runtime(containers, "stacksmith-platform-init", "--grant-roles")


def test_debug_container_generates_host_writable_migration(containers, tmp_path):
    _runtime(containers, "stacksmith-platform-init")
    shutil.copytree(ROOT / "src", tmp_path / "src")
    shutil.copy(ROOT / "alembic.ini", tmp_path / "alembic.ini")
    source = tmp_path / "src/stacksmith_platform/database.py"
    source.write_text(
        source.read_text().replace(
            'Column("login", String), Column("token", Text)',
            'Column("login", String), Column("nickname", String), Column("token", Text)',
        )
    )
    result = _docker(
        "run",
        "--rm",
        "--network",
        containers,
        "--user",
        f"{os.getuid()}:{os.getgid()}",
        "--workdir",
        "/workspace",
        "-e",
        "PYTHONPATH=/workspace/src",
        "-e",
        "HOME=/tmp",
        "-e",
        "RUFF_CACHE_DIR=/tmp/ruff",
        "-e",
        "STACKSMITH_PLATFORM_DATABASE_URL=postgresql+psycopg://platform:test-password@postgres/platform",
        "--mount",
        f"type=bind,src={tmp_path},dst=/workspace",
        "--entrypoint",
        "python",
        DEVELOPMENT,
        "-m",
        "stacksmith_platform.migration_tools",
        "revision",
        "--message",
        "Add nickname",
    )
    assert "ruff_format" in result.stdout
    generated = [
        path
        for path in (tmp_path / "src/stacksmith_platform/migrations/versions").glob(
            "*.py"
        )
        if "0001" not in path.name
    ]
    assert len(generated) == 1 and '"nickname"' in generated[0].read_text()
    assert generated[0].stat().st_uid == os.getuid()


def test_adminer_automatically_logs_into_disposable_database(containers):
    _runtime(containers, "stacksmith-platform-init")
    _docker(
        "run",
        "--detach",
        "--name",
        f"{containers}-adminer",
        "--network",
        containers,
        "--publish",
        "127.0.0.1::8080",
        "-e",
        "ADMINER_DEFAULT_SERVER=postgres",
        "-e",
        "ADMINER_DEFAULT_USERNAME=platform",
        "-e",
        "ADMINER_DEFAULT_PASSWORD=test-password",
        "-e",
        "ADMINER_DEFAULT_DB=platform",
        "--mount",
        f"type=bind,src={ROOT / 'deploy/adminer/index.php'},dst=/var/www/html/index.php,readonly",
        "adminer:5.5.1-standalone",
    )
    _docker("network", "connect", f"{containers}-public", f"{containers}-adminer")
    port = json.loads(_docker("inspect", f"{containers}-adminer").stdout)[0][
        "NetworkSettings"
    ]["Ports"]["8080/tcp"][0]["HostPort"]
    opener = urllib.request.build_opener(
        urllib.request.HTTPCookieProcessor(http.cookiejar.CookieJar())
    )
    for _ in range(30):
        try:
            with opener.open(f"http://127.0.0.1:{port}/", timeout=2) as response:
                page = response.read().decode()
            break
        except urllib.error.URLError, ConnectionError:
            time.sleep(1)
    else:
        pytest.fail("Adminer did not become ready")
    assert "alembic_version" in page and "repositories" in page
