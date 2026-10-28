import hashlib
import hmac
import json
import os
import time
from functools import partial
from types import SimpleNamespace

import pytest
from botocore.exceptions import ClientError
from cryptography.fernet import Fernet
from fastapi.testclient import TestClient
from sqlalchemy import event

from stacksmith_platform.app import create_app
from stacksmith_platform.database import (
    Database,
    artifacts,
    audit,
    audits,
    environments,
    locks,
    metadata,
    phase_claims,
    row,
    runs,
    secrets,
    tokens,
)
from stacksmith_platform.lifecycle import phases
from stacksmith_platform.settings import Settings
from stacksmith_platform.worker import Worker

SHA = "a" * 40
OTHER_SHA = "b" * 40
CONFIG = "git+https://github.com/acme/config//config.yaml@" + SHA


class MemoryStorage:
    """Private storage test adapter with scoped key URLs."""

    def __init__(self):
        self.objects = {}

    def put(self, key, content):
        self.objects[key] = content
        return hashlib.sha256(content).hexdigest()

    def get(self, key):
        if key not in self.objects:
            raise ClientError({"Error": {"Code": "NoSuchKey"}}, "GetObject")
        return self.objects[key]

    def digest(self, key):
        return hashlib.sha256(self.get(key)).hexdigest()

    def url(self, key, method, lifetime=3600):
        return key

    def delete(self, key):
        self.objects.pop(key, None)


class ExecutionDriver:
    """Fake remote runtime preserving executions across worker restarts."""

    def __init__(self):
        self.executions = {}
        self.launches = 0

    def launch(self, name, image, environment, timeout):
        self.launches += 1
        assert name not in self.executions
        self.executions[name] = {
            "state": "running",
            "environment": environment,
            "logs": "",
        }

    def inspect(self, name):
        return self.executions.get(name, {}).get("state", "missing")

    def cleanup(self, name):
        self.executions.pop(name, None)

    def logs(self, name):
        return self.executions[name]["logs"]


class Provider:
    """Deterministic GitHub installation provider."""

    def __init__(self):
        self.head = SHA

    def revision(self, repository, reference):
        return {"sha": self.head if reference == "main" else reference}

    def repository_installation(self, repository):
        """Return the configured App's installation in the test fixture."""
        return 1

    def catalog(self):
        return []

    def installation_token(self, installation):
        return "installation-secret-token"

    def request(self, installation, method, path, **kwargs):
        return {"default_branch": "main"}


class UserGitHub:
    """User access checks kept separate from installation access."""

    def __init__(self, token, transport):
        self.token = token

    def _repository(self, owner, repo):
        from stacksmith_platform.errors import ComposerError

        if self.token == "denied":
            raise ComposerError("GitHub access was removed", 403)
        return {
            "default_branch": "main",
            "topics": ["stacksmith-stack"],
            "custom_properties": {"stacksmith": "stack"},
        }

    def repositories(self):
        return [
            {
                "name": "acme/service",
                "github_id": "1",
                "installation_id": 1,
                "default_branch": "main",
                "topics": ["stacksmith-stack"],
                "custom_properties": {"stacksmith": "stack"},
            }
        ]


@pytest.fixture
def platform(tmp_path, monkeypatch):
    (tmp_path / "config.yaml").write_text("backend: {}\n")
    settings = Settings(
        _env_file=None,
        database=tmp_path / "platform.sqlite",
        secret_key="test-platform-secret-at-least-32-characters",
        config=tmp_path / "config.yaml",
    )
    app = create_app(settings)
    if os.environ.get("STACKSMITH_PLATFORM_TEST_DATABASE_URL"):
        app.state.database = Database(
            os.environ["STACKSMITH_PLATFORM_TEST_DATABASE_URL"], settings.secret_key
        )
        app.state.database.initialize()
        with app.state.database.transaction() as connection:
            for table in reversed(metadata.sorted_tables):
                connection.execute(table.delete())
        from stacksmith_platform.sessions import SessionStore

        app.state.sessions = SessionStore(app.state.database)
    settings.execution_enabled = True
    settings.admin_logins = ["admin"]
    settings.execution_image = "execution@sha256:" + "c" * 64
    settings.github_webhook_secret = "webhook-secret"
    settings.secret_references = {"plan-credential": "external-secret-value"}
    app.state.provider = Provider()
    app.state.artifacts = MemoryStorage()
    monkeypatch.setattr("stacksmith_platform.control.GitHubClient", UserGitHub)
    monkeypatch.setattr("stacksmith_platform.app.GitHubClient", UserGitHub)
    driver = ExecutionDriver()
    worker = Worker(
        settings, app.state.database, driver, app.state.artifacts, app.state.provider
    )
    with TestClient(app, headers={"X-Composer-Request": "1"}) as client:
        client.cookies.set(
            "platform_session", app.state.sessions.create("admin", "user-token")
        )
        with app.state.database.transaction() as connection:
            from stacksmith_platform.database import repositories

            connection.execute(
                repositories.insert().values(
                    id="acme/service",
                    installation=1,
                    branch="main",
                    config=CONFIG,
                    enabled=1,
                    deployment_enabled=1,
                )
            )
        assert (
            client.post(
                "/api/v1/repositories/acme/service/environments", json={"name": "dev"}
            ).status_code
            == 200
        )
        yield SimpleNamespace(
            app=app,
            client=client,
            database=app.state.database,
            settings=settings,
            driver=driver,
            storage=app.state.artifacts,
            worker=worker,
        )
    app.state.database.engine.dispose()


def test_environment_operations_inspects_current_default_branch(platform, monkeypatch):
    calls = []
    monkeypatch.setattr(
        "stacksmith_platform.inspection.InspectionQueue.__init__",
        lambda self, settings: None,
    )
    monkeypatch.setattr(
        "stacksmith_platform.inspection.InspectionQueue.execute",
        lambda self, *args: (
            calls.append(args)
            or json.dumps({"revision": SHA, "operations": [{"name": "deploy"}]})
        ),
    )
    response = platform.client.get(
        "/api/v1/repositories/acme/service/environments/dev/operations"
    )
    assert response.status_code == 200
    assert response.json()["operations"] == [{"name": "deploy"}]
    assert calls[0][0] == ["platform-operations"]
    source = json.loads(calls[0][1])
    assert source["repository"] == "acme/service"
    assert source["revision"] == SHA
    assert source["environment"] == "dev"
    assert source["settings"]["discovery_mode"] == "auto"
    assert calls[0][2] == "installation-secret-token"


def test_composer_discovery_does_not_require_platform_registration(
    platform, monkeypatch
):
    from stacksmith_platform.database import repositories

    monkeypatch.setattr("stacksmith_platform.app.GitHubClient", UserGitHub)
    with platform.database.transaction() as connection:
        connection.execute(repositories.delete())
    response = platform.client.get("/api/repository-discovery")
    assert response.status_code == 200
    assert response.json()["repositories"][0]["name"] == "acme/service"
    with platform.database.transaction() as connection:
        repository = row(connection, repositories, "acme/service")
        assert repository["deployment_enabled"] == 0
    monkeypatch.setattr(UserGitHub, "repositories", lambda self: [])
    assert platform.client.get("/api/repository-discovery").json() == {
        "repositories": [],
        "empty_reason": "github_access",
    }


def test_repository_list_applies_repository_discovery_filter(platform, monkeypatch):

    discovered = [
        {
            "name": "acme/service",
            "github_id": "1",
            "installation_id": 1,
            "default_branch": "main",
            "topics": ["stacksmith-stack"],
            "custom_properties": {},
        },
        {
            "name": "acme/stacksmith-example-aws-modules",
            "github_id": "2",
            "installation_id": 1,
            "default_branch": "main",
            "topics": ["terraform", "aws"],
            "custom_properties": {},
        },
    ]
    monkeypatch.setattr(UserGitHub, "repositories", lambda self: discovered)

    response = platform.client.get("/api/v1/repositories")

    assert response.status_code == 200
    assert [repository["id"] for repository in response.json()] == ["acme/service"]


