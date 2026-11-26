"""The tracked item of a Support Bundle: a note or a document that a person names with its link.

- ``resolve_owned_target`` finds the item of a link inside the items of one account (the account-scope bundle). ``resolve_note_link`` finds a note of any account (the server-wide bundle; its documents use the document target resolver of ``support_bundle``).
- ``document_trail`` collects the extraction trail of a document: the stored extractions with the model of each, the model that the item view shows, the MCP calls of the document (``mcp_telemetry`` with the columns ``kb_id``, ``document_id``, ``detail``), the federation publication, and the findings.
- ``note_trail`` collects the delivery trail of a note: the note state, the client outbox tasks of the note, the reactions, the quote state, and the findings.
- No member has source text, a summary, note text, a remote address, a claim token, or a key.
"""

from __future__ import annotations

import json
import sqlite3
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from ..core import ids
from ..store import db as dbm
from ..store import permalinks
from ..store.telemetry import query_mcp_trail

MAX_EXTRACTIONS = 50
MAX_MCP_CALLS = 500
MAX_TASKS = 100
# A client outbox task of a note that waits longer than this is a finding.
PENDING_TASK_SECONDS = 600.0
# The keys of ``meta.client_extraction`` that a bundle copies. ``expected_content_hash`` is a hash of the source and not a secret.
_PROVENANCE_KEYS = ("extraction_origin", "client_id", "oauth_client_id", "model", "schema_version", "prompt_version", "prompt_template", "template_version", "expected_content_hash", "committed_at")


class SupportTargetNotFound(ValueError):
    """The link does not name a note or a document of the caller. The message can go to the person."""


@dataclass(frozen=True)
class TrackedTarget:
    """One resolved item. ``kind`` is ``document`` or ``note``."""

    kind: str
    matched_by: str
    link: str | None
    document_id: str | None = None
    kb_id: int | None = None
    note_public_id: int | None = None
    account_id: int | None = None

    def resolution(self, input_target: str) -> dict[str, Any]:
        out: dict[str, Any] = {"input_target": input_target, "resolution_status": "resolved", "kind": self.kind, "matched_by": self.matched_by, "link": self.link}
        if self.kind == "document":
            out["document_id"] = self.document_id
        else:
            out["note"] = ids.format_public_id(int(self.note_public_id or 0))
        return out


def _iso(value: Any) -> str | None:
    if value is None:
        return None
    try:
        return datetime.fromtimestamp(float(value), timezone.utc).isoformat()
    except (TypeError, ValueError, OverflowError):
        return None


def _finding(code: str, message: str) -> dict[str, str]:
    return {"code": code, "message": message}


def _origin(settings: Any) -> str:
    return str(getattr(settings, "public_url", "") or "").rstrip("/")


def _address(settings: Any, token: str | None) -> str | None:
    if not token:
        return None
    origin = _origin(settings)
    return permalinks.permalink_url(origin, token) if origin else permalinks.permalink_path(token)


def permalink_token_of(raw: str) -> str | None:
    """The address token of a Silvercord link (absolute, path, or the bare token). None for other text."""
    text = (raw or "").strip()
    if permalinks.PERMALINK_PATH_PREFIX in text:
        token = text.split(permalinks.PERMALINK_PATH_PREFIX, 1)[1].split("/", 1)[0].split("?", 1)[0].split("#", 1)[0]
        return token if permalinks.plausible_token(token) else None
    return text if permalinks.plausible_token(text) else None


def _tables(conn: sqlite3.Connection) -> set[str]:
    return {str(r[0]) for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}


def _note_exists(settings: Any, *, account_id: int, public_id: int) -> bool:
    from ..store.client_db import client_db_path

    path = client_db_path(settings.db_file)
    if not path.is_file():
        return False
    conn = dbm.connect_existing(path, readonly=True)
    try:
        if "federation_notes" not in _tables(conn):
            return False
        return conn.execute("SELECT 1 FROM federation_notes WHERE public_id=? AND account_id=?", (int(public_id), int(account_id))).fetchone() is not None
    finally:
        conn.close()


