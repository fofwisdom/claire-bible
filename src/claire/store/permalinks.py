"""The permalink registry (docs/design/PERMALINK_UNIFICATION_DESIGN.md Section 7).

One table, ``permalinks`` in ``claire.db``, gives each document, note, and entity one address token. The address of a document or a note is ``{origin}/silvercord/{token}`` (Silvercord: only for ActivityPub object kinds). The address of an entity is ``{origin}/entity/{token}``. The token never changes and is never given to a second item.

- A row is made when the item is first seen (``ensure``): the migration makes the rows of the items that exist, and each later read or write of an item without a row makes its row. No insert path of documents, notes, or entities has a hook.
- ``alias_of``: the token of a document that a near-duplicate merge removed points to the token of the keeper.
- ``gone_at``: the item does not exist now. A route sets it when it does not find the target (``mark_gone``).
- An entity row has ``identity`` (type and normalized name). When an entity with the same knowledge base and identity is made again, and exactly one row without a live entity matches, that row gets the new entity ID: the permalink survives a second extraction.
"""

from __future__ import annotations

import os
import secrets
import sqlite3
import time
from dataclasses import dataclass
from typing import Literal

Kind = Literal["document", "note", "entity"]
KINDS: tuple[Kind, ...] = ("document", "note", "entity")

# The one place of each path word (design U1). Each other identifier uses ``permalink``.
# ``/silvercord/`` is only for the kinds that can be ActivityPub objects: a document (``Article``) and a note (``Note``). A kind that is not an ActivityPub object gets its own path word, as the entity does.
PERMALINK_PATH_PREFIX = "/silvercord/"
ENTITY_PATH_PREFIX = "/entity/"
ACTIVITYPUB_KINDS: tuple[Kind, ...] = ("document", "note")

TOKEN_ALPHABET = "23456789abcdefghjkmnpqrstuvwxyz"
TOKEN_LENGTH = 16

_IDENTITY_SEPARATOR = "\x1f"

SCHEMA = """
CREATE TABLE IF NOT EXISTS permalinks (
    token TEXT PRIMARY KEY,
    kind TEXT NOT NULL CHECK (kind IN ('document', 'note', 'entity')),
    kb_id INTEGER,
    account_id INTEGER,
    target TEXT NOT NULL,
    identity TEXT,
    link_access INTEGER NOT NULL DEFAULT 0 CHECK (link_access IN (0, 1)),
    alias_of TEXT,
    gone_at REAL,
    created_at REAL NOT NULL
);
CREATE UNIQUE INDEX IF NOT EXISTS idx_permalinks_live ON permalinks(kind, target) WHERE alias_of IS NULL;
CREATE INDEX IF NOT EXISTS idx_permalinks_entity_identity ON permalinks(kb_id, identity) WHERE kind = 'entity' AND alias_of IS NULL;
"""


@dataclass(frozen=True)
class Permalink:
    """One live row of the registry (an alias is followed before this is made)."""

    token: str
    kind: Kind
    kb_id: int | None
    account_id: int | None
    target: str
    link_access: bool
    gone_at: float | None
    # The token of the request when it was an alias of ``token``; else None.
    alias: str | None = None


def plausible_token(token: str) -> bool:
    """True when ``token`` has the length and the alphabet of an address token. No database read."""
    return len(token or "") == TOKEN_LENGTH and all(char in TOKEN_ALPHABET for char in token)


def permalink_path(token: str) -> str:
    """The Silvercord path of a document or a note."""
    return f"{PERMALINK_PATH_PREFIX}{token}"


def entity_path(token: str) -> str:
    """The address path of an entity."""
    return f"{ENTITY_PATH_PREFIX}{token}"


def path_of(kind: Kind, token: str) -> str:
    return entity_path(token) if kind == "entity" else permalink_path(token)


def permalink_url(origin: str, token: str) -> str:
    return f"{origin.rstrip('/')}{PERMALINK_PATH_PREFIX}{token}"


