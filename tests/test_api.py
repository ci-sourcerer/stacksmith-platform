from copy import deepcopy
from unittest.mock import patch
from urllib.parse import parse_qs, urlparse

import pytest


def _draft(client):
    snapshot = client.get("/api/sample").json()
    return {
        "snapshot": snapshot["snapshot"],
        "components": snapshot["document"]["components"],
    }


def test_api_exposes_documentation_without_hosting_frontend(client):
    assert client.get("/").status_code == 404
    assert client.get("/runs").status_code == 404
    assert client.get("/assets/missing.js").status_code == 404
    assert client.get("/docs").status_code == 200
    assert client.get("/openapi.json").status_code == 200


def test_sample_and_review_preserve_exact_original(client):
    assert client.get("/api/health").json() == {"status": "ok"}
    sample = client.get("/api/sample").json()
    response = client.post(
        "/api/review",
        json={
            "snapshot": sample["snapshot"],
            "components": sample["document"]["components"],
        },
    )
    assert response.status_code == 200
    assert response.json()["source"] == sample["source"]
    assert response.json()["diff"] == ""


def test_review_returns_authored_diff(client):
    draft = _draft(client)
    draft["components"]["request_reader"]["properties"]["function_name"] = (
        "new-function"
    )
    response = client.post("/api/review", json=draft)
    assert response.status_code == 200
    assert "+      function_name: new-function" in response.json()["diff"]


def test_source_renders_layout_without_inspection(client):
    draft = _draft(client)
    draft["positions"] = {"request_reader": {"x": 100, "y": 200}}
    with (
        patch.object(client.app.state.adapter, "catalog") as catalog,
        patch.object(client.app.state.adapter, "associations") as associations,
    ):
        response = client.post("/api/source", json=draft)
    assert response.status_code == 200
    assert "stacksmith_composer:" in response.json()["source"]
    assert "+        x: 100.0" in response.json()["diff"]
    assert "validation" not in response.json()
    catalog.assert_not_called()
    associations.assert_not_called()


def test_source_exports_a_draft_that_fails_inspection(client):
    draft = _draft(client)
    draft["components"]["request_reader"]["type"] = "not-catalogued"
    assert client.post("/api/review", json=draft).status_code == 422
    response = client.post("/api/source", json=draft)
    assert response.status_code == 200
    assert "type: not-catalogued" in response.json()["source"]


def test_controller_source_remains_available_without_managed_configuration(
    client, tmp_path, monkeypatch
):
    from unittest.mock import AsyncMock

    from conftest import _fake_author

    draft = _draft(client)
    client.app.state.settings.inspection_controller_enabled = True
    client.app.state.settings.config = tmp_path / "unavailable-config.yaml"
    monkeypatch.setattr(
        "stacksmith_platform.adapter.StacksmithAdapter.aauthor",
        AsyncMock(side_effect=_fake_author),
    )
    response = client.post("/api/source", json=draft)
    assert response.status_code == 200, response.text


def test_structural_graph_does_not_invoke_authoring_or_resolution(client):
    draft = _draft(client)
    draft["components"]["request_reader"]["properties"]["nested"] = {
        "url": '{{ components["request_reader"]["arn"] }}',
        "broken": "{{ broken",
    }
    with (
        patch.object(client.app.state.adapter, "catalog") as catalog,
        patch.object(client.app.state.adapter, "associations") as associations,
        patch.object(client.app.state.adapter, "author") as author,
    ):
        response = client.post("/api/graph", json=draft)
    assert response.status_code == 200
    assert any(edge["path"] == ["nested", "url"] for edge in response.json()["edges"])
    assert response.json()["diagnostics"]
    catalog.assert_not_called()
    associations.assert_not_called()
    author.assert_not_called()


@pytest.mark.parametrize(
    "path",
    ["/api/review", "/api/resolution", "/api/source", "/api/graph", "/api/catalog"],
)
def test_snapshot_tampering_is_rejected(client, path):
    draft = _draft(client)
    draft["snapshot"] += "tampered"
    assert client.post(path, json=draft).status_code == 409


