"""Store of the themes: collections of knowledge bases with the same subject and the same knowledge type (SHARED_KNOWLEDGE_BASES_AND_THEMES_DESIGN.md Sections 8 and 11.2, unit CT-1).

Only this module reads and writes the tables ``themes``, ``theme_knowledge_bases``, ``theme_remote_knowledge_bases``, ``kb_signatures``, and ``kb_document_matches`` (store boundary RD27 R2).

A remote knowledge base (a record of ``knowledge_federation.remote_kbs``) can be a member of a local theme (KNOWLEDGE_FEDERATION_DESIGN.md Section 8.2). Its memberships are in ``theme_remote_knowledge_bases``, with the same columns and the same admin adjustments as a local membership. A theme number never leaves the server.

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


def themes_of_kb(conn: sqlite3.Connection, kb_id: int) -> list[int]:
    """The IDs of the active themes that have the knowledge base as a member (not excluded)."""
    rows = conn.execute(
        "SELECT m.theme_id FROM theme_knowledge_bases m JOIN themes t ON t.id = m.theme_id WHERE m.kb_id=? AND m.excluded=0 AND t.state='active' ORDER BY m.theme_id",
        (int(kb_id),),
    ).fetchall()
    return [int(r[0]) for r in rows]


def share_a_theme(conn: sqlite3.Connection, kb_a: int, kb_b: int) -> bool:
    """True when the two knowledge bases are members of one active theme."""
    return bool(set(themes_of_kb(conn, kb_a)) & set(themes_of_kb(conn, kb_b)))


# --- judgement (design Sections 8.2, 8.3, 8.5; unit CT-5) -----------------------------------------------------------

_THEME_UPDATES = frozenset({"label", "label_locked", "description", "state", "merged_into_theme_id", "top_entities", "signature", "last_activity_at", "computed_at"})
_MEMBER_KEYS = ("theme_id", "kb_id", "source", "score", "pinned", "excluded")


def update_theme(conn: sqlite3.Connection, theme_id: int, *, now: float | None = None, **fields: Any) -> None:
    """Change columns of one theme. ``ValueError`` for a column that does not change or a state that is not valid."""
    unknown = set(fields) - _THEME_UPDATES
    if unknown:
        raise ValueError(f"columns of a theme that do not change: {sorted(unknown)}")
    if "state" in fields and fields["state"] not in THEME_STATES:
        raise ValueError(f"invalid theme state: {fields['state']!r}")
    names = sorted(fields)
    conn.execute(
        f"UPDATE themes SET {''.join(f'{n}=?, ' for n in names)}updated_at=? WHERE id=?",
        (*(fields[n] for n in names), time.time() if now is None else now, int(theme_id)),
    )


def all_members(conn: sqlite3.Connection) -> list[dict[str, Any]]:
    """Each row of ``theme_knowledge_bases``, the excluded ones included."""
    rows = conn.execute(f"SELECT {', '.join(_MEMBER_KEYS)} FROM theme_knowledge_bases ORDER BY theme_id, kb_id").fetchall()
    return [dict(zip(_MEMBER_KEYS, tuple(r))) for r in rows]


def get_member(conn: sqlite3.Connection, theme_id: int, kb_id: int) -> dict[str, Any] | None:
    row = conn.execute(f"SELECT {', '.join(_MEMBER_KEYS)} FROM theme_knowledge_bases WHERE theme_id=? AND kb_id=?", (int(theme_id), int(kb_id))).fetchone()
    return None if row is None else dict(zip(_MEMBER_KEYS, tuple(row)))


def set_computed_member(conn: sqlite3.Connection, *, theme_id: int, kb_id: int, score: float | None) -> bool:
    """Put a knowledge base in a theme as a result of the judgement. A row that the admin made (pinned or excluded) stays as it is; then the result is False."""
    current = get_member(conn, theme_id, kb_id)
    if current is not None and (current["pinned"] or current["excluded"]):
        return False
    conn.execute(
        "INSERT INTO theme_knowledge_bases (theme_id, kb_id, source, score, pinned, excluded) VALUES (?, ?, 'computed', ?, 0, 0) "
        "ON CONFLICT(theme_id, kb_id) DO UPDATE SET source='computed', score=excluded.score",
        (int(theme_id), int(kb_id), score),
    )
    return True


def delete_free_members_of_kb(conn: sqlite3.Connection, kb_id: int) -> int:
    """Delete the memberships of a knowledge base that the judgement can change: not pinned and not excluded (design Section 8.3)."""
    return conn.execute("DELETE FROM theme_knowledge_bases WHERE kb_id=? AND pinned=0 AND excluded=0", (int(kb_id),)).rowcount


def set_admin_member(conn: sqlite3.Connection, *, theme_id: int, kb_id: int, pinned: bool, excluded: bool) -> None:
    """An adjustment of the admin (design Section 8.3): pin a knowledge base to a theme, or exclude it from the theme. The two flags off deletes the row; the next run decides again."""
    if pinned and excluded:
        raise ValueError("a membership is pinned or excluded, not the two")
    if not pinned and not excluded:
        conn.execute("DELETE FROM theme_knowledge_bases WHERE theme_id=? AND kb_id=?", (int(theme_id), int(kb_id)))
        return
    conn.execute(
        "INSERT INTO theme_knowledge_bases (theme_id, kb_id, source, score, pinned, excluded) VALUES (?, ?, 'admin', NULL, ?, ?) "
        "ON CONFLICT(theme_id, kb_id) DO UPDATE SET source='admin', pinned=excluded.pinned, excluded=excluded.excluded",
        (int(theme_id), int(kb_id), 1 if pinned else 0, 1 if excluded else 0),
    )


_SIGNATURE_KEYS = ("kb_id", "knowledge_type", "embed_model", "entities", "centroid", "visible_documents", "computed_at")


def save_signature(conn: sqlite3.Connection, *, kb_id: int, knowledge_type: str, embed_model: str, entities: str, centroid: bytes | None, visible_documents: int, now: float | None = None) -> None:
    """Store the signature of one knowledge base. ``entities`` is the JSON list ``[[type, norm_name, weight, name | null], ...]``."""
    conn.execute(
        "INSERT INTO kb_signatures (kb_id, knowledge_type, embed_model, entities, centroid, visible_documents, computed_at) VALUES (?, ?, ?, ?, ?, ?, ?) "
        "ON CONFLICT(kb_id) DO UPDATE SET knowledge_type=excluded.knowledge_type, embed_model=excluded.embed_model, entities=excluded.entities, centroid=excluded.centroid, "
        "visible_documents=excluded.visible_documents, computed_at=excluded.computed_at",
        (int(kb_id), knowledge_type, embed_model, entities, centroid, int(visible_documents), time.time() if now is None else now),
    )


def delete_signature(conn: sqlite3.Connection, kb_id: int) -> None:
    conn.execute("DELETE FROM kb_signatures WHERE kb_id=?", (int(kb_id),))


def load_signatures(conn: sqlite3.Connection) -> list[dict[str, Any]]:
    rows = conn.execute(f"SELECT {', '.join(_SIGNATURE_KEYS)} FROM kb_signatures ORDER BY kb_id").fetchall()
    return [dict(zip(_SIGNATURE_KEYS, tuple(r))) for r in rows]


_MATCH_KEYS = ("document_id", "kb_id", "theme_id", "knowledge_type", "score", "computed_at")


def save_document_match(conn: sqlite3.Connection, *, document_id: str, kb_id: int, theme_id: int | None, knowledge_type: str | None, score: float | None, now: float | None = None) -> None:
    """Store the theme match of one document (design Section 8.5 rule 7). ``theme_id`` None: the document has no match."""
    conn.execute(
        "INSERT INTO kb_document_matches (document_id, kb_id, theme_id, knowledge_type, score, computed_at) VALUES (?, ?, ?, ?, ?, ?) "
        "ON CONFLICT(document_id) DO UPDATE SET kb_id=excluded.kb_id, theme_id=excluded.theme_id, knowledge_type=excluded.knowledge_type, score=excluded.score, computed_at=excluded.computed_at",
        (document_id, int(kb_id), theme_id, knowledge_type, score, time.time() if now is None else now),
    )


def get_document_match(conn: sqlite3.Connection, document_id: str) -> dict[str, Any] | None:
    row = conn.execute(f"SELECT {', '.join(_MATCH_KEYS)} FROM kb_document_matches WHERE document_id=?", (document_id,)).fetchone()
    return None if row is None else dict(zip(_MATCH_KEYS, tuple(row)))


def document_matches_with_themes(conn: sqlite3.Connection, document_ids: list[str]) -> dict[str, dict[str, Any]]:
    """The theme match of each given document that has a theme, with one query: ``{document_id: {"theme_number", "theme_label", "knowledge_type", "score"}}``. A document with no match, or with a match that has no theme, is not in the result."""
    if not document_ids:
        return {}
    marks = ", ".join("?" for _ in document_ids)
    rows = conn.execute(
        f"SELECT m.document_id, t.number, t.label, m.knowledge_type, m.score FROM kb_document_matches m JOIN themes t ON t.id = m.theme_id WHERE m.document_id IN ({marks})",
        list(document_ids),
    ).fetchall()
    return {str(r[0]): {"theme_number": int(r[1]), "theme_label": r[2], "knowledge_type": r[3], "score": r[4]} for r in rows}


def delete_document_matches_of_kb(conn: sqlite3.Connection, kb_id: int) -> None:
    conn.execute("DELETE FROM kb_document_matches WHERE kb_id=?", (int(kb_id),))


def delete_kb_rows(conn: sqlite3.Connection, kb_id: int) -> None:
    """Delete the theme rows of a knowledge base that goes: its document matches, its signature, and its memberships in themes."""
    delete_document_matches_of_kb(conn, kb_id)
    delete_signature(conn, kb_id)
    conn.execute("DELETE FROM theme_knowledge_bases WHERE kb_id=?", (int(kb_id),))


def fiction_like_documents(conn: sqlite3.Connection, kb_id: int) -> set[str]:
    """The documents of a knowledge base whose theme match is a ``fictional`` theme (design Section 8.5, CT18)."""
    return {str(r[0]) for r in conn.execute("SELECT document_id FROM kb_document_matches WHERE kb_id=? AND knowledge_type='fictional' AND theme_id IS NOT NULL", (int(kb_id),))}


def kb_type_for_themes(conn: sqlite3.Connection, kb_id: int) -> str | None:
    """The knowledge type of a knowledge base for the themes: a personal knowledge base counts as ``real`` (CT18). None: no such knowledge base."""
    row = conn.execute("SELECT is_personal, knowledge_type FROM knowledge_bases WHERE id=?", (int(kb_id),)).fetchone()
    if row is None:
        return None
    return "real" if int(row[0] or 0) == 1 else row[1]


def merge_themes(conn: sqlite3.Connection, *, source_id: int, target_id: int, now: float | None = None) -> None:
    """Merge theme A into theme B (design Section 8.3): A gets the state ``merged`` and points to B, and the members of A are pinned to B. The two themes must have one knowledge type. No commit."""
    source, target = get_theme(conn, source_id), get_theme(conn, target_id)
    if source is None or target is None:
        raise KeyError("theme not found")
    if source_id == target_id or target["state"] == "merged" or source["state"] == "merged":
        raise ValueError("theme_merge_invalid")
    if source["knowledge_type"] != target["knowledge_type"]:
        raise ValueError("knowledge_type_mismatch")
    for member in theme_members(conn, source_id):
        set_admin_member(conn, theme_id=target_id, kb_id=int(member["kb_id"]), pinned=True, excluded=False)
    conn.execute("DELETE FROM theme_knowledge_bases WHERE theme_id=?", (int(source_id),))
    # The remote members go with the local ones (KNOWLEDGE_FEDERATION_DESIGN.md Section 8.2).
    for member in remote_members(conn, theme_id=source_id):
        set_admin_remote_member(conn, theme_id=target_id, remote_kb_id=int(member["remote_kb_id"]), pinned=True, excluded=False)
    conn.execute("DELETE FROM theme_remote_knowledge_bases WHERE theme_id=?", (int(source_id),))
    update_theme(conn, source_id, now=now, state="merged", merged_into_theme_id=int(target_id))
    update_theme(conn, target_id, now=now, state="active")


# --- remote knowledge bases in local themes (KNOWLEDGE_FEDERATION_DESIGN.md Section 8.2, unit KF2) -----------------

REMOTE_MEMBER_TABLE = "theme_remote_knowledge_bases"
REMOTE_MEMBER_COLUMNS = ("theme_id", "remote_kb_id", "source", "score", "pinned", "excluded")
REMOTE_MEMBER_SOURCES = ("computed", "admin")

# ``remote_kb_id`` is ``kf_remote_knowledge_bases.id``. It has no REFERENCES clause: the store of remote knowledge bases deletes the rows of a record that goes (``delete_remote_kb_rows``).
_REMOTE_MEMBER_SCHEMA = """
CREATE TABLE IF NOT EXISTS theme_remote_knowledge_bases (
    theme_id INTEGER NOT NULL REFERENCES themes(id) ON DELETE CASCADE,
    remote_kb_id INTEGER NOT NULL,
    source TEXT NOT NULL CHECK (source IN ('computed', 'admin')),
    score REAL,
    pinned INTEGER NOT NULL DEFAULT 0,
    excluded INTEGER NOT NULL DEFAULT 0,
    PRIMARY KEY (theme_id, remote_kb_id)
);
CREATE INDEX IF NOT EXISTS idx_theme_remote_knowledge_bases_kb ON theme_remote_knowledge_bases(remote_kb_id)
"""


def ensure_remote_member_schema(conn: sqlite3.Connection) -> None:
    """Make the table of the remote members when it is not there. Idempotent; the migration of ``store.db`` calls it at each start."""
    for statement in _REMOTE_MEMBER_SCHEMA.split(";"):
        if statement.strip():
            conn.execute(statement)


def remote_members(conn: sqlite3.Connection, *, theme_id: int | None = None, include_excluded: bool = False) -> list[dict[str, Any]]:
    """The memberships of remote knowledge bases, of one theme or of each theme. An excluded row is in the result only with ``include_excluded``."""
    sql = f"SELECT {', '.join(REMOTE_MEMBER_COLUMNS)} FROM theme_remote_knowledge_bases WHERE 1=1"
    args: list[Any] = []
    if theme_id is not None:
        sql += " AND theme_id=?"
        args.append(int(theme_id))
    if not include_excluded:
        sql += " AND excluded=0"
    return [dict(zip(REMOTE_MEMBER_COLUMNS, tuple(r))) for r in conn.execute(sql + " ORDER BY theme_id, remote_kb_id", args).fetchall()]


def get_remote_member(conn: sqlite3.Connection, theme_id: int, remote_kb_id: int) -> dict[str, Any] | None:
    row = conn.execute(f"SELECT {', '.join(REMOTE_MEMBER_COLUMNS)} FROM theme_remote_knowledge_bases WHERE theme_id=? AND remote_kb_id=?", (int(theme_id), int(remote_kb_id))).fetchone()
    return None if row is None else dict(zip(REMOTE_MEMBER_COLUMNS, tuple(row)))


def set_computed_remote_member(conn: sqlite3.Connection, *, theme_id: int, remote_kb_id: int, score: float | None) -> bool:
    """Put a remote knowledge base in a theme as a result of the judgement. A row that the admin made (pinned or excluded) stays as it is; then the result is False."""
    current = get_remote_member(conn, theme_id, remote_kb_id)
    if current is not None and (current["pinned"] or current["excluded"]):
        return False
    conn.execute(
        "INSERT INTO theme_remote_knowledge_bases (theme_id, remote_kb_id, source, score, pinned, excluded) VALUES (?, ?, 'computed', ?, 0, 0) "
        "ON CONFLICT(theme_id, remote_kb_id) DO UPDATE SET source='computed', score=excluded.score",
        (int(theme_id), int(remote_kb_id), score),
    )
    return True


def delete_free_remote_members(conn: sqlite3.Connection) -> int:
    """Delete each membership of a remote knowledge base that the judgement can change: not pinned and not excluded. The judgement computes these rows again in each run."""
    return conn.execute("DELETE FROM theme_remote_knowledge_bases WHERE pinned=0 AND excluded=0").rowcount


def set_admin_remote_member(conn: sqlite3.Connection, *, theme_id: int, remote_kb_id: int, pinned: bool, excluded: bool) -> None:
    """An adjustment of the admin for a remote knowledge base: the same rule as ``set_admin_member``. The two flags off deletes the row; the next run decides again."""
    if pinned and excluded:
        raise ValueError("a membership is pinned or excluded, not the two")
    if not pinned and not excluded:
        conn.execute("DELETE FROM theme_remote_knowledge_bases WHERE theme_id=? AND remote_kb_id=?", (int(theme_id), int(remote_kb_id)))
        return
    conn.execute(
        "INSERT INTO theme_remote_knowledge_bases (theme_id, remote_kb_id, source, score, pinned, excluded) VALUES (?, ?, 'admin', NULL, ?, ?) "
        "ON CONFLICT(theme_id, remote_kb_id) DO UPDATE SET source='admin', pinned=excluded.pinned, excluded=excluded.excluded",
        (int(theme_id), int(remote_kb_id), 1 if pinned else 0, 1 if excluded else 0),
    )


def delete_remote_kb_rows(conn: sqlite3.Connection, remote_kb_ids: list[int]) -> None:
    """Delete the memberships of remote knowledge bases that go, the admin adjustments included."""
    for remote_kb_id in remote_kb_ids:
        conn.execute("DELETE FROM theme_remote_knowledge_bases WHERE remote_kb_id=?", (int(remote_kb_id),))
