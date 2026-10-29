import pytest
from pydantic import ValidationError

from stacksmith_platform.settings import Settings


def test_inspection_concurrency_default_and_environment(monkeypatch):
    monkeypatch.delenv("STACKSMITH_PLATFORM_INSPECTION_CONCURRENCY", raising=False)
    assert Settings(_env_file=None).inspection_concurrency == 4
    monkeypatch.setenv("STACKSMITH_PLATFORM_INSPECTION_CONCURRENCY", "8")
    assert Settings(_env_file=None).inspection_concurrency == 8


@pytest.mark.parametrize("value", [0, -1])
def test_inspection_concurrency_must_be_positive(value):
    with pytest.raises(ValidationError):
        Settings(_env_file=None, inspection_concurrency=value)


def test_inspection_poll_interval_default_and_environment(monkeypatch):
    monkeypatch.delenv("STACKSMITH_PLATFORM_INSPECTION_POLL_INTERVAL", raising=False)
    assert Settings(_env_file=None).inspection_poll_interval == 2
    monkeypatch.setenv("STACKSMITH_PLATFORM_INSPECTION_POLL_INTERVAL", "0.5")
    assert Settings(_env_file=None).inspection_poll_interval == 0.5


@pytest.mark.parametrize("value", [0, -0.25, float("inf"), float("nan")])
def test_inspection_poll_interval_must_be_positive_and_finite(value):
    with pytest.raises(ValidationError):
        Settings(_env_file=None, inspection_poll_interval=value)


def test_unrelated_dotenv_values_do_not_break_platform_configuration(tmp_path):
    dotenv = tmp_path / ".env"
    dotenv.write_text(
        "COMPOSER_SECRET_KEY=old-secret\nUNRELATED_SECRET=private-value\nSTACKSMITH_PLATFORM_COMMAND_TIMEOUT=45\n"
    )
    settings = Settings(_env_file=dotenv)
    assert settings.command_timeout == 45
    assert not settings.github_enabled


def test_configuration_errors_do_not_echo_credentials():
    with pytest.raises(ValidationError) as error:
        Settings(
            _env_file=None,
            github_client_id="private-client-identifier",
            github_client_secret="",
        )
    assert "private-client-identifier" not in str(error.value)


def _execution_settings():
    return {
        "_env_file": None,
        "execution_enabled": True,
        "database_url": "postgresql+psycopg://test:test@postgres/platform",
        "admin_logins": ["owner"],
        "github_client_id": "client",
        "github_client_secret": "secret",
        "github_app_id": "1",
        "github_private_key": "private-key",
        "github_webhook_secret": "webhook",
        "secret_key": "x" * 32,
        "artifact_access_key": "key",
        "artifact_secret_key": "secret",
    }


def test_docker_accepts_local_immutable_image_ids_but_kubernetes_requires_named_digests():
    assert Settings(
        **_execution_settings(), runner="docker", execution_image="sha256:" + "a" * 64
    ).execution_enabled
    with pytest.raises(ValidationError):
        Settings(
            **_execution_settings(),
            runner="kubernetes",
            execution_image="sha256:" + "a" * 64,
        )
    assert Settings(
        **_execution_settings(),
        runner="kubernetes",
        execution_image="registry/image@sha256:" + "a" * 64,
    ).execution_enabled


def test_github_app_key_can_be_loaded_from_a_mounted_file(tmp_path):
    key = tmp_path / "github.pem"
    key.write_text("private-key-from-file")
    assert (
        Settings(_env_file=None, github_private_key_file=key).github_private_key
        == "private-key-from-file"
    )


def test_custom_ca_bundle_requires_an_absolute_path():
    with pytest.raises(ValidationError, match="Custom CA bundle path must be absolute"):
        Settings(_env_file=None, ca_bundle="certs/custom.pem")


def test_local_kubernetes_image_requires_explicit_dev_setting():
    with pytest.raises(ValidationError):
        Settings(
            **_execution_settings(),
            runner="kubernetes",
            execution_image="stacksmith-platform-execution:local",
        )
    assert Settings(
        **_execution_settings(),
        runner="kubernetes",
        execution_image="stacksmith-platform-execution:local",
        allow_local_execution_image=True,
    ).execution_enabled
