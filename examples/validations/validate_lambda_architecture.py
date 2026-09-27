"""Require planned Lambda functions to use arm64."""

from typing import Any


def validate(value: Any, **context: Any) -> str | dict[str, str]:
    """Validate Lambda architectures in an OpenTofu plan.

    Args:
        value: OpenTofu plan JSON supplied by Stacksmith.
        **context: Plan validation context.

    Returns:
        A Stacksmith plan-validation outcome.
    """
    for change in value.get("resource_changes", []) if isinstance(value, dict) else []:
        if change.get("type") != "aws_lambda_function":
            continue
        if (change.get("change") or {}).get("after", {}).get("architectures") != [
            "arm64"
        ]:
            return {
                "status": "fail",
                "message": "Lambda functions must use the arm64 architecture.",
            }
    return "pass"
