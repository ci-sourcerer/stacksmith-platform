import json
import sys
from pathlib import Path


def _discover(payload):
    from stacksmith.ci.service import inspect_environments
    from stacksmith.loading import load_stack_metadata

    operations = {}
    for row in inspect_environments(
        gitops_root=payload["settings"]["gitops_root"],
        discovery_mode=payload["settings"]["discovery_mode"],
        environments=payload["environment"],
    )["matrix"]:
        if row["environment"] != payload["environment"]:
            continue
        for path in _stack_paths(row):
            stack = load_stack_metadata(path)
            for name, invocation in stack.operations.items():
                entry = operations.setdefault(
                    name,
                    {"name": name, "description": invocation.description, "stacks": []},
                )
                if stack.name not in entry["stacks"]:
                    entry["stacks"].append(stack.name)
    return {
        "revision": payload["revision"],
        "operations": [operations[name] for name in sorted(operations)],
    }


def _stack_paths(row):
    if row.get("stack"):
        return [Path(row["stack"])]

    from stacksmith.loading import load_runfiles
    from stacksmith.remote import resolve_references

    return resolve_references(
        load_runfiles(
            [Path(row["runfile"])]
            + (
                [Path(row["environment_runfile"])]
                if row.get("environment_runfile")
                else []
            )
        ).stacks,
        Path(".stacksmith/.cache"),
    )


def main() -> None:
    """Write operation metadata for the checked-out repository to standard output.

    Returns:
        None.

    Raises:
        ValueError: Discovery input or repository metadata is invalid.
    """
    print(json.dumps(_discover(json.load(sys.stdin))))


if __name__ == "__main__":
    main()
