import pytest
from test_access import _grant
from test_control import _complete, _create, _login, _read, _to_approval
from test_control import platform as _platform_fixture

from stacksmith_platform.database import environments, row
from stacksmith_platform.execution_credentials import ExecutionCredentialCatalog
from stacksmith_platform.integrations import SecretSourceError
from stacksmith_platform.settings import SourceDefinition

BASE = (
    "/api/v1/repositories/acme/service/environments/dev/execution-credential-bindings"
)


@pytest.fixture(name="platform")
def _binding_platform(tmp_path, monkeypatch):
    for platform in _platform_fixture.__wrapped__(tmp_path, monkeypatch):
        platform.settings.execution_credential_providers = {
            name: SourceDefinition(
                provider="aws-assume-role",
                options={
                    "region": "us-east-1",
                    "role_arn": f"arn:aws:iam::123456789012:role/{name}",
                    "external_id": "private-value",
                },
            )
            for name in ("one", "two")
        }
        platform.app.state.execution_credential_catalog = ExecutionCredentialCatalog(
            platform.settings
        )
        platform.worker.execution_credential_catalog = ExecutionCredentialCatalog(
            platform.settings
        )
        yield platform


def _revision(platform):
    with platform.database.transaction() as connection:
        return row(connection, environments, "acme/service:dev")["revision"]


def test_bind_list_replace_and_remove_invalidate_approvals(platform):
    revision = _revision(platform)
    assert platform.client.post(
        BASE, json={"provider": "one", "phases": ["plan", "prepare", "plan"]}
    ).json() == {"provider": "one", "phases": ["plan", "prepare"]}
    assert _revision(platform) == revision + 1
    assert platform.client.get(BASE).json() == {
        "bindings": [{"provider": "one", "phases": ["plan", "prepare"]}]
    }
    assert (
        platform.client.post(
            BASE, json={"provider": "one", "phases": ["apply"]}
        ).status_code
        == 200
    )
    assert _revision(platform) == revision + 2
    assert platform.client.post(f"{BASE}/one/remove").status_code == 200
    assert _revision(platform) == revision + 3
    assert platform.client.get(BASE).json() == {"bindings": []}
    assert platform.client.post(f"{BASE}/one/remove").status_code == 200
    assert _revision(platform) == revision + 3


def test_binding_rejects_unknown_and_overlapping_providers(platform):
    assert (
        platform.client.post(
            BASE, json={"provider": "missing", "phases": ["plan"]}
        ).status_code
        == 422
    )
    assert (
        platform.client.post(BASE, json={"provider": "one", "phases": []}).status_code
        == 422
    )
    assert (
        platform.client.post(
            BASE, json={"provider": "one", "phases": ["plan"]}
        ).status_code
        == 200
    )
    assert (
        platform.client.post(
            BASE, json={"provider": "two", "phases": ["plan", "apply"]}
        ).status_code
        == 422
    )
    assert (
        platform.client.post(
            BASE, json={"provider": "two", "phases": ["apply"]}
        ).status_code
        == 200
    )


@pytest.mark.parametrize("role", ["viewer", "runner"])
def test_binding_requires_credential_manager(platform, role):
    _grant(
        platform,
        role,
        () if role == "viewer" else ("infrastructure",),
        environment="dev",
    )
    _login(platform, "operator")
    assert platform.client.get(BASE).status_code == 403
    assert (
        platform.client.post(
            BASE, json={"provider": "one", "phases": ["plan"]}
        ).status_code
        == 403
    )
    assert platform.client.post(f"{BASE}/one/remove").status_code == 403


def test_provider_changes_affect_fingerprint(platform):
    assert (
        platform.client.post(
            BASE, json={"provider": "one", "phases": ["plan"]}
        ).status_code
        == 200
    )
    run = {"repository": "acme/service", "environment": "dev"}
    assert platform.worker._credential_snapshot(run, "plan")[0] == {}
    _, previous = platform.worker._credential_snapshot(run, "prepare")
    platform.settings.execution_credential_providers["one"].options["role_arn"] = (
        "arn:aws:iam::123456789012:role/changed"
    )
    _, changed = platform.worker._credential_snapshot(run, "prepare")
    assert previous != changed
    assert "private-value" not in changed
    del platform.settings.execution_credential_providers["one"]
    with pytest.raises(SecretSourceError, match="unavailable"):
        platform.worker._credential_snapshot(run, "prepare")


def test_bound_phase_receives_refresh_authorization(platform):
    assert (
        platform.client.post(
            BASE, json={"provider": "one", "phases": ["prepare"]}
        ).status_code
        == 200
    )
    identifier = _create(platform).json()["id"]
    platform.worker.tick()
    assert _read(platform, identifier)["status"] == "running"
    payload = _complete(platform, identifier)
    assert payload["credential_authorization"]["protocol"] == "aws-container"
    assert payload["credential_authorization"]["token"] != payload["claim_token"]
    assert payload["credentials"] == {}


def test_changed_provider_configuration_stops_in_flight_run(platform):
    assert (
        platform.client.post(
            BASE, json={"provider": "one", "phases": ["apply"]}
        ).status_code
        == 200
    )
    identifier = _create(platform).json()["id"]
    platform.worker.tick()
    _complete(platform, identifier)
    platform.settings.execution_credential_providers["one"].options["role_arn"] = (
        "arn:aws:iam::123456789012:role/changed"
    )
    platform.worker.tick()
    assert _read(platform, identifier)["status"] == "failed"
    assert platform.driver.launches == 1


def test_new_binding_invalidates_pending_deployment_approval(platform):
    identifier = _create(platform).json()["id"]
    _to_approval(platform, identifier)
    assert (
        platform.client.post(
            BASE, json={"provider": "one", "phases": ["apply"]}
        ).status_code
        == 200
    )
    assert platform.client.post(f"/api/v1/runs/{identifier}/approve").status_code == 409
