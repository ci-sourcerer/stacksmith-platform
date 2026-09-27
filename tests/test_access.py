import hashlib
import hmac
import json

import pytest
from test_control import (
    _complete,
    _create,
    _login,
    _read,
    _to_approval,
)
from test_control import platform as _platform_fixture

from stacksmith_platform.database import audits, runs


@pytest.fixture(name="platform")
def _role_platform(tmp_path, monkeypatch):
    yield from _platform_fixture.__wrapped__(tmp_path, monkeypatch)


def _grant(platform, role, workloads=(), environment="*", login="operator"):
    _login(platform, "admin")
    response = platform.client.post(
        "/api/v1/repositories/acme/service/grants",
        json={
            "login": login,
            "environment": environment,
            "role": role,
            "workloads": list(workloads),
        },
    )
    assert response.status_code == 200, response.text
    return response.json()["id"]


def _request(platform, workload, apply=False, key="role-test"):
    return _create(
        platform,
        action=("apply" if apply else "plan")
        if workload == "infrastructure"
        else ("apply-operation" if apply else "plan-operation"),
        key=key,
        include_infrastructure=workload == "infrastructure",
        include_operations=workload == "operations",
        apply_after_plan=apply,
        destroy_plan=False,
    )


@pytest.mark.parametrize("role", ["viewer", "runner", "provisioner"])
@pytest.mark.parametrize("assigned", ["infrastructure", "operations"])
@pytest.mark.parametrize("requested", ["infrastructure", "operations"])
@pytest.mark.parametrize("apply", [False, True])
def test_role_workload_execution_matrix(platform, role, assigned, requested, apply):
    _grant(platform, role, () if role == "viewer" else (assigned,))
    _login(platform, "operator")
    assert (
        platform.client.get(
            "/api/v1/runs?repository=acme/service&environment=dev"
        ).status_code
        == 200
    )
    response = _request(platform, requested, apply)
    assert response.status_code == (
        200 if role != "viewer" and assigned == requested else 403
    )


def test_additive_roles_do_not_promote_runner_workload_to_approver(platform):
    _grant(platform, "provisioner", ("operations",))
    _grant(platform, "runner", ("infrastructure",), environment="dev")
    _login(platform, "operator")
    access = platform.client.get(
        "/api/v1/repositories/acme/service/access?environment=dev"
    ).json()
    assert "execute_infrastructure" in access["capabilities"]
    assert "approve_operations" in access["capabilities"]
    assert "approve_infrastructure" not in access["capabilities"]
    assert "author" in access["capabilities"]


def test_environment_provisioner_cannot_change_shared_settings_or_roles(platform):
    _grant(platform, "provisioner", ("infrastructure",), environment="dev")
    _login(platform, "operator")
    access = platform.client.get(
        "/api/v1/repositories/acme/service/access?environment=dev"
    ).json()
    assert "configure_environment" in access["capabilities"]
    assert "manage_credentials" in access["capabilities"]
    assert "configure_repository" not in access["capabilities"]
    assert "author" not in access["capabilities"]
    assert (
        platform.client.post(
            "/api/v1/repositories/acme/service/configuration",
            json={"mode": "inherit", "refs": []},
        ).status_code
        == 403
    )
    assert (
        platform.client.get("/api/v1/repositories/acme/service/grants").status_code
        == 403
    )
    assert (
        platform.client.post(
            "/api/v1/repositories/acme/service/grants",
            json={"login": "other", "role": "viewer", "workloads": []},
        ).status_code
        == 403
    )
    assert (
        platform.client.get(
            "/api/v1/repositories/acme/service/access?environment=prod"
        ).status_code
        == 403
    )


def test_runner_cannot_approve_but_provisioner_can_self_approve(platform):
    _grant(platform, "runner", ("infrastructure",))
    _login(platform, "operator")
    identifier = _request(platform, "infrastructure", True).json()["id"]
    for _ in range(2):
        platform.worker.tick()
        _complete(platform, identifier)
    platform.worker.tick()
    assert _read(platform, identifier)["status"] == "awaiting_approval"
    assert (
        platform.client.post(f"/api/v1/runs/{identifier}/approve", json={}).status_code
        == 403
    )
    _grant(platform, "provisioner", ("infrastructure",))
    _login(platform, "operator")
    assert (
        platform.client.post(f"/api/v1/runs/{identifier}/approve", json={}).status_code
        == 200
    )
    platform.worker.tick()
    assert _read(platform, identifier)["status"] == "running"


