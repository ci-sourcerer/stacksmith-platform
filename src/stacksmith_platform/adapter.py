import json
import logging
import os
import shutil
from pathlib import Path
from tempfile import TemporaryDirectory
from threading import BoundedSemaphore
from typing import Any
from urllib.parse import parse_qs, quote, urlparse

from .certificates import ca_environment
from .configuration import config_arguments
from .errors import ComposerError
from .inspection_results import _raise_inspection_failure, _run_inspection
from .settings import Settings

LOGGER = logging.getLogger(__name__)


class StacksmithAdapter:
    """Call Stacksmith in an isolated runtime or through a separate local CLI.

    Args:
        settings: Administrator-owned command and managed configuration settings.
        context: Server-selected repository and environment inspection context.
        database: Process-owned query pool shared with the API.
    """

    def __init__(self, settings: Settings, context: dict | None = None, database=None):
        self.settings = settings
        self.context = context
        self.database = database
        self.slots = BoundedSemaphore(2)
        self.command = shutil.which(settings.stacksmith_command)
        if self.command:
            self.command = str(Path(self.command).resolve())

    def _run(
        self,
        args: list[str],
        source: str | None = None,
        github_token: str | None = None,
        authoring_request: dict[str, Any] | None = None,
    ) -> str:
        if self.settings.execution_enabled:
            return self._execute(args, source, github_token, authoring_request)
        if not self.slots.acquire(timeout=1):
            raise ComposerError(
                "Stacksmith is busy inspecting another draft. Retry shortly.", 503
            )
        try:
            return self._execute(args, source, github_token, authoring_request)
        finally:
            self.slots.release()

    def _execute(
        self,
        args: list[str],
        source: str | None = None,
        github_token: str | None = None,
        authoring_request: dict[str, Any] | None = None,
    ) -> str:
        if self.settings.execution_enabled:
            from .inspection import InspectionQueue

            return InspectionQueue(self.settings, database=self.database).execute(
                args,
                source,
                github_token,
                authoring_request,
                **({"context": self.context} if self.context else {}),
            )
        if not self.command:
            raise ComposerError(
                "Stacksmith is unavailable. Set STACKSMITH_PLATFORM_STACKSMITH_COMMAND "
                "to your Stacksmith executable and restart the API.",
                503,
            )
        with TemporaryDirectory(prefix="stacksmith-platform-") as directory:
            if source is not None:
                Path(directory, "stack.yaml").write_text(source)
                args = [*args, "--stack", str(Path(directory, "stack.yaml"))]
            result = _run_inspection(
                [self.command, *args]
                if authoring_request is not None
                else [
                    self.command,
                    *args,
                    *config_arguments(self.settings.config),
                    "--build-dir",
                    str(self.settings.stacksmith_build_dir),
                ],
                input=json.dumps(authoring_request)
                if authoring_request is not None
                else None,
                stage=" ".join(args[:2]),
                authoring=authoring_request is not None,
                redactions=[github_token],
                cwd=directory,
                env=_subprocess_environment(github_token),
                timeout=self.settings.command_timeout,
            )
        _raise_inspection_failure(result)
        if source is not None and args[:2] == ["info", "explain"]:
            return result["inspection_output"].replace(
                str(Path(directory, "stack.yaml").resolve()), "Current draft"
            )
        return result["inspection_output"]

    def _json(
        self,
        args: list[str],
        source: str | None = None,
        github_token: str | None = None,
    ) -> dict[str, Any]:
        try:
            return json.loads(
                self._run([*args, "--format", "json"], source, github_token)
            )
        except json.JSONDecodeError as exc:
            raise ComposerError(
                "Stacksmith returned an incompatible inspection response.", 502
            ) from exc

    def author(
        self, source: str, edits: list[dict[str, Any]], path: str
    ) -> dict[str, Any]:
        """Propose authored edits through the pinned Stacksmith library.

        Args:
            source: Original authored stack source.
            edits: Ordered set or remove edits, including Composer layout metadata.
            path: Repository filename selecting the serialization format.

        Returns:
            Version 1 document-validated source, diff, and change indicator.

        Raises:
            ComposerError: The document or edits are invalid.
        """
        from .editing_contexts import author_source

        return author_source(source, edits, path)

    def catalog(self, github_token: str | None = None) -> dict[str, Any]:
        """Return module inspection metadata from the configured Stacksmith CLI.

        Returns:
            Component types keyed by their managed names.

        Raises:
            ComposerError: If the CLI cannot inspect the trusted configuration.
        """
        return _relative_module_sources(
            self._json(["info", "modules-and-policies"], github_token=github_token),
            self.settings.module_source_prefix,
        )

    def review(
        self,
        source: str,
        edits: list[dict[str, Any]],
        path: str,
        github_token: str | None = None,
    ) -> dict[str, Any]:
        """Author and inspect a draft in one isolated execution.

        Args:
            source: Original authored document.
            edits: Ordered document edits, including layout metadata.
            path: Repository filename selecting the document format.
            github_token: Current session credential for approved sources.

        Returns:
            Authored source, catalog, associations, and effective stack document.

        Raises:
            ComposerError: Authoring or inspection fails.
        """
        if not self.settings.execution_enabled:
            proposal = self.author(source, edits, path)
            return {
                "source": proposal["source"],
                "catalog": self.catalog(github_token),
                "associations": self.associations(proposal["source"], github_token),
            }
        result = json.loads(
            self._run(
                ["review"],
                github_token=github_token,
                authoring_request={
                    "version": 1,
                    "source": source,
                    "edits": edits,
                    "path": path,
                },
            )
        )
        LOGGER.info("Stacksmith review stage timings: %s", result.pop("timings", {}))
        result["catalog"] = _relative_module_sources(
            result["catalog"], self.settings.module_source_prefix
        )
        return result

    def associations(
        self, source: str, github_token: str | None = None
    ) -> dict[str, Any]:
        """Resolve managed associations for an authored stack in a temporary workspace.

        Args:
            source: Stack source with supported quoted Jinja references.

        Returns:
            Stacksmith's association provenance response.

        Raises:
            ComposerError: If the stack cannot be resolved.
        """
        return self._json(["info", "associations"], source, github_token)

    def resolution(
        self, source: str, github_token: str | None = None, query: str | None = None
    ) -> dict[str, Any]:
        """Inspect effective configuration and provenance with values visible.

        Args:
            source: The current authored stack source.
            github_token: Session credential for approved private sources.
            query: Optional dotted configuration address to explain.

        Returns:
            Effective documents and ordered events, or a queried value and events.

        Raises:
            ComposerError: If Stacksmith cannot resolve the draft or address.
        """
        return self._json(
            ["info", "explain", *([query] if query else []), "--show-values"],
            source,
            github_token,
        )