def _target_of_permalink(settings: Any, conn: sqlite3.Connection, token: str) -> TrackedTarget | None:
    found = permalinks.resolve(conn, token)
    if found is None or found.kind not in permalinks.ACTIVITYPUB_KINDS:
        return None
    link = _address(settings, found.token)
    if found.kind == "document":
        row = conn.execute("SELECT id, kb_id FROM documents WHERE id=?", (found.target,)).fetchone()
        if row is None:
            return None
        return TrackedTarget(kind="document", matched_by="permalink", link=link, document_id=str(row[0]), kb_id=int(row[1]))
    try:
        public_id = int(found.target)
    except ValueError:
        return None
    if found.account_id is None or not _note_exists(settings, account_id=int(found.account_id), public_id=public_id):
        return None
    return TrackedTarget(kind="note", matched_by="permalink", link=link, note_public_id=public_id, account_id=int(found.account_id))


def resolve_owned_target(settings: Any, conn: sqlite3.Connection, raw: str, *, account_id: int, kb_ids: list[int]) -> TrackedTarget:
    """The note or document of a link inside one account: a Silvercord link (or its token), a document ID, or the source URL of a document. Raises ``SupportTargetNotFound`` for each other case, also for an item of another account."""
    text = (raw or "").strip()
    token = permalink_token_of(text)
    if token is not None:
        target = _target_of_permalink(settings, conn, token)
        if target is None:
            raise SupportTargetNotFound("The link does not name a note or a document.")
        if target.kind == "document" and target.kb_id not in kb_ids:
            raise SupportTargetNotFound("The document is not in a knowledge base of this account.")
        if target.kind == "note" and target.account_id != int(account_id):
            raise SupportTargetNotFound("The note is not a note of this account.")
        return target
    if text and kb_ids:
        marks = ",".join("?" for _ in kb_ids)
        row = conn.execute(f"SELECT id, kb_id FROM documents WHERE kb_id IN ({marks}) AND (id = ? OR url = ? OR canonical_url = ?) ORDER BY id LIMIT 1", (*kb_ids, text, text, text)).fetchone()
        if row is not None:
            doc_token = permalinks.token_of(conn, "document", str(row[0]))
            return TrackedTarget(kind="document", matched_by="document_id" if str(row[0]) == text else "source_url", link=_address(settings, doc_token), document_id=str(row[0]), kb_id=int(row[1]))
    raise SupportTargetNotFound("The link does not name a note or a document of this account.")


def resolve_note_link(settings: Any, conn: sqlite3.Connection, raw: str) -> TrackedTarget | None:
    """A note of any account from its Silvercord link (the server-wide bundle). None when the link is not the link of a note."""
    token = permalink_token_of(raw)
    if token is None:
        return None
    target = _target_of_permalink(settings, conn, token)
    return target if target is not None and target.kind == "note" else None


def _badge_label(provider: str | None, model: str | None) -> str | None:
    """The text of the model badge of the item view (``reader.js``): the model, else the provider unless the provider is ``agent_client``."""
    if model:
        return model
    if provider and provider != "agent_client":
        return provider
    return None


