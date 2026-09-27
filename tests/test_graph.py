import pytest

from stacksmith_platform.errors import ComposerError
from stacksmith_platform.graph import extract_graph, inspect_graph

CATALOG = {"type": {"outputs": [{"name": "endpoint"}, {"name": "id"}]}}


def test_bracket_references_inside_collections_have_precise_paths():
    edges = inspect_graph(
        {
            "data-store": {"type": "type"},
            "app": {
                "type": "type",
                "properties": {
                    "config": [{"url": '{{ components["data-store"].endpoint }}'}]
                },
            },
        },
        CATALOG,
        {},
    )
    assert edges == [
        {
            "source": "data-store",
            "target": "app",
            "output": "endpoint",
            "property": "config",
            "path": ["config", 0, "url"],
            "kind": "reference",
            "rule": None,
        }
    ]


def test_invalid_reference_is_rejected():
    with pytest.raises(ComposerError, match="missing component"):
        inspect_graph(
            {
                "app": {
                    "type": "type",
                    "properties": {"url": "{{ components.missing.endpoint }}"},
                }
            },
            CATALOG,
            {},
        )


def test_unknown_public_output_is_rejected():
    with pytest.raises(ComposerError, match="does not expose"):
        inspect_graph(
            {
                "app": {
                    "type": "type",
                    "properties": {"url": "{{ components.data.private }}"},
                },
                "data": {"type": "type"},
            },
            CATALOG,
            {},
        )


def test_cycle_combining_explicit_and_managed_edges_is_rejected():
    associations = {
        "rules": [
            {"name": "managed", "bindings": [{"output": "id", "property": "network"}]}
        ],
        "applications": [
            {
                "rule": "managed",
                "producers": ["app"],
                "consumer": "data",
                "property": "network",
            }
        ],
    }
    with pytest.raises(ComposerError, match="cycle"):
        inspect_graph(
            {
                "app": {
                    "type": "type",
                    "properties": {"url": "{{ components.data.endpoint }}"},
                },
                "data": {"type": "type"},
            },
            CATALOG,
            associations,
        )


def test_preserved_root_output_prevents_dangling_component_deletion():
    from stacksmith_platform.graph import validate_root_references

    with pytest.raises(ComposerError, match="missing component"):
        validate_root_references(
            {
                "components": {},
                "outputs": {"url": {"value": "{{ components.api.endpoint }}"}},
            },
            CATALOG,
        )


def test_structural_graph_keeps_valid_edges_when_other_expressions_are_invalid():
    graph = extract_graph(
        {
            "producer": {"type": "unknown"},
            "consumer": {
                "properties": {
                    "invalid": "{{ broken",
                    "settings": [{"url": "{{ components.producer.endpoint }}"}],
                }
            },
        }
    )
    assert graph["edges"][0]["path"] == ["settings", 0, "url"]
    assert graph["diagnostics"][0]["path"] == [
        "components",
        "consumer",
        "properties",
        "invalid",
    ]


def test_structural_cycles_are_diagnostics_without_hiding_connections():
    graph = extract_graph(
        {
            "one": {"properties": {"value": "{{ components.two.id }}"}},
            "two": {"properties": {"value": "{{ components.one.id }}"}},
        }
    )
    assert len(graph["edges"]) == 2
    assert "cycle" in graph["diagnostics"][0]["message"]
