import hashlib
import importlib
import io
import json
import os
import signal
import subprocess
import sys
import tarfile
import time
from contextlib import chdir
from pathlib import Path

import httpx
from cryptography.fernet import Fernet

from .certificates import ca_environment, configure_ca_bundle
from .configuration import config_arguments
from .credential_delivery import CredentialDelivery
from .errors import ComposerError
from .inspection_results import _inspection_failure, _run_inspection

MAX_HANDOFF = 1024**3
PROVIDER_CACHE = Path("/work/home/.cache/terragrunt/providers")


def _download(url):
    output = bytearray()
    with httpx.stream("GET", url, timeout=120, follow_redirects=True) as response:
        response.raise_for_status()
        for chunk in response.iter_bytes():
            output.extend(chunk)
            if len(output) > MAX_HANDOFF:
                raise ValueError("Execution handoff exceeds size limit")
    return bytes(output)


def _extract(content, destination):
    with tarfile.open(fileobj=io.BytesIO(content)) as archive:
        if sum(member.size for member in archive.getmembers()) > 4 * MAX_HANDOFF:
            raise ValueError("Expanded execution workspace exceeds size limit")
        archive.extractall(destination, filter="data")


def _archive(path):
    output = io.BytesIO()
    with tarfile.open(fileobj=output, mode="w:gz", dereference=False) as archive:
        _archive_member(archive, path, "workspace", path.resolve())
    if output.tell() > MAX_HANDOFF:
        raise ValueError("Execution context exceeds size limit")
    return output.getvalue()


def _archive_member(archive, path, name, workspace, *, inputs=False, overrides=None):
    if inputs and (
        ".git" in path.parts
        or "__pycache__" in path.parts
        or path == workspace / ".stacksmith/build"
    ):
        return
    if overrides and path in overrides:
        member = tarfile.TarInfo(name)
        member.size = len(overrides[path])
        archive.addfile(member, io.BytesIO(overrides[path]))
        return
    if path.is_symlink():
        target = path.resolve()
        if target.is_relative_to(workspace):
            member = archive.gettarinfo(path, arcname=name)
            member.linkname = os.path.relpath(target, path.parent.resolve())
            archive.addfile(member)
            return
        if not target.is_relative_to(PROVIDER_CACHE.resolve()):
            raise ValueError("Workspace symlink points outside the execution context")
        # Provider caches live outside the workspace and disappear between phases.
        _archive_member(archive, target, name, workspace)
        return
    member = archive.gettarinfo(path, arcname=name)
    if member.isfile():
        with path.open("rb") as source:
            archive.addfile(member, source)
    elif member.isdir():
        archive.addfile(member)
        for child in sorted(path.iterdir()):
            _archive_member(
                archive,
                child,
                f"{name}/{child.name}",
                workspace,
                inputs=inputs,
                overrides=overrides,
            )
    elif member.islnk():
        archive.addfile(member)
    else:
        raise ValueError("Workspace contains an unsupported file type")


def _checkout(payload, directory):
    response = httpx.get(
        f"https://api.github.com/repos/{payload['repository']}/tarball/{payload['revision']}",
        headers={"Authorization": f"Bearer {payload['github_token']}"},
        timeout=30,
        follow_redirects=False,
    )
    if response.status_code != 302 or not response.headers.get(
        "location", ""
    ).startswith("https://codeload.github.com/"):
        raise ValueError("GitHub did not return an immutable source archive")
    _extract(_download(response.headers["location"]), directory / "checkout")
    entries = list((directory / "checkout").iterdir())
    if len(entries) != 1 or not entries[0].is_dir():
        raise ValueError("Invalid repository archive")
    entries[0].rename(directory / "workspace")


def _environment(payload):
    if payload.get("allow_local_backend") and payload.get("action") not in {
        "plan",
        "plan-operation",
    }:
        raise ValueError("Local backend overrides are restricted to test plans")
    return {
        "PATH": os.environ["PATH"],
        "HOME": "/work/home",
        "LANG": "C.UTF-8",
        "FORCE_COLOR": "1",
        "TERM": "xterm-256color",
        "STACKSMITH_GIT_TOKEN": payload["github_token"],
        "STACKSMITH_WARN_ON_UNLOCKED": "0",
        **ca_environment(),
        **payload["credentials"],
        **(
            {"STACKSMITH_CI_SKIP_BACKEND_VALIDATION": "1"}
            if payload.get("allow_local_backend")
            else {}
        ),
        "STACKSMITH_CI_SUMMARY_FORMAT": "none",
    }