def entity_identity(entity_type: str, norm_name: str) -> str:
    return f"{entity_type}{_IDENTITY_SEPARATOR}{norm_name}"


def ensure_schema(conn: sqlite3.Connection) -> None:
    conn.executescript(SCHEMA)


def _new_token() -> str:
    return "".join(secrets.choice(TOKEN_ALPHABET) for _ in range(TOKEN_LENGTH))


def _insert(conn: sqlite3.Connection, *, kind: Kind, target: str, kb_id: int | None, account_id: int | None, identity: str | None, link_access: int, now: float, token: str | None = None) -> str:
    """Insert one live row. A given ``token`` (migration of a share token) is used as it is; else a new one is made until it is free."""
    while True:
        candidate = token or _new_token()
        try:
            conn.execute(
                "INSERT INTO permalinks(token, kind, kb_id, account_id, target, identity, link_access, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                (candidate, kind, kb_id, account_id, target, identity, link_access, now),
            )
            return candidate
        except sqlite3.IntegrityError:
            # The live row of the item exists (a second writer made it), or a new token is in use.
            row = conn.execute("SELECT token FROM permalinks WHERE kind=? AND target=? AND alias_of IS NULL", (kind, target)).fetchone()
            if row is not None:
                return str(row[0])
            if token is not None:
                raise


def token_of(conn: sqlite3.Connection, kind: Kind, target: str | int) -> str | None:
    """The address token of an item that has a row, or None. No write."""
    row = conn.execute("SELECT token FROM permalinks WHERE kind=? AND target=? AND alias_of IS NULL", (kind, str(target))).fetchone()
    return None if row is None else str(row[0])


def tokens_of(conn: sqlite3.Connection, kind: Kind, targets: list[str]) -> dict[str, str]:
    """The address tokens of the items that have a row, by target. No write."""
    found: dict[str, str] = {}
    for start in range(0, len(targets), 500):
        chunk = [str(t) for t in targets[start:start + 500]]
        marks = ",".join("?" for _ in chunk)
        for row in conn.execute(f"SELECT target, token FROM permalinks WHERE kind=? AND alias_of IS NULL AND target IN ({marks})", (kind, *chunk)):
            found[str(row[0])] = str(row[1])
    return found


def _revive_entity_row(conn: sqlite3.Connection, *, kb_id: int, identity: str, entity_id: str) -> str | None:
    """Give the one row of this knowledge base and identity that has no live entity to ``entity_id``. None when no row or more than one row matches."""
    rows = conn.execute(
        "SELECT p.token FROM permalinks p WHERE p.kind='entity' AND p.alias_of IS NULL AND p.kb_id=? AND p.identity=? AND NOT EXISTS (SELECT 1 FROM entities e WHERE e.id = p.target)",
        (kb_id, identity),
    ).fetchall()
    if len(rows) != 1:
        return None
    token = str(rows[0][0])
    conn.execute("UPDATE permalinks SET target=?, gone_at=NULL WHERE token=?", (entity_id, token))
    return token


def ensure(conn: sqlite3.Connection, kind: Kind, target: str | int, *, kb_id: int | None = None, account_id: int | None = None, identity: str | None = None, now: float | None = None) -> str:
    """The address token of an item. Makes the row when the item has none. The caller commits."""
    key = str(target)
    existing = token_of(conn, kind, key)
    if existing is not None:
        return existing
    if kind == "entity" and kb_id is not None and identity:
        revived = _revive_entity_row(conn, kb_id=int(kb_id), identity=identity, entity_id=key)
        if revived is not None:
            return revived
    return _insert(conn, kind=kind, target=key, kb_id=kb_id, account_id=account_id, identity=identity, link_access=0, now=time.time() if now is None else now)


def ensure_document(conn: sqlite3.Connection, document_id: str, kb_id: int) -> str:
    return ensure(conn, "document", document_id, kb_id=int(kb_id))


