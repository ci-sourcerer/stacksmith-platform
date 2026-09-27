import json
import os
import shutil
import stat
import subprocess
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock
from uuid import uuid4

import pytest
from dotenv import dotenv_values, set_key

from scripts import build_execution
from scripts.configure import check, configure

ROOT = Path(__file__).parents[1]


def _configured(path):
    configure(path, ROOT / ".env.example")
    pem = path.parent / "github.pem"
    pem.write_text("test-private-key")
    for key, value in {
        "STACKSMITH_PLATFORM_GITHUB_CLIENT_ID": "client-id",
        "STACKSMITH_PLATFORM_GITHUB_CLIENT_SECRET": "client-secret",
        "STACKSMITH_PLATFORM_GITHUB_APP_ID": "1",
        "STACKSMITH_PLATFORM_GITHUB_PRIVATE_KEY_FILE": str(pem),
        "STACKSMITH_PLATFORM_GITHUB_PRIVATE_KEY": "test-private-key",
        "STACKSMITH_PLATFORM_ADMIN_LOGINS": '["owner"]',
        "STACKSMITH_PLATFORM_CONFIG": "git+https://github.com/owner/config//config.yaml@"
        + "a" * 40,
        "STACKSMITH_PLATFORM_STACKSMITH_IMAGE": "registry/stacksmith:version",
        "STACKSMITH_PLATFORM_EXECUTION_IMAGE": "sha256:" + "b" * 64,
    }.items():
        set_key(path, key, value)
    return dotenv_values(path, interpolate=False)


def test_configure_migrates_auth_generates_secrets_and_preserves_local_values(tmp_path):
    path = tmp_path / ".env"
    path.write_text(
        "COMPOSER_GITHUB_CLIENT_ID=old-client\nCOMPOSER_GITHUB_CLIENT_SECRET=old-secret\nCOMPOSER_SECRET_KEY=stable-encryption-key\nCOMPOSER_PUBLIC_URL=http://127.0.0.1:5173\nLOCAL_EXTRA=preserved\n"
    )
    missing = configure(path, ROOT / ".env.example")
    values = dotenv_values(path, interpolate=False)
    assert values["STACKSMITH_PLATFORM_GITHUB_CLIENT_SECRET"] == "old-secret"
    assert values["STACKSMITH_PLATFORM_SECRET_KEY"] == "stable-encryption-key"
    assert values["STACKSMITH_PLATFORM_PUBLIC_URL"] == "http://localhost:8000"
    assert values["LOCAL_EXTRA"] == "preserved"
    assert not any(key.startswith("COMPOSER_") for key in values)
    assert len(values["STACKSMITH_PLATFORM_DB_PASSWORD"]) >= 32
    assert (
        values["STACKSMITH_PLATFORM_DB_PASSWORD"]
        != values["STACKSMITH_PLATFORM_API_DB_PASSWORD"]
    )
    assert "STACKSMITH_PLATFORM_GITHUB_APP_ID" in missing
    assert stat.S_IMODE(path.stat().st_mode) == 0o600
    original = path.read_text()
    configure(path, ROOT / ".env.example")
    assert path.read_text() == original


def test_configure_preserves_quoted_secrets_without_printing_them(
    tmp_path, monkeypatch, capsys
):
    monkeypatch.chdir(tmp_path)
    shutil.copyfile(ROOT / ".env.example", ".env.example")
    set_key(".env", "STACKSMITH_PLATFORM_GITHUB_CLIENT_SECRET", "secret'$value")
    from scripts.configure import main

    main()
    assert (
        dotenv_values(".env", interpolate=False)[
            "STACKSMITH_PLATFORM_GITHUB_CLIENT_SECRET"
        ]
        == "secret'$value"
    )
    assert "secret'$value" not in capsys.readouterr().out


