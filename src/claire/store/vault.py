"""Obsidian vault subsystem (deprecated).

This module is deprecated. The Obsidian Vault subsystem and markdown export
logic have been removed.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any


def _entity_filename(ent: Any) -> str:
    """Return a filename for the entity. Retained for legacy test compatibility."""
    return f"{ent.id}.md"


def export_entity(*args: Any, **kwargs: Any) -> Path | None:
    """Deprecated: no-op."""
    return None


def export_entities(*args: Any, **kwargs: Any) -> list[Path]:
    """Deprecated: no-op."""
    return []
