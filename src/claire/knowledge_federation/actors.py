"""Knowledge base actors of this server (KNOWLEDGE_FEDERATION_DESIGN.md Sections 9.2, 9.3, and 10.1; unit KF3).

Each shared knowledge base of this server is a ``Group`` actor (KD5). Its IRIs are those of ``collections.knowledge_base_iris``: ``{origin}/ap/kb/{number}`` with ``/inbox``, ``/outbox``, ``/followers``, and ``/documents``. A document of the knowledge base has the IRI ``{origin}/ap/kb/{number}/documents/{contribution}``; ``{contribution}`` is the public ID of its contribution, which does not change when a revision replaces the copy.

This module owns the tables ``kf_kb_followers`` (the server actors of other servers that follow an actor), ``kf_kb_actor_state`` (the record that the followers have), and ``kf_document_announcements`` (the revisions and removals that the followers have). No function here commits.

Rules:

- A follower is the server actor of a Claire server (design Section 9.2 rule 5). The table keeps the actor IRI; the sidecar reads the inbox from the actor document at the delivery.
- The actor document is the ``claire:KnowledgeBase`` of the ``/kb`` collection with the public key of the actor. It is public while the switch ``federation`` is on: another server needs it to verify a signature.
- The collections ``followers``, ``outbox``, and ``documents`` answer only a signed request of a server actor (``access.collection_access``). ``followers`` and ``outbox`` give the number of items only.
- Only a visible document leaves the server (KS5): a ``visible`` contribution whose document is not hidden and has an extraction.
- A holder never gives a remote object as its own (design Section 10.1 rule 2): a copy of a document of another server has ``origin`` (the document that was copied), the root of that document, and its credit.
- The credit of a local document (``attributedTo``) is the actor of the contributor only when that account is a federation actor (FEDERATION.md Section 3). For each other account the field is absent: an account that is not public does not get an address for other servers.
- The root of a document whose root is on this server is the document itself (its own ``id``): the root document is in a personal knowledge base, which never leaves the server (KD3, KS5).
"""

from __future__ import annotations

import hashlib
import json
import re
import sqlite3
from typing import Any

import nh3

from .. import ids
from ..store import accounts as accounts_store
from ..store import federation as federation_store
from ..store import kb_collab
from . import collections, remote_kbs, vocabulary

__all__ = [
    "DOCUMENTS_PAGE_SIZE",
    "SECURITY_CONTEXT",
    "TABLES",
    "actor_document",
    "add_follower",
    "document_iri",
    "drop_actor",
    "knowledge_base_object",
    "document_object",
    "documents_page",
    "documents_root",
    "ensure_schema",
    "followers",
    "followers_collection",
    "followed_numbers",
    "get_follower",
    "local_number",
    "outbox_collection",
    "record_digest",
    "remove_follower",
    "shared_kb",
]

SECURITY_CONTEXT = "https://w3id.org/security/v1"
DOCUMENTS_PAGE_SIZE = 20

SCHEMA = """
CREATE TABLE IF NOT EXISTS kf_kb_followers (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    kb_number INTEGER NOT NULL,
    actor_iri TEXT NOT NULL,
    domain TEXT NOT NULL,
    follow_iri TEXT NOT NULL,
    created_at REAL NOT NULL,
    UNIQUE (kb_number, actor_iri)
);
CREATE TABLE IF NOT EXISTS kf_kb_actor_state (
    kb_number INTEGER PRIMARY KEY,
    record_digest TEXT NOT NULL,
    updated_at REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS kf_document_announcements (
    contribution_public_id INTEGER PRIMARY KEY,
    revised_at REAL,
    removed INTEGER NOT NULL DEFAULT 0
)
"""

# The columns that the code of this module reads, for each table. The migration (``store.db``) gives a table of a different form a new name and makes this one again.
TABLES: dict[str, tuple[str, ...]] = {
    "kf_kb_followers": ("id", "kb_number", "actor_iri", "domain", "follow_iri", "created_at"),
    "kf_kb_actor_state": ("kb_number", "record_digest", "updated_at"),
    "kf_document_announcements": ("contribution_public_id", "revised_at", "removed"),
}

