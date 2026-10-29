import hashlib
import json
import os
import ssl
import time
from collections import OrderedDict
from contextlib import ExitStack
from dataclasses import dataclass
from functools import partial
from threading import Lock
from typing import Any
from uuid import uuid4

import aiohttp
import anyio
import certifi
from anyio.from_thread import start_blocking_portal

from .errors import ComposerError


@dataclass(frozen=True)
class GitHubResponse:
    """Hold a fully consumed response outside the HTTP event loop.

    Args:
        status_code: HTTP response status.
        content: Response body.
    """

    status_code: int
    content: bytes

    @property
    def is_error(self) -> bool:
        """Identify HTTP failures.

        Returns:
            Whether the status is a client or server error.
        """
        return self.status_code >= 400

    def json(self) -> Any:
        """Decode the response body.

        Returns:
            Decoded JSON value.

        Raises:
            ValueError: The response does not contain valid JSON.
        """
        return json.loads(self.content)


class GitHubTransport:
    """Share a bounded aiohttp pool among synchronous GitHub callers.

    The owner must call `close` after its requests finish. The event-loop thread
    and session are started lazily and never contain shared user credentials.

    Args:
        base_url: GitHub API origin, overridable for local transport tests.
    """

    def __init__(self, base_url: str = "https://api.github.com"):
        self._base_url = base_url
        self._lifecycle = ExitStack()
        self._lock = Lock()
        self._portal = None
        self._session = None
        self._closed = False
        self.request_count = 0
        self.conditional_hits = 0
        self.counter_id = uuid4().hex
        self._conditional = OrderedDict()

    def request(self, method: str, path: str, **kwargs) -> GitHubResponse:
        """Perform a request using the process's shared connection pool.

        Args:
            method: HTTP method.
            path: Absolute API path.
            **kwargs: Per-request headers, parameters, and body.

        Returns:
            Fully consumed HTTP response.

        Raises:
            ComposerError: GitHub is unreachable or the request times out.
            RuntimeError: The transport has already been closed.
        """
        with self._lock:
            if self._closed:
                raise RuntimeError("GitHub transport is closed")
            self.request_count += 1
            if self._portal is None:
                self._portal = self._lifecycle.enter_context(
                    start_blocking_portal(name="github-http")
                )
            pending = self._portal.start_task_soon(
                partial(self._request, method, path, **kwargs)
            )
        return pending.result()

    async def _request(self, method, path, **kwargs):
        if self._session is None:
            self._session = aiohttp.ClientSession(
                base_url=self._base_url,
                connector=aiohttp.TCPConnector(
                    limit=20,
                    limit_per_host=20,
                    keepalive_timeout=30,
                    ssl=ssl.create_default_context(
                        cafile=os.environ.get("SSL_CERT_FILE") or certifi.where()
                    ),
                ),
                cookie_jar=aiohttp.DummyCookieJar(),
                timeout=aiohttp.ClientTimeout(total=20),
                trust_env=True,
            )
        key = _conditional_key(method, path, kwargs)
        cached = self._conditional.get(key)
        if cached and time.monotonic() - cached[2] >= 300:
            self._conditional.pop(key, None)
            cached = None
        if cached:
            kwargs = {
                **kwargs,
                "headers": {**kwargs.get("headers", {}), "If-None-Match": cached[0]},
            }
        try:
            async with self._session.request(
                method, path, allow_redirects=False, **kwargs
            ) as response:
                return self._conditional_response(
                    key,
                    cached,
                    GitHubResponse(response.status, await response.read()),
                    response.headers.get("etag"),
                )
        except (aiohttp.ClientError, TimeoutError) as exc:
            raise ComposerError(
                "GitHub is temporarily unreachable. Please retry.", 502
            ) from exc

    def _conditional_response(self, key, cached, response, etag):
        if response.status_code == 304:
            if not cached:
                return response
            self.conditional_hits += 1
            response, etag = cached[1], etag or cached[0]
        if (
            key
            and response.status_code == 200
            and etag
            and len(response.content) <= 512 * 1024
        ):
            self._conditional[key] = (etag, response, time.monotonic())
            self._conditional.move_to_end(key)
            while (
                len(self._conditional) > 128
                or sum(len(entry[1].content) for entry in self._conditional.values())
                > 4 * 1024**2
            ):
                self._conditional.popitem(last=False)
        elif key:
            self._conditional.pop(key, None)
        return response

    async def _close_session(self):
        self._conditional.clear()
        if self._session is not None:
            await self._session.close()
            # Allow TLS transports to close before stopping their event loop.
            await anyio.sleep(0.25)

    def close(self) -> None:
        """Release pooled connections and join the HTTP event-loop thread.

        Returns:
            None.
        """
        with self._lock:
            if self._closed:
                return
            self._closed = True
            try:
                if self._portal is not None:
                    self._portal.call(self._close_session)
            finally:
                self._lifecycle.close()


def _conditional_key(method, path, options):
    headers = {
        name.lower(): value for name, value in options.get("headers", {}).items()
    }
    if (
        method != "GET"
        or not headers.get("authorization")
        or "if-none-match" in headers
        or "if-modified-since" in headers
    ):
        return None
    return hashlib.sha256(
        json.dumps(
            [path, options.get("params"), headers], sort_keys=True, default=str
        ).encode()
    ).hexdigest()
