"""Initialize and validate the single local Compose environment file."""

import os
import re
import secrets
import sys
from pathlib import Path
from tempfile import NamedTemporaryFile

from dotenv import dotenv_values, set_key
from pydantic import ValidationError
from pydantic_settings import SettingsError

from stacksmith_platform.settings import Settings

_GENERATED = (
    "STACKSMITH_PLATFORM_SECRET_KEY",
    "STACKSMITH_PLATFORM_DB_PASSWORD",
    "STACKSMITH_PLATFORM_API_DB_PASSWORD",
    "STACKSMITH_PLATFORM_WORKER_DB_PASSWORD",
    "STACKSMITH_PLATFORM_ARTIFACT_SECRET_KEY",
    "STACKSMITH_PLATFORM_GITHUB_WEBHOOK_SECRET",
)
_MIGRATED = (
    "GITHUB_CLIENT_ID",
    "GITHUB_CLIENT_SECRET",
    "SECRET_KEY",
    "CONFIG",
    "DRAFT_PULL_REQUESTS",
)
_REQUIRED = ("GITHUB_CLIENT_ID", "GITHUB_CLIENT_SECRET", "GITHUB_APP_ID", "CONFIG")


def configure(path: Path, template: Path) -> list[str]:
    """Merge the Compose template with existing credentials and generated secrets.

    Args:
        path: Private environment file to create or update.
        template: Commented environment template.

    Returns:
        Human-owned configuration fields that still need attention.

    Raises:
        OSError: The template or environment cannot be read or written.
    """
    existing = dict(dotenv_values(path, interpolate=False)) if path.exists() else {}
    values = {
        **dotenv_values(template, interpolate=False),
        **{
            key: value
            for key, value in existing.items()
            if not key.startswith("COMPOSER_")
        },
    }
    for suffix in _MIGRATED:
        if not existing.get(f"STACKSMITH_PLATFORM_{suffix}") and existing.get(
            f"COMPOSER_{suffix}"
        ):
            values[f"STACKSMITH_PLATFORM_{suffix}"] = existing[f"COMPOSER_{suffix}"]
    for key in _GENERATED:
        if not values.get(key):
            values[key] = secrets.token_urlsafe(48)
    _import_key(values)
    _write(path, template, values)
    return _missing(values)


def _write(path, template, values):
    remaining = dict(values)
    lines = []
    for line in template.read_text().splitlines():
        match = re.match(r"^([A-Z][A-Z0-9_]*)=", line)
        if match:
            line = _assignment(match[1], remaining.pop(match[1], ""))
        lines.append(line)
    if remaining:
        lines.extend(["", "# Additional local settings preserved by configure."])
        lines.extend(_assignment(key, value) for key, value in remaining.items())
    with NamedTemporaryFile(
        mode="w", dir=path.parent, prefix=".env.", suffix=".tmp", delete=False
    ) as output:
        output.write("\n".join(lines) + "\n")
        output.flush()
        os.fchmod(output.fileno(), 0o600)
        temporary = Path(output.name)
    temporary.replace(path)


def _assignment(key, value):
    return f"{key}='" + (value or "").replace("'", "\\'") + "'"


def _import_key(values):
    if (
        values.get("STACKSMITH_PLATFORM_GITHUB_PRIVATE_KEY_FILE")
        and Path(values["STACKSMITH_PLATFORM_GITHUB_PRIVATE_KEY_FILE"]).is_file()
    ):
        values["STACKSMITH_PLATFORM_GITHUB_PRIVATE_KEY"] = Path(
            values["STACKSMITH_PLATFORM_GITHUB_PRIVATE_KEY_FILE"]
        ).read_text()


def _missing(values):
    missing = [
        f"STACKSMITH_PLATFORM_{suffix}"
        for suffix in _REQUIRED
        if not values.get(f"STACKSMITH_PLATFORM_{suffix}")
    ]
    if values.get("STACKSMITH_PLATFORM_ADMIN_LOGINS", "[]") in {"", "[]"}:
        missing.append("STACKSMITH_PLATFORM_ADMIN_LOGINS")
    if not values.get("STACKSMITH_PLATFORM_GITHUB_PRIVATE_KEY"):
        missing.append(
            "STACKSMITH_PLATFORM_GITHUB_PRIVATE_KEY_FILE (save the App PEM at this path)"
        )
    if not values.get("STACKSMITH_PLATFORM_STACKSMITH_IMAGE") and not values.get(
        "STACKSMITH_PLATFORM_EXECUTION_IMAGE"
    ):
        missing.append(
            "STACKSMITH_PLATFORM_STACKSMITH_IMAGE or STACKSMITH_PLATFORM_EXECUTION_IMAGE"
        )
    return missing


def main() -> None:
    """Populate `.env` without printing any credential values.

    Returns:
        None.

    Raises:
        OSError: Configuration files cannot be read or written.
    """
    missing = configure(Path(".env"), Path(".env.example"))
    print(
        "Updated .env with preserved settings and generated local secrets (mode 0600)."
    )
    if missing:
        print("Complete these settings before running poe stack-up:")
        for key in missing:
            print(f"  {key}")


def check() -> None:
    """Validate Compose configuration before building images or starting services.

    Returns:
        None; exits nonzero for incomplete or invalid configuration.
    """
    values = dotenv_values(".env", interpolate=False)
    _import_key(values)
    missing = _missing(values)
    if missing:
        print("Compose configuration is incomplete. Run poe configure and fill:")
        for key in missing:
            print(f"  {key}")
        sys.exit(1)
    set_key(
        ".env",
        "STACKSMITH_PLATFORM_GITHUB_PRIVATE_KEY",
        values["STACKSMITH_PLATFORM_GITHUB_PRIVATE_KEY"],
    )
    Path(".env").chmod(0o600)
    try:
        Settings(
            _env_file=".env",
            execution_enabled=True,
            runner="docker",
            github_private_key=values["STACKSMITH_PLATFORM_GITHUB_PRIVATE_KEY"],
            github_private_key_file=None,
            database_url="postgresql+psycopg://configured:configured@postgres/platform",
            execution_image=values.get("STACKSMITH_PLATFORM_EXECUTION_IMAGE")
            or "preflight@sha256:" + "0" * 64,
        )
    except ValidationError as exc:
        print("Invalid platform configuration:")
        for error in exc.errors(
            include_input=False, include_context=False, include_url=False
        ):
            print(
                f"  {'.'.join(str(part) for part in error['loc']) or 'settings'}: {error['msg']}"
            )
        sys.exit(1)
    except SettingsError:
        print("ADMIN_LOGINS and SECRET_REFERENCES must contain valid JSON.")
        sys.exit(1)
    for key in _GENERATED:
        if not values.get(key):
            print(f"Missing {key}; run poe configure.")
            sys.exit(1)
    for suffix in ("DB_PASSWORD", "API_DB_PASSWORD", "WORKER_DB_PASSWORD"):
        if not re.fullmatch(r"[A-Za-z0-9_-]+", values[f"STACKSMITH_PLATFORM_{suffix}"]):
            print(
                f"STACKSMITH_PLATFORM_{suffix} must use URL-safe letters, digits, underscores, and hyphens."
            )
            sys.exit(1)
    print("Compose configuration is ready.")


if __name__ == "__main__":
    main()
