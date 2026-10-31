import json
import logging
from contextlib import closing
from types import SimpleNamespace
from unittest.mock import AsyncMock

import aiohttp
import anyio
import pytest

from stacksmith_platform.errors import ComposerError
from stacksmith_platform.github_transport import GitHubTransport


class _Response:
    def __init__(self, status, headers=None):
        self.status = status
        self.headers = headers or {}

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        return None

    async def read(self):
        """Return the fixture body.

        Returns:
            A JSON response body.
        """
        return b'{"ok":true}'


@pytest.mark.parametrize("status", [500, 502, 503, 504])
def test_transient_get_retries_once(monkeypatch, status):
    request = AsyncMock(side_effect=[_Response(status), _Response(200)])
    monkeypatch.setattr(aiohttp.ClientSession, "_request", request)
    monkeypatch.setattr(anyio, "sleep", AsyncMock())
    with closing(GitHubTransport()) as transport:
        assert transport.request("GET", "/user").status_code == 200
    assert request.call_count == 2


@pytest.mark.parametrize("status", [400, 401, 403, 404, 409, 422, 429])
def test_nontransient_get_is_not_retried(monkeypatch, status):
    request = AsyncMock(return_value=_Response(status))
    monkeypatch.setattr(aiohttp.ClientSession, "_request", request)
    monkeypatch.setattr(anyio, "sleep", AsyncMock())
    with closing(GitHubTransport()) as transport:
        assert transport.request("GET", "/user").status_code == status
    assert request.call_count == 1


@pytest.mark.parametrize("method", ["POST", "PATCH", "PUT", "DELETE"])
def test_writes_are_not_retried(monkeypatch, method):
    request = AsyncMock(return_value=_Response(503))
    monkeypatch.setattr(aiohttp.ClientSession, "_request", request)
    monkeypatch.setattr(anyio, "sleep", AsyncMock())
    with closing(GitHubTransport()) as transport:
        assert transport.request(method, "/write").status_code == 503
    assert request.call_count == 1


@pytest.mark.parametrize(
    "headers", [{"Retry-After": "60"}, {"X-RateLimit-Remaining": "0"}]
)
def test_retry_hints_do_not_trigger_immediate_retry(monkeypatch, headers):
    request = AsyncMock(return_value=_Response(503, headers))
    monkeypatch.setattr(aiohttp.ClientSession, "_request", request)
    monkeypatch.setattr(anyio, "sleep", AsyncMock())
    with closing(GitHubTransport()) as transport:
        assert transport.request("GET", "/user").status_code == 503
    assert request.call_count == 1


@pytest.mark.parametrize("method, expected", [("GET", 2), ("POST", 1)])
def test_connection_errors_have_a_bounded_retry(monkeypatch, method, expected):
    request = AsyncMock(side_effect=aiohttp.ClientConnectionError("unreachable"))
    monkeypatch.setattr(aiohttp.ClientSession, "_request", request)
    monkeypatch.setattr(anyio, "sleep", AsyncMock())
    with closing(GitHubTransport()) as transport, pytest.raises(ComposerError) as error:
        transport.request(method, "/user")
    assert error.value.status == 502
    assert request.call_count == expected


def test_certificate_errors_are_not_retried(monkeypatch):
    request = AsyncMock(
        side_effect=aiohttp.ClientSSLError(None, OSError("certificate failure"))
    )
    monkeypatch.setattr(aiohttp.ClientSession, "_request", request)
    monkeypatch.setattr(anyio, "sleep", AsyncMock())
    with closing(GitHubTransport()) as transport, pytest.raises(ComposerError):
        transport.request("GET", "/user")
    assert request.call_count == 1


_FAIL_AFTER = anyio.fail_after


def _short_deadline(seconds):
    assert seconds == 20
    return _FAIL_AFTER(0.01)


def test_retry_backoff_shares_the_original_deadline(monkeypatch):
    request = AsyncMock(return_value=_Response(503))
    monkeypatch.setattr(aiohttp.ClientSession, "_request", request)
    monkeypatch.setattr(anyio, "fail_after", _short_deadline)
    with closing(GitHubTransport()) as transport, pytest.raises(ComposerError):
        transport.request("GET", "/user")
    assert request.call_count == 1


def test_retry_cannot_serve_cached_data_after_access_denial(monkeypatch):
    request = AsyncMock(
        side_effect=[
            _Response(200, {"etag": '"first"'}),
            _Response(503),
            _Response(403),
        ]
    )
    monkeypatch.setattr(aiohttp.ClientSession, "_request", request)
    monkeypatch.setattr(anyio, "sleep", AsyncMock())
    with closing(GitHubTransport()) as transport:
        headers = {"Authorization": "Bearer private-token"}
        assert transport.request("GET", "/user", headers=headers).status_code == 200
        assert transport.request("GET", "/user", headers=headers).status_code == 403
        assert not transport._conditional
    assert request.call_count == 3


def _profiled_read(request):
    return request.app.state.github_transport.request(
        "GET", "/user", headers={"Authorization": "Bearer private-token"}
    )


@pytest.mark.parametrize(
    "first", [aiohttp.ClientConnectionError("unreachable"), _Response(503)]
)
def test_recovered_attempts_remain_visible_in_profiles(monkeypatch, caplog, first):
    from stacksmith_platform.review_profiling import profile_review

    request = AsyncMock(side_effect=[first, _Response(200)])
    monkeypatch.setattr(aiohttp.ClientSession, "_request", request)
    monkeypatch.setattr(anyio, "sleep", AsyncMock())
    with caplog.at_level(logging.INFO), closing(GitHubTransport()) as transport:
        caller = SimpleNamespace(
            headers={},
            app=SimpleNamespace(
                state=SimpleNamespace(
                    settings=SimpleNamespace(review_profiling=True),
                    github_transport=transport,
                )
            ),
        )
        assert profile_review(_profiled_read)(caller).status_code == 200
    event = json.loads(
        next(
            record.getMessage().split("REVIEW_PROFILE ")[1]
            for record in caplog.records
            if "REVIEW_PROFILE " in record.getMessage()
        )
    )
    assert event["outcome"] == "succeeded"
    entry = event["github"][0]
    assert entry["count"] == 2 and entry["retries"] == 1 and entry["failures"] == 1
    assert entry["duplicate_reads"] == 0
    assert entry["outcomes"] == {
        "network" if isinstance(first, Exception) else "503": 1,
        "200": 1,
    }
    assert "private-token" not in json.dumps(event)