def test_single_deployment_does_not_require_environment_names(platform):
    response = platform.client.post(
        "/api/v1/repositories/acme/service/environments",
        json={"discovery_mode": "single"},
    )
    assert response.status_code == 200
    assert response.json()["id"] == "acme/service:default"
    response = platform.client.post(
        "/api/v1/runs",
        headers={"Idempotency-Key": "single-deployment"},
        json={"repository": "acme/service", "revision": SHA, "action": "plan"},
    )
    assert response.status_code == 200
    assert response.json()["environment"] == "default"
    assert response.json()["spec"]["settings"]["discovery_mode"] == "single"
    listed = platform.client.get("/api/v1/runs", params={"repository": "acme/service"})
    assert listed.status_code == 200
    assert [item["environment"] for item in listed.json()["items"]] == ["default"]


def test_single_deployment_rejects_conflicting_environment_name(platform):
    assert (
        platform.client.post(
            "/api/v1/repositories/acme/service/environments",
            json={"name": "prod", "discovery_mode": "single"},
        ).status_code
        == 422
    )


def test_repository_deployment_discovery_and_reviewed_sync(platform, monkeypatch):
    calls = []
    detected = ["prod"]

    with platform.database.transaction() as connection:
        connection.execute(
            secrets.insert().values(
                id="acme/service:dev:PRESERVED",
                environment="acme/service:dev",
                name="PRESERVED",
                phases=["plan"],
                ciphertext="opaque",
            )
        )

    monkeypatch.setattr(
        "stacksmith_platform.inspection.InspectionQueue.__init__",
        lambda self, settings: None,
    )

    def discover(self, args, source, github_token, authoring_request, context=None):
        calls.append((args, json.loads(source), github_token))
        return json.dumps(
            {
                "deployments": [
                    {
                        "gitops_root": "",
                        "requested_discovery_mode": "auto",
                        "discovery_mode": "env-files",
                        "all_environments": list(detected),
                        "selected_environments": list(detected),
                        "common_runfile": "common/stacksmith.yaml",
                        "changed_paths": [],
                        "matrix": [
                            {
                                "environment": environment,
                                "runfile": "common/stacksmith.yaml",
                                "environment_runfile": f"environments/{environment}.yaml",
                            }
                            for environment in detected
                        ],
                        "source_lockfiles": {
                            "status": "missing",
                            "present": [],
                            "missing": ["common/stacksmith.lock.yaml"],
                            "missing_environments": list(detected),
                        },
                    }
                ]
            }
        )

    monkeypatch.setattr(
        "stacksmith_platform.inspection.InspectionQueue.execute", discover
    )
    response = platform.client.post(
        "/api/v1/repositories/acme/service/deployment-discovery", json={}
    )
    assert response.status_code == 200
    assert response.json()["revision"] == SHA
    assert response.json()["deployments"][0]["all_environments"] == ["prod"]
    assert calls[0][0] == ["platform-deployments"]
    assert calls[0][1] == {
        "repository": "acme/service",
        "revision": SHA,
        "discovery_mode": "auto",
    }
    response = platform.client.put(
        "/api/v1/repositories/acme/service/deployment-configuration",
        json={
            "revision": SHA,
            "gitops_root": "",
            "discovery_mode": "auto",
            "locked": False,
            "automatic": False,
            "deactivate_missing": False,
        },
    )
    assert response.status_code == 409
    with platform.database.transaction() as connection:
        assert row(connection, environments, "acme/service:dev")["active"] == 1
        assert row(connection, environments, "acme/service:prod") is None

    response = platform.client.put(
        "/api/v1/repositories/acme/service/deployment-configuration",
        json={
            "revision": SHA,
            "gitops_root": "",
            "discovery_mode": "auto",
            "locked": False,
            "automatic": False,
            "deactivate_missing": True,
        },
    )
    assert response.status_code == 200
    assert response.json()["added"] == ["prod"]
    assert response.json()["deactivated"] == ["dev"]
    with platform.database.transaction() as connection:
        assert row(connection, environments, "acme/service:dev")["active"] == 0
        prod = row(connection, environments, "acme/service:prod")
        assert prod["active"] == 1
        assert prod["settings"]["resolved_discovery_mode"] == "env-files"
    assert [
        item["name"]
        for item in platform.client.get(
            "/api/v1/repositories/acme/service/environments"
        ).json()
    ] == ["prod"]
    response = platform.client.post(
        "/api/v1/runs",
        headers={"Idempotency-Key": "inactive-environment"},
        json={
            "repository": "acme/service",
            "environment": "dev",
            "action": "plan",
        },
    )
    assert response.status_code == 404
    configuration = platform.client.get(
        "/api/v1/repositories/acme/service/deployment-configuration"
    ).json()
    assert configuration["deployment_enabled"] is True
    assert configuration["settings"]["automatic"] is False
    assert configuration["environments"] == [
        {"name": "dev", "active": False, "revision": 2},
        {"name": "prod", "active": True, "revision": 1},
    ]
    response = platform.client.put(
        "/api/v1/repositories/acme/service/deployment-configuration",
        json={
            "revision": SHA,
            "gitops_root": "",
            "discovery_mode": "auto",
            "locked": False,
            "automatic": False,
            "deactivate_missing": True,
        },
    )
    assert response.status_code == 200
    assert all(
        response.json()[change] == []
        for change in ("added", "reactivated", "updated", "deactivated")
    )

    detected.append("dev")
    response = platform.client.put(
        "/api/v1/repositories/acme/service/deployment-configuration",
        json={
            "revision": SHA,
            "gitops_root": "",
            "discovery_mode": "auto",
            "locked": False,
            "automatic": False,
            "deactivate_missing": False,
        },
    )
    assert response.status_code == 200
    assert response.json()["reactivated"] == ["dev"]
    with platform.database.transaction() as connection:
        assert row(connection, environments, "acme/service:dev")["active"] == 1
        assert row(connection, secrets, "acme/service:dev:PRESERVED") is not None


def test_deployment_sync_rejects_stale_revision_before_inspection(
    platform, monkeypatch
):
    monkeypatch.setattr(
        "stacksmith_platform.inspection.InspectionQueue.execute",
        lambda *args: (_ for _ in ()).throw(AssertionError("inspection started")),
    )
    response = platform.client.put(
        "/api/v1/repositories/acme/service/deployment-configuration",
        json={
            "revision": "b" * 40,
            "locked": False,
            "automatic": False,
        },
    )
    assert response.status_code == 409
    assert response.json()["detail"] == (
        "Repository changed. Detect deployments again."
    )


@pytest.mark.parametrize("root", ["/absolute", "../escape", "nested\\escape"])
def test_deployment_discovery_rejects_roots_outside_repository(platform, root):
    response = platform.client.post(
        "/api/v1/repositories/acme/service/deployment-discovery",
        json={"gitops_root": root},
    )

    assert response.status_code == 422


def test_platform_audit_filters_and_deterministic_pagination(platform):
    identifier = _create(platform, action="plan").json()["id"]
    with platform.database.transaction() as connection:
        connection.execute(audits.delete())
        for event_id, repository, actor in (
            ("a", "acme/service", "admin"),
            ("b", "acme/service", "admin"),
            ("c", "acme/other", "other"),
        ):
            connection.execute(
                audits.insert().values(
                    id=event_id,
                    repository=repository,
                    run=identifier,
                    actor=actor,
                    action="run.created",
                    detail={},
                    created=100,
                )
            )
    query = f"/api/v1/audit?repository=acme/service&run_id={identifier}&actor=admin&action=run.created&page_size=1"
    first = platform.client.get(query).json()
    assert first["total"] == 2
    assert [event["id"] for event in first["items"]] == ["b"]
    assert [
        event["id"] for event in platform.client.get(f"{query}&page=2").json()["items"]
    ] == ["a"]
    assert platform.client.get("/api/v1/audit?action=absent").json()["items"] == []
    assert platform.client.get("/api/v1/audit?page_size=101").status_code == 422


