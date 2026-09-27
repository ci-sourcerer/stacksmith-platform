import json
import time
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from unittest.mock import MagicMock
from uuid import uuid4

import pytest
from cryptography.fernet import Fernet
from test_control import CONFIG, _complete, _create, _read
from test_execution_credential_bindings import BASE, _binding_platform

from stacksmith_platform.database import (
    environments,
    execution_credential_sessions,
    row,
    runs,
)
from stacksmith_platform.integrations import (
    ExecutionCredentialError,
    ExecutionCredentialLease,
)


@pytest.fixture(name="platform")
def _broker_platform(tmp_path, monkeypatch):
    yield from _binding_platform.__wrapped__(tmp_path, monkeypatch)


def _lease(suffix="first"):
    return ExecutionCredentialLease(
        values={
            "AWS_ACCESS_KEY_ID": f"access-{suffix}",
            "AWS_SECRET_ACCESS_KEY": f"secret-{suffix}",
            "AWS_SESSION_TOKEN": f"session-{suffix}",
        },
        expires_at=datetime.now(UTC) + timedelta(hours=1),
    )


def _start(platform, claim=True):
    assert (
        platform.client.post(
            BASE, json={"provider": "one", "phases": ["prepare", "plan"]}
        ).status_code
        == 200
    )
    identifier = _create(platform).json()["id"]
    platform.worker.tick()
    run = _read(platform, identifier)
    payload = json.loads(
        Fernet(platform.database.decrypt(run["execution"]["key"]).encode()).decrypt(
            platform.storage.get(run["execution"]["input"])
        )
    )
    if claim:
        assert (
            platform.client.post(
                payload["claim_url"],
                headers={"Authorization": f"Bearer {payload['claim_token']}"},
            ).status_code
            == 200
        )
    platform.app.state.execution_credential_catalog.providers["one"].issue = MagicMock(
        return_value=_lease()
    )
    return identifier, payload


def _refresh(platform, payload, token=None):
    return platform.client.post(
        payload["credential_authorization"]["url"],
        headers={
            "Authorization": f"Bearer {token or payload['credential_authorization']['token']}"
        },
    )


def test_credentials_require_distinct_capability_and_consumed_claim(platform):
    _, payload = _start(platform, claim=False)
    assert _refresh(platform, payload).status_code == 403
    assert (
        platform.client.post(
            payload["claim_url"],
            headers={"Authorization": f"Bearer {payload['claim_token']}"},
        ).status_code
        == 200
    )
    assert _refresh(platform, payload, token=payload["claim_token"]).status_code == 403
    assert _refresh(platform, payload, token="invalid").status_code == 403
    response = _refresh(platform, payload)
    assert response.status_code == 200
    assert response.headers["cache-control"] == "no-store"
    assert response.json()["values"] == _lease().values


def test_credential_leases_are_cached_encrypted_refreshed_and_redacted(platform):
    identifier, payload = _start(platform)
    _, fingerprint = platform.worker._credential_snapshot(
        _read(platform, identifier), "prepare"
    )
    assert _refresh(platform, payload).status_code == 200
    assert _refresh(platform, payload).status_code == 200
    platform.app.state.execution_credential_catalog.providers[
        "one"
    ].issue.assert_called_once()
    with platform.database.transaction() as connection:
        session = row(connection, execution_credential_sessions, f"{identifier}:0")
        assert "secret-first" not in str(dict(session))
        cached = json.loads(platform.database.decrypt(session["lease"]))
        cached["refresh_at"] = 0
        connection.execute(
            execution_credential_sessions.update()
            .where(execution_credential_sessions.c.id == session["id"])
            .values(lease=platform.database.encrypt(json.dumps(cached)))
        )
    platform.app.state.execution_credential_catalog.providers[
        "one"
    ].issue.return_value = _lease("second")
    assert _refresh(platform, payload).json()["values"] == _lease("second").values
    assert (
        platform.worker._credential_snapshot(_read(platform, identifier), "prepare")[1]
        == fingerprint
    )
    run = _read(platform, identifier)
    platform.driver.executions[run["execution"]["name"]]["logs"] = (
        f"secret-first secret-second {payload['credential_authorization']['token']}"
    )
    platform.worker.tick()
    assert _read(platform, identifier)["result"]["logs"].count("<redacted>") == 3
    _complete(
        platform,
        identifier,
        claim=False,
        report={"plan-summary.json": {"value": "secret-second"}},
    )
    assert "secret-second" not in str(_read(platform, identifier)["result"])
    # A completed phase cannot keep using its capability, even before the next launch.
    assert _refresh(platform, payload).status_code == 403


@pytest.mark.parametrize(
    "change",
    [
        "cancel",
        "finish",
        "phase",
        "fence",
        "deadline",
        "binding",
        "provider",
        "configuration",
        "permission",
    ],
)
def test_refresh_rejects_stale_or_revoked_execution_access(platform, change):
    identifier, payload = _start(platform)
    with platform.database.transaction() as connection:
        if change in {"cancel", "finish", "phase", "fence"}:
            connection.execute(
                runs.update()
                .where(runs.c.id == identifier)
                .values(
                    **{
                        "cancel": {"status": "cancelling"},
                        "finish": {"status": "succeeded"},
                        "phase": {"phase": 1},
                        "fence": {"fence": 999},
                    }[change]
                )
            )
        if change == "deadline":
            connection.execute(
                execution_credential_sessions.update()
                .where(execution_credential_sessions.c.run == identifier)
                .values(expires=time.time() - 1)
            )
    if change == "binding":
        assert platform.client.post(f"{BASE}/one/remove").status_code == 200
    if change == "provider":
        platform.app.state.execution_credential_catalog.providers.clear()
    if change == "configuration":
        platform.app.state.execution_credential_catalog.definitions["one"].options[
            "role_arn"
        ] = "arn:aws:iam::123456789012:role/changed"
    if change == "permission":
        platform.settings.admin_logins = []
    assert _refresh(platform, payload).status_code in {403, 409}


