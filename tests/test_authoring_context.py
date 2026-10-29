import json
from functools import partial
from importlib.metadata import version
from pathlib import Path
from unittest.mock import Mock

import pytest
from ruamel.yaml import YAML
from test_access import _grant
from test_control import CONFIG, SHA, _login
from test_control import platform as _platform_fixture

from stacksmith_platform.app import _github
from stacksmith_platform.database import environments, row
from stacksmith_platform.errors import ComposerError


def _warm_context(platform, snapshot, monkeypatch):
    from conftest import CATALOG

    snapshot.update(
        principal="admin",
        source=json.dumps(
            {"name": "test", "components": {"policy": {"type": "aws_iam_policy"}}}
        ),
    )
    preparation = Mock(
        return_value={
            "source": snapshot["source"],
            "catalog": CATALOG,
            "associations": {"rules": [], "applications": []},
            "_editing_context": {
                "resolver_version": version("stacksmith"),
                "config": YAML(typ="safe").load(
                    Path(__file__).resolve().parents[1] / "examples/platform.yaml"
                ),
            },
        }
    )
    monkeypatch.setattr(
        "stacksmith_platform.adapter.StacksmithAdapter.review", preparation
    )
    assert (
        platform.client.post(
            "/api/review",
            json={
                "snapshot": "signed",
                "components": json.loads(snapshot["source"])["components"],
            },
        ).status_code
        == 200
    )
    return preparation


def _change_access_after_resolution(monkeypatch, original, change, *args):
    result = original(*args)
    if change == "access":
        monkeypatch.setattr(
            "test_control.UserGitHub.repositories", Mock(return_value=[])
        )
    else:
        monkeypatch.setattr(
            "stacksmith_platform.app._config_revision", Mock(return_value="changed")
        )
    return result


@pytest.mark.parametrize(
    ("change", "status"), [("access", 403), ("configuration", 409)]
)
def test_warm_reviews_recheck_access_and_configuration_after_resolution(
    platform, snapshot, monkeypatch, change, status
):
    from stacksmith_platform.editing_contexts import _resolve

    preparation = _warm_context(platform, snapshot, monkeypatch)
    monkeypatch.setattr(
        "stacksmith_platform.editing_contexts._resolve",
        partial(_change_access_after_resolution, monkeypatch, _resolve, change),
    )
    response = platform.client.post(
        "/api/review",
        json={
            "snapshot": "signed",
            "components": {
                "policy": {"type": "aws_iam_policy", "properties": {"name": "updated"}}
            },
        },
    )
    assert response.status_code == status, response.text
    assert preparation.call_count == 1


@pytest.fixture(name="platform")
def _context_platform(tmp_path, monkeypatch):
    yield from _platform_fixture.__wrapped__(tmp_path, monkeypatch)


@pytest.fixture
def snapshot(platform, monkeypatch):
    source = json.dumps(
        {"name": "test", "components": {"database": {"type": "database"}}}
    )
    snapshot = {
        "principal": "operator",
        "repository": "acme/service",
        "head": SHA,
        "path": "stack.yaml",
        "source": source,
        "config_revision": "configuration",
    }
    monkeypatch.setattr("stacksmith_platform.app._snapshot", lambda *args: snapshot)
    monkeypatch.setattr(
        "stacksmith_platform.app._draft_source",
        lambda *args, **kwargs: (snapshot, source),
    )
    monkeypatch.setattr(
        "stacksmith_platform.app._config_revision", lambda *args: "configuration"
    )
    monkeypatch.setattr(
        "stacksmith_platform.app._managed_layers",
        lambda *args: [("local.yaml", "{}", "configuration")],
    )
    return snapshot


@pytest.mark.parametrize(
    ("role", "workloads", "availability", "can_inspect"),
    [
        ("viewer", [], "forbidden", False),
        ("runner", ["infrastructure"], "available", True),
    ],
)
def test_target_discovery_explains_inspection_access(
    platform, role, workloads, availability, can_inspect
):
    _grant(platform, role, workloads)
    _login(platform, "operator")
    response = platform.client.get("/api/inspection-targets?repository=acme/service")
    assert response.status_code == 200, response.text
    assert response.json() == {
        "targets": [{"name": "dev", "can_inspect": can_inspect}],
        "availability": availability,
        "can_configure": False,
    }