def _extraction_rows(conn: sqlite3.Connection, *, kb_id: int, document_id: str) -> list[dict[str, Any]]:
    from ..ingest.client_extraction import resolve_oauth_client_name

    out: list[dict[str, Any]] = []
    names: dict[str, str | None] = {}
    rows = conn.execute(
        "SELECT id, provider, model, prompt_version, raw_response, created_at FROM extractions WHERE document_id=? AND kb_id=? ORDER BY id DESC LIMIT ?",
        (document_id, int(kb_id), MAX_EXTRACTIONS),
    ).fetchall()
    for row in rows:
        try:
            raw = json.loads(row[4] or "{}")
        except ValueError:
            raw = {}
        if not isinstance(raw, dict):
            raw = {}
        oauth_client = raw.get("oauth_client_id") if isinstance(raw.get("oauth_client_id"), str) else None
        if oauth_client and oauth_client not in names:
            names[oauth_client] = resolve_oauth_client_name(conn, oauth_client)
        stored_model = row[2] or None
        out.append(
            {
                "extraction_id": int(row[0]),
                "created_at": _iso(row[5]),
                "provider": row[1],
                "model": stored_model,
                "model_recorded": bool(stored_model and str(stored_model).strip()),
                "prompt_version": row[3],
                "extraction_origin": raw.get("extraction_origin"),
                "client_id": raw.get("client_id"),
                "oauth_client_id": oauth_client,
                "oauth_client_name": names.get(oauth_client) if oauth_client else None,
                "schema_version": raw.get("schema_version"),
                "model_in_response": raw.get("model") if "model" in raw else "absent",
            }
        )
    return out


def _mcp_calls(settings: Any, *, kb_id: int, document_id: str) -> list[dict[str, Any]]:
    calls = []
    for row in query_mcp_trail(settings.data_dir, kb_id=kb_id, document_id=document_id, limit=MAX_MCP_CALLS):
        calls.append(
            {
                "at": _iso(row.get("timestamp")),
                "tool": row.get("tool_name"),
                "status": row.get("status"),
                "client_id": row.get("client_id"),
                "duration_ms": row.get("duration_ms"),
                "response_bytes": row.get("response_bytes"),
                "error_message": (str(row.get("error_message"))[:300] if row.get("error_message") else None),
                "detail": row.get("detail") if isinstance(row.get("detail"), dict) else {},
            }
        )
    return calls


def _document_federation(conn: sqlite3.Connection, *, kb_id: int, document_id: str) -> dict[str, Any]:
    tables = _tables(conn)
    if "federation_objects" not in tables:
        return {"published": False}
    row = conn.execute("SELECT public_id, published_at, updated_at FROM federation_objects WHERE kb_id=? AND document_id=?", (int(kb_id), document_id)).fetchone()
    if row is None:
        return {"published": False}
    tasks = []
    if "federation_outbox" in tables:
        for task in conn.execute(
            "SELECT kind, status, error_code, created_at, handed_off_at FROM federation_outbox WHERE object_public_id=? ORDER BY id DESC LIMIT ?",
            (int(row[0]), MAX_TASKS),
        ):
            tasks.append({"kind": task[0], "status": task[1], "error_code": task[2], "created_at": _iso(task[3]), "handed_off_at": _iso(task[4])})
    return {"published": True, "object": ids.format_public_id(int(row[0])), "published_at": _iso(row[1]), "updated_at": _iso(row[2]), "outbox_tasks": tasks}


def _document_findings(document: dict[str, Any], extractions: list[dict[str, Any]], calls: list[dict[str, Any]]) -> list[dict[str, str]]:
    findings: list[dict[str, str]] = []
    latest = extractions[0] if extractions else None
    if latest is None:
        findings.append(_finding("NO_EXTRACTION", "The document has no stored extraction."))
    elif latest.get("provider") == "agent_client" and not latest.get("model_recorded"):
        findings.append(_finding("EXTRACTION_MODEL_MISSING", "The latest extraction came from an agent client without a model name; the item view shows no model badge."))
    commits = [c for c in calls if c.get("tool") == "extract_submit" and c["detail"].get("dry_run") is False]
    if any(c["detail"].get("model_field") in ("absent", "empty") for c in commits):
        findings.append(_finding("SUBMIT_WITHOUT_MODEL", "An extract_submit commit had no payload.model."))
    ignored = [c for c in calls if c.get("tool") == "extract_submit" and c["detail"].get("dry_run") is True and "model_missing" in (c["detail"].get("warnings") or [])]
    if ignored and any(c["detail"].get("model_field") != "set" for c in commits):
        findings.append(_finding("MODEL_WARNING_NOT_ADDRESSED", "A dry run gave the warning model_missing and the client committed without a model."))
    if latest is not None and latest.get("provider") == "agent_client" and not calls:
        findings.append(_finding("MCP_TRAIL_ABSENT", "No MCP call of this document is recorded (the calls are older than the trail columns, or older than the telemetry retention)."))
    if document.get("extraction_status") == "pending" and document.get("lease_expires_at") is None:
        findings.append(_finding("PENDING_WITHOUT_CLAIM", "The document waits for extraction and no client holds a claim."))
    return findings


