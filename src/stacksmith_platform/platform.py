import re
from pathlib import Path
from typing import Any
from urllib.parse import quote, urlparse

from ruamel.yaml import YAML

_SENSITIVE_KEY = re.compile(
    r"(?:password|secret|token|credential|private.?key|access.?key)", re.IGNORECASE
)


def platform_summary(source: Path | str) -> dict[str, Any]:
    """Expose authored platform metadata and readable source previews.

    Args:
        source: Administrator-selected local config path or YAML content.

    Returns:
        An allowlisted summary of the authored configuration, not effective values.
    """
    config = YAML(typ="safe").load(
        source.read_text() if isinstance(source, Path) else source
    )
    return {
        "description": config.get(
            "description", "Platform-managed Stacksmith contract"
        ),
        "source": source.name if isinstance(source, Path) else "GitHub managed config",
        "scope": "Authored configuration; layered and runtime values are not resolved.",
        "backend": {
            "kind": next(iter(config.get("backend", {})), "unknown"),
            "type": config.get("backend", {}).get("data", {}).get("type"),
            "path": config.get("backend", {}).get("data", {}).get("path"),
            "source": _source(
                "data",
                "Static configuration data",
                configuration=_public_configuration(
                    config.get("backend", {}).get("data", {})
                ),
            ),
        },
        "tools": {
            name: _tool_summary(spec) for name, spec in config.get("tools", {}).items()
        },
        "providers": [
            _provider_summary(name, spec)
            for name, spec in config.get("provider_mappings", {}).items()
        ],
        "components": [
            {
                "name": name,
                "description": spec.get("description", ""),
                "tags": spec.get("tags", []),
                "source": _module_source(spec.get("source", {})),
                "inputs": [
                    {
                        "name": input_name,
                        "description": input_spec.get("description", ""),
                    }
                    for input_name, input_spec in spec.get("properties", {}).items()
                ],
                "outputs": [
                    {
                        "name": output_name,
                        "description": output_spec.get("description", ""),
                    }
                    for output_name, output_spec in spec.get("outputs", {}).items()
                ],
            }
            for name, spec in config.get("module_mappings", {}).items()
        ],
        "policies": [
            {
                "name": name,
                "description": spec.get("description", ""),
                "kind": kind,
                "source": _generated_source(
                    spec.get("rule", {}) if kind == "plan_validations" else spec,
                    config_dir=(
                        source.resolve().parent if isinstance(source, Path) else None
                    ),
                ),
            }
            for kind in ("var_validations", "plan_validations")
            for name, spec in config.get(kind, {}).items()
        ],
        "associations": [
            {
                "name": name,
                "description": spec.get("description", ""),
                "source": _source(
                    "data",
                    "Declarative platform configuration",
                    configuration=_public_configuration(spec),
                ),
            }
            for name, spec in config.get("associations", {}).items()
        ],
        "required_input_sets": config.get("required_module_input_sets", []),
        "has_default_mapping": bool(config.get("default_module_mapping")),
    }


def _provider_summary(name: str, spec: dict[str, Any]) -> dict[str, Any]:
    data = spec.get("source", {}).get("data", {})
    return {
        "name": name,
        "address": data.get("address", name),
        "version": data.get("version"),
        "source": _source(
            "registry",
            "OpenTofu Registry",
            _provider_registry_url(data.get("address", name), data.get("version")),
        ),
        "instances": [
            {
                "name": instance_name,
                "description": instance.get("description", ""),
                "source": _generated_source(instance.get("config", {})),
            }
            for instance_name, instance in spec.get("instances", {}).items()
        ],
    }


def _tool_summary(spec: dict[str, Any]) -> dict[str, Any]:
    source = _source("data", "Managed tool configuration")
    source["configuration"] = _public_configuration(spec)
    return {"version": spec.get("version"), "source": source}


def _generated_source(
    spec: dict[str, Any],
    *,
    include_configuration: bool = True,
    config_dir: Path | None = None,
) -> dict[str, Any]:
    if "inline" in spec:
        source = _source("inline", "Inline Python")
        source["content"] = spec["inline"]
        return source
    if "script" in spec:
        return _script_source(spec["script"], config_dir)
    if "data" in spec:
        source = _source("data", "Static configuration data")
        if include_configuration:
            source["configuration"] = _public_configuration(spec["data"])
        return source
    return _source("unknown", "Source not declared")


