"""Web application entrypoint that wires renderer ports before creating the app."""

from __future__ import annotations

from typing import Any

from ..bootstrap import configure

__all__ = ["create_app"]


def create_app(*args: Any, **kwargs: Any) -> Any:
    """Configure renderer ports and delegate to ``claire.api.server.create_app``."""
    configure()
    from claire.api.server import create_app as claire_create_app

    return claire_create_app(*args, **kwargs)