@pytest.mark.parametrize("path", ["/api/review", "/api/resolution", "/api/source"])
def test_config_change_requires_reopening(client, tmp_path, path):
    draft = _draft(client)
    client.app.state.settings.config = tmp_path / "new-config.yaml"
    client.app.state.settings.config.write_text("description: changed\n")
    assert client.post(path, json=draft).status_code == 409


def test_structural_graph_does_not_require_current_managed_configuration(
    client, tmp_path
):
    draft = _draft(client)
    client.app.state.settings.config = tmp_path / "unavailable-config.yaml"
    assert client.post("/api/graph", json=draft).status_code == 200


@pytest.mark.parametrize(
    "path",
    ["/api/review", "/api/resolution", "/api/source", "/api/graph", "/api/catalog"],
)
def test_cross_origin_mutation_is_rejected(client, path):
    assert (
        client.post(
            path,
            json=_draft(client),
            headers={"Origin": "https://attacker.invalid"},
        ).status_code
        == 403
    )


@pytest.mark.parametrize(
    "path",
    ["/api/review", "/api/resolution", "/api/source", "/api/graph", "/api/catalog"],
)
def test_missing_csrf_header_is_rejected(client, path):
    draft = _draft(client)
    client.headers.pop("X-Composer-Request")
    assert client.post(path, json=draft).status_code == 403


@pytest.mark.parametrize(
    "query", [None, "components.request_reader.properties.function_name"]
)
def test_resolution_inspects_current_edits_and_opt_out(client, query):
    draft = _draft(client)
    draft["components"]["request_reader"]["properties"]["function_name"] = (
        "edited-function"
    )
    draft["disabled_associations"] = ["lambda-execution-role"]
    draft["query"] = query
    with patch.object(
        client.app.state.adapter,
        "resolution",
        create=True,
        return_value={"value": "[REDACTED]", "events": []},
    ) as resolve:
        response = client.post("/api/resolution", json=draft)
    assert response.status_code == 200
    assert response.json() == {"value": "[REDACTED]", "events": [], "context": None}
    assert "function_name: edited-function" in resolve.call_args.args[0]
    assert "lambda-execution-role" in resolve.call_args.args[0]
    assert resolve.call_args.args[1:] == (None, query)


@pytest.mark.parametrize(
    "query",
    ["--show-values", "modules.api", "inputs..region", "inputs.region\n--show-values"],
)
def test_resolution_rejects_invalid_addresses(client, query):
    assert (
        client.post(
            "/api/resolution", json={**_draft(client), "query": query}
        ).status_code
        == 422
    )


@pytest.mark.parametrize("path", ["/api/review", "/api/resolution", "/api/source"])
def test_draft_operations_require_github_session(client, path):
    draft = _draft(client)
    client.app.state.settings.github_client_id = "configured"
    assert client.post(path, json=draft).status_code == 401


def test_demo_never_creates_a_pull_request(client):
    assert client.post("/api/pull-requests", json=_draft(client)).status_code == 503


def test_github_mode_requires_authentication_for_metadata(client):
    client.app.state.settings.github_client_id = "configured"
    for path in ("/api/catalog", "/api/platform", "/api/sample", "/api/repositories"):
        assert client.get(path).status_code == 401


def test_read_only_source_still_opens(client, tmp_path):
    client.app.state.settings.sample = tmp_path / "stack.yaml"
    client.app.state.settings.sample.write_text(
        "name: generated\n{% for x in [] %}{% endfor %}"
    )
    response = client.get("/api/sample")
    assert response.status_code == 200
    assert not response.json()["editable"]
    assert "whole-file Jinja" in response.json()["reason"]


