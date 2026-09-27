import os
from datetime import UTC, datetime, timedelta
from unittest.mock import MagicMock

import boto3
import httpx
import pytest

from stacksmith_platform.credential_delivery import CredentialDelivery


def _response(suffix):
    return httpx.Response(
        200,
        json={
            "values": {
                "AWS_ACCESS_KEY_ID": f"access-{suffix}",
                "AWS_SECRET_ACCESS_KEY": f"secret-{suffix}",
                "AWS_SESSION_TOKEN": f"token-{suffix}",
            },
            "expires_at": (datetime.now(UTC) + timedelta(hours=1)).isoformat(),
        },
        request=httpx.Request("POST", "https://platform.example/credentials"),
    )


def test_aws_sdk_refreshes_through_local_phase_endpoint(monkeypatch):
    for key in tuple(os.environ):
        if key.startswith("AWS_"):
            monkeypatch.delenv(key)
    broker = MagicMock(side_effect=[_response("first"), _response("second")])
    monkeypatch.setattr("stacksmith_platform.credential_delivery.httpx.post", broker)
    with CredentialDelivery(
        {
            "protocol": "aws-container",
            "url": "https://platform.example/credentials",
            "token": "phase-capability",
        }
    ) as environment:
        for key, value in environment.items():
            monkeypatch.setenv(key, value)
        credentials = boto3.Session(region_name="us-east-1").get_credentials()
        assert credentials.get_frozen_credentials().access_key == "access-first"
        credentials._expiry_time = datetime.now(UTC) - timedelta(seconds=1)
        assert credentials.get_frozen_credentials().access_key == "access-second"
        assert broker.call_count == 2
        assert broker.call_args.kwargs["headers"] == {
            "Authorization": "Bearer phase-capability"
        }
        assert "AWS_SECRET_ACCESS_KEY" not in environment


def test_local_endpoint_rejects_requests_without_capability(monkeypatch):
    broker = MagicMock()
    monkeypatch.setattr("stacksmith_platform.credential_delivery.httpx.post", broker)
    with CredentialDelivery(
        {
            "protocol": "aws-container",
            "url": "https://platform.example/credentials",
            "token": "phase-capability",
        }
    ) as environment:
        assert (
            httpx.get(environment["AWS_CONTAINER_CREDENTIALS_FULL_URI"]).status_code
            == 403
        )
        broker.assert_not_called()


def test_local_endpoint_sanitizes_broker_failure(monkeypatch):
    monkeypatch.setattr(
        "stacksmith_platform.credential_delivery.httpx.post",
        MagicMock(side_effect=httpx.ConnectError("private-details")),
    )
    with CredentialDelivery(
        {
            "protocol": "aws-container",
            "url": "https://platform.example/credentials",
            "token": "phase-capability",
        }
    ) as environment:
        response = httpx.get(
            environment["AWS_CONTAINER_CREDENTIALS_FULL_URI"],
            headers={"Authorization": environment["AWS_CONTAINER_AUTHORIZATION_TOKEN"]},
        )
        assert response.status_code == 503
        assert "private-details" not in response.text


def test_absent_and_unsupported_delivery_protocols():
    with CredentialDelivery(None) as environment:
        assert environment == {}
    with (
        pytest.raises(ValueError, match="Unsupported"),
        CredentialDelivery({"protocol": "invalid"}),
    ):
        pytest.fail("Unsupported protocol started")
