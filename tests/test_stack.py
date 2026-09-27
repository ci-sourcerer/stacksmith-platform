import json
import subprocess
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from scripts import stack


@pytest.fixture
def builders(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(stack.configure, "check", MagicMock())
    monkeypatch.setattr(stack.images, "platform", MagicMock(return_value="FROM python"))
    monkeypatch.setattr(stack.images, "frontend", MagicMock())
    monkeypatch.setattr(stack.build_execution, "main", MagicMock())
    monkeypatch.setattr(stack, "get_uv_version", lambda: "0.11.0")
    monkeypatch.setattr(stack, "get_project_version", lambda: "0.1.0")
    monkeypatch.setattr(
        stack, "_run", MagicMock(return_value=SimpleNamespace(returncode=0))
    )


def test_invalid_configuration_stops_before_building(builders):
    stack.configure.check.side_effect = SystemExit(1)
    with pytest.raises(SystemExit, match="1"):
        stack.up()
    stack.images.platform.assert_not_called()
    stack.images.frontend.assert_not_called()
    stack._run.assert_not_called()


def test_detached_debug_stack_builds_all_images_without_watch(builders):
    stack.up(debug=True, detach=True, no_cache=True)
    stack.images.platform.assert_any_call(no_cache=True)
    stack.images.platform.assert_any_call(debug=True, no_cache=True)
    stack.images.frontend.assert_called_once_with(debug=True, no_cache=True)
    stack.build_execution.main.assert_called_once_with(no_cache=True)
    assert Path(".data/Dockerfile.api").read_text() == "FROM python"
    assert Path(".data/stack-debug").exists()
    assert "--watch" not in stack._run.call_args.args
    assert "--wait" in stack._run.call_args.args
    stack.down()
    assert not Path(".data/Dockerfile.api").exists()
    assert not Path(".data/stack-debug").exists()


@pytest.mark.parametrize(
    "status,cleanup_status,expected", [(0, 0, 0), (7, 9, 7), (0, 9, 9)]
)
def test_foreground_preserves_failure_and_cleans_up(
    builders, status, cleanup_status, expected
):
    stack._run.side_effect = [
        SimpleNamespace(returncode=status),
        SimpleNamespace(returncode=cleanup_status),
    ]
    with pytest.raises(SystemExit) as error:
        stack.up(debug=True)
    assert error.value.code == expected
    assert "--watch" in stack._run.call_args_list[0].args
    assert stack._run.call_args_list[1].args == ("rm", "--force", "--stop", "--volumes")
    assert not Path(".data/Dockerfile.api").exists()
    assert not Path(".data/stack-debug").exists()


def test_interrupted_foreground_still_removes_containers(builders):
    stack._run.side_effect = [KeyboardInterrupt, SimpleNamespace(returncode=0)]
    with pytest.raises(SystemExit) as error:
        stack.up()
    assert error.value.code == 130
    assert "--no-build" in stack._run.call_args_list[0].args
    assert "--stop" in stack._run.call_args_list[1].args


def test_environment_passes_run_values_but_drops_stale_image_ids(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    Path(".env").write_text(
        "STACKSMITH_RUN_FILE_VALUE=file\nSTACKSMITH_RUN_OVERRIDE=file\n"
        'STACKSMITH_PLATFORM_RUN_ENVIRONMENT={"EXPLICIT":"kept"}\n'
    )
    monkeypatch.setenv("STACKSMITH_RUN_OVERRIDE", "shell")
    monkeypatch.setenv("STACKSMITH_PLATFORM_EXECUTION_IMAGE", "stale")
    assert "STACKSMITH_PLATFORM_EXECUTION_IMAGE" not in stack._environment()
    assert json.loads(stack._environment()["STACKSMITH_PLATFORM_RUN_ENVIRONMENT"]) == {
        "FILE_VALUE": "file",
        "OVERRIDE": "shell",
        "EXPLICIT": "kept",
    }


def test_migrations_use_schema_and_grant_services_in_order(builders):
    stack.run_db_migrations()
    assert [call.args for call in stack._run.call_args_list] == [
        ("up", "--detach", "--wait", "postgres"),
        ("run", "--rm", "--no-deps", "migrate"),
        ("run", "--rm", "--no-deps", "database-grants"),
    ]


def test_migration_failure_prevents_grants(builders):
    stack._run.side_effect = [
        SimpleNamespace(returncode=0),
        subprocess.CalledProcessError(1, "migrate"),
    ]
    with pytest.raises(subprocess.CalledProcessError):
        stack.run_db_migrations()
    assert stack._run.call_count == 2


def test_migrations_build_current_source_by_default(builders):
    stack.run_db_migrations()
    stack.images.platform.assert_called_once_with()


def test_migrations_can_use_prepared_image(builders):
    stack.run_db_migrations(no_build=True)
    stack.images.platform.assert_not_called()


def test_database_shell_waits_for_postgres(builders):
    stack.db_shell()
    assert stack._run.call_args_list[0].args == ("up", "--detach", "--wait", "postgres")
    assert stack._run.call_args_list[1].args[:2] == ("exec", "postgres")


def test_service_selection_builds_and_cleans_only_dependency_closure(builders):
    stack._run.side_effect = [
        SimpleNamespace(
            stdout=json.dumps(
                {
                    "services": {
                        "adminer": {"depends_on": {"postgres": {}}},
                        "postgres": {},
                    }
                }
            )
        ),
        SimpleNamespace(returncode=0),
        SimpleNamespace(returncode=0),
    ]
    with pytest.raises(SystemExit) as error:
        stack.up(debug=True, services=["adminer"])
    assert error.value.code == 0
    stack.images.platform.assert_not_called()
    stack.images.frontend.assert_not_called()
    stack.build_execution.main.assert_not_called()
    assert stack._run.call_args_list[1].args[-2:] == ("adminer", "postgres")
    assert "--remove-orphans" not in stack._run.call_args_list[1].args
    assert stack._run.call_args_list[2].args[-2:] == ("adminer", "postgres")


def test_unknown_service_fails_before_building(builders):
    stack._run.return_value.stdout = '{"services": {"postgres": {}}}'
    with pytest.raises(ValueError, match="Unknown Compose service"):
        stack.up(services=["typo"])
    stack.images.platform.assert_not_called()


def test_reset_removes_only_resolved_postgres_volume(builders, monkeypatch):
    stack._run.return_value.stdout = json.dumps(
        {
            "volumes": {
                "postgres": {"name": "custom-postgres"},
                "artifacts": {"name": "custom-artifacts"},
            }
        }
    )
    run = MagicMock(
        return_value=SimpleNamespace(stdout="custom-postgres\ncustom-artifacts\n")
    )
    monkeypatch.setattr(stack.subprocess, "run", run)
    stack.reset_db()
    assert stack._run.call_args.args == ("rm", "--force", "--stop", "--volumes")
    assert run.call_args.args[0] == ["docker", "volume", "rm", "custom-postgres"]


def test_reset_refuses_external_volume_before_stopping(builders):
    stack._run.return_value.stdout = (
        '{"volumes": {"postgres": {"name": "shared", "external": true}}}'
    )
    with pytest.raises(ValueError, match="external"):
        stack.reset_db()
    assert stack._run.call_count == 1


def test_authoring_mounts_current_source_with_host_identity(builders):
    stack.create_db_migration("Add a field")
    stack.images.platform.assert_called_once_with(debug=True)
    assert stack._run.call_args.args == (
        "--file",
        "compose.migrations.yaml",
        "run",
        "--rm",
        "--no-deps",
        "migration-tools",
        "revision",
        "--message",
        "Add a field",
    )
    assert "LOCAL_UID" in stack._run.call_args.kwargs["environment"]
