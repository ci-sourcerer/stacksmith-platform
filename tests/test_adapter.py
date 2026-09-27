import json
import subprocess
from concurrent.futures import ThreadPoolExecutor
from functools import partial
from pathlib import Path
from threading import Barrier
from unittest.mock import patch

import pytest

from stacksmith_platform.adapter import StacksmithAdapter
from stacksmith_platform.errors import ComposerError
from stacksmith_platform.settings import Settings


def _queued_inspection(barrier, *args):
    barrier.wait(timeout=5)
    return "{}"


def test_execution_requests_bypass_local_subprocess_slots(tmp_path):
    adapter = StacksmithAdapter(Settings(_env_file=None, config=tmp_path / "config"))
    adapter.settings.execution_enabled = True
    with (
        patch("stacksmith_platform.inspection.InspectionQueue") as queue,
        ThreadPoolExecutor(max_workers=4) as executor,
    ):
        queue.return_value.execute.side_effect = partial(_queued_inspection, Barrier(4))
        futures = [executor.submit(adapter.catalog) for _ in range(4)]
        assert [future.result(timeout=10) for future in futures] == [{}, {}, {}, {}]
        assert queue.return_value.execute.call_count == 4


def test_environment_context_is_forwarded_to_isolated_inspection(tmp_path):
    context = {"repository": "acme/service", "environment": "dev", "revision": "a" * 40}
    adapter = StacksmithAdapter(
        Settings(_env_file=None, config=tmp_path / "config"), context=context
    )
    adapter.settings.execution_enabled = True
    with patch("stacksmith_platform.inspection.InspectionQueue") as queue:
        queue.return_value.execute.return_value = "{}"
        assert adapter.catalog("user-token") == {}
        assert queue.return_value.execute.call_args.kwargs["context"] == context


def test_compound_review_submits_one_job_with_document_edits(tmp_path):
    context = {"repository": "acme/service", "environment": "dev"}
    adapter = StacksmithAdapter(
        Settings(_env_file=None, config=tmp_path / "config"), context=context
    )
    adapter.settings.execution_enabled = True
    edits = [{"action": "set", "path": ["name"], "value": "draft"}]
    with patch("stacksmith_platform.inspection.InspectionQueue") as queue:
        queue.return_value.execute.return_value = json.dumps(
            {
                "source": "name: draft\n",
                "catalog": {},
                "associations": {"rules": []},
                "timings": {"authoring": 0.01},
            }
        )
        assert adapter.review("name: original\n", edits, "stack.yaml", "token") == {
            "source": "name: draft\n",
            "catalog": {},
            "associations": {"rules": []},
        }
    queue.return_value.execute.assert_called_once_with(
        ["review"],
        None,
        "token",
        {
            "version": 1,
            "source": "name: original\n",
            "edits": edits,
            "path": "stack.yaml",
        },
        context=context,
    )


def test_local_subprocess_limit_is_retained_and_released_after_failure(tmp_path):
    adapter = StacksmithAdapter(Settings(_env_file=None, config=tmp_path / "config"))
    assert adapter.slots.acquire(blocking=False)
    assert adapter.slots.acquire(blocking=False)
    with pytest.raises(ComposerError, match="busy"):
        adapter.catalog()
    adapter.slots.release()
    with (
        patch.object(adapter, "_execute", side_effect=ComposerError("failed")),
        pytest.raises(ComposerError, match="failed"),
    ):
        adapter.catalog()
    assert adapter.slots.acquire(blocking=False)
    assert not adapter.slots.acquire(blocking=False)
    adapter.slots.release()
    adapter.slots.release()


@pytest.mark.parametrize("query", [None, "components.api.properties.region"])
def test_resolution_shows_values_on_current_source(tmp_path, query):
    adapter = StacksmithAdapter(
        Settings(_env_file=None, config=tmp_path / "config.yaml")
    )
    adapter.command = "/example/bin/stacksmith"
    with patch(
        "subprocess.run",
        return_value=subprocess.CompletedProcess([], 0, '{"events": []}', ""),
    ) as run:
        assert adapter.resolution(
            "name: draft\ncomponents: {}", "session-token", query
        ) == {"events": []}
    args = run.call_args.args[0]
    assert args[1:3] == ["info", "explain"]
    assert "--show-values" in args
    assert args[args.index("--format") + 1] == "json"
    if query:
        assert args[3] == query
    assert "--stack" in args
    assert run.call_args.kwargs["env"]["STACKSMITH_GIT_TOKEN"] == "session-token"
    assert not Path(run.call_args.kwargs["cwd"]).exists()