def test_audit_scope_is_recorded_without_secret_values(platform):
    identifier = _create(platform, action="plan").json()["id"]
    with platform.database.transaction() as connection:
        connection.execute(audits.delete())
        audit(connection, "admin", "test.run", identifier)
        audit(connection, "admin", "test.repository", repository="acme/service")
        audit(connection, "admin", "test.environment", environment="acme/service:dev")
        audit(connection, "admin", "test.configuration", scope="acme/service")
        audit(connection, "admin", "test.platform", scope="platform")
    events = {
        event["action"]: event
        for event in platform.client.get("/api/v1/audit").json()["items"]
    }
    assert all(
        events[action]["repository"] == "acme/service"
        for action in (
            "test.run",
            "test.repository",
            "test.environment",
            "test.configuration",
        )
    )
    assert events["test.platform"]["repository"] is None


def test_audit_requires_admin_browser_session(platform):
    identifier = _create(platform, action="plan").json()["id"]
    assert platform.client.get("/api/v1/audit").status_code == 200
    assert platform.client.get(f"/api/v1/runs/{identifier}/audit").status_code == 200
    issued = platform.client.post(
        "/api/v1/tokens",
        json={
            "name": "audit-test",
            "repository": "acme/service",
            "environment": "dev",
            "permissions": ["view"],
            "workloads": ["infrastructure"],
        },
    )
    assert issued.status_code == 200
    for path in ("/api/v1/audit", f"/api/v1/runs/{identifier}/audit"):
        assert (
            platform.client.get(
                path, headers={"Authorization": f"Bearer {issued.json()['token']}"}
            ).status_code
            == 403
        )
    platform.settings.admin_logins = []
    for path in ("/api/v1/audit", f"/api/v1/runs/{identifier}/audit"):
        assert platform.client.get(path).status_code == 403


def _create(platform, action="apply", key="request-1", **changes):
    return platform.client.post(
        "/api/v1/runs",
        headers={"Idempotency-Key": key},
        json={
            "repository": "acme/service",
            "environment": "dev",
            "action": action,
            **changes,
        },
    )


def _read(platform, identifier):
    with platform.database.transaction() as connection:
        return dict(row(connection, runs, identifier))


def _complete(platform, identifier, exit_code=0, claim=True, report=None):
    run = _read(platform, identifier)
    execution = platform.driver.executions[run["execution"]["name"]]
    cipher = Fernet(execution["environment"]["PLATFORM_HANDOFF_KEY"].encode())
    payload = json.loads(
        cipher.decrypt(
            platform.storage.get(execution["environment"]["PLATFORM_INPUT_URL"])
        )
    )
    if claim:
        assert (
            platform.client.post(
                payload["claim_url"],
                headers={"Authorization": "Bearer " + payload["claim_token"]},
            ).status_code
            == 200
        )
    result = {
        "exit_code": exit_code,
        "revision": run["revision"],
        "phase": payload["phase"],
        "manifest_digest": "manifest-digest",
        "plan_digest": "reviewed-plan-digest",
        "reports": report
        or {"plan-summary.json": {"complete": True, "totals": {"create": 1}}},
    }
    platform.storage.put(
        run["execution"]["result"], cipher.encrypt(json.dumps(result).encode())
    )
    platform.storage.put(
        run["execution"]["context"], cipher.encrypt(b"workspace context")
    )
    execution["state"] = "failed" if exit_code else "succeeded"
    platform.worker.tick()
    return payload


def _to_approval(platform, identifier):
    for _ in range(3):
        platform.worker.tick()
        _complete(platform, identifier)
    platform.worker.tick()
    assert _read(platform, identifier)["status"] == "awaiting_approval"


def _login(platform, login, token="user-token"):
    platform.client.cookies.set(
        "platform_session", platform.app.state.sessions.create(login, token)
    )


def test_saved_plan_lifecycle_and_private_artifacts(platform):
    response = _create(platform)
    assert response.status_code == 200
    identifier = response.json()["id"]
    _to_approval(platform, identifier)
    assert platform.driver.launches == 3
    assert platform.client.post(f"/api/v1/runs/{identifier}/approve").status_code == 200
    for expected_phase in ("apply", "operation"):
        platform.worker.tick()
        payload = _complete(platform, identifier)
        assert payload["phase"] == expected_phase
        assert payload["manifest_digest"] == "manifest-digest"
        assert payload["plan_digest"] == "reviewed-plan-digest"
        assert payload["context_digest"]
    assert _read(platform, identifier)["status"] == "succeeded"
    public = platform.client.get(f"/api/v1/runs/{identifier}").json()
    assert not any(key.startswith("_") for key in public["result"])
    assert "installation-secret-token" not in json.dumps(public)
    listed = platform.client.get(f"/api/v1/runs/{identifier}/artifacts").json()
    assert listed and all("phase-context" != item["name"] for item in listed)
    with platform.database.transaction() as connection:
        private = (
            connection.execute(artifacts.select().where(artifacts.c.private == 1))
            .mappings()
            .first()
        )
        assert row(connection, locks, "acme/service") is None
    assert (
        platform.client.get(
            f"/api/v1/runs/{identifier}/artifacts/{private['id']}"
        ).status_code
        == 404
    )
    link = platform.client.get(
        f"/api/v1/runs/{identifier}/artifacts/{listed[0]['id']}"
    ).json()["url"]
    assert platform.client.get(link).status_code == 200


def test_idempotency_and_commit_policy(platform):
    first = _create(platform)
    assert _create(platform).json()["id"] == first.json()["id"]
    assert _create(platform, action="destroy").status_code == 409
    assert _create(platform, key="different", revision=OTHER_SHA).status_code == 200
    assert (
        _create(platform, action="plan", key="plan", revision=OTHER_SHA).status_code
        == 200
    )
    assert _create(platform, operations=["deploy"]).status_code == 409
    assert (
        _create(platform, operations=["deploy"], key="selected-operations").status_code
        == 200
    )


def test_run_component_tags_validation_and_idempotency(platform):
    first = _create(platform, key="tags", tags=[" prod ", "shared", "prod", " "])
    assert first.status_code == 200
    assert first.json()["spec"]["tags"] == ["prod", "shared"]
    assert (
        _create(platform, key="tags", tags=["prod", "shared"]).json()["id"]
        == first.json()["id"]
    )
    assert _create(platform, key="tags", tags=["prod"]).status_code == 409
    assert _create(platform, key="comma", tags=["prod,shared"]).status_code == 422
    assert (
        _create(
            platform, action="plan-operation", key="operation", tags=["prod"]
        ).status_code
        == 422
    )


def test_permission_revocation_and_github_recheck(platform):
    _login(platform, "reader")
    assert _create(platform, action="plan").status_code == 403
    _login(platform, "admin")
    assert (
        platform.client.post(
            "/api/v1/repositories/acme/service/grants",
            json={
                "login": "reader",
                "environment": "dev",
                "role": "runner",
                "workloads": ["infrastructure", "operations"],
            },
        ).status_code
        == 200
    )
    _login(platform, "reader")
    assert _create(platform, action="plan").status_code == 200
    assert _create(platform, key="apply").status_code == 200
    _login(platform, "reader", "denied")
    assert _create(platform, action="plan", key="denied").status_code == 403


