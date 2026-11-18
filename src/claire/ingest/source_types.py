"""Source types that the core knows (INGEST_ADAPTER_PLUGIN_DESIGN.md §7.5).

Core code branches only on these values. A plugin that gives audio or video uses ``video``. Any other value of a plugin is opaque to the core.

``youtube`` is the one frozen value of a plugin: stored documents have it, so the core treats it as ``video`` and the audit keeps its content hash rule (body only). It is in this one place.
"""

from __future__ import annotations

CORE_SOURCE_TYPES: tuple[str, ...] = ("web", "video", "pdf", "odt", "text", "file", "custom")

# Stored documents of the YouTube plugin (claire.youtube). Frozen: no new value joins this list.
LEGACY_MEDIA_SOURCE_TYPES: tuple[str, ...] = ("youtube",)

MEDIA_SOURCE_TYPES: tuple[str, ...] = ("video", *LEGACY_MEDIA_SOURCE_TYPES)

# Source types whose content hash covers the body only (store.db audit); other types hash the title and the body.
BODY_ONLY_HASH_SOURCE_TYPES: tuple[str, ...] = (*LEGACY_MEDIA_SOURCE_TYPES, "text")


def is_media_source_type(source_type: str | None) -> bool:
    return (source_type or "") in MEDIA_SOURCE_TYPES
