import sys
from pathlib import Path
from types import SimpleNamespace

from stacksmith_platform.operations import _discover


def test_operation_discovery_groups_stack_local_names_and_filters_environment(
    monkeypatch,
):
    monkeypatch.setitem(
        sys.modules,
        "stacksmith.ci.service",
        SimpleNamespace(
            inspect_environments=lambda **kwargs: {
                "matrix": [
                    {"environment": "prod", "stack": "first.yaml"},
                    {"environment": "prod", "stack": "second.yaml"},
                    {"environment": "dev", "stack": "excluded.yaml"},
                ]
            }
        ),
    )
    monkeypatch.setitem(
        sys.modules,
        "stacksmith.loading",
        SimpleNamespace(
            load_stack_metadata=lambda path: SimpleNamespace(
                name=path.stem,
                operations={"deploy": SimpleNamespace(description="Deploy service")},
            ),
        ),
    )
    assert _discover(
        {
            "settings": {"gitops_root": ".", "discovery_mode": "single"},
            "environment": "prod",
            "revision": "a" * 40,
        }
    ) == {
        "revision": "a" * 40,
        "operations": [
            {
                "name": "deploy",
                "description": "Deploy service",
                "stacks": ["first", "second"],
            }
        ],
    }


def test_operation_discovery_uses_runfile_overlays(monkeypatch):
    from stacksmith_platform.operations import _stack_paths

    sources = []
    monkeypatch.setitem(
        sys.modules,
        "stacksmith.loading",
        SimpleNamespace(
            load_runfiles=lambda paths: (
                sources.extend(paths) or SimpleNamespace(stacks=["service.yaml"])
            )
        ),
    )
    monkeypatch.setitem(
        sys.modules,
        "stacksmith.remote",
        SimpleNamespace(resolve_references=lambda references, cache: references),
    )
    assert _stack_paths(
        {"runfile": "run.yaml", "environment_runfile": "environments/prod.yaml"}
    ) == ["service.yaml"]
    assert sources == [Path("run.yaml"), Path("environments/prod.yaml")]
