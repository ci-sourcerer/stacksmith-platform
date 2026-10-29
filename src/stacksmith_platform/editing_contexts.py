import hashlib
import json
import time
from collections import OrderedDict
from concurrent.futures import Future
from copy import deepcopy
from importlib.metadata import version
from threading import Lock

from cryptography.fernet import InvalidToken
from jinja2 import Environment, TemplateSyntaxError, nodes
from pydantic import ValidationError
from sqlalchemy.dialects.postgresql import insert as postgres_insert
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from stacksmith.associations import association_resolution_payload, resolve_associations
from stacksmith.authoring import StackEdit, author_stack
from stacksmith.exceptions import StacksmithConfigError
from stacksmith.models import StackDefinition, ToolConfig

from .database import prepared_contexts, row
from .documents import load_document
from .errors import ComposerError
from .graph import _chain

MAX_CONTEXT_BYTES = 32 * 1024**2
MAX_CONTEXTS = 64
MAX_PREPARATIONS = 16


def prepare_editing_context(inputs):
    """Export validated metadata for resolving literal, single-file stack edits.

    Args:
        inputs: Inputs already inspected in an isolated execution.

    Returns:
        Private context metadata, or `None` when preparation cannot be reused.
    """
    from stacksmith.api import load_runtime_config

    if len(inputs["stack_file"]) != 1:
        return None
    _, _, config = load_runtime_config(
        inputs["config"], inputs["build_dir"], merge_mode=inputs["merge_mode"]
    )
    if config.default_module_mapping is not None:
        return None
    return {
        "resolver_version": version("stacksmith"),
        "config": config.model_dump(mode="json", exclude={"remote_auth"}),
    }


def author_source(source, edits, path):
    """Apply document edits using the same authoring library as inspection.

    Args:
        source: Original authored document.
        edits: Ordered structured edits.
        path: Repository document filename.

    Returns:
        Authored source, diff, and change status.

    Raises:
        ComposerError: The source or proposed edits are invalid.
    """
    load_document(source)
    try:
        proposed = author_stack(
            source, [StackEdit.model_validate(edit) for edit in edits], path
        )
    except (StacksmithConfigError, ValidationError) as exc:
        raise ComposerError(str(exc), 422) from exc
    load_document(proposed.source)
    return {
        "version": 1,
        "validation": "document",
        "source": proposed.source,
        "diff": proposed.diff,
        "changed": proposed.changed,
    }


def _resolve(context, source, edits, path):
    proposed = author_source(source, edits, path)
    document = load_document(proposed["source"])
    if not _static_selection(document) or not _deferred_references_only(document):
        return None
    try:
        config = ToolConfig.model_validate(context["config"])
    except ValidationError:
        return None
    try:
        associations = association_resolution_payload(
            resolve_associations(StackDefinition.model_validate(document), config),
            config,
        )
    except (StacksmithConfigError, ValidationError) as exc:
        raise ComposerError(str(exc), 422) from exc
    return {
        "source": proposed["source"],
        "catalog": deepcopy(context["catalog"]),
        "associations": {**associations, "_document": document},
    }


def _static_selection(document):
    return not any(
        marker
        in json.dumps(
            {
                "name": document.get("name"),
                "tags": document.get("tags"),
                "disabled": document.get("disabled_associations"),
                "components": {
                    name: {
                        key: value
                        for key, value in component.items()
                        if key in {"type", "tags", "disabled_associations"}
                    }
                    for name, component in document.get("components", {}).items()
                },
            }
        )
        for marker in ("{{", "{%", "${")
    )


def _deferred_references_only(value):
    if isinstance(value, dict):
        return all(
            not any(marker in key for marker in ("{{", "{%", "${"))
            and _deferred_references_only(child)
            for key, child in value.items()
        )
    if isinstance(value, list):
        return all(_deferred_references_only(child) for child in value)
    if not isinstance(value, str) or not any(
        marker in value for marker in ("{{", "{%", "${")
    ):
        return True
    if "{%" in value or "${" in value:
        return False
    try:
        expression = Environment().parse(value)
    except TemplateSyntaxError:
        return False
    # Parse references without executing user-authored template expressions.
    return all(
        isinstance(output, nodes.Output)
        and all(
            isinstance(child, nodes.TemplateData)
            or (
                bool(chain := _chain(child))
                and len(chain) == 3
                and chain[0] == "components"
            )
            for child in output.nodes
        )
        for output in expression.body
    )


def context_key(snapshot, context, image):
    """Identify metadata by principal and every verified preparation revision.

    Args:
        snapshot: Signed source snapshot verified for the current principal.
        context: Current authorized inspection context, if available.
        image: Immutable execution image identifier.

    Returns:
        An opaque digest, or `None` when dependency stability is unproven.
    """
    if context and not (context.get("_input_manifest") or {}).get("cacheable"):
        return None
    return hashlib.sha256(
        json.dumps(
            {
                "principal": snapshot["principal"],
                "repository": snapshot.get("repository"),
                "path": snapshot["path"],
                "revision": snapshot.get("head"),
                "configuration": snapshot["config_revision"],
                "environment": (context or {}).get("environment"),
                "environment_revision": (context or {}).get("environment_revision"),
                "dependencies": (context or {}).get("_input_manifest"),
                "image": image,
                "resolver": version("stacksmith"),
            },
            sort_keys=True,
        ).encode()
    ).hexdigest()