def test_platform_view_exposes_sources_and_hides_backend_values(client, tmp_path):
    client.app.state.settings.config = tmp_path / "platform.yaml"
    (tmp_path / "validate.py").write_text("def validate(value): return 'pass'\n")
    client.app.state.settings.config.write_text("""
backend:
  data:
    type: s3
    secret_key: do-not-expose
var_validations:
  environment:
    description: Validate environment
    inline: secret-hook-source
plan_validations:
  local_script:
    description: Local source preview
    rule:
      script:
        source: local
        data:
          path: validate.py
  approved_plan:
    description: Validate the generated plan
    rule:
      script:
        source: git
        data:
          repo: https://github.com/example/platform.git
          path: policies/plan.py
          ref: abc123
provider_mappings:
  aws:
    source:
      source: registry
      data:
        address: hashicorp/aws
        version: "= 6.39.0"
    instances:
        default:
          description: Primary account
          config:
            data:
              region: us-east-1
              session_token: do-not-expose
module_mappings:
  service:
    description: Example service
    source:
      source: git
      data:
        repo: https://github.com/example/modules.git
        path: service
        ref: abc123
    properties:
      name:
        description: Service name
    outputs:
      arn:
        description: Service ARN
tools:
  tofu:
    version: 1.8.0
    binary: tofu
    api_token: do-not-expose
associations:
  execution_role:
    description: Attach the managed role.
    producers:
      select: component_type == 'role'
    consumers:
      select: component_type == 'function'
    bindings:
      - output: arn
        property: role_arn
    password: do-not-expose
""")
    response = client.get("/api/platform")
    assert response.status_code == 200
    assert "do-not-expose" not in response.text
    assert response.json()["backend"]["source"]["configuration"] == {
        "type": "s3",
        "secret_key": "[redacted]",
    }
    assert response.json()["tools"]["tofu"]["source"]["configuration"] == {
        "version": "1.8.0",
        "binary": "tofu",
        "api_token": "[redacted]",
    }
    assert response.json()["associations"][0]["source"]["configuration"][
        "bindings"
    ] == [{"output": "arn", "property": "role_arn"}]
    assert (
        response.json()["associations"][0]["source"]["configuration"]["password"]
        == "[redacted]"
    )
    assert "secret-hook-source" in response.text
    assert "def validate(value): return 'pass'" in response.text
    assert "Validate environment" in response.text
    assert response.json()["policies"][0]["source"]["label"] == "Inline Python"
    assert response.json()["policies"][2]["source"]["url"].endswith(
        "/example/platform/tree/abc123/policies/plan.py"
    )
    assert response.json()["providers"][0]["source"]["url"].endswith(
        "/provider/hashicorp/aws/v6.39.0"
    )
    assert (
        response.json()["providers"][0]["instances"][0]["source"]["label"]
        == "Static configuration data"
    )
    assert response.json()["providers"][0]["instances"][0]["source"][
        "configuration"
    ] == {"region": "us-east-1", "session_token": "[redacted]"}
    assert response.json()["components"][0]["source"]["url"].endswith(
        "/example/modules/tree/abc123/service"
    )


def test_unknown_component_type_blocks_review(client):
    draft = deepcopy(_draft(client))
    draft["components"]["request_reader"]["type"] = "not-catalogued"
    assert client.post("/api/review", json=draft).status_code == 422


def test_oauth_state_is_required(client):
    response = client.get(
        "/api/auth/callback?code=example&state=bad", follow_redirects=False
    )
    assert response.status_code == 303
    assert "cookie is missing" in _auth_error(response)


def _auth_error(response):
    return parse_qs(urlparse(response.headers["location"]).query)["auth_error"][0]


def _start_login(client):
    client.app.state.settings.github_client_id = "test-client"
    client.base_url = client.app.state.settings.public_url
    response = client.get("/api/auth/github", follow_redirects=False)
    assert response.status_code == 307
    return parse_qs(urlparse(response.headers["location"]).query)["state"][0]


def test_oauth_login_uses_public_origin_before_setting_cookie(client):
    client.app.state.settings.github_client_id = "test-client"
    client.base_url = "http://localhost:5173"
    response = client.get("/api/auth/github", follow_redirects=False)
    assert response.headers["location"] == "http://127.0.0.1:5173/api/auth/github"
    assert "set-cookie" not in response.headers
    _start_login(client)
    assert client.cookies.get("platform_oauth")


