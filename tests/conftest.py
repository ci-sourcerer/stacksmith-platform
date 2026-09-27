import json
from io import StringIO
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from ruamel.yaml import YAML

from stacksmith_platform.app import create_app
from stacksmith_platform.documents import load_document, source_diff
from stacksmith_platform.settings import Settings

EXAMPLES = Path(__file__).parent.parent / "examples"
CATALOG = {
    "aws_lambda_function": {
        "inputs": [{"name": "function_name"}, {"name": "role_arn"}],
        "outputs": [{"name": "arn"}, {"name": "function_name"}],
    },
    "aws_iam_role": {
        "inputs": [{"name": "role_name"}, {"name": "policy_arns"}],
        "outputs": [{"name": "arn"}, {"name": "name"}],
    },
    "aws_iam_policy": {
        "inputs": [{"name": "name"}, {"name": "policy_statements"}],
        "outputs": [{"name": "arn"}],
    },
    "aws_managed_policy": {
        "inputs": [{"name": "policy_arn"}],
        "outputs": [{"name": "arn"}],
    },
}


def _fake_author(source, edits, path):
    document = load_document(source)
    original = json.dumps(document, sort_keys=True)
    for edit in edits:
        current = document
        for key in edit["path"][:-1]:
            current = current.setdefault(key, {})
        key = edit["path"][-1]
        if edit["action"] == "remove":
            current.pop(key, None)
        else:
            current[key] = edit["value"]
    if json.dumps(document, sort_keys=True) == original:
        proposed = source
    elif path.endswith(".json"):
        proposed = json.dumps(document, indent=2) + "\n"
    else:
        output = StringIO()
        parser = YAML(typ="rt")
        parser.indent(mapping=2, sequence=4, offset=2)
        parser.dump(document, output)
        proposed = output.getvalue()
    return {
        "version": 1,
        "validation": "document",
        "source": proposed,
        "diff": source_diff(source, proposed, path),
        "changed": proposed != source,
    }


class FakeAdapter:
    """Replace CLI calls in HTTP contract tests."""

    def author(self, source, edits, path):
        """Return a fake authoring proposal for HTTP contract tests.

        Args:
            source: Original source.
            edits: Composer's ordered set or remove edits.
            path: Source filename selecting YAML or JSON.

        Returns:
            A version 1 source proposal without inspection.
        """
        return _fake_author(source, edits, path)

    def catalog(self, github_token=None):
        """Return test component interfaces."""
        return CATALOG

    def associations(self, source, github_token=None):
        """Return an empty association result for a provided stack source."""
        return {"rules": [], "applications": []}

    def review(self, source, edits, path, github_token=None):
        """Return authored source and inspection data for HTTP tests.

        Args:
            source: Original source.
            edits: Ordered authored edits.
            path: Document filename.
            github_token: Unused session credential.

        Returns:
            Authored source, catalog, and association provenance.
        """
        proposal = self.author(source, edits, path)
        return {
            "source": proposal["source"],
            "catalog": self.catalog(github_token),
            "associations": self.associations(proposal["source"], github_token),
        }

    def resolution(self, source, github_token=None, query=None):
        """Return an empty redacted resolution response.

        Args:
            source: Current authored stack source.
            github_token: Session credential for private sources.
            query: Optional configuration address.

        Returns:
            An empty resolution history with redacted effective data.
        """
        return {"effective": {}, "events": []}


@pytest.fixture
def client(tmp_path):
    app = create_app(
        Settings(
            _env_file=None,
            config=EXAMPLES / "platform.yaml",
            sample=EXAMPLES / "stack.yaml",
            database=tmp_path / "sessions.sqlite3",
            secret_key="test-secret-key-with-at-least-32-characters",
        )
    )
    app.state.adapter = FakeAdapter()
    with TestClient(app, headers={"X-Composer-Request": "1"}) as client:
        yield client
