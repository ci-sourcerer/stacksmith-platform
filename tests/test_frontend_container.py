import json
import os
import re
import socket
import subprocess
import time
from uuid import uuid4

import httpx
import pytest
from websockets.sync.client import connect

pytestmark = pytest.mark.skipif(
    os.environ.get("STACKSMITH_TEST_WEB_IMAGES") != "1",
    reason="Set STACKSMITH_TEST_WEB_IMAGES=1 after building the frontend images",
)

_BACKEND = """
import json
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

class Handler(BaseHTTPRequestHandler):
    def do_GET(self):
        if self.path == '/api/auth/github':
            self.send_response(307)
            self.send_header('Location', 'https://github.com/login/oauth/authorize')
            self.send_header('Set-Cookie', 'platform_oauth=test; HttpOnly; Path=/api/auth')
            self.end_headers()
            return
        if self.path == '/api/stream':
            self.send_response(200)
            self.send_header('Content-Length', '6')
            self.end_headers()
            self.wfile.write(b'first')
            self.wfile.flush()
            time.sleep(1)
            self.wfile.write(b'!')
            return
        self.send_response(404 if self.path == '/api/missing' else 200)
        self.send_header('Content-Type', 'application/json')
        if self.path == '/api/download':
            self.send_header('Content-Disposition', 'attachment; filename=report.json')
        self.end_headers()
        self.wfile.write(json.dumps({
            'path': self.path,
            'host': self.headers['Host'],
            'proto': self.headers.get('X-Forwarded-Proto'),
            'cookie': self.headers.get('Cookie'),
        }).encode())

    def do_POST(self):
        valid = self.headers.get('Origin') == 'https://' + self.headers['Host']
        self.send_response(200 if valid else 403)
        self.end_headers()

ThreadingHTTPServer(('0.0.0.0', 8000), Handler).serve_forever()
"""


def _docker(*arguments, check=True):
    result = subprocess.run(
        ["docker", *arguments],
        shell=False,
        check=check,
        capture_output=True,
        text=True,
    )
    return result.stdout.strip() + (result.stderr if arguments[0] == "logs" else "")


@pytest.fixture(params=["local", "debug"])
def frontend(request):
    name = f"stacksmith-web-test-{uuid4().hex[:10]}"
    _docker("network", "create", name)
    try:
        _docker(
            "run",
            "--detach",
            "--name",
            f"{name}-api",
            "--network",
            name,
            "--entrypoint",
            "python",
            "stacksmith-platform:local",
            "-u",
            "-c",
            _BACKEND,
        )
        _docker(
            "run",
            "--detach",
            "--name",
            name,
            "--network",
            name,
            *(["--read-only"] if request.param == "local" else []),
            "--tmpfs",
            "/tmp",
            "--publish",
            "127.0.0.1::8080",
            "--env",
            f"API_UPSTREAM=http://{name}-api:8000",
            f"stacksmith-platform-frontend:{request.param}",
        )
        with httpx.Client(
            base_url=f"http://{_docker('port', name, '8080/tcp')}", trust_env=False
        ) as client:
            for _ in range(100):
                try:
                    if client.get("/api/health").status_code == 200:
                        break
                except httpx.TransportError:
                    pass
                time.sleep(0.1)
            else:
                pytest.fail(_docker("logs", name))
            yield client, request.param, name
    finally:
        _docker("rm", "--force", name, f"{name}-api", check=False)
        _docker("network", "rm", name, check=False)