_SHARED = "is_personal=0 AND account_id IS NULL AND is_public=1 AND knowledge_type IN ('real', 'fictional') AND number IS NOT NULL"
_HASH = re.compile(r"[0-9a-f]{64}")
# A reference of the HTML that starts at the root of this server (``href="/..."``, ``src="/..."``), but not a protocol-relative one.
_ROOT_REFERENCE = re.compile(r"""(\s(?:href|src)=["'])/(?!/)""")


def ensure_schema(conn: sqlite3.Connection) -> None:
    """Make the tables when they are not there. Idempotent; the migration of ``store.db`` calls it at each start."""
    for statement in SCHEMA.split(";"):
        if statement.strip():
            conn.execute(statement)


def shared_kb(conn: sqlite3.Connection, number: int) -> sqlite3.Row | None:
    """The row ``(id, number, label, description, knowledge_type)`` of the shared knowledge base with the number, or None. A personal knowledge base is never an actor (KD3)."""
    return conn.execute(f"SELECT id, number, label, description, knowledge_type FROM knowledge_bases WHERE number=? AND {_SHARED}", (int(number),)).fetchone()


def local_number(iri: str, origin: str) -> int | None:
    """The number of the knowledge base actor of this server with the IRI, or None when the IRI is not ``{origin}/ap/kb/{number}``."""
    prefix = f"{origin}{remote_kbs.COLLECTION_PATH}/"
    if not iri.startswith(prefix):
        return None
    try:
        return ids.parse_public_id(iri[len(prefix):])
    except ValueError:
        return None


def document_iri(origin: str, number: int, contribution_public_id: int) -> str:
    return f"{collections.knowledge_base_iris(origin, int(number))['documents']}/{ids.format_public_id(int(contribution_public_id))}"


# --- followers -------------------------------------------------------------------------------------


def get_follower(conn: sqlite3.Connection, number: int, actor_iri: str) -> dict[str, Any] | None:
    row = conn.execute("SELECT id, kb_number, actor_iri, domain, follow_iri, created_at FROM kf_kb_followers WHERE kb_number=? AND actor_iri=?", (int(number), actor_iri)).fetchone()
    return None if row is None else dict(zip(TABLES["kf_kb_followers"], tuple(row)))


def add_follower(conn: sqlite3.Connection, number: int, *, actor_iri: str, domain: str, follow_iri: str, now: float) -> bool:
    """Store the follow of one server actor. Return True for a new follower. A second ``Follow`` of the same actor keeps the first time and takes the new activity IRI."""
    current = get_follower(conn, number, actor_iri)
    if current is not None:
        conn.execute("UPDATE kf_kb_followers SET follow_iri=? WHERE id=?", (follow_iri, current["id"]))
        return False
    conn.execute("INSERT INTO kf_kb_followers (kb_number, actor_iri, domain, follow_iri, created_at) VALUES (?, ?, ?, ?, ?)", (int(number), actor_iri, domain, follow_iri, now))
    return True


def remove_follower(conn: sqlite3.Connection, number: int, actor_iri: str) -> bool:
    return conn.execute("DELETE FROM kf_kb_followers WHERE kb_number=? AND actor_iri=?", (int(number), actor_iri)).rowcount > 0


def followers(conn: sqlite3.Connection, number: int, *, deliverable_only: bool = False) -> list[dict[str, Any]]:
    """The followers of one actor, the oldest first. ``deliverable_only``: without the followers on a blocked domain (design Section 6.3)."""
    rows = [dict(zip(TABLES["kf_kb_followers"], tuple(row))) for row in conn.execute("SELECT id, kb_number, actor_iri, domain, follow_iri, created_at FROM kf_kb_followers WHERE kb_number=? ORDER BY id", (int(number),)).fetchall()]
    if deliverable_only:
        rows = [row for row in rows if federation_store.blocking_domain(conn, row["domain"]) is None]
    return rows