def test_scoped_tokens_expiry_and_interactive_approval(platform):
    identifier = _create(platform).json()["id"]
    _to_approval(platform, identifier)
    issued = platform.client.post(
        "/api/v1/tokens",
        json={
            "workloads": ["infrastructure", "operations"],
            "name": "automation",
            "repository": "acme/service",
            "environment": "dev",
            "permissions": ["view", "deploy"],
        },
    ).json()
    headers = {"Authorization": "Bearer " + issued["token"]}
    platform.client.cookies.clear()
    assert (
        platform.client.get(f"/api/v1/runs/{identifier}", headers=headers).status_code
        == 200
    )
    assert (
        platform.client.post(
            f"/api/v1/runs/{identifier}/approve", headers=headers
        ).status_code
        == 403
    )
    assert (
        platform.client.get(
            "/api/v1/runs?repository=acme/service&environment=prod", headers=headers
        ).status_code
        == 403
    )
    with platform.database.transaction() as connection:
        record = row(connection, tokens, issued["id"])
        assert record["id"] != issued["token"]
        assert "user-token" not in json.dumps(record["scope"])
        connection.execute(
            tokens.update()
            .where(tokens.c.id == issued["id"])
            .values(expires=time.time() - 1)
        )
    assert (
        platform.client.get(f"/api/v1/runs/{identifier}", headers=headers).status_code
        == 401
    )


@pytest.mark.parametrize("change", ["configuration", "head", "expiry"])
def test_stale_approvals_are_rejected(platform, change):
    identifier = _create(platform).json()["id"]
    _to_approval(platform, identifier)
    if change == "head":
        platform.app.state.provider.head = OTHER_SHA
    else:
        with platform.database.transaction() as connection:
            if change == "configuration":
                connection.execute(environments.update().values(revision=2))
            else:
                connection.execute(
                    runs.update()
                    .where(runs.c.id == identifier)
                    .values(expires=time.time() - 1)
                )
    assert platform.client.post(f"/api/v1/runs/{identifier}/approve").status_code == 409


def test_claim_is_one_use_and_cannot_run_arbitrary_work(platform):
    identifier = _create(platform, action="plan").json()["id"]
    platform.worker.tick()
    run = _read(platform, identifier)
    execution = platform.driver.executions[run["execution"]["name"]]
    payload = json.loads(
        Fernet(execution["environment"]["PLATFORM_HANDOFF_KEY"].encode()).decrypt(
            platform.storage.get(run["execution"]["input"])
        )
    )
    assert set(execution["environment"]) == {
        "PLATFORM_HANDOFF_KEY",
        "PLATFORM_INPUT_URL",
    }
    assert (
        platform.client.post(
            payload["claim_url"], headers={"Authorization": "Bearer wrong"}
        ).status_code
        == 403
    )
    headers = {"Authorization": "Bearer " + payload["claim_token"]}
    assert (
        platform.client.post(payload["claim_url"], headers=headers).status_code == 200
    )
    assert (
        platform.client.post(payload["claim_url"], headers=headers).status_code == 409
    )
    assert platform.client.get("/api/v1/status", headers=headers).status_code == 401


def test_worker_restart_reconciles_without_replaying(platform):
    identifier = _create(platform, action="plan").json()["id"]
    platform.worker.tick()
    with platform.database.transaction() as connection:
        connection.execute(
            runs.update().where(runs.c.id == identifier).values(lease=time.time() - 1)
        )
    replacement = Worker(
        platform.settings,
        platform.database,
        platform.driver,
        platform.storage,
        platform.app.state.provider,
    )
    replacement.tick()
    assert platform.driver.launches == 1
    assert _read(platform, identifier)["worker"] == replacement.identifier


def test_uncertain_mutation_keeps_lock_until_reconciliation(platform):
    identifier = _create(platform).json()["id"]
    _to_approval(platform, identifier)
    platform.client.post(f"/api/v1/runs/{identifier}/approve")
    platform.worker.tick()
    run = _read(platform, identifier)
    with platform.database.transaction() as connection:
        connection.execute(
            phase_claims.insert().values(
                id=f"{identifier}:{run['phase']}", run=identifier, fence=run["fence"]
            )
        )
    platform.driver.cleanup(run["execution"]["name"])
    platform.worker.tick()
    assert _read(platform, identifier)["status"] == "uncertain"
    with platform.database.transaction() as connection:
        assert row(connection, locks, "acme/service")["run"] == identifier
    assert (
        platform.client.post(
            f"/api/v1/runs/{identifier}/resolve",
            json={"note": "Inspected infrastructure and remote state."},
        ).status_code
        == 200
    )
    with platform.database.transaction() as connection:
        assert row(connection, locks, "acme/service") is None


def test_secret_scope_encryption_and_log_redaction(platform):
    assert (
        platform.client.post(
            "/api/v1/repositories/acme/service/environments/dev/secrets",
            json={
                "name": "PROVIDER_TOKEN",
                "phases": ["plan"],
                "value": "cloud-secret-token",
            },
        ).status_code
        == 200
    )
    assert (
        platform.client.post(
            "/api/v1/repositories/acme/service/environments/dev/secrets",
            json={"name": "PYTHONPATH", "phases": ["prepare"], "value": "bad"},
        ).status_code
        == 422
    )
    with platform.database.transaction() as connection:
        stored = connection.execute(secrets.select()).mappings().one()
        assert "cloud-secret-token" not in stored["ciphertext"]
    metadata_response = platform.client.get(
        "/api/v1/repositories/acme/service/environments/dev/secrets"
    )
    assert (
        "cloud-secret-token" not in metadata_response.text
        and "ciphertext" not in metadata_response.text
    )
    identifier = _create(platform, action="plan").json()["id"]
    platform.worker.tick()
    payload = _complete(platform, identifier)
    assert payload["credentials"] == {}
    platform.worker.tick()
    run = _read(platform, identifier)
    platform.driver.executions[run["execution"]["name"]]["logs"] = (
        "provider cloud-secret-token installation-secret-token"
    )
    platform.worker.tick()
    assert (
        "cloud-secret-token"
        not in platform.client.get(f"/api/v1/runs/{identifier}/logs").text
    )
    payload = _complete(platform, identifier)
    assert payload["credentials"] == {"PROVIDER_TOKEN": "cloud-secret-token"}


def _webhook(platform, payload, event, delivery="delivery-1"):
    content = json.dumps(
        {
            "repository": {"full_name": "acme/service"},
            "installation": {"id": 1},
            "sender": {"login": "admin"},
            **payload,
        }
    ).encode()
    return platform.client.post(
        "/api/v1/webhooks/github",
        content=content,
        headers={
            "X-GitHub-Event": event,
            "X-GitHub-Delivery": delivery,
            "X-Hub-Signature-256": "sha256="
            + hmac.new(b"webhook-secret", content, hashlib.sha256).hexdigest(),
        },
    )


def test_pr_events_require_authorization_and_deduplicate(platform):
    payload = {
        "action": "opened",
        "pull_request": {"head": {"sha": SHA, "repo": {"full_name": "acme/service"}}},
    }
    assert _webhook(platform, payload, "pull_request").status_code == 200
    assert _webhook(platform, payload, "pull_request").json()["duplicate"]
    with platform.database.transaction() as connection:
        run = connection.execute(runs.select()).mappings().one()
    platform.worker.tick()
    assert platform.driver.launches == 0
    assert run["status"] == "awaiting_authorization"
    assert (
        platform.client.post(f"/api/v1/runs/{run['id']}/authorize").status_code == 200
    )
    platform.worker.tick()
    assert platform.driver.launches == 1


def test_fork_and_invalid_webhooks_do_not_execute(platform):
    assert (
        _webhook(
            platform,
            {
                "action": "opened",
                "pull_request": {
                    "head": {"sha": SHA, "repo": {"full_name": "fork/service"}}
                },
            },
            "pull_request",
        ).json()["accepted"]
        is False
    )
    assert (
        platform.client.post("/api/v1/webhooks/github", content=b"{}").status_code
        == 401
    )
    with platform.database.transaction() as connection:
        assert connection.execute(runs.select()).first() is None


def test_push_enqueues_default_branch_deployment(platform):
    assert (
        _webhook(platform, {"ref": "refs/heads/main", "after": SHA}, "push").status_code
        == 200
    )
    with platform.database.transaction() as connection:
        run = connection.execute(runs.select()).mappings().one()
    assert run["action"] == "apply" and run["status"] == "queued"


def test_cancel_before_claim_is_safe(platform):
    identifier = _create(platform).json()["id"]
    platform.worker.tick()
    assert platform.client.post(f"/api/v1/runs/{identifier}/cancel").status_code == 200
    platform.worker.tick()
    assert _read(platform, identifier)["status"] == "cancelled"


