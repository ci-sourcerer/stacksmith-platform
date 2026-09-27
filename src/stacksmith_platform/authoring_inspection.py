import json
import sys
from contextlib import contextmanager
from pathlib import Path
from time import perf_counter

from .documents import load_document


@contextmanager
def _stage(timings, name):
    started = perf_counter()
    try:
        yield
    finally:
        timings[name] = perf_counter() - started


def _catalog(inputs):
    from stacksmith.api import inspect_modules
    from stacksmith.inspector import format_json

    return json.loads(
        format_json(
            inspect_modules(
                config=inputs["config"],
                merge_mode=inputs["merge_mode"],
                build_dir=inputs["build_dir"],
            )[0]
        )
    )


def _associations(inputs):
    from stacksmith.api import inspect_associations
    from stacksmith.loading import load_stack_metadata

    return {
        **inspect_associations(**inputs),
        "_document": load_stack_metadata(
            inputs["stack_file"], merge_mode=inputs["merge_mode"]
        ).model_dump(mode="json", exclude={"source_path"}),
    }


def _review(inspection):
    from stacksmith.authoring import StackEdit, author_stack

    timings = {}
    with _stage(timings, "authoring"):
        proposal = author_stack(
            inspection["authoring_request"]["source"],
            [
                StackEdit.model_validate(edit)
                for edit in inspection["authoring_request"]["edits"]
            ],
            inspection["authoring_request"]["path"],
        )
        load_document(proposal.source)
    with _stage(timings, "environment"):
        inputs = _inspection_inputs({**inspection, "source": proposal.source})
    with _stage(timings, "catalog"):
        catalog = _catalog(inputs)
    with _stage(timings, "associations"):
        associations = _associations(inputs)
    return {
        "source": proposal.source,
        "catalog": catalog,
        "associations": associations,
        "timings": timings,
    }


def _standalone_inputs(inspection, source):
    path = Path((inspection.get("context") or {}).get("path", "stack.yaml"))
    if not path.resolve().is_relative_to(Path.cwd().resolve()):
        raise ValueError("The stack path is outside the repository.")
    if source is not None:
        path.write_text(source)
    return {
        "stack_file": [path],
        "config": inspection["config"].split(","),
        "merge_mode": "deep",
        "build_dir": Path(".stacksmith/build"),
    }


def _inspection_inputs(inspection):
    if (inspection.get("context") or {}).get("environment"):
        return _environment_inputs(inspection)
    return _standalone_inputs(inspection, inspection["source"])


def _environment_inputs(inspection):
    from stacksmith.ci.service import inspect_environments
    from stacksmith.loading import load_runfiles
    from stacksmith.models import MergePolicy, RunFile
    from stacksmith.remote import resolve_references

    context = inspection["context"]
    rows = [
        item
        for item in inspect_environments(
            gitops_root=context["settings"]["gitops_root"],
            discovery_mode=context["settings"]["discovery_mode"],
            environments=context["environment"],
        )["matrix"]
        if item["environment"] == context["environment"]
    ]
    if len(rows) != 1:
        raise ValueError("The selected environment could not be discovered.")
    runfile = (
        RunFile()
        if rows[0].get("stack")
        else load_runfiles(
            [
                Path(rows[0]["runfile"]),
                *(
                    [Path(rows[0]["environment_runfile"])]
                    if rows[0].get("environment_runfile")
                    else []
                ),
            ]
        )
    )
    paths = (
        [Path(rows[0]["stack"])]
        if rows[0].get("stack")
        else resolve_references(runfile.stacks, Path(".stacksmith/.cache"))
    )
    target = (Path.cwd() / context["path"]).resolve()
    if not target.is_relative_to(Path.cwd().resolve()) or target not in [
        Path(path).resolve() for path in paths
    ]:
        raise ValueError("The selected environment does not consume this stack file.")
    if inspection["source"] is not None:
        target.write_text(inspection["source"])
    return {
        "stack_file": paths,
        "config": [*runfile.configs, *inspection["config"].split(",")],
        "input_layers": [
            *(("vars", value) for value in runfile.vars),
            ("var", f"environment={context['environment']}"),
        ],
        "merge_mode": MergePolicy(
            default=runfile.merge_mode or "deep", rules=runfile.merge_rules
        )
        if runfile.merge_rules
        else runfile.merge_mode or "deep",
        "build_dir": Path(".stacksmith/build"),
    }


def _inspect_context(inspection):
    if inspection["args"] == ["review"]:
        return _review(inspection)

    inputs = _inspection_inputs(inspection)
    if inspection["args"][:2] == ["info", "modules-and-policies"]:
        return _catalog(inputs)
    if inspection["args"][:2] == ["info", "associations"]:
        return _associations(inputs)
    if inspection["args"][:2] == ["info", "explain"]:
        from stacksmith.api import inspect_configuration

        return inspect_configuration(
            **inputs,
            query=inspection["args"][2]
            if not inspection["args"][2].startswith("--")
            else None,
            show_values=True,
        )
    raise ValueError("Unsupported authoring inspection command.")


def main() -> None:
    """Inspect a draft using the selected environment in the isolated runtime.

    Returns:
        None. Writes JSON inspection output to standard output.

    Raises:
        ValueError: The environment does not consume the selected stack.
    """
    print(json.dumps(_inspect_context(json.load(sys.stdin))))


if __name__ == "__main__":
    main()
