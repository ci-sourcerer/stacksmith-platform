import json
import sys
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from stacksmith_platform.authoring_inspection import (
    _environment_inputs,
    _inspect_context,
    _inspection_inputs,
)


def test_preview_substitutes_original_nested_path_without_discovering_deployments(
    context_runtime,
):
    Path("stacks").mkdir()
    Path("stacks/service.yaml").write_text("original")
    context_runtime.inspection["context"] = {
        "path": "stacks/service.yaml",
        "environment": None,
    }
    assert _inspection_inputs(context_runtime.inspection) == {
        "stack_file": [Path("stacks/service.yaml")],
        "config": ["managed-base", "managed-overlay"],
        "merge_mode": "deep",
        "build_dir": Path(".stacksmith/build"),
    }
    assert Path("stacks/service.yaml").read_text() == "draft-with-deletion"
    assert Path("stack.yaml").read_text() == "original"
    context_runtime.discover.assert_not_called()
    context_runtime.load.assert_not_called()


def test_preview_rejects_paths_outside_checkout(context_runtime):
    context_runtime.inspection["context"] = {"path": "../stack.yaml"}
    with pytest.raises(ValueError, match="outside the repository"):
        _inspection_inputs(context_runtime.inspection)


@pytest.fixture
def context_runtime(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    (tmp_path / "stack.yaml").write_text("original")
    discover = Mock(
        return_value={
            "matrix": [
                {
                    "environment": "dev",
                    "runfile": "common.yaml",
                    "environment_runfile": "environments/dev.yaml",
                }
            ]
        }
    )
    runfile = SimpleNamespace(
        stacks=[Path("base.yaml"), Path("stack.yaml"), Path("overlay.yaml")],
        configs=["runfile-config"],
        vars=["base-vars", "dev-vars"],
        merge_mode="deep",
        merge_rules=["list-rule"],
    )
    load = Mock(return_value=runfile)
    monkeypatch.setitem(
        sys.modules,
        "stacksmith.ci.service",
        SimpleNamespace(inspect_environments=discover),
    )
    monkeypatch.setitem(
        sys.modules,
        "stacksmith.loading",
        SimpleNamespace(
            load_runfiles=load,
            load_stack_metadata=Mock(
                return_value=SimpleNamespace(
                    model_dump=Mock(return_value={"components": {}})
                )
            ),
        ),
    )
    monkeypatch.setitem(
        sys.modules,
        "stacksmith.models",
        SimpleNamespace(RunFile=lambda: runfile, MergePolicy=lambda **kwargs: kwargs),
    )
    monkeypatch.setitem(
        sys.modules,
        "stacksmith.remote",
        SimpleNamespace(resolve_references=lambda references, cache: references),
    )
    return SimpleNamespace(
        discover=discover,
        load=load,
        runfile=runfile,
        inspection={
            "source": "draft-with-deletion",
            "config": "managed-base,managed-overlay",
            "args": ["info", "associations", "--format", "json"],
            "context": {
                "path": "stack.yaml",
                "environment": "dev",
                "settings": {"gitops_root": ".", "discovery_mode": "env-files"},
            },
        },
    )


def test_environment_inputs_substitute_draft_without_changing_stack_layer_order(
    context_runtime,
):
    result = _environment_inputs(context_runtime.inspection)
    assert Path("stack.yaml").read_text() == "draft-with-deletion"
    assert result["stack_file"] == [
        Path("base.yaml"),
        Path("stack.yaml"),
        Path("overlay.yaml"),
    ]
    assert result["config"] == ["runfile-config", "managed-base", "managed-overlay"]
    assert result["input_layers"] == [
        ("vars", "base-vars"),
        ("vars", "dev-vars"),
        ("var", "environment=dev"),
    ]
    assert result["merge_mode"] == {"default": "deep", "rules": ["list-rule"]}
    context_runtime.load.assert_called_once_with(
        [Path("common.yaml"), Path("environments/dev.yaml")]
    )


def test_only_direct_stack_environments_prove_static_catalog_inputs(context_runtime):
    proof = {}
    _environment_inputs(context_runtime.inspection, proof)
    assert proof == {"static": False}
    context_runtime.discover.return_value = {
        "matrix": [{"environment": "dev", "stack": "stack.yaml"}]
    }
    context_runtime.runfile.configs = []
    context_runtime.runfile.vars = []
    _environment_inputs(context_runtime.inspection, proof)
    assert proof == {"static": True}


@pytest.mark.parametrize("mode", ["single", "folders", "flat-files", "env-files"])
def test_inspection_uses_selected_discovery_mode(context_runtime, mode):
    context_runtime.inspection["context"]["settings"]["discovery_mode"] = mode
    if mode == "single":
        context_runtime.discover.return_value = {
            "matrix": [{"environment": "dev", "stack": "stack.yaml"}]
        }
    _environment_inputs(context_runtime.inspection)
    context_runtime.discover.assert_called_once_with(
        gitops_root=".", discovery_mode=mode, environments="dev"
    )
    if mode == "single":
        context_runtime.load.assert_not_called()


@pytest.mark.parametrize("path", ["unrelated.yaml", "../outside.yaml"])
def test_environment_must_consume_selected_stack_before_writing(context_runtime, path):
    context_runtime.inspection["context"]["path"] = path
    with pytest.raises(ValueError, match="does not consume"):
        _environment_inputs(context_runtime.inspection)
    assert Path("stack.yaml").read_text() == "original"


def test_unknown_environment_is_rejected(context_runtime):
    context_runtime.discover.return_value = {"matrix": []}
    with pytest.raises(ValueError, match="could not be discovered"):
        _environment_inputs(context_runtime.inspection)


@pytest.mark.parametrize(
    "operation", ["associations", "explain", "modules-and-policies"]
)
def test_context_inspections_share_layered_inputs(
    context_runtime, monkeypatch, operation
):
    associations = Mock(return_value={"rules": []})
    resolution = Mock(return_value={"events": []})
    modules = Mock(return_value=(["module"], []))
    monkeypatch.setitem(
        sys.modules,
        "stacksmith.api",
        SimpleNamespace(
            inspect_associations=associations,
            inspect_configuration=resolution,
            inspect_modules=modules,
        ),
    )
    monkeypatch.setitem(
        sys.modules,
        "stacksmith.inspector",
        SimpleNamespace(format_json=lambda results: json.dumps({"module": results[0]})),
    )
    context_runtime.inspection["args"] = ["info", operation, "--format", "json"]
    _inspect_context(context_runtime.inspection)
    call = {
        "associations": associations,
        "explain": resolution,
        "modules-and-policies": modules,
    }[operation].call_args.kwargs
    assert call["config"] == ["runfile-config", "managed-base", "managed-overlay"]
    if operation != "modules-and-policies":
        assert call["input_layers"][-1] == ("var", "environment=dev")
        assert call["stack_file"].count(Path("stack.yaml")) == 1


def test_compound_review_authors_and_discovers_once_with_shared_inputs(
    context_runtime, monkeypatch
):
    author = Mock(return_value=SimpleNamespace(source="name: draft\ncomponents: {}\n"))
    monkeypatch.setitem(
        sys.modules,
        "stacksmith.authoring",
        SimpleNamespace(
            author_stack=author,
            StackEdit=SimpleNamespace(model_validate=lambda edit: edit),
        ),
    )
    modules = Mock(return_value=(["module"], []))
    associations = Mock(return_value={"rules": [], "applications": []})
    monkeypatch.setitem(
        sys.modules,
        "stacksmith.api",
        SimpleNamespace(inspect_modules=modules, inspect_associations=associations),
    )
    monkeypatch.setitem(
        sys.modules,
        "stacksmith.inspector",
        SimpleNamespace(format_json=lambda results: json.dumps({"module": results[0]})),
    )
    context_runtime.inspection.update(
        args=["review"],
        authoring_request={"source": "original", "edits": [], "path": "stack.yaml"},
    )
    result = _inspect_context(context_runtime.inspection)
    author.assert_called_once_with("original", [], "stack.yaml")
    context_runtime.discover.assert_called_once()
    assert (
        Path("stack.yaml").read_text()
        == result["source"]
        == "name: draft\ncomponents: {}\n"
    )
    assert result["catalog"] == {"module": "module"}
    assert result["associations"]["_document"] == {"components": {}}
    assert set(result["timings"]) == {
        "authoring",
        "environment",
        "catalog",
        "associations",
    }
    assert all(value >= 0 for value in result["timings"].values())
    modules.assert_called_once()
    associations.assert_called_once()
    for key in ("config", "merge_mode", "build_dir"):
        assert modules.call_args.kwargs[key] == associations.call_args.kwargs[key]
    assert associations.call_args.kwargs["stack_file"] == [
        Path("base.yaml"),
        Path("stack.yaml"),
        Path("overlay.yaml"),
    ]


def test_compound_review_rejects_invalid_authored_source_before_inspection(
    context_runtime, monkeypatch
):
    monkeypatch.setitem(
        sys.modules,
        "stacksmith.authoring",
        SimpleNamespace(
            author_stack=Mock(side_effect=ValueError("invalid document")),
            StackEdit=SimpleNamespace(model_validate=lambda edit: edit),
        ),
    )
    context_runtime.inspection.update(
        args=["review"],
        authoring_request={"source": "invalid", "edits": [], "path": "stack.yaml"},
    )
    with pytest.raises(ValueError, match="invalid document"):
        _inspect_context(context_runtime.inspection)
    context_runtime.discover.assert_not_called()


def test_compound_review_enforces_document_limits_before_inspection(
    context_runtime, monkeypatch
):
    from stacksmith_platform.errors import ComposerError

    monkeypatch.setitem(
        sys.modules,
        "stacksmith.authoring",
        SimpleNamespace(
            author_stack=Mock(
                return_value=SimpleNamespace(source="name: " + "x" * 100_001)
            ),
            StackEdit=SimpleNamespace(model_validate=lambda edit: edit),
        ),
    )
    context_runtime.inspection.update(
        args=["review"],
        authoring_request={"source": "original", "edits": [], "path": "stack.yaml"},
    )
    with pytest.raises(ComposerError, match="100 KB"):
        _inspect_context(context_runtime.inspection)
    context_runtime.discover.assert_not_called()