def test_target_discovery_distinguishes_missing_setup_from_hidden_targets(platform):
    assert (
        platform.client.post(
            "/api/v1/repositories/acme/service/environments", json={"name": "prod"}
        ).status_code
        == 200
    )
    _grant(platform, "viewer", environment="prod")
    with platform.database.transaction() as connection:
        connection.execute(environments.delete().where(environments.c.name == "prod"))
    _login(platform, "operator")
    response = platform.client.get("/api/inspection-targets?repository=acme/service")
    assert response.status_code == 200, response.text
    assert response.json() == {
        "targets": [],
        "availability": "forbidden",
        "can_configure": False,
    }
    with platform.database.transaction() as connection:
        connection.execute(environments.delete())
    response = platform.client.get("/api/inspection-targets?repository=acme/service")
    assert response.json() == {
        "targets": [],
        "availability": "unconfigured",
        "can_configure": False,
    }


@pytest.mark.parametrize("endpoint", ["/review", "/resolution", "/catalog"])
def test_stack_preview_does_not_require_a_deployment(
    platform, snapshot, monkeypatch, endpoint
):
    with platform.database.transaction() as connection:
        connection.execute(environments.delete())
    _grant(platform, "viewer")
    _login(platform, "operator")
    for method, result in {
        "review": {
            "source": snapshot["source"],
            "catalog": {"database": {"outputs": []}},
            "associations": {"rules": [], "applications": []},
        },
        "resolution": {"effective": {}, "events": []},
        "catalog": {},
    }.items():
        monkeypatch.setattr(
            f"stacksmith_platform.adapter.StacksmithAdapter.{method}",
            Mock(return_value=result),
        )
    response = platform.client.post(
        f"/api{endpoint}",
        json={"snapshot": "signed", "components": {"database": {"type": "database"}}},
    )
    assert response.status_code == 200, response.text
    assert response.json()["context"] == {
        "mode": "stack_preview",
        "repository": "acme/service",
        "revision": SHA,
        "path": "stack.yaml",
        "environment": None,
        "config_revision": "configuration",
    }


def test_review_uses_environment_and_returns_context(platform, snapshot, monkeypatch):
    calls = []
    monkeypatch.setattr(
        "stacksmith_platform.adapter.StacksmithAdapter.review",
        lambda self, source, edits, path, token: (
            calls.append(self.context)
            or {
                "source": source,
                "catalog": {"database": {"outputs": []}},
                "associations": {"rules": [], "applications": []},
            }
        ),
    )
    response = platform.client.post(
        "/api/review",
        json={
            "snapshot": "signed",
            "environment": "dev",
            "components": {"database": {"type": "database"}},
        },
    )
    assert response.status_code == 200, response.text
    assert len(calls) == 1
    assert calls[0]["revision"] == SHA
    assert calls[0]["actor"] == "admin"
    assert response.json()["context"]["environment"] == "dev"
    assert response.json()["context"]["mode"] == "deployment"
    assert response.json()["catalog"] == {"database": {"outputs": []}}
    assert "actor" not in response.json()["context"]


@pytest.mark.parametrize("environment", [None, "dev"])
def test_review_reuses_preflight_discovery_but_rechecks_access_after_inspection(
    platform, snapshot, monkeypatch, environment
):
    import stacksmith_platform.app as app_module

    discovery = Mock(wraps=app_module._repository_discovery)
    monkeypatch.setattr(app_module, "_repository_discovery", discovery)
    monkeypatch.setattr(
        "stacksmith_platform.adapter.StacksmithAdapter.review",
        Mock(
            return_value={
                "source": snapshot["source"],
                "catalog": {"database": {"outputs": []}},
                "associations": {"rules": [], "applications": []},
            }
        ),
    )
    draft = {
        "snapshot": "signed",
        "environment": environment,
        "components": {"database": {"type": "database"}},
    }
    for expected in (2, 4):
        response = platform.client.post("/api/review", json=draft)
        assert response.status_code == 200, response.text
        assert discovery.call_count == expected
    assert all(call.args[2] == "acme/service" for call in discovery.call_args_list)


@pytest.mark.parametrize("environment", [None, "dev"])
def test_review_logs_stage_durations_without_source_or_credentials(
    platform, snapshot, monkeypatch, caplog, environment
):
    monkeypatch.setattr(
        "stacksmith_platform.adapter.StacksmithAdapter.review",
        Mock(
            return_value={
                "source": snapshot["source"],
                "catalog": {"database": {"outputs": []}},
                "associations": {"rules": [], "applications": []},
            }
        ),
    )
    with caplog.at_level("INFO", logger="stacksmith_platform.app"):
        response = platform.client.post(
            "/api/review",
            json={
                "snapshot": "signed",
                "environment": environment,
                "components": {"database": {"type": "database"}},
            },
        )
    assert response.status_code == 200
    record = next(
        record
        for record in caplog.records
        if record.message.startswith("Review completed:")
    )
    assert record.args[0] >= sum(record.args[1].values())
    assert set(record.args[1]) == {"preflight", "inspection", "completion", "graph"}
    assert all(duration >= 0 for duration in record.args[1].values())
    assert snapshot["source"] not in record.message
    assert "signed" not in record.message
    assert "timings" not in response.json()


