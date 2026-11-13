"""Store functions of the federation client (FEDERATION_REMOTE_READING_DESIGN.md Sections 5.1, 7.1, 7.3, and 10, unit FP4-2).

Two files (DATA_STORE_LAYOUT_DESIGN.md, RD27):

- ``client.db`` (``store.client_db``): ``federation_following``, ``federation_account_settings``, ``federation_client_outbox``. The functions with ``conn`` for these tables take a ``client.db`` connection.
- ``claire.db``: the reading settings of the instance, one JSON value in ``instance_settings`` (key ``federation_reading``). ``get_reading_settings`` and ``set_reading_settings`` take a ``claire.db`` connection.

- A follow starts as ``pending`` with the handle or IRI that the owner gave (``request``). ``actor_iri`` is ``None`` until the sidecar resolves it (FP4-3).
- The retention of an account is the lower of the account value and the instance value (Section 10 rule 1). An account value above the instance value is refused when the owner sets it; a later decrease of the instance value does not change the account value.

No function in this module commits, except ``set_reading_settings``. The caller owns the transaction.
"""

from __future__ import annotations

import json
import sqlite3
import time
from dataclasses import dataclass
from typing import Any, Literal

__all__ = [
    "FOLLOWING_STATES",
    "READING_DEFAULTS",
    "READING_SETTING_KEY",
    "Following",
    "ReadingSettingsError",
    "add_following",
    "count_following",
    "effective_retention_days",
    "get_account_retention",
    "get_following",
    "get_reading_settings",
    "list_following",
    "add_client_task",
    "remove_following",
    "set_account_retention",
    "set_following_state",
    "set_reading_settings",
    "validate_reading_update",
]

FollowingState = Literal["pending", "accepted", "rejected"]
FOLLOWING_STATES: tuple[str, ...] = ("pending", "accepted", "rejected")
READING_SETTING_KEY = "federation_reading"
_MIB = 1024 * 1024

# Section 10: default and range of each instance value. Each value is an int, except ``context_fetch``.
READING_DEFAULTS: dict[str, Any] = {
    "retention_days": 90,
    "following_limit": 1000,
    "media_image_max_bytes": 20 * _MIB,
    "media_av_max_bytes": 100 * _MIB,
    "media_evict_days": 30,
    "context_fetch": "on",
    "context_depth": 5,
    "context_max_items": 100,
    "context_refetch_minutes": 15,
    "note_max_chars": 5000,
    "mention_rate_per_hour": 100,
    "auto_reply_hours": 24,
}
_READING_RANGES: dict[str, tuple[int, int]] = {
    "retention_days": (1, 3650),
    "following_limit": (1, 100000),
    "media_image_max_bytes": (1 * _MIB, 100 * _MIB),
    "media_av_max_bytes": (1 * _MIB, 1024 * _MIB),
    "media_evict_days": (1, 365),
    "context_depth": (0, 20),
    "context_max_items": (0, 1000),
    "context_refetch_minutes": (1, 1440),
    "note_max_chars": (500, 100000),
    "mention_rate_per_hour": (1, 10000),
    "auto_reply_hours": (1, 720),
}
_CONTEXT_FETCH_VALUES = ("on", "off")
_FOLLOWING_COLUMNS = "id, account_id, request, actor_iri, actor_type, inbox_iri, shared_inbox_iri, display_name, state, created_at, updated_at"
_MAX_REQUEST_LENGTH = 2048


class ReadingSettingsError(ValueError):
    """A value of the reading settings is not valid. ``field`` names the first value that is not valid."""

    def __init__(self, field: str, message: str):
        super().__init__(message)
        self.field = field


@dataclass(frozen=True, slots=True)
class Following:
    id: int
    account_id: int
    request: str
    actor_iri: str | None
    actor_type: str | None
    inbox_iri: str | None
    shared_inbox_iri: str | None
    display_name: str | None
    state: FollowingState
    created_at: float
    updated_at: float


def _following(row: sqlite3.Row | tuple) -> Following:
    values = tuple(row)
    return Following(int(values[0]), int(values[1]), str(values[2]), *values[3:8], values[8], float(values[9]), float(values[10]))


