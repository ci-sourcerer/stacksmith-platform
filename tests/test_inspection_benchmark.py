import json

import pytest

from scripts.benchmark_inspections import Editor


def test_capacity_runner_rejects_stale_source_and_missing_connections():
    editor = Editor(None, 1, 15)
    expected = {
        "policy": {"type": "aws_iam_policy"},
        "role": {"type": "aws_iam_role"},
    }
    result = {
        "source": json.dumps({"name": "capacity", "components": expected}),
        "associations": {"applications": []},
        "edges": [
            {
                "source": "policy",
                "target": "role",
                "property": "policy_arns",
                "rule": "role-baseline-policies",
                "kind": "association",
            }
        ],
    }
    with pytest.raises(ValueError, match="no managed association"):
        editor._validate(result, expected, review=True)
    editor._validate(result, expected, review=False)
    result["associations"]["applications"] = [{"rule": "role-baseline-policies"}]
    editor._validate(result, expected, review=True)
    result["edges"][0]["target"] = "wrong-role"
    with pytest.raises(ValueError, match="connections do not match"):
        editor._validate(result, expected, review=True)
    with pytest.raises(ValueError, match="submitted generation"):
        editor._validate(result, {"other": {"type": "aws_iam_policy"}})
