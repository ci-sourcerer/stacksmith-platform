import re
from pathlib import Path
from secrets import token_urlsafe
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class SourceDefinition(BaseModel):
    """Explicitly selected provider and its integration-specific options."""

    model_config = ConfigDict(extra="forbid", hide_input_in_errors=True)
    provider: str
    options: dict = Field(default_factory=dict)


class Settings(BaseSettings):
    """Read deployment settings from environment variables and a local `.env`."""

    model_config = SettingsConfigDict(
        env_prefix="STACKSMITH_PLATFORM_",
        env_file=".env",
        extra="ignore",
        hide_input_in_errors=True,
    )
    stacksmith_command: str = "stacksmith"
    config: str | Path = Path("examples/platform.yaml")
    sample: Path = Path("examples/stack.yaml")
    module_source_prefix: Path | None = None
    public_url: str = "http://127.0.0.1:5173"
    execution_callback_url: str = ""
    database: Path = Path(".data/platform.sqlite3")
    database_url: str = ""
    execution_enabled: bool = False
    admin_logins: list[str] = []
    github_app_id: str = ""
    github_app_slug: str = ""
    github_private_key: str = ""
    github_private_key_file: Path | None = None
    ca_bundle: str = ""
    github_webhook_secret: str = ""
    execution_image: str = ""
    allow_local_execution_image: bool = False
    runner: str = "docker"
    worker_id: str = ""
    supervision_mode: Literal["worker", "kubernetes-jobs"] = "worker"
    supervisor_image: str = ""
    supervisor_namespace: str = "default"
    supervisor_config_secret: str = ""
    supervisor_database_secret: str = ""
    supervisor_service_account: str = "stacksmith-supervisor"
    supervisor_image_pull_policy: str = "IfNotPresent"
    supervisor_cpu: str = "1"
    supervisor_memory: str = "1Gi"
    inspection_wait_timeout: int | None = Field(default=None, ge=1)
    inspection_concurrency: int = Field(default=4, ge=1)
    execution_namespace: str = "stacksmith-runs"
    execution_service_account: str = "stacksmith-execution"
    execution_cpu: str = "1"
    execution_memory: str = "2Gi"
    docker_memory: str = "2g"
    docker_network: str = "stacksmith-execution"
    phase_timeout: int = 3600
    approval_ttl: int = 86400
    artifact_retention: int = 2592000
    artifact_endpoint: str = ""
    artifact_region: str = "us-east-1"
    artifact_bucket: str = "stacksmith-platform"
    artifact_access_key: str = ""
    artifact_secret_key: str = ""
    secret_references: dict[str, str] = {}
    secret_sources: dict[str, SourceDefinition] = Field(default_factory=dict)
    execution_credential_providers: dict[str, SourceDefinition] = Field(
        default_factory=dict
    )
    artifact_store: str = "s3"
    artifact_options: dict = Field(default_factory=dict)
    runner_options: dict = Field(default_factory=dict)
    run_environment: dict[str, str] = Field(default_factory=dict)
    stacksmith_build_dir: Path = Path(".data/stacksmith-build")
    secret_key: str = Field(default_factory=lambda: token_urlsafe(48))
    github_client_id: str = ""
    github_client_secret: str = ""
    draft_pull_requests: bool = False
    command_timeout: int = 30

    @property
    def github_enabled(self) -> bool:
        """Return whether a GitHub App is configured."""
        return bool(self.github_client_id)

    @model_validator(mode="after")
    def _validate_github_settings(self):
        protected_run_names = {
            "HOME",
            "LANG",
            "PATH",
            "TERM",
            "FORCE_COLOR",
            "NO_COLOR",
            "STACKSMITH_GIT_TOKEN",
            "STACKSMITH_WARN_ON_UNLOCKED",
        }
        for name in self.run_environment:
            if (
                not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", name)
                or name in protected_run_names
                or name.startswith(
                    ("STACKSMITH_", "PLATFORM_", "GIT_", "PYTHON", "LD_")
                )
            ):
                raise ValueError(f"Invalid run environment variable name: {name}")
        if self.github_private_key_file is not None and not self.github_private_key:
            try:
                self.github_private_key = self.github_private_key_file.read_text()
            except OSError as exc:
                raise ValueError("Cannot read the GitHub App private key file") from exc
        if self.ca_bundle and not Path(self.ca_bundle).is_absolute():
            raise ValueError("Custom CA bundle path must be absolute")
        if (
            self.execution_enabled
            and self.supervision_mode == "kubernetes-jobs"
            and not all(
                (
                    self.supervisor_image,
                    self.supervisor_config_secret,
                    self.supervisor_database_secret,
                )
            )
        ):
            raise ValueError(
                "Kubernetes supervision requires its image and configuration/database Secret names"
            )
        if self.execution_enabled:
            if not self.database_url.startswith("postgresql+psycopg://"):
                raise ValueError("Execution requires PostgreSQL via database_url")
            if not self.github_enabled or not self.admin_logins:
                raise ValueError(
                    "Execution requires GitHub sign-in and bootstrap administrators"
                )
            if not all(
                (
                    self.github_app_id,
                    self.github_private_key,
                    self.github_webhook_secret,
                )
            ):
                raise ValueError(
                    "Execution requires GitHub App credentials and webhook secret"
                )
            if (
                not re.fullmatch(r"[^\s]+@sha256:[0-9a-f]{64}", self.execution_image)
                and not (
                    self.runner == "docker"
                    and re.fullmatch(r"sha256:[0-9a-f]{64}", self.execution_image)
                )
                and not (
                    self.runner == "kubernetes"
                    and self.allow_local_execution_image
                    and self.execution_image == "stacksmith-platform-execution:local"
                )
            ):
                raise ValueError(
                    "Execution image must be a named digest, a local Docker image ID "
                    "for Docker, or an explicitly enabled Minikube image"
                )
            if self.artifact_store == "s3" and not all(
                (self.artifact_access_key, self.artifact_secret_key)
            ):
                raise ValueError("Execution requires artifact storage credentials")
        if self.phase_timeout < 1 or not 1 <= self.approval_ttl <= 86400:
            raise ValueError(
                "Phase timeout must be positive and approvals at most 24 hours"
            )
        if bool(self.github_client_id) != bool(self.github_client_secret):
            raise ValueError("Both GitHub client ID and client secret are required")
        if self.github_enabled and (
            "secret_key" not in self.model_fields_set or len(self.secret_key) < 32
        ):
            raise ValueError(
                "GitHub mode requires a stable secret key of 32+ characters"
            )
        if not (isinstance(self.config, str) and self.config.startswith("git+")):
            self.config = Path(self.config).resolve()
        self.sample = self.sample.resolve()
        self.stacksmith_build_dir = self.stacksmith_build_dir.resolve()
        if self.module_source_prefix is not None:
            self.module_source_prefix = self.module_source_prefix.resolve()
        self.public_url = self.public_url.rstrip("/")
        return self