@pytest.mark.parametrize(
    ("query", "message"),
    [
        ("code=example&state=wrong", "state did not match"),
        ("code=example", "state did not match"),
        ("state={state}&error=access_denied", "authorization was denied"),
        ("state={state}", "authorization is incomplete"),
    ],
)
def test_oauth_callback_failures_return_to_gui(client, query, message):
    response = client.get(
        f"/api/auth/callback?{query.replace('{state}', _start_login(client))}",
        follow_redirects=False,
    )
    assert response.status_code == 303
    assert message in _auth_error(response)
    assert not client.cookies.get("platform_oauth")


def test_oauth_expired_state_returns_to_gui(client):
    with patch(
        "itsdangerous.timed.TimestampSigner.get_timestamp", return_value=1_700_000_000
    ):
        state = _start_login(client)
    with patch(
        "itsdangerous.timed.TimestampSigner.get_timestamp", return_value=1_700_000_601
    ):
        response = client.get(
            f"/api/auth/callback?code=example&state={state}", follow_redirects=False
        )
    assert "login expired" in _auth_error(response)


def test_oauth_invalid_cookie_returns_to_gui(client):
    _start_login(client)
    client.cookies.clear()
    client.cookies.set(
        "platform_oauth", "invalid", domain="127.0.0.1", path="/api/auth"
    )
    response = client.get(
        "/api/auth/callback?code=example&state=example", follow_redirects=False
    )
    assert "cookie is invalid" in _auth_error(response)


def test_oauth_success_creates_session(client):
    state = _start_login(client)
    with (
        patch(
            "stacksmith_platform.app._exchange_token",
            return_value={"access_token": "test-token"},
        ),
        patch(
            "stacksmith_platform.app.GitHubClient.user",
            return_value={"login": "example"},
        ),
    ):
        response = client.get(
            f"/api/auth/callback?code=example&state={state}", follow_redirects=False
        )
    assert response.status_code == 307
    assert response.headers["location"] == (
        client.app.state.settings.public_url + "/settings?section=github&auth_return=1"
    )
    assert client.get("/api/session").json()["login"] == "example"
    assert "test-token" not in response.text
    assert not client.cookies.get("platform_oauth")


def test_platform_tool_metadata_exposes_sanitized_configuration(client, tmp_path):
    client.app.state.settings.config = tmp_path / "platform.yaml"
    client.app.state.settings.config.write_text("""
backend:
  data:
    type: local
tools:
  tofu:
    version: 1.0.0
    private_token: secret-tool-value
""")
    response = client.get("/api/platform")
    assert response.status_code == 200
    assert response.json()["tools"] == {
        "tofu": {
            "version": "1.0.0",
            "source": {
                "kind": "data",
                "label": "Managed tool configuration",
                "detail": None,
                "url": None,
                "configuration": {
                    "version": "1.0.0",
                    "private_token": "[redacted]",
                },
            },
        }
    }
    assert "secret-tool-value" not in response.text


@pytest.mark.parametrize(
    "status,body",
    [(502, b"unavailable"), (200, b"invalid"), (302, b'{"access_token":"unexpected"}')],
)
def test_oauth_exchange_maps_http_and_json_failures(client, status, body):
    from stacksmith_platform.app import _exchange_token
    from stacksmith_platform.errors import ComposerError
    from stacksmith_platform.github_transport import GitHubResponse

    with (
        patch.object(
            client.app.state.github_transport,
            "request",
            return_value=GitHubResponse(status, body),
        ),
        pytest.raises(ComposerError, match="authorization failed") as caught,
    ):
        _exchange_token(
            client.app.state.settings, "code", client.app.state.github_transport
        )
    assert caught.value.status == 502


def test_oauth_exchange_uses_shared_transport(client):
    from stacksmith_platform.app import _exchange_token
    from stacksmith_platform.github_transport import GitHubResponse

    with patch.object(
        client.app.state.github_transport,
        "request",
        return_value=GitHubResponse(200, b'{"access_token":"user-token"}'),
    ) as request:
        assert _exchange_token(
            client.app.state.settings, "code", client.app.state.github_transport
        ) == {"access_token": "user-token"}
    assert request.call_args.args == (
        "POST",
        "https://github.com/login/oauth/access_token",
    )
    assert request.call_args.kwargs["data"]["code"] == "code"
