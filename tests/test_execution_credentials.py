from datetime import UTC, datetime, timedelta
from unittest.mock import MagicMock

import boto3
import pytest
from botocore.exceptions import ClientError, EndpointConnectionError
from botocore.stub import Stubber
from pydantic import ValidationError

from stacksmith_platform.execution_credentials import (
    AWSRoleConfig,
    AWSRoleCredentialProvider,
    ExecutionCredentialCatalog,
)
from stacksmith_platform.integrations import ExecutionCredentialError
from stacksmith_platform.settings import Settings


def _settings(**options):
    return Settings(
        _env_file=None,
        execution_credential_providers={
            "production": {
                "provider": "aws-assume-role",
                "options": {
                    "region": "us-east-1",
                    "role_arn": "arn:aws:iam::123456789012:role/deploy",
                    **options,
                },
            }
        },
    )


def test_catalog_exposes_names_without_sensitive_provider_configuration():
    catalog = ExecutionCredentialCatalog(_settings(external_id="private-external-id"))
    assert catalog.list() == [{"name": "production", "provider": "aws-assume-role"}]
    assert "private-external-id" not in repr(catalog.list())


@pytest.mark.parametrize("name", ["", "PROD", "a:role", "a" * 65])
def test_catalog_rejects_invalid_names(name):
    settings = _settings()
    settings.execution_credential_providers[name] = (
        settings.execution_credential_providers.pop("production")
    )
    with pytest.raises(ValueError, match="Invalid execution credential provider name"):
        ExecutionCredentialCatalog(settings)


@pytest.mark.parametrize(
    "options",
    [
        {"role_arn": "not-an-arn"},
        {"duration_seconds": 899},
        {"duration_seconds": 43201},
        {"unknown": "value"},
    ],
)
def test_provider_rejects_invalid_configuration(options):
    with pytest.raises(ValidationError):
        ExecutionCredentialCatalog(_settings(**options))


def test_issue_assumes_configured_role_and_returns_private_expiring_values():
    provider = AWSRoleCredentialProvider(
        None,
        AWSRoleConfig(
            region="us-east-1",
            role_arn="arn:aws:iam::123456789012:role/deploy",
            external_id="private-external-id",
        ),
    )
    provider._client = MagicMock()
    expires = datetime.now(UTC) + timedelta(hours=1)
    provider._client.assume_role.return_value = {
        "Credentials": {
            "AccessKeyId": "access-key",
            "SecretAccessKey": "secret-key",
            "SessionToken": "session-token",
            "Expiration": expires,
        }
    }
    lease = provider.issue("run:phase")
    assert lease.values == {
        "AWS_ACCESS_KEY_ID": "access-key",
        "AWS_SECRET_ACCESS_KEY": "secret-key",
        "AWS_SESSION_TOKEN": "session-token",
    }
    assert lease.expires_at == expires
    assert "secret-key" not in repr(lease)
    assert provider._client.assume_role.call_args.kwargs == {
        "RoleArn": "arn:aws:iam::123456789012:role/deploy",
        "RoleSessionName": "stacksmith-f49dcac35a8558862aecabd7635d6e9c",
        "DurationSeconds": 3600,
        "ExternalId": "private-external-id",
    }


def test_issue_matches_sts_request_and_response_contract():
    provider = ExecutionCredentialCatalog(_settings()).providers["production"]
    provider._client = boto3.client(
        "sts",
        region_name="us-east-1",
        aws_access_key_id="source-access-key",
        aws_secret_access_key="source-secret-key",
    )
    with Stubber(provider._client) as stubber:
        stubber.add_response(
            "assume_role",
            {
                "Credentials": {
                    "AccessKeyId": "A" * 20,
                    "SecretAccessKey": "s" * 40,
                    "SessionToken": "session-token",
                    "Expiration": datetime.now(UTC) + timedelta(hours=1),
                },
                "AssumedRoleUser": {
                    "AssumedRoleId": "A" * 21,
                    "Arn": "arn:aws:sts::123456789012:assumed-role/deploy/session",
                },
            },
            {
                "RoleArn": "arn:aws:iam::123456789012:role/deploy",
                "RoleSessionName": "stacksmith-f49dcac35a8558862aecabd7635d6e9c",
                "DurationSeconds": 3600,
            },
        )
        assert (
            provider.issue("run:phase").values["AWS_SESSION_TOKEN"] == "session-token"
        )
        stubber.assert_no_pending_responses()


@pytest.mark.parametrize(
    "error",
    [
        ClientError(
            {"Error": {"Code": "AccessDenied", "Message": "private"}}, "AssumeRole"
        ),
        EndpointConnectionError(endpoint_url="https://private.example"),
    ],
)
def test_issue_sanitizes_provider_failures(error):
    provider = ExecutionCredentialCatalog(_settings()).providers["production"]
    provider._client = MagicMock()
    provider._client.assume_role.side_effect = error
    with pytest.raises(
        ExecutionCredentialError, match="Execution credentials are unavailable"
    ) as failure:
        provider.issue("run:phase")
    assert "private" not in str(failure.value)