def test_destroy_order_and_operation_actions():
    assert phases("destroy") == [
        "prepare",
        "plan",
        "plan-operation",
        "approval",
        "operation",
        "destroy",
    ]
    assert phases("apply-operation") == [
        "prepare",
        "plan-operation",
        "approval",
        "operation",
    ]
    with pytest.raises(ValueError):
        phases("shell")


def test_new_commit_after_approval_prevents_mutation(platform):
    identifier = _create(platform).json()["id"]
    _to_approval(platform, identifier)
    assert platform.client.post(f"/api/v1/runs/{identifier}/approve").status_code == 200
    platform.app.state.provider.head = OTHER_SHA
    platform.worker.tick()
    assert _read(platform, identifier)["status"] == "failed"
    assert platform.driver.launches == 3


def test_idle_worker_removes_expired_private_handoffs(platform):
    platform.storage.put("expired-context", b"encrypted")
    with platform.database.transaction() as connection:
        connection.execute(
            artifacts.insert().values(
                id="expired",
                run="old-run",
                name="context",
                key="expired-context",
                digest="",
                private=1,
                expires=time.time() - 1,
            )
        )
    platform.worker.tick()
    assert "expired-context" not in platform.storage.objects


def test_missing_mutation_result_retains_uncertain_lock(platform):
    identifier = _create(platform).json()["id"]
    _to_approval(platform, identifier)
    platform.client.post(f"/api/v1/runs/{identifier}/approve")
    platform.worker.tick()
    run = _read(platform, identifier)
    platform.driver.executions[run["execution"]["name"]]["state"] = "failed"
    platform.worker.tick()
    assert _read(platform, identifier)["status"] == "uncertain"


def test_phase_leases_prevent_parallel_supervisor_launch(platform):
    identifier = _create(platform, action="plan").json()["id"]
    platform.worker.tick()
    Worker(
        platform.settings,
        platform.database,
        platform.driver,
        platform.storage,
        platform.app.state.provider,
    ).tick()
    assert platform.driver.launches == 1
    assert _read(platform, identifier)["worker"] == platform.worker.identifier


def test_one_locked_repository_does_not_block_other_work(platform):
    identifier = _create(platform).json()["id"]
    _to_approval(platform, identifier)
    second = _create(platform, key="second").json()["id"]
    preview = _create(platform, action="plan", key="preview").json()["id"]
    platform.worker.tick()
    assert _read(platform, second)["status"] == "queued"
    assert _read(platform, preview)["status"] == "running"


def test_phase_result_redaction_preserves_json_types(platform):
    execution = {"redactions": platform.database.encrypt(json.dumps(["1"]))}
    result = platform.worker._redact_result(
        execution, {"exit_code": 1, "text": "secret 1"}
    )
    assert result == {"exit_code": 1, "text": "secret <redacted>"}


def test_inspection_runs_in_isolated_runtime_and_encrypts_output(platform):
    from concurrent.futures import ThreadPoolExecutor

    from stacksmith_platform.inspection import InspectionQueue

    platform.settings.database_url = platform.database.engine.url.render_as_string(
        hide_password=False
    )
    queue = InspectionQueue(platform.settings)
    with ThreadPoolExecutor(max_workers=1) as executor:
        future = executor.submit(
            queue.execute,
            ["info", "associations", "--format", "json"],
            "components: {}",
            "user-git-secret",
            None,
        )
        deadline = time.monotonic() + 5
        identifier = None
        while time.monotonic() < deadline:
            with platform.database.transaction() as connection:
                found = (
                    connection.execute(
                        runs.select().where(runs.c.action == "inspection")
                    )
                    .mappings()
                    .first()
                )
            if found:
                identifier = found["id"]
                break
            time.sleep(0.01)
        assert identifier
        platform.worker.tick()
        run = _read(platform, identifier)
        execution = platform.driver.executions[run["execution"]["name"]]
        cipher = Fernet(execution["environment"]["PLATFORM_HANDOFF_KEY"].encode())
        payload = json.loads(
            cipher.decrypt(platform.storage.get(run["execution"]["input"]))
        )
        assert payload["inspection"]["github_token"] == "user-git-secret"
        assert "user-git-secret" not in json.dumps(run["spec"])
        assert (
            platform.client.post(
                payload["claim_url"],
                headers={"Authorization": "Bearer " + payload["claim_token"]},
            ).status_code
            == 200
        )
        platform.storage.put(
            run["execution"]["result"],
            cipher.encrypt(
                json.dumps(
                    {
                        "exit_code": 0,
                        "inspection_output": '{"private": "resolved-value"}',
                    }
                ).encode()
            ),
        )
        execution["state"] = "succeeded"
        platform.worker.tick()
        assert future.result(timeout=5) == '{"private": "resolved-value"}'
        assert "resolved-value" not in json.dumps(_read(platform, identifier)["result"])
    queue.database.engine.dispose()


def test_revoking_secret_invalidates_approved_environment_revision(platform):
    path = "/api/v1/repositories/acme/service/environments/dev/secrets"
    assert (
        platform.client.post(
            path,
            json={
                "name": "PROVIDER_TOKEN",
                "phases": ["apply"],
                "value": "cloud-token",
            },
        ).status_code
        == 200
    )
    identifier = _create(platform).json()["id"]
    _to_approval(platform, identifier)
    assert platform.client.post(path + "/PROVIDER_TOKEN/remove").status_code == 200
    assert platform.client.get(path).json() == []
    assert platform.client.post(f"/api/v1/runs/{identifier}/approve").status_code == 409


def test_stale_supervisor_cannot_release_another_workers_deployment_lock(platform):
    identifier = _create(platform).json()["id"]
    platform.worker.tick()
    stale = _read(platform, identifier)
    with platform.database.transaction() as connection:
        connection.execute(
            runs.update().where(runs.c.id == identifier).values(worker="replacement")
        )
        platform.worker._finish(connection, stale, "failed")
    assert _read(platform, identifier)["status"] == "running"
    with platform.database.transaction() as connection:
        assert row(connection, locks, "acme/service")["run"] == identifier


def test_operation_names_cannot_be_cli_options(platform):
    assert (
        _create(
            platform, action="apply-operation", operations=["--destroy"]
        ).status_code
        == 422
    )


def test_expired_run_metadata_removes_private_context_and_logs(platform):
    identifier = _create(platform, action="plan").json()["id"]
    with platform.database.transaction() as connection:
        connection.execute(
            runs.update()
            .where(runs.c.id == identifier)
            .values(
                status="succeeded",
                updated=time.time() - 40 * 86400,
                result={
                    "_key": "private-key",
                    "_context": "private-context",
                    "logs": "sensitive logs",
                    "reports": {"old": {}},
                    "plan_digest": "reviewed-digest",
                },
            )
        )
    platform.worker.tick()
    record = _read(platform, identifier)
    assert "_key" not in record["result"] and "_context" not in record["result"]
    assert "logs" not in record["result"] and "reports" not in record["result"]
    assert record["result"]["plan_digest"] == "reviewed-digest"


def test_deployment_secret_reference_changes_invalidate_in_flight_runs(platform):
    assert (
        platform.client.post(
            "/api/v1/repositories/acme/service/environments/dev/secrets",
            json={
                "name": "PROVIDER_TOKEN",
                "phases": ["prepare", "apply"],
                "reference": "plan-credential",
            },
        ).status_code
        == 200
    )
    identifier = _create(platform).json()["id"]
    platform.worker.tick()
    _complete(platform, identifier)
    platform.settings.secret_references["plan-credential"] = (
        "changed-account-credential"
    )
    platform.worker.tick()
    assert _read(platform, identifier)["status"] == "failed"
    assert platform.driver.launches == 1