def test_refresh_failures_are_sanitized(platform):
    _, payload = _start(platform)
    platform.app.state.execution_credential_catalog.providers[
        "one"
    ].issue.side_effect = ExecutionCredentialError("private-provider-details")
    response = _refresh(platform, payload)
    assert response.status_code == 503
    assert "private-provider-details" not in response.text


def test_refresh_rechecks_deadline_after_issuance(platform, monkeypatch):
    identifier, payload = _start(platform)
    with platform.database.transaction() as connection:
        deadline = row(connection, execution_credential_sessions, f"{identifier}:0")[
            "expires"
        ]
    monkeypatch.setattr(
        "stacksmith_platform.credential_broker.time",
        SimpleNamespace(time=MagicMock(side_effect=[deadline - 1, deadline + 1])),
    )
    monkeypatch.setattr(
        "stacksmith_platform.credential_broker._issue", MagicMock(return_value={})
    )
    assert _refresh(platform, payload).status_code == 403


def test_private_metadata_expiration_removes_cached_credentials(platform):
    identifier, payload = _start(platform)
    assert _refresh(platform, payload).status_code == 200
    with platform.database.transaction() as connection:
        connection.execute(
            runs.update()
            .where(runs.c.id == identifier)
            .values(
                status="succeeded",
                updated=time.time() - platform.settings.approval_ttl - 1,
            )
        )
    platform.worker.maintenance()
    with platform.database.transaction() as connection:
        assert row(connection, execution_credential_sessions, f"{identifier}:0") is None


def test_provider_binding_rejects_static_identity_overrides(platform):
    platform.settings.run_environment = {"AWS_ACCESS_KEY_ID": "global-key"}
    assert (
        platform.client.post(
            BASE, json={"provider": "one", "phases": ["plan"]}
        ).status_code
        == 422
    )
    platform.settings.run_environment = {}
    assert (
        platform.client.post(
            BASE, json={"provider": "one", "phases": ["plan"]}
        ).status_code
        == 200
    )
    assert (
        platform.client.post(
            BASE.removesuffix("execution-credential-bindings") + "secrets",
            json={"name": "AWS_PROFILE", "phases": ["plan"], "value": "override"},
        ).status_code
        == 422
    )


@pytest.mark.parametrize("deployment", [True, False])
def test_inspection_uses_prepare_identity_and_redacts_issued_credentials(
    platform, deployment
):
    assert (
        platform.client.post(
            BASE, json={"provider": "one", "phases": ["prepare"]}
        ).status_code
        == 200
    )
    identifier = uuid4().hex
    with platform.database.transaction() as connection:
        connection.execute(
            runs.insert().values(
                id=identifier,
                repository="inspection",
                environment="inspection",
                revision="0" * 40,
                action="inspection",
                actor="authoring",
                status="queued",
                phase=0,
                fence=0,
                lock_key="",
                dedup=identifier,
                created=time.time(),
                updated=time.time(),
                expires=time.time() + 3600,
                spec={
                    "image": platform.settings.execution_image,
                    "phases": ["inspect"],
                    "_inspection": platform.database.encrypt(
                        json.dumps(
                            {
                                "args": ["review"],
                                "source": "components: {}",
                                "github_token": "github-token",
                                "authoring_request": {},
                                "config": CONFIG,
                                "config_source": None,
                                "context": {
                                    "repository": "acme/service",
                                    "environment": "dev" if deployment else None,
                                    "actor": "admin",
                                    "environment_revision": row(
                                        connection, environments, "acme/service:dev"
                                    )["revision"],
                                },
                            }
                        )
                    ),
                },
                result={},
            )
        )
    platform.worker.tick()
    run = _read(platform, identifier)
    cipher = Fernet(platform.database.decrypt(run["execution"]["key"]).encode())
    payload = json.loads(
        cipher.decrypt(platform.storage.get(run["execution"]["input"]))
    )
    if not deployment:
        assert payload["credential_authorization"] is None
        assert payload["inspection"]["credentials"] == {}
        return
    assert (
        platform.client.post(
            payload["claim_url"],
            headers={"Authorization": f"Bearer {payload['claim_token']}"},
        ).status_code
        == 200
    )
    platform.app.state.execution_credential_catalog.providers["one"].issue = MagicMock(
        return_value=_lease()
    )
    assert _refresh(platform, payload).status_code == 200
    platform.storage.put(
        run["execution"]["result"],
        cipher.encrypt(
            json.dumps(
                {
                    "exit_code": 1,
                    "detail": "secret-first",
                    "diagnostics": {"stderr": "secret-first"},
                }
            ).encode()
        ),
    )
    platform.driver.executions[run["execution"]["name"]]["state"] = "succeeded"
    platform.worker.tick()
    result = json.loads(
        platform.database.decrypt(
            _read(platform, identifier)["result"]["_inspection_result"]
        )
    )
    assert "secret-first" not in str(result)
    assert result["detail"] == "<redacted>"
    assert _refresh(platform, payload).status_code == 403