def test_revocation_finishes_launched_phase_and_stops_next_phase(platform):
    grant = _grant(platform, "runner", ("operations",))
    _login(platform, "operator")
    identifier = _request(platform, "operations").json()["id"]
    platform.worker.tick()
    assert platform.driver.launches == 1
    _login(platform, "admin")
    assert (
        platform.client.delete(
            f"/api/v1/repositories/acme/service/grants/{grant}"
        ).status_code
        == 200
    )
    _complete(platform, identifier)
    assert _read(platform, identifier)["status"] == "queued"
    platform.worker.tick()
    assert _read(platform, identifier)["status"] == "cancelled"
    assert platform.driver.launches == 1
    assert (
        "Role or workload access changed"
        in _read(platform, identifier)["result"]["message"]
    )


def test_revoking_approver_stops_approved_mutation(platform):
    grant = _grant(platform, "provisioner", ("infrastructure", "operations"))
    _login(platform, "admin")
    identifier = _create(platform).json()["id"]
    _to_approval(platform, identifier)
    _login(platform, "operator")
    assert (
        platform.client.post(f"/api/v1/runs/{identifier}/approve", json={}).status_code
        == 200
    )
    _login(platform, "admin")
    assert (
        platform.client.delete(
            f"/api/v1/repositories/acme/service/grants/{grant}"
        ).status_code
        == 200
    )
    platform.worker.tick()
    assert _read(platform, identifier)["status"] == "cancelled"
    assert platform.driver.launches == 3


def test_token_is_narrowed_by_workload_and_live_roles(platform):
    grant = _grant(platform, "runner", ("operations",))
    _login(platform, "operator")
    denied = platform.client.post(
        "/api/v1/tokens",
        json={
            "name": "infra",
            "repository": "acme/service",
            "environment": "dev",
            "permissions": ["view", "deploy"],
            "workloads": ["infrastructure"],
        },
    )
    assert denied.status_code == 403
    issued = platform.client.post(
        "/api/v1/tokens",
        json={
            "name": "ops",
            "repository": "acme/service",
            "environment": "dev",
            "permissions": ["view", "deploy"],
            "workloads": ["operations"],
        },
    ).json()
    _login(platform, "admin")
    platform.client.delete(f"/api/v1/repositories/acme/service/grants/{grant}")
    response = platform.client.get(
        "/api/v1/runs?repository=acme/service&environment=dev",
        headers={"Authorization": f"Bearer {issued['token']}"},
    )
    assert response.status_code == 403


def test_webhook_sender_cannot_bypass_role_checks(platform):
    body = json.dumps(
        {
            "repository": {"full_name": "acme/service"},
            "installation": {"id": 1},
            "ref": "refs/heads/main",
            "after": "a" * 40,
            "sender": {"login": "outsider"},
        }
    ).encode()
    signature = hmac.new(b"webhook-secret", body, hashlib.sha256).hexdigest()
    response = platform.client.post(
        "/api/v1/webhooks/github",
        content=body,
        headers={
            "X-GitHub-Event": "push",
            "X-GitHub-Delivery": "unauthorized-role",
            "X-Hub-Signature-256": f"sha256={signature}",
        },
    )
    assert response.status_code == 200
    with platform.database.transaction() as connection:
        assert (
            connection.execute(
                audits.select().where(audits.c.action == "webhook.access_denied")
            ).first()
            is not None
        )


def test_mixed_workload_request_requires_both_permissions(platform):
    _grant(platform, "runner", ("operations",))
    _login(platform, "operator")
    assert _create(platform, action="plan").status_code == 403


@pytest.mark.parametrize("scope, expected", [("dev", 403), ("*", 200)])
def test_composer_write_requires_repository_wide_provisioner(
    platform, monkeypatch, scope, expected
):
    import stacksmith_platform.app as app_module

    _grant(platform, "provisioner", ("infrastructure",), environment=scope)
    _login(platform, "operator")
    monkeypatch.setattr(
        app_module,
        "_snapshot",
        lambda *args: {
            "repository": "acme/service",
            "source": "name: original\ncomponents: {}\n",
            "path": "stack.yaml",
        },
    )
    monkeypatch.setattr(
        platform.app.state.adapter,
        "author",
        lambda *args, **kwargs: {"source": "name: changed\ncomponents: {}\n"},
    )
    response = platform.client.post(
        "/api/source", json={"snapshot": "signed", "components": {}}
    )
    assert response.status_code == expected, response.text


