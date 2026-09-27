import os
import ssl
from pathlib import Path
from tempfile import NamedTemporaryFile

import certifi

CA_BUNDLE_MOUNT_PATH = "/etc/stacksmith-custom-ca.pem"

_CA_ENVIRONMENT = (
    "AWS_CA_BUNDLE",
    "CURL_CA_BUNDLE",
    "GIT_SSL_CAINFO",
    "REQUESTS_CA_BUNDLE",
    "SSL_CERT_FILE",
)


def configure_ca_bundle(custom_bundle: str | Path | None) -> None:
    """Add custom CA certificates to the public roots for this process.

    Args:
        custom_bundle: Absolute path to an additional PEM certificate bundle.

    Returns:
        None.

    Raises:
        FileNotFoundError: The configured bundle does not exist.
        OSError: The configured bundle cannot be read or the merged bundle cannot
            be written.
        ssl.SSLError: The merged bundle contains invalid certificates.
        ValueError: The configured path is not absolute or is not ASCII.
    """
    if not custom_bundle:
        return

    custom = Path(custom_bundle)
    if not custom.is_absolute():
        raise ValueError("Custom CA bundle path must be absolute")
    if not custom.is_file():
        raise FileNotFoundError(f"Custom CA bundle is not a readable file: {custom}")

    combined = Path(certifi.where()).read_bytes() + b"\n" + custom.read_bytes()
    ssl.create_default_context(cadata=combined.decode("ascii"))
    with NamedTemporaryFile(
        mode="wb", prefix="stacksmith-ca-", suffix=".pem", dir="/tmp", delete=False
    ) as bundle:
        bundle.write(combined)
        bundle_path = str(Path(bundle.name))

    for name in _CA_ENVIRONMENT:
        os.environ[name] = bundle_path


def ca_environment() -> dict[str, str]:
    """Return configured CA environment variables for child processes.

    Returns:
        The custom merged bundle settings currently present in this process.
    """
    return {name: os.environ[name] for name in _CA_ENVIRONMENT if name in os.environ}