def test_command_is_explicit_and_only_session_github_credential_is_forwarded(
    tmp_path, monkeypatch
):
    monkeypatch.setenv("STACKSMITH_PLATFORM_GITHUB_CLIENT_SECRET", "must-not-leak")
    monkeypatch.setenv("GITHUB_TOKEN", "must-not-leak")
    monkeypatch.setenv("STACKSMITH_RUN_FILE", "untrusted-runfile")
    adapter = StacksmithAdapter(
        Settings(
            _env_file=None, github_client_secret="", config=tmp_path / "trusted.yaml"
        )
    )
    adapter.command = "/example/bin/stacksmith"
    with patch(
        "subprocess.run",
        return_value=subprocess.CompletedProcess([], 0, json.dumps({"rules": []}), ""),
    ) as run:
        assert adapter.associations("name: demo\ncomponents: {}", "session-token") == {
            "rules": []
        }
    assert run.call_args.kwargs["shell"] is False
    assert "GITHUB_TOKEN" not in run.call_args.kwargs["env"]
    assert "STACKSMITH_PLATFORM_GITHUB_CLIENT_SECRET" not in run.call_args.kwargs["env"]
    assert "STACKSMITH_RUN_FILE" not in run.call_args.kwargs["env"]
    assert run.call_args.kwargs["env"]["STACKSMITH_GIT_TOKEN"] == "session-token"
    assert not Path(run.call_args.kwargs["cwd"]).exists()


def test_subprocess_environment_forwards_custom_ca(monkeypatch):
    from stacksmith_platform.adapter import _subprocess_environment

    monkeypatch.setenv("SSL_CERT_FILE", "/tmp/stacksmith-ca.pem")

    assert _subprocess_environment(None)["SSL_CERT_FILE"] == "/tmp/stacksmith-ca.pem"


def test_timeout_has_a_public_recovery_message(tmp_path):
    adapter = StacksmithAdapter(
        Settings(_env_file=None, config=tmp_path / "trusted.yaml")
    )
    adapter.command = "/example/bin/stacksmith"
    with (
        patch(
            "subprocess.run", side_effect=subprocess.TimeoutExpired("stacksmith", 30)
        ),
        pytest.raises(ComposerError, match="timed out"),
    ):
        adapter.catalog()


def test_cli_stderr_is_not_exposed(tmp_path):
    adapter = StacksmithAdapter(
        Settings(_env_file=None, config=tmp_path / "trusted.yaml")
    )
    adapter.command = "/example/bin/stacksmith"
    with (
        patch(
            "subprocess.run",
            return_value=subprocess.CompletedProcess([], 1, "", "secret-value"),
        ),
        pytest.raises(ComposerError) as error,
    ):
        adapter.catalog()
    assert "secret-value" not in str(error.value)


