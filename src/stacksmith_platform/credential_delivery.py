import hmac
import json
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from threading import Thread

import httpx


class _CredentialHandler(BaseHTTPRequestHandler):
    def log_message(self, format, *args):
        """Suppress HTTP access logs that could contain credential capabilities.

        Args:
            format: Unused HTTP log template.
            *args: Unused HTTP log arguments.

        Returns:
            None.
        """

    def do_GET(self):
        """Return provider-format credentials for a valid local capability.

        Returns:
            None; writes the credential response or a sanitized error.
        """
        if self.path != "/credentials" or not hmac.compare_digest(
            self.headers.get("Authorization", "").encode(),
            f"Bearer {self.server.authorization['token']}".encode(),
        ):
            self.send_error(403, "Invalid credential capability")
            return
        try:
            response = httpx.post(
                self.server.authorization["url"],
                headers={
                    "Authorization": f"Bearer {self.server.authorization['token']}"
                },
                timeout=30,
                follow_redirects=False,
            )
            response.raise_for_status()
            lease = response.json()
            body = json.dumps(
                {
                    "AccessKeyId": lease["values"]["AWS_ACCESS_KEY_ID"],
                    "SecretAccessKey": lease["values"]["AWS_SECRET_ACCESS_KEY"],
                    "Token": lease["values"]["AWS_SESSION_TOKEN"],
                    "Expiration": lease["expires_at"],
                }
            ).encode()
        except httpx.HTTPError, ValueError, KeyError, TypeError:
            self.send_error(503, "Execution credentials are unavailable")
            return
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Cache-Control", "no-store")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        try:
            self.wfile.write(body)
        except BrokenPipeError, ConnectionResetError:
            return


class CredentialDelivery:
    """Serve refreshable credentials only on the execution's loopback interface.

    Args:
        authorization: Phase-scoped broker URL, token, and delivery protocol.

    Raises:
        ValueError: The requested delivery protocol is unsupported.
    """

    def __init__(self, authorization: dict | None):
        self.authorization = authorization
        self.server = None
        self.thread = None

    def __enter__(self) -> dict[str, str]:
        """Start a local credential endpoint for the phase.

        Returns:
            SDK environment overrides, or an empty mapping without a provider.

        Raises:
            ValueError: The delivery protocol is unsupported.
            OSError: The local credential endpoint cannot start.
        """
        if not self.authorization:
            return {}
        if self.authorization["protocol"] != "aws-container":
            raise ValueError("Unsupported execution credential delivery protocol")
        self.server = ThreadingHTTPServer(("127.0.0.1", 0), _CredentialHandler)
        self.server.authorization = self.authorization
        self.thread = Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        return {
            "AWS_CONTAINER_CREDENTIALS_FULL_URI": f"http://127.0.0.1:{self.server.server_port}/credentials",
            "AWS_CONTAINER_AUTHORIZATION_TOKEN": f"Bearer {self.authorization['token']}",
            "AWS_EC2_METADATA_DISABLED": "true",
        }

    def __exit__(self, exc_type, exc, traceback) -> None:
        """Stop the endpoint when the phase finishes or fails.

        Args:
            exc_type: Exception type, if any.
            exc: Exception instance, if any.
            traceback: Exception traceback, if any.

        Returns:
            None.
        """
        if self.server:
            self.server.shutdown()
            self.server.server_close()
            self.thread.join(timeout=5)