def document_trail(settings: Any, conn: sqlite3.Connection, *, kb_id: int, document_id: str) -> dict[str, Any]:
    """The extraction trail of one document (see the module text). ``conn`` is a read connection of ``claire.db``."""
    row = conn.execute(
        "SELECT id, kb_id, extraction_status, claimed_by_client_id, lease_expires_at, raw_chars, fetched_at, hidden, meta FROM documents WHERE id=? AND kb_id=?",
        (document_id, int(kb_id)),
    ).fetchone()
    if row is None:
        return {"document": None, "findings": [_finding("DOCUMENT_ABSENT", "The document does not exist now.")]}
    try:
        meta = json.loads(row[8] or "{}")
    except ValueError:
        meta = {}
    provenance_raw = meta.get("client_extraction") if isinstance(meta, dict) else None
    provenance = {k: provenance_raw.get(k) for k in _PROVENANCE_KEYS if k in provenance_raw} if isinstance(provenance_raw, dict) else None
    if provenance and provenance.get("committed_at") is not None:
        provenance["committed_at"] = _iso(provenance["committed_at"])
    document = {
        "id": str(row[0]),
        "link": _address(settings, permalinks.token_of(conn, "document", str(row[0]))),
        "extraction_status": row[2],
        "extraction_origin": meta.get("extraction_origin") if isinstance(meta, dict) else None,
        "claimed_by_client_id": row[3],
        "lease_expires_at": _iso(row[4]),
        "raw_chars": row[5],
        "fetched_at": _iso(row[6]),
        "hidden": bool(row[7]),
    }
    provider, model = dbm.latest_extraction_model(conn, str(row[0]), kb_id=int(kb_id))
    extractions = _extraction_rows(conn, kb_id=int(kb_id), document_id=str(row[0]))
    calls = _mcp_calls(settings, kb_id=int(kb_id), document_id=str(row[0]))
    return {
        "document": document,
        "item_view": {"extraction_provider": provider, "extraction_model": model, "model_badge": _badge_label(provider, model)},
        "client_extraction": provenance,
        "extractions": extractions,
        "mcp_calls": calls,
        "federation": _document_federation(conn, kb_id=int(kb_id), document_id=str(row[0])),
        "findings": _document_findings(document, extractions, calls),
    }