@pytest.mark.parametrize("matches_filter", [False, True])
def test_targeted_review_preserves_filter_and_subset_synchronization(
    platform, snapshot, monkeypatch, matches_filter
):
    from test_control import UserGitHub

    from stacksmith_platform.database import repositories

    monkeypatch.setattr(
        UserGitHub,
        "repositories",
        lambda self, repository=None, installation_id=None: [
            {
                "name": "acme/service",
                "github_id": "1",
                "installation_id": 1,
                "default_branch": "main",
                "topics": ["stacksmith-stack"] if matches_filter else [],
                "custom_properties": {},
            }
        ],
    )
    inspection = Mock(
        return_value={
            "source": snapshot["source"],
            "catalog": {"database": {"outputs": []}},
            "associations": {"rules": []},
        }
    )
    monkeypatch.setattr(
        "stacksmith_platform.adapter.StacksmithAdapter.review", inspection
    )
    with platform.database.transaction() as connection:
        connection.execute(
            repositories.insert().values(
                id="acme/other", github_id="2", enabled=1, installation=1
            )
        )
    response = platform.client.post(
        "/api/review",
        json={
            "snapshot": "signed",
            "components": {"database": {"type": "database"}},
        },
    )
    assert response.status_code == (200 if matches_filter else 403), response.text
    assert inspection.call_count == int(matches_filter)
    with platform.database.transaction() as connection:
        assert row(connection, repositories, "acme/other")["enabled"] == 1
        if matches_filter:
            assert row(connection, repositories, "acme/service")["github_id"] == "1"


@pytest.mark.parametrize("environment", [None, "dev"])
def test_review_rejects_github_access_revoked_during_inspection(
    platform, snapshot, monkeypatch, environment
):
    monkeypatch.setattr(
        "stacksmith_platform.app._repository_discovery",
        Mock(
            side_effect=[
                {"repositories": [{"name": "acme/service"}]},
                {"repositories": []},
            ]
        ),
    )
    inspection = Mock(
        return_value={
            "source": snapshot["source"],
            "catalog": {"database": {"outputs": []}},
            "associations": {"rules": []},
        }
    )
    monkeypatch.setattr(
        "stacksmith_platform.adapter.StacksmithAdapter.review", inspection
    )
    response = platform.client.post(
        "/api/review",
        json={
            "snapshot": "signed",
            "environment": environment,
            "components": {"database": {"type": "database"}},
        },
    )
    assert response.status_code == 403
    inspection.assert_called_once()


@pytest.mark.parametrize("environment", [None, "dev"])
def test_pr_reinspects_and_records_server_verified_context(
    platform, snapshot, monkeypatch, environment
):
    review = Mock(
        return_value={
            "source": json.dumps(
                {"name": "changed", "components": {"database": {"type": "database"}}}
            ),
            "catalog": {"database": {"outputs": []}},
            "associations": {"rules": [], "applications": []},
        }
    )
    monkeypatch.setattr("stacksmith_platform.adapter.StacksmithAdapter.review", review)
    github = Mock()
    github.create_pr.return_value = {"url": "https://github.com/acme/service/pull/1"}
    platform.app.dependency_overrides[_github] = lambda: github
    response = platform.client.post(
        "/api/pull-requests",
        json={
            "snapshot": "signed",
            "environment": environment,
            "components": {"database": {"type": "database"}},
        },
    )
    assert response.status_code == 200, response.text
    review.assert_called_once()
    assert github.create_pr.call_args.kwargs["inspection_context"]["mode"] == (
        "deployment" if environment else "stack_preview"
    )
    assert (
        github.create_pr.call_args.kwargs["inspection_context"]["config_revision"]
        == "configuration"
    )
    assert "actor" not in github.create_pr.call_args.kwargs["inspection_context"]


def test_environment_access_is_checked_before_catalog_inspection(
    platform, snapshot, monkeypatch
):
    _grant(platform, "viewer")
    _login(platform, "operator")
    catalog = Mock(side_effect=AssertionError("unauthorized inspection"))
    monkeypatch.setattr(
        "stacksmith_platform.adapter.StacksmithAdapter.catalog", catalog
    )
    response = platform.client.post(
        "/api/catalog", json={"snapshot": "signed", "environment": "dev"}
    )
    assert response.status_code == 403
    catalog.assert_not_called()


