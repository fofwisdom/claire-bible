"""The collections of shared knowledge of this server in the form for another server (KNOWLEDGE_FEDERATION_DESIGN.md Sections 8.1, 9.1, and 9.2; unit KF2).

Another Claire server reads the ``/kb`` collection of this server with a signed GET (Section 8.1 rule 2). The sidecar verifies the signature and asks Claire for the document; ``access.collection_access`` decides before a function of this module is called. No function here changes data.

- The collection is at ``/ap/kb`` (``remote_kbs.COLLECTION_PATH``): the root is an ``OrderedCollection`` with ``totalItems`` and ``first``; a page is an ``OrderedCollectionPage`` with at most ``KB_PAGE_SIZE`` items, the newest knowledge base first, and ``next`` when the page is full.
- An item is the ``claire:KnowledgeBase`` of ``vocabulary.build_knowledge_base``: label, description, knowledge type, counts, signature, and the IRIs of its actor (Section 9.2). No document is in the collection (rule 1).
- Only shared knowledge bases are in the collection: no owner, public, with a knowledge type. A personal knowledge base is never in it (KD3, KS5).
- The themes collection (``/ap/themes``) has the active themes with their label, description, knowledge type, and page. A theme has no ``id`` here: themes are not exchanged, and a theme number is not an identifier outside this server (Section 8.1 rule 5, Section 8.2 rule 2).
"""

from __future__ import annotations

import sqlite3
from typing import Any

from .. import ids, theme_judgement
from ..store import themes as themes_store
from . import remote_kbs, vocabulary

__all__ = ["KB_PAGE_SIZE", "THEMES_PATH", "kb_collection_page", "kb_collection_root", "knowledge_base_iris", "knowledge_base_object", "themes_collection"]

KB_PAGE_SIZE = 20
THEMES_PATH = "/ap/themes"

# The shared knowledge bases (shared knowledge base design Section 11.2).
_SHARED = "is_personal=0 AND account_id IS NULL AND is_public=1 AND knowledge_type IN ('real', 'fictional') AND number IS NOT NULL"


def knowledge_base_iris(origin: str, number: int) -> dict[str, str]:
    """The IRIs of the actor of a shared knowledge base of this server (the input ``iris`` of ``vocabulary.build_knowledge_base``). ``url`` is the page of the knowledge base."""
    public = ids.format_public_id(int(number))
    actor = f"{origin}{remote_kbs.COLLECTION_PATH}/{public}"
    return {"id": actor, "url": f"{origin}/kb/{public}/", "inbox": f"{actor}/inbox", "outbox": f"{actor}/outbox", "followers": f"{actor}/followers", "documents": f"{actor}/documents"}


def knowledge_base_object(conn: sqlite3.Connection, row: sqlite3.Row | tuple, *, origin: str, signatures: dict[int, dict[str, Any]]) -> dict[str, Any]:
    """The ``claire:KnowledgeBase`` of one row ``(id, number, label, description, knowledge_type)``. The counts are those that each reader of the public page sees: a hidden document does not count."""
    kb_id, number, label, description, knowledge_type = tuple(row)
    signature_row = signatures.get(int(kb_id))
    kb = {
        "label": str(label or ""),
        "description": str(description or ""),
        "knowledge_type": knowledge_type,
        "documents": int(conn.execute("SELECT COUNT(*) FROM documents WHERE kb_id=? AND COALESCE(hidden, 0)=0", (int(kb_id),)).fetchone()[0]),
        "entities": int(conn.execute("SELECT COUNT(*) FROM entities WHERE kb_id=?", (int(kb_id),)).fetchone()[0]),
        "relations": int(conn.execute("SELECT COUNT(*) FROM relations WHERE kb_id=?", (int(kb_id),)).fetchone()[0]),
        "signature": None if signature_row is None else theme_judgement.wire_signature(signature_row),
    }
    return vocabulary.build_knowledge_base(kb, knowledge_base_iris(origin, int(number)))


def kb_collection_root(conn: sqlite3.Connection, *, origin: str) -> dict[str, Any]:
    """The root of the ``/kb`` collection: the number of shared knowledge bases and the address of the first page."""
    collection = f"{origin}{remote_kbs.COLLECTION_PATH}"
    total = int(conn.execute(f"SELECT COUNT(*) FROM knowledge_bases WHERE {_SHARED}").fetchone()[0])
    return {"@context": list(vocabulary.WIRE_CONTEXT), "id": collection, "type": "OrderedCollection", "totalItems": total, "first": f"{collection}?page=true"}


def kb_collection_page(conn: sqlite3.Connection, *, origin: str, max_id: int | None) -> dict[str, Any]:
    """One page of the ``/kb`` collection: the knowledge bases with a number below ``max_id`` (each one when ``max_id`` is None), the highest number first."""
    collection = f"{origin}{remote_kbs.COLLECTION_PATH}"
    sql, args = f"SELECT id, number, label, description, knowledge_type FROM knowledge_bases WHERE {_SHARED}", []
    if max_id is not None:
        sql += " AND number < ?"
        args.append(int(max_id))
    rows = conn.execute(sql + " ORDER BY number DESC LIMIT ?", (*args, KB_PAGE_SIZE)).fetchall()
    signatures = {int(row["kb_id"]): row for row in themes_store.load_signatures(conn)}
    page: dict[str, Any] = {
        "@context": list(vocabulary.WIRE_CONTEXT),
        "id": f"{collection}?page=true" + ("" if max_id is None else f"&max_id={ids.format_public_id(int(max_id))}"),
        "type": "OrderedCollectionPage",
        "partOf": collection,
        "orderedItems": [knowledge_base_object(conn, row, origin=origin, signatures=signatures) for row in rows],
    }
    if len(rows) == KB_PAGE_SIZE:
        page["next"] = f"{collection}?page=true&max_id={ids.format_public_id(int(rows[-1][1]))}"
    return page


def themes_collection(conn: sqlite3.Connection, *, origin: str) -> dict[str, Any]:
    """The active themes of this server, the highest number first. An item has no ``id``."""
    items = [
        {"type": "Collection", "name": str(theme["label"] or ""), "summary": str(theme["description"] or ""), "url": f"{origin}/themes/{ids.format_public_id(int(theme['number']))}/", vocabulary.KNOWLEDGE_TYPE: theme["knowledge_type"]}
        for theme in themes_store.list_themes(conn)
        if theme["state"] == "active"
    ]
    return {"@context": list(vocabulary.WIRE_CONTEXT), "id": f"{origin}{THEMES_PATH}", "type": "OrderedCollection", "totalItems": len(items), "orderedItems": items}
