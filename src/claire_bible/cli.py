"""CLI entrypoint that wires renderer ports before calling Claire CLI."""

from __future__ import annotations

from .bootstrap import configure

__all__ = ["main"]


def main(argv: list[str] | None = None) -> int:
    """Configure renderer ports and delegate to ``claire.cli.main``."""
    configure()
    from claire.cli import main as claire_main

    return claire_main(argv)


if __name__ == "__main__":
    raise SystemExit(main())
