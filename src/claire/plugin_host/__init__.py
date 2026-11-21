"""Plugin host of the core: discovery, states, loading, and records of plugins.

Design: ``docs/design/INGEST_ADAPTER_PLUGIN_DESIGN.md``. A plugin imports only ``claire.plugin_sdk``; the core never imports a plugin module (PL-D10). This package is the only core code that imports plugin folders, and it does so only for plugins in the state ``enabled`` (PL-D8).
"""

from __future__ import annotations

# Plugin API levels that this core supports (§6.1 rule 4). No "nearest level".
PLUGIN_API_LEVELS: tuple[int, ...] = (1,)

# Manifest format versions that this core reads (§6.1 rule 10).
MANIFEST_VERSIONS: tuple[int, ...] = (1,)

# Plugin types and their type folders. The type folder has the name of the type (PL-D3).
TYPE_INGEST_ADAPTER = "ingest_adapter"
TYPE_AGENT_CLIENT = "agent_client"
PLUGIN_TYPES: tuple[str, ...] = (TYPE_INGEST_ADAPTER, TYPE_AGENT_CLIENT)

# The publisher of project plugins (§6.1 rule 1).
PROJECT_PUBLISHER = "claire"

# Origins (§6.5): the project plugin folder or the plugin folder of the server administrator.
ORIGIN_PROJECT = "project"
ORIGIN_PLUGIN_FOLDER = "plugin_folder"

# States (§8.1), in the order of the checks.
STATE_INVALID = "invalid"
STATE_UNLOCKED = "unlocked"
STATE_INCOMPATIBLE = "incompatible"
STATE_MISSING_REQUIREMENT = "missing_requirement"
STATE_NEW = "new"
STATE_DISABLED = "disabled"
STATE_CHANGED = "changed"
STATE_OVERLAP = "overlap"
STATE_ENABLED = "enabled"
STATE_FAILED = "failed"
STATE_REMOVED = "removed"

STATES: tuple[str, ...] = (
    STATE_INVALID,
    STATE_UNLOCKED,
    STATE_INCOMPATIBLE,
    STATE_MISSING_REQUIREMENT,
    STATE_NEW,
    STATE_DISABLED,
    STATE_CHANGED,
    STATE_OVERLAP,
    STATE_ENABLED,
    STATE_FAILED,
    STATE_REMOVED,
)

from .files import PluginFileError

__all__ = [
    "MANIFEST_VERSIONS",
    "ORIGIN_PLUGIN_FOLDER",
    "ORIGIN_PROJECT",
    "PLUGIN_API_LEVELS",
    "PLUGIN_TYPES",
    "PROJECT_PUBLISHER",
    "PluginFileError",
    "STATE_CHANGED",
    "STATE_DISABLED",
    "STATE_ENABLED",
    "STATE_FAILED",
    "STATE_INCOMPATIBLE",
    "STATE_INVALID",
    "STATE_MISSING_REQUIREMENT",
    "STATE_NEW",
    "STATE_OVERLAP",
    "STATE_REMOVED",
    "STATE_UNLOCKED",
    "STATES",
    "TYPE_AGENT_CLIENT",
    "TYPE_INGEST_ADAPTER",
]