def test_review_and_pr_each_submit_a_fresh_compound_job(
    platform, snapshot, monkeypatch
):
    monkeypatch.setattr(
        "stacksmith_platform.inspection.InspectionQueue.__init__",
        lambda self, settings, database=None: None,
    )
    queued = Mock(
        return_value=json.dumps(
            {
                "source": snapshot["source"],
                "catalog": {"database": {"outputs": []}},
                "associations": {"rules": [], "applications": []},
                "timings": {},
            }
        )
    )
    monkeypatch.setattr(
        "stacksmith_platform.inspection.InspectionQueue.execute", queued
    )
    monkeypatch.setattr(
        "stacksmith_platform.adapter.StacksmithAdapter.author",
        Mock(side_effect=AssertionError("Review submitted a separate authoring job")),
    )
    monkeypatch.setattr(
        "stacksmith_platform.adapter.StacksmithAdapter.catalog",
        Mock(side_effect=AssertionError("Review submitted a separate catalog job")),
    )
    monkeypatch.setattr(
        "stacksmith_platform.adapter.StacksmithAdapter.associations",
        Mock(
            side_effect=AssertionError("Review submitted a separate associations job")
        ),
    )
    draft = {
        "snapshot": "signed",
        "environment": "dev",
        "components": {"database": {"type": "database"}},
    }
    assert platform.client.post("/api/review", json=draft).status_code == 200
    assert queued.call_count == 1
    assert queued.call_args.args[0] == ["review"]
    assert queued.call_args.kwargs["context"]["environment"] == "dev"
    # No diff prevents creating a PR, but inspection must still run again.
    assert platform.client.post("/api/pull-requests", json=draft).status_code == 422
    assert queued.call_count == 2


def test_environment_changes_during_inspection_invalidate_result(
    platform, snapshot, monkeypatch
):
    monkeypatch.setattr(
        "stacksmith_platform.adapter.StacksmithAdapter.review",
        Mock(
            return_value={
                "source": snapshot["source"],
                "catalog": {"database": {"outputs": []}},
                "associations": {"rules": []},
            }
        ),
    )
    monkeypatch.setattr(
        "stacksmith_platform.app._config_revision", Mock(side_effect=["changed"])
    )
    response = platform.client.post(
        "/api/review",
        json={
            "snapshot": "signed",
            "environment": "dev",
            "components": {"database": {"type": "database"}},
        },
    )
    assert response.status_code == 409


def test_inspection_credentials_follow_prepare_phase_bindings(platform):
    platform.settings.run_environment = {"GLOBAL_INPUT": "shared", "OVERRIDE": "global"}
    for name, phases, value in [
        ("OVERRIDE", ["prepare"], "environment"),
        ("APPLY_ONLY", ["apply"], "private-apply"),
    ]:
        assert (
            platform.client.post(
                "/api/v1/repositories/acme/service/environments/dev/secrets",
                json={"name": name, "phases": phases, "value": value},
            ).status_code
            == 200
        )
    with platform.database.transaction() as connection:
        revision = row(connection, environments, "acme/service:dev")["revision"]
    inspection = {
        "config": CONFIG,
        "context": {
            "actor": "admin",
            "repository": "acme/service",
            "environment": "dev",
            "environment_revision": revision,
        },
    }
    assert platform.worker._inspection_credentials(inspection) == {
        "GLOBAL_INPUT": "shared",
        "OVERRIDE": "environment",
    }
    inspection["context"]["environment_revision"] -= 1
    with pytest.raises(ComposerError, match="configuration changed"):
        platform.worker._inspection_credentials(inspection)


def test_preview_worker_checks_access_and_never_supplies_credentials(
    platform, monkeypatch
):
    _grant(platform, "viewer")
    credentials = Mock(
        side_effect=AssertionError("Preview requested deployment credentials")
    )
    monkeypatch.setattr(platform.worker, "_credential_snapshot", credentials)
    platform.settings.run_environment = {"GLOBAL_SECRET": "secret"}
    inspection = {
        "config": CONFIG,
        "context": {
            "actor": "operator",
            "repository": "acme/service",
            "environment": None,
        },
    }
    assert platform.worker._inspection_credentials(inspection) == {}
    credentials.assert_not_called()
    inspection["context"]["actor"] = "unassigned"
    with pytest.raises(ComposerError, match="role"):
        platform.worker._inspection_credentials(inspection)
    inspection["context"]["actor"] = "operator"
    inspection["config"] = "stale-config"
    with pytest.raises(ComposerError, match="configuration changed"):
        platform.worker._inspection_credentials(inspection)
