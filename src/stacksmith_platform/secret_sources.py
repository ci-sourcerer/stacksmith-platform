import json
import re
from dataclasses import asdict

import boto3
import botocore.session
from botocore.config import Config
from botocore.credentials import (
    AssumeRoleCredentialFetcher,
    DeferredRefreshableCredentials,
)
from botocore.exceptions import BotoCoreError, ClientError, NoCredentialsError
from pydantic import Field, model_validator

from .integrations import (
    IntegrationConfig,
    SecretReference,
    SecretReferenceError,
    SecretSource,
    SecretSourceError,
    create_integration,
)


class ConfiguredSecretSource(SecretSource):
    """Read deployment-supplied values without changing legacy reference names.

    Args:
        settings: Platform settings.
        options: Validated source configuration.
    """

    def __init__(self, settings, options=None):
        self.settings = settings

    def discover(self) -> list[str]:
        """List configured names.

        Returns:
            Configured reference names.
        """
        return list(self.settings.secret_references)

    def validate(self, name: str) -> None:
        """Check that a configured reference exists.

        Args:
            name: Reference name.

        Returns:
            None.

        Raises:
            SecretReferenceError: Reference no longer exists.
        """
        if name not in self.settings.secret_references:
            raise SecretReferenceError("Deployment secret reference is unavailable.")

    def resolve(self, name: str) -> str:
        """Read a configured value.

        Args:
            name: Reference name.

        Returns:
            Secret value.

        Raises:
            SecretReferenceError: Reference no longer exists.
        """
        self.validate(name)
        return self.settings.secret_references[name]


class AWSSecretConfig(IntegrationConfig):
    """AWS session and exact discovery-tag configuration."""

    region: str = Field(min_length=1)
    profile: str | None = None
    role_arn: str | None = Field(default=None, min_length=1)
    external_id: str | None = Field(default=None, min_length=1)
    tag_key: str = Field(default="stacksmith:platform", min_length=1)

    @model_validator(mode="after")
    def _validate_role(self):
        if self.external_id and not self.role_arn:
            raise ValueError("An external ID requires a role ARN")
        return self


class AWSSecretSource(SecretSource):
    """Discover explicitly tagged AWS Secrets Manager string secrets.

    Args:
        settings: Platform settings.
        options: Validated AWS source configuration.
    """

    config_model = AWSSecretConfig
    supports_json_key = True

    def __init__(self, settings, options):
        self.options = options
        # Create the SDK client lazily so startup does not require network access.
        self._client = None

    @property
    def client(self):
        """Return a reusable AWS client using the configured credential chain.

        Returns:
            Secrets Manager client.

        Raises:
            SecretSourceError: AWS credentials cannot be loaded.
        """
        if self._client is None:
            try:
                self._client = self._session().client(
                    "secretsmanager",
                    config=Config(
                        connect_timeout=5,
                        read_timeout=10,
                        retries={"max_attempts": 2, "mode": "standard"},
                    ),
                )
            except BotoCoreError, ClientError:
                raise SecretSourceError("AWS secret source is unavailable.") from None
        return self._client

    def _session(self):
        source = boto3.Session(
            profile_name=self.options.profile, region_name=self.options.region
        )
        if not self.options.role_arn:
            return source
        credentials = source._session.get_credentials()
        if credentials is None:
            raise NoCredentialsError()
        fetcher = AssumeRoleCredentialFetcher(
            client_creator=source._session.create_client,
            source_credentials=credentials,
            role_arn=self.options.role_arn,
            extra_args={"ExternalId": self.options.external_id}
            if self.options.external_id
            else {},
        )
        session = botocore.session.Session()
        session.set_config_variable("region", self.options.region)
        session._credentials = DeferredRefreshableCredentials(
            refresh_using=fetcher.fetch_credentials, method="assume-role"
        )
        return boto3.Session(botocore_session=session, region_name=self.options.region)

    def _allowed(self, metadata):
        return not metadata.get("DeletedDate") and any(
            tag.get("Key") == self.options.tag_key and tag.get("Value") == "true"
            for tag in metadata.get("Tags", [])
        )

    def discover(self) -> list[str]:
        """Discover all currently listed secrets with the exact enabled tag.

        Returns:
            Available AWS secret names.

        Raises:
            SecretSourceError: AWS discovery is unavailable.
        """
        try:
            return sorted(
                secret["Name"]
                for page in self.client.get_paginator("list_secrets").paginate(
                    Filters=[
                        {"Key": "tag-key", "Values": [self.options.tag_key]},
                        {"Key": "tag-value", "Values": ["true"]},
                    ],
                    IncludePlannedDeletion=False,
                )
                for secret in page.get("SecretList", [])
                if self._allowed(secret)
            )
        except BotoCoreError, ClientError:
            raise SecretSourceError("AWS secret discovery is unavailable.") from None

    def validate(self, name: str) -> None:
        """Check current tags and deletion state without fetching a value.

        Args:
            name: AWS secret name.

        Returns:
            None.

        Raises:
            SecretReferenceError: Secret is absent or no longer enabled.
            SecretSourceError: AWS metadata cannot be read.
        """
        try:
            metadata = self.client.describe_secret(SecretId=name)
        except ClientError as exc:
            if exc.response["Error"]["Code"] == "ResourceNotFoundException":
                raise SecretReferenceError(
                    "AWS secret reference is unavailable."
                ) from None
            raise SecretSourceError("AWS secret metadata is unavailable.") from None
        except BotoCoreError:
            raise SecretSourceError("AWS secret metadata is unavailable.") from None
        if not self._allowed(metadata):
            raise SecretReferenceError("AWS secret reference is not enabled.")

    def resolve(self, name: str) -> str:
        """Read the current string value of an enabled secret.

        Args:
            name: AWS secret name.

        Returns:
            Current secret string.

        Raises:
            SecretReferenceError: Reference is unavailable or contains binary data.
            SecretSourceError: AWS retrieval is unavailable.
        """
        self.validate(name)
        try:
            result = self.client.get_secret_value(
                SecretId=name, VersionStage="AWSCURRENT"
            )
        except BotoCoreError, ClientError:
            raise SecretSourceError("AWS secret retrieval is unavailable.") from None
        if not isinstance(result.get("SecretString"), str):
            raise SecretReferenceError("AWS secret must contain a string value.")
        return result["SecretString"]


