import json
import subprocess
from unittest.mock import Mock

import pytest

from stacksmith_platform.execution import _inspect
from stacksmith_platform.inspection_results import _diagnostic_text


def _payload(authoring=None):
    return {
        "revision": "a" * 40,
        "timeout": 30,
        "inspection": {
            "args": ["author"] if authoring else ["info", "associations"],
            "source": None,
            "config": "config.yaml",
            "config_source": None,
            "github_token": "git-secret",
            "authoring_request": authoring,
        },
    }


def test_failed_inspection_retains_sanitized_streams(tmp_path, monkeypatch):
    monkeypatch.setattr(
        "subprocess.run",
        Mock(
            return_value=subprocess.CompletedProcess(
                [], 2, "partial result", "\x1b[31mMissing input\x1b[0m git-secret"
            )
        ),
    )
    result = _inspect(_payload(), tmp_path)
    assert result["exit_code"] == 2
    assert result["diagnostics"]["stderr"] == "Missing input <redacted>"
    assert result["diagnostics"]["stdout"] == "partial result"
    assert "git-secret" not in json.dumps(result)


@pytest.mark.parametrize(
    "context", [None, {"repository": "acme/service", "revision": "a" * 40}]
)
def test_compound_review_uses_one_runtime_and_checkout(tmp_path, monkeypatch, context):
    checkout = Mock(side_effect=lambda *args: (tmp_path / "workspace").mkdir())
    monkeypatch.setattr("stacksmith_platform.execution._checkout", checkout)
    command = Mock(
        return_value=subprocess.CompletedProcess(
            [],
            0,
            json.dumps(
                {"source": "draft", "catalog": {}, "associations": {}, "timings": {}}
            ),
            "",
        )
    )
    monkeypatch.setattr("subprocess.run", command)
    payload = _payload(
        {"version": 1, "source": "original", "edits": [], "path": "stack.yaml"}
    )
    payload["inspection"].update(args=["review"], context=context)
    result = _inspect(payload, tmp_path)
    assert result["exit_code"] == 0
    assert checkout.call_count == (1 if context else 0)
    command.assert_called_once()
    assert command.call_args.args[0][1:] == [
        "-m",
        "stacksmith_platform.authoring_inspection",
    ]
    assert command.call_args.kwargs["shell"] is False
    assert command.call_args.kwargs["timeout"] == 30
    assert (
        json.loads(command.call_args.kwargs["input"])["authoring_request"]["source"]
        == "original"
    )
    timings = json.loads(result["inspection_output"])["timings"]
    assert timings["runtime"] >= timings["preparation"] >= 0


def test_inspection_timeout_returns_uploadable_failure(tmp_path, monkeypatch):
    monkeypatch.setattr(
        "subprocess.run",
        Mock(
            side_effect=subprocess.TimeoutExpired(
                "stacksmith", 30, output=b"partial", stderr=b"missing git-secret"
            )
        ),
    )
    result = _inspect(_payload(), tmp_path)
    assert result["diagnostics"]["status"] == 504
    assert result["diagnostics"]["code"] == "timeout"
    assert result["diagnostics"]["stdout"] == "partial"
    assert result["exit_code"] != 0


def test_document_rejection_returns_uploadable_failure(tmp_path, monkeypatch):
    monkeypatch.setattr(
        "subprocess.run",
        Mock(
            return_value=subprocess.CompletedProcess(
                [],
                6,
                json.dumps(
                    {
                        "version": 1,
                        "error": {
                            "code": "invalid_document",
                            "message": "Missing name",
                        },
                    }
                ),
                "document failed",
            )
        ),
    )
    result = _inspect(_payload({"version": 1}), tmp_path)
    assert result["diagnostics"]["code"] == "document_rejected"
    assert result["diagnostics"]["status"] == 422
    assert result["diagnostics"]["stderr"] == "document failed"


@pytest.mark.parametrize(
    "failure", [OSError("cannot launch"), ValueError("invalid context")]
)
def test_inspection_preparation_failures_are_reported(tmp_path, monkeypatch, failure):
    monkeypatch.setattr(
        "stacksmith_platform.execution._prepare_inspection", Mock(side_effect=failure)
    )
    assert _inspect(_payload(), tmp_path)["diagnostics"]["code"] == "context_failed"


def test_diagnostic_stream_limit_applies_after_redaction():
    text = _diagnostic_text(
        "\x1b[32mtoken-secret\x1b[0m\x00" + "é" * 40_000, ["token-secret"]
    )
    assert text.startswith("<redacted>")
    assert text.endswith("[output truncated]")
    assert len(text.encode()) <= 32 * 1024
    assert "\x1b" not in text and "\x00" not in text
