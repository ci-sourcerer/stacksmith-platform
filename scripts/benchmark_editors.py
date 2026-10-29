import argparse
import asyncio
import copy
import json
import math
import random
import subprocess
import time
from contextlib import AsyncExitStack
from pathlib import Path
from uuid import uuid4

import docker
import httpx
from kubernetes import client as kubernetes_client
from kubernetes import config as kubernetes_config
from kubernetes import watch as kubernetes_watch
from kubernetes.client.exceptions import ApiException
from requests.exceptions import RequestException
from urllib3.exceptions import HTTPError

from stacksmith_platform.documents import load_document


class Editor:
    """One independently authenticated editor with monotonic draft generations.

    Args:
        client: Session-specific HTTP client.
        index: Editor index controlling stack size and source visibility.
        users: Total editor count.
        environment: Optional deployment environment for this editor.
        workload: Policy-only or connected serverless fixture.
    """

    def __init__(self, client, index, users, environment=None, workload="serverless"):
        self.client = client
        self.index = index
        self.users = users
        self.environment = environment
        self.workload = workload
        self.identifier = f"capacity-{uuid4().hex}"
        self.generation = 0
        self.body = None
        self.samples = []
        self.source_samples = []
        self.initial_seconds = None
        self.failures = []
        self.superseded = 0
        self.source_reused = 0

    async def open(self, repository, path):
        """Open an immutable snapshot and immediately test its first loaded edit.

        Args:
            repository: Authorized owner and repository name.
            path: Discovered stack filename.

        Returns:
            None after initial inspection and the first loaded-stack edit.

        Raises:
            httpx.HTTPError: The snapshot cannot be opened.
            ValueError: The supplied fixture lacks a representative policy component.
        """
        started = time.monotonic()
        platform = await self.client.get(
            "/api/platform", params={"repository": repository}
        )
        platform.raise_for_status()
        response = await self.client.get(
            f"/api/repositories/{repository}/stack", params={"path": path}
        )
        response.raise_for_status()
        snapshot = response.json()
        original_name, original = next(iter(snapshot["document"]["components"].items()))
        if original["type"] != "aws_iam_policy":
            raise ValueError(
                "The capacity fixture must contain an aws_iam_policy component"
            )
        components = {}
        for index in range(1 + self.index * 24 // max(1, self.users - 1)):
            components[original_name if index == 0 else f"capacity_{index}"] = (
                _component(original, self.index, index, self.workload)
            )
        self.body = {
            "snapshot": snapshot["snapshot"],
            "environment": self.environment,
            "components": components,
            "disabled_associations": [],
            "positions": {},
            "routes": [],
            "editor_id": self.identifier,
        }
        self.generation = 1
        response = await self.client.post(
            "/api/review", json={**self.body, "generation": 1}
        )
        response.raise_for_status()
        self._validate(response.json(), components, review=True)
        self.initial_seconds = time.monotonic() - started
        await self.edit()

    async def edit(self, source_visible=False):
        """Submit a settled edit including the browser's 400 ms debounce.

        Args:
            source_visible: Whether independent source output is also requested.

        Returns:
            None. Latency and failures are recorded on this editor.
        """
        self.generation += 1
        generation = self.generation
        first = next(iter(self.body["components"].values()))
        first["properties"]["description"] = (
            f"Capacity editor {self.index} generation {generation}"
        )
        body = copy.deepcopy({**self.body, "generation": generation})
        started = time.monotonic()
        await asyncio.sleep(0.4)
        accepted = await self._request("/api/review", body, started, self.samples)
        if source_visible and generation == self.generation:
            if accepted:
                self.source_reused += 1
                self.source_samples.append(time.monotonic() - started)
            else:
                await self._request("/api/source", body, started, self.source_samples)

    async def _request(self, path, body, started, samples):
        try:
            response = await self.client.post(path, json=body)
            if (
                response.status_code == 409
                and response.json().get("diagnostics", {}).get("code")
                == "inspection_superseded"
            ):
                self.superseded += 1
                return
            response.raise_for_status()
            self._validate(response.json(), body["components"], path == "/api/review")
            if body["generation"] == self.generation:
                samples.append(time.monotonic() - started)
                return True
        except (httpx.HTTPError, ValueError) as exc:
            self.failures.append(
                {
                    "operation": path,
                    "generation": body["generation"],
                    "error": type(exc).__name__,
                }
            )

    def _validate(self, result, expected, review=False):
        if load_document(result["source"]).get("components") != expected:
            raise ValueError("Accepted source does not match the submitted generation")
        if (
            review
            and self.workload == "serverless"
            and len(expected) > 1
            and not result["associations"]["applications"]
        ):
            raise ValueError(
                "Connected fixture has no managed association applications"
            )
        if (
            review
            and self.workload == "serverless"
            and {
                (edge["source"], edge["target"], edge["property"], edge["rule"])
                for edge in result["edges"]
                if edge["kind"] == "association"
            }
            != _expected_connections(expected)
        ):
            raise ValueError("Managed connections do not match the submitted fixture")


def _expected_connections(components):
    role = next(
        (name for name, value in components.items() if value["type"] == "aws_iam_role"),
        None,
    )
    return (
        {
            (
                next(
                    name
                    for name, value in components.items()
                    if value["type"] == "aws_iam_policy"
                ),
                role,
                "policy_arns",
                "role-baseline-policies",
            ),
            *(
                (role, name, "role_arn", "lambda-execution-role")
                for name, value in components.items()
                if value["type"] == "aws_lambda_function"
            ),
        }
        if role
        else set()
    )


def _component(original, editor, index, workload):
    if workload == "policies" or index == 0:
        component = copy.deepcopy(original)
        component.setdefault("properties", {})["name"] = (
            f"stacksmith-capacity-{editor}-{index}"
        )
        if workload == "serverless":
            component["tags"] = ["lambda_baseline"]
        return component
    if index == 1:
        return {
            "type": "aws_iam_role",
            "tags": ["lambda_execution"],
            "properties": {
                "role_name": f"stacksmith-capacity-{editor}-role",
                "trusted_service": "lambda.amazonaws.com",
            },
        }
    return {
        "type": "aws_lambda_function",
        "properties": {
            "function_name": f"stacksmith-capacity-{editor}-{index}",
            "image_uri": "example.invalid/capacity:fixture",
            "architecture": "arm64",
            "memory_size": 128,
            "timeout": 30,
            "environment_variables": {},
        },
    }


async def _timeline(editor, duration, source_users):
    randomizer = random.Random(editor.index)
    events = {duration * fraction for fraction in (0.1, 0.3, 0.5, 0.7, 0.9)}
    when = randomizer.uniform(20, 60)
    while when < duration:
        events.add(when)
        when += randomizer.uniform(20, 60)
    started = time.monotonic()
    pending = []
    for when in sorted(events):
        await asyncio.sleep(max(0, started + when - time.monotonic()))
        pending.append(asyncio.create_task(editor.edit(editor.index < source_users)))
    await asyncio.gather(*pending)


def _resources(observer):
    result = observer.exec_run(["cat", "/proc/stat", "/proc/meminfo"])
    if result.exit_code:
        raise ValueError("Resource observer cannot read VM counters")
    lines = result.output.decode().splitlines()
    cpu = [int(value) for value in lines[0].split()[1:]]
    memory = {
        line.split(":", 1)[0]: int(line.split()[1]) * 1024
        for line in lines
        if line.startswith(("MemTotal:", "MemAvailable:"))
    }
    return {
        "at": time.monotonic(),
        "cpu": cpu,
        "memory_total": memory["MemTotal"],
        "memory_used": memory["MemTotal"] - memory["MemAvailable"],
    }


def _database_activity(observer, args, since=None):
    command = [
        "python",
        "-c",
        (
            "import json; from sqlalchemy import create_engine,text; "
            "from stacksmith_platform.settings import Settings; "
            "engine=create_engine(Settings().database_url); "
            "connection=engine.connect(); "
            "stats=dict(connection.execute(text("
            '"SELECT xact_commit,xact_rollback,blks_read,blks_hit,tup_returned,tup_fetched,'
            "tup_inserted,tup_updated,tup_deleted,numbackends FROM pg_stat_database "
            'WHERE datname=current_database()")).mappings().one()); '
            f"since={since!r}; "
            'delays=[] if since is None else [list(row) for row in connection.execute(text("'
            "SELECT CAST(execution->>'started' AS DOUBLE PRECISION)-created, updated-CAST(execution->>'started' AS DOUBLE PRECISION) "
            "FROM runs WHERE action='inspection' AND created>=:since "
            "AND status IN ('succeeded','failed','cancelled','expired') AND execution IS NOT NULL"
            '"), {"since":since})]; '
            'jobs=connection.execute(text("SELECT count(*) FROM runs WHERE action=:action AND created>=:since"), {"action":"inspection","since":since or 0}).scalar_one(); '
            'print(json.dumps({"database":stats,"delays":delays,"jobs":jobs})); '
            "connection.close(); engine.dispose()"
        ),
    ]
    if args.kubernetes_namespace:
        result = subprocess.run(
            [
                "kubectl",
                "--kubeconfig",
                args.kubeconfig,
                "--context",
                args.kubernetes_context,
                "-n",
                args.kubernetes_namespace,
                "exec",
                f"deployment/{args.kubernetes_release}-api",
                "--",
                *command,
            ],
            capture_output=True,
            check=True,
            shell=False,
        )
        return json.loads(result.stdout)
    result = observer.exec_run(command)
    if result.exit_code:
        raise ValueError("Database activity observer failed")
    return json.loads(result.output)


async def _observe(client, observer, stop, snapshots):
    while not stop.is_set():
        sample = await asyncio.to_thread(_resources, observer)
        response = await client.get("/api/editor-status")
        response.raise_for_status()
        sample["status"] = response.json()
        snapshots.append(sample)
        try:
            await asyncio.wait_for(stop.wait(), 2)
        except TimeoutError:
            continue


def _observe_kubernetes_ooms(args, stop, events, errors):
    kubernetes_config.load_kube_config(
        config_file=args.kubeconfig, context=args.kubernetes_context
    )
    api = kubernetes_client.CoreV1Api()
    try:
        while not stop.is_set():
            stream = kubernetes_watch.Watch()
            try:
                for event in stream.stream(
                    api.list_pod_for_all_namespaces, timeout_seconds=30
                ):
                    pod = event["object"]
                    for status in [
                        *(pod.status.container_statuses or []),
                        *(pod.status.init_container_statuses or []),
                    ]:
                        if any(
                            state
                            and state.terminated
                            and state.terminated.reason == "OOMKilled"
                            for state in (status.state, status.last_state)
                        ):
                            events.add(pod.metadata.uid)
                    if stop.is_set():
                        break
            except (ApiException, HTTPError, OSError) as exc:
                errors.append(type(exc).__name__)
                time.sleep(1)
            finally:
                stream.stop()
    finally:
        api.api_client.close()


def _observe_docker_ooms(stream, stop, events, errors):
    try:
        for event in stream:
            events.add((event.get("id"), event.get("timeNano")))
        if not stop.is_set():
            errors.append("UnexpectedEndOfStream")
    except (docker.errors.DockerException, RequestException, OSError) as exc:
        if not stop.is_set():
            errors.append(type(exc).__name__)


def _summary(values):
    if not values:
        return {
            "count": 0,
            "p50_seconds": None,
            "p95_seconds": None,
            "maximum_seconds": None,
        }
    values = sorted(values)
    return {
        "count": len(values),
        "p50_seconds": values[(len(values) - 1) // 2],
        "p95_seconds": values[math.ceil(len(values) * 0.95) - 1],
        "maximum_seconds": values[-1],
    }


def _environment_for(index, args):
    return (
        args.environment
        if index
        in {
            round(position * (args.users - 1) / max(1, args.environment_users - 1))
            for position in range(args.environment_users)
        }
        else None
    )


def _report(editors, resources, args):
    latency = _summary([value for editor in editors for value in editor.samples])
    failures = [failure for editor in editors for failure in editor.failures]
    final_pool = resources[-1]["status"]
    first_cpu = resources[0]["cpu"]
    last_cpu = resources[-1]["cpu"]
    total_cpu = sum(last_cpu[:8]) - sum(first_cpu[:8])
    idle_cpu = sum(last_cpu[3:5]) - sum(first_cpu[3:5])
    memory_peak = max(sample["memory_used"] for sample in resources)
    memory_budget = min(resources[0]["memory_total"], 16 * 1024**3)
    query_budget_held = all(
        sample["status"].get("database_pool", {}).get("capacity")
        and sample["status"]["database_pool"]["peak_in_use"]
        <= sample["status"]["database_pool"]["capacity"]
        and sample["status"]["database_pool"]["checkout_timeouts"] == 0
        for sample in resources
    )
    return {
        "users": args.users,
        "duration_seconds": args.duration,
        "component_range": [
            min(len(editor.body["components"]) for editor in editors),
            max(len(editor.body["components"]) for editor in editors),
        ],
        "workload": args.workload,
        "debounce_seconds": 0.4,
        "source_viewers": args.source_users,
        "source_reused": sum(editor.source_reused for editor in editors),
        "latency_by_environment": {
            environment or "stack_preview": _summary(
                [
                    sample
                    for editor in editors
                    if editor.environment == environment
                    for sample in editor.samples
                ]
            )
            for environment in {editor.environment for editor in editors}
        },
        "simultaneous_edit_bursts": 5,
        "latency": latency,
        "initial_load": _summary([editor.initial_seconds for editor in editors]),
        "source_latency": _summary(
            [value for editor in editors for value in editor.source_samples]
        ),
        "github_requests": final_pool["github_requests"]
        - resources[0]["status"]["github_requests"],
        "github_conditional_hits": final_pool["github_conditional_hits"]
        - resources[0]["status"]["github_conditional_hits"],
        "failures": failures,
        "superseded": sum(editor.superseded for editor in editors),
        "resource_scope": "Complete Docker VM, including other running workloads and Kubernetes control plane if present",
        "vm_memory_gib": resources[0]["memory_total"] / 1024**3,
        "peak_vm_memory_gib": memory_peak / 1024**3,
        "memory_headroom_fraction": 1 - memory_peak / memory_budget,
        "mean_vm_cpu_percent": 100 * (1 - idle_cpu / max(1, total_cpu)),
        "final_status": final_pool,
        "query_connection_budget_held": query_budget_held,
        "configuration_subscribers": max(
            sample["status"]["notifications"]["subscribers"] for sample in resources
        ),
        "listener_limit_held": all(
            sample["status"]["notifications"]["listener_connections"] == 1
            for sample in resources
        ),
        "github_counter_consistent": len(
            {sample["status"]["github_counter_id"] for sample in resources}
        )
        == 1,
        "resource_samples": resources,
        "acceptance_run": args.users == 15
        and args.duration >= 600
        and args.source_users >= 3
        and args.workload == "serverless",
        "passed": bool(
            latency["count"]
            and args.users == 15
            and args.duration >= 600
            and args.source_users >= 3
            and args.workload == "serverless"
            and latency["p95_seconds"] < 5
            and not failures
            and final_pool["contexts"]["pending"] == 0
            and not any(
                final_pool["inspections"].get(state, 0)
                for state in ("queued", "running", "cancelling")
            )
            and memory_peak <= memory_budget * 0.8
            and query_budget_held
            and all(
                sample["status"]["notifications"]["listener_connections"] == 1
                and sample["status"]["notifications"]["subscribers"] >= args.users
                for sample in resources
            )
            and len({sample["status"]["github_counter_id"] for sample in resources})
            == 1
        ),
    }


async def _run(args):
    if (
        args.users < 1
        or args.duration <= 0
        or not 0 <= args.source_users <= args.users
        or not 0 <= args.environment_users <= args.users
    ):
        raise ValueError(
            "Supply positive users and duration, with source/environment users within the total"
        )
    if args.environment_users and not args.environment:
        raise ValueError("Supply an environment for environment editors")
    cookies = json.loads(Path(args.cookies).read_text())
    if len(cookies) < args.users:
        raise ValueError("Supply one isolated session cookie per editor")
    runtime = docker.from_env()
    observer = runtime.containers.get(args.observer_container)
    database_before = (await asyncio.to_thread(_database_activity, observer, args))[
        "database"
    ]
    started = time.time()
    resources = []
    stop = asyncio.Event()
    kube_ooms = set()
    kube_errors = []
    docker_ooms = set()
    docker_errors = []
    docker_stream = await asyncio.to_thread(
        runtime.events,
        since=int(started),
        filters={"type": "container", "event": "oom"},
        decode=True,
    )
    docker_monitor = asyncio.create_task(
        asyncio.to_thread(
            _observe_docker_ooms, docker_stream, stop, docker_ooms, docker_errors
        )
    )
    kube_monitor = (
        asyncio.create_task(
            asyncio.to_thread(
                _observe_kubernetes_ooms, args, stop, kube_ooms, kube_errors
            )
        )
        if args.kubernetes_namespace
        else None
    )
    async with AsyncExitStack() as stack:
        editors = [
            Editor(
                await stack.enter_async_context(
                    httpx.AsyncClient(
                        base_url=args.url,
                        cookies={"platform_session": cookies[index]},
                        headers={"X-Composer-Request": "1", "Origin": args.url},
                        timeout=180,
                    )
                ),
                index,
                args.users,
                _environment_for(index, args),
                args.workload,
            )
            for index in range(args.users)
        ]
        for editor in editors:
            events = await stack.enter_async_context(
                editor.client.stream("GET", "/api/configuration-events")
            )
            events.raise_for_status()
        monitor = asyncio.create_task(
            _observe(editors[0].client, observer, stop, resources)
        )
        try:
            while not resources:
                if monitor.done():
                    await monitor
                await asyncio.sleep(0.01)
            await asyncio.gather(
                *(editor.open(args.repository, args.path) for editor in editors)
            )
            initial_database = await asyncio.to_thread(
                _database_activity, observer, args, started
            )
            edits_started = time.time()
            print(
                "All editors loaded; initial loaded-stack edits completed", flush=True
            )
            await asyncio.gather(
                *(
                    _timeline(editor, args.duration, args.source_users)
                    for editor in editors
                )
            )
        finally:
            stop.set()
            await asyncio.to_thread(docker_stream.close)
            await docker_monitor
            await monitor
            if kube_monitor:
                await kube_monitor
        response = await editors[0].client.get("/api/editor-status")
        response.raise_for_status()
        resources[-1]["status"] = response.json()
        report = _report(editors, resources, args)
        database_after = await asyncio.to_thread(
            _database_activity, observer, args, edits_started
        )
        report["database_activity"] = {
            key: database_after["database"][key] - database_before[key]
            for key in database_before
            if key != "numbackends"
        }
        report["database_connections_final"] = database_after["database"]["numbackends"]
        report["queue_wait"] = _summary(
            [delay[0] for delay in database_after["delays"]]
        )
        report["execution_wall_time"] = _summary(
            [delay[1] for delay in database_after["delays"]]
        )
        report["initial_inspection_jobs"] = initial_database["jobs"]
        report["inspection_jobs_during_edits"] = database_after["jobs"]
        report["initial_queue_wait"] = _summary(
            [delay[0] for delay in initial_database["delays"]]
        )
        report["initial_execution_wall_time"] = _summary(
            [delay[1] for delay in initial_database["delays"]]
        )
        report["passed"] = (
            report["passed"]
            and database_after["jobs"] == 0
            and initial_database["jobs"] <= args.users
        )
        report["oom_events"] = len(docker_ooms)
        report["docker_observation_errors"] = docker_errors
        report["passed"] = report["passed"] and not docker_ooms and not docker_errors
        report["kubernetes_oom_pods"] = len(kube_ooms)
        report["kubernetes_observation_errors"] = kube_errors
        report["passed"] = report["passed"] and not kube_ooms and not kube_errors
        Path(args.report).parent.mkdir(parents=True, exist_ok=True)
        Path(args.report).write_text(json.dumps(report, indent=2) + "\n")
        print(
            json.dumps(
                {
                    key: value
                    for key, value in report.items()
                    if key not in {"resource_samples", "failures"}
                },
                indent=2,
            )
        )
        return 0 if report["passed"] else 1


def main() -> int:
    """Run the repeatable authenticated 15-editor inspection capacity scenario.

    Returns:
        Zero only when latency, source correctness, backlog, and memory checks pass.

    Raises:
        ValueError: Fixtures or measurement inputs are invalid.
        httpx.HTTPError: Initial loading or monitoring fails.
        docker.errors.DockerException: The resource observer is unavailable.
    """
    parser = argparse.ArgumentParser(
        description="Run isolated inspection capacity tests"
    )
    parser.add_argument("--url", required=True)
    parser.add_argument(
        "--cookies",
        required=True,
        help="Private JSON array of temporary session cookies",
    )
    parser.add_argument("--repository", required=True)
    parser.add_argument("--path", default="stack.yaml")
    parser.add_argument("--duration", type=float, default=600)
    parser.add_argument("--users", type=int, default=15)
    parser.add_argument("--source-users", type=int, default=3)
    parser.add_argument(
        "--workload", choices=("policies", "serverless"), default="serverless"
    )
    parser.add_argument("--environment")
    parser.add_argument("--environment-users", type=int, default=0)
    parser.add_argument("--observer-container", required=True)
    parser.add_argument("--report", required=True)
    parser.add_argument("--kubeconfig", default="")
    parser.add_argument("--kubernetes-context", default="")
    parser.add_argument("--kubernetes-namespace", default="")
    parser.add_argument("--kubernetes-release", default="inspection-bench")
    return asyncio.run(_run(parser.parse_args()))


if __name__ == "__main__":
    import sys

    sys.exit(main())