def test_viewer_can_inspect_original_source_but_cannot_submit_changes(
    platform, monkeypatch
):
    import stacksmith_platform.app as app_module

    _grant(platform, "viewer")
    _login(platform, "operator")
    monkeypatch.setattr(
        app_module,
        "_snapshot",
        lambda *args: {
            "repository": "acme/service",
            "source": "name: original\ncomponents: {}\n",
            "path": "stack.yaml",
        },
    )
    monkeypatch.setattr(
        platform.app.state.adapter,
        "author",
        lambda *args, **kwargs: {"source": "name: original\ncomponents: {}\n"},
    )
    assert (
        platform.client.post(
            "/api/source", json={"snapshot": "signed", "components": {}}
        ).status_code
        == 200
    )
    monkeypatch.setattr(
        platform.app.state.adapter,
        "author",
        lambda *args, **kwargs: {"source": "name: changed\ncomponents: {}\n"},
    )
    assert (
        platform.client.post(
            "/api/source", json={"snapshot": "signed", "components": {}}
        ).status_code
        == 403
    )


def test_viewer_changes_are_denied_before_authoring_executes(platform, monkeypatch):
    from unittest.mock import Mock

    import stacksmith_platform.app as app_module

    _grant(platform, "viewer")
    _login(platform, "operator")
    monkeypatch.setattr(
        app_module,
        "_snapshot",
        lambda *args: {
            "repository": "acme/service",
            "source": "name: original\ncomponents: {}\n",
            "path": "stack.yaml",
        },
    )
    author = Mock(
        side_effect=AssertionError("Viewer changes reached the authoring runtime")
    )
    monkeypatch.setattr(platform.app.state.adapter, "author", author)
    response = platform.client.post(
        "/api/source",
        json={"snapshot": "signed", "components": {"changed": {"type": "aws"}}},
    )
    assert response.status_code == 403
    author.assert_not_called()


@pytest.mark.parametrize("execution_enabled, expected", [(True, 503), (False, 200)])
def test_readiness_detects_missing_role_table_privileges(
    platform, monkeypatch, execution_enabled, expected
):
    from unittest.mock import MagicMock

    from sqlalchemy.exc import SQLAlchemyError

    assert platform.client.get("/api/ready").status_code == 200
    connection = MagicMock()
    connection.execute.side_effect = [
        None,
        SQLAlchemyError("permission denied for table role_assignments"),
    ]
    database = MagicMock()
    database.transaction.return_value.__enter__.return_value = connection
    with monkeypatch.context() as patch:
        patch.setitem(platform.app.state._state, "database", database)
        patch.setattr(platform.settings, "execution_enabled", execution_enabled)
        assert platform.client.get("/api/ready").status_code == expected


def test_secret_reference_catalog_exposes_only_sorted_names(platform):
    platform.settings.secret_references = {
        "production/aws": "never-disclose-this-value",
        "development/aws": "another-private-value",
    }
    response = platform.client.get(
        "/api/v1/repositories/acme/service/environments/dev/secret-references"
    )
    assert response.status_code == 200
    assert response.json() == {
        "references": [
            {
                "reference": name,
                "name": name,
                "source": "configured",
                "supports_json_key": False,
            }
            for name in ["development/aws", "production/aws"]
        ]
    }
    assert "never-disclose" not in response.text
    assert "another-private-value" not in response.text
    platform.settings.secret_references = {}
    assert platform.client.get(
        "/api/v1/repositories/acme/service/environments/dev/secret-references"
    ).json() == {"references": []}
    assert (
        platform.client.get(
            "/api/v1/repositories/acme/service/environments/missing/secret-references"
        ).status_code
        == 404
    )


@pytest.mark.parametrize(
    ("role", "expected"),
    [("provisioner", 200), ("runner", 403), ("viewer", 403)],
)
def test_secret_reference_catalog_requires_credential_management(
    platform, role, expected
):
    _grant(
        platform,
        role,
        () if role == "viewer" else ("infrastructure",),
        environment="dev",
    )
    _login(platform, "operator")
    assert (
        platform.client.get(
            "/api/v1/repositories/acme/service/environments/dev/secret-references"
        ).status_code
        == expected
    )


def test_secret_reference_catalog_respects_environment_scope(platform):
    assert (
        platform.client.post(
            "/api/v1/repositories/acme/service/environments", json={"name": "other"}
        ).status_code
        == 200
    )
    _grant(platform, "provisioner", ("infrastructure",), environment="other")
    _login(platform, "operator")
    assert (
        platform.client.get(
            "/api/v1/repositories/acme/service/environments/dev/secret-references"
        ).status_code
        == 403
    )


