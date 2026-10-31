import json
import logging
from concurrent.futures import ThreadPoolExecutor
from contextvars import copy_context
from types import SimpleNamespace

import pytest

from stacksmith_platform.review_profiling import (
    _capture,
    _operation,
    _stage,
    profile_review,
)


def _request(enabled=True):
    return SimpleNamespace(
        headers={"X-Review-Profile-ID": "a" * 32},
        app=SimpleNamespace(
            state=SimpleNamespace(
                settings=SimpleNamespace(review_profiling=enabled),
                github_transport=SimpleNamespace(counter_id="process"),
            )
        ),
    )


def _observe():
    profile, stage = _capture()
    profile._observe(stage, "/repos/private-owner/private-repo/commits/main", 0.25, 304)


def _review(request):
    _stage("completion")
    with ThreadPoolExecutor(max_workers=4) as executor:
        pending = [executor.submit(copy_context().run, _observe) for _ in range(8)]
        for future in pending:
            future.result()
    return "ok"


def test_profile_attribution_and_redaction(caplog):
    with caplog.at_level(logging.INFO):
        assert profile_review(_review)(_request()) == "ok"
    event = json.loads(caplog.records[-1].getMessage().split("REVIEW_PROFILE ")[1])
    assert event["review_id"] == "a" * 32
    assert event["outcome"] == "succeeded"
    assert event["github"] == [
        {
            "stage": "completion",
            "operation": "dependency_revision",
            "count": 8,
            "seconds": 2.0,
            "conditional_hits": 8,
            "failures": 0,
            "duplicate_reads": 0,
            "retries": 0,
            "outcomes": {"304": 8},
        }
    ]
    assert "private-owner" not in caplog.text
    assert _capture() == (None, None)


def _failed_review(request):
    raise ValueError("private failure text")


def test_failed_review_emits_summary_and_restores_context(caplog):
    with caplog.at_level(logging.INFO), pytest.raises(ValueError):
        profile_review(_failed_review)(_request())
    assert '"outcome": "failed"' in caplog.text
    assert "private failure text" not in caplog.text
    assert _capture() == (None, None)


def test_disabled_profile_has_no_event(caplog):
    assert profile_review(str)(_request(False))
    assert "REVIEW_PROFILE" not in caplog.text


@pytest.mark.parametrize(
    ("path", "expected"),
    [
        ("/user/installations", "repository_discovery"),
        ("/user/installations/1/repositories", "repository_discovery"),
        ("/repos/o/r", "repository_metadata"),
        ("/repos/o/r/contents/config.yaml", "managed_configuration"),
        ("/repos/o/r/commits/main", "dependency_revision"),
        ("/repos/o/r/properties/values", "other"),
    ],
)
def test_operation_categories(path, expected):
    assert _operation(path) == expected


def test_duplicate_reads_do_not_cross_stage_boundary():
    from stacksmith_platform.review_profiling import ReviewProfile

    profile = ReviewProfile("a", "p")
    profile._observe("preflight", "/repos/o/r", 0.1, 200, "opaque")
    profile._observe("preflight", "/repos/o/r", 0.1, 304, "opaque")
    profile._observe("completion", "/repos/o/r", 0.1, 304, "opaque")
    assert (
        profile.operations[("preflight", "repository_metadata")]["duplicate_reads"] == 1
    )
    assert (
        profile.operations[("completion", "repository_metadata")]["duplicate_reads"]
        == 0
    )