def _script_source(
    reference: dict[str, Any], config_dir: Path | None = None
) -> dict[str, Any]:
    data = reference.get("data", {})
    match reference.get("source"):
        case "local":
            source = _source("script", "Local Python script", detail=data.get("path"))
            if config_dir is not None and isinstance(data.get("path"), str):
                source["content"] = _read_local_script(config_dir, data["path"])
            return source
        case "git":
            return _source(
                "script",
                "Python script from GitHub",
                _github_url(data.get("repo"), data.get("ref"), data.get("path")),
                _git_detail(data),
            )
        case "http":
            return _source(
                "script",
                "Remote Python script",
                _safe_http_url(data.get("url")),
                data.get("url"),
            )
        case _:
            return _source("script", "Python script")


def _module_source(reference: dict[str, Any]) -> dict[str, str | None]:
    data = reference.get("data", {})
    match reference.get("source"):
        case "local":
            return _source("local", "Local OpenTofu module", detail=data.get("path"))
        case "git":
            return _source(
                "git",
                "GitHub OpenTofu module",
                _github_url(data.get("repo"), data.get("ref"), data.get("path")),
                _git_detail(data),
            )
        case "registry":
            return _source(
                "registry",
                "OpenTofu Registry module",
                detail=" ".join(
                    value
                    for value in (data.get("address"), data.get("version"))
                    if value
                ),
            )
        case _:
            return _source("unknown", "Module source not declared")


def _source(
    kind: str,
    label: str,
    url: str | None = None,
    detail: str | None = None,
    configuration: Any = None,
) -> dict[str, Any]:
    source = {"kind": kind, "label": label, "detail": detail, "url": url}
    if configuration is not None:
        source["configuration"] = configuration
    return source


def _read_local_script(config_dir: Path, relative_path: str) -> str | None:
    script_path = (config_dir / relative_path).resolve()
    if not script_path.is_relative_to(config_dir) or not script_path.is_file():
        return None
    try:
        if script_path.stat().st_size > 65536:
            return None
        return script_path.read_text()
    except OSError, UnicodeDecodeError:
        return None


def _public_configuration(value: Any) -> Any:
    if isinstance(value, dict):
        return {
            key: (
                "[redacted]"
                if _SENSITIVE_KEY.search(str(key))
                else _public_configuration(item)
            )
            for key, item in value.items()
        }
    if isinstance(value, list):
        return [_public_configuration(item) for item in value]
    return value


def _git_detail(data: dict[str, Any]) -> str:
    repo = str(data.get("repo", "")).removesuffix(".git")
    parsed = urlparse(repo)
    repository = parsed.path.strip("/") if parsed.hostname == "github.com" else repo
    return " · ".join(
        value for value in (repository, data.get("path"), data.get("ref")) if value
    )


def _github_url(repo: Any, ref: Any = None, path: Any = None) -> str | None:
    if not isinstance(repo, str):
        return None
    parsed = urlparse(repo.removesuffix(".git"))
    if parsed.scheme != "https" or parsed.hostname != "github.com":
        return None
    url = f"https://github.com/{parsed.path.strip('/')}"
    if ref:
        url += f"/tree/{quote(str(ref), safe='')}"
        if path:
            url += f"/{'/'.join(quote(part, safe='') for part in str(path).split('/'))}"
    return url


def _safe_http_url(value: Any) -> str | None:
    if not isinstance(value, str):
        return None
    return value if urlparse(value).scheme in {"http", "https"} else None


def _provider_registry_url(address: Any, version: Any) -> str | None:
    if not isinstance(address, str):
        return None
    parts = address.split("/")
    if len(parts) == 2:
        namespace, provider = parts
    elif len(parts) == 3 and parts[0] == "registry.opentofu.org":
        _, namespace, provider = parts
    else:
        return None
    exact_version = re.fullmatch(r"\s*=?\s*(\d+\.\d+\.\d+)\s*", str(version or ""))
    release = f"v{exact_version.group(1)}" if exact_version else "latest"
    return f"https://search.opentofu.org/provider/{namespace}/{provider}/{release}"