def _prepare(payload, workspace, environment):
    command = [
        "stacksmith",
        "ci",
        "prepare",
        "--command",
        payload.get("command", payload["action"]),
        "--config-ref",
        payload["config"],
        "--gitops-root",
        payload["settings"]["gitops_root"],
        "--discovery-mode",
        payload["settings"]["discovery_mode"],
        "--environments",
        payload["environment"],
        "--event-name",
        "pull_request" if payload["event"] == "pull_request" else "push",
        "--ref-name",
        payload["default_branch"],
        "--default-branch",
        payload["default_branch"],
        "--after",
        payload["revision"],
        "--env-file",
        "/dev/null",
        "--format",
        "json",
    ]
    if payload["settings"].get("locked", True):
        command.append("--locked")
    if payload["operations"]:
        command.extend(["--operation-names", ",".join(payload["operations"])])
    if payload.get("tags"):
        command.extend(["--tags", ",".join(payload["tags"])])
    result = subprocess.run(
        command,
        shell=False,
        cwd=workspace,
        env=environment,
        stdout=subprocess.PIPE,
        timeout=payload["timeout"],
        check=False,
    )
    if result.returncode:
        return result.returncode
    manifest = json.loads(result.stdout)
    if manifest.get("version") != 2 or [
        item["environment"] for item in manifest.get("matrix", [])
    ] != [payload["environment"]]:
        raise ValueError("Unsupported or mismatched Stacksmith CI manifest")
    (workspace / ".platform-manifest.json").write_text(json.dumps(manifest))
    return 0


def _execute(payload, workspace):
    environment = _environment(payload)
    Path(environment["HOME"]).mkdir(parents=True, exist_ok=True)
    if payload["phase"] == "prepare":
        return _prepare(payload, workspace, environment)
    if (
        hashlib.sha256((workspace / ".platform-manifest.json").read_bytes()).hexdigest()
        != payload["manifest_digest"]
    ):
        raise ValueError("Prepared manifest digest changed")
    if payload["phase"] in {"apply", "destroy"}:
        plan = workspace / ".stacksmith-ci" / payload["environment"] / "reviewed.tfplan"
        if (
            not plan.is_file()
            or hashlib.sha256(plan.read_bytes()).hexdigest() != payload["plan_digest"]
        ):
            raise ValueError("Reviewed plan digest changed")
    return subprocess.run(
        [
            "stacksmith",
            "ci",
            "execute",
            "--manifest",
            ".platform-manifest.json",
            "--environment",
            payload["environment"],
            "--phase",
            payload["phase"],
            "--validation-report-output",
            f".stacksmith-ci/{payload['environment']}/validation-report.json",
        ],
        shell=False,
        cwd=workspace,
        env=environment,
        timeout=payload["timeout"],
        check=False,
    ).returncode


def _inspect(payload, directory):
    inspection = payload["inspection"]
    try:
        result = _prepare_inspection(payload, directory)
    except (ComposerError, httpx.HTTPError, ValueError, OSError) as exc:
        result = _inspection_failure(
            "prepare inspection",
            str(exc),
            "context_failed",
            redactions=[
                inspection["github_token"],
                *inspection.get("credentials", {}).values(),
            ],
        )
    return {**result, "revision": payload["revision"], "phase": "inspect"}


def _prepare_inspection(payload, directory):
    inspection = payload["inspection"]
    if inspection["args"] == ["platform-operations"]:
        return _inspect_operations(payload, directory)
    if inspection["args"] == ["platform-deployments"]:
        return _inspect_deployments(payload, directory)
    if (
        inspection.get("context")
        or inspection["args"] == ["review"]
        or (
            inspection["args"] == ["author"]
            and os.environ.get("PLATFORM_INSPECTION_PREWARMED")
        )
    ):
        return _inspect_authoring(payload, directory)
    workspace = directory / "workspace"
    workspace.mkdir()
    args = inspection["args"]
    if inspection["source"] is not None:
        (workspace / "stack.yaml").write_text(inspection["source"])
        args = [*args, "--stack", str(workspace / "stack.yaml")]
    if inspection["config_source"] is not None:
        (workspace / "config.yaml").write_text(inspection["config_source"])
    return _run_inspection(
        [
            "stacksmith",
            *args,
            *(
                []
                if inspection["authoring_request"] is not None
                else [
                    *config_arguments(
                        str(workspace / "config.yaml")
                        if inspection["config_source"] is not None
                        else inspection["config"]
                    ),
                    "--build-dir",
                    str(workspace / "build"),
                ]
            ),
        ],
        stage=" ".join(inspection["args"][:2]),
        authoring=inspection["authoring_request"] is not None,
        redactions=[inspection["github_token"]],
        input=json.dumps(inspection["authoring_request"])
        if inspection["authoring_request"] is not None
        else None,
        cwd=workspace,
        env={
            "PATH": os.environ["PATH"],
            "HOME": "/work",
            **ca_environment(),
            **(
                {"STACKSMITH_GIT_TOKEN": inspection["github_token"]}
                if inspection["github_token"]
                else {}
            ),
        },
        timeout=payload["timeout"],
    )


