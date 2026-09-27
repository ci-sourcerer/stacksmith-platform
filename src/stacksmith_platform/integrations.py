from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import datetime
from importlib.metadata import entry_points
from typing import ClassVar, Literal

from pydantic import BaseModel, ConfigDict


class IntegrationConfig(BaseModel):
    """Reject unsupported integration options."""

    model_config = ConfigDict(extra="forbid", hide_input_in_errors=True)


class Integration(ABC):
    """Base for installed integrations with a validated configuration model."""

    config_model: ClassVar[type[BaseModel]] = IntegrationConfig


@dataclass(frozen=True)
class SecretReference:
    """Public reference metadata; never contains a secret value."""

    reference: str
    name: str
    source: str
    supports_json_key: bool = False


class SecretSource(Integration):
    """Discover, validate, and resolve source-local string references."""

    @abstractmethod
    def discover(self) -> list[str]:
        """List available source-local names without retrieving values.

        Returns:
            Available names.

        Raises:
            SecretSourceError: Discovery is unavailable.
        """

    @abstractmethod
    def validate(self, name: str) -> None:
        """Validate current availability without reading a value.

        Args:
            name: Source-local name.

        Raises:
            SecretReferenceError: The reference is unavailable.
            SecretSourceError: The source is unavailable.
        """

    @abstractmethod
    def resolve(self, name: str) -> str:
        """Read the current string value after validating availability.

        Args:
            name: Source-local name.

        Returns:
            Secret string.

        Raises:
            SecretReferenceError: The reference is unavailable or unsupported.
            SecretSourceError: The source is unavailable.
        """

    supports_json_key: ClassVar[bool] = False


class SecretSourceError(Exception):
    """A source is unavailable; exception text must contain no secret data."""


class SecretReferenceError(SecretSourceError):
    """A reference is missing, disallowed, or cannot supply a string value."""


@dataclass(frozen=True)
class ExecutionCredentialLease:
    """Short-lived provider credentials, kept out of object representations.

    Args:
        values: Credential values interpreted by the provider's delivery adapter.
        expires_at: Timezone-aware expiration of the issued credentials.
    """

    values: dict[str, str] = field(repr=False)
    expires_at: datetime


class ExecutionCredentialError(Exception):
    """Credential issuance failed; exception text must contain no secret data."""


class ExecutionCredentialProvider(Integration):
    """Issue short-lived credentials for an authorized execution."""

    protocol: ClassVar[str] = ""
    conflicting_variables: ClassVar[frozenset[str]] = frozenset()

    @abstractmethod
    def issue(self, request_id: str) -> ExecutionCredentialLease:
        """Issue credentials for a platform-generated execution identifier.

        Args:
            request_id: Opaque identifier used to correlate provider audit records.

        Returns:
            Short-lived credential values and their expiration.

        Raises:
            ExecutionCredentialError: Credentials cannot be issued.
        """


class ArtifactNotFoundError(Exception):
    """The requested artifact does not exist; safe for reconciliation handling."""


class ArtifactStorage(Integration):
    """Private artifact storage supporting bounded reads and scoped HTTP URLs."""

    @abstractmethod
    def put(self, key: str, content: bytes) -> str:
        """Store bytes and return their SHA256 digest.

        Args:
            key: Object key.
            content: Object bytes.

        Returns:
            SHA256 digest.
        """

    @abstractmethod
    def get(self, key: str) -> bytes:
        """Read an object with the platform's 16 MiB limit.

        Args:
            key: Object key.

        Returns:
            Object bytes.

        Raises:
            ValueError: Object exceeds the limit.
            ArtifactNotFoundError: Object does not exist.
        """

    @abstractmethod
    def digest(self, key: str) -> str:
        """Hash a workspace with the platform's one GiB limit.

        Args:
            key: Object key.

        Returns:
            SHA256 digest.

        Raises:
            ValueError: Object exceeds the limit.
            ArtifactNotFoundError: Object does not exist.
        """

    @abstractmethod
    def url(self, key: str, method: str, lifetime: int = 3600) -> str:
        """Issue an expiring, single-object HTTP transfer capability.

        Args:
            key: Object key.
            method: `get_object` or `put_object`.
            lifetime: Expiry in seconds.

        Returns:
            Scoped URL.
        """

    @abstractmethod
    def delete(self, key: str) -> None:
        """Delete an object idempotently.

        Args:
            key: Object key.

        Returns:
            None.
        """


class ExecutionDriver(Integration):
    """Launch and reconcile isolated executions by stable resource name."""

    @abstractmethod
    def inspect(
        self, name: str
    ) -> Literal["missing", "running", "succeeded", "failed"]:
        """Inspect an execution without creating one.

        Args:
            name: Stable resource name.

        Returns:
            Execution state.
        """

    @abstractmethod
    def launch(
        self, name: str, image: str, environment: dict[str, str], timeout: int
    ) -> None:
        """Launch a restricted execution with phase-scoped handoff capabilities.

        Args:
            name: Stable resource name.
            image: Immutable image.
            environment: Handoff environment.
            timeout: Phase deadline in seconds.

        Returns:
            None.
        """

    @abstractmethod
    def logs(self, name: str) -> str:
        """Read a bounded UTF-8 execution log tail.

        Args:
            name: Stable resource name.

        Returns:
            Log text, or an empty string for a missing resource.
        """

    @abstractmethod
    def cleanup(self, name: str) -> None:
        """Stop and remove an execution idempotently.

        Args:
            name: Stable resource name.

        Returns:
            None.
        """


def create_integration(
    group: str,
    name: str,
    contract: type[Integration],
    builtins: dict,
    settings,
    options: dict,
):
    """Construct only the selected built-in or installed provider.

    Args:
        group: Entry-point group suffix.
        name: Configured implementation name.
        contract: Required ABC.
        builtins: Built-in implementation registry.
        settings: Platform settings.
        options: Provider options validated by its configuration model.

    Returns:
        A configured implementation of the required ABC.

    Raises:
        ValueError: Registration is missing, duplicated, or incomplete.
        TypeError: Plugin does not implement the required contract.
        ValidationError: Provider configuration is invalid.
    """
    registrations = dict(builtins)
    for entry in entry_points(group=f"stacksmith_platform.{group}"):
        if entry.name in registrations:
            raise ValueError(f"Duplicate {group} integration: {entry.name}")
        registrations[entry.name] = entry
    if name not in registrations:
        raise ValueError(f"Unknown {group} integration: {name}")
    implementation = registrations[name]
    if not isinstance(implementation, type):
        implementation = implementation.load()
    if not isinstance(implementation, type) or not issubclass(implementation, contract):
        raise TypeError(f"Incompatible {group} integration: {name}")
    if implementation.__abstractmethods__:
        raise ValueError(f"Incomplete {group} integration: {name}")
    return implementation(settings, implementation.config_model.model_validate(options))