@pytest.mark.parametrize(
    ("role", "expected"),
    [("provisioner", 200), ("runner", 403), ("viewer", 403)],
)
def test_execution_credential_catalog_requires_credential_management(
    platform, role, expected
):
    _grant(
        platform,
        role,
        () if role == "viewer" else ("infrastructure",),
        environment="dev",
    )
    _login(platform, "operator")
    assert (
        platform.client.get(
            "/api/v1/repositories/acme/service/environments/dev/execution-credential-providers"
        ).status_code
        == expected
    )


def test_execution_credential_catalog_does_not_expose_options(platform):
    from stacksmith_platform.execution_credentials import ExecutionCredentialCatalog
    from stacksmith_platform.settings import SourceDefinition

    platform.settings.execution_credential_providers = {
        "production": SourceDefinition(
            provider="aws-assume-role",
            options={
                "region": "us-east-1",
                "role_arn": "arn:aws:iam::123456789012:role/deploy",
                "external_id": "private-external-id",
            },
        )
    }
    platform.app.state.execution_credential_catalog = ExecutionCredentialCatalog(
        platform.settings
    )
    response = platform.client.get(
        "/api/v1/repositories/acme/service/environments/dev/execution-credential-providers"
    )
    assert response.status_code == 200
    assert response.json() == {
        "providers": [{"name": "production", "provider": "aws-assume-role"}]
    }
    assert "private-external-id" not in response.text


def test_removed_secret_reference_cannot_be_bound(platform):
    platform.settings.secret_references = {}
    response = platform.client.post(
        "/api/v1/repositories/acme/service/environments/dev/secrets",
        json={
            "name": "AWS_ACCESS_KEY_ID",
            "phases": ["plan"],
            "reference": "plan-credential",
        },
    )
    assert response.status_code == 422


def test_api_tokens_cannot_discover_secret_references(platform):
    issued = platform.client.post(
        "/api/v1/tokens",
        json={
            "name": "catalog-test",
            "repository": "acme/service",
            "environment": "dev",
            "permissions": ["view"],
            "workloads": ["infrastructure"],
        },
    )
    assert issued.status_code == 200
    assert (
        platform.client.get(
            "/api/v1/repositories/acme/service/environments/dev/secret-references",
            headers={"Authorization": f"Bearer {issued.json()['token']}"},
        ).status_code
        == 403
    )


def _history_runs(platform):
    identifiers = []
    for index, environment in enumerate(["dev", "prod", "dev", "prod"]):
        identifiers.append(
            _create(platform, action="plan", key=f"history-{index}").json()["id"]
        )
        with platform.database.transaction() as connection:
            connection.execute(
                runs.update()
                .where(runs.c.id == identifiers[-1])
                .values(environment=environment, created=100)
            )
    return identifiers


def test_repository_history_filters_before_pagination(platform):
    identifiers = _history_runs(platform)
    _grant(platform, "viewer", environment="dev")
    _login(platform, "operator")
    pages = [
        platform.client.get(
            f"/api/v1/runs?repository=acme/service&page_size=1&page={page}"
        )
        for page in (1, 2, 3)
    ]
    assert all(response.status_code == 200 for response in pages)
    assert [response.json()["total"] for response in pages] == [2, 2, 2]
    assert {item["id"] for response in pages for item in response.json()["items"]} == {
        identifiers[0],
        identifiers[2],
    }
    assert (
        platform.client.get(
            "/api/v1/runs?repository=acme/service&environment=prod"
        ).status_code
        == 403
    )
    _login(platform, "stranger")
    assert (
        platform.client.get("/api/v1/runs?repository=acme/service").status_code == 403
    )


def test_repository_history_admin_and_token_scope(platform):
    assert (
        platform.client.get("/api/v1/runs?repository=acme/service").json()["total"] == 0
    )
    identifiers = _history_runs(platform)
    response = platform.client.get("/api/v1/runs?repository=acme/service").json()
    assert response["total"] == 4
    assert [item["id"] for item in response["items"]] == sorted(
        identifiers, reverse=True
    )
    assert (
        platform.client.get(
            "/api/v1/runs?repository=acme/service&environment=prod"
        ).json()["total"]
        == 2
    )
    issued = platform.client.post(
        "/api/v1/tokens",
        json={
            "name": "history",
            "repository": "acme/service",
            "environment": "dev",
            "permissions": ["view"],
        },
    ).json()
    response = platform.client.get(
        "/api/v1/runs?repository=acme/service",
        headers={"Authorization": f"Bearer {issued['token']}"},
    )
    assert response.status_code == 200
    assert response.json()["total"] == 2
    assert {item["environment"] for item in response.json()["items"]} == {"dev"}