def note_trail(settings: Any, *, account_id: int, public_id: int, now: float | None = None) -> dict[str, Any]:
    """The delivery trail of one note of one account (see the module text)."""
    from ..store.client_db import client_db_path

    when = time.time() if now is None else now
    path = client_db_path(settings.db_file)
    if not Path(path).is_file():
        return {"note": None, "findings": [_finding("CLIENT_DB_ABSENT", "client.db does not exist.")]}
    conn = dbm.connect_existing(path, readonly=True)
    try:
        tables = _tables(conn)
        if "federation_notes" not in tables:
            return {"note": None, "findings": [_finding("NOTE_TABLES_ABSENT", "client.db has no note table.")]}
        columns = {str(r[1]) for r in conn.execute("PRAGMA table_info(federation_notes)")}
        quote_cols = ", quote_state, quote_policy, quote_of_iri IS NOT NULL" if {"quote_state", "quote_policy", "quote_of_iri"} <= columns else ", NULL, NULL, 0"
        row = conn.execute(
            "SELECT public_id, published_at, updated_at, edited_at, deleted_at, in_reply_to_iri IS NOT NULL, audience_iri IS NOT NULL, sensitive, language, length(content_text), mentions_json, tags_json"
            + quote_cols
            + " FROM federation_notes WHERE public_id=? AND account_id=?",
            (int(public_id), int(account_id)),
        ).fetchone()
        if row is None:
            return {"note": None, "findings": [_finding("NOTE_ABSENT", "The note does not exist now.")]}

        def _count(raw: Any) -> int:
            try:
                value = json.loads(raw or "[]")
            except ValueError:
                return 0
            return len(value) if isinstance(value, list) else 0

        note = {
            "note": ids.format_public_id(int(row[0])),
            "published_at": _iso(row[1]),
            "updated_at": _iso(row[2]),
            "edited_at": _iso(row[3]),
            "deleted_at": _iso(row[4]),
            "is_reply": bool(row[5]),
            "has_audience": bool(row[6]),
            "sensitive": bool(row[7]),
            "language": row[8],
            "text_chars": int(row[9] or 0),
            "mentions": _count(row[10]),
            "tags": _count(row[11]),
            "quote_state": row[12],
            "quote_policy": row[13],
            "is_quote": bool(row[14]),
        }
        tasks = []
        if "federation_client_outbox" in tables:
            for task in conn.execute(
                "SELECT kind, status, error_code, created_at, handed_off_at FROM federation_client_outbox WHERE account_id=? AND ref_id=? ORDER BY id DESC LIMIT ?",
                (int(account_id), int(public_id), MAX_TASKS),
            ):
                tasks.append({"kind": task[0], "status": task[1], "error_code": task[2], "created_at": _iso(task[3]), "handed_off_at": _iso(task[4]), "_created": task[3]})
        reactions: dict[str, int] = {}
        if "federation_object_reactions" in tables:
            for kind, number in conn.execute("SELECT kind, COUNT(*) FROM federation_object_reactions WHERE object_public_id=? GROUP BY kind ORDER BY kind", (int(public_id),)):
                reactions[str(kind)] = int(number)
        quote_requests: dict[str, int] = {}
        if "federation_quote_authorizations" in tables:
            for state, number in conn.execute("SELECT state, COUNT(*) FROM federation_quote_authorizations WHERE object_public_id=? GROUP BY state ORDER BY state", (int(public_id),)):
                quote_requests[str(state)] = int(number)
    finally:
        conn.close()

    findings: list[dict[str, str]] = []
    if note["deleted_at"]:
        findings.append(_finding("NOTE_DELETED", "The note is deleted."))
    if any(t["status"] == "rejected" for t in tasks):
        findings.append(_finding("CLIENT_TASK_REJECTED", "A delivery task of the note was rejected."))
    if any(t["status"] == "pending" and t["_created"] is not None and when - float(t["_created"]) > PENDING_TASK_SECONDS for t in tasks):
        findings.append(_finding("CLIENT_TASK_PENDING", "A delivery task of the note waits for more than 10 minutes."))
    if not tasks:
        findings.append(_finding("NO_CLIENT_TASK", "The note has no delivery task."))
    for task in tasks:
        task.pop("_created", None)
    token = None
    claire = dbm.connect_existing(settings.db_file, readonly=True)
    try:
        token = permalinks.token_of(claire, "note", int(public_id))
    finally:
        claire.close()
    note["link"] = _address(settings, token)
    return {"note": note, "client_tasks": tasks, "reactions": reactions, "quote_requests": quote_requests, "findings": findings}


__all__ = [
    "SupportTargetNotFound",
    "TrackedTarget",
    "document_trail",
    "note_trail",
    "permalink_token_of",
    "resolve_note_link",
    "resolve_owned_target",
]
