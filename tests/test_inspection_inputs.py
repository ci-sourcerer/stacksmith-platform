import io
import json
import os
import tarfile
from types import SimpleNamespace
from unittest.mock import Mock

import pytest
from cryptography.fernet import Fernet

from stacksmith_platform import execution
from stacksmith_platform.errors import ComposerError
from stacksmith_platform.inspection_inputs import (
    _catalog_configuration,
    _dependencies,
    _revision,
    rewrite_dependencies,
)


def test_provider_binaries_do_not_disable_immutable_catalog_reuse():
    client = SimpleNamespace(_request=Mock(return_value={"sha": "a" * 40}))
    assert _dependencies(
        _catalog_configuration(
            "provider_mappings:\n"
            "  aws:\n"
            "    source: {source: registry, data: {address: hashicorp/aws}}\n"
            "module_mappings:\n"
            "  policy:\n"
            "    source:\n"
            "      source: git\n"
            "      data: {repo: https://github.com/acme/modules, ref: main}\n"
        ),
        client,
        {},
    )
    client._request.assert_called_once()
    assert not _dependencies(
        _catalog_configuration(
            "module_mappings:\n"
            "  policy:\n"
            "    source: {source: registry, data: {address: acme/policy/aws}}\n"
        ),
        client,
        {},
    )


def test_dependency_resolution_pins_refs_once_and_rejects_unknown_sources():
    client = SimpleNamespace(_request=Mock(return_value={"sha": "a" * 40}))
    revisions = {}
    assert (
        _revision(client, "https://github.com/acme/modules.git", "v1.0.0", revisions)
        == "a" * 40
    )
    assert (
        _revision(client, "https://github.com/acme/modules.git", "v1.0.0", revisions)
        == "a" * 40
    )
    assert client._request.call_count == 1
    assert (
        _revision(client, "https://elsewhere.example/acme/modules", "main", revisions)
        is None
    )
    assert not _dependencies(
        "git+https://github.com/acme/other//config.yaml@main", client, revisions
    )
    assert not _dependencies(
        {"source": "registry", "data": {"version": ">= 1.0.0"}}, client, revisions
    )
    config = {
        "module": {
            "source": "git",
            "data": {"repo": "https://github.com/acme/modules.git", "ref": "v1.0.0"},
        }
    }
    rewrite_dependencies(config, revisions)
    assert config["module"]["data"]["ref"] == "a" * 40


def test_cache_bundle_excludes_git_credentials_generated_values_and_draft_source(
    tmp_path, monkeypatch
):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    (workspace / "stack.yaml").write_text("components: {edited: {}}\n")
    (workspace / ".git").mkdir()
    (workspace / ".git/config").write_text("private-git-token")
    (workspace / ".stacksmith/build").mkdir(parents=True)
    (workspace / ".stacksmith/build/manifest").write_text("resolved-secret-value")
    key = Fernet.generate_key()
    uploaded = {}

    def put(url, content, **kwargs):
        uploaded["content"] = content
        return SimpleNamespace(raise_for_status=lambda: None)

    monkeypatch.setattr(execution.httpx, "put", put)
    inspection = {
        "context": {"path": "stack.yaml"},
        "authoring_request": {"source": "components: {}\n"},
        "_cache": {"output_url": "private-cache", "output_key": key.decode()},
    }
    assert execution._write_input_cache(inspection, tmp_path, {"module": {}})
    content = Fernet(key).decrypt(uploaded["content"])
    assert b"private-git-token" not in content
    with tarfile.open(fileobj=io.BytesIO(content)) as archive:
        assert not any(
            ".git" in name or ".stacksmith/build" in name for name in archive.getnames()
        )
        assert archive.extractfile("workspace/stack.yaml").read() == b"components: {}\n"
        assert json.load(archive.extractfile("inspection-catalog.json")) == {
            "module": {}
        }
    assert "edited" in (workspace / "stack.yaml").read_text()


def test_pinned_dependency_rechecks_access_and_unknown_origins_bypass_reuse():
    client = SimpleNamespace(
        _request=Mock(side_effect=ComposerError("Access revoked", 403))
    )
    with pytest.raises(ComposerError, match="Access revoked"):
        _revision(client, "https://github.com/acme/modules", "a" * 40, {})
    assert (
        _revision(client, "https://elsewhere.example/acme/modules", "a" * 40, {})
        is None
    )


def test_warm_inspection_does_not_expose_assignment_or_control_plane_environment(
    tmp_path, monkeypatch
):
    (tmp_path / "workspace").mkdir()
    monkeypatch.setattr(
        os,
        "environ",
        {
            "PATH": "/bin",
            "PLATFORM_SANDBOX_TOKEN": "standby-secret",
            "CONTROL_SECRET": "private",
        },
    )
    observed = {}

    def inspect(context):
        observed.update(os.environ)
        return {"catalog": {}}

    monkeypatch.setattr(
        "stacksmith_platform.authoring_inspection._inspect_context", inspect
    )
    result = execution._warm_inspection(
        {"timeout": 30},
        {
            "args": ["review"],
            "github_token": "job-token",
            "credentials": {"AWS_REGION": "test"},
        },
        tmp_path,
    )
    assert result["exit_code"] == 0
    assert observed["STACKSMITH_GIT_TOKEN"] == "job-token"
    assert observed["AWS_REGION"] == "test"
    assert "CONTROL_SECRET" not in observed
    assert "PLATFORM_SANDBOX_TOKEN" not in observed