def followed_numbers(conn: sqlite3.Connection) -> list[int]:
    """The numbers of the knowledge base actors that have a follower."""
    return [int(row[0]) for row in conn.execute("SELECT DISTINCT kb_number FROM kf_kb_followers ORDER BY kb_number").fetchall()]


def drop_actor(conn: sqlite3.Connection, number: int) -> None:
    """Delete the followers and the state of an actor whose knowledge base is gone. The rows of ``kf_document_announcements`` stay: their contributions are gone, and the table has no knowledge base column."""
    conn.execute("DELETE FROM kf_kb_followers WHERE kb_number=?", (int(number),))
    conn.execute("DELETE FROM kf_kb_actor_state WHERE kb_number=?", (int(number),))


# --- actor document and collections ----------------------------------------------------------------


def _object(conn: sqlite3.Connection, row: sqlite3.Row, origin: str) -> dict[str, Any]:
    from ..store import themes as themes_store

    signatures = {int(sig["kb_id"]): sig for sig in themes_store.load_signatures(conn) if int(sig["kb_id"]) == int(row["id"])}
    return collections.knowledge_base_object(conn, row, origin=origin, signatures=signatures)


def knowledge_base_object(conn: sqlite3.Connection, number: int, *, origin: str) -> dict[str, Any] | None:
    """The ``claire:KnowledgeBase`` of one shared knowledge base of this server, without ``@context``, or None."""
    row = shared_kb(conn, number)
    return None if row is None else _object(conn, row, origin)


def record_digest(obj: dict[str, Any]) -> str:
    """The digest of the part of the record whose change the followers get as an ``Update``: the label, the description, and the knowledge type. The counts and the signature change with each document; a follower gets them with its daily read of the ``/kb`` collection (design Section 8.1 rule 2)."""
    return hashlib.sha256(json.dumps([obj["name"], obj["summary"], obj[vocabulary.KNOWLEDGE_TYPE]], ensure_ascii=False).encode("utf-8")).hexdigest()


def actor_document(conn: sqlite3.Connection, number: int, *, origin: str, public_key_pem: str) -> dict[str, Any] | None:
    """The actor document of one shared knowledge base: the ``claire:KnowledgeBase`` with the public key for HTTP Signatures. None when the number is not a shared knowledge base."""
    obj = knowledge_base_object(conn, number, origin=origin)
    if obj is None:
        return None
    return {
        "@context": [*vocabulary.WIRE_CONTEXT, SECURITY_CONTEXT],
        **obj,
        # A follow of this actor needs no approval of a person: the handler of ``Follow`` answers ``Accept`` (design Section 6.1).
        "manuallyApprovesFollowers": False,
        "publicKey": {"id": f"{obj['id']}#main-key", "owner": obj["id"], "publicKeyPem": public_key_pem},
    }


def followers_collection(conn: sqlite3.Connection, number: int, *, origin: str) -> dict[str, Any]:
    """The followers collection of an actor: the number only (no address of a follower is shown)."""
    total = int(conn.execute("SELECT COUNT(*) FROM kf_kb_followers WHERE kb_number=?", (int(number),)).fetchone()[0])
    return {"@context": list(vocabulary.WIRE_CONTEXT), "id": collections.knowledge_base_iris(origin, number)["followers"], "type": "OrderedCollection", "totalItems": total}


def outbox_collection(conn: sqlite3.Connection, number: int, *, origin: str, total: int) -> dict[str, Any]:
    """The outbox collection of an actor: the number of activities that the outbox of this server keeps for it (``outbox.count_activities``). The activities go only to the followers, so the collection has no items."""
    return {"@context": list(vocabulary.WIRE_CONTEXT), "id": collections.knowledge_base_iris(origin, number)["outbox"], "type": "OrderedCollection", "totalItems": int(total)}


