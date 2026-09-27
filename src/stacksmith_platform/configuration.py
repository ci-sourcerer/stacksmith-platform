import re
from pathlib import Path

from .database import configuration, row
from .errors import ComposerError


def configuration_policy(connection, settings, repository=None):
    """Return stored policy and the effective ordered configuration layers.

    Args:
        connection: Active database connection.
        settings: Deployment bootstrap settings.
        repository: Optional registered repository record.

    Returns:
        Policy, platform defaults, and effective layer references.
    """
    platform = row(connection, configuration, "platform")
    defaults = platform["policy"]["refs"] if platform else [str(settings.config)]
    stored = (
        row(connection, configuration, repository["id"]) if repository else platform
    )
    policy = (
        stored["policy"]
        if stored
        else (
            {"mode": "replace", "refs": [repository["config"]]}
            if repository and repository.get("config")
            else {
                "mode": "inherit" if repository else "replace",
                "refs": [] if repository else defaults,
            }
        )
    )
    effective = (
        policy["refs"] if policy["mode"] == "replace" else [*defaults, *policy["refs"]]
    )
    return {
        **policy,
        "platform_refs": defaults,
        "effective_refs": list(dict.fromkeys(effective)),
    }


def effective_config(connection, settings, repository):
    """Return immutable effective configuration references for an execution.

    Args:
        connection: Active database connection.
        settings: Deployment bootstrap settings.
        repository: Registered repository record.

    Returns:
        Ordered references encoded for Stacksmith's CI contract.
    """
    return ",".join(
        configuration_policy(connection, settings, repository)["effective_refs"]
    )


def validate_references(refs):
    """Validate approved GitHub references before storing policy.

    Args:
        refs: Ordered configuration layer references.

    Returns:
        None.

    Raises:
        ComposerError: A reference is not an immutable GitHub file URL.
    """
    if any(
        not re.fullmatch(r"git\+https://github\.com/[^\s,]+@[0-9a-f]{40}", ref)
        for ref in refs
    ):
        raise ComposerError(
            "Each configuration layer must be a GitHub git+https file reference pinned to a full commit SHA.",
            422,
        )


def config_arguments(value: str | Path) -> list[str]:
    """Expand ordered configuration references into explicit CLI arguments.

    Args:
        value: Local file or comma-separated approved references.

    Returns:
        Repeated configuration arguments preserving layer order.
    """
    return [
        argument
        for reference in str(value).split(",")
        for argument in ("--config", reference)
    ]