# --- Follows ---------------------------------------------------------------


def add_following(conn: sqlite3.Connection, *, account_id: int, request: str, now: float) -> tuple[Following, bool]:
    """Add a ``pending`` follow for the handle or IRI ``request``. Return the follow and ``True`` when it is new. A follow of the same account with the same request that is not resolved yet is returned again (``False``)."""
    text = (request or "").strip()
    if not text or len(text) > _MAX_REQUEST_LENGTH or any(ord(c) < 32 for c in text):
        raise ValueError("request must be a handle or an IRI without control characters")
    row = conn.execute(f"SELECT {_FOLLOWING_COLUMNS} FROM federation_following WHERE account_id=? AND request=? AND actor_iri IS NULL", (account_id, text)).fetchone()
    if row is not None:
        return _following(row), False
    cur = conn.execute("INSERT INTO federation_following (account_id, request, state, created_at, updated_at) VALUES (?, ?, 'pending', ?, ?)", (account_id, text, now, now))
    found = get_following(conn, account_id, int(cur.lastrowid))
    assert found is not None
    return found, True


def get_following(conn: sqlite3.Connection, account_id: int, following_id: int) -> Following | None:
    row = conn.execute(f"SELECT {_FOLLOWING_COLUMNS} FROM federation_following WHERE id=? AND account_id=?", (following_id, account_id)).fetchone()
    return None if row is None else _following(row)


def count_following(conn: sqlite3.Connection, account_id: int) -> int:
    """All follows of the account that are not ``rejected``. The following limit (Section 10 rule 3) counts these."""
    return int(conn.execute("SELECT COUNT(*) FROM federation_following WHERE account_id=? AND state!='rejected'", (account_id,)).fetchone()[0])


def list_following(conn: sqlite3.Connection, account_id: int, *, limit: int, max_id: int | None = None) -> list[Following]:
    """Follows of the account, newest first. ``max_id`` gives the follows with a lower ID (cursor)."""
    if limit < 1:
        raise ValueError("limit must be more than 0")
    if max_id is None:
        rows = conn.execute(f"SELECT {_FOLLOWING_COLUMNS} FROM federation_following WHERE account_id=? ORDER BY id DESC LIMIT ?", (account_id, limit)).fetchall()
    else:
        rows = conn.execute(f"SELECT {_FOLLOWING_COLUMNS} FROM federation_following WHERE account_id=? AND id<? ORDER BY id DESC LIMIT ?", (account_id, max_id, limit)).fetchall()
    return [_following(r) for r in rows]


def set_following_state(conn: sqlite3.Connection, following_id: int, state: FollowingState, *, now: float) -> bool:
    if state not in FOLLOWING_STATES:
        raise ValueError(f"unknown following state: {state!r}")
    return conn.execute("UPDATE federation_following SET state=?, updated_at=? WHERE id=?", (state, now, following_id)).rowcount == 1


def remove_following(conn: sqlite3.Connection, account_id: int, following_id: int) -> Following | None:
    """Delete the follow and return it, or ``None`` when the account has no such follow. The caller adds the ``undo_follow`` task (FP4-3). The timeline items of the follow stay (Section 5.1)."""
    found = get_following(conn, account_id, following_id)
    if found is not None:
        conn.execute("DELETE FROM federation_following WHERE id=?", (following_id,))
    return found


# --- Reading settings of the instance (Section 10) -------------------------


def validate_reading_update(body: Any) -> dict[str, Any]:
    """Check a partial update of the reading settings. Each key must be known, and each value in its range (C1). Return the checked values."""
    if not isinstance(body, dict) or not body:
        raise ReadingSettingsError("", "The body must be an object with one or more settings")
    checked: dict[str, Any] = {}
    for key, value in body.items():
        if key not in READING_DEFAULTS:
            raise ReadingSettingsError(key, f"Unknown setting: {key}")
        if key == "context_fetch":
            if value not in _CONTEXT_FETCH_VALUES:
                raise ReadingSettingsError(key, "context_fetch must be on or off")
            checked[key] = value
            continue
        low, high = _READING_RANGES[key]
        if isinstance(value, bool) or not isinstance(value, int) or not low <= value <= high:
            raise ReadingSettingsError(key, f"{key} must be an integer from {low} to {high}")
        checked[key] = value
    return checked


