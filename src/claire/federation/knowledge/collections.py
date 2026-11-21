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

from ...collaboration import theme_judgement
from ...core import ids
from ...store import themes as themes_store
from . import access, remote_kbs, vocabulary

__all__ = ["KB_PAGE_SIZE", "THEMES_PATH", "kb_collection_page", "kb_collection_root", "knowledge_base_iris", "knowledge_base_object", "themes_collection", "visible_graph_counts"]

KB_PAGE_SIZE = 20
THEMES_PATH = "/ap/themes"

# The shared knowledge bases (shared knowledge base design Section 11.2).
_SHARED = "is_personal=0 AND account_id IS NULL AND is_public=1 AND knowledge_type IN ('real', 'fictional') AND number IS NOT NULL"


def knowledge_base_iris(origin: str, number: int) -> dict[str, str]:
    """The IRIs of the actor of a shared knowledge base of this server (the input ``iris`` of ``vocabulary.build_knowledge_base``). ``url`` is the page of the knowledge base."""
    public = ids.format_public_id(int(number))
    actor = f"{origin}{remote_kbs.COLLECTION_PATH}/{public}"
    return {"id": actor, "url": f"{origin}/kb/{public}/", "inbox": f"{actor}/inbox", "outbox": f"{actor}/outbox", "followers": f"{actor}/followers", "documents": f"{actor}/documents"}


def knowledge_base_object(conn: sqlite3.Connection, row: sqlite3.Row | tuple, *, origin: str, signatures: dict[int, dict[str, Any]], updated: float | None) -> dict[str, Any]:
    """The ``claire:KnowledgeBase`` of one row ``(id, number, label, description, knowledge_type)``. The counts are those that each reader of the public page sees: a hidden document, and the entities and relations that only hidden documents have, do not count. ``claire:acceptsAutomatic`` is the state of the switches of this server now (``access.auto_contribution_accepted``, design Section 12.3 rule 1). ``updated``: the time of the record that the followers have (``actors.record_updated_at``), or None when no follower got it."""
    kb_id, number, label, description, knowledge_type = tuple(row)
    signature_row = signatures.get(int(kb_id))
    kb = {
        "label": str(label or ""),
        "description": str(description or ""),
        "knowledge_type": knowledge_type,
        "documents": int(conn.execute("SELECT COUNT(*) FROM documents WHERE kb_id=? AND COALESCE(hidden, 0)=0", (int(kb_id),)).fetchone()[0]),
        **dict(zip(("entities", "relations"), visible_graph_counts(conn, int(kb_id)))),
        "signature": None if signature_row is None else theme_judgement.wire_signature(signature_row),
        "accepts_automatic": access.auto_contribution_accepted(conn),
        "updated": updated,
    }
    return vocabulary.build_knowledge_base(kb, knowledge_base_iris(origin, int(number)))


# The entities of a knowledge base that a reader of its public page sees (``store.queries.graph_json`` without hidden documents): an entity with no source, or with one source that is not a hidden document. The relations between two such entities.
_VISIBLE_GRAPH_SQL = """
WITH hidden AS (SELECT id FROM documents WHERE kb_id=:kb AND hidden=1),
visible AS (
    SELECT e.id FROM entities e WHERE e.kb_id=:kb AND (
        e.sources IS NULL OR json_valid(e.sources)=0 OR json_type(e.sources)!='array' OR json_array_length(e.sources)=0
        OR EXISTS (SELECT 1 FROM json_each(e.sources) j WHERE j.value NOT IN (SELECT id FROM hidden))
    )
)
SELECT (SELECT COUNT(*) FROM visible), (SELECT COUNT(*) FROM relations r WHERE r.kb_id=:kb AND r.source_id IN (SELECT id FROM visible) AND r.target_id IN (SELECT id FROM visible))
"""


def visible_graph_counts(conn: sqlite3.Connection, kb_id: int) -> tuple[int, int]:
    """The numbers of entities and relations of a knowledge base without the graph of its hidden documents (KS5: a hidden document does not leave the server, and its graph does not count)."""
    row = conn.execute(_VISIBLE_GRAPH_SQL, {"kb": int(kb_id)}).fetchone()
    return int(row[0]), int(row[1])


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
    from . import actors  # ``actors`` imports this module

    updated = actors.record_updated_times(conn)
    page: dict[str, Any] = {
        "@context": list(vocabulary.WIRE_CONTEXT),
        "id": f"{collection}?page=true" + ("" if max_id is None else f"&max_id={ids.format_public_id(int(max_id))}"),
        "type": "OrderedCollectionPage",
        "partOf": collection,
        "orderedItems": [knowledge_base_object(conn, row, origin=origin, signatures=signatures, updated=updated.get(int(row[1]))) for row in rows],
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
