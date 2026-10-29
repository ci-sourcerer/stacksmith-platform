import hashlib
import json
import re
from concurrent.futures import ThreadPoolExecutor
from functools import partial
from urllib.parse import quote, urlsplit

from ruamel.yaml import YAML


def prepare_inputs(request, context):
    """Resolve managed Git inputs to immutable revisions for one inspection.

    Args:
        request: Authorized request with fresh configuration access checks.
        context: Server-verified stack and environment context.

    Returns:
        The context with a private immutable-input manifest, or the original context.

    Raises:
        ComposerError: Current GitHub access or reference resolution fails.
    """
    if context is None:
        return context
    from .app import _github, _managed_layers

    client = _github(request)
    revisions = {}
    configs = {}
    cacheable = True
    previous = getattr(request.state, "inspection_layers", None)
    layers = (
        previous[1]
        if previous and previous[0] == context["repository"]
        else _managed_layers(request, context["repository"])
    )
    references = set()
    for reference, content, _ in layers:
        if match := _git_file(reference):
            references.add((f"https://github.com/{match[1]}/{match[2]}", match[4]))
        references.update(_git_sources(_catalog_configuration(content)))
    with ThreadPoolExecutor(max_workers=4) as executor:
        for reference, revision in zip(
            sorted(references),
            executor.map(partial(_resolve_reference, client), sorted(references)),
            strict=True,
        ):
            if revision is not None:
                revisions[f"{reference[0]}@{reference[1]}"] = revision
    for reference, content, _ in layers:
        if str(reference).startswith("git+"):
            match = _git_file(reference)
            if not match:
                cacheable = False
                continue
            commit = _revision(
                client, f"https://github.com/{match[1]}/{match[2]}", match[4], revisions
            )
            configs[str(reference)] = (
                f"git+https://github.com/{match[1]}/{match[2]}//{match[3]}@{commit}"
            )
        if any(marker in content for marker in ("{{", "{%", "${", "!env")):
            cacheable = False
        cacheable = (
            _dependencies(_catalog_configuration(content), client, revisions)
            and cacheable
        )
    manifest = {"configs": configs, "revisions": revisions, "cacheable": cacheable}
    manifest["digest"] = hashlib.sha256(
        json.dumps(manifest, sort_keys=True).encode()
    ).hexdigest()
    return {
        **context,
        "_input_manifest": manifest,
        **(
            {"_config_source": layers[0][1]}
            if len(layers) == 1 and not str(layers[0][0]).startswith("git+")
            else {}
        ),
    }


def _catalog_configuration(content):
    # Inspection reads provider declarations but never downloads provider binaries.
    # Their declarations remain covered by the configuration revision in the key.
    return {
        key: value
        for key, value in (YAML(typ="safe").load(content) or {}).items()
        if key != "provider_mappings"
    }


def _git_file(reference):
    return re.fullmatch(
        r"git\+https://github\.com/([^/]+)/([^/]+)//(.+?)@([^@]+)", str(reference)
    )


def _git_sources(value):
    if isinstance(value, dict):
        if value.get("source") == "git" and isinstance(value.get("data"), dict):
            yield value["data"].get("repo", ""), value["data"].get("ref", "HEAD")
        for key, item in value.items():
            if key != "inline":
                yield from _git_sources(item)
    elif isinstance(value, list):
        for item in value:
            yield from _git_sources(item)


def _resolve_reference(client, reference):
    return _revision(client, *reference, {})


def _revision(client, repo, ref, revisions):
    key = f"{repo}@{ref}"
    if key not in revisions:
        parts = urlsplit(repo)
        if (
            parts.scheme != "https"
            or parts.netloc != "github.com"
            or len(parts.path.strip("/").split("/")) != 2
        ):
            return None
        # Pinned commits still require current authorization before private cache reuse.
        revisions[key] = client._request(
            "GET",
            f"/repos/{parts.path.strip('/').removesuffix('.git')}/commits/{quote(ref, safe='')}",
        )["sha"]
    return revisions[key]


def _dependencies(value, client, revisions):
    if isinstance(value, list):
        return all(_dependencies(item, client, revisions) for item in value)
    if not isinstance(value, dict):
        # Nested configuration references need their own dependency graph; do not reuse them implicitly.
        return not (isinstance(value, str) and value.startswith("git+"))
    immutable = True
    if (
        isinstance(value.get("source"), str)
        and "data" in value
        and value["source"] not in {"git", "local", "inline", "registry"}
    ):
        immutable = False
    if value.get("source") == "git" and isinstance(value.get("data"), dict):
        immutable = bool(
            _revision(
                client,
                value["data"].get("repo", ""),
                value["data"].get("ref", "HEAD"),
                revisions,
            )
        )
    if value.get("source") == "registry":
        # A version alone does not prove an immutable download or current registry access.
        immutable = False
    return (
        all(
            _dependencies(item, client, revisions)
            for key, item in value.items()
            if key != "inline"
        )
        and immutable
    )


def rewrite_dependencies(value, revisions):
    """Pin discovered source references inside an isolated configuration copy.

    Args:
        value: Parsed configuration data in the sandbox.
        revisions: Server-resolved repository and reference mapping.

    Returns:
        None. The supplied parsed data is updated in place.
    """
    if isinstance(value, dict):
        if value.get("source") == "git" and isinstance(value.get("data"), dict):
            key = f"{value['data'].get('repo', '')}@{value['data'].get('ref', 'HEAD')}"
            if key in revisions:
                value["data"]["ref"] = revisions[key]
        for item in value.values():
            rewrite_dependencies(item, revisions)
    elif isinstance(value, list):
        for item in value:
            rewrite_dependencies(item, revisions)
