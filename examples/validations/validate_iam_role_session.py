"""Limit planned IAM role sessions to one hour."""

from typing import Any


def validate(value: Any, **context: Any) -> str | dict[str, str]:
    """Validate IAM role session durations in an OpenTofu plan.

    Args:
        value: OpenTofu plan JSON supplied by Stacksmith.
        **context: Plan validation context.

    Returns:
        A Stacksmith plan-validation outcome.
    """
    for change in value.get("resource_changes", []) if isinstance(value, dict) else []:
        if change.get("type") != "aws_iam_role":
            continue
        duration = (
            (change.get("change") or {}).get("after", {}).get("max_session_duration")
        )
        if not isinstance(duration, int) or duration != 3600:
            return {
                "status": "fail",
                "message": "IAM role max_session_duration must be 3600 seconds.",
            }
    return "pass"
