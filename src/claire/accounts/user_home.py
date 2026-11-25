"""User Home (``/@{username}/``) ActivityPub profile, always-public KB summary, and public outbox timeline builders."""

from __future__ import annotations

import html
import sqlite3
import time
from typing import Any

import nh3

from ..store import accounts as accounts_store
from ..store import db as dbm
from ..store import federation as federation_store
from ..store import federation_client as client_store
from ..store import federation_notes as notes_store
from ..store import federation_quotes as quotes_store
from ..store import permalinks
from ..store.knowledge_base import KnowledgeBaseInfo

__all__ = [
    "TIMELINE_DEFAULT_LIMIT",
    "TIMELINE_MAX_LIMIT",
    "build_user_home_profile",
    "build_user_home_timeline",
]

TIMELINE_DEFAULT_LIMIT = 20
TIMELINE_MAX_LIMIT = 50
_ARTICLE_SUMMARY_MAX_CHARS = 500


def _is_actor(account: accounts_store.Account | None) -> bool:
    return account is not None and account.status == "active" and bool(account.username)


def _note_permalink(conn: sqlite3.Connection, origin: str, public_id: int, account_id: int) -> str | None:
    """The permalink of a note. A note that has no address token gets one, with a short write connection to the same database."""
    token = permalinks.token_of(conn, "note", str(public_id))
    if token is None:
        db_file = permalinks.database_file(conn)
        token = None if db_file is None else permalinks.ensure_note_token(db_file, public_id, account_id)
    return None if token is None else permalinks.permalink_url(origin, token)


def _article_excerpt(raw: str | None) -> str:
    collapsed = " ".join((raw or "").split())
    if len(collapsed) <= _ARTICLE_SUMMARY_MAX_CHARS:
        return collapsed
    cut = collapsed[:_ARTICLE_SUMMARY_MAX_CHARS].rsplit(" ", 1)[0]
    return (cut or collapsed[:_ARTICLE_SUMMARY_MAX_CHARS]) + "…"


def build_user_home_profile(
    conn: sqlite3.Connection,
    client_conn: sqlite3.Connection | None,
    *,
    account: accounts_store.Account,
    personal_kb: KnowledgeBaseInfo | None,
    origin: str,
    handle_host: str,
) -> dict[str, Any]:
    """Build the public ActivityPub actor profile and always-public personal KB summary."""
    actor: dict[str, Any] | None = None
    if _is_actor(account):
        username = str(account.username)
        followers_count = federation_store.count_followers(conn, account.id)
        following_count = client_store.count_accepted_following(client_conn, account.id) if client_conn is not None else 0
        public_articles = federation_store.count_objects(conn, account.id, public_themes_only=True)
        own_notes = notes_store.count_notes(client_conn, account.id) if client_conn is not None else 0
        actor = {
            "public_id": str(account.public_id),
            "username": username,
            "display_name": account.display_name or username,
            "handle": f"@{username}@{handle_host}",
            "actor_url": f"{origin}/ap/actors/{account.public_id}",
            "profile_url": f"{origin}/@{username}/",
            "summary": account.summary or "",
            "activated_at": account.activated_at,
            "discoverable": bool(account.profile_public),
            "manually_approves_followers": False,
            "followers_count": followers_count,
            "following_count": following_count,
            "outbox_count": public_articles + own_notes,
        }

    kb_summary: dict[str, Any] | None = None
    if personal_kb is not None:
        counts = dbm.counts(conn, include_hidden=False, kb_id=personal_kb.id)
        last_row = conn.execute(
            "SELECT MAX(fetched_at) FROM documents WHERE kb_id=? AND COALESCE(hidden, 0)=0",
            (personal_kb.id,),
        ).fetchone()
        last_ingest_at = float(last_row[0]) if last_row and last_row[0] is not None else None
        kb_summary = {
            "label": personal_kb.label,
            "is_public": bool(personal_kb.is_public),
            "documents": int(counts.get("documents", 0)),
            "entities": int(counts.get("entities", 0)),
            "relations": int(counts.get("relations", 0)),
            "last_ingest_at": last_ingest_at,
        }

    return {"actor": actor, "kb_summary": kb_summary}


