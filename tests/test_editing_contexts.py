import json
from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
from functools import partial
from importlib.metadata import version
from pathlib import Path
from threading import Event
from unittest.mock import Mock

import pytest
from ruamel.yaml import YAML
from stacksmith.api import inspect_associations

from stacksmith_platform.database import Database, prepared_contexts
from stacksmith_platform.documents import load_document
from stacksmith_platform.editing_contexts import EditingContexts, context_key
from stacksmith_platform.errors import ComposerError

CONFIG = Path(__file__).resolve().parents[1] / "examples/platform.yaml"
SOURCE = json.dumps(
    {
        "name": "editing",
        "components": {
            "policy": {"type": "aws_iam_policy", "tags": ["lambda_baseline"]},
            "role": {"type": "aws_iam_role", "tags": ["lambda_execution"]},
            "function": {"type": "aws_lambda_function"},
        },
    }
)


@pytest.fixture
def prepared():
    return {
        "resolver_version": version("stacksmith"),
        "config": YAML(typ="safe").load(CONFIG),
        "catalog": {"aws_lambda_function": {"inputs": [], "outputs": []}},
    }


def _initial(prepared, source=SOURCE):
    return {
        "source": source,
        "catalog": deepcopy(prepared["catalog"]),
        "associations": {"rules": [], "applications": []},
        "_editing_context": deepcopy(prepared),
    }


@pytest.mark.parametrize(
    "edits",
    [
        [
            {
                "action": "set",
                "path": ["components", "second"],
                "value": {"type": "aws_lambda_function"},
            }
        ],
        [{"action": "remove", "path": ["components", "function"]}],
        [
            {
                "action": "set",
                "path": ["disabled_associations"],
                "value": ["lambda-execution-role"],
            }
        ],
        [
            {
                "action": "set",
                "path": ["components", "function", "properties"],
                "value": {"role_arn": "explicit"},
            }
        ],
    ],
)
def test_warm_edits_match_full_inspection_without_another_execution(
    prepared, tmp_path, edits
):
    contexts = EditingContexts()
    prepare = Mock(return_value=_initial(prepared))
    contexts.review("context", SOURCE, [], "stack.json", prepare)

    result = contexts.review("context", SOURCE, edits, "stack.json", prepare)
    stack = tmp_path / "stack.json"
    stack.write_text(result["source"])
    result["associations"].pop("_document")
    assert result["associations"] == inspect_associations(stack, config=[str(CONFIG)])
    assert prepare.call_count == 1
    assert contexts.metrics()["hits"] == 1


def test_source_comments_and_authored_layout_survive_prepared_review(prepared):
    contexts = EditingContexts()
    prepare = Mock(return_value=_initial(prepared))
    contexts.review("context", SOURCE, [], "stack.yaml", prepare)
    source = f"# keep this comment\n{SOURCE}\n"
    result = contexts.review(
        "context",
        source,
        [
            {
                "action": "set",
                "path": ["metadata"],
                "value": {
                    "stacksmith_composer": {"positions": {"node": {"x": 7, "y": 3}}}
                },
            }
        ],
        "stack.yaml",
        prepare,
    )
    assert result["source"].startswith("# keep this comment")
    assert (
        load_document(result["source"])["metadata"]["stacksmith_composer"]["positions"][
            "node"
        ]["x"]
        == 7
    )
    assert prepare.call_count == 1


def test_real_stack_with_deferred_references_matches_full_inspection(
    prepared, tmp_path
):
    contexts = EditingContexts()
    prepare = Mock(return_value=_initial(prepared))
    contexts.review("context", SOURCE, [], "stack.yaml", prepare)
    source = (CONFIG.parent / "stack.yaml").read_text()
    result = contexts.review("context", source, [], "stack.yaml", prepare)
    stack = tmp_path / "stack.yaml"
    stack.write_text(result["source"])
    result["associations"].pop("_document")
    assert result["associations"] == inspect_associations(stack, config=[str(CONFIG)])
    assert result["associations"]["applications"]
    assert prepare.call_count == 1


def _blocked_prepare(started, release, result):
    started.set()
    assert release.wait(5)
    return result


def test_simultaneous_editors_share_metadata_but_never_share_authored_results(prepared):
    contexts = EditingContexts()
    started, release = Event(), Event()
    prepare = Mock(
        side_effect=partial(_blocked_prepare, started, release, _initial(prepared))
    )
    with ThreadPoolExecutor(max_workers=2) as executor:
        first = executor.submit(
            contexts.review, "context", SOURCE, [], "stack.json", prepare
        )
        assert started.wait(5)
        second = executor.submit(
            contexts.review,
            "context",
            SOURCE,
            [{"action": "remove", "path": ["components", "function"]}],
            "stack.json",
            prepare,
        )
        release.set()
        assert "function" in load_document(first.result(5)["source"])["components"]
        assert "function" not in load_document(second.result(5)["source"])["components"]
    assert prepare.call_count == 1


def test_invalidation_during_preparation_cannot_repopulate_old_context(prepared):
    contexts = EditingContexts()
    started, release = Event(), Event()
    with ThreadPoolExecutor(max_workers=1) as executor:
        pending = executor.submit(
            contexts.review,
            "context",
            SOURCE,
            [],
            "stack.json",
            partial(_blocked_prepare, started, release, _initial(prepared)),
        )
        assert started.wait(5)
        contexts.invalidate()
        release.set()
        pending.result(5)
    assert contexts.metrics()["entries"] == 0


