import json
import threading
from concurrent.futures import ThreadPoolExecutor
from contextlib import closing
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from unittest.mock import AsyncMock

import aiohttp
import pytest

from stacksmith_platform.errors import ComposerError
from stacksmith_platform.github import GitHubClient
from stacksmith_platform.github_transport import GitHubResponse, GitHubTransport


class _GitHubHandler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def do_GET(self):
        """Echo request identity and connection information for transport tests.

        Returns:
            None.
        """
        self._respond()

    def do_POST(self):
        """Echo a consumed JSON request body for transport tests.

        Returns:
            None.
        """
        self._respond()

    def _respond(self):
        if self.path == "/conditional":
            if getattr(self.server, "denied", False):
                self.send_response(403)
            elif self.headers.get("If-None-Match") == getattr(
                self.server, "etag", '"first"'
            ):
                self.send_response(304)
            else:
                self.send_response(200)
            self.send_header("ETag", getattr(self.server, "etag", '"first"'))
            self.send_header("Content-Length", "0")
            self.end_headers()
            return
        content = json.dumps(
            {
                "authorization": self.headers.get("Authorization"),
                "cookie": self.headers.get("Cookie"),
                "connection": self.client_address[1],
                "body": self.rfile.read(
                    int(self.headers.get("Content-Length", 0))
                ).decode(),
                "path": self.path,
            }
        ).encode()
        self.send_response(403 if self.path == "/denied" else 200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(content)))
        self.send_header("Set-Cookie", "identity=first-user; Path=/")
        self.end_headers()
        self.wfile.write(content)
        self.wfile.flush()

    def log_message(self, format, *args):
        """Suppress HTTP server diagnostics in tests.

        Args:
            format: Unused message format.
            *args: Unused message arguments.

        Returns:
            None.
        """


@pytest.fixture
def github_server():
    with ThreadingHTTPServer(("127.0.0.1", 0), _GitHubHandler) as server:
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            yield f"http://127.0.0.1:{server.server_port}"
        finally:
            server.shutdown()
            thread.join()


def test_reuses_connections_without_sharing_identity_or_cookies(github_server):
    with closing(GitHubTransport(github_server)) as transport:
        first = GitHubClient("first-user", transport).user()
        second = GitHubClient("second-user", transport).user()
        assert first["connection"] == second["connection"]
        assert first["authorization"] == "Bearer first-user"
        assert second["authorization"] == "Bearer second-user"
        assert first["cookie"] is None and second["cookie"] is None
        assert (
            transport.request("POST", "/body", json={"name": "stack"}).json()["body"]
            == '{"name": "stack"}'
        )
        assert (
            transport.request("GET", "/paths", params={"page": 2}).json()["path"]
            == "/paths?page=2"
        )
    assert transport._session.closed
    transport.close()
    with pytest.raises(RuntimeError, match="closed"):
        transport.request("GET", "/user")


def _user(transport, token):
    return GitHubClient(token, transport).user()


def test_concurrent_requests_share_a_bounded_pool(github_server):
    with (
        closing(GitHubTransport(github_server)) as transport,
        ThreadPoolExecutor(max_workers=24) as callers,
    ):
        pending = [callers.submit(_user, transport, str(index)) for index in range(48)]
        responses = [request.result() for request in pending]
        assert [response["authorization"] for response in responses] == [
            f"Bearer {index}" for index in range(48)
        ]
        assert all(response["cookie"] is None for response in responses)
        assert len({response["connection"] for response in responses}) <= 20


def test_http_errors_keep_github_error_semantics_and_close(github_server):
    transport = GitHubTransport(github_server)
    with pytest.raises(ComposerError, match="access was denied"), closing(transport):
        GitHubClient("token", transport)._request("GET", "/denied")
    assert transport._session.closed
    assert not any(thread.name == "github-http" for thread in threading.enumerate())


@pytest.mark.parametrize("error", [aiohttp.ClientConnectionError(), TimeoutError()])
def test_network_errors_become_retryable_gateway_errors(monkeypatch, error):
    monkeypatch.setattr(aiohttp.ClientSession, "_request", AsyncMock(side_effect=error))
    with closing(GitHubTransport()) as transport:
        with pytest.raises(ComposerError, match="temporarily unreachable") as caught:
            transport.request("GET", "/user")
        assert caught.value.status == 502