def test_catalog_removes_configured_module_source_prefix(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("STACKSMITH_PLATFORM_MODULE_SOURCE_PREFIX", str(tmp_path))
    adapter = StacksmithAdapter(
        Settings(_env_file=None, config=tmp_path / "trusted.yaml")
    )
    with patch.object(
        adapter,
        "_json",
        return_value={
            "service": {
                "module_source": str(tmp_path / "modules" / "service"),
                "module_version": "local",
            },
            "remote": {
                "module_source": "https://github.com/example/modules.git//service",
                "module_version": "v1.2.3",
            },
        },
    ):
        assert adapter.catalog() == {
            "service": {
                "module_path": "modules/service",
                "module_source": "modules/service",
                "module_version": "local",
            },
            "remote": {
                "module_source": "https://github.com/example/modules.git//service",
                "module_source_url": (
                    "https://github.com/example/modules/tree/v1.2.3/service"
                ),
                "module_version": "v1.2.3",
            },
        }


@pytest.mark.parametrize(
    ("module_source", "module_version", "expected"),
    [
        (
            "https://github.com/example/modules.git//service",
            None,
            "https://github.com/example/modules/tree/HEAD/service",
        ),
        (
            "https://github.com/example/modules.git//service",
            "v1.2.3",
            "https://github.com/example/modules/tree/v1.2.3/service",
        ),
        (
            "https://github.com/example/modules.git//service?ref=main",
            "v1.2.3",
            "https://github.com/example/modules/tree/main/service",
        ),
        (
            "https://github.com/example/modules.git//",
            None,
            "https://github.com/example/modules/tree/HEAD",
        ),
        (
            "https://github.com/example/modules.git",
            None,
            "https://github.com/example/modules",
        ),
        (
            "https://github.com/example/modules.git",
            "v1.0.0",
            "https://github.com/example/modules/tree/v1.0.0",
        ),
    ],
)
def test_catalog_module_source_url_rendering(
    tmp_path, module_source, module_version, expected
):
    adapter = StacksmithAdapter(
        Settings(_env_file=None, config=tmp_path / "trusted.yaml")
    )
    with patch.object(
        adapter,
        "_json",
        return_value={
            "remote": {
                "module_source": module_source,
                "module_version": module_version,
            }
        },
    ):
        assert adapter.catalog()["remote"]["module_source_url"] == expected


def test_authoring_uses_json_stdin_without_config_or_credentials(tmp_path, monkeypatch):
    monkeypatch.setenv("GITHUB_TOKEN", "must-not-leak")
    monkeypatch.setenv("STACKSMITH_GIT_TOKEN", "must-not-leak")
    adapter = StacksmithAdapter(
        Settings(_env_file=None, config=tmp_path / "missing.yaml")
    )
    adapter.command = "/example/bin/stacksmith"
    response = {
        "version": 1,
        "validation": "document",
        "source": "name: proposed\n",
        "diff": "reviewed diff",
        "changed": True,
    }
    edits = [{"action": "set", "path": ["name"], "value": "proposed"}]
    with patch(
        "subprocess.run",
        return_value=subprocess.CompletedProcess([], 0, json.dumps(response), ""),
    ) as run:
        assert adapter.author("name: original\n", edits, "apps/stack.yaml") == response
    assert run.call_args.args[0] == [adapter.command, "author"]
    assert json.loads(run.call_args.kwargs["input"]) == {
        "version": 1,
        "source": "name: original\n",
        "edits": edits,
        "path": "apps/stack.yaml",
    }
    assert run.call_args.kwargs["shell"] is False
    assert "STACKSMITH_GIT_TOKEN" not in run.call_args.kwargs["env"]
    assert "GITHUB_TOKEN" not in run.call_args.kwargs["env"]
    assert not Path(run.call_args.kwargs["cwd"]).exists()
    assert not (tmp_path / "missing.yaml").exists()


@pytest.mark.parametrize(
    "response",
    [
        "not json",
        json.dumps(
            {
                "version": 2,
                "validation": "document",
                "source": "source",
                "diff": "",
                "changed": False,
            }
        ),
        json.dumps({"version": 1, "source": "source", "diff": "", "changed": False}),
        json.dumps(
            {
                "version": 1,
                "validation": "document",
                "source": "source",
                "diff": "",
                "changed": "false",
            }
        ),
    ],
)
def test_authoring_rejects_incompatible_success_responses(tmp_path, response):
    adapter = StacksmithAdapter(
        Settings(_env_file=None, config=tmp_path / "config.yaml")
    )
    adapter.command = "/example/bin/stacksmith"
    with (
        patch(
            "subprocess.run",
            return_value=subprocess.CompletedProcess([], 0, response, "secret-output"),
        ),
        pytest.raises(ComposerError) as error,
    ):
        adapter.author("name: example\n", [], "stack.yaml")
    assert error.value.status == 502
    assert "secret-output" not in str(error.value)


def test_authoring_reports_document_rejection_without_exposing_cli_diagnostics(
    tmp_path,
):
    adapter = StacksmithAdapter(
        Settings(_env_file=None, config=tmp_path / "config.yaml")
    )
    adapter.command = "/example/bin/stacksmith"
    response = json.dumps(
        {"version": 1, "error": {"code": "invalid_document", "message": "secret-value"}}
    )
    with (
        patch(
            "subprocess.run",
            return_value=subprocess.CompletedProcess([], 6, response, "secret-output"),
        ),
        pytest.raises(ComposerError) as error,
    ):
        adapter.author("name: example\n", [], "stack.yaml")
    assert error.value.status == 422
    assert "secret" not in str(error.value)


def test_old_stacksmith_has_a_clear_authoring_compatibility_error(tmp_path):
    adapter = StacksmithAdapter(
        Settings(_env_file=None, config=tmp_path / "config.yaml")
    )
    adapter.command = "/example/bin/stacksmith"
    with (
        patch(
            "subprocess.run",
            return_value=subprocess.CompletedProcess([], 2, "", "unknown command"),
        ),
        pytest.raises(ComposerError, match="version 1 authoring") as error,
    ):
        adapter.author("name: example\n", [], "stack.yaml")
    assert error.value.status == 502
