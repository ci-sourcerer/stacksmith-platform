"""Project-owned Compose orchestration, adapted from common-python-tasks.

The foreground/watch lifecycle follows its MIT-licensed stack task, while
configuration and services belong to Stacksmith Platform.
"""

import json
import os
import platform as python_platform
import signal
import subprocess
import sys
from pathlib import Path

from common_python_tasks.project import get_project_version, get_uv_version
from dotenv import dotenv_values

from scripts import build_execution, configure, images

_MODE = Path(".data/stack-debug")
_DOCKERFILE = Path(".data/Dockerfile.api")


def _run_environment():
    values = {
        key: value
        for key, value in dotenv_values(".env", interpolate=False).items()
        if value is not None
    }
    return {
        **json.loads(values.get("STACKSMITH_PLATFORM_RUN_ENVIRONMENT") or "{}"),
        **{
            key.removeprefix("STACKSMITH_RUN_"): value
            for key, value in {**values, **os.environ}.items()
            if key.startswith("STACKSMITH_RUN_")
        },
    }


def _environment():
    # Poe loads .env before builds; discard stale platform values after builders
    # update image IDs and private keys in the file.
    return {
        **{
            key: value
            for key, value in os.environ.items()
            if not key.startswith("STACKSMITH_PLATFORM_")
        },
        "STACKSMITH_PLATFORM_RUN_ENVIRONMENT": json.dumps(_run_environment()),
        **(
            {
                "STACKSMITH_BUILD_PYTHON_VERSION": python_platform.python_version(),
                "STACKSMITH_BUILD_UV_VERSION": get_uv_version(),
                "STACKSMITH_BUILD_PACKAGE_VERSION": get_project_version(),
            }
            if _DOCKERFILE.exists()
            else {}
        ),
    }


def _run(*arguments, debug=None, check=True, capture_output=False, environment=None):
    return subprocess.run(
        [
            "docker",
            "compose",
            "--file",
            "compose.yaml",
            *(
                ["--file", "compose.debug.yaml"]
                if (debug if debug is not None else _MODE.exists())
                else []
            ),
            *arguments,
        ],
        shell=False,
        check=check,
        env={**_environment(), **(environment or {})},
        capture_output=capture_output,
        text=True,
    )


def _clear_debug():
    _MODE.unlink(missing_ok=True)
    _DOCKERFILE.unlink(missing_ok=True)


def _foreground(debug, services=(), no_rebuild=False):
    status = 0
    up_args = ["--force-recreate", *([] if services else ["--remove-orphans"]), *services]
    if debug:
        up_args.insert(0, "--watch")
    if no_rebuild:
        up_args.insert(0, "--no-build")
    try:
        status = _run("up", *up_args, debug=debug, check=False).returncode
    except KeyboardInterrupt:
        status = 130
    finally:
        # The terminal sends SIGINT to both Python and Compose. Let teardown
        # finish even when the foreground command was interrupted.
        previous_handler = signal.signal(signal.SIGINT, signal.SIG_IGN)
        try:
            cleanup_result = _run(
                "rm",
                "--force",
                "--stop",
                "--volumes",
                *services,
                debug=debug,
                check=False,
                capture_output=True,
            )
            cleanup_status = cleanup_result.returncode
            if cleanup_status != 0:
                if cleanup_result.stdout:
                    print(cleanup_result.stdout, file=sys.stdout)
                if cleanup_result.stderr:
                    print(cleanup_result.stderr, file=sys.stderr)
        finally:
            signal.signal(signal.SIGINT, previous_handler)
            _clear_debug()
    sys.exit(status or cleanup_status)


def up(
    debug: bool = False,
    detach: bool = False,
    no_cache: bool = False,
    no_rebuild: bool = False,
    services: list[str] | None = None,
) -> None:
    """Build or reuse local images, then start the platform stack.

    Args:
        debug: Enable Python debugging and frontend development; watch when attached.
        detach: Start in the background and wait for healthy services.
        no_cache: Disable image build caches.
        no_rebuild: Reuse the current local images instead of rebuilding them.
        services: Selected services and their dependencies; all services by default.

    Returns:
        None.

    Raises:
        SystemExit: Configuration is invalid or foreground startup has finished.
        subprocess.CalledProcessError: An image build or detached startup fails.
        OSError: Development state cannot be written.
    """
    configure.check()
    services = _selected_services(services, debug)
    if no_rebuild:
        print("Reusing current local images; skipping stack-up rebuilds.")
    else:
        if not services or set(services).intersection(
            {"api", "worker", "migrate", "database-grants", "storage-init"}
        ):
            images.platform(no_cache=no_cache)
        if not services or "frontend" in services:
            images.frontend(debug=debug, no_cache=no_cache)
        if not services or set(services).intersection({"api", "worker"}):
            build_execution.main(no_cache=no_cache)
    if debug:
        if not services or set(services).intersection({"api", "worker"}):
            if not no_rebuild:
                _DOCKERFILE.parent.mkdir(exist_ok=True)
                _DOCKERFILE.write_text(images.platform(debug=True, no_cache=no_cache))
        _MODE.parent.mkdir(exist_ok=True)
        _MODE.touch()
    else:
        _clear_debug()
    if detach:
        _run(
            "up",
            "--detach",
            "--wait",
            *( ["--no-build"] if no_rebuild else [] ),
            "--force-recreate",
            *([] if services else ["--remove-orphans"]),
            *services,
            debug=debug,
        )
    else:
        _foreground(debug, services, no_rebuild)


