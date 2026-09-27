from types import SimpleNamespace
from unittest.mock import MagicMock

import httpx
import pytest

from stacksmith_platform.errors import ComposerError
from stacksmith_platform.provider import GitHubProvider


def test_repository_installation_uses_configured_app_jwt(monkeypatch):
    monkeypatch.setattr(
        "stacksmith_platform.provider.jwt.encode", lambda *args, **kwargs: "app-jwt"
    )
    getter = MagicMock(return_value=httpx.Response(200, json={"id": 73}))
    monkeypatch.setattr("stacksmith_platform.provider.httpx.get", getter)
    assert (
        GitHubProvider(
            SimpleNamespace(github_app_id="app-id", github_private_key="key")
        ).repository_installation("acme/service")
        == 73
    )
    assert getter.call_args.args == (
        "https://api.github.com/repos/acme/service/installation",
    )
    assert getter.call_args.kwargs["headers"]["Authorization"] == "Bearer app-jwt"


def test_missing_repository_installation_explains_setup(monkeypatch):
    monkeypatch.setattr(
        "stacksmith_platform.provider.jwt.encode", lambda *args, **kwargs: "app-jwt"
    )
    monkeypatch.setattr(
        "stacksmith_platform.provider.httpx.get",
        lambda *args, **kwargs: httpx.Response(404),
    )
    with pytest.raises(ComposerError, match="Install the configured GitHub App"):
        GitHubProvider(
            SimpleNamespace(github_app_id="app-id", github_private_key="key")
        ).repository_installation("acme/service")


def test_catalog_paginates_repositories_and_skips_suspended_installations(monkeypatch):
    provider = GitHubProvider(
        SimpleNamespace(github_app_id="app-id", github_private_key="pem")
    )
    monkeypatch.setattr(
        provider,
        "_app_request",
        lambda path, params=None: [{"id": 1}, {"id": 2, "suspended_at": "now"}],
    )
    request = MagicMock(
        side_effect=[
            {"repositories": [{"id": index} for index in range(100)]},
            {"repositories": [{"id": 100}]},
        ]
    )
    monkeypatch.setattr(provider, "request", request)
    catalog = provider.catalog()
    assert len(catalog) == 101
    assert all(repository["installation_id"] == 1 for repository in catalog)
    assert request.call_count == 2
    assert request.call_args.kwargs["params"]["page"] == 2


def test_catalog_failure_does_not_return_partial_results(monkeypatch):
    provider = GitHubProvider(
        SimpleNamespace(github_app_id="app-id", github_private_key="pem")
    )
    monkeypatch.setattr(
        provider, "_app_request", lambda path, params=None: [{"id": 1}, {"id": 2}]
    )
    monkeypatch.setattr(
        provider,
        "request",
        MagicMock(
            side_effect=[
                {"repositories": [{"id": 1}]},
                ComposerError("unavailable", 502),
            ]
        ),
    )
    with pytest.raises(ComposerError, match="unavailable"):
        provider.catalog()