def test_preflight_fails_with_field_names_before_building(
    tmp_path, monkeypatch, capsys
):
    monkeypatch.chdir(tmp_path)
    configure(Path(".env"), ROOT / ".env.example")
    with pytest.raises(SystemExit) as error:
        check()
    assert error.value.code == 1
    output = capsys.readouterr().out
    assert "STACKSMITH_PLATFORM_GITHUB_APP_ID" in output
    assert dotenv_values(".env")["STACKSMITH_PLATFORM_SECRET_KEY"] not in output


def test_preflight_accepts_complete_configuration_and_rejects_invalid_json(
    tmp_path, monkeypatch, capsys
):
    monkeypatch.chdir(tmp_path)
    _configured(Path(".env"))
    check()
    assert "ready" in capsys.readouterr().out
    set_key(".env", "STACKSMITH_PLATFORM_ADMIN_LOGINS", "invalid-json")
    with pytest.raises(SystemExit):
        check()
    assert "valid JSON" in capsys.readouterr().out


def test_execution_build_uses_fresh_wheel_context_and_saves_immutable_id(
    tmp_path, monkeypatch
):
    monkeypatch.chdir(tmp_path)
    _configured(Path(".env"))
    runner = MagicMock(
        side_effect=[
            SimpleNamespace(),
            SimpleNamespace(),
            SimpleNamespace(stdout="sha256:" + "c" * 64 + "\n"),
        ]
    )
    monkeypatch.setattr(build_execution.subprocess, "run", runner)
    build_execution.main()
    calls = runner.call_args_list
    assert calls[0].args[0][:3] == ["uv", "build", "--wheel"]
    assert calls[1].args[0][:4] == [
        "docker",
        "build",
        "-f",
        "deploy/Dockerfile.execution",
    ]
    assert "--build-context" in calls[1].args[0]
    assert all(call.kwargs["shell"] is False for call in calls)
    assert not Path(calls[0].args[0][-1]).exists()
    assert (
        dotenv_values(".env")["STACKSMITH_PLATFORM_EXECUTION_IMAGE"]
        == "sha256:" + "c" * 64
    )


