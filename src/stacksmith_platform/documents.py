import difflib
import math
from collections.abc import Callable
from copy import deepcopy
from typing import Any

from ruamel.yaml import YAML
from ruamel.yaml.error import YAMLError
from ruamel.yaml.tokens import AliasToken, AnchorToken, TagToken

from .errors import ComposerError

MAX_SOURCE_BYTES = 100_000


def load_document(source: str) -> dict[str, Any]:
    """Parse a conservative, editable subset of Stacksmith YAML or JSON.

    Args:
        source: Original source text, including comments and quoted expressions.

    Returns:
        A round-trip mapping that retains YAML comments and scalar styles.

    Raises:
        ComposerError: If the document requires template rendering or is unsupported.
    """
    if len(source.encode()) > MAX_SOURCE_BYTES:
        raise ComposerError("Stack files larger than 100 KB are not supported yet.")
    if "{%" in source or "{#" in source:
        raise ComposerError(
            "This stack uses whole-file Jinja. "
            "Visual editing is read-only; edit its source."
        )
    parser = YAML(typ="rt")
    parser.preserve_quotes = True
    try:
        if any(
            isinstance(token, AliasToken | AnchorToken | TagToken)
            for token in parser.scan(source)
        ):
            raise ComposerError(
                "YAML aliases, anchors, and custom tags are read-only in this version."
            )
        document = parser.load(source)
    except (YAMLError, RecursionError) as exc:
        raise ComposerError(
            "This stack is not valid YAML/JSON with quoted expressions."
        ) from exc
    _validate_json_shape(document)
    if not isinstance(document, dict) or not isinstance(document.get("name"), str):
        raise ComposerError("A stack must be a mapping with a string name.")
    if not isinstance(document.get("components", {}), dict):
        raise ComposerError("Stack components must be a mapping.")
    _validate_components(document.get("components", {}))
    _validate_metadata(document.get("metadata", {}))
    return document


def _validate_json_shape(value: Any, depth: int = 0) -> None:
    if depth > 40:
        raise ComposerError("Deeply nested YAML is read-only in this version.")
    if isinstance(value, dict):
        for key, child in value.items():
            if not isinstance(key, str):
                raise ComposerError(
                    "YAML mapping keys must be strings for visual editing."
                )
            _validate_json_shape(child, depth + 1)
    elif isinstance(value, list):
        for child in value:
            _validate_json_shape(child, depth + 1)
    elif isinstance(value, int) and abs(value) > 2**53 - 1:
        raise ComposerError(
            "Large YAML integers are read-only to avoid browser precision loss."
        )
    elif isinstance(value, float) and not math.isfinite(value):
        raise ComposerError("Non-finite YAML numbers are read-only in this version.")
    elif value is not None and not isinstance(value, str | int | float | bool):
        raise ComposerError("Special YAML scalar types are read-only in this version.")


def _validate_components(components: dict[str, Any]) -> None:
    for name, component in components.items():
        if not isinstance(name, str) or not name or not isinstance(component, dict):
            raise ComposerError("Each component needs a name and a mapping definition.")
        if not isinstance(component.get("type"), str):
            raise ComposerError(f"Component {name} needs a type.")
        if not isinstance(component.get("properties", {}), dict):
            raise ComposerError(f"Component {name} properties must be a mapping.")