def _inspect_authoring(payload, directory):
    inspection = payload["inspection"]
    started = time.monotonic()
    cache_hit = _read_input_cache(inspection, directory)
    if cache_hit:
        inspection = {
            **inspection,
            "_cached_catalog": json.loads(
                (directory / "inspection-catalog.json").read_text()
            ),
        }
    elif inspection.get("context"):
        _checkout(
            {**inspection["context"], "github_token": inspection["github_token"]},
            directory,
        )
    else:
        (directory / "workspace").mkdir()
    if inspection["config_source"] is not None:
        (directory / "managed-config.yaml").write_text(inspection["config_source"])
        inspection = {**inspection, "config": str(directory / "managed-config.yaml")}
    preparation_duration = time.monotonic() - started
    result = (
        _warm_inspection(payload, inspection, directory)
        if os.environ.get("PLATFORM_INSPECTION_PREWARMED")
        else _run_inspection(
            [sys.executable, "-m", "stacksmith_platform.authoring_inspection"],
            stage=" ".join(inspection["args"][:2]),
            redactions=[
                inspection["github_token"],
                *inspection.get("credentials", {}).values(),
            ],
            input=json.dumps(inspection),
            cwd=directory / "workspace",
            env={
                "PATH": os.environ["PATH"],
                "HOME": "/work/home",
                **ca_environment(),
                **inspection.get("credentials", {}),
                "STACKSMITH_GIT_TOKEN": inspection["github_token"] or "",
            },
            timeout=payload["timeout"],
        )
    )
    if inspection["args"] == ["review"] and not result["exit_code"]:
        output = json.loads(result["inspection_output"])
        output["timings"]["preparation"] = preparation_duration
        output["timings"]["runtime"] = time.monotonic() - started
        if inspection.get("_cache"):
            output["timings"]["cache_hit"] = cache_hit
        if (
            output.pop("_cache_stable", False)
            and inspection.get("_cache")
            and not cache_hit
        ):
            result["_cache_written"] = _write_input_cache(
                inspection, directory, output["catalog"]
            )
        result["inspection_output"] = json.dumps(output)
    return result


def _read_input_cache(inspection, directory):
    cache = inspection.get("_cache") or {}
    if not cache.get("input_url"):
        return False
    try:
        _extract(
            Fernet(cache["input_key"].encode()).decrypt(_download(cache["input_url"])),
            directory,
        )
    except httpx.HTTPStatusError as exc:
        if exc.response.status_code != 404:
            raise
        return False
    return True


def _write_input_cache(inspection, directory, catalog):
    output = io.BytesIO()
    workspace = directory / "workspace"
    with tarfile.open(fileobj=output, mode="w:gz") as archive:
        _archive_member(
            archive,
            workspace,
            "workspace",
            workspace.resolve(),
            inputs=True,
            overrides={
                (workspace / inspection["context"]["path"]).resolve(): inspection[
                    "authoring_request"
                ]["source"].encode()
            },
        )
        content = json.dumps(catalog).encode()
        member = tarfile.TarInfo("inspection-catalog.json")
        member.size = len(content)
        archive.addfile(member, io.BytesIO(content))
    if output.tell() > 8 * 1024**2:
        return False
    try:
        response = httpx.put(
            inspection["_cache"]["output_url"],
            content=Fernet(inspection["_cache"]["output_key"].encode()).encrypt(
                output.getvalue()
            ),
            timeout=30,
        )
        response.raise_for_status()
    except httpx.HTTPError:
        # Inspection remains usable if the optional cache cannot be stored.
        return False
    return True


def _inspection_deadline(signum, frame):
    raise TimeoutError("Stacksmith inspection timed out.")