def test_prebuilt_execution_image_does_not_trigger_a_build(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    _configured(Path(".env"))
    set_key(".env", "STACKSMITH_PLATFORM_STACKSMITH_IMAGE", "")
    runner = MagicMock()
    monkeypatch.setattr(build_execution.subprocess, "run", runner)
    build_execution.main()
    runner.assert_not_called()


@pytest.mark.skipif(
    not shutil.which("docker"), reason="Docker Compose CLI is unavailable"
)
@pytest.mark.parametrize("debug", [False, True])
def test_compose_uses_one_env_and_separates_database_credentials(tmp_path, debug):
    values = _configured(tmp_path / ".env")
    ca_bundle = tmp_path / "custom-ca.pem"
    ca_bundle.write_text("custom CA PEM")
    set_key(tmp_path / ".env", "STACKSMITH_PLATFORM_CA_BUNDLE", str(ca_bundle))
    set_key(tmp_path / ".env", "UNRELATED_SECRET", "must-not-be-injected")
    set_key(tmp_path / ".env", "STACKSMITH_PLATFORM_INSPECTION_CONCURRENCY", "7")
    result = subprocess.run(
        [
            "docker",
            "compose",
            "--env-file",
            str(tmp_path / ".env"),
            "-f",
            str(ROOT / "compose.yaml"),
            *(["-f", str(ROOT / "compose.debug.yaml")] if debug else []),
            "config",
            "--format",
            "json",
        ],
        shell=False,
        check=True,
        capture_output=True,
        text=True,
        env={
            key: value
            for key, value in os.environ.items()
            if not key.startswith("STACKSMITH_PLATFORM_")
        },
    )
    config = json.loads(result.stdout)
    assert config["services"]["postgres"]["image"] == "postgres:18-alpine"
    assert any(
        mount["target"] == "/var/lib/postgresql"
        for mount in config["services"]["postgres"]["volumes"]
    )
    if debug:
        adminer = config["services"]["adminer"]
        assert adminer["ports"][0]["host_ip"] == "127.0.0.1"
        assert adminer["ports"][0]["published"] == "8081"
        assert (
            adminer["environment"]["ADMINER_DEFAULT_PASSWORD"]
            == values["STACKSMITH_PLATFORM_DB_PASSWORD"]
        )
        assert adminer["depends_on"]["postgres"]["condition"] == "service_healthy"
        assert set(adminer["networks"]) == {"control", "public"}
    else:
        assert "adminer" not in config["services"]
    api = config["services"]["api"]["environment"]
    worker = config["services"]["worker"]["environment"]
    assert worker["STACKSMITH_PLATFORM_INSPECTION_CONCURRENCY"] == "7"
    assert api["STACKSMITH_PLATFORM_CA_BUNDLE"] == str(ca_bundle)
    assert worker["STACKSMITH_PLATFORM_CA_BUNDLE"] == str(ca_bundle)
    assert any(
        mount["source"] == str(ca_bundle)
        and mount["target"] == "/etc/stacksmith-custom-ca.pem"
        and mount["read_only"]
        for mount in config["services"]["api"]["volumes"]
    )
    assert any(
        mount["source"] == str(ca_bundle)
        and mount["target"] == "/etc/stacksmith-custom-ca.pem"
        and mount["read_only"]
        for mount in config["services"]["worker"]["volumes"]
    )
    assert values["STACKSMITH_PLATFORM_DB_PASSWORD"] not in json.dumps(api)
    assert values["STACKSMITH_PLATFORM_WORKER_DB_PASSWORD"] not in json.dumps(api)
    assert values["STACKSMITH_PLATFORM_API_DB_PASSWORD"] not in json.dumps(worker)
    assert "must-not-be-injected" not in json.dumps(config)
    assert (
        config["services"]["storage-backend"]["environment"]["AWS_SECRET_ACCESS_KEY"]
        == api["STACKSMITH_PLATFORM_ARTIFACT_SECRET_KEY"]
    )
    assert json.loads(api["STACKSMITH_PLATFORM_SECRET_REFERENCES"]) == {}
    assert set(config["services"]["storage-backend"]["networks"]) == {
        "artifact-backend"
    }
    assert config["networks"]["artifact-backend"]["internal"]
    assert "artifact-backend" not in config["services"]["api"]["networks"]
    assert "ports" not in config["services"]["storage"]
    assert api["STACKSMITH_PLATFORM_GITHUB_PRIVATE_KEY"]
    assert "STACKSMITH_PLATFORM_GITHUB_PRIVATE_KEY_FILE" not in api
    frontend = config["services"]["frontend"]
    assert frontend["stop_grace_period"] == "30s"
    assert config["services"]["api"]["init"] is True
    assert config["services"]["api"]["stop_grace_period"] == "1m30s"
    assert frontend["ports"][0]["target"] == 8080
    assert set(frontend["environment"]) == (
        {"API_UPSTREAM", "STACKSMITH_PLATFORM_PUBLIC_URL"}
        if debug
        else {"API_UPSTREAM"}
    )
    assert set(frontend["networks"]) == {"control", "public"}
    if debug:
        assert config["services"]["api"]["ports"][0]["host_ip"] == "127.0.0.1"
        assert config["services"]["api"]["ports"][0]["target"] == 5678
        assert not config["services"]["api"].get("tmpfs")
        assert (
            config["services"]["worker"]["develop"]["watch"][0]["action"]
            == "sync+restart"
        )
        assert config["services"]["api"]["build"]["target"] == "debug"
    else:
        assert "ports" not in config["services"]["api"]


@pytest.mark.skipif(
    not shutil.which("docker"), reason="Docker Compose CLI is unavailable"
)
def test_compose_preserves_a_postgres_17_volume_and_selected_database(tmp_path):
    _configured(tmp_path / ".env")
    set_key(
        tmp_path / ".env", "STACKSMITH_PLATFORM_POSTGRES_IMAGE", "postgres:17-alpine"
    )
    set_key(
        tmp_path / ".env", "STACKSMITH_PLATFORM_POSTGRES_PGDATA", "/var/lib/postgresql"
    )
    set_key(tmp_path / ".env", "STACKSMITH_PLATFORM_POSTGRES_DATABASE", "platform_copy")
    config = json.loads(
        subprocess.run(
            [
                "docker",
                "compose",
                "--env-file",
                str(tmp_path / ".env"),
                "-f",
                str(ROOT / "compose.yaml"),
                "-f",
                str(ROOT / "compose.debug.yaml"),
                "-f",
                str(ROOT / "compose.migrations.yaml"),
                "--profile",
                "tools",
                "config",
                "--format",
                "json",
            ],
            shell=False,
            check=True,
            capture_output=True,
            text=True,
            env={
                key: value
                for key, value in os.environ.items()
                if not key.startswith("STACKSMITH_PLATFORM_")
            },
        ).stdout
    )["services"]
    assert config["postgres"]["image"] == "postgres:17-alpine"
    assert config["postgres"]["environment"]["PGDATA"] == "/var/lib/postgresql"
    assert config["postgres"]["environment"]["POSTGRES_DB"] == "platform_copy"
    assert config["adminer"]["environment"]["ADMINER_DEFAULT_DB"] == "platform_copy"
    for service in ("api", "worker", "migrate", "database-grants", "migration-tools"):
        assert config[service]["environment"][
            "STACKSMITH_PLATFORM_DATABASE_URL"
        ].endswith("/platform_copy")


def test_compose_commands_reload_env_written_by_earlier_tasks(tmp_path, monkeypatch):
    from scripts import stack

    monkeypatch.chdir(tmp_path)
    _configured(Path(".env"))
    monkeypatch.setenv(
        "STACKSMITH_PLATFORM_EXECUTION_IMAGE", "stale-image-from-task-start"
    )
    set_key(".env", "STACKSMITH_PLATFORM_EXECUTION_IMAGE", "sha256:" + "d" * 64)
    runner = MagicMock()
    monkeypatch.setattr(stack.subprocess, "run", runner)
    stack._run("up", "--detach", "--wait")
    assert "STACKSMITH_PLATFORM_EXECUTION_IMAGE" not in runner.call_args.kwargs["env"]
    assert (
        dotenv_values(".env")["STACKSMITH_PLATFORM_EXECUTION_IMAGE"]
        == "sha256:" + "d" * 64
    )
    assert runner.call_args.args[0] == [
        "docker",
        "compose",
        "--file",
        "compose.yaml",
        "up",
        "--detach",
        "--wait",
    ]
    stack.down()
    assert "down" not in runner.call_args.args[0]
    assert runner.call_args.args[0][-4:] == ["rm", "--force", "--stop", "--volumes"]


def _docker(*arguments, check=True):
    return subprocess.run(
        ["docker", *arguments],
        shell=False,
        check=check,
        capture_output=True,
        text=True,
        env={
            key: value
            for key, value in os.environ.items()
            if not key.startswith("STACKSMITH_PLATFORM_")
        },
    )


@pytest.mark.skipif(
    os.environ.get("STACKSMITH_TEST_COMPOSE") != "1",
    reason="Set STACKSMITH_TEST_COMPOSE=1 to run isolated Compose runtime tests",
)
def test_compose_configs_healthchecks_and_graceful_shutdown(tmp_path):
    _configured(tmp_path / ".env")
    config = json.loads(
        _docker(
            "compose",
            "--env-file",
            str(tmp_path / ".env"),
            "-f",
            str(ROOT / "compose.yaml"),
            "config",
            "--format",
            "json",
        ).stdout
    )
    services = {
        name: config["services"][name]
        for name in (
            "postgres",
            "storage-backend",
            "storage",
            "storage-init",
            "migrate",
            "database-grants",
            "worker",
        )
    }
    services["worker"]["depends_on"] = {
        "database-grants": {"condition": "service_completed_successfully"},
        "storage-init": {"condition": "service_completed_successfully"},
    }
    for name in ("migrate", "database-grants", "worker"):
        services[name]["environment"]["PYTHONPATH"] = "/runtime-src"
        services[name].setdefault("volumes", []).append(
            {
                "type": "bind",
                "source": str(ROOT / "src"),
                "target": "/runtime-src",
                "read_only": True,
            }
        )
    path = tmp_path / "runtime.json"
    path.write_text(
        json.dumps(
            {
                "services": services,
                "configs": config["configs"],
                "volumes": {"postgres": {}, "artifacts": {}},
                "networks": {
                    "control": {"internal": True},
                    "execution": {},
                    "artifact-backend": {"internal": True},
                },
            }
        )
    )
    project = f"stacksmith-compose-test-{uuid4().hex[:10]}"
    compose = ("compose", "--project-name", project, "-f", str(path))
    try:
        _docker(*compose, "up", "--detach", "--wait", "--wait-timeout", "180")
        postgres = _docker(*compose, "ps", "-q", "postgres").stdout.strip()
        storage = _docker(*compose, "ps", "-q", "storage").stdout.strip()
        worker = _docker(*compose, "ps", "-q", "worker").stdout.strip()
        assert (
            _docker(
                "exec", postgres, "cat", "/docker-entrypoint-initdb.d/10-roles.sql"
            ).stdout
            == (ROOT / "deploy/postgres/init.sql").read_text()
        )
        assert (
            _docker(
                "exec",
                postgres,
                "psql",
                "-U",
                "platform",
                "-Atc",
                "SELECT count(*) FROM pg_roles WHERE rolname IN ('platform_api', 'platform_worker')",
            ).stdout.strip()
            == "2"
        )
        assert (
            _docker("exec", storage, "cat", "/etc/nginx/nginx.conf").stdout
            == (ROOT / "deploy/storage/nginx.conf").read_text()
        )
        _docker("exec", storage, "nginx", "-t")
        for container, target in (
            (postgres, "/docker-entrypoint-initdb.d/10-roles.sql"),
            (storage, "/etc/nginx/nginx.conf"),
        ):
            assert (
                _docker(
                    "exec",
                    "--user",
                    "0",
                    container,
                    "sh",
                    "-c",
                    f"echo changed >> {target}",
                    check=False,
                ).returncode
                != 0
            )
        _docker("exec", worker, "python", "-m", "stacksmith_platform.worker_health")
        assert (
            _docker(
                "exec",
                "-e",
                "STACKSMITH_PLATFORM_WORKER_ID=missing-worker",
                worker,
                "python",
                "-m",
                "stacksmith_platform.worker_health",
                check=False,
            ).returncode
            != 0
        )
        inspected = json.loads(_docker("inspect", worker).stdout)[0]
        assert inspected["HostConfig"]["Init"] is True
        assert inspected["Config"]["StopTimeout"] == 90
        identifier = _docker("exec", worker, "hostname").stdout.strip()
        _docker(*compose, "stop", "worker")
        assert (
            json.loads(_docker("inspect", worker).stdout)[0]["State"]["ExitCode"] == 0
        )
        assert (
            _docker(
                "exec",
                postgres,
                "psql",
                "-U",
                "platform",
                "-Atc",
                f"SELECT count(*) FROM workers WHERE id = '{identifier}'",
            ).stdout.strip()
            == "0"
        )
        _docker(*compose, "stop", "storage-backend")
        backend = _docker(
            *compose, "ps", "--all", "-q", "storage-backend"
        ).stdout.strip()
        assert (
            json.loads(_docker("inspect", backend).stdout)[0]["State"]["ExitCode"] == 0
        )
        assert (
            _docker(
                "exec",
                storage,
                "sh",
                "-c",
                services["storage"]["healthcheck"]["test"][1],
                check=False,
            ).returncode
            != 0
        )
        for service, container in (("storage", storage), ("postgres", postgres)):
            _docker(*compose, "stop", service)
            inspected = json.loads(_docker("inspect", container).stdout)[0]
            assert inspected["State"]["ExitCode"] == 0
            assert inspected["Config"]["StopTimeout"] == (
                30 if service == "storage" else 60
            )
    finally:
        _docker(*compose, "down", "--volumes", "--remove-orphans", check=False)
