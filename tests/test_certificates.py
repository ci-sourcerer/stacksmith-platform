import os
from pathlib import Path

import certifi

from stacksmith_platform.certificates import ca_environment, configure_ca_bundle

_CA_ENVIRONMENT = (
    "AWS_CA_BUNDLE",
    "CURL_CA_BUNDLE",
    "GIT_SSL_CAINFO",
    "REQUESTS_CA_BUNDLE",
    "SSL_CERT_FILE",
)


def test_custom_bundle_supplements_certifi_roots(tmp_path, monkeypatch):
    base = tmp_path / "base.pem"
    custom = tmp_path / "custom.pem"
    content = Path(certifi.where()).read_bytes()
    base.write_bytes(content)
    custom.write_bytes(content)
    monkeypatch.setattr("stacksmith_platform.certificates.certifi.where", lambda: base)
    previous = {name: os.environ.get(name) for name in _CA_ENVIRONMENT}
    generated = None
    try:
        configure_ca_bundle(custom)
        environment = ca_environment()
        generated = Path(environment["SSL_CERT_FILE"])
        assert generated.read_bytes() == content + b"\n" + content
        assert set(environment) == set(_CA_ENVIRONMENT)
        assert all(value == str(generated) for value in environment.values())
    finally:
        generated.unlink(missing_ok=True) if generated else None
        for name, value in previous.items():
            if value is None:
                os.environ.pop(name, None)
            else:
                os.environ[name] = value
