"""Run the complete local Stacksmith deployment in Minikube."""

import base64
import ipaddress
import json
import os
import signal
import subprocess
import sys
import time
from pathlib import Path
from urllib.parse import quote
from urllib.request import URLError, urlopen

from dotenv import dotenv_values

NAMESPACE = "stacksmith-platform"
RELEASE = "stacksmith"
POSTGRES = "stacksmith-postgres"
STORAGE = "stacksmith-storage"


def _run(*args: str, input_text: str | None = None) -> str:
    result = subprocess.run(
        args,
        input=input_text,
        text=True,
        capture_output=True,
        check=False,
        shell=False,
    )
    if result.returncode:
        print(result.stderr.strip(), file=sys.stderr)
        raise subprocess.CalledProcessError(
            result.returncode, args, result.stdout, result.stderr
        )
    return result.stdout


def _kubectl(*args: str, input_text: str | None = None) -> str:
    return _run("kubectl", "--context", "minikube", *args, input_text=input_text)


def _required(values: dict[str, str], key: str) -> str:
    if not values.get(key):
        raise ValueError(f"Set {key} in .env before running kube-up")
    return values[key]


def _secret(name: str, values: dict[str, str]) -> dict:
    return {
        "apiVersion": "v1",
        "kind": "Secret",
        "metadata": {"name": name, "namespace": NAMESPACE},
        "type": "Opaque",
        "data": {
            key: base64.b64encode(value.encode()).decode()
            for key, value in values.items()
        },
    }


def _service(name: str, port: int) -> dict:
    return {
        "apiVersion": "v1",
        "kind": "Service",
        "metadata": {"name": name, "namespace": NAMESPACE},
        "spec": {
            "selector": {"app": name},
            "ports": [{"name": "tcp", "port": port, "targetPort": port}],
        },
    }


def _volume(name: str) -> dict:
    return {
        "apiVersion": "v1",
        "kind": "PersistentVolumeClaim",
        "metadata": {"name": name, "namespace": NAMESPACE},
        "spec": {
            "accessModes": ["ReadWriteOnce"],
            "resources": {"requests": {"storage": "2Gi"}},
        },
    }


def _deployment(name: str, container: dict, volume: str, mount: str) -> dict:
    container.setdefault("volumeMounts", []).append(
        {"name": "data", "mountPath": mount}
    )
    return {
        "apiVersion": "apps/v1",
        "kind": "Deployment",
        "metadata": {"name": name, "namespace": NAMESPACE},
        "spec": {
            "replicas": 1,
            "strategy": {"type": "Recreate"},
            "selector": {"matchLabels": {"app": name}},
            "template": {
                "metadata": {"labels": {"app": name}},
                "spec": {
                    "containers": [container],
                    "volumes": [
                        {"name": "data", "persistentVolumeClaim": {"claimName": volume}}
                    ],
                },
            },
        },
    }


def _database_url(user: str, password: str) -> str:
    return (
        f"postgresql+psycopg://{user}:{quote(password, safe='')}@"
        f"{POSTGRES}.{NAMESPACE}.svc/platform"
    )


def _credentials() -> dict[str, str]:
    if os.environ.get("AWS_ACCESS_KEY_ID") and os.environ.get("AWS_SECRET_ACCESS_KEY"):
        return {
            key: os.environ[key]
            for key in (
                "AWS_ACCESS_KEY_ID",
                "AWS_SECRET_ACCESS_KEY",
                "AWS_SESSION_TOKEN",
            )
            if os.environ.get(key)
        }
    try:
        exported = json.loads(
            _run(
                "aws",
                "configure",
                "export-credentials",
                "--profile",
                "root",
                "--format",
                "process",
            )
        )
    except (OSError, subprocess.CalledProcessError) as exc:
        raise ValueError("Run `aws login --profile root` before kube-up") from exc
    return {
        name: exported[key]
        for name, key in (
            ("AWS_ACCESS_KEY_ID", "AccessKeyId"),
            ("AWS_SECRET_ACCESS_KEY", "SecretAccessKey"),
            ("AWS_SESSION_TOKEN", "SessionToken"),
        )
        if exported.get(key)
    }