def ensure_entity(conn: sqlite3.Connection, entity_id: str) -> str | None:
    """The address token of an entity of ``entities``. None when the entity does not exist."""
    existing = token_of(conn, "entity", entity_id)
    if existing is not None:
        return existing
    row = conn.execute("SELECT kb_id, type, norm_name FROM entities WHERE id=?", (entity_id,)).fetchone()
    if row is None:
        return None
    return ensure(conn, "entity", entity_id, kb_id=int(row[0]), identity=entity_identity(str(row[1]), str(row[2])))


def ensure_note(conn: sqlite3.Connection, public_id: int, account_id: int) -> str:
    return ensure(conn, "note", str(int(public_id)), account_id=int(account_id))


def database_file(conn: sqlite3.Connection) -> str | None:
    """The file of the main database of a connection, or None for a database in memory."""
    for row in conn.execute("PRAGMA database_list"):
        if row[1] == "main":
            return str(row[2]) or None
    return None


def ensure_note_token(db_file: str | os.PathLike[str], public_id: int, account_id: int) -> str | None:
    """The address token of a note, with a short connection of its own to ``claire.db`` (a note is in ``client.db``, and its callers have no write connection to ``claire.db``). None when the database cannot be written."""
    try:
        conn = sqlite3.connect(str(db_file), timeout=10)
    except sqlite3.OperationalError:
        return None
    try:
        token = ensure_note(conn, public_id, account_id)
        conn.commit()
        return token
    except sqlite3.OperationalError:
        return None
    finally:
        conn.close()


def resolve(conn: sqlite3.Connection, token: str) -> Permalink | None:
    """The live row of a token. An alias gives the row of its target, with ``alias`` set. None for an unknown token."""
    if not plausible_token(token):
        return None
    alias: str | None = None
    current = token
    for _ in range(8):
        row = conn.execute("SELECT token, kind, kb_id, account_id, target, link_access, alias_of, gone_at FROM permalinks WHERE token=?", (current,)).fetchone()
        if row is None:
            return None
        if row[6] is None:
            return Permalink(token=str(row[0]), kind=row[1], kb_id=row[2], account_id=row[3], target=str(row[4]), link_access=bool(row[5]), gone_at=row[7], alias=alias)
        alias = token
        current = str(row[6])
    return None


def set_link_access(conn: sqlite3.Connection, kind: Kind, target: str | int, value: bool) -> bool:
    """Set link access of an item that has a row. False when it has none. The caller commits."""
    cursor = conn.execute("UPDATE permalinks SET link_access=? WHERE kind=? AND target=? AND alias_of IS NULL", (1 if value else 0, kind, str(target)))
    return cursor.rowcount > 0


def link_access(conn: sqlite3.Connection, kind: Kind, target: str | int) -> bool:
    row = conn.execute("SELECT link_access FROM permalinks WHERE kind=? AND target=? AND alias_of IS NULL", (kind, str(target))).fetchone()
    return bool(row and row[0])


def mark_kb_gone(conn: sqlite3.Connection, kb_id: int, *, now: float | None = None) -> None:
    """Record that the documents and entities of a knowledge base do not exist now (the knowledge base was deleted or emptied). The caller commits."""
    conn.execute("UPDATE permalinks SET gone_at=COALESCE(gone_at, ?), link_access=0 WHERE kb_id=? AND kind IN ('document', 'entity') AND alias_of IS NULL", (time.time() if now is None else now, int(kb_id)))


def mark_gone(conn: sqlite3.Connection, kind: Kind, target: str | int, *, now: float | None = None) -> None:
    """Record that the item does not exist now. The row stays: the token is not given again. The caller commits."""
    conn.execute("UPDATE permalinks SET gone_at=COALESCE(gone_at, ?) WHERE kind=? AND target=? AND alias_of IS NULL", (time.time() if now is None else now, kind, str(target)))


