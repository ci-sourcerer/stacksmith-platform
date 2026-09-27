import json
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest
from botocore.exceptions import ClientError, EndpointConnectionError
from cryptography.fernet import Fernet
from pydantic import ValidationError
from test_control import _complete, _create, _read
from test_control import platform as _platform_fixture

from stacksmith_platform.database import Database
from stacksmith_platform.drivers import DockerDriver, KubernetesDriver
from stacksmith_platform.integrations import (
    ArtifactStorage,
    ExecutionDriver,
    SecretReferenceError,
    SecretSource,
    SecretSourceError,
    create_integration,
)
from stacksmith_platform.secret_sources import (
    AWSSecretConfig,
    AWSSecretSource,
    ConfiguredSecretSource,
    SecretCatalog,
)
from stacksmith_platform.settings import Settings
from stacksmith_platform.storage import ArtifactStore


@pytest.fixture(name="platform")
def _source_platform(tmp_path, monkeypatch):
    yield from _platform_fixture.__wrapped__(tmp_path, monkeypatch)


def _aws_source():
    source = AWSSecretSource(None, AWSSecretConfig(region="us-east-1"))
    source._client = MagicMock()
    source._client.describe_secret.return_value = {
        "Tags": [{"Key": "stacksmith:platform", "Value": "true"}]
    }
    return source


def test_aws_source_assumes_role_with_refreshable_credentials():
    source = AWSSecretSource(
        None,
        AWSSecretConfig(
            region="us-east-1",
            profile="platform",
            role_arn="arn:aws:iam::123456789012:role/secrets",
            external_id="external-id",
        ),
    )
    with (
        patch("stacksmith_platform.secret_sources.boto3.Session") as session,
        patch(
            "stacksmith_platform.secret_sources.AssumeRoleCredentialFetcher"
        ) as fetcher,
        patch(
            "stacksmith_platform.secret_sources.DeferredRefreshableCredentials"
        ) as refreshable,
    ):
        assert source.client == session.return_value.client.return_value
        fetcher.assert_called_once_with(
            client_creator=session.return_value._session.create_client,
            source_credentials=session.return_value._session.get_credentials.return_value,
            role_arn="arn:aws:iam::123456789012:role/secrets",
            extra_args={"ExternalId": "external-id"},
        )
        refreshable.assert_called_once_with(
            refresh_using=fetcher.return_value.fetch_credentials, method="assume-role"
        )


def test_aws_source_rejects_external_id_without_role():
    with pytest.raises(ValidationError):
        AWSSecretConfig(region="us-east-1", external_id="external-id")


def test_aws_source_refreshes_assumed_role_credentials(monkeypatch):
    monkeypatch.setenv("AWS_ACCESS_KEY_ID", "source-key")
    monkeypatch.setenv("AWS_SECRET_ACCESS_KEY", "source-secret")
    fetch = MagicMock(
        side_effect=[
            {
                "access_key": key,
                "secret_key": "secret",
                "token": "token",
                "expiry_time": (datetime.now(UTC) + timedelta(minutes=5)).isoformat(),
            }
            for key in ("first", "second")
        ]
    )
    monkeypatch.setattr(
        "stacksmith_platform.secret_sources.AssumeRoleCredentialFetcher.fetch_credentials",
        fetch,
    )
    source = AWSSecretSource(
        None,
        AWSSecretConfig(
            region="us-east-1", role_arn="arn:aws:iam::123456789012:role/test"
        ),
    )
    assert (
        source.client._request_signer._credentials.get_frozen_credentials().access_key
        == "first"
    )
    assert (
        source.client._request_signer._credentials.get_frozen_credentials().access_key
        == "second"
    )


def _enable_aws(platform):
    source = _aws_source()
    platform.app.state.secret_catalog.sources["aws"] = source
    platform.worker.secret_catalog.sources["aws"] = source
    return source


