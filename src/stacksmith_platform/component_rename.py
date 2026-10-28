import ast
import json

from jinja2 import Environment, TemplateSyntaxError

from .errors import ComposerError


def _expression(value, old, new):
    if "{{" not in value:
        return value
    try:
        environment = Environment(keep_trailing_newline=True)
        environment.parse(value)
        tokens = list(environment.lex(value))
    except TemplateSyntaxError as exc:
        raise ComposerError(
            "Fix invalid template expressions before renaming a component."
        ) from exc
    if "".join(token[2] for token in tokens) != value:
        raise ComposerError("This template cannot be safely preserved during a rename.")
    significant = [
        index for index, token in enumerate(tokens) if token[1] != "whitespace"
    ]
    replacements = {}
    for position, index in enumerate(significant):
        if tokens[index][1:] != ("name", "components"):
            continue
        if position and tokens[significant[position - 1]][2] in {".", "|"}:
            continue
        if position + 2 >= len(significant):
            raise ComposerError(
                "A dynamic component reference cannot be safely renamed."
            )
        operator = significant[position + 1]
        key = significant[position + 2]
        if tokens[operator][2] == "." and tokens[key][1] == "name":
            if (
                position + 3 < len(significant)
                and tokens[significant[position + 3]][2] == "("
            ):
                raise ComposerError(
                    "A dynamic component reference cannot be safely renamed."
                )
            if tokens[key][2] == old:
                replacements[operator] = f"[{json.dumps(new, ensure_ascii=False)}]"
                replacements[key] = ""
        elif tokens[operator][2] == "[" and tokens[key][1] == "string":
            if (
                position + 3 >= len(significant)
                or tokens[significant[position + 3]][2] != "]"
            ):
                raise ComposerError(
                    "A dynamic component reference cannot be safely renamed."
                )
            try:
                name = ast.literal_eval(tokens[key][2])
            except (ValueError, SyntaxError) as exc:
                raise ComposerError(
                    "A component reference cannot be safely renamed."
                ) from exc
            if name == old:
                replacements[key] = json.dumps(new, ensure_ascii=False)
        else:
            raise ComposerError(
                "A dynamic component reference cannot be safely renamed."
            )
    return "".join(
        replacements.get(index, token[2]) for index, token in enumerate(tokens)
    )


def _rewrite(value, old, new):
    if isinstance(value, str):
        return _expression(value, old, new)
    if isinstance(value, list):
        return [_rewrite(child, old, new) for child in value]
    if isinstance(value, dict):
        return {key: _rewrite(child, old, new) for key, child in value.items()}
    return value


def rename_components(components, old, new):
    """Rename a component and rewrite static template references.

    Args:
        components: Current authored component mapping.
        old: Existing component identifier.
        new: Replacement identifier.

    Returns:
        Updated component mapping with preserved order.

    Raises:
        ComposerError: The name is unavailable or a reference cannot be rewritten safely.
    """
    if old not in components:
        raise ComposerError("The component no longer exists.", 404)
    if new in components:
        raise ComposerError("A component with this name already exists.", 409)
    return {
        new if name == old else name: _rewrite(component, old, new)
        for name, component in components.items()
    }


def _changed_values(original, updated, path):
    if isinstance(original, dict):
        for key in original:
            yield from _changed_values(original[key], updated[key], [*path, key])
    elif isinstance(original, list):
        for index, child in enumerate(original):
            yield from _changed_values(child, updated[index], [*path, index])
    elif original != updated:
        yield {"action": "set", "path": path, "value": updated}


def root_rename_edits(document, renames):
    """Return source edits for preserved root references affected by renames.

    Args:
        document: Original parsed stack document.
        renames: Ordered component rename operations.

    Returns:
        Edits for changed root expression values.

    Raises:
        ComposerError: A template reference cannot be rewritten safely.
    """
    edits = []
    for key, original in document.items():
        if key == "components":
            continue
        if key == "metadata":
            original = {
                name: value
                for name, value in original.items()
                if name != "stacksmith_composer"
            }
        updated = original
        for rename in renames:
            updated = _rewrite(updated, rename.old, rename.new)
        edits.extend(_changed_values(original, updated, [key]))
    return edits
