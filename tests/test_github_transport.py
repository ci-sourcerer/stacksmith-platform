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