def _warm_inspection(payload, inspection, directory):
    from .authoring_inspection import _inspect_context
    from .documents import load_document, source_diff

    environment = {
        "PATH": os.environ["PATH"],
        "HOME": "/work/home",
        **ca_environment(),
        **inspection.get("credentials", {}),
        "STACKSMITH_GIT_TOKEN": inspection["github_token"] or "",
        "PLATFORM_INSPECTION_PREWARMED": "1",
    }
    os.environ.clear()
    os.environ.update(environment)
    previous = signal.signal(signal.SIGALRM, _inspection_deadline)
    signal.setitimer(signal.ITIMER_REAL, payload["timeout"])
    try:
        with chdir(directory / "workspace"):
            if inspection["args"] == ["author"]:
                from stacksmith.authoring import StackEdit, author_stack

                request = inspection["authoring_request"]
                proposed = author_stack(
                    request["source"],
                    [StackEdit.model_validate(edit) for edit in request["edits"]],
                    request["path"],
                )
                load_document(proposed.source)
                result = {
                    "version": 1,
                    "validation": "document",
                    "source": proposed.source,
                    "diff": source_diff(
                        request["source"], proposed.source, request["path"]
                    ),
                    "changed": proposed.source != request["source"],
                }
            else:
                result = _inspect_context(inspection)
        return {"exit_code": 0, "inspection_output": json.dumps(result)}
    except TimeoutError:
        return _inspection_failure(
            "inspection runtime", "Stacksmith inspection timed out.", "timeout", 504
        )
    finally:
        signal.setitimer(signal.ITIMER_REAL, 0)
        signal.signal(signal.SIGALRM, previous)


def _inspect_operations(payload, directory):
    inspection = payload["inspection"]
    source = json.loads(inspection["source"])
    _checkout({**source, "github_token": inspection["github_token"]}, directory)
    return _run_inspection(
        [sys.executable, "-m", "stacksmith_platform.operations"],
        input=json.dumps(source),
        stage="operation discovery",
        redactions=[inspection["github_token"]],
        cwd=directory / "workspace",
        env={
            "PATH": os.environ["PATH"],
            "HOME": "/work/home",
            **ca_environment(),
            "STACKSMITH_GIT_TOKEN": inspection["github_token"],
        },
        timeout=payload["timeout"],
    )


def _inspect_deployments(payload, directory):
    inspection = payload["inspection"]
    source = json.loads(inspection["source"])
    _checkout({**source, "github_token": inspection["github_token"]}, directory)
    return _run_inspection(
        [sys.executable, "-m", "stacksmith_platform.deployment_inspection"],
        input=json.dumps(source),
        stage="deployment discovery",
        redactions=[inspection["github_token"]],
        cwd=directory / "workspace",
        env={
            "PATH": os.environ["PATH"],
            "HOME": "/work/home",
            **ca_environment(),
            "STACKSMITH_GIT_TOKEN": inspection["github_token"],
        },
        timeout=payload["timeout"],
    )


def _result(payload, workspace, exit_code):
    result = {
        "exit_code": exit_code,
        "revision": payload["revision"],
        "phase": payload["phase"],
        "reports": {},
    }
    if (workspace / ".platform-manifest.json").is_file():
        result["manifest_digest"] = hashlib.sha256(
            (workspace / ".platform-manifest.json").read_bytes()
        ).hexdigest()
    for name in (
        "plan-summary.json",
        "apply-summary.json",
        "stacksmith-report.json",
        "validation-report.json",
    ):
        path = workspace / ".stacksmith-ci" / payload["environment"] / name
        if path.is_file() and path.stat().st_size < 4_000_000:
            result["reports"][name] = json.loads(path.read_text())
    plan = workspace / ".stacksmith-ci" / payload["environment"] / "reviewed.tfplan"
    if plan.is_file():
        result["plan_digest"] = hashlib.sha256(plan.read_bytes()).hexdigest()
    result["stacksmith_version"] = subprocess.run(
        ["stacksmith", "--version"],
        shell=False,
        capture_output=True,
        text=True,
        timeout=10,
        check=True,
    ).stdout.strip()
    return result


