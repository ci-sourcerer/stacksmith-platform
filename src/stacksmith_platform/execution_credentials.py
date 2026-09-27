import hashlib
import hmac
import json
import re

import boto3
from botocore.config import Config
from botocore.exceptions import BotoCoreError, ClientError
from pydantic import ConfigDict, Field

from .integrations import (
    ExecutionCredentialError,
    ExecutionCredentialLease,
    ExecutionCredentialProvider,
    IntegrationConfig,
    create_integration,
)


class AWSRoleConfig(IntegrationConfig):
    """Deployment-approved AWS role and source identity configuration."""

    model_config = ConfigDict(frozen=True)

    role_arn: str = Field(pattern=r"^arn:[a-z0-9-]+:iam::[0-9]{12}:role/.+$")
    region: str = Field(min_length=1)
    profile: str | None = Field(default=None, min_length=1)
    external_id: str | None = Field(default=None, min_length=1)
    duration_seconds: int = Field(default=3600, ge=900, le=43200)


class AWSRoleCredentialProvider(ExecutionCredentialProvider):
    """Issue STS credentials for an administrator-approved IAM role.

    Args:
        settings: Platform deployment settings.
        options: Validated role configuration.
    """

    config_model = AWSRoleConfig
    protocol = "aws-container"
    conflicting_variables = frozenset(
        {
            "AWS_ACCESS_KEY_ID",
            "AWS_SECRET_ACCESS_KEY",
            "AWS_SESSION_TOKEN",
            "AWS_SECURITY_TOKEN",
            "AWS_CREDENTIAL_FILE",
            "BOTO_CONFIG",
            "AWS_PROFILE",
            "AWS_DEFAULT_PROFILE",
            "AWS_CONFIG_FILE",
            "AWS_SHARED_CREDENTIALS_FILE",
            "AWS_ROLE_ARN",
            "AWS_WEB_IDENTITY_TOKEN_FILE",
            "AWS_ROLE_SESSION_NAME",
            "AWS_CONTAINER_CREDENTIALS_FULL_URI",
            "AWS_CONTAINER_CREDENTIALS_RELATIVE_URI",
            "AWS_CONTAINER_AUTHORIZATION_TOKEN",
            "AWS_CONTAINER_AUTHORIZATION_TOKEN_FILE",
        }
    )

    def __init__(self, settings, options):
        self.options = options
        self._client = None

    def issue(self, request_id: str) -> ExecutionCredentialLease:
        """Assume the configured role for the execution.

        Args:
            request_id: Opaque execution identifier for the AWS audit session.

        Returns:
            AWS environment values and their expiration.

        Raises:
            ExecutionCredentialError: AWS cannot issue credentials.
        """
        try:
            if self._client is None:
                self._client = boto3.Session(
                    profile_name=self.options.profile, region_name=self.options.region
                ).client(
                    "sts",
                    config=Config(
                        connect_timeout=5,
                        read_timeout=10,
                        retries={"max_attempts": 2, "mode": "standard"},
                    ),
                )
            credentials = self._client.assume_role(
                RoleArn=self.options.role_arn,
                RoleSessionName=f"stacksmith-{hashlib.sha256(request_id.encode()).hexdigest()[:32]}",
                DurationSeconds=self.options.duration_seconds,
                **(
                    {"ExternalId": self.options.external_id}
                    if self.options.external_id
                    else {}
                ),
            )["Credentials"]
        except BotoCoreError, ClientError:
            raise ExecutionCredentialError(
                "Execution credentials are unavailable."
            ) from None
        return ExecutionCredentialLease(
            values={
                "AWS_ACCESS_KEY_ID": credentials["AccessKeyId"],
                "AWS_SECRET_ACCESS_KEY": credentials["SecretAccessKey"],
                "AWS_SESSION_TOKEN": credentials["SessionToken"],
            },
            expires_at=credentials["Expiration"],
        )


class ExecutionCredentialCatalog:
    """Load deployment-approved execution credential providers.

    Args:
        settings: Deployment settings containing named provider definitions.

    Raises:
        ValueError: A provider name or integration registration is invalid.
        TypeError: A selected integration does not implement the provider contract.
    """

    def __init__(self, settings):
        self.providers = {}
        self.secret_key = settings.secret_key
        self.definitions = {
            name: definition.model_copy(deep=True)
            for name, definition in settings.execution_credential_providers.items()
        }
        for name, definition in self.definitions.items():
            if not re.fullmatch(r"[a-z][a-z0-9_-]{0,63}", name):
                raise ValueError("Invalid execution credential provider name")
            self.providers[name] = create_integration(
                "execution_credential_providers",
                definition.provider,
                ExecutionCredentialProvider,
                {"aws-assume-role": AWSRoleCredentialProvider},
                settings,
                definition.options,
            )
            if self.providers[name].protocol not in {"aws-container"}:
                raise ValueError("Unsupported execution credential delivery protocol")

    def revision(self, name: str) -> str:
        """Fingerprint provider identity without storing configuration in runs.

        Args:
            name: Approved provider name.

        Returns:
            Keyed fingerprint of the validated configuration and delivery protocol.

        Raises:
            KeyError: Provider is unavailable.
        """
        return hmac.new(
            self.secret_key.encode(),
            json.dumps(
                {
                    "provider": self.definitions[name].provider,
                    "options": self.providers[name]
                    .config_model.model_validate(self.definitions[name].options)
                    .model_dump(mode="json"),
                    "protocol": self.providers[name].protocol,
                },
                sort_keys=True,
            ).encode(),
            hashlib.sha256,
        ).hexdigest()

    def list(self) -> list[dict[str, str]]:
        """List approved provider names without disclosing their configuration.

        Returns:
            Names and provider types in stable order.
        """
        return [
            {"name": name, "provider": self.definitions[name].provider}
            for name in sorted(self.providers)
        ]