def down() -> None:
    """Stop and remove stack containers, preserving named data volumes.

    Returns:
        None.

    Raises:
        subprocess.CalledProcessError: Compose cannot remove the containers.
        OSError: Development state cannot be removed.
    """
    _run("rm", "--force", "--stop", "--volumes")
    _clear_debug()


def logs() -> None:
    """Follow frontend, API, and worker logs.

    Returns:
        None.

    Raises:
        subprocess.CalledProcessError: Compose cannot read the logs.
    """
    _run("logs", "--follow", "frontend", "api", "worker")


def db_shell() -> None:
    """Open an interactive administrator psql shell in the running database.

    Returns:
        None.

    Raises:
        subprocess.CalledProcessError: The database shell fails.
    """
    _run("up", "--detach", "--wait", "postgres", debug=False)
    _run("exec", "postgres", "psql", "--username", "platform", "--dbname", "platform")


def run_db_migrations(no_build: bool = False) -> None:
    """Upgrade the platform schema and refresh runtime role grants.

    Args:
        no_build: Use the prepared image instead of building current source.

    Returns:
        None.

    Raises:
        SystemExit: Required configuration is missing.
        subprocess.CalledProcessError: Initialization or database grants fail.
    """
    configure.check()
    if not no_build:
        images.platform()
    _run("up", "--detach", "--wait", "postgres", debug=False)
    _run("run", "--rm", "--no-deps", "migrate", debug=False)
    _run("run", "--rm", "--no-deps", "database-grants", debug=False)


def _selected_services(services, debug):
    if not services:
        return []
    definitions = json.loads(
        _run("config", "--format", "json", debug=debug, capture_output=True).stdout
    )["services"]
    selected = set()
    pending = list(services)
    while pending:
        name = pending.pop()
        if name not in definitions:
            raise ValueError(f"Unknown Compose service: {name}")
        if name not in selected:
            selected.add(name)
            pending.extend(definitions[name].get("depends_on", {}))
    return sorted(selected)


def reset_db() -> None:
    """Stop the local stack and remove only its PostgreSQL data volume.

    Returns:
        None; recreate the database with the next stack startup.

    Raises:
        ValueError: The PostgreSQL volume is external.
        subprocess.CalledProcessError: Compose or Docker fails.
    """
    volume = json.loads(_run("config", "--format", "json", capture_output=True).stdout)[
        "volumes"
    ]["postgres"]
    if volume.get("external"):
        raise ValueError("Refusing to reset an external PostgreSQL volume")
    down()
    if (
        volume["name"]
        in subprocess.run(
            ["docker", "volume", "ls", "--format", "{{.Name}}"],
            shell=False,
            check=True,
            capture_output=True,
            text=True,
        ).stdout.splitlines()
    ):
        subprocess.run(
            ["docker", "volume", "rm", volume["name"]], shell=False, check=True
        )


def _migration_tools(*arguments):
    configure.check()
    images.platform(debug=True)
    _run("up", "--detach", "--wait", "postgres", debug=False)
    _run(
        "--file",
        "compose.migrations.yaml",
        "run",
        "--rm",
        "--no-deps",
        "migration-tools",
        *arguments,
        debug=False,
        environment={"LOCAL_UID": str(os.getuid()), "LOCAL_GID": str(os.getgid())},
    )


def create_db_migration(message: str, empty: bool = False) -> None:
    """Generate a migration into the checkout using current source in Docker.

    Args:
        message: Description for the new revision.
        empty: Create an empty revision instead of autogenerating operations.

    Returns:
        None.

    Raises:
        SystemExit: Configuration is incomplete.
        subprocess.CalledProcessError: Building or migration generation fails.
    """
    _migration_tools("revision", "--message", message, *(["--empty"] if empty else []))


def check_db_migrations() -> None:
    """Check that database revisions and model metadata agree.

    Returns:
        None.

    Raises:
        SystemExit: Configuration is incomplete.
        subprocess.CalledProcessError: Building or migration checks fail.
    """
    _migration_tools("check")