def build_user_home_timeline(
    conn: sqlite3.Connection,
    client_conn: sqlite3.Connection | None,
    *,
    account: accounts_store.Account,
    origin: str,
    limit: int = TIMELINE_DEFAULT_LIMIT,
    max_id: int | None = None,
    kind: str | None = None,
    now: float | None = None,
) -> dict[str, Any]:
    """Build one cursor page of the merged public ActivityPub timeline (own Notes + public-KB Articles)."""
    if not _is_actor(account):
        return {"items": [], "total": 0, "next_max_id": None}

    clamped_limit = max(1, min(TIMELINE_MAX_LIMIT, int(limit)))
    norm_kind = kind if kind in ("note", "article") else "all"
    current_time = time.time() if now is None else now

    articles: list[federation_store.FederationObject] = []
    article_total = 0
    if norm_kind in ("all", "article"):
        article_total = federation_store.count_objects(conn, account.id, public_themes_only=True)
        articles = federation_store.list_objects(
            conn,
            account.id,
            limit=clamped_limit + 1,
            max_id=max_id,
            public_themes_only=True,
        )

    notes: list[notes_store.Note] = []
    note_total = 0
    if client_conn is not None and norm_kind in ("all", "note"):
        note_total = notes_store.count_notes(client_conn, account.id)
        notes = notes_store.list_notes(
            client_conn,
            account.id,
            limit=clamped_limit + 1,
            max_id=max_id,
        )

    candidates: list[tuple[int, str, Any]] = []
    for obj in articles:
        candidates.append((obj.public_id, "Article", obj))
    for note in notes:
        candidates.append((note.public_id, "Note", note))
    candidates.sort(key=lambda entry: entry[0], reverse=True)

    rendered_items: list[dict[str, Any]] = []
    public_ids_for_counts: list[int] = []
    has_more = False

    for public_id, entry_kind, raw_obj in candidates:
        if len(rendered_items) >= clamped_limit:
            has_more = True
            break
        if entry_kind == "Article":
            doc = dbm.get_document_row(conn, raw_obj.document_id, kb_id=raw_obj.kb_id)
            if doc is None or doc["hidden"]:
                continue
            address_token = permalinks.token_of(conn, "document", raw_obj.document_id)
            share_url = None if address_token is None else permalinks.permalink_url(origin, address_token)
            title = str(doc["title"] or "")
            excerpt = _article_excerpt(dbm.latest_extraction_summary(conn, raw_obj.document_id, kb_id=raw_obj.kb_id))
            summary_text = excerpt or title
            content_html = nh3.clean(f"<p>{html.escape(summary_text)}</p>") if summary_text else ""
            public_ids_for_counts.append(public_id)
            rendered_items.append(
                {
                    "id": str(public_id),
                    "kind": "Article",
                    "object_url": f"{origin}/ap/objects/{public_id}",
                    "title": title,
                    "summary_text": summary_text,
                    "content_html": content_html,
                    "share_url": share_url,
                    "document_id": str(raw_obj.document_id),
                    "source_type": str(doc["source_type"] or ""),
                    "published_at": raw_obj.published_at,
                    "updated_at": raw_obj.updated_at,
                    "edited_at": None,
                    "sensitive": False,
                    "cw_summary": None,
                    "language": None,
                    "tags": [],
                    "in_reply_to": None,
                    "quote_policy": federation_store.DOCUMENT_QUOTE_POLICY,
                    "quote": None,
                    "likes": 0,
                    "announces": 0,
                    "quotes": 0,
                }
            )
        else:
            public_ids_for_counts.append(public_id)
            rendered_items.append(
                {
                    "id": str(public_id),
                    "kind": "Note",
                    "object_url": f"{origin}/ap/objects/{public_id}",
                    "title": None,
                    "summary_text": None,
                    "content_html": nh3.clean(raw_obj.content_html),
                    # The permalink of the note: the same field and the same form as for a document (PERMALINK_UNIFICATION_DESIGN.md U10).
                    "share_url": _note_permalink(conn, origin, public_id, account.id),
                    "document_id": None,
                    "source_type": None,
                    "published_at": raw_obj.published_at,
                    "updated_at": raw_obj.updated_at,
                    "edited_at": raw_obj.edited_at,
                    "sensitive": bool(raw_obj.sensitive),
                    "cw_summary": raw_obj.summary,
                    "language": raw_obj.language,
                    "tags": list(raw_obj.tags),
                    "in_reply_to": raw_obj.in_reply_to_iri,
                    "quote_policy": raw_obj.quote_policy,
                    "quote": None if raw_obj.quote_of_iri is None else {"iri": raw_obj.quote_of_iri, "state": raw_obj.quote_state},
                    "likes": 0,
                    "announces": 0,
                    "quotes": 0,
                }
            )

    if client_conn is not None and public_ids_for_counts:
        reactions = client_store.count_object_reactions(client_conn, public_ids_for_counts)
        quotes = quotes_store.count_approved_quotes(client_conn, public_ids_for_counts)
        for item in rendered_items:
            pid = int(item["id"])
            rx = reactions.get(pid, {"like": 0, "announce": 0})
            item["likes"] = int(rx.get("like", 0))
            item["announces"] = int(rx.get("announce", 0))
            item["quotes"] = int(quotes.get(pid, 0))

    next_max_id = rendered_items[-1]["id"] if has_more and rendered_items else None
    return {
        "items": rendered_items,
        "total": article_total + note_total,
        "next_max_id": next_max_id,
    }