def test_manual_runs_resolve_default_branch_tip_and_keep_idempotent_revision(platform):
    platform.app.state.provider.head = OTHER_SHA
    response = _create(platform, action="plan")
    assert response.status_code == 200
    assert response.json()["revision"] == OTHER_SHA
    assert response.json()["spec"]["revision_override"] is False
    platform.app.state.provider.head = SHA
    repeated = _create(platform, action="plan")
    assert repeated.status_code == 200
    assert repeated.json()["id"] == response.json()["id"]
    assert repeated.json()["revision"] == OTHER_SHA


def test_explicit_commit_requires_platform_administrator(platform):
    assert (
        platform.client.post(
            "/api/v1/repositories/acme/service/grants",
            json={
                "login": "operator",
                "environment": "dev",
                "role": "runner",
                "workloads": ["infrastructure", "operations"],
            },
        ).status_code
        == 200
    )
    platform.client.cookies.set(
        "platform_session", platform.app.state.sessions.create("operator", "user-token")
    )
    assert _create(platform, action="plan", revision=SHA).status_code == 403
    assert _create(platform, action="plan", key="tip-plan").status_code == 200


def test_failed_prepare_records_failure_and_releases_run(platform):
    identifier = _create(platform, action="plan").json()["id"]
    platform.worker.tick()
    _complete(platform, identifier, exit_code=1)
    platform.worker.tick()
    assert _read(platform, identifier)["status"] == "failed"


def test_administrator_commit_override_preserves_reviewed_revision(platform):
    identifier = _create(platform, revision=OTHER_SHA).json()["id"]
    _to_approval(platform, identifier)
    assert platform.client.post(f"/api/v1/runs/{identifier}/approve").status_code == 200
    platform.worker.tick()
    assert _read(platform, identifier)["status"] == "running"
    assert _read(platform, identifier)["revision"] == OTHER_SHA


def test_commit_override_cannot_be_approved_by_regular_approver(platform):
    identifier = _create(platform, revision=OTHER_SHA).json()["id"]
    _to_approval(platform, identifier)
    assert (
        platform.client.post(
            "/api/v1/repositories/acme/service/grants",
            json={
                "login": "approver",
                "environment": "dev",
                "role": "provisioner",
                "workloads": ["infrastructure", "operations"],
            },
        ).status_code
        == 200
    )
    _login(platform, "approver")
    assert platform.client.post(f"/api/v1/runs/{identifier}/approve").status_code == 403
    assert _read(platform, identifier)["status"] == "awaiting_approval"


def test_discovery_inherits_defaults_and_activation_is_explicit(platform, monkeypatch):
    from stacksmith_platform.database import repositories

    monkeypatch.setattr(
        platform.app.state.provider,
        "catalog",
        lambda: [
            {
                "id": 2,
                "full_name": "acme/another",
                "installation_id": 73,
                "default_branch": "main",
                "topics": ["stacksmith-stack"],
                "custom_properties": {"stacksmith": "stack"},
            }
        ],
    )
    response = platform.client.post("/api/v1/repositories/refresh", json={})
    assert response.status_code == 200
    with platform.database.transaction() as connection:
        repository = row(connection, repositories, "acme/another")
        assert repository["installation"] == 73
        assert repository["deployment_enabled"] == 0
    policy = platform.client.get("/api/v1/repositories/acme/another/configuration")
    assert policy.status_code == 200
    assert policy.json()["mode"] == "inherit"
    response = platform.client.post(
        "/api/v1/repositories/acme/another/deployment", json={"enabled": True}
    )
    assert response.status_code == 409
    assert response.json()["detail"] == (
        "Configure at least one active deployment environment first."
    )
    response = platform.client.post(
        "/api/v1/repositories/acme/another/deployment", json={"enabled": False}
    )
    assert response.status_code == 200
    assert response.json()["deployment_enabled"] is False


def test_configuration_policies_preserve_layer_order_and_invalidate_queued_runs(
    platform,
):
    base = CONFIG.replace("config.yaml", "base.yaml")
    extra = CONFIG.replace("config.yaml", "extra.yaml")
    assert (
        platform.client.post(
            "/api/v1/configuration", json={"mode": "replace", "refs": [base]}
        ).status_code
        == 200
    )
    path = "/api/v1/repositories/acme/service/configuration"
    assert platform.client.post(path, json={"mode": "inherit"}).status_code == 200
    assert platform.client.get(path).json()["effective_refs"] == [base]
    assert (
        platform.client.post(path, json={"mode": "append", "refs": [extra]}).status_code
        == 200
    )
    assert platform.client.get(path).json()["effective_refs"] == [base, extra]
    run = _create(platform, action="plan").json()
    assert run["spec"]["config"] == f"{base},{extra}"
    assert (
        platform.client.post(
            path, json={"mode": "replace", "refs": [extra]}
        ).status_code
        == 200
    )
    assert platform.client.get(path).json()["effective_refs"] == [extra]
    platform.worker.tick()
    assert _read(platform, run["id"])["status"] == "failed"
    assert platform.driver.launches == 0


def test_changed_platform_defaults_invalidate_reviewed_deployments(platform):
    assert (
        platform.client.post(
            "/api/v1/configuration", json={"mode": "replace", "refs": [CONFIG]}
        ).status_code
        == 200
    )
    assert (
        platform.client.post(
            "/api/v1/repositories/acme/service/configuration", json={"mode": "inherit"}
        ).status_code
        == 200
    )
    identifier = _create(platform).json()["id"]
    _to_approval(platform, identifier)
    assert (
        platform.client.post(
            "/api/v1/configuration",
            json={
                "mode": "replace",
                "refs": [CONFIG.replace("config.yaml", "new.yaml")],
            },
        ).status_code
        == 200
    )
    assert platform.client.post(f"/api/v1/runs/{identifier}/approve").status_code == 409


def test_repository_admin_can_append_but_cannot_change_platform_defaults(platform):
    assert (
        platform.client.post(
            "/api/v1/repositories/acme/service/grants",
            json={
                "login": "maintainer",
                "role": "provisioner",
                "workloads": ["infrastructure", "operations"],
            },
        ).status_code
        == 200
    )
    _login(platform, "maintainer")
    assert (
        platform.client.post(
            "/api/v1/repositories/acme/service/configuration",
            json={"mode": "append", "refs": [CONFIG]},
        ).status_code
        == 200
    )
    assert (
        platform.client.post(
            "/api/v1/configuration", json={"mode": "replace", "refs": [CONFIG]}
        ).status_code
        == 403
    )
    assert platform.client.get("/api/v1/configuration").status_code == 403


@pytest.mark.parametrize(
    "policy",
    [
        {"mode": "inherit", "refs": [CONFIG]},
        {"mode": "replace", "refs": []},
        {
            "mode": "append",
            "refs": ["git+https://github.com/acme/config//config.yaml@main"],
        },
        {"mode": "replace", "refs": ["https://untrusted.example/config.yaml"]},
    ],
)
def test_invalid_configuration_policies_are_rejected(platform, policy):
    assert (
        platform.client.post(
            "/api/v1/repositories/acme/service/configuration", json=policy
        ).status_code
        == 422
    )


def test_composer_catalog_uses_repository_configuration_layers(platform, monkeypatch):
    from stacksmith_platform.adapter import StacksmithAdapter

    assert (
        platform.client.post(
            "/api/v1/configuration", json={"mode": "replace", "refs": [CONFIG]}
        ).status_code
        == 200
    )
    extra = CONFIG.replace("config.yaml", "extra.yaml")
    assert (
        platform.client.post(
            "/api/v1/repositories/acme/service/configuration",
            json={"mode": "append", "refs": [extra]},
        ).status_code
        == 200
    )
    monkeypatch.setattr(
        StacksmithAdapter,
        "catalog",
        lambda self, token: {"configuration": str(self.settings.config)},
    )
    assert platform.client.get("/api/catalog?repository=acme/service").json() == {
        "configuration": f"{CONFIG},{extra}"
    }