def _configuration(
    values: dict[str, str], credentials: dict[str, str]
) -> dict[str, str]:
    run_environment = json.loads(
        values.get("STACKSMITH_PLATFORM_RUN_ENVIRONMENT") or "{}"
    )
    run_environment.update(
        {
            key.removeprefix("STACKSMITH_RUN_"): value
            for key, value in {**values, **os.environ}.items()
            if key.startswith("STACKSMITH_RUN_") and value
        }
    )
    if not json.loads(
        values.get("STACKSMITH_PLATFORM_EXECUTION_CREDENTIAL_PROVIDERS") or "{}"
    ):
        run_environment.update(credentials)
    values.update(credentials)
    values["STACKSMITH_PLATFORM_RUN_ENVIRONMENT"] = json.dumps(run_environment)
    values["STACKSMITH_PLATFORM_ARTIFACT_ENDPOINT"] = (
        f"http://{STORAGE}.{NAMESPACE}.svc:9000"
    )
    return values


def _resources(values: dict[str, str], credentials: dict[str, str]) -> list[dict]:
    postgres = {
        "name": POSTGRES,
        "image": "postgres:18-alpine",
        "envFrom": [{"secretRef": {"name": "stacksmith-local-postgres"}}],
        "ports": [{"containerPort": 5432}],
        "readinessProbe": {
            "exec": {"command": ["pg_isready", "-U", "postgres", "-d", "platform"]}
        },
        "volumeMounts": [{"name": "init", "mountPath": "/docker-entrypoint-initdb.d"}],
    }
    storage = {
        "name": STORAGE,
        "image": "chrislusf/seaweedfs:4.48",
        "args": [
            "mini",
            "-dir=/data",
            "-s3.port=9000",
            "-webdav=false",
            "-admin.ui=false",
            "-master.telemetry=false",
        ],
        "envFrom": [{"secretRef": {"name": "stacksmith-local-storage"}}],
        "ports": [{"containerPort": 9000}],
        "readinessProbe": {"tcpSocket": {"port": 9000}, "periodSeconds": 5},
    }
    database = _deployment(
        POSTGRES, postgres, f"{POSTGRES}-data", "/var/lib/postgresql"
    )
    database["spec"]["template"]["spec"]["volumes"].append(
        {"name": "init", "configMap": {"name": "stacksmith-database-init"}}
    )
    return [
        {"apiVersion": "v1", "kind": "Namespace", "metadata": {"name": NAMESPACE}},
        {
            "apiVersion": "v1",
            "kind": "ConfigMap",
            "metadata": {"name": "stacksmith-database-init", "namespace": NAMESPACE},
            "data": {"10-roles.sql": Path("deploy/postgres/init.sql").read_text()},
        },
        _secret(
            "stacksmith-local-postgres",
            {
                "POSTGRES_USER": "platform",
                "POSTGRES_DB": "platform",
                "POSTGRES_PASSWORD": _required(
                    values, "STACKSMITH_PLATFORM_DB_PASSWORD"
                ),
                "PLATFORM_API_DB_PASSWORD": _required(
                    values, "STACKSMITH_PLATFORM_API_DB_PASSWORD"
                ),
                "PLATFORM_WORKER_DB_PASSWORD": _required(
                    values, "STACKSMITH_PLATFORM_WORKER_DB_PASSWORD"
                ),
            },
        ),
        _secret(
            "stacksmith-local-storage",
            {
                "AWS_ACCESS_KEY_ID": _required(
                    values, "STACKSMITH_PLATFORM_ARTIFACT_ACCESS_KEY"
                ),
                "AWS_SECRET_ACCESS_KEY": _required(
                    values, "STACKSMITH_PLATFORM_ARTIFACT_SECRET_KEY"
                ),
                "S3_BUCKET": values.get("STACKSMITH_PLATFORM_ARTIFACT_BUCKET")
                or "stacksmith-platform",
            },
        ),
        _volume(f"{POSTGRES}-data"),
        _volume(f"{STORAGE}-data"),
        database,
        _deployment(STORAGE, storage, f"{STORAGE}-data", "/data"),
        _service(POSTGRES, 5432),
        _service(STORAGE, 9000),
        _secret(
            "stacksmith-platform-config", _configuration(values.copy(), credentials)
        ),
        *[
            _secret(
                name,
                {"STACKSMITH_PLATFORM_DATABASE_URL": _database_url(user, password)},
            )
            for name, user, password in (
                (
                    "stacksmith-platform-api-database",
                    "platform_api",
                    _required(values, "STACKSMITH_PLATFORM_API_DB_PASSWORD"),
                ),
                (
                    "stacksmith-platform-supervisor-database",
                    "platform_worker",
                    _required(values, "STACKSMITH_PLATFORM_WORKER_DB_PASSWORD"),
                ),
                (
                    "stacksmith-platform-migration-database",
                    "platform",
                    _required(values, "STACKSMITH_PLATFORM_DB_PASSWORD"),
                ),
            )
        ],
    ]


