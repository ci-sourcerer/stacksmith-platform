import json
from unittest.mock import Mock

import pytest

from stacksmith_platform.documents import compose_source, load_document, source_diff
from stacksmith_platform.errors import ComposerError

SOURCE = """# Keep the platform team's note.
name: storefront
tags: [production]
components:
  api:
    type: service
    properties:
      name: "hello" # Keep this inline comment.
outputs:
  url:
    value: "{{ components.api.url }}"
"""


def test_components_and_opt_out_are_delegated_without_materializing_properties():
    author = Mock(return_value={"source": "proposed source"})
    components = load_document(SOURCE)["components"]
    assert (
        compose_source(SOURCE, components, ["network"], "stack.yaml", author=author)
        == "proposed source"
    )
    assert author.call_args.args == (
        SOURCE,
        [
            {"action": "set", "path": ["components"], "value": components},
            {"action": "set", "path": ["disabled_associations"], "value": ["network"]},
        ],
        "stack.yaml",
    )
    assert "network_id" not in str(author.call_args)


def test_empty_components_and_opt_out_are_removed_instead_of_invalid_empty_mappings():
    author = Mock(return_value={"source": "name: storefront\n"})
    compose_source(SOURCE, {}, [], "stack.yaml", author=author)
    assert author.call_args.args[1] == [
        {"action": "remove", "path": ["components"]},
        {"action": "remove", "path": ["disabled_associations"]},
    ]


def test_layout_edits_preserve_other_tools_metadata_and_unknown_layout_fields():
    source = (
        SOURCE
        + "metadata:\n  other_tool: {owner: team}\n  stacksmith_composer:\n    custom: keep\n    positions: {api: {x: 0, y: 0}}\n"
    )
    author = Mock(return_value={"source": "proposed source"})
    route = {
        "source": "api",
        "target": "worker",
        "label_position": {"x": 220, "y": 132},
    }
    compose_source(
        source,
        load_document(source)["components"],
        [],
        "stack.yaml",
        positions={},
        routes=[route],
        author=author,
    )
    assert author.call_args.args[1][-1] == {
        "action": "set",
        "path": ["metadata"],
        "value": {
            "other_tool": {"owner": "team"},
            "stacksmith_composer": {"custom": "keep", "routes": [route]},
        },
    }


def test_clearing_layout_removes_empty_metadata():
    source = (
        SOURCE
        + "metadata:\n  stacksmith_composer:\n    positions: {api: {x: 0, y: 0}}\n"
    )
    author = Mock(return_value={"source": SOURCE})
    compose_source(
        source,
        load_document(source)["components"],
        [],
        "stack.yaml",
        positions={},
        routes=[],
        author=author,
    )
    assert author.call_args.args[1][-1] == {"action": "remove", "path": ["metadata"]}


def test_source_diff_labels_the_reviewed_file():
    assert "+++ b/apps/stack.yaml" in source_diff(
        "name: first\n", "name: second\n", "apps/stack.yaml"
    )


@pytest.mark.parametrize(
    "point", [{"x": True, "y": 0}, {"x": float("inf"), "y": 0}, {"x": 1}]
)
def test_invalid_label_positions_are_rejected(point):
    with pytest.raises(ComposerError, match="finite x and y"):
        load_document(
            json.dumps(
                {
                    "name": "test",
                    "components": {},
                    "metadata": {
                        "stacksmith_composer": {
                            "routes": [
                                {
                                    "source": "api",
                                    "target": "worker",
                                    "output": "url",
                                    "property": "url",
                                    "path": ["url"],
                                    "kind": "reference",
                                    "points": [],
                                    "label_position": point,
                                }
                            ]
                        }
                    },
                }
            )
        )


@pytest.mark.parametrize(
    "source",
    [
        "name: test\n{% for item in inputs.items %}\n{% endfor %}",
        "name: test\ncomponents: &components {}",
        "name: test\ncomponents: !!map {}",
        "name: test\ncomponents: [bad]",
        "name: test\ncomponents:\n  api:\n    type: service\n    properties: []",
        "name: test\nname: duplicate",
        "name: test\n" + "# too large\n" * 10000,
    ],
)
def test_unsupported_sources_are_rejected(source):
    with pytest.raises(ComposerError):
        load_document(source)


@pytest.mark.parametrize("value", ["2026-01-01", ".nan", "9007199254740993"])
def test_yaml_values_that_cannot_round_trip_through_browser_are_read_only(value):
    with pytest.raises(ComposerError, match="read-only"):
        load_document(
            "name: test\ncomponents:\n  api:\n    type: service\n    properties:\n"
            f"      value: {value}\n"
        )
