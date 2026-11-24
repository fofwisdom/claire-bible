"""Opaque keyset cursor helpers and paginated wire payload builder (AR4)."""

from __future__ import annotations

import base64
import json
import math
from typing import Any


def encode_cursor(payload: dict[str, Any]) -> str:
    """Encode a JSON cursor object into an opaque URL-safe base64 token."""
    raw = json.dumps(payload, separators=(",", ":"), sort_keys=True, ensure_ascii=False).encode("utf-8")
    return base64.urlsafe_b64encode(raw).decode("ascii").rstrip("=")


def decode_cursor(cursor: str) -> dict[str, Any]:
    """Decode and validate an opaque base64url cursor token."""
    if not isinstance(cursor, str) or not cursor or not cursor.isascii():
        raise ValueError("invalid_cursor")
    padding = "=" * (-len(cursor) % 4)
    try:
        raw = base64.urlsafe_b64decode((cursor + padding).encode("ascii"))
        parsed = json.loads(raw.decode("utf-8"))
    except (ValueError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ValueError("invalid_cursor") from exc
    if not isinstance(parsed, dict):
        raise ValueError("invalid_cursor")
    return parsed


def encode_number_cursor(number: int) -> str:
    """Encode a numeric keyset cursor (`{"n": <int>}`)."""
    if isinstance(number, bool) or not isinstance(number, int) or number < 0:
        raise ValueError("invalid_cursor")
    return encode_cursor({"n": number})


def decode_number_cursor(cursor: str) -> int:
    """Decode a numeric keyset cursor back into a non-negative integer."""
    data = decode_cursor(cursor)
    if set(data.keys()) != {"n"}:
        raise ValueError("invalid_cursor")
    val = data["n"]
    if isinstance(val, bool) or not isinstance(val, int) or val < 0:
        raise ValueError("invalid_cursor")
    return val


def encode_document_cursor(fetched_at: float | None, document_id: str) -> str:
    """Encode a document list keyset cursor (`{"ts": float|None, "id": str}`)."""
    if not isinstance(document_id, str) or not document_id:
        raise ValueError("invalid_cursor")
    ts_val: float | None = None
    if fetched_at is not None:
        ts_val = float(fetched_at)
        if not math.isfinite(ts_val):
            raise ValueError("invalid_cursor")
    return encode_cursor({"ts": ts_val, "id": document_id})


def decode_document_cursor(cursor: str) -> tuple[float | None, str]:
    """Decode a document list keyset cursor into `(fetched_at, document_id)`."""
    data = decode_cursor(cursor)
    if set(data.keys()) != {"ts", "id"}:
        raise ValueError("invalid_cursor")
    ts_raw = data["ts"]
    doc_id = data["id"]
    if not isinstance(doc_id, str) or not doc_id:
        raise ValueError("invalid_cursor")
    if ts_raw is None:
        return None, doc_id
    if isinstance(ts_raw, bool) or not isinstance(ts_raw, (int, float)):
        raise ValueError("invalid_cursor")
    ts_val = float(ts_raw)
    if not math.isfinite(ts_val):
        raise ValueError("invalid_cursor")
    return ts_val, doc_id


def parse_limit(
    raw: str | None,
    *,
    default: int = 50,
    minimum: int = 1,
    maximum: int = 100,
) -> int:
    """Parse and validate a positive integer `limit` query parameter."""
    if raw is None or raw == "":
        return default
    text = raw.strip()
    if not text or not text.isascii() or not text.isdigit():
        raise ValueError("invalid_limit")
    value = int(text)
    if not minimum <= value <= maximum:
        raise ValueError("invalid_limit")
    return value


def paginated_payload(
    items: list[Any],
    *,
    next_cursor: str | None,
    total: int | None = None,
) -> dict[str, Any]:
    """Build the standard v1 paginated response body."""
    payload: dict[str, Any] = {
        "items": items,
        "next_cursor": next_cursor,
    }
    if total is not None:
        payload["total"] = int(total)
    return payload
