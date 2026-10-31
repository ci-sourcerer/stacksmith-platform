import json
import logging
import re
from contextvars import ContextVar
from functools import wraps
from threading import Lock
from time import perf_counter
from uuid import uuid4

_CURRENT = ContextVar("review_profile", default=None)


def _operation(path):
    if re.fullmatch(r"/user/installations(?:/[^/]+/repositories)?", path):
        return "repository_discovery"
    if "/contents/" in path:
        return "managed_configuration"
    if "/commits/" in path:
        return "dependency_revision"
    if re.fullmatch(r"/repos/[^/]+/[^/]+", path):
        return "repository_metadata"
    return "other"


class ReviewProfile:
    """Collect bounded, identity-free timing totals for one review.

    Args:
        identifier: Opaque review correlation ID.
        process: Opaque API process lifetime ID.
    """

    def __init__(self, identifier, process):
        self.identifier = identifier
        self.process = process
        self.started = perf_counter()
        self.stage = "preflight"
        self.stage_started = self.started
        self.stages = {}
        self.operations = {}
        self.reads = set()
        self.lock = Lock()

    def _stage(self, name):
        now = perf_counter()
        self.stages[self.stage] = (
            self.stages.get(self.stage, 0) + now - self.stage_started
        )
        self.stage, self.stage_started = name, now

    def _observe(
        self,
        stage,
        path,
        elapsed,
        status,
        request_key=None,
        *,
        retried=False,
        outcome=None,
    ):
        with self.lock:
            entry = self.operations.setdefault(
                (stage, _operation(path)),
                {
                    "count": 0,
                    "seconds": 0.0,
                    "conditional_hits": 0,
                    "failures": 0,
                    "duplicate_reads": 0,
                    "retries": 0,
                    "outcomes": {},
                },
            )
            if request_key is not None:
                identity = (stage, request_key)
                entry["duplicate_reads"] += identity in self.reads
                if len(self.reads) < 1024:
                    self.reads.add(identity)
            entry["retries"] += retried
            outcome = outcome or (str(status) if status is not None else "network")
            entry["outcomes"][outcome] = entry["outcomes"].get(outcome, 0) + 1
            entry["count"] += 1
            entry["seconds"] += elapsed
            entry["conditional_hits"] += status == 304
            entry["failures"] += status is None or status >= 400

    def _emit(self, outcome):
        self._stage("finished")
        logging.getLogger(__name__).info(
            "REVIEW_PROFILE %s",
            json.dumps(
                {
                    "version": 1,
                    "review_id": self.identifier,
                    "process_id": self.process,
                    "outcome": outcome,
                    "total_seconds": perf_counter() - self.started,
                    "stages": self.stages,
                    "github": [
                        {"stage": stage, "operation": operation, **values}
                        for (stage, operation), values in sorted(
                            self.operations.items()
                        )
                    ],
                }
            ),
        )


def profile_review(function):
    """Decorate a review with optional diagnostic attribution.

    Args:
        function: Synchronous review implementation.

    Returns:
        The implementation with opt-in summary logging.
    """

    @wraps(function)
    def wrapped(request, *args, **kwargs):
        if not request.app.state.settings.review_profiling:
            return function(request, *args, **kwargs)
        identifier = request.headers.get("X-Review-Profile-ID", "")
        profile = ReviewProfile(
            identifier if re.fullmatch(r"[0-9a-f]{32}", identifier) else uuid4().hex,
            request.app.state.github_transport.counter_id,
        )
        token = _CURRENT.set(profile)
        outcome = "failed"
        try:
            result = function(request, *args, **kwargs)
            outcome = "succeeded"
            return result
        finally:
            _CURRENT.reset(token)
            profile._emit(outcome)

    return wrapped


def _stage(name):
    if profile := _CURRENT.get():
        profile._stage(name)


def _capture():
    profile = _CURRENT.get()
    return (profile, profile.stage) if profile else (None, None)
