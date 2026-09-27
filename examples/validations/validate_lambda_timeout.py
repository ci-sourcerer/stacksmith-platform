"""Limit planned Lambda function timeouts to 30 seconds."""

from typing import Any


def validate(value: Any, **context: Any) -> str | dict[str, str]:
    """Validate Lambda timeouts in an OpenTofu plan.

    Args:
        value: OpenTofu plan JSON supplied by Stacksmith.
        **context: Plan validation context.

    Returns:
        A Stacksmith plan-validation outcome.
    """
    for change in value.get("resource_changes", []) if isinstance(value, dict) else []:
        if change.get("type") != "aws_lambda_function":
            continue
        timeout = (change.get("change") or {}).get("after", {}).get("timeout")
        if not isinstance(timeout, int) or not 1 <= timeout <= 30:
            return {
                "status": "fail",
                "message": "Lambda timeout must be between 1 and 30 seconds.",
            }
    return "pass"