def _storage_cidr() -> str:
    service = json.loads(
        _kubectl("-n", NAMESPACE, "get", "service", STORAGE, "-o", "json")
    )
    return str(ipaddress.ip_network(f"{service['spec']['clusterIP']}/32"))


def _create_bucket() -> None:
    _kubectl(
        "-n",
        NAMESPACE,
        "exec",
        f"deployment/{RELEASE}-api",
        "--",
        "python",
        "-c",
        "from stacksmith_platform.settings import Settings; "
        "from stacksmith_platform.storage import ArtifactStore; "
        "from botocore.exceptions import ClientError; "
        "store = ArtifactStore(Settings()); "
        "store.client.create_bucket(Bucket=store.bucket) "
        "if store.bucket not in [item['Name'] for item in "
        "store.client.list_buckets()['Buckets']] else None",
    )


def _forward() -> None:
    pid_file = Path(".data/kube-port-forward.pid")
    if pid_file.exists():
        try:
            pid = int(pid_file.read_text())
            os.kill(pid, 0)
        except OSError, ValueError:
            pass
        else:
            try:
                with urlopen("http://127.0.0.1:8000/healthz", timeout=2) as response:
                    if response.status == 200:
                        print("Stacksmith is available at http://localhost:8000")
                        return
            except OSError, URLError:
                pass
            command = subprocess.run(
                ["ps", "-p", str(pid), "-o", "command="],
                shell=False,
                check=False,
                capture_output=True,
                text=True,
            ).stdout
            if (
                "kubectl --context minikube -n stacksmith-platform port-forward"
                in command
            ):
                try:
                    os.kill(pid, signal.SIGTERM)
                except ProcessLookupError:
                    pass
    try:
        with urlopen("http://127.0.0.1:8000/api/health", timeout=2) as response:
            if response.status == 200:
                raise RuntimeError(
                    "Port 8000 is in use. Stop Compose or its current port forward, "
                    "then run `uv run poe kube-start`."
                )
    except OSError, URLError:
        pass
    Path(".data").mkdir(exist_ok=True)
    with Path(".data/kube-port-forward.log").open("a") as output:
        process = subprocess.Popen(
            [
                "kubectl",
                "--context",
                "minikube",
                "-n",
                NAMESPACE,
                "port-forward",
                f"service/{RELEASE}-frontend",
                "8000:8080",
            ],
            stdin=subprocess.DEVNULL,
            stdout=output,
            stderr=output,
            start_new_session=True,
        )
    pid_file.write_text(f"{process.pid}\n")
    for _ in range(20):
        if process.poll() is not None:
            raise RuntimeError(
                "Port forwarding failed; see .data/kube-port-forward.log"
            )
        try:
            with urlopen("http://127.0.0.1:8000/healthz", timeout=1) as response:
                if response.status == 200:
                    print("Stacksmith is available at http://localhost:8000")
                    return
        except OSError, URLError:
            time.sleep(0.5)
    raise RuntimeError(
        "Port forwarding did not become ready; see .data/kube-port-forward.log"
    )


