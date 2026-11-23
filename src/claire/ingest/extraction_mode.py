"""The extraction mode of one ingest (AGENT_DRIVEN_EXTRACTION_AND_EMBEDDING_DESIGN.md decision AED4-rev).

``provider``: the server API extracts the source at once. ``client``: the server keeps the full source in the queue (``documents.extraction_status='pending'``) until an agent client claims, extracts, and submits it. Each ingest request selects one mode; the account has no stored mode.
"""

from __future__ import annotations

from typing import Any, Final, Literal

ExtractionMode = Literal["provider", "client"]
EXTRACTION_MODES: Final[tuple[str, ...]] = ("provider", "client")
# The provider names of describe_ingest_effort_options that tell that the account has no API.
_NO_API_PROVIDERS: Final = frozenset({"", "none", "unavailable"})


def parse_extraction_mode(value: Any) -> ExtractionMode:
    """Return ``value`` when it is an extraction mode. Raise ``ValueError('invalid_extraction_mode')`` for all other values, also for a missing value."""
    if isinstance(value, str) and value in EXTRACTION_MODES:
        return value  # type: ignore[return-value]
    raise ValueError("invalid_extraction_mode")


def available_extraction_modes(settings: Any, *, ctx: Any = None, provider_error: Exception | None = None) -> list[str]:
    """The modes that the account can select, in the display order. With an API (a key of the account, the server tier, or the server providers): ``["provider", "client"]``. Without an API: ``["client"]``. The first mode is the default mode of the account.

    A registered API that the job cannot use (``provider_error``: a rejected key, a key refused in a worker loop process, an account that is not active) stays the API of the account: the API mode fails with the policy error and never changes to the queue (C2).
    """
    from ..accounts.provider_policy import describe_ingest_effort_options

    if provider_error is not None:
        return ["provider", "client"]
    info = describe_ingest_effort_options(settings, ctx=ctx)
    if str(info.get("provider") or "") in _NO_API_PROVIDERS:
        return ["client"]
    return ["provider", "client"]
