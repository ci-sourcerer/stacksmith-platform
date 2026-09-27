"""Require planned IAM roles to trust only AWS Lambda."""

import json
from typing import Any


def _as_list(value: Any) -> list[Any]:
    return value if isinstance(value, list) else [value]


def _load_policy(value: Any) -> dict[str, Any] | None:
    if isinstance(value, dict):
        return value
    if not isinstance(value, str):
        return None
    try:
        parsed = json.loads(value)
    except json.JSONDecodeError:
        return None
    return parsed if isinstance(parsed, dict) else None


def _trusts_only_lambda(policy: dict[str, Any]) -> bool:
    statements = _as_list(policy.get("Statement", []))
    if not statements:
        return False
    for statement in statements:
        if not isinstance(statement, dict) or statement.get("Effect") != "Allow":
            return False
        if set(_as_list(statement.get("Action"))) != {"sts:AssumeRole"}:
            return False
        if statement.get("Principal") == "*":
            return False
        principal = statement.get("Principal")
        if not isinstance(principal, dict):
            return False
        if set(principal) != {"Service"}:
            return False
        if set(_as_list(principal["Service"])) != {"lambda.amazonaws.com"}:
            return False
    return True


def validate(value: Any, **context: Any) -> str | dict[str, str]:
    """Validate IAM role trust policies in an OpenTofu plan.

    Args:
        value: OpenTofu plan JSON supplied by Stacksmith.
        **context: Plan validation context.

    Returns:
        A Stacksmith plan-validation outcome.
    """
    for change in value.get("resource_changes", []) if isinstance(value, dict) else []:
        if change.get("type") != "aws_iam_role":
            continue
        policy = _load_policy(
            (change.get("change") or {}).get("after", {}).get("assume_role_policy")
        )
        if policy is None or not _trusts_only_lambda(policy):
            return {
                "status": "fail",
                "message": "IAM roles must trust only lambda.amazonaws.com for sts:AssumeRole.",
            }
    return "pass"