class EditingContexts:
    """Bound prepared metadata and reuse encrypted metadata across API replicas.

    Args:
        database: Optional shared store for encrypted metadata, without draft results.
    """

    def __init__(self, database=None):
        self.database = database
        self._lock = Lock()
        self._entries = OrderedDict()
        self._pending = {}
        self._bytes = 0
        self._revision = 0
        self._hits = 0
        self._preparations = 0
        self._shared_hits = 0

    def invalidate(self):
        """Discard metadata after a configuration notification or reconnect.

        Returns:
            None. Preparations already running cannot repopulate this revision.
        """
        with self._lock:
            self._entries.clear()
            self._bytes = 0
            self._revision += 1

    def metrics(self):
        """Report bounded cache use without principal or repository identities.

        Returns:
            Entry, byte, pending, preparation, and hit counts.
        """
        with self._lock:
            return {
                "entries": len(self._entries),
                "bytes": self._bytes,
                "pending": len(self._pending),
                "preparations": self._preparations,
                "hits": self._hits,
                "shared_hits": self._shared_hits,
            }

    def review(self, key, source, edits, path, prepare):
        """Resolve an edit against prepared metadata, preparing only on a miss.

        Args:
            key: Verified context digest, or `None` to require isolation.
            source: Original authored source.
            edits: Ordered document edits.
            path: Repository filename.
            prepare: Callable performing a fresh isolated review.

        Returns:
            A review of this request's source and edits.

        Raises:
            ComposerError: Preparation or connection resolution fails.
        """
        if key is None:
            return prepare()
        self._load_shared(key)
        with self._lock:
            revision = self._revision
            cached = self._entries.get(key)
            if cached:
                self._entries.move_to_end(key)
            pending = self._pending.get(key)
            owner = cached is None and pending is None
            if owner:
                if len(self._pending) >= MAX_PREPARATIONS:
                    raise ComposerError(
                        "Editing context preparation is busy. Retry shortly.", 503
                    )
                pending = self._pending[key] = Future()
                self._preparations += 1
        if not owner:
            context = cached[0] if cached else pending.result()
            if (
                context is not None
                and (result := _resolve(context, source, edits, path)) is not None
            ):
                with self._lock:
                    self._hits += 1
                return result
            return prepare()
        try:
            result = prepare()
            context = result.pop("_editing_context", None)
            if context and context.get("resolver_version") != version("stacksmith"):
                context = None
            if context:
                context = {**context, "catalog": deepcopy(result["catalog"])}
                self._save_shared(key, context)
            with self._lock:
                if context and revision == self._revision:
                    self._store(key, context)
                pending.set_result(context)
            return result
        except BaseException as exc:
            # Release other requests waiting for this failed preparation, including shutdown.
            pending.set_exception(exc)
            raise
        finally:
            with self._lock:
                self._pending.pop(key, None)

    def _store(self, key, context):
        size = len(json.dumps(context).encode())
        if size > MAX_CONTEXT_BYTES:
            return
        if previous := self._entries.pop(key, None):
            self._bytes -= previous[1]
        while self._entries and (
            len(self._entries) >= MAX_CONTEXTS or self._bytes + size > MAX_CONTEXT_BYTES
        ):
            _, (_, expired_size) = self._entries.popitem(last=False)
            self._bytes -= expired_size
        self._entries[key] = (context, size)
        self._bytes += size

    def _load_shared(self, key):
        if self.database is None:
            return
        with self._lock:
            if key in self._entries:
                return
            revision = self._revision
        with self.database.transaction() as connection:
            stored = row(connection, prepared_contexts, key)
        if not stored or stored["expires"] <= time.time():
            return
        try:
            context = json.loads(self.database.decrypt(stored["payload"]))
        except InvalidToken, json.JSONDecodeError:
            # Invalid optional metadata is never used to resolve an edit.
            return
        if not isinstance(context, dict) or context.get("resolver_version") != version(
            "stacksmith"
        ):
            return
        with self._lock:
            if revision == self._revision:
                self._store(key, context)
                self._shared_hits += 1

    def _save_shared(self, key, context):
        if (
            self.database is None
            or len(json.dumps(context).encode()) > MAX_CONTEXT_BYTES
        ):
            return
        with self.database.transaction() as connection:
            statement = (
                postgres_insert
                if connection.dialect.name == "postgresql"
                else sqlite_insert
            )(prepared_contexts).values(
                id=key,
                payload=self.database.encrypt(json.dumps(context)),
                expires=time.time() + 86400,
            )
            connection.execute(
                statement.on_conflict_do_update(
                    index_elements=["id"],
                    set_={
                        "payload": statement.excluded.payload,
                        "expires": statement.excluded.expires,
                    },
                )
            )