class SecretCatalog:
    """Route configured reference identifiers to explicitly enabled sources.

    Args:
        settings: Platform settings containing named sources.

    Raises:
        ValueError: Source names or namespaces conflict.
    """

    def __init__(self, settings):
        self.configured = create_integration(
            "secret_sources",
            "configured",
            SecretSource,
            {
                "configured": ConfiguredSecretSource,
                "aws-secrets-manager": AWSSecretSource,
            },
            settings,
            {},
        )
        self.sources = {}
        for name, definition in settings.secret_sources.items():
            if not re.fullmatch(r"[a-z][a-z0-9_-]{0,63}", name) or name == "configured":
                raise ValueError("Invalid secret source instance name")
            if any(key.startswith(f"{name}:") for key in settings.secret_references):
                raise ValueError("Configured secret conflicts with a source namespace")
            self.sources[name] = create_integration(
                "secret_sources",
                definition.provider,
                SecretSource,
                {
                    "configured": ConfiguredSecretSource,
                    "aws-secrets-manager": AWSSecretSource,
                },
                settings,
                definition.options,
            )

    def discover(self) -> list[dict]:
        """List only public metadata across every configured source.

        Returns:
            Sorted reference metadata.

        Raises:
            SecretSourceError: Any configured source cannot be listed.
        """
        return [
            asdict(item)
            for item in sorted(
                [
                    SecretReference(name, name, "configured")
                    for name in self.configured.discover()
                ]
                + [
                    SecretReference(
                        f"{source}:{name}", name, source, provider.supports_json_key
                    )
                    for source, provider in self.sources.items()
                    for name in provider.discover()
                ],
                key=lambda item: (item.source, item.name),
            )
        ]

    def _source(self, reference):
        prefix, separator, name = reference.partition(":")
        if separator and prefix in self.sources:
            return self.sources[prefix], name
        return self.configured, reference

    def validate(self, reference: str, json_key: str | None = None) -> None:
        """Validate a reference and selector without reading its value.

        Args:
            reference: Public reference identifier.
            json_key: Optional top-level string field.

        Returns:
            None.

        Raises:
            SecretReferenceError: Reference or selector is unsupported.
            SecretSourceError: Source cannot validate availability.
        """
        provider, name = self._source(reference)
        if json_key is not None and not provider.supports_json_key:
            raise SecretReferenceError("This source does not support JSON fields.")
        provider.validate(name)

    def resolve(
        self, reference: str, json_key: str | None = None, snapshot: dict | None = None
    ) -> str:
        """Resolve a reference, reusing only the supplied phase-local snapshot.

        Args:
            reference: Public reference identifier.
            json_key: Optional top-level string field.
            snapshot: Per-phase cache of raw values, never reused across phases.

        Returns:
            Resolved string value.

        Raises:
            SecretReferenceError: Reference or selected value is unsupported.
            SecretSourceError: Source is unavailable.
        """
        provider, name = self._source(reference)
        if json_key is not None and not provider.supports_json_key:
            raise SecretReferenceError("This source does not support JSON fields.")
        if snapshot is None:
            snapshot = {}
        if reference not in snapshot:
            snapshot[reference] = provider.resolve(name)
            if not isinstance(snapshot[reference], str):
                raise SecretReferenceError("Secret source must return a string value.")
        if json_key is None:
            return snapshot[reference]
        try:
            value = json.loads(snapshot[reference])
        except json.JSONDecodeError:
            raise SecretReferenceError("Selected secret is not valid JSON.") from None
        if not isinstance(value, dict) or not isinstance(value.get(json_key), str):
            raise SecretReferenceError("Selected JSON field must contain a string.")
        return value[json_key]