def test_worker_health_excludes_stale_process_records(platform):
    from stacksmith_platform.database import workers

    with platform.database.transaction() as connection:
        connection.execute(
            workers.insert(),
            [
                {
                    "id": "retired-process",
                    "driver": "docker",
                    "heartbeat": time.time() - 3600,
                },
                {"id": "current-process", "driver": "docker", "heartbeat": time.time()},
            ],
        )
    response = platform.client.get("/api/v1/status")
    assert response.status_code == 200
    assert [worker["id"] for worker in response.json()["workers"]] == [
        "current-process"
    ]
    assert response.json()["stale_workers"] == 1


def test_container_worker_identity_survives_process_restart(platform, monkeypatch):
    monkeypatch.setattr("stacksmith_platform.worker.os.getpid", lambda: 1)
    monkeypatch.setattr(
        "stacksmith_platform.worker.socket.gethostname", lambda: "worker-container"
    )
    assert (
        Worker(
            platform.settings,
            platform.database,
            platform.driver,
            platform.storage,
            platform.app.state.provider,
        ).identifier
        == "worker-container"
    )


def test_each_phase_uses_current_app_installation_and_fresh_token(
    platform, monkeypatch
):
    from unittest.mock import MagicMock

    discovery = MagicMock(return_value=73)
    issuance = MagicMock(side_effect=["fresh-prepare-token", "fresh-init-token"])
    monkeypatch.setattr(platform.worker.provider, "repository_installation", discovery)
    monkeypatch.setattr(platform.worker.provider, "installation_token", issuance)
    identifier = _create(platform, action="plan").json()["id"]
    platform.worker.tick()
    assert _complete(platform, identifier)["github_token"] == "fresh-prepare-token"
    platform.worker.tick()
    assert _complete(platform, identifier)["github_token"] == "fresh-init-token"
    assert discovery.call_count == 2
    discovery.assert_called_with("acme/service")
    assert [call.args for call in issuance.call_args_list] == [(73,), (73,)]


def test_token_listing_exposes_only_owned_safe_scope_metadata(platform):
    issued = platform.client.post(
        "/api/v1/tokens",
        json={
            "workloads": ["infrastructure", "operations"],
            "name": "automation",
            "repository": "acme/service",
            "environment": "dev",
            "permissions": ["view", "plan"],
        },
    ).json()
    response = platform.client.get("/api/v1/tokens")
    assert response.status_code == 200
    assert response.json()["items"] == [
        {
            "workloads": ["infrastructure", "operations"],
            "id": issued["id"],
            "name": "automation",
            "expires": issued["expires"],
            "revoked": 0,
            "repository": "acme/service",
            "environment": "dev",
            "permissions": ["view", "plan"],
        }
    ]
    with platform.database.transaction() as connection:
        assert (
            row(connection, tokens, issued["id"])["scope"]["credential"]
            not in response.text
        )
    assert issued["token"] not in response.text
    assert "user-token" not in response.text
    assert "credential" not in json.dumps(response.json())
    platform.client.cookies.set(
        "platform_session",
        platform.app.state.sessions.create("another-user", "other-token"),
    )
    assert platform.client.get("/api/v1/tokens").json()["items"] == []


def test_run_history_pages_include_all_runs_without_overlaps(platform):
    identifiers = {
        _create(platform, action="plan", key=f"page-{index}").json()["id"]
        for index in range(105)
    }
    with platform.database.transaction() as connection:
        connection.execute(runs.update().values(created=100))
    pages = [
        platform.client.get(
            f"/api/v1/runs?repository=acme/service&environment=dev&page={page}"
        ).json()
        for page in range(1, 7)
    ]
    assert [len(page["items"]) for page in pages] == [20, 20, 20, 20, 20, 5]
    assert all(page["total"] == 105 for page in pages)
    returned = [item["id"] for page in pages for item in page["items"]]
    assert len(set(returned)) == 105
    assert set(returned) == identifiers
    assert returned == sorted(returned, reverse=True)


@pytest.mark.parametrize("query", ["page=0", "page_size=0", "page_size=101"])
def test_pagination_rejects_invalid_bounds(platform, query):
    assert (
        platform.client.get(f"/api/v1/runs?repository=acme/service&{query}").status_code
        == 422
    )
    assert platform.client.get(f"/api/v1/tokens?{query}").status_code == 422


def test_admin_can_delete_terminal_runs(platform):
    identifier = _create(platform, action="plan").json()["id"]
    # Active run cannot be deleted.
    assert platform.client.delete(f"/api/v1/runs/{identifier}").status_code == 409
    assert platform.client.post(f"/api/v1/runs/{identifier}/cancel").status_code == 200
    assert _read(platform, identifier)["status"] == "cancelled"
    with platform.database.transaction() as connection:
        connection.execute(
            artifacts.insert().values(
                id="test-artifact",
                run=identifier,
                name="plan.json",
                key="artifact-storage-key",
                digest="abc",
                private=0,
                expires=time.time() + 3600,
            )
        )
    platform.storage.put("artifact-storage-key", b"report data")
    _login(platform, "non-admin")
    assert platform.client.delete(f"/api/v1/runs/{identifier}").status_code == 403
    _login(platform, "admin")
    assert platform.client.delete(f"/api/v1/runs/{identifier}").json() == {
        "deleted": True
    }
    assert platform.client.get(f"/api/v1/runs/{identifier}").status_code == 404
    with platform.database.transaction() as connection:
        assert (
            connection.execute(
                artifacts.select().where(artifacts.c.run == identifier)
            ).fetchall()
            == []
        )
    assert "artifact-storage-key" not in platform.storage.objects


def test_token_pages_count_all_owned_statuses_and_handle_revocation(platform):
    issued = [
        platform.client.post(
            "/api/v1/tokens",
            json={
                "name": f"token-{index}",
                "repository": "acme/service",
                "environment": "dev",
                "permissions": ["view"],
            },
        ).json()["id"]
        for index in range(50)
    ]
    with platform.database.transaction() as connection:
        connection.execute(
            tokens.update()
            .where(tokens.c.id.in_(issued[30:]))
            .values(expires=time.time() - 100)
        )
        connection.execute(
            tokens.update()
            .where(tokens.c.id.in_(issued[40:]))
            .values(revoked=time.time())
        )
    first = platform.client.get("/api/v1/tokens").json()
    second = platform.client.get("/api/v1/tokens?page=2").json()
    assert first["counts"] == {"all": 50, "active": 30, "expired": 10, "revoked": 10}
    assert first["total"] == second["total"] == 30
    assert len(first["items"]) == 20 and len(second["items"]) == 10
    assert not {item["id"] for item in first["items"]} & {
        item["id"] for item in second["items"]
    }
    assert (
        len(platform.client.get("/api/v1/tokens?status=all&page=3").json()["items"])
        == 10
    )
    assert platform.client.get("/api/v1/tokens?status=revoked").json()["total"] == 10
    for item in second["items"]:
        assert (
            platform.client.post(f"/api/v1/tokens/{item['id']}/revoke").status_code
            == 200
        )
    emptied = platform.client.get("/api/v1/tokens?page=2").json()
    assert emptied["items"] == [] and emptied["total"] == 20


@pytest.mark.parametrize("action", ["plan", "plan-operation"])
def test_admin_can_request_local_backend_test_plan(platform, action):
    response = _create(platform, action=action, allow_local_backend=True)
    assert response.status_code == 200
    assert response.json()["spec"]["allow_local_backend"] is True
    platform.worker.tick()
    assert _complete(platform, response.json()["id"])["allow_local_backend"] is True


@pytest.mark.parametrize("action", ["apply", "destroy", "apply-operation"])
def test_mutating_local_backend_override_is_rejected(platform, action):
    assert _create(platform, action=action, allow_local_backend=True).status_code == 422


def test_local_backend_override_requires_admin_and_is_part_of_idempotency(platform):
    assert _create(platform, action="plan").status_code == 200
    assert _create(platform, action="plan", allow_local_backend=True).status_code == 409
    assert (
        platform.client.post(
            "/api/v1/repositories/acme/service/grants",
            json={
                "login": "planner",
                "role": "runner",
                "workloads": ["infrastructure", "operations"],
            },
        ).status_code
        == 200
    )
    _login(platform, "planner")
    assert (
        _create(
            platform, action="plan", key="planner", allow_local_backend=True
        ).status_code
        == 403
    )