def _bind(platform, name="TOKEN", **changes):
    return platform.client.post(
        "/api/v1/repositories/acme/service/environments/dev/secrets",
        json={
            "name": name,
            "phases": ["prepare", "plan"],
            "reference": "aws:app",
            **changes,
        },
    )


def test_discovery_paginates_and_checks_exact_tag_pairs():
    source = _aws_source()
    source.client.get_paginator.return_value.paginate.return_value = [
        {
            "SecretList": [
                {
                    "Name": "allowed",
                    "Tags": [{"Key": "stacksmith:platform", "Value": "true"}],
                },
                {
                    "Name": "prefix-key",
                    "Tags": [{"Key": "stacksmith:platform-other", "Value": "true"}],
                },
                {
                    "Name": "prefix-value",
                    "Tags": [{"Key": "stacksmith:platform", "Value": "true-ish"}],
                },
                {
                    "Name": "separate-tags",
                    "Tags": [
                        {"Key": "stacksmith:platform", "Value": "false"},
                        {"Key": "other", "Value": "true"},
                    ],
                },
            ]
        },
        {"SecretList": []},
        {
            "SecretList": [
                {
                    "Name": "also-allowed",
                    "Tags": [{"Key": "stacksmith:platform", "Value": "true"}],
                },
                {
                    "Name": "deleted",
                    "DeletedDate": 1,
                    "Tags": [{"Key": "stacksmith:platform", "Value": "true"}],
                },
            ]
        },
    ]
    assert source.discover() == ["allowed", "also-allowed"]
    source.client.get_secret_value.assert_not_called()
    assert source.client.get_paginator.call_args.args == ("list_secrets",)


@pytest.mark.parametrize(
    "tags",
    [
        [],
        [{"Key": "stacksmith:platform", "Value": "TRUE"}],
        [{"Key": "stacksmith:platform", "Value": "false"}],
    ],
)
def test_resolution_rechecks_tags_before_fetching(tags):
    source = _aws_source()
    source.client.describe_secret.return_value = {"Tags": tags}
    with pytest.raises(SecretReferenceError):
        source.resolve("app")
    source.client.get_secret_value.assert_not_called()


def test_resolution_reads_current_string_and_rejects_binary():
    source = _aws_source()
    source.client.get_secret_value.return_value = {"SecretString": "private-value"}
    assert source.resolve("app") == "private-value"
    source.client.get_secret_value.assert_called_once_with(
        SecretId="app", VersionStage="AWSCURRENT"
    )
    source.client.get_secret_value.return_value = {"SecretBinary": b"private-value"}
    with pytest.raises(SecretReferenceError):
        source.resolve("app")


@pytest.mark.parametrize("operation", ["discover", "validate", "resolve"])
def test_sdk_failures_are_sanitized(operation):
    source = _aws_source()
    failure = ClientError(
        {
            "Error": {
                "Code": "AccessDeniedException",
                "Message": "sensitive-sdk-details",
            }
        },
        "GetSecretValue",
    )
    if operation == "discover":
        source.client.get_paginator.return_value.paginate.side_effect = failure
    elif operation == "validate":
        source.client.describe_secret.side_effect = failure
    else:
        source.client.get_secret_value.side_effect = failure
    with pytest.raises(SecretSourceError) as error:
        getattr(source, operation)(*(() if operation == "discover" else ("app",)))
    assert "sensitive-sdk-details" not in str(error.value)


def test_network_and_missing_secret_errors():
    source = _aws_source()
    source.client.describe_secret.side_effect = EndpointConnectionError(
        endpoint_url="private-endpoint"
    )
    with pytest.raises(SecretSourceError, match="metadata is unavailable"):
        source.validate("app")
    source.client.describe_secret.side_effect = ClientError(
        {"Error": {"Code": "ResourceNotFoundException"}}, "DescribeSecret"
    )
    with pytest.raises(SecretReferenceError):
        source.validate("app")