def up() -> None:
    """Build inputs are loaded and the complete local Minikube stack is started.

    Returns:
        None.

    Raises:
        ValueError: Required local configuration or image is missing.
        subprocess.CalledProcessError: A Minikube, Kubernetes, or Helm command fails.
        RuntimeError: Local port forwarding cannot start.
    """
    if _run("kubectl", "config", "current-context").strip() != "minikube":
        raise ValueError("Select the minikube kubectl context before running kube-up")
    values = {
        key: value
        for key, value in dotenv_values(".env", interpolate=False).items()
        if value is not None
    }
    credentials = _credentials()
    for image in (
        values.get("STACKSMITH_PLATFORM_IMAGE") or "stacksmith-platform:local",
        values.get("STACKSMITH_PLATFORM_FRONTEND_IMAGE")
        or "stacksmith-platform-frontend:local",
        "stacksmith-platform-execution:local",
        "postgres:18-alpine",
        "chrislusf/seaweedfs:4.48",
    ):
        print(f"Loading {image} into Minikube", flush=True)
        _run("minikube", "image", "load", "--overwrite=true", image)
    _kubectl(
        "apply",
        "--server-side",
        "-f",
        "-",
        input_text=json.dumps(
            {
                "apiVersion": "v1",
                "kind": "List",
                "items": _resources(values, credentials),
            }
        ),
    )
    for deployment in (POSTGRES, STORAGE):
        print(f"Waiting for {deployment}", flush=True)
        _kubectl(
            "-n",
            NAMESPACE,
            "rollout",
            "status",
            f"deployment/{deployment}",
            "--timeout=180s",
        )
    print("Installing Stacksmith Helm chart", flush=True)
    _run(
        "helm",
        "upgrade",
        "--install",
        RELEASE,
        "deploy/helm/stacksmith-platform",
        "--kube-context",
        "minikube",
        "--namespace",
        NAMESPACE,
        "--wait",
        "--timeout",
        "5m",
        "--set-string",
        f"image={values.get('STACKSMITH_PLATFORM_IMAGE') or 'stacksmith-platform:local'}",
        "--set-string",
        f"frontendImage={values.get('STACKSMITH_PLATFORM_FRONTEND_IMAGE') or 'stacksmith-platform-frontend:local'}",
        "--set-string",
        "executionImage=stacksmith-platform-execution:local",
        "--set",
        "allowLocalExecutionImage=true",
        "--set-string",
        f"publicUrl={values.get('STACKSMITH_PLATFORM_PUBLIC_URL') or 'http://localhost:8000'}",
        "--set-json",
        f"adminLogins={values.get('STACKSMITH_PLATFORM_ADMIN_LOGINS') or '[]'}",
        "--set-json",
        f'privateEgress=[{{"cidr":"{_storage_cidr()}","port":9000}}]',
    )
    _kubectl("-n", NAMESPACE, "rollout", "restart", f"deployment/{RELEASE}-api")
    _kubectl(
        "-n",
        NAMESPACE,
        "rollout",
        "status",
        f"deployment/{RELEASE}-api",
        "--timeout=180s",
    )
    _create_bucket()
    _kubectl("-n", NAMESPACE, "rollout", "restart", f"deployment/{RELEASE}-frontend")
    _kubectl(
        "-n",
        NAMESPACE,
        "rollout",
        "status",
        f"deployment/{RELEASE}-frontend",
        "--timeout=180s",
    )
    _forward()


def forward() -> None:
    """Restore browser access to the running local Minikube frontend.

    Returns:
        None.

    Raises:
        RuntimeError: Port 8000 is occupied or forwarding cannot start.
    """
    _forward()


if __name__ == "__main__":
    sys.exit(up())