def _validate_metadata(metadata: Any) -> None:
    if not isinstance(metadata, dict):
        raise ComposerError("Stack metadata must be a mapping.")
    composer = metadata.get("stacksmith_composer", {})
    if not isinstance(composer, dict):
        raise ComposerError("Stacksmith Platform metadata must be a mapping.")
    positions = composer.get("positions", {})
    if not isinstance(positions, dict):
        raise ComposerError("Diagram positions must be a mapping.")
    for name, position in positions.items():
        if (
            not isinstance(name, str)
            or not isinstance(position, dict)
            or set(position) != {"x", "y"}
            or not all(
                isinstance(position[axis], int | float)
                and not isinstance(position[axis], bool)
                and math.isfinite(position[axis])
                for axis in ("x", "y")
            )
        ):
            raise ComposerError("Each diagram position needs finite x and y numbers.")
    routes = composer.get("routes", [])
    if not isinstance(routes, list):
        raise ComposerError("Diagram routes must be a list.")
    for route in routes:
        if (
            not isinstance(route, dict)
            or not all(
                isinstance(route.get(key), str)
                for key in ("source", "target", "output", "property", "kind")
            )
            or route.get("kind") not in {"reference", "association"}
            or not isinstance(route.get("path"), list)
            or not all(
                isinstance(part, str | int) and not isinstance(part, bool)
                for part in route["path"]
            )
            or route.get("rule") is not None
            and not isinstance(route.get("rule"), str)
            or not isinstance(route.get("points"), list)
        ):
            raise ComposerError("Each diagram route needs a list of points.")
        for point in route["points"] + (
            [route["label_position"]] if "label_position" in route else []
        ):
            if (
                not isinstance(point, dict)
                or set(point) != {"x", "y"}
                or not all(
                    isinstance(point[axis], int | float)
                    and not isinstance(point[axis], bool)
                    and math.isfinite(point[axis])
                    for axis in ("x", "y")
                )
            ):
                raise ComposerError("Each route point needs finite x and y numbers.")


def _layout_edits(
    document: dict[str, Any],
    positions: dict[str, dict[str, float]] | None,
    routes: list[dict[str, Any]] | None,
) -> list[dict[str, Any]]:
    if positions is None and routes is None:
        return []
    metadata = deepcopy(document.get("metadata", {}))
    composer = metadata.setdefault("stacksmith_composer", {})
    for key, value in (("positions", positions), ("routes", routes)):
        if value is not None:
            if value:
                composer[key] = value
            else:
                composer.pop(key, None)
    if not composer:
        metadata.pop("stacksmith_composer", None)
    return [
        {"action": "set", "path": ["metadata"], "value": metadata}
        if metadata
        else {"action": "remove", "path": ["metadata"]}
    ]


def compose_source(
    original: str,
    components: dict[str, Any],
    disabled: list[str],
    path: str,
    positions: dict[str, dict[str, float]] | None = None,
    routes: list[dict[str, Any]] | None = None,
    *,
    author: Callable[[str, list[dict[str, Any]], str], dict[str, Any]],
) -> str:
    """Delegate authored edits to Stacksmith while managing Composer layout.

    Args:
        original: Base source captured when opening the stack.
        components: Desired authored component definitions.
        disabled: Desired stack-level association opt-outs.
        path: Source filename selecting YAML or JSON serialization.
        positions: Optional diagram positions keyed by component name.
        routes: Optional relationship routes with intermediate points.
        author: Adapter for Stacksmith's version 1 authoring contract.

    Returns:
        Document-validated authored source from Stacksmith, preserving unrelated
        fields and surviving YAML comments and styles.

    Raises:
        ComposerError: If the source, edits, or Stacksmith response are invalid.
    """
    return author(
        original,
        _source_edits(original, components, disabled, positions, routes),
        path,
    )["source"]


def _source_edits(original, components, disabled, positions=None, routes=None):
    _validate_components(components)
    return [
        {"action": "set", "path": ["components"], "value": components}
        if components
        else {"action": "remove", "path": ["components"]},
        {"action": "set", "path": ["disabled_associations"], "value": disabled}
        if disabled
        else {"action": "remove", "path": ["disabled_associations"]},
        *_layout_edits(load_document(original), positions, routes),
    ]


def source_diff(original: str, proposed: str, path: str) -> str:
    """Return a unified diff suitable for reviewing authored changes.

    Args:
        original: Base source.
        proposed: Proposed source.
        path: Repository-relative filename.

    Returns:
        Unified text diff, empty if no changes were made.
    """
    return "".join(
        difflib.unified_diff(
            original.splitlines(keepends=True),
            proposed.splitlines(keepends=True),
            fromfile=f"a/{path}",
            tofile=f"b/{path}",
        )
    )
