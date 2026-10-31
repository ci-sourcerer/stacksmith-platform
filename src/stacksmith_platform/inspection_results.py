import json
import re
import subprocess
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, ValidationError

from .errors import ComposerError

_STREAM_LIMIT = 32 * 1024
_TERMINAL_SEQUENCE = re.compile(
    r"\x1b(?:\[[0-?]*[ -/]*[@-~]|\][^\x07\x1b]*(?:\x07|\x1b\\))"
)


def _diagnostic_text(value, redactions=()):
    if isinstance(value, bytes):
        value = value.decode(errors="replace")
    value = value or ""
    value = _TERMINAL_SEQUENCE.sub("", value)
    value = "".join(
        character
        for character in value
        if character in "\n\t" or ord(character) >= 32 and ord(character) != 127
    )
    for secret in sorted(set(filter(None, redactions)), key=len, reverse=True):
        value = value.replace(secret, "<redacted>")
    if len(value.encode()) > _STREAM_LIMIT:
        return (
            value.encode()[: _STREAM_LIMIT - len("\n[output truncated]")].decode(
                errors="ignore"
            )
            + "\n[output truncated]"
        )
    return value


def _inspection_failure(
    stage,
    message,
    code="command_failed",
    status=422,
    exit_code=1,
    stdout="",
    stderr="",
    redactions=(),
):
    return {
        "exit_code": exit_code,
        "inspection_output": "",
        "diagnostics": {
            "stage": stage,
            "code": code,
            "status": status,
            "exit_code": exit_code,
            "message": _diagnostic_text(message, redactions),
            "stdout": _diagnostic_text(stdout, redactions),
            "stderr": _diagnostic_text(stderr, redactions),
        },
    }


def _run_inspection(command, *, stage, authoring=False, redactions=(), **options):
    try:
        result = subprocess.run(
            command, shell=False, capture_output=True, text=True, check=False, **options
        )
    except subprocess.TimeoutExpired as exc:
        return _inspection_failure(
            stage,
            "Stacksmith inspection timed out.",
            "timeout",
            504,
            stdout=exc.stdout,
            stderr=exc.stderr,
            redactions=redactions,
        )
    except OSError as exc:
        return _inspection_failure(
            stage,
            "Could not launch the inspection executable.",
            "launch_failed",
            503,
            stderr=str(exc),
            redactions=redactions,
        )
    if authoring:
        try:
            output = json.dumps(_authoring_response(result))
        except ComposerError as exc:
            return _inspection_failure(
                stage,
                str(exc),
                "document_rejected" if exc.status == 422 else "incompatible_response",
                exc.status,
                result.returncode or 1,
                result.stdout,
                result.stderr,
                redactions,
            )
    elif result.returncode:
        return _inspection_failure(
            stage,
            "Stacksmith inspection failed. Review the draft and environment inputs.",
            exit_code=result.returncode,
            stdout=result.stdout,
            stderr=result.stderr,
            redactions=redactions,
        )
    else:
        output = result.stdout
    return {"exit_code": 0, "inspection_output": output}


def _raise_inspection_failure(result):
    if result["exit_code"]:
        diagnostics = result.get("diagnostics", {})
        raise ComposerError(
            diagnostics.get("message", "Stacksmith inspection failed."),
            diagnostics.get("status", 422),
            diagnostics or None,
        )


class _AuthoringProposal(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    version: Literal[1]
    validation: Literal["document"]
    source: str
    diff: str
    changed: bool


def _authoring_response(result: subprocess.CompletedProcess[str]) -> dict[str, Any]:
    if result.returncode:
        try:
            response = json.loads(result.stdout)
        except json.JSONDecodeError as exc:
            raise ComposerError(
                "Stacksmith must support the version 1 authoring CLI.", 502
            ) from exc
        if (
            isinstance(response, dict)
            and response.get("version") == 1
            and isinstance(response.get("error"), dict)
            and response["error"].get("code") == "invalid_document"
        ):
            raise ComposerError(
                "Stacksmith rejected this authored document. Review its source and field values."
            )
        raise ComposerError("Stacksmith must support the version 1 authoring CLI.", 502)
    try:
        return _AuthoringProposal.model_validate_json(result.stdout).model_dump()
    except ValidationError as exc:
        raise ComposerError(
            "Stacksmith returned an incompatible authoring response.", 502
        ) from exc