# --- documents -------------------------------------------------------------------------------------


def _credit(conn: sqlite3.Connection, contribution: dict[str, Any], origin: str) -> str | None:
    if contribution["remote_credit_iri"]:
        return str(contribution["remote_credit_iri"])
    if contribution["remote_source_iri"] or contribution["contributor_account_id"] is None:
        return None
    store = accounts_store.AccountStore(conn)
    if store.federation_state() != "on" or store.server_mode() != "network":
        return None
    try:
        account = store.get(int(contribution["contributor_account_id"]))
    except accounts_store.AccountNotFound:
        return None
    if account.status != "active" or not account.profile_public or not account.username:
        return None
    return f"{origin}/ap/actors/{ids.format_public_id(account.public_id)}"


def _content_html(row: sqlite3.Row, origin: str) -> str:
    """The HTML of a document for another server: the compiled HTML, or the render of the source text; then the ``nh3`` allowlist sanitizer. A reference that starts at the root of this server gets the origin, so that it does not point to the server of the reader."""
    html = str(row["detail_html"] or "")
    if not html and str(row["detail"] or "").strip():
        from ..render import render_to_html

        html = render_to_html(row["detail"], format=row["detail_format"] or "adoc") or ""
    return _ROOT_REFERENCE.sub(lambda match: f"{match.group(1)}{origin}/", nh3.clean(html))


def _wire_extraction(conn: sqlite3.Connection, kb_id: int, document_id: str) -> dict[str, Any] | None:
    """The ``claire:Extraction`` of a document from its stored extraction, or None when the document has no extraction that the wire contract can have. A relation whose source or target is not an entity of the extraction is not sent: the merge job does not use it."""
    from .. import contributions

    row = conn.execute("SELECT raw_response FROM extractions WHERE document_id=? AND kb_id=? ORDER BY id DESC LIMIT 1", (document_id, int(kb_id))).fetchone()
    if row is None:
        return None
    try:
        data = json.loads(row["raw_response"] or "")
    except ValueError:
        return None
    if not isinstance(data, dict):
        return None
    entities = [e for e in data.get("entities") or [] if isinstance(e, dict) and isinstance(e.get("name"), str) and e["name"].strip() and isinstance(e.get("type"), str) and e["type"].strip()]
    names = {e["name"] for e in entities}
    relations = [r for r in data.get("relations") or [] if isinstance(r, dict) and r.get("source") in names and r.get("target") in names]
    result = {
        "summary": data.get("summary") if isinstance(data.get("summary"), str) else "",
        "key_claims": [c for c in data.get("key_claims") or [] if isinstance(c, str)],
        "entities": [{"name": e["name"], "type": e["type"], "aliases": [a for a in e.get("aliases") or [] if isinstance(a, str)], "observations": [o for o in e.get("observations") or [] if isinstance(o, str)], "proposed_type": e.get("proposed_type") or None} for e in entities],
        "relations": [{"source": r["source"], "target": r["target"], "type": r.get("type"), "proposed_type": r.get("proposed_type") or None} for r in relations],
    }
    try:
        return vocabulary.build_extraction(result, contributions.authority_ids_of(conn, kb_id, sorted(names)))
    except vocabulary.ContractError:
        return None


def _visible_document(conn: sqlite3.Connection, kb_id: int, contribution: dict[str, Any]) -> sqlite3.Row | None:
    if contribution["state"] != "visible" or not contribution["document_id"]:
        return None
    return conn.execute(
        "SELECT id, title, canonical_url, content_hash, raw_text, detail, detail_format, detail_html FROM documents WHERE id=? AND kb_id=? AND COALESCE(hidden, 0)=0",
        (contribution["document_id"], int(kb_id)),
    ).fetchone()