@pytest.mark.parametrize(
    "raw",
    ["invalid-json", "[]", '{"field": 1}', '{"other": "value"}', '{"field": null}'],
)
def test_json_field_requires_a_string(raw):
    catalog = SecretCatalog(Settings(_env_file=None))
    catalog.sources["aws"] = _aws_source()
    catalog.sources["aws"].client.get_secret_value.return_value = {"SecretString": raw}
    with pytest.raises(SecretReferenceError):
        catalog.resolve("aws:app", "field")


def test_snapshot_reuses_raw_value_for_multiple_json_fields():
    catalog = SecretCatalog(Settings(_env_file=None))
    catalog.sources["aws"] = _aws_source()
    catalog.sources["aws"].client.get_secret_value.return_value = {
        "SecretString": '{"first":"one","second":"two"}'
    }
    snapshot = {}
    assert catalog.resolve("aws:app", "first", snapshot) == "one"
    assert catalog.resolve("aws:app", "second", snapshot) == "two"
    assert catalog.sources["aws"].client.get_secret_value.call_count == 1
    assert catalog.resolve("aws:app", snapshot={}) == '{"first":"one","second":"two"}'
    assert catalog.sources["aws"].client.get_secret_value.call_count == 2


@pytest.mark.parametrize("name", ["configured", "bad:name", "UPPER"])
def test_invalid_source_instance_names_fail_startup(name):
    with pytest.raises(ValueError, match="source instance"):
        SecretCatalog(
            Settings(
                _env_file=None,
                secret_sources={
                    name: {
                        "provider": "aws-secrets-manager",
                        "options": {"region": "us-east-1"},
                    }
                },
            )
        )


def test_source_namespaces_cannot_shadow_legacy_references():
    with pytest.raises(ValueError, match="namespace"):
        SecretCatalog(
            Settings(
                _env_file=None,
                secret_references={"aws:app": "value"},
                secret_sources={
                    "aws": {
                        "provider": "aws-secrets-manager",
                        "options": {"region": "us-east-1"},
                    }
                },
            )
        )


def test_configured_source_contract_and_selector_restriction():
    catalog = SecretCatalog(
        Settings(_env_file=None, secret_references={"legacy/name": "secret"})
    )
    assert isinstance(catalog.configured, SecretSource)
    assert catalog.resolve("legacy/name") == "secret"
    with pytest.raises(SecretReferenceError):
        catalog.validate("legacy/name", "field")
    with pytest.raises(SecretReferenceError):
        catalog.resolve("missing")


def test_plugin_loading_is_explicit_and_config_validated(monkeypatch):
    selected = SimpleNamespace(
        name="external", load=MagicMock(return_value=ConfiguredSecretSource)
    )
    unused = SimpleNamespace(name="unused", load=MagicMock())
    monkeypatch.setattr(
        "stacksmith_platform.integrations.entry_points",
        lambda **kwargs: [selected, unused],
    )
    assert isinstance(
        create_integration(
            "secret_sources", "external", SecretSource, {}, Settings(_env_file=None), {}
        ),
        ConfiguredSecretSource,
    )
    selected.load.assert_called_once()
    unused.load.assert_not_called()
    with pytest.raises(ValidationError):
        create_integration(
            "secret_sources",
            "external",
            SecretSource,
            {},
            None,
            {"unexpected": "private"},
        )


@pytest.mark.parametrize("implementation", [str, ArtifactStore])
def test_incompatible_plugin_rejected(implementation):
    with pytest.raises(TypeError, match="Incompatible"):
        create_integration(
            "secret_sources", "bad", SecretSource, {"bad": implementation}, None, {}
        )


