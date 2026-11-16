"""Store of the themes: collections of knowledge bases with the same subject and the same knowledge type (SHARED_KNOWLEDGE_BASES_AND_THEMES_DESIGN.md Sections 8 and 11.2, unit CT-1).

Only this module reads and writes the tables ``themes``, ``theme_knowledge_bases``, ``kb_signatures``, and ``kb_document_matches`` (store boundary RD27 R2).

A theme number is never the number of a knowledge base, and a knowledge base number is never a theme number (Section 5.4): the generator refuses a number that the other table has and tries the next ``seq``.

The functions do not commit: the caller sets the transaction boundary.
"""

from __future__ import annotations

import sqlite3
import time
from typing import Any, Callable

from .. import ids
from . import db as dbm

KNOWLEDGE_TYPES = ("real", "fictional")
THEME_STATES = ("active", "empty", "merged")
MEMBER_SOURCES = ("computed", "admin", "created", "fork")

_THEME_KEYS = (
    "id", "number", "label", "label_locked", "description", "knowledge_type", "state", "merged_into_theme_id",
    "top_entities", "signature", "last_activity_at", "computed_at", "created_at", "updated_at",
)


def number_is_kb_number(conn: sqlite3.Connection, number: int) -> bool:
    return conn.execute("SELECT 1 FROM knowledge_bases WHERE number=?", (int(number),)).fetchone() is not None


def number_is_theme_number(conn: sqlite3.Connection, number: int) -> bool:
    return conn.execute("SELECT 1 FROM themes WHERE number=?", (int(number),)).fetchone() is not None


def create_theme(
    conn: sqlite3.Connection,
    *,
    label: str,
    knowledge_type: str,
    description: str = "",
    now: float | None = None,
    now_ms: Callable[[], int] | None = None,
) -> dict[str, Any]:
    """Insert one theme in the state ``empty`` (no knowledge base yet) and return it."""
    if knowledge_type not in KNOWLEDGE_TYPES:
        raise ValueError(f"invalid knowledge type: {knowledge_type!r}")
    cleaned = str(label or "").strip()
    if not cleaned:
        raise ValueError("theme label is required")
    ts = time.time() if now is None else now
    holder: dict[str, int] = {}

    def _insert(number: int) -> None:
        cur = conn.execute(
            "INSERT INTO themes (number, label, description, knowledge_type, state, created_at, updated_at) VALUES (?, ?, ?, ?, 'empty', ?, ?)",
            (number, cleaned, description, knowledge_type, ts, ts),
        )
        holder["id"] = int(cur.lastrowid)

    ids.insert_with_timestamp_id(
        conn,
        table="themes",
        column="number",
        salt=dbm.read_id_salt(conn),
        insert=_insert,
        now_ms=now_ms,
        is_taken=lambda n: number_is_kb_number(conn, n),
    )
    theme = get_theme(conn, holder["id"])
    assert theme is not None
    return theme


def get_theme(conn: sqlite3.Connection, theme_id: int) -> dict[str, Any] | None:
    row = conn.execute(f"SELECT {', '.join(_THEME_KEYS)} FROM themes WHERE id=?", (int(theme_id),)).fetchone()
    return None if row is None else dict(zip(_THEME_KEYS, tuple(row)))


def get_theme_by_number(conn: sqlite3.Connection, number: int) -> dict[str, Any] | None:
    row = conn.execute(f"SELECT {', '.join(_THEME_KEYS)} FROM themes WHERE number=?", (int(number),)).fetchone()
    return None if row is None else dict(zip(_THEME_KEYS, tuple(row)))


def list_themes(conn: sqlite3.Connection, *, knowledge_type: str | None = None, include_merged: bool = False) -> list[dict[str, Any]]:
    sql = f"SELECT {', '.join(_THEME_KEYS)} FROM themes WHERE 1=1"
    args: list[Any] = []
    if knowledge_type is not None:
        if knowledge_type not in KNOWLEDGE_TYPES:
            raise ValueError(f"invalid knowledge type: {knowledge_type!r}")
        sql += " AND knowledge_type=?"
        args.append(knowledge_type)
    if not include_merged:
        sql += " AND state != 'merged'"
    sql += " ORDER BY number DESC"
    return [dict(zip(_THEME_KEYS, tuple(r))) for r in conn.execute(sql, args).fetchall()]


def add_theme_member(
    conn: sqlite3.Connection,
    *,
    theme_id: int,
    kb_id: int,
    source: str,
    score: float | None = None,
    pinned: bool = False,
    now: float | None = None,
) -> None:
    """Put a knowledge base in a theme. The knowledge type of the knowledge base must be the one of the theme (a personal knowledge base counts as ``real``, CT18)."""
    if source not in MEMBER_SOURCES:
        raise ValueError(f"invalid member source: {source!r}")
    theme = get_theme(conn, theme_id)
    if theme is None:
        raise KeyError(f"theme {theme_id} not found")
    row = conn.execute("SELECT is_personal, knowledge_type FROM knowledge_bases WHERE id=?", (int(kb_id),)).fetchone()
    if row is None:
        raise KeyError(f"knowledge base {kb_id} not found")
    kb_type = "real" if int(row[0] or 0) == 1 else row[1]
    if kb_type != theme["knowledge_type"]:
        raise ValueError("knowledge_type_mismatch")
    ts = time.time() if now is None else now
    conn.execute(
        "INSERT INTO theme_knowledge_bases (theme_id, kb_id, source, score, pinned) VALUES (?, ?, ?, ?, ?) "
        "ON CONFLICT(theme_id, kb_id) DO UPDATE SET source=excluded.source, score=excluded.score, pinned=excluded.pinned, excluded=0",
        (int(theme_id), int(kb_id), source, score, 1 if pinned else 0),
    )
    conn.execute("UPDATE themes SET state='active', updated_at=? WHERE id=? AND state='empty'", (ts, int(theme_id)))


def theme_members(conn: sqlite3.Connection, theme_id: int) -> list[dict[str, Any]]:
    keys = ("theme_id", "kb_id", "source", "score", "pinned", "excluded")
    return [
        dict(zip(keys, tuple(r)))
        for r in conn.execute(
            "SELECT theme_id, kb_id, source, score, pinned, excluded FROM theme_knowledge_bases WHERE theme_id=? AND excluded=0 ORDER BY kb_id",
            (int(theme_id),),
        ).fetchall()
    ]
