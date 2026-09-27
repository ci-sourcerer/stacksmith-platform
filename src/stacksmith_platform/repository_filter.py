"""Repository discovery filter settings and matching."""

from __future__ import annotations

from .database import configuration, row

FILTER_ID = "platform:repository-filter"
DEFAULT_FILTER = {
    "properties": {"stacksmith": "stack"},
    "topics": ["stacksmith-stack"],
}


def get_filter(connection):
    """Return configured repository discovery filter or its defaults."""
    stored = row(connection, configuration, FILTER_ID)
    return stored["policy"] if stored else DEFAULT_FILTER


def matches(repository, filter_settings):
    """Return whether a repository matches any configured property or topic."""
    properties = repository.get("custom_properties") or {}
    if any(
        str(properties.get(name, "")).casefold() == str(value).casefold()
        for name, value in filter_settings["properties"].items()
    ):
        return True
    return bool(set(repository.get("topics") or ()) & set(filter_settings["topics"]))


def filtered(repositories, filter_settings):
    """Return repositories matching configured properties or topics."""
    return [item for item in repositories if matches(item, filter_settings)]