def test_missing_duplicate_and_abstract_plugins_rejected(monkeypatch):
    monkeypatch.setattr(
        "stacksmith_platform.integrations.entry_points", lambda **kwargs: []
    )
    with pytest.raises(ValueError, match="Unknown"):
        create_integration("secret_sources", "missing", SecretSource, {}, None, {})
    with pytest.raises(ValueError, match="Incomplete"):
        create_integration(
            "secret_sources",
            "abstract",
            SecretSource,
            {"abstract": SecretSource},
            None,
            {},
        )
    monkeypatch.setattr(
        "stacksmith_platform.integrations.entry_points",
        lambda **kwargs: [SimpleNamespace(name="configured")],
    )
    with pytest.raises(ValueError, match="Duplicate"):
        create_integration(
            "secret_sources",
            "configured",
            SecretSource,
            {"configured": ConfiguredSecretSource},
            None,
            {},
        )


def test_builtin_adapters_implement_public_contracts():
    assert issubclass(ArtifactStore, ArtifactStorage)
    assert issubclass(DockerDriver, ExecutionDriver)
    assert issubclass(KubernetesDriver, ExecutionDriver)
    assert not ArtifactStore.__abstractmethods__
    assert not DockerDriver.__abstractmethods__
    assert not KubernetesDriver.__abstractmethods__


def test_catalog_and_binding_do_not_read_external_values(platform):
    source = _enable_aws(platform)
    source.client.get_paginator.return_value.paginate.return_value = [
        {
            "SecretList": [
                {
                    "Name": "app",
                    "Tags": [{"Key": "stacksmith:platform", "Value": "true"}],
                }
            ]
        }
    ]
    response = platform.client.get(
        "/api/v1/repositories/acme/service/environments/dev/secret-references"
    )
    assert response.status_code == 200
    assert {
        "reference": "aws:app",
        "name": "app",
        "source": "aws",
        "supports_json_key": True,
    } in response.json()["references"]
    assert _bind(platform, json_key="token").status_code == 200
    source.client.get_secret_value.assert_not_called()
    metadata = platform.client.get(
        "/api/v1/repositories/acme/service/environments/dev/secrets"
    ).json()[0]
    assert metadata["reference"] == "aws:app" and metadata["json_key"] == "token"
    assert "ciphertext" not in metadata


def test_catalog_outage_has_no_partial_results_and_is_sanitized(platform):
    source = _enable_aws(platform)
    source.client.get_paginator.return_value.paginate.side_effect = ClientError(
        {"Error": {"Code": "AccessDeniedException", "Message": "private-value"}},
        "ListSecrets",
    )
    response = platform.client.get(
        "/api/v1/repositories/acme/service/environments/dev/secret-references"
    )
    assert response.status_code == 503
    assert "private-value" not in response.text
    assert "references" not in response.json()


def test_binding_rejects_removed_tag_and_source_outage(platform):
    source = _enable_aws(platform)
    source.client.describe_secret.return_value = {"Tags": []}
    assert _bind(platform).status_code == 422
    source.client.describe_secret.side_effect = EndpointConnectionError(
        endpoint_url="private-endpoint"
    )
    response = _bind(platform)
    assert response.status_code == 503 and "private-endpoint" not in response.text
    assert (
        _bind(platform, reference=None, value="entered", json_key="field").status_code
        == 422
    )


def test_external_rotation_fails_before_next_phase(platform):
    source = _enable_aws(platform)
    assert _bind(platform).status_code == 200
    source.client.get_secret_value.return_value = {"SecretString": "original"}
    identifier = _create(platform, action="plan").json()["id"]
    platform.worker.tick()
    _complete(platform, identifier)
    source.client.get_secret_value.return_value = {"SecretString": "rotated"}
    platform.worker.tick()
    assert _read(platform, identifier)["status"] == "failed"
    assert platform.driver.launches == 1


def test_source_failure_marks_run_failed_without_leaking_values(platform):
    source = _enable_aws(platform)
    assert _bind(platform).status_code == 200
    identifier = _create(platform, action="plan").json()["id"]
    source.client.get_secret_value.side_effect = ClientError(
        {"Error": {"Code": "AccessDeniedException", "Message": "private-value"}},
        "GetSecretValue",
    )
    platform.worker.tick()
    assert _read(platform, identifier)["status"] == "failed"
    assert "private-value" not in json.dumps(_read(platform, identifier))
    assert not platform.driver.launches