def _summary(row: sqlite3.Row, contribution: dict[str, Any], iri: str) -> dict[str, Any]:
    """The fields of a document that the collection page and the full object have."""
    content_hash = str(row["content_hash"] or "")
    if _HASH.fullmatch(content_hash) is None:
        # A document from before the content hash has no value of the wire form: the hash of its text identifies its content.
        content_hash = hashlib.sha256(str(row["raw_text"] or "").encode("utf-8", "ignore")).hexdigest()
    url = str(row["canonical_url"] or "")
    return {"title": str(row["title"] or "").strip() or url or iri, "canonical_url": url if vocabulary.is_iri(url) else None, "content_hash": content_hash, "published": contribution["contributed_at"], "updated": contribution["revised_at"]}


def document_object(conn: sqlite3.Connection, kb: sqlite3.Row, contribution: dict[str, Any], *, origin: str) -> dict[str, Any] | None:
    """The ``Article`` of one visible document of a shared knowledge base of this server, with its ``claire:Extraction``, without ``@context``. None when the document does not leave the server: not visible, hidden, or without an extraction."""
    row = _visible_document(conn, int(kb["id"]), contribution)
    if row is None:
        return None
    extraction = _wire_extraction(conn, int(kb["id"]), str(row["id"]))
    if extraction is None:
        return None
    iri = document_iri(origin, int(kb["number"]), int(contribution["public_id"]))
    doc = {**_summary(row, contribution, iri), "content_html": _content_html(row, origin)}
    iris = {"id": iri, "attributed_to": _credit(conn, contribution, origin), "origin": contribution["remote_source_iri"] or None, "root": contribution["remote_root_iri"] or iri}
    try:
        return vocabulary.build_document(doc, iris, extraction)
    except vocabulary.ContractError:
        return None


def documents_root(conn: sqlite3.Connection, kb: sqlite3.Row, *, origin: str) -> dict[str, Any]:
    """The root of the documents collection of a knowledge base: the number of visible documents and the address of the first page."""
    collection = collections.knowledge_base_iris(origin, int(kb["number"]))["documents"]
    return {"@context": list(vocabulary.WIRE_CONTEXT), "id": collection, "type": "OrderedCollection", "totalItems": kb_collab.count_visible_contributions(conn, int(kb["id"])), "first": f"{collection}?page=true"}


def documents_page(conn: sqlite3.Connection, kb: sqlite3.Row, *, origin: str, max_id: int | None) -> dict[str, Any]:
    """One page of the documents collection: the newest contribution first. An item has the metadata of a document (``id``, ``type``, ``name``, ``url``, ``claire:contentHash``, ``published``, ``updated``) and no content: a reader gets the document with its extraction from the ``id`` (P3)."""
    collection = collections.knowledge_base_iris(origin, int(kb["number"]))["documents"]
    rows = kb_collab.visible_contributions_page(conn, int(kb["id"]), limit=DOCUMENTS_PAGE_SIZE, max_id=max_id)
    items = []
    for contribution in rows:
        row = _visible_document(conn, int(kb["id"]), contribution)
        if row is None:
            continue
        iri = document_iri(origin, int(kb["number"]), int(contribution["public_id"]))
        doc = _summary(row, contribution, iri)
        item: dict[str, Any] = {"id": iri, "type": "Article", "name": doc["title"]}
        if doc["canonical_url"]:
            item["url"] = doc["canonical_url"]
        item[vocabulary.CONTENT_HASH] = doc["content_hash"]
        item["published"] = vocabulary.iso_time(doc["published"])
        if doc["updated"] is not None:
            item["updated"] = vocabulary.iso_time(doc["updated"])
        items.append(item)
    page: dict[str, Any] = {
        "@context": list(vocabulary.WIRE_CONTEXT),
        "id": f"{collection}?page=true" + ("" if max_id is None else f"&max_id={ids.format_public_id(int(max_id))}"),
        "type": "OrderedCollectionPage",
        "partOf": collection,
        "orderedItems": items,
    }
    if len(rows) == DOCUMENTS_PAGE_SIZE:
        page["next"] = f"{collection}?page=true&max_id={ids.format_public_id(int(rows[-1]['public_id']))}"
    return page