def test_frontend_serves_spa_and_preserves_api_contract(frontend):
    client, mode, name = frontend
    assert "<html" in client.get("/").text
    assert "<html" in client.get("/runs").text
    if mode == "local":
        assert client.get("/assets/missing.js").status_code == 404
    response = client.get("/api/auth/github")
    assert response.status_code == 307
    assert response.headers["location"] == "https://github.com/login/oauth/authorize"
    assert response.cookies["platform_oauth"] == "test"
    client.cookies.set("platform_session", "session")
    response = client.get("/api/headers", headers={"X-Forwarded-Proto": "https"})
    assert response.json()["host"] == str(client.base_url.netloc.decode())
    assert response.json()["proto"] == "https"
    assert "platform_session=session" in response.json()["cookie"]
    assert client.get("/api/missing").status_code == 404
    for path in ("/docs", "/redoc", "/openapi.json"):
        assert client.get(path).json()["path"] == path
    assert "report.json" in client.get("/api/download").headers["content-disposition"]
    assert (
        client.post(
            "/api/mutate",
            headers={"Origin": f"https://{client.base_url.netloc.decode()}"},
        ).status_code
        == 200
    )
    assert (
        client.post(
            "/api/mutate", headers={"Origin": "https://elsewhere.test"}
        ).status_code
        == 403
    )
    if mode == "debug":
        _assert_hmr(client, name)


def test_frontend_does_not_buffer_api_streams(frontend):
    client, _, _ = frontend
    started = time.monotonic()
    with client.stream("GET", "/api/stream") as response:
        chunks = response.iter_bytes(chunk_size=5)
        assert next(chunks) == b"first"
        assert time.monotonic() - started < 0.8
        assert b"".join(chunks) == b"!"


def _assert_hmr(client, name):
    assert client.get("/src/App.tsx").status_code == 200
    with connect(
        str(client.base_url).replace("http://", "ws://")
        + "?token="
        + re.search(r'const wsToken = "([^"]+)"', client.get("/@vite/client").text)[1],
        subprotocols=["vite-hmr"],
    ) as websocket:
        assert json.loads(websocket.recv(timeout=5))["type"] == "connected"
        _docker(
            "exec",
            name,
            "node",
            "--input-type=module",
            "-e",
            "import {appendFileSync} from 'node:fs'; "
            "appendFileSync('/web/src/App.tsx', '\\nexport const hmrSmoke = \"hmr-smoke\";\\n');",
        )
        assert json.loads(websocket.recv(timeout=5))["type"] in {
            "update",
            "full-reload",
        }
        assert "hmr-smoke" in client.get("/src/App.tsx").text


def test_python_debug_image_reloads_source_and_listens_for_debugger():
    name = f"stacksmith-debug-test-{uuid4().hex[:10]}"
    try:
        _docker(
            "run",
            "--detach",
            "--name",
            name,
            "--publish",
            "127.0.0.1::8000",
            "--publish",
            "127.0.0.1::5678",
            "--env",
            "PYTHONPATH=/tmp/build/src",
            "--env",
            "STACKSMITH_PLATFORM_DATABASE=/tmp/platform.sqlite3",
            "--workdir",
            "/tmp/build",
            "--entrypoint",
            "python",
            "stacksmith-platform:debug",
            "-Xfrozen_modules=off",
            "-m",
            "debugpy",
            "--listen",
            "0.0.0.0:5678",
            "-m",
            "stacksmith_platform",
            "--reload",
            "--reload-dir",
            "/tmp/build/src",
        )
        with httpx.Client(
            base_url=f"http://{_docker('port', name, '8000/tcp')}", trust_env=False
        ) as client:
            _wait_for_health(client, name, "ok")
            with socket.create_connection(
                ("127.0.0.1", int(_docker("port", name, "5678/tcp").split(":")[-1]))
            ):
                pass
            _docker(
                "exec",
                name,
                "python",
                "-c",
                "from pathlib import Path; "
                "path = Path('/tmp/build/src/stacksmith_platform/app.py'); "
                'path.write_text(path.read_text().replace(\'{"status": "ok"}\', \'{"status": "reloaded"}\'))',
            )
            _wait_for_health(client, name, "reloaded")
    finally:
        _docker("rm", "--force", name, check=False)


def _wait_for_health(client, name, expected):
    for _ in range(100):
        try:
            if client.get("/api/health").json() == {"status": expected}:
                return
        except httpx.TransportError:
            pass
        time.sleep(0.1)
    pytest.fail(_docker("logs", name))