def test_empty_response_has_no_json_payload():
    assert GitHubResponse(204, b"").content == b""
    assert not GitHubResponse(204, b"").is_error
    with pytest.raises(ValueError):
        GitHubResponse(200, b"invalid").json()


def test_conditional_requests_revalidate_each_credential(github_server):
    with closing(GitHubTransport(github_server)) as transport:
        assert (
            transport.request(
                "GET", "/conditional", headers={"Authorization": "Bearer first"}
            ).status_code
            == 200
        )
        assert (
            transport.request(
                "GET", "/conditional", headers={"Authorization": "Bearer first"}
            ).status_code
            == 200
        )
        assert transport.conditional_hits == 1
        assert (
            transport.request(
                "GET", "/conditional", headers={"Authorization": "Bearer second"}
            ).status_code
            == 200
        )
        assert transport.conditional_hits == 1
        assert transport.request_count == 3
        assert all("Bearer" not in key for key in transport._conditional)


def test_conditional_errors_evict_without_accepting_stale_content():
    with closing(GitHubTransport()) as transport:
        transport._conditional_response(
            "key", None, GitHubResponse(200, b"old"), '"old"'
        )
        previous = transport._conditional["key"]
        assert (
            transport._conditional_response(
                "key", previous, GitHubResponse(403, b"denied"), None
            ).status_code
            == 403
        )
        assert "key" not in transport._conditional
        assert (
            transport._conditional_response(
                "key", previous, GitHubResponse(200, b"new"), '"new"'
            ).content
            == b"new"
        )
        assert transport._conditional["key"][0] == '"new"'


def test_explicit_conditionals_and_unauthenticated_requests_bypass_cache(github_server):
    with closing(GitHubTransport(github_server)) as transport:
        transport.request("GET", "/conditional")
        response = transport.request(
            "GET",
            "/conditional",
            headers={"Authorization": "Bearer first", "If-None-Match": '"first"'},
        )
        assert response.status_code == 304
        assert not transport._conditional


def test_configured_ca_bundle_is_used(github_server, monkeypatch):
    from unittest.mock import Mock

    import certifi

    from stacksmith_platform import github_transport

    context = Mock(wraps=github_transport.ssl.create_default_context)
    monkeypatch.setenv("SSL_CERT_FILE", certifi.where())
    monkeypatch.setattr(github_transport.ssl, "create_default_context", context)
    with closing(GitHubTransport(github_server)) as transport:
        transport.request("GET", "/user")
    context.assert_called_once_with(cafile=certifi.where())


def _profiled_requests(request):
    from stacksmith_platform.review_profiling import _stage

    request.transport.request(
        "GET", "/conditional", headers={"Authorization": "Bearer secret"}
    )
    _stage("completion")
    request.transport.request(
        "GET", "/conditional", headers={"Authorization": "Bearer secret"}
    )


def test_profile_records_wire_conditional_hits(github_server, caplog):
    import logging
    from types import SimpleNamespace

    from stacksmith_platform.review_profiling import profile_review

    with closing(GitHubTransport(github_server)) as transport:
        request = SimpleNamespace(
            transport=transport,
            headers={},
            app=SimpleNamespace(
                state=SimpleNamespace(
                    settings=SimpleNamespace(review_profiling=True),
                    github_transport=transport,
                )
            ),
        )
        with caplog.at_level(logging.INFO):
            profile_review(_profiled_requests)(request)
    event = json.loads(
        next(
            record.getMessage().split("REVIEW_PROFILE ")[1]
            for record in caplog.records
            if "REVIEW_PROFILE " in record.getMessage()
        )
    )
    assert sum(item["count"] for item in event["github"]) == 2
    assert sum(item["conditional_hits"] for item in event["github"]) == 1
    assert {item["stage"] for item in event["github"]} == {"preflight", "completion"}
    assert "secret" not in json.dumps(event)