def test_worker_injects_and_fingerprints_one_snapshot(platform):
    source = _enable_aws(platform)
    assert _bind(platform, name="FIRST", json_key="first").status_code == 200
    assert _bind(platform, name="SECOND", json_key="second").status_code == 200
    source.client.get_secret_value.side_effect = [
        {"SecretString": '{"first":"one","second":"two"}'},
        {"SecretString": '{"first":"rotated","second":"different"}'},
    ]
    identifier = _create(platform, action="plan").json()["id"]
    platform.worker.tick()
    run = _read(platform, identifier)
    execution = platform.driver.executions[run["execution"]["name"]]
    payload = json.loads(
        Fernet(execution["environment"]["PLATFORM_HANDOFF_KEY"].encode()).decrypt(
            platform.storage.get(execution["environment"]["PLATFORM_INPUT_URL"])
        )
    )
    assert payload["credentials"] == {"FIRST": "one", "SECOND": "two"}
    assert source.client.get_secret_value.call_count == 1
    assert "rotated" not in json.dumps(run)


def test_baseline_includes_nullable_selector_column(tmp_path):
    database = Database(f"sqlite:///{tmp_path / 'platform.sqlite'}", "test-key")
    database.initialize()
    with database.engine.begin() as connection:
        connection.exec_driver_sql(
            "INSERT INTO secrets (id, reference) VALUES ('binding', 'external')"
        )
    database.initialize()
    with database.engine.begin() as connection:
        assert connection.exec_driver_sql(
            "SELECT reference, json_key FROM secrets"
        ).one() == ("external", None)
    database.engine.dispose()


def test_custom_discovery_tag_key():
    source = AWSSecretSource(
        None, AWSSecretConfig(region="us-east-1", tag_key="custom:enabled")
    )
    source._client = MagicMock()
    source.client.describe_secret.return_value = {
        "Tags": [{"Key": "custom:enabled", "Value": "true"}]
    }
    source.validate("app")
    source.client.get_secret_value.assert_not_called()


def test_configured_aws_source_does_not_connect_at_startup(monkeypatch):
    session = MagicMock()
    monkeypatch.setattr("stacksmith_platform.secret_sources.boto3.Session", session)
    catalog = SecretCatalog(
        Settings(
            _env_file=None,
            secret_sources={
                "aws": {
                    "provider": "aws-secrets-manager",
                    "options": {"region": "us-east-1", "profile": "dev"},
                }
            },
        )
    )
    assert isinstance(catalog.sources["aws"], AWSSecretSource)
    session.assert_not_called()


def test_storage_and_runner_plugins_use_the_same_factory(monkeypatch):
    from stacksmith_platform.factories import artifact_storage, execution_driver

    monkeypatch.setattr("stacksmith_platform.drivers.docker.from_env", MagicMock())
    selected = []

    def registrations(**kwargs):
        selected.append(kwargs["group"])
        if kwargs["group"].endswith("artifact_stores"):
            return [SimpleNamespace(name="external-s3", load=lambda: ArtifactStore)]
        return [SimpleNamespace(name="external-docker", load=lambda: DockerDriver)]

    monkeypatch.setattr("stacksmith_platform.integrations.entry_points", registrations)
    monkeypatch.setattr("stacksmith_platform.storage.boto3.client", MagicMock())
    settings = Settings(
        _env_file=None, artifact_store="external-s3", runner="external-docker"
    )
    assert isinstance(artifact_storage(settings), ArtifactStorage)
    assert isinstance(execution_driver(settings), ExecutionDriver)
    assert selected == [
        "stacksmith_platform.artifact_stores",
        "stacksmith_platform.execution_drivers",
    ]