def _subprocess_environment(github_token: str | None) -> dict[str, str]:
    environment = {
        key: value
        for key, value in os.environ.items()
        if key in {"PATH", "HOME", "TMPDIR", "LANG", "SYSTEMROOT"}
    }
    environment.update(ca_environment())
    if github_token:
        environment["STACKSMITH_GIT_TOKEN"] = github_token
    return environment


def _relative_module_sources(
    catalog: dict[str, Any], prefix: Path | None
) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for name, value in catalog.items():
        if not isinstance(value, dict) or not isinstance(
            value.get("module_source"), str
        ):
            result[name] = value
            continue
        definition = {**value}
        if source_url := _module_source_url(
            value["module_source"], value.get("module_version")
        ):
            definition["module_source_url"] = source_url
        if prefix is not None and not source_url:
            try:
                definition["module_path"] = (
                    Path(value["module_source"])
                    .resolve()
                    .relative_to(prefix)
                    .as_posix()
                )
                definition["module_source"] = definition["module_path"]
            except ValueError:
                pass
        result[name] = definition
    return result


def _module_source_url(source: str, version: Any = None) -> str | None:
    rendered = source.removeprefix("git::")
    parsed = urlparse(rendered)
    if parsed.scheme != "https" or parsed.hostname != "github.com":
        return None
    repository_path, separator, module_path = parsed.path.removesuffix(
        ".git"
    ).partition(".git//")
    if not separator:
        repository_path, separator, module_path = parsed.path.partition("//")
    repository = repository_path.removesuffix(".git").strip("/")
    if len(repository.split("/")) != 2:
        return None
    url = f"https://github.com/{repository}"
    has_subpath_separator = bool(separator)
    query_ref = parse_qs(parsed.query).get("ref", [None])[0]
    ref = query_ref if query_ref else version
    if ref:
        url += f"/tree/{quote(str(ref), safe='')}"
    elif has_subpath_separator:
        url += "/tree/HEAD"
    # Without an explicit ref or module subpath, link to the repository root.
    if has_subpath_separator and module_path:
        url += f"/{'/'.join(quote(part, safe='') for part in module_path.split('/'))}"
    return url