def main() -> None:
    """Claim and execute exactly one authorized isolated phase.

    Returns:
        None; exits with the phase's process status.

    Raises:
        httpx.HTTPError: Artifact handoff or claim service is unavailable.
        ValueError: Execution context or prepared inputs fail verification.
    """
    configure_ca_bundle(os.environ.get("PLATFORM_CA_BUNDLE"))
    environment = _standby() if os.environ.get("PLATFORM_SANDBOX_URL") else os.environ
    started = time.monotonic()
    cipher = Fernet(environment["PLATFORM_HANDOFF_KEY"].encode())
    payload = json.loads(cipher.decrypt(_download(environment["PLATFORM_INPUT_URL"])))
    if os.environ.get("PLATFORM_SANDBOX_URL") and payload["phase"] != "inspect":
        raise ValueError("Standby sandboxes only accept inspections.")
    if os.environ.get("PLATFORM_SANDBOX_URL"):
        os.environ.pop("PLATFORM_SANDBOX_TOKEN", None)
        os.environ["PLATFORM_INSPECTION_PREWARMED"] = "1"
    _inspection_stage("handoff_received", started)
    response = httpx.post(
        payload["claim_url"],
        headers={"Authorization": f"Bearer {payload['claim_token']}"},
        timeout=30,
    )
    response.raise_for_status()
    _inspection_stage("claimed", started)
    with CredentialDelivery(payload.get("credential_authorization")) as credentials:
        if payload["phase"] == "inspect":
            payload["inspection"].setdefault("credentials", {}).update(credentials)
        else:
            payload["credentials"].update(credentials)
        _execute_payload(payload, cipher)


def _standby():
    # Import trusted platform wrappers before advertising readiness; no job inputs exist yet.

    for module in (
        "stacksmith.authoring",
        "stacksmith.api",
        "stacksmith.loading",
        "stacksmith.inspector",
    ):
        importlib.import_module(module)
    while time.time() < float(os.environ["PLATFORM_SANDBOX_EXPIRES"]):
        response = httpx.get(
            os.environ["PLATFORM_SANDBOX_URL"],
            headers={"Authorization": f"Bearer {os.environ['PLATFORM_SANDBOX_TOKEN']}"},
            timeout=35,
        )
        response.raise_for_status()
        if response.status_code != 204:
            return response.json()
    raise ValueError("Unused inspection sandbox expired.")


def _execute_payload(payload, cipher):
    directory = Path("/work")
    if payload["phase"] == "inspect":
        started = time.monotonic()
        result = _inspect(payload, directory)
        _inspection_stage("work_finished", started)
        response = httpx.put(
            payload["result_output_url"],
            content=cipher.encrypt(json.dumps(result).encode()),
            timeout=120,
        )
        response.raise_for_status()
        _inspection_stage("upload_finished", started)
        _announce_inspection_result(payload)
        sys.exit(result["exit_code"])
    if payload.get("context_url"):
        context = _download(payload["context_url"])
        if hashlib.sha256(context).hexdigest() != payload["context_digest"]:
            raise ValueError("Reviewed execution context digest changed")
        _extract(cipher.decrypt(context), directory)
    else:
        _checkout(payload, directory)
    started = time.time()
    exit_code = 1
    try:
        exit_code = _execute(payload, directory / "workspace")
    except (subprocess.TimeoutExpired, ValueError, OSError) as exc:
        print(f"Phase could not complete: {type(exc).__name__}", flush=True)
    result = _result(payload, directory / "workspace", exit_code)
    result["duration"] = time.time() - started
    response = httpx.put(
        payload["context_output_url"],
        content=cipher.encrypt(_archive(directory / "workspace")),
        timeout=120,
    )
    response.raise_for_status()
    response = httpx.put(
        payload["result_output_url"],
        content=cipher.encrypt(json.dumps(result).encode()),
        timeout=120,
    )
    response.raise_for_status()
    sys.exit(exit_code)


def _inspection_stage(name, started):
    if os.environ.get("PLATFORM_INSPECTION_PREWARMED"):
        print(
            json.dumps(
                {
                    "inspection_stage": name,
                    "seconds": round(time.monotonic() - started, 6),
                    "timestamp": time.time(),
                }
            ),
            flush=True,
        )


def _announce_inspection_result(payload):
    if not payload.get("completion_url"):
        return
    try:
        response = httpx.post(
            payload["completion_url"],
            headers={"Authorization": f"Bearer {payload['claim_token']}"},
            timeout=10,
        )
        response.raise_for_status()
    except httpx.HTTPError:
        # Runtime events and the recovery scan still discover the uploaded result.
        print(
            "Inspection completion notification unavailable; runtime reconciliation will recover it.",
            flush=True,
        )


if __name__ == "__main__":
    main()
