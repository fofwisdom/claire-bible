"""Pure rendering and document UI engine for Claire Bible."""

from __future__ import annotations

from pathlib import Path

STATIC_DIR: Path = Path(__file__).resolve().parent / "static"
TEMPLATES_DIR: Path = Path(__file__).resolve().parent / "templates"

__all__ = ["STATIC_DIR", "TEMPLATES_DIR"]