def get_reading_settings(conn: sqlite3.Connection) -> dict[str, Any]:
    """All reading settings: the stored values over the defaults. A stored value that is not valid raises ``RuntimeError`` (C1): only ``set_reading_settings`` writes the value."""
    row = conn.execute("SELECT value FROM instance_settings WHERE key=?", (READING_SETTING_KEY,)).fetchone()
    values = dict(READING_DEFAULTS)
    if row is None:
        return values
    try:
        stored = json.loads(str(row[0]))
        values.update(validate_reading_update(stored) if stored else {})
    except (ValueError, TypeError) as exc:
        raise RuntimeError(f"instance_settings.{READING_SETTING_KEY} is not valid: {exc}") from exc
    return values


def set_reading_settings(conn: sqlite3.Connection, update: dict[str, Any], *, updated_by: int | None, now: float | None = None) -> dict[str, Any]:
    """Apply a checked partial update and commit. Only values that differ from the default are stored. Return all settings."""
    values = get_reading_settings(conn)
    values.update(validate_reading_update(update))
    stored = {k: v for k, v in values.items() if v != READING_DEFAULTS[k]}
    conn.execute(
        "INSERT INTO instance_settings (key, value, updated_at, updated_by) VALUES (?, ?, ?, ?) "
        "ON CONFLICT(key) DO UPDATE SET value=excluded.value, updated_at=excluded.updated_at, updated_by=excluded.updated_by",
        (READING_SETTING_KEY, json.dumps(stored, sort_keys=True), time.time() if now is None else now, updated_by),
    )
    conn.commit()
    return values


# --- Retention of an account (Section 10 rule 1) ---------------------------


def get_account_retention(conn: sqlite3.Connection, account_id: int) -> int | None:
    """The retention that the owner set, or ``None`` (the instance value)."""
    row = conn.execute("SELECT retention_days FROM federation_account_settings WHERE account_id=?", (account_id,)).fetchone()
    return None if row is None or row[0] is None else int(row[0])


def set_account_retention(conn: sqlite3.Connection, account_id: int, retention_days: int | None, *, instance_max: int, now: float) -> None:
    """Set the retention of the account (``client.db``). ``None`` returns to the instance value. A value above ``instance_max`` (the instance value of ``claire.db``) raises ``ReadingSettingsError``."""
    if retention_days is not None:
        if isinstance(retention_days, bool) or not isinstance(retention_days, int) or not 1 <= retention_days <= instance_max:
            raise ReadingSettingsError("retention_days", f"retention_days must be null or an integer from 1 to {instance_max}")
    conn.execute(
        "INSERT INTO federation_account_settings (account_id, retention_days, updated_at) VALUES (?, ?, ?) "
        "ON CONFLICT(account_id) DO UPDATE SET retention_days=excluded.retention_days, updated_at=excluded.updated_at",
        (account_id, retention_days, now),
    )


def effective_retention_days(conn: sqlite3.Connection, account_id: int, *, instance_value: int) -> int:
    own = get_account_retention(conn, account_id)
    return instance_value if own is None else min(own, instance_value)


# --- Client outbox (Section 5.2) -------------------------------------------


def add_client_task(conn: sqlite3.Connection, *, account_id: int, kind: str, target_iri: str | None, ref_id: int | None, now: float) -> int:
    """Add one ``pending`` task to the client outbox of ``client.db`` and return its ID. ``ref_id`` is the row that the task is for (for example the follow). The caller commits, in the same transaction as the row."""
    from .client_db import CLIENT_OUTBOX_KINDS

    if kind not in CLIENT_OUTBOX_KINDS:
        raise ValueError(f"unknown client task kind: {kind!r}")
    cur = conn.execute("INSERT INTO federation_client_outbox (account_id, kind, target_iri, ref_id, status, created_at) VALUES (?, ?, ?, ?, 'pending', ?)", (account_id, kind, target_iri, ref_id, now))
    return int(cur.lastrowid)
