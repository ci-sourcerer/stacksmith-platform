import base64
import json
from pathlib import Path
from unittest.mock import MagicMock
from urllib.error import URLError

from scripts import kubernetes
from scripts.kubernetes import _resources


def test_local_stack_provisions_database_secrets_and_multiline_configuration(
    monkeypatch,
):
    monkeypatch.setenv("STACKSMITH_RUN_CUSTOM_VALUE", "from-shell")
    resources = _resources(
        {
            "STACKSMITH_PLATFORM_DB_PASSWORD": "admin/secret",
            "STACKSMITH_PLATFORM_API_DB_PASSWORD": "api/secret",
            "STACKSMITH_PLATFORM_WORKER_DB_PASSWORD": "worker/secret",
            "STACKSMITH_PLATFORM_ARTIFACT_ACCESS_KEY": "artifact-key",
            "STACKSMITH_PLATFORM_ARTIFACT_SECRET_KEY": "artifact-secret",
            "STACKSMITH_PLATFORM_GITHUB_PRIVATE_KEY": "first\nsecond\n",
            "STACKSMITH_PLATFORM_RUN_ENVIRONMENT": '{"EXISTING":"kept"}',
        },
        {
            "AWS_ACCESS_KEY_ID": "aws-key",
            "AWS_SECRET_ACCESS_KEY": "aws-secret",
        },
    )
    database = next(
        resource
        for resource in resources
        if resource["metadata"].get("name") == "stacksmith-postgres"
    )
    postgres_container = database["spec"]["template"]["spec"]["containers"][0]
    assert postgres_container["image"] == "postgres:18-alpine"
    data_mount = next(
        mount for mount in postgres_container["volumeMounts"] if mount["name"] == "data"
    )
    assert data_mount["mountPath"] == "/var/lib/postgresql"
    secrets = {
        resource["metadata"]["name"]: {
            key: base64.b64decode(value).decode()
            for key, value in resource["data"].items()
        }
        for resource in resources
        if resource["kind"] == "Secret"
    }
    assert (
        secrets["stacksmith-platform-config"]["STACKSMITH_PLATFORM_GITHUB_PRIVATE_KEY"]
        == "first\nsecond\n"
    )
    assert json.loads(
        secrets["stacksmith-platform-config"]["STACKSMITH_PLATFORM_RUN_ENVIRONMENT"]
    ) == {
        "EXISTING": "kept",
        "CUSTOM_VALUE": "from-shell",
        "AWS_ACCESS_KEY_ID": "aws-key",
        "AWS_SECRET_ACCESS_KEY": "aws-secret",
    }
    assert {name for name in secrets if name.endswith("-database")} == {
        "stacksmith-platform-api-database",
        "stacksmith-platform-supervisor-database",
        "stacksmith-platform-migration-database",
    }
    assert (
        "api%2Fsecret"
        in secrets["stacksmith-platform-api-database"][
            "STACKSMITH_PLATFORM_DATABASE_URL"
        ]
    )


def test_execution_providers_keep_local_platform_identity_out_of_runs():
    configured = kubernetes._configuration(
        {
            "STACKSMITH_PLATFORM_EXECUTION_CREDENTIAL_PROVIDERS": '{"production":{"provider":"aws-assume-role"}}'
        },
        {"AWS_ACCESS_KEY_ID": "source-key", "AWS_SECRET_ACCESS_KEY": "source-secret"},
    )
    assert configured["AWS_ACCESS_KEY_ID"] == "source-key"
    assert "AWS_ACCESS_KEY_ID" not in json.loads(
        configured["STACKSMITH_PLATFORM_RUN_ENVIRONMENT"]
    )


def test_minikube_loads_both_application_images_and_restarts_frontend(
    tmp_path, monkeypatch
):
    monkeypatch.chdir(tmp_path)
    Path(".env").write_text(
        "STACKSMITH_PLATFORM_FRONTEND_IMAGE=custom-frontend:local\n"
    )
    monkeypatch.setattr(kubernetes, "_run", MagicMock(return_value="minikube"))
    monkeypatch.setattr(kubernetes, "_kubectl", MagicMock())
    monkeypatch.setattr(kubernetes, "_credentials", dict)
    monkeypatch.setattr(kubernetes, "_resources", lambda *_: [])
    monkeypatch.setattr(kubernetes, "_storage_cidr", lambda: "10.0.0.1/32")
    monkeypatch.setattr(kubernetes, "_create_bucket", MagicMock())
    monkeypatch.setattr(kubernetes, "_forward", MagicMock())
    kubernetes.up()
    kubernetes._run.assert_any_call(
        "minikube", "image", "load", "--overwrite=true", "custom-frontend:local"
    )
    assert "frontendImage=custom-frontend:local" in kubernetes._run.call_args.args
    kubernetes._kubectl.assert_any_call(
        "-n",
        "stacksmith-platform",
        "rollout",
        "status",
        "deployment/stacksmith-frontend",
        "--timeout=180s",
    )
    kubernetes._forward.assert_called_once()


def test_port_forward_targets_frontend_service(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    response = MagicMock()
    response.__enter__.return_value.status = 200
    monkeypatch.setattr(
        kubernetes, "urlopen", MagicMock(side_effect=[URLError("offline"), response])
    )
    process = MagicMock(pid=1234)
    process.poll.return_value = None
    monkeypatch.setattr(kubernetes.subprocess, "Popen", MagicMock(return_value=process))
    kubernetes._forward()
    assert (
        "service/stacksmith-frontend" in kubernetes.subprocess.Popen.call_args.args[0]
    )
    assert "8000:8080" in kubernetes.subprocess.Popen.call_args.args[0]