def test_inactive_repository_rejects_manual_and_webhook_execution(platform):
    from stacksmith_platform.database import repositories

    with platform.database.transaction() as connection:
        connection.execute(repositories.update().values(deployment_enabled=0))
    assert _create(platform).status_code == 409
    response = _webhook(platform, {"ref": "refs/heads/main", "after": SHA}, "push")
    assert response.json() == {"accepted": False}
    with platform.database.transaction() as connection:
        assert connection.execute(runs.select()).first() is None


def test_disabled_repository_stops_queued_run_before_credentials(platform):
    from stacksmith_platform.database import repositories

    identifier = _create(platform).json()["id"]
    with platform.database.transaction() as connection:
        connection.execute(repositories.update().values(deployment_enabled=0))
    platform.worker.tick()
    assert _read(platform, identifier)["status"] == "failed"
    assert not platform.driver.executions


def test_deployment_activation_requires_platform_administrator(platform):
    _login(platform, "viewer")
    assert (
        platform.client.post(
            "/api/v1/repositories/acme/service/deployment", json={"enabled": True}
        ).status_code
        == 403
    )
    assert (
        platform.client.post("/api/v1/repositories/refresh", json={}).status_code == 403
    )


def test_installation_events_revoke_restore_and_deduplicate(platform, monkeypatch):
    from stacksmith_platform.database import repositories

    monkeypatch.setattr(platform.app.state.provider, "catalog", list)
    response = _webhook(platform, {"action": "suspend"}, "installation")
    assert response.status_code == 200
    with platform.database.transaction() as connection:
        assert row(connection, repositories, "acme/service")["enabled"] == 0
    assert (
        _webhook(platform, {"action": "suspend"}, "installation").json()["duplicate"]
        is True
    )
    monkeypatch.setattr(
        platform.app.state.provider,
        "catalog",
        lambda: [
            {
                "id": 1,
                "full_name": "acme/service",
                "installation_id": 1,
                "default_branch": "main",
            }
        ],
    )
    assert (
        _webhook(
            platform, {"action": "unsuspend"}, "installation", "restore"
        ).status_code
        == 200
    )
    with platform.database.transaction() as connection:
        assert row(connection, repositories, "acme/service")["enabled"] == 1
        assert row(connection, repositories, "acme/service")["deployment_enabled"] == 1


def test_installation_removal_is_retained_when_reconciliation_fails(
    platform, monkeypatch
):
    from stacksmith_platform.database import repositories
    from stacksmith_platform.errors import ComposerError

    def unavailable():
        raise ComposerError("Retry GitHub discovery", 502)

    monkeypatch.setattr(platform.app.state.provider, "catalog", unavailable)
    response = _webhook(
        platform,
        {
            "action": "removed",
            "repositories_removed": [{"id": 1, "full_name": "acme/service"}],
        },
        "installation_repositories",
    )
    assert response.status_code == 502
    with platform.database.transaction() as connection:
        assert row(connection, repositories, "acme/service")["enabled"] == 0


def test_setup_callback_verifies_user_installation_access(platform, monkeypatch):
    monkeypatch.setattr(
        UserGitHub, "_pages", lambda self, path, key: [{"id": 1}], raising=False
    )
    assert (
        platform.client.get(
            "/api/auth/setup?installation_id=999", follow_redirects=False
        ).status_code
        == 403
    )
    response = platform.client.get(
        "/api/auth/setup?installation_id=1", follow_redirects=False
    )
    assert response.status_code == 307
    assert response.headers["location"].endswith(
        "/settings?section=github&github_setup=complete"
    )


def test_github_connection_links_use_configured_app(platform, monkeypatch):
    platform.settings.github_app_slug = "stacksmith-test"
    monkeypatch.setattr(
        UserGitHub,
        "_pages",
        lambda self, path, key: [
            {
                "id": 1,
                "account": {"login": "acme", "type": "Organization"},
                "html_url": "https://github.com/organizations/acme/settings/installations/1",
            }
        ],
        raising=False,
    )
    response = platform.client.get("/api/github-connection")
    assert response.status_code == 200
    assert (
        response.json()["install_url"]
        == "https://github.com/apps/stacksmith-test/installations/new"
    )
    assert response.json()["installations"][0]["account"] == "acme"
    assert response.json()["installations"][0]["account_type"] == "Organization"


def test_repository_filter_can_be_saved_through_its_admin_endpoint(platform):
    rules = {"properties": {}, "topics": ["stacksmith-stack", "deployment"]}
    assert platform.client.put("/api/v1/repository-filter", json=rules).json() == rules
    assert platform.client.get("/api/v1/repository-filter").json() == rules


def test_personal_catalog_refresh_does_not_request_properties(platform, monkeypatch):
    monkeypatch.setattr(
        platform.app.state.provider,
        "catalog",
        lambda: [
            {
                "id": 2,
                "full_name": "personal/stack",
                "installation_id": 73,
                "default_branch": "main",
                "owner": {"type": "User"},
            }
        ],
    )
    requests = []

    def request(installation, method, path):
        requests.append(path)
        assert path == "/repos/personal/stack"
        return {"owner": {"type": "User"}, "topics": ["stacksmith-stack"]}

    monkeypatch.setattr(platform.app.state.provider, "request", request)
    assert (
        platform.client.post("/api/v1/repositories/refresh", json={}).status_code == 200
    )
    assert requests == ["/repos/personal/stack"]


def _record_statement(
    statements, connection, cursor, statement, parameters, context, many
):
    statements.append(statement)


@pytest.fixture
def sql_statements(platform):
    statements = []
    listener = partial(_record_statement, statements)
    event.listen(platform.database.engine, "before_cursor_execute", listener)
    try:
        yield statements
    finally:
        event.remove(platform.database.engine, "before_cursor_execute", listener)


def test_unchanged_worker_output_renews_lease_without_rewriting_json(
    platform, sql_statements
):
    identifier = _create(platform, action="plan").json()["id"]
    platform.worker.tick()
    platform.worker.tick()
    previous = _read(platform, identifier)
    sql_statements.clear()
    platform.worker.tick()
    current = _read(platform, identifier)
    assert current["lease"] > previous["lease"]
    assert current["updated"] == previous["updated"]
    assert current["result"] == previous["result"]
    assert not any(
        statement.startswith("UPDATE runs SET")
        and ("result=" in statement or "execution=" in statement)
        for statement in sql_statements
    )
    platform.driver.executions[current["execution"]["name"]]["logs"] = "new output"
    platform.worker.tick()
    changed = _read(platform, identifier)
    assert changed["updated"] > current["updated"]
    assert "new output" in changed["result"]["logs"]
    _complete(platform, identifier)
    assert _read(platform, identifier)["phase"] == 1


def test_history_summary_never_loads_large_run_payloads(platform, sql_statements):
    identifier = _create(platform, action="plan").json()["id"]
    with platform.database.transaction() as connection:
        connection.execute(
            runs.update()
            .where(runs.c.id == identifier)
            .values(
                result={
                    "logs": "large log" * 100000,
                    "reports": {"report": "large" * 100000},
                }
            )
        )
    sql_statements.clear()
    response = platform.client.get("/api/v1/runs?repository=acme/service")
    assert response.status_code == 200
    assert response.json()["items"][0] == {
        key: value
        for key, value in platform.client.get(f"/api/v1/runs/{identifier}")
        .json()
        .items()
        if key not in {"spec", "result"}
    }
    assert len(response.content) < 1000
    history_queries = [
        statement
        for statement in sql_statements
        if "ORDER BY runs.created" in statement
    ]
    assert len(history_queries) == 1
    assert "runs.spec" not in history_queries[0]
    assert "runs.result" not in history_queries[0]