def merge_documents(conn: sqlite3.Connection, keeper_id: str, loser_ids: list[str], *, kb_id: int) -> None:
    """After a near-duplicate merge: the tokens of the losers become aliases of the token of the keeper, and the keeper gets the highest link access of the group (design Section 7). The caller commits."""
    keeper_token = ensure_document(conn, keeper_id, kb_id)
    shared = link_access(conn, "document", keeper_id)
    for loser in loser_ids:
        loser_token = token_of(conn, "document", loser)
        if loser_token is None or loser_token == keeper_token:
            continue
        shared = shared or link_access(conn, "document", loser)
        # Aliases of the loser now point to the keeper, and then the loser.
        conn.execute("UPDATE permalinks SET alias_of=? WHERE alias_of=?", (keeper_token, loser_token))
        conn.execute("UPDATE permalinks SET alias_of=?, link_access=0 WHERE token=?", (keeper_token, loser_token))
    if shared:
        set_link_access(conn, "document", keeper_id, True)


def adopt_share_tokens(conn: sqlite3.Connection, rows: list, *, now: float | None = None) -> None:
    """Take the rows of an old ``doc_shares`` table into the registry. A row is ``(token, document_id, created_at, expires_at)``.

    For each document, the newest valid token becomes its address token with link access 1, and its older valid tokens become aliases. A document that has an address token keeps it and gets link access 1: then each valid token becomes an alias. Thus each share link that worked before still opens. A token that is expired, that does not have the format, that the registry has, or whose document does not exist is not taken.
    """
    current = time.time() if now is None else now
    valid = [r for r in rows if r[3] is None or float(r[3]) > current]
    for token, document_id, _created_at, _expires_at in sorted(valid, key=lambda r: float(r[2] or 0), reverse=True):
        token = str(token)
        if not plausible_token(token) or conn.execute("SELECT 1 FROM permalinks WHERE token=?", (token,)).fetchone():
            continue
        document = conn.execute("SELECT kb_id FROM documents WHERE id=?", (str(document_id),)).fetchone()
        if document is None:
            continue
        kb_id = int(document[0])
        main = token_of(conn, "document", str(document_id))
        if main is None:
            _insert(conn, kind="document", target=str(document_id), kb_id=kb_id, account_id=None, identity=None, link_access=1, now=current, token=token)
        else:
            conn.execute(
                "INSERT INTO permalinks(token, kind, kb_id, target, link_access, alias_of, created_at) VALUES (?, 'document', ?, ?, 0, ?, ?)",
                (token, kb_id, str(document_id), main, current),
            )
            set_link_access(conn, "document", str(document_id), True)


def migrate(conn: sqlite3.Connection) -> None:
    """Schema v43. Make the registry and the rows of the documents and entities that exist. Idempotent.

    The share tokens of ``doc_shares`` go into the registry first (``adopt_share_tokens``). The caller removes the table after this function.
    """
    ensure_schema(conn)
    now = time.time()
    tables = {str(r[0]) for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    if "documents" not in tables:
        return
    if "doc_shares" in tables:
        adopt_share_tokens(conn, conn.execute("SELECT token, document_id, created_at, expires_at FROM doc_shares").fetchall(), now=now)
    for document_id, kb_id in conn.execute("SELECT d.id, d.kb_id FROM documents d WHERE NOT EXISTS (SELECT 1 FROM permalinks p WHERE p.kind='document' AND p.target=d.id AND p.alias_of IS NULL)").fetchall():
        _insert(conn, kind="document", target=str(document_id), kb_id=int(kb_id), account_id=None, identity=None, link_access=0, now=now)
    if "entities" in tables:
        for entity_id, kb_id, entity_type, norm_name in conn.execute("SELECT e.id, e.kb_id, e.type, e.norm_name FROM entities e WHERE NOT EXISTS (SELECT 1 FROM permalinks p WHERE p.kind='entity' AND p.target=e.id AND p.alias_of IS NULL)").fetchall():
            _insert(conn, kind="entity", target=str(entity_id), kb_id=int(kb_id), account_id=None, identity=entity_identity(str(entity_type), str(norm_name)), link_access=0, now=now)
