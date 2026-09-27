from graphlib import CycleError, TopologicalSorter
from typing import Any

from jinja2 import Environment, TemplateSyntaxError, nodes

from .errors import ComposerError


def _chain(node: nodes.Node) -> list[str] | None:
    if isinstance(node, nodes.Name):
        return [node.name]
    if isinstance(node, nodes.Getattr):
        prefix = _chain(node.node)
        return [*prefix, node.attr] if prefix else None
    if isinstance(node, nodes.Getitem) and isinstance(node.arg, nodes.Const):
        prefix = _chain(node.node)
        return [*prefix, str(node.arg.value)] if prefix else None
    return None


def _references(value: Any, path: list[str | int], diagnostics=None):
    if isinstance(value, dict):
        for key, child in value.items():
            yield from _references(child, [*path, key], diagnostics)
    elif isinstance(value, list):
        for index, child in enumerate(value):
            yield from _references(child, [*path, index], diagnostics)
    elif isinstance(value, str) and "{{" in value:
        try:
            expression = Environment().parse(value)
        except TemplateSyntaxError as exc:
            if diagnostics is not None:
                diagnostics.append(
                    {"path": path, "message": f"Invalid expression: {exc.message}"}
                )
                return
            raise ComposerError(f"Invalid expression at {path}.") from exc
        seen = set()
        for node in expression.find_all((nodes.Getattr, nodes.Getitem)):
            chain = _chain(node)
            if chain and len(chain) >= 3 and chain[0] == "components":
                reference = (chain[1], chain[2])
                if reference not in seen:
                    seen.add(reference)
                    yield (*reference, path)


def extract_graph(components: dict[str, Any]) -> dict[str, Any]:
    """Extract authored relationships without resolving or rejecting the draft.

    Args:
        components: Current authored component definitions.

    Returns:
        Extractable edges and diagnostics with component property paths.
    """
    edges = []
    diagnostics = []
    for name, component in components.items():
        problems = []
        for producer, output, path in _references(
            component.get("properties", {}), [], problems
        ):
            edges.append(
                {
                    "source": producer,
                    "target": name,
                    "output": output,
                    "property": str(path[0]),
                    "path": path,
                    "kind": "reference",
                    "rule": None,
                }
            )
            if producer not in components:
                problems.append(
                    {
                        "path": path,
                        "message": f"A reference uses missing component {producer}.",
                    }
                )
        diagnostics.extend(
            {**problem, "path": ["components", name, "properties", *problem["path"]]}
            for problem in problems
        )
    try:
        TopologicalSorter(
            {
                name: {edge["source"] for edge in edges if edge["target"] == name}
                for name in components
            }
        ).prepare()
    except CycleError:
        diagnostics.append(
            {
                "path": ["components"],
                "message": "These connections create a component dependency cycle.",
            }
        )
    return {"edges": edges, "diagnostics": diagnostics}


def _explicit_edges(components: dict[str, Any], catalog: dict[str, Any]):
    for name, component in components.items():
        for producer, output, path in _references(component.get("properties", {}), []):
            _validate_reference(components, catalog, producer, output)
            yield {
                "source": producer,
                "target": name,
                "output": output,
                "property": str(path[0]),
                "path": path,
                "kind": "reference",
                "rule": None,
            }


def _validate_reference(components, catalog, producer, output):
    if producer not in components:
        raise ComposerError(f"A reference uses missing component {producer}.")
    if output not in {
        item["name"]
        for item in catalog.get(components[producer]["type"], {}).get("outputs", [])
    }:
        raise ComposerError(f"{producer} does not expose output {output}.")


def validate_root_references(document: dict[str, Any], catalog: dict[str, Any]) -> None:
    """Reject dangling component references in preserved outputs and operations.

    Args:
        document: The proposed authored stack, including unchanged root fields.
        catalog: Public component interface metadata.

    Raises:
        ComposerError: If removing a component leaves a dangling root reference.
    """
    for field in ("outputs", "operations"):
        for producer, output, _path in _references(document.get(field, {}), [field]):
            _validate_reference(
                document.get("components", {}), catalog, producer, output
            )


def _association_edges(associations: dict[str, Any]):
    rules = {rule["name"]: rule for rule in associations.get("rules", [])}
    for application in associations.get("applications", []):
        for binding in rules[application["rule"]]["bindings"]:
            if binding["property"] != application["property"]:
                continue
            for producer in application["producers"]:
                yield {
                    "source": producer,
                    "target": application["consumer"],
                    "output": binding["output"],
                    "property": binding["property"],
                    "path": [binding["property"]],
                    "kind": "association",
                    "rule": application["rule"],
                }


def inspect_graph(
    components: dict[str, Any], catalog: dict[str, Any], associations: dict[str, Any]
) -> list[dict[str, Any]]:
    """Build and validate component edges without evaluating template expressions.

    Args:
        components: Authored components.
        catalog: Inspected public component interfaces.
        associations: Association provenance returned by Stacksmith.

    Returns:
        Explicit and managed edges with property paths and provenance.

    Raises:
        ComposerError: If a component type, reference, or dependency cycle is invalid.
    """
    for name, component in components.items():
        if component["type"] not in catalog:
            raise ComposerError(
                f"{name} uses an uncatalogued type: {component['type']}."
            )
    edges = [
        *_explicit_edges(components, catalog),
        *_association_edges(associations),
    ]
    dependencies: dict[str, set[str]] = {name: set() for name in components}
    for edge in edges:
        dependencies[edge["target"]].add(edge["source"])
    try:
        TopologicalSorter(dependencies).prepare()
    except CycleError as exc:
        raise ComposerError(
            "These connections create a component dependency cycle."
        ) from exc
    return edges