def test_template_rendering_stays_isolated_and_pr_review_can_bypass_reuse(prepared):
    contexts = EditingContexts()
    prepare = Mock(
        side_effect=[_initial(prepared), _initial(prepared), _initial(prepared)]
    )
    contexts.review("context", SOURCE, [], "stack.json", prepare)
    contexts.review(
        "context",
        SOURCE.replace(
            '"type": "aws_lambda_function"',
            '"type": "aws_lambda_function", "properties": {"name": "{{ inputs.name }}"}',
        ),
        [],
        "stack.json",
        prepare,
    )
    contexts.review(None, SOURCE, [], "stack.json", prepare)
    assert prepare.call_count == 3


def test_context_bounds_and_resolver_version_mismatch_bypass_reuse(
    prepared, monkeypatch
):
    contexts = EditingContexts()
    monkeypatch.setattr("stacksmith_platform.editing_contexts.MAX_CONTEXT_BYTES", 1)
    contexts.review(
        "context", SOURCE, [], "stack.json", Mock(return_value=_initial(prepared))
    )
    assert contexts.metrics()["entries"] == contexts.metrics()["bytes"] == 0
    prepared["resolver_version"] = "different-execution-library"
    contexts.review(
        "other", SOURCE, [], "stack.json", Mock(return_value=_initial(prepared))
    )
    assert contexts.metrics()["entries"] == 0


@pytest.mark.parametrize(
    "field", ["principal", "repository", "path", "head", "config_revision"]
)
def test_context_keys_separate_principals_and_snapshot_revisions(field):
    snapshot = {
        "principal": "alice",
        "repository": "owner/repo",
        "path": "stack.yaml",
        "head": "commit",
        "config_revision": "config",
    }
    context = {
        "_input_manifest": {"cacheable": True, "digest": "dependencies"},
        "environment": "dev",
        "environment_revision": 1,
    }
    assert context_key(snapshot, context, "image") != context_key(
        {**snapshot, field: "changed"}, context, "image"
    )
    assert context_key(snapshot, context, "image") != context_key(
        snapshot, {**context, "environment_revision": 2}, "image"
    )
    assert context_key(snapshot, context, "image") != context_key(
        snapshot, context, "changed-image"
    )
    assert (
        context_key(snapshot, {"_input_manifest": {"cacheable": False}}, "image")
        is None
    )


def test_invalid_edits_do_not_fall_back_to_running_user_code(prepared):
    contexts = EditingContexts()
    prepare = Mock(return_value=_initial(prepared))
    contexts.review("context", SOURCE, [], "stack.json", prepare)
    with pytest.raises(ComposerError):
        contexts.review(
            "context",
            SOURCE,
            [{"action": "set", "path": ["name"], "value": 123}],
            "stack.json",
            prepare,
        )
    assert prepare.call_count == 1


def test_changed_producer_tags_preserve_cardinality_failures(prepared, tmp_path):
    from stacksmith.exceptions import StacksmithConfigError

    contexts = EditingContexts()
    prepare = Mock(return_value=_initial(prepared))
    contexts.review("context", SOURCE, [], "stack.json", prepare)
    with pytest.raises(ComposerError, match="exactly_one"):
        contexts.review(
            "context",
            SOURCE,
            [{"action": "set", "path": ["components", "role", "tags"], "value": []}],
            "stack.json",
            prepare,
        )
    document = json.loads(SOURCE)
    document["components"]["role"]["tags"] = []
    stack = tmp_path / "stack.json"
    stack.write_text(json.dumps(document))
    with pytest.raises(StacksmithConfigError, match="exactly_one"):
        inspect_associations(stack, config=[str(CONFIG)])
    assert prepare.call_count == 1


def test_replicas_share_encrypted_metadata_without_sharing_draft_results(prepared):
    database = Database("sqlite://", "shared-context-test-key")
    database.initialize()
    try:
        first = EditingContexts(database)
        first.review(
            "scoped-context",
            SOURCE,
            [],
            "stack.json",
            Mock(return_value=_initial(prepared)),
        )
        second = EditingContexts(database)
        prepare = Mock(
            side_effect=AssertionError("Another replica launched an inspection")
        )
        result = second.review(
            "scoped-context",
            SOURCE,
            [{"action": "remove", "path": ["components", "function"]}],
            "stack.json",
            prepare,
        )
        assert "function" not in load_document(result["source"])["components"]
        assert second.metrics()["shared_hits"] == 1
        with database.transaction() as connection:
            payload = (
                connection.execute(prepared_contexts.select())
                .mappings()
                .one()["payload"]
            )
        assert "resolver_version" not in payload
        assert set(json.loads(database.decrypt(payload))) == {
            "resolver_version",
            "config",
            "catalog",
        }
        prepare.assert_not_called()
    finally:
        database.engine.dispose()


@pytest.mark.parametrize("change", ["expired", "invalid-encryption"])
def test_expired_or_invalid_shared_metadata_is_prepared_again(prepared, change):
    database = Database("sqlite://", "shared-context-test-key")
    database.initialize()
    try:
        EditingContexts(database).review(
            "context", SOURCE, [], "stack.json", Mock(return_value=_initial(prepared))
        )
        with database.transaction() as connection:
            connection.execute(
                prepared_contexts.update().values(
                    **(
                        {"expires": 0}
                        if change == "expired"
                        else {"payload": "invalid"}
                    )
                )
            )
        prepare = Mock(return_value=_initial(prepared))
        EditingContexts(database).review("context", SOURCE, [], "stack.json", prepare)
        prepare.assert_called_once()
    finally:
        database.engine.dispose()
