import pytest

from stacksmith_platform.component_rename import rename_components
from stacksmith_platform.documents import load_document
from stacksmith_platform.errors import ComposerError


def test_renames_nested_references_and_preserves_other_values():
    components = {
        "old": {"type": "policy", "properties": {"label": "old"}},
        "other": {
            "type": "role",
            "properties": {
                "nested": [
                    "{{ components.old.arn }}",
                    "prefix {{ components['old'][\"arn\"] }} suffix",
                    "{{ components.other.arn }}",
                    "{{ other.components.old.arn }}",
                    "{{ 'components.old.arn' }}",
                ]
            },
        },
    }
    result = rename_components(components, "old", "renamed-policy")
    assert list(result) == ["renamed-policy", "other"]
    assert result["renamed-policy"]["properties"]["label"] == "old"
    assert result["other"]["properties"]["nested"] == [
        '{{ components["renamed-policy"].arn }}',
        'prefix {{ components["renamed-policy"]["arn"] }} suffix',
        "{{ components.other.arn }}",
        "{{ other.components.old.arn }}",
        "{{ 'components.old.arn' }}",
    ]
    assert "old" in components


@pytest.mark.parametrize(
    "expression",
    ["{{ components[variable].arn }}", "{{ components.get('old').arn }}", "{{ broken"],
)
def test_unsafe_or_invalid_references_block_rename(expression):
    with pytest.raises(ComposerError, match="rename|renaming"):
        rename_components(
            {
                "old": {"type": "policy"},
                "consumer": {"type": "role", "properties": {"value": expression}},
            },
            "old",
            "new",
        )


def test_rename_updates_root_output_in_source_and_review(client):
    snapshot = client.get("/api/sample").json()
    draft = {
        "snapshot": snapshot["snapshot"],
        "components": snapshot["document"]["components"],
    }
    response = client.post(
        "/api/rename-component",
        json={**draft, "old_name": "request_reader", "new_name": "reader"},
    )
    assert response.status_code == 200, response.text
    draft["components"] = response.json()["components"]
    draft["renames"] = [{"old": "request_reader", "new": "reader"}]
    for endpoint in ("source", "review"):
        response = client.post(f"/api/{endpoint}", json=draft)
        assert response.status_code == 200, response.text
        document = load_document(response.json()["source"])
        assert "reader" in document["components"]
        assert "request_reader" not in document["components"]
        assert (
            document["outputs"]["function_arn"]["value"]
            == '{{ components["reader"]["arn"] }}'
        )


def test_rename_rejects_duplicate_and_invalid_names(client):
    snapshot = client.get("/api/sample").json()
    draft = {
        "snapshot": snapshot["snapshot"],
        "components": snapshot["document"]["components"],
        "old_name": "request_reader",
    }
    assert (
        client.post(
            "/api/rename-component", json={**draft, "new_name": "aws_read_only"}
        ).status_code
        == 409
    )
    assert (
        client.post(
            "/api/rename-component", json={**draft, "new_name": "bad name"}
        ).status_code
        == 422
    )


def test_chained_renames_update_nested_root_values_and_metadata(client, tmp_path):
    source = tmp_path / "stack.yaml"
    source.write_text(
        """name: example
components:
  old: {type: aws_iam_policy}
outputs:
  result:
    value: "{{ components.old.arn }}"
operations:
  list:
    - "{{ components['old']['arn'] }}"
metadata:
  other_tool:
    template: "{{ components.old.arn }}"
    label: "old"
"""
    )
    client.app.state.settings.sample = source
    snapshot = client.get("/api/sample").json()
    draft = {
        "snapshot": snapshot["snapshot"],
        "components": snapshot["document"]["components"],
    }
    for old, new in [("old", "middle"), ("middle", "new")]:
        response = client.post(
            "/api/rename-component", json={**draft, "old_name": old, "new_name": new}
        )
        assert response.status_code == 200, response.text
        draft["components"] = response.json()["components"]
        draft.setdefault("renames", []).append({"old": old, "new": new})
    response = client.post("/api/source", json=draft)
    assert response.status_code == 200, response.text
    document = load_document(response.json()["source"])
    assert document["outputs"]["result"]["value"] == '{{ components["new"].arn }}'
    assert document["operations"]["list"][0] == "{{ components[\"new\"]['arn'] }}"
    assert document["metadata"]["other_tool"] == {
        "template": '{{ components["new"].arn }}',
        "label": "old",
    }


def test_dynamic_root_reference_prevents_rename(client, tmp_path):
    source = tmp_path / "stack.yaml"
    source.write_text(
        'name: example\ncomponents:\n  old: {type: aws_iam_policy}\noutputs:\n  result:\n    value: "{{ components[name].arn }}"\n'
    )
    client.app.state.settings.sample = source
    snapshot = client.get("/api/sample").json()
    response = client.post(
        "/api/rename-component",
        json={
            "snapshot": snapshot["snapshot"],
            "components": snapshot["document"]["components"],
            "old_name": "old",
            "new_name": "new",
        },
    )
    assert response.status_code == 422
    assert "dynamic component reference" in response.json()["detail"]
