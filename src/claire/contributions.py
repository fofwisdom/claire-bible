"""Contributions to a shared knowledge base (SHARED_KNOWLEDGE_BASES_AND_THEMES_DESIGN.md Sections 9.1, 9.4 to 9.6; unit CT-4).

A contribution is a copy of a document of a personal knowledge base in a shared knowledge base K. The copy has its own document ID, its own files, and its own graph in K.

- ``contribute_direct`` copies the document row and its latest extraction and adds the contribution and its merge job, in one transaction.
- ``run_merge_job`` builds the graph of K from the copied extraction. It makes no LLM call (CT24): there is no new extraction, no judge of same entities, and no judge of relations. Embeddings use the fixed embedding model of K and the provider of the server.
- The review actions (Section 9.6), the withdrawal (Section 9.4), and the revisions (Section 9.5) are the other functions of this module.

Facts that this unit checked in the code:
- ``extractions.raw_response`` is the JSON of ``ExtractionResult`` (summary, entities, relations), so the graph comes from it without the model.
- Files are named by the document ID (``data/images/<id>_<n>``, ``data/raw/artifacts/<id>.txt.zst``, ``data/raw/attachments/<id>/``), and a purge deletes them by that ID. Thus the copy gets copies of the files. The uploaded source file of ``raw_inbox`` is not copied.
- ``documents.content_hash`` is the hash of the fetched source text (``ingest.normalize.content_hash``). Regenerate does not change it.
"""

from __future__ import annotations

import json
import logging
import shutil
import sqlite3
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .extract.provider import ExtractionResult
from .extract.resolver import resolve_or_create
from .ontology.base import Relation, new_id
from .ontology.registry import classify_entity_type, classify_relation_type, validate_relation
from .store import db as dbm
from .store import kb_collab
from .store.vectors import EmbeddingModelMismatch, make_vector_store, read_embed_pin, resolve_embed_identity

log = logging.getLogger("claire.contributions")

MERGE_JOB = "contribution_merge"
# A job that is ``running`` for longer than this did not end (its process stopped): the next sweep takes it again.
STALE_JOB_SECONDS = 15 * 60
# Reason codes for reject and remove (design Section 9.3 rule 3).
REASON_CODES = ("off_subject", "wrong_knowledge_type", "duplicate", "low_quality", "spam", "other")
# Keys of ``documents.meta`` that are results of the work in the source knowledge base. The copy does not take them: K makes its own.
_SOURCE_ONLY_META = ("focus", "resolution_log", "heatmap_matrix", "syndication", "model_lineage")
# Columns of ``documents`` that the copy does not take from the source.
_COPY_OVERRIDES = {"seen": 1, "watch_enabled": 0, "watch_interval": None, "last_watched_at": None, "watch_reason": None, "pinned": 0}


class ContributionError(Exception):
    """A refusal with an HTTP status and a code. ``extra`` goes into the response body."""

    def __init__(self, status: int, code: str, **extra: Any) -> None:
        super().__init__(code)
        self.status = status
        self.code = code
        self.extra = extra


@dataclass(frozen=True, slots=True)
class MergeResult:
    state: str  # the state of the contribution after the job
    error: str = ""  # the code of a failure
    entities: int = 0
    relations: int = 0


# --- copy ------------------------------------------------------------------------------------------------------------


def _document_columns(conn: sqlite3.Connection) -> list[str]:
    return [str(r[1]) for r in conn.execute("PRAGMA table_info(documents)")]


def copy_document(conn: sqlite3.Connection, *, source_document_id: str, source_kb_id: int, target_kb_id: int, hidden: bool) -> str:
    """Copy one ``documents`` row and its latest ``extractions`` row into ``target_kb_id`` with a new document ID. No graph, no files, no commit.

    ``ContributionError(409, "document_not_extracted")`` when the source has no extraction.
    """
    columns = _document_columns(conn)
    row = conn.execute(f"SELECT {', '.join(columns)} FROM documents WHERE id=? AND kb_id=?", (source_document_id, int(source_kb_id))).fetchone()
    if row is None:
        raise ContributionError(404, "document_not_found")
    extraction = conn.execute(
        "SELECT provider, model, prompt_version, raw_response FROM extractions WHERE document_id=? AND kb_id=? ORDER BY id DESC LIMIT 1",
        (source_document_id, int(source_kb_id)),
    ).fetchone()
    if extraction is None or not str(extraction["raw_response"] or "").strip():
        raise ContributionError(409, "document_not_extracted")
    values = dict(zip(columns, tuple(row)))
    target_id = new_id("doc")
    try:
        meta = json.loads(values.get("meta") or "{}")
    except (TypeError, ValueError):
        meta = {}
    if not isinstance(meta, dict):
        meta = {}
    for key in _SOURCE_ONLY_META:
        meta.pop(key, None)
    values.update(_COPY_OVERRIDES)
    values.update({"id": target_id, "kb_id": int(target_kb_id), "hidden": 1 if hidden else 0, "meta": json.dumps(meta, ensure_ascii=False)})
    conn.execute(f"INSERT INTO documents ({', '.join(columns)}) VALUES ({', '.join('?' for _ in columns)})", tuple(values[c] for c in columns))
    conn.execute(
        "INSERT INTO extractions (kb_id, document_id, provider, model, prompt_version, raw_response, created_at) VALUES (?, ?, ?, ?, ?, ?, ?)",
        (int(target_kb_id), target_id, extraction["provider"], extraction["model"], extraction["prompt_version"], extraction["raw_response"], time.time()),
    )
    return target_id


def copy_document_files(conn: sqlite3.Connection, data_dir: Path, *, source_document_id: str, target_document_id: str, kb_id: int) -> int:
    """Give the copy its own files: local images, the text artifact, and the attachments. Return the count of copied files. The caller commits.

    A file that is not there is skipped: the document then shows the remote image URL, as after a failed download.
    """
    data_dir = Path(data_dir)
    copied = 0
    row = conn.execute("SELECT meta FROM documents WHERE id=? AND kb_id=?", (target_document_id, int(kb_id))).fetchone()
    try:
        meta = json.loads(row["meta"] or "{}") if row is not None else {}
    except (TypeError, ValueError):
        meta = {}
    images = meta.get("images") if isinstance(meta, dict) else None
    if isinstance(images, list):
        prefix = f"images/{source_document_id}_"
        for image in images:
            local = image.get("local") if isinstance(image, dict) else None
            if not isinstance(local, str) or not local.startswith(prefix):
                continue
            target_local = f"images/{target_document_id}_{local[len(prefix):]}"
            source_path = data_dir / local
            if source_path.is_file():
                (data_dir / "images").mkdir(parents=True, exist_ok=True)
                shutil.copyfile(source_path, data_dir / target_local)
                image["local"] = target_local
                copied += 1
            else:
                image.pop("local", None)
        conn.execute("UPDATE documents SET meta=? WHERE id=? AND kb_id=?", (json.dumps(meta, ensure_ascii=False), target_document_id, int(kb_id)))
    artifacts = data_dir / "raw" / "artifacts"
    for suffix in (".txt.zst", ".txt.gz"):
        source_path = artifacts / f"{source_document_id}{suffix}"
        if source_path.is_file():
            shutil.copyfile(source_path, artifacts / f"{target_document_id}{suffix}")
            copied += 1
    attachments = data_dir / "raw" / "attachments" / source_document_id
    if attachments.is_dir():
        shutil.copytree(attachments, data_dir / "raw" / "attachments" / target_document_id, dirs_exist_ok=True)
        copied += sum(1 for p in attachments.rglob("*") if p.is_file())
    return copied


# --- direct contribution (Section 9.1) -------------------------------------------------------------------------------


def find_duplicate(conn: sqlite3.Connection, kb_id: int, *, canonical_url: str | None, content_hash: str | None) -> str | None:
    """The ID of a document of the knowledge base with the same ``canonical_url`` or ``content_hash``, or None."""
    conditions, params = [], []
    if canonical_url:
        conditions.append("canonical_url=?")
        params.append(canonical_url)
    if content_hash:
        conditions.append("content_hash=?")
        params.append(content_hash)
    if not conditions:
        return None
    row = conn.execute(f"SELECT id FROM documents WHERE kb_id=? AND ({' OR '.join(conditions)}) ORDER BY id LIMIT 1", (int(kb_id), *params)).fetchone()
    return None if row is None else str(row[0])


def contribute_direct(
    conn: sqlite3.Connection,
    *,
    kb_id: int,
    source_kb_id: int,
    source_document_id: str,
    contributor_account_id: int,
    approved: bool,
    now: float | None = None,
    origin: str = "direct",
    pending: bool = False,
    submission_item_id: int | None = None,
) -> dict[str, Any]:
    """Copy a document of the personal knowledge base ``source_kb_id`` into the shared knowledge base ``kb_id`` as a direct contribution, with its merge job (design Section 9.1 steps 1 and 2). One transaction; the caller commits.

    ``approved``: the contributor is a reviewer of K, so the contribution needs no approval (Section 6.2 rule 2). Else ``approved_at`` stays NULL and the contributor can withdraw (CT7).
    The checks of the capability, the scope, and the state of K are of the caller (``kb_access.decide``).
    ``pending`` (a contribution to a theme by an account that is not a reviewer of K, Section 9.2 rule 5): the copy stays hidden, the state is ``pending``, and no merge job starts until a reviewer approves.
    """
    if pending and approved:
        raise ValueError("a pending contribution is not approved")
    ts = time.time() if now is None else now
    source = conn.execute("SELECT canonical_url, content_hash FROM documents WHERE id=? AND kb_id=?", (source_document_id, int(source_kb_id))).fetchone()
    if source is None:
        raise ContributionError(404, "document_not_found")
    duplicate = find_duplicate(conn, kb_id, canonical_url=source["canonical_url"], content_hash=source["content_hash"])
    if duplicate is not None:
        raise ContributionError(409, "contribution_duplicate", document=duplicate)
    # A removed contribution leaves a tombstone: the same source does not come back (Section 9.6).
    by_url = bool(source["canonical_url"])
    if dbm.is_tombstoned(conn, canonical_url=source["canonical_url"] if by_url else None, content_hash=None if by_url else source["content_hash"], kb_id=int(kb_id)):
        raise ContributionError(409, "contribution_tombstoned")
    # The copy is hidden until the merge job completes: a document without a graph is not a visible contribution.
    target_id = copy_document(conn, source_document_id=source_document_id, source_kb_id=source_kb_id, target_kb_id=kb_id, hidden=True)
    public_id = kb_collab.add_contribution(
        conn,
        kb_id=kb_id,
        document_id=target_id,
        origin=origin,
        state="pending" if pending else "merging",
        contributor_account_id=contributor_account_id,
        source_kb_id=source_kb_id,
        source_document_id=source_document_id,
        root_kb_id=source_kb_id,
        root_document_id=source_document_id,
        approved_at=ts if approved else None,
        approved_by_account_id=contributor_account_id if approved else None,
        submission_item_id=submission_item_id,
        now=ts,
    )
    if not pending:
        add_merge_job(conn, kb_id=kb_id, contribution=public_id, now=ts)
    contribution = kb_collab.get_contribution(conn, public_id)
    assert contribution is not None
    return contribution


def add_merge_job(conn: sqlite3.Connection, *, kb_id: int, contribution: int, revision: int | None = None, now: float | None = None) -> int:
    """Add one ``contribution_merge`` job. ``revision``: the row of ``kb_contribution_revisions`` that the job applies."""
    ts = time.time() if now is None else now
    payload = {"contribution": int(contribution)}
    if revision is not None:
        payload["revision"] = int(revision)
    cur = conn.execute(
        "INSERT INTO jobs (kb_id, payload, source_type, status, created_at, updated_at) VALUES (?, ?, ?, 'pending', ?, ?)",
        (int(kb_id), json.dumps(payload), MERGE_JOB, ts, ts),
    )
    return int(cur.lastrowid)


def claim_job(conn: sqlite3.Connection, job_id: int) -> bool:
    """Take one pending job for this process. False when another process has it. Commits."""
    taken = conn.execute("UPDATE jobs SET status='running', updated_at=? WHERE id=? AND status='pending'", (time.time(), int(job_id))).rowcount == 1
    conn.commit()
    return taken


def has_pending_merge_jobs(conn: sqlite3.Connection) -> bool:
    row = conn.execute("SELECT 1 FROM jobs WHERE source_type=? AND (status='pending' OR (status='running' AND updated_at < ?)) LIMIT 1", (MERGE_JOB, time.time() - STALE_JOB_SECONDS)).fetchone()
    return row is not None


def pending_merge_jobs(conn: sqlite3.Connection, *, kb_id: int | None = None) -> list[dict[str, Any]]:
    """The merge jobs that did not run to the end, oldest first. A job that is ``running`` for too long becomes ``pending`` again."""
    conn.execute("UPDATE jobs SET status='pending' WHERE source_type=? AND status='running' AND updated_at < ?", (MERGE_JOB, time.time() - STALE_JOB_SECONDS))
    conn.commit()
    sql = "SELECT id, kb_id, payload FROM jobs WHERE source_type=? AND status='pending'"
    params: list[Any] = [MERGE_JOB]
    if kb_id is not None:
        sql += " AND kb_id=?"
        params.append(int(kb_id))
    jobs = []
    for row in conn.execute(sql + " ORDER BY id", params).fetchall():
        try:
            payload = json.loads(row["payload"] or "{}")
        except ValueError:
            payload = {}
        jobs.append({"id": int(row["id"]), "kb_id": int(row["kb_id"]), "contribution": payload.get("contribution"), "revision": payload.get("revision")})
    return jobs


# --- merge job (Section 9.1 step 3) ----------------------------------------------------------------------------------


def _parse_extraction(raw_response: str) -> ExtractionResult:
    try:
        data = json.loads(raw_response)
    except ValueError as exc:
        raise ContributionError(409, "extraction_unreadable") from exc
    if not isinstance(data, dict):
        raise ContributionError(409, "extraction_unreadable")
    try:
        return ExtractionResult.model_validate({k: data[k] for k in ("summary", "key_claims", "entities", "relations") if k in data})
    except Exception as exc:  # noqa: BLE001 - a validation error of the stored JSON
        raise ContributionError(409, "extraction_unreadable") from exc


def build_graph_from_extraction(conn: sqlite3.Connection, provider: Any, settings: Any, *, kb_id: int, document_id: str) -> tuple[int, int]:
    """Build the graph of ``kb_id`` for one copied document from its stored extraction. No LLM call (CT24).

    ``provider`` is the provider of the server: only its embedding function is used, and only when its embedding model is the fixed model of the knowledge base (else ``ContributionError(409, "embedding_model_unavailable")``). Entities merge by the rules that need no judge (name, alias, and the automatic threshold); relations come from the extraction only.
    Return the counts of touched entities and added relations. The caller commits.
    """
    extraction = conn.execute("SELECT raw_response FROM extractions WHERE document_id=? AND kb_id=? ORDER BY id DESC LIMIT 1", (document_id, int(kb_id))).fetchone()
    if extraction is None:
        raise ContributionError(409, "document_not_extracted")
    result = _parse_extraction(str(extraction["raw_response"] or ""))
    doc = dbm.get_document(conn, document_id, kb_id=kb_id)
    if doc is None:
        raise ContributionError(404, "document_not_found")

    # The same rule as the ingest pipeline: a provider without the function embeds, but the CLI providers need a Gemini key for it.
    if hasattr(provider, "has_embed_capability"):
        has_embed = bool(provider.has_embed_capability())
    else:
        has_embed = getattr(provider, "name", "") not in ("codex", "antigravity") or bool(getattr(getattr(provider, "settings", settings), "gemini_api_key", ""))
    pin = resolve_embed_identity(provider, settings)
    fixed = read_embed_pin(conn, kb_id)
    if has_embed and fixed is not None and fixed.model and fixed.model != pin.model:
        raise ContributionError(409, "embedding_model_unavailable", model=fixed.model)
    vstore = make_vector_store(conn, getattr(settings, "vector_backend", "brute"), kb_id=kb_id)

    name_to_id: dict[str, str] = {}
    decisions = []
    for extracted in result.entities:
        etype, provisional = classify_entity_type(extracted.type)
        embed_fn = None
        if has_embed:
            def _embed(extracted=extracted):
                from .extract.frame import format_entity_frame

                return provider.embed(format_entity_frame(extracted), task_type="RETRIEVAL_DOCUMENT", title=extracted.name)

            embed_fn = _embed
        try:
            resolved = resolve_or_create(
                conn, vstore,
                name=extracted.name, etype=etype, aliases=extracted.aliases, observations=extracted.observations,
                document_id=document_id, embed_fn=embed_fn, judge_fn=None, provisional=provisional, shadow=None,
                kb_id=kb_id, embed_model=pin.model, embed_provider=pin.provider,
            )
        except EmbeddingModelMismatch as exc:
            raise ContributionError(409, "embedding_model_unavailable") from exc
        name_to_id[extracted.name] = resolved.entity.id
        if getattr(resolved, "decision", None) is not None:
            decisions.append(resolved.decision)
    if decisions:
        # The log lets a later purge of this copy undo its merges (``rollback_document_graph_contributions``).
        from .extract.decision import attach_resolution_meta

        attach_resolution_meta(doc, decisions)
        dbm.update_document_meta(conn, document_id, doc.meta, kb_id=kb_id)

    relations = 0
    for extracted in result.relations:
        source_id, target_id = name_to_id.get(extracted.source), name_to_id.get(extracted.target)
        if not source_id or not target_id or source_id == target_id:
            continue
        rtype, _ = classify_relation_type(extracted.type)
        source, target = dbm.get_entity(conn, source_id, kb_id=kb_id), dbm.get_entity(conn, target_id, kb_id=kb_id)
        verdict = validate_relation(rtype, source.type if source else "", target.type if target else "")
        if not verdict.ok:
            continue
        dbm.upsert_relation(conn, Relation(type=rtype, source_id=source_id, target_id=target_id, sources=[document_id], provisional=verdict.provisional), kb_id=kb_id)
        relations += 1
    return len(name_to_id), relations


def _finish_job(conn: sqlite3.Connection, job_id: int | None, *, error: str = "") -> None:
    if job_id is not None:
        conn.execute("UPDATE jobs SET status=?, error=?, updated_at=? WHERE id=?", ("error" if error else "done", error or None, time.time(), int(job_id)))


def run_merge_job(conn: sqlite3.Connection, provider: Any, settings: Any, *, contribution: int, job_id: int | None = None, revision: int | None = None) -> MergeResult:
    """Run the merge job of one contribution: give the copy its files, build its graph, and make it visible. Commits.

    With ``job_id`` the job is taken first (``claim_job``); when another process has it, the result has the error ``job_taken`` and nothing changes.

    With ``revision`` the job applies a revision (Section 9.5 rule 3): the graph of the new copy is built, the contribution points to it, and the old copy is purged. The state stays (a hidden contribution stays hidden).
    A failure sets the state ``failed`` (a first merge) or keeps the old copy (a revision) and records the code; ``retry_merge`` tries again.
    """
    if job_id is not None and not claim_job(conn, job_id):
        current = kb_collab.get_contribution(conn, contribution)
        return MergeResult(str(current["state"]) if current is not None else "removed", "job_taken")
    row = kb_collab.get_contribution(conn, contribution)
    if row is None:
        _finish_job(conn, job_id, error="contribution_not_found")
        conn.commit()
        return MergeResult("removed", "contribution_not_found")
    kb_id = int(row["kb_id"])
    data_dir = Path(settings.data_dir)
    if revision is not None:
        return _run_revision(conn, provider, settings, row=row, revision_id=int(revision), job_id=job_id, data_dir=data_dir)
    if row["state"] not in ("merging", "failed"):
        # The job is not for this state (for example the contribution was withdrawn or is pending): nothing to do.
        _finish_job(conn, job_id)
        conn.commit()
        return MergeResult(str(row["state"]))
    document_id = str(row["document_id"])
    try:
        # A retry starts from a clean graph: the first try can have left some entities of this copy.
        dbm.rollback_document_graph_contributions(conn, [document_id], prune_orphans=True, kb_id=kb_id)
        copy_document_files(conn, data_dir, source_document_id=str(row["source_document_id"]), target_document_id=document_id, kb_id=kb_id)
        entities, relations = build_graph_from_extraction(conn, provider, settings, kb_id=kb_id, document_id=document_id)
    except ContributionError as exc:
        conn.rollback()
        return _fail(conn, row, job_id, exc.code)
    except Exception as exc:  # noqa: BLE001 - the job records each failure; the server continues
        conn.rollback()
        log.warning("merge job of contribution %s failed: %s", contribution, type(exc).__name__)
        return _fail(conn, row, job_id, "merge_failed")
    conn.execute("UPDATE documents SET hidden=0 WHERE id=? AND kb_id=?", (document_id, kb_id))
    kb_collab.update_contribution(conn, contribution, state="visible")
    _finish_job(conn, job_id)
    conn.commit()
    return MergeResult("visible", entities=entities, relations=relations)


def _fail(conn: sqlite3.Connection, row: dict[str, Any], job_id: int | None, code: str) -> MergeResult:
    kb_collab.update_contribution(conn, int(row["public_id"]), state="failed", decision_reason=code)
    _finish_job(conn, job_id, error=code)
    conn.commit()
    return MergeResult("failed", code)


def retry_merge(conn: sqlite3.Connection, *, contribution: int) -> int:
    """Add a new merge job for a failed contribution (Section 9.1 step 3). ``ContributionError(409, "contribution_not_failed")`` for each other state. The caller commits."""
    row = kb_collab.get_contribution(conn, contribution)
    if row is None:
        raise ContributionError(404, "contribution_not_found")
    if row["state"] != "failed":
        raise ContributionError(409, "contribution_not_failed")
    kb_collab.update_contribution(conn, contribution, state="merging", decision_reason="")
    return add_merge_job(conn, kb_id=int(row["kb_id"]), contribution=contribution)


def run_pending_merge_jobs(conn: sqlite3.Connection, provider_for_kb: Any, settings_for_kb: Any, *, kb_id: int | None = None) -> list[MergeResult]:
    """Run each merge job that is still ``pending`` (a job whose process stopped, or a job that waits). ``provider_for_kb(kb_id)`` gives the provider of the server and ``settings_for_kb(kb_id)`` the settings of that knowledge base."""
    results = []
    for job in pending_merge_jobs(conn, kb_id=kb_id):
        if job["contribution"] is None:
            _finish_job(conn, job["id"], error="job_payload_invalid")
            conn.commit()
            continue
        results.append(run_merge_job(conn, provider_for_kb(job["kb_id"]), settings_for_kb(job["kb_id"]), contribution=int(job["contribution"]), job_id=job["id"], revision=job["revision"]))
    return results


# --- review actions, withdrawal (Sections 9.4, 9.6) ------------------------------------------------------------------


def _purge_copy(conn: sqlite3.Connection, data_dir: Path, *, kb_id: int, document_id: str, tombstone: bool, reason: str) -> None:
    """Delete one copy from K: its graph sources, its embeddings, its rows, and its files. Commits."""
    dbm.rollback_document_graph_contributions(conn, [document_id], prune_orphans=True, kb_id=kb_id)
    dbm.purge_document_cascade(conn, data_dir=Path(data_dir), target_ids=[document_id], reason=reason, tombstone=tombstone, kb_id=kb_id)


def _contribution_in(conn: sqlite3.Connection, kb_id: int, contribution: int) -> dict[str, Any]:
    row = kb_collab.get_contribution(conn, contribution)
    if row is None or int(row["kb_id"]) != int(kb_id):
        raise ContributionError(404, "contribution_not_found")
    return row


def withdraw(conn: sqlite3.Connection, data_dir: Path, *, kb_id: int, contribution: int, account_id: int) -> None:
    """The contributor withdraws its contribution while it is not approved (CT7). The copy and the row are deleted. Commits.

    After the approval: ``ContributionError(409, "contribution_approved")``. A contribution of another account: 404.
    """
    row = _contribution_in(conn, kb_id, contribution)
    if row["contributor_account_id"] != account_id:
        raise ContributionError(404, "contribution_not_found")
    if row["approved_at"] is not None:
        raise ContributionError(409, "contribution_approved")
    if row["state"] in ("removed", "rejected"):
        raise ContributionError(409, "contribution_closed")
    # The row goes first: the purge then finds no contribution of this document and leaves no state behind.
    kb_collab.delete_contribution(conn, contribution)
    conn.commit()
    if row["document_id"]:
        _purge_copy(conn, data_dir, kb_id=kb_id, document_id=str(row["document_id"]), tombstone=False, reason="contribution_withdrawn")


def approve(conn: sqlite3.Connection, *, kb_id: int, contribution: int, account_id: int, now: float | None = None) -> dict[str, Any]:
    """Accept the donation (Section 9.6). An approval is final. A pending contribution gets its merge job. The caller commits."""
    row = _contribution_in(conn, kb_id, contribution)
    if row["state"] in ("removed", "rejected"):
        raise ContributionError(409, "contribution_closed")
    if row["approved_at"] is not None:
        raise ContributionError(409, "contribution_approved")
    ts = time.time() if now is None else now
    changes: dict[str, Any] = {"approved_at": ts, "approved_by_account_id": account_id, "decided_at": ts}
    if row["state"] == "pending":
        changes["state"] = "merging"
        add_merge_job(conn, kb_id=kb_id, contribution=contribution, now=ts)
    kb_collab.update_contribution(conn, contribution, now=ts, **changes)
    kb_collab.log_moderation(conn, kb_id=kb_id, actor_account_id=account_id, action="contribution_approve", target=str(contribution), now=ts)
    return kb_collab.get_contribution(conn, contribution) or row


def reject(conn: sqlite3.Connection, data_dir: Path, *, kb_id: int, contribution: int, account_id: int, reason: str) -> None:
    """Reject a pending contribution with a reason code: the copy is deleted, the state is ``rejected`` (Section 9.6). Commits."""
    if reason not in REASON_CODES:
        raise ContributionError(400, "reason_invalid")
    row = _contribution_in(conn, kb_id, contribution)
    if row["state"] != "pending":
        raise ContributionError(409, "contribution_not_pending")
    ts = time.time()
    document_id = row["document_id"]
    kb_collab.update_contribution(conn, contribution, now=ts, state="rejected", decision_reason=reason, decided_at=ts, document_id=None)
    kb_collab.log_moderation(conn, kb_id=kb_id, actor_account_id=account_id, action="contribution_reject", target=str(contribution), reason=reason, now=ts)
    conn.commit()
    if document_id:
        _purge_copy(conn, data_dir, kb_id=kb_id, document_id=str(document_id), tombstone=False, reason="contribution_rejected")


def set_hidden(conn: sqlite3.Connection, *, kb_id: int, contribution: int, account_id: int, hidden: bool) -> dict[str, Any]:
    """Hide or unhide a contribution (Section 9.6): ``documents.hidden`` and the state ``hidden`` or ``visible``. Reversible. The caller commits."""
    row = _contribution_in(conn, kb_id, contribution)
    expected = "visible" if hidden else "hidden"
    if row["state"] != expected:
        raise ContributionError(409, "contribution_not_visible" if hidden else "contribution_not_hidden")
    conn.execute("UPDATE documents SET hidden=? WHERE id=? AND kb_id=?", (1 if hidden else 0, row["document_id"], int(kb_id)))
    kb_collab.update_contribution(conn, contribution, state="hidden" if hidden else "visible")
    kb_collab.log_moderation(conn, kb_id=kb_id, actor_account_id=account_id, action="contribution_hide" if hidden else "contribution_unhide", target=str(contribution))
    return kb_collab.get_contribution(conn, contribution) or row


def set_updates_frozen(conn: sqlite3.Connection, *, kb_id: int, contribution: int, account_id: int, frozen: bool) -> dict[str, Any]:
    """Stop or restart the updates of a contribution (Sections 9.5 rule 6 and 9.6). The caller commits."""
    row = _contribution_in(conn, kb_id, contribution)
    if row["state"] in ("removed", "rejected"):
        raise ContributionError(409, "contribution_closed")
    kb_collab.update_contribution(conn, contribution, updates_frozen=1 if frozen else 0)
    kb_collab.log_moderation(conn, kb_id=kb_id, actor_account_id=account_id, action="contribution_stop_updates" if frozen else "contribution_restart_updates", target=str(contribution))
    return kb_collab.get_contribution(conn, contribution) or row


def remove(conn: sqlite3.Connection, data_dir: Path, *, kb_id: int, contribution: int, account_id: int, reason: str) -> None:
    """Remove a contribution with a reason code (Section 9.6): the document is purged in K, a tombstone keeps the same source out, and the state is ``removed``. Commits."""
    if reason not in REASON_CODES:
        raise ContributionError(400, "reason_invalid")
    row = _contribution_in(conn, kb_id, contribution)
    if row["state"] in ("removed", "rejected"):
        raise ContributionError(409, "contribution_closed")
    ts = time.time()
    document_id = row["document_id"]
    kb_collab.update_contribution(conn, contribution, now=ts, state="removed", decision_reason=reason, decided_at=ts, document_id=None)
    kb_collab.log_moderation(conn, kb_id=kb_id, actor_account_id=account_id, action="contribution_remove", target=str(contribution), reason=reason, now=ts)
    conn.commit()
    if document_id:
        _purge_copy(conn, data_dir, kb_id=kb_id, document_id=str(document_id), tombstone=True, reason=f"contribution_removed:{reason}")


# --- revisions (Section 9.5) -----------------------------------------------------------------------------------------


def start_revisions(conn: sqlite3.Connection, data_dir: Path, *, root_kb_id: int, root_document_id: str) -> list[int]:
    """The content of a root document changed: start the revision of each contribution that follows it (Section 9.5 rules 2 to 4). Commits. Return the IDs of the new merge jobs.

    - A contribution with stopped updates, and a closed one (``removed``, ``rejected``), keeps its version.
    - A visible or hidden contribution: the new version and its extraction are copied into K without graph; the merge job applies it. No approval; the state stays.
    - A pending contribution: the new version replaces the pending copy.
    - A contribution in ``merging`` or ``failed``: its copy has no complete graph yet, so the new version replaces the copy and a new merge job starts.
    """
    root = conn.execute("SELECT content_hash FROM documents WHERE id=? AND kb_id=?", (root_document_id, int(root_kb_id))).fetchone()
    if root is None:
        return []
    content_hash = str(root["content_hash"] or "")
    jobs: list[int] = []
    for row in kb_collab.contributions_of_root(conn, root_document_id):
        if row["updates_frozen"] or row["state"] in ("removed", "rejected") or not row["document_id"]:
            continue
        kb_id = int(row["kb_id"])
        current = conn.execute("SELECT content_hash FROM documents WHERE id=? AND kb_id=?", (row["document_id"], kb_id)).fetchone()
        if current is not None and str(current["content_hash"] or "") == content_hash:
            continue  # the copy has this version (Section 9.5 rule 1: the same content makes no revision)
        old_copy = str(row["document_id"])
        public_id = int(row["public_id"])
        try:
            new_copy = copy_document(conn, source_document_id=root_document_id, source_kb_id=root_kb_id, target_kb_id=kb_id, hidden=True)
        except ContributionError as exc:
            conn.rollback()
            log.warning("revision of contribution %s did not start: %s", public_id, exc.code)
            continue
        if row["state"] in ("visible", "hidden"):
            revision = kb_collab.add_revision(conn, contribution_public_id=public_id, previous_document_id=old_copy, document_id=new_copy, content_hash=content_hash)
            jobs.append(add_merge_job(conn, kb_id=kb_id, contribution=public_id, revision=revision))
            conn.commit()
            continue
        # pending, merging, failed: the copy has no complete graph. The new version replaces it.
        kb_collab.update_contribution(conn, public_id, document_id=new_copy, revised_at=time.time(), **({"state": "merging", "decision_reason": ""} if row["state"] == "failed" else {}))
        if row["state"] in ("merging", "failed"):
            jobs.append(add_merge_job(conn, kb_id=kb_id, contribution=public_id))
        conn.commit()
        _purge_copy(conn, data_dir, kb_id=kb_id, document_id=old_copy, tombstone=False, reason="contribution_revised")
    return jobs


def _run_revision(conn: sqlite3.Connection, provider: Any, settings: Any, *, row: dict[str, Any], revision_id: int, job_id: int | None, data_dir: Path) -> MergeResult:
    """Apply one revision: build the graph of the new copy, point the contribution to it, and purge the old copy. A failure keeps the old copy."""
    public_id, kb_id = int(row["public_id"]), int(row["kb_id"])
    revision = next((r for r in kb_collab.list_revisions(conn, public_id) if int(r["id"]) == revision_id), None)
    if revision is None or revision["state"] != "merging":
        _finish_job(conn, job_id)
        conn.commit()
        return MergeResult(str(row["state"]))
    new_copy, old_copy = str(revision["document_id"]), revision["previous_document_id"]
    if row["state"] not in ("visible", "hidden") or row["updates_frozen"] or row["document_id"] != old_copy:
        # The contribution changed after the revision started (removed, updates stopped, or a newer revision): this new copy is not used.
        kb_collab.complete_revision(conn, revision_id, state="failed")
        _finish_job(conn, job_id, error="revision_superseded")
        conn.commit()
        _purge_copy(conn, data_dir, kb_id=kb_id, document_id=new_copy, tombstone=False, reason="contribution_revision_dropped")
        return MergeResult(str(row["state"]), "revision_superseded")
    try:
        copy_document_files(conn, data_dir, source_document_id=str(row["root_document_id"]), target_document_id=new_copy, kb_id=kb_id)
        entities, relations = build_graph_from_extraction(conn, provider, settings, kb_id=kb_id, document_id=new_copy)
    except Exception as exc:  # noqa: BLE001 - the old copy stays; the job records the failure
        conn.rollback()
        code = exc.code if isinstance(exc, ContributionError) else "merge_failed"
        kb_collab.complete_revision(conn, revision_id, state="failed")
        _finish_job(conn, job_id, error=code)
        conn.commit()
        _purge_copy(conn, data_dir, kb_id=kb_id, document_id=new_copy, tombstone=False, reason="contribution_revision_failed")
        return MergeResult(str(row["state"]), code)
    ts = time.time()
    # The contribution points to the new copy before the old one is purged, so the purge finds no contribution of the old document.
    conn.execute("UPDATE documents SET hidden=? WHERE id=? AND kb_id=?", (1 if row["state"] == "hidden" else 0, new_copy, kb_id))
    kb_collab.update_contribution(conn, public_id, now=ts, document_id=new_copy, revised_at=ts)
    kb_collab.complete_revision(conn, revision_id, state="done", now=ts)
    _finish_job(conn, job_id)
    conn.commit()
    if old_copy:
        _purge_copy(conn, data_dir, kb_id=kb_id, document_id=str(old_copy), tombstone=False, reason="contribution_revised")
    return MergeResult(str(row["state"]), entities=entities, relations=relations)


# --- contribution to a theme (Section 9.2) ---------------------------------------------------------------------------

# Admin settings in ``server_settings``: the documents of one submission, and the pending contributions of one account in one knowledge base. The reputation of unit CT-11 makes the second limit depend on the level of the account.
LIMITS = {"submission_max_documents": 20, "contribution_pending_limit": 20}


def read_limits(conn: sqlite3.Connection) -> dict[str, int]:
    """The limits of contributions. A stored value that is not a positive integer is a defect of the data: ``RuntimeError``."""
    values = dict(LIMITS)
    for key in LIMITS:
        row = conn.execute("SELECT value FROM server_settings WHERE key=?", (key,)).fetchone()
        if row is None:
            continue
        try:
            value = int(row[0])
        except (TypeError, ValueError):
            raise RuntimeError(f"server_settings.{key} is not an integer: {row[0]!r}") from None
        if value < 1:
            raise RuntimeError(f"server_settings.{key} is out of range: {value!r}")
        values[key] = value
    return values


def submit_to_themes(
    conn: sqlite3.Connection,
    *,
    account_id: int,
    is_admin: bool,
    personal_kb_id: int,
    documents: list[str],
    theme_id: int | None,
    now: float | None = None,
) -> int:
    """Contribute documents of the personal knowledge base of the account to themes (design Section 9.2). Return the public ID of the submission. One transaction; the caller commits and then runs the merge jobs.

    For each document: the theme is ``theme_id`` (the account selected it) or the theme match of the document (the server routes it). Without a theme the item has no target (the response gives ``no_theme``). The targets are the active shared knowledge bases of the theme where the account is not banned. In each target: a duplicate, a tombstone, and the pending limit skip the target with the reason; a reviewer of the target contributes without approval; each other account makes a pending contribution with a hidden copy and no graph.
    """
    from . import theme_judgement
    from .store import themes as themes_store

    ts = time.time() if now is None else now
    limits = read_limits(conn)
    if not documents or len(documents) > limits["submission_max_documents"]:
        raise ContributionError(400, "documents_invalid", limit=limits["submission_max_documents"])
    if len(set(documents)) != len(documents):
        raise ContributionError(400, "documents_invalid", limit=limits["submission_max_documents"])
    for document_id in documents:
        if conn.execute("SELECT 1 FROM documents WHERE id=? AND kb_id=?", (document_id, int(personal_kb_id))).fetchone() is None:
            raise ContributionError(404, "document_not_found", document=document_id)
    submission = kb_collab.add_submission(conn, account_id=account_id, now=ts)
    for document_id in documents:
        if theme_id is not None:
            item_theme, routed_by, score = theme_id, "account", None
        else:
            match = theme_judgement.match_document(conn, kb_id=personal_kb_id, document_id=document_id, now=ts)
            item_theme, routed_by, score = match.theme_id, "server", match.score
        item_id = kb_collab.add_submission_item(conn, submission_public_id=submission, source_document_id=document_id, theme_id=item_theme, routed_by=routed_by, match_score=score)
        if item_theme is None:
            continue
        targets: list[int] = []
        skipped: list[dict[str, Any]] = []
        for member in themes_store.theme_members(conn, item_theme):
            kb = conn.execute("SELECT id, is_personal, account_id, state FROM knowledge_bases WHERE id=?", (int(member["kb_id"]),)).fetchone()
            if kb is None or kb["is_personal"] or kb["account_id"] is not None or kb["state"] != "active":
                continue  # only an active shared knowledge base is a target
            kb_id = int(kb["id"])
            membership = kb_collab.get_membership(conn, kb_id, account_id)
            if membership is not None and membership["state"] == "banned":
                continue
            targets.append(kb_id)
            reviewer = is_admin or (membership is not None and membership["state"] == "active" and membership["role"] in ("collaborator", "moderator"))
            if not reviewer and kb_collab.pending_count(conn, kb_id=kb_id, account_id=account_id) >= limits["contribution_pending_limit"]:
                skipped.append({"kb_id": kb_id, "reason": "pending_limit"})
                continue
            try:
                contribute_direct(
                    conn, kb_id=kb_id, source_kb_id=personal_kb_id, source_document_id=document_id, contributor_account_id=account_id,
                    approved=reviewer, pending=not reviewer, origin="bulk", submission_item_id=item_id, now=ts,
                )
            except ContributionError as exc:
                if exc.code not in ("contribution_duplicate", "contribution_tombstoned", "document_not_extracted"):
                    raise
                skipped.append({"kb_id": kb_id, "reason": exc.code})
        kb_collab.set_submission_item_targets(conn, item_id, target_kb_ids=targets, skipped=skipped)
    return submission


def decision_of(row: dict[str, Any]) -> str:
    """The decision of the reviewers of one knowledge base on a contribution, for the review help (Section 9.3 rule 2)."""
    if row["state"] == "rejected":
        return "rejected"
    if row["state"] == "removed":
        return "removed"
    return "approved" if row["approved_at"] is not None else "pending"


def decision_agreement(conn: sqlite3.Connection) -> list[dict[str, Any]]:
    """For each shared knowledge base: the agreement of its decisions with the majority decision of the other knowledge bases on the same submission items, for items with 3 or more decisions (Section 9.3 rule 5)."""
    rows = conn.execute("SELECT submission_item_id, kb_id, state, approved_at FROM kb_contributions WHERE submission_item_id IS NOT NULL").fetchall()
    by_item: dict[int, dict[int, str]] = {}
    for row in rows:
        decision = decision_of({"state": row["state"], "approved_at": row["approved_at"]})
        if decision in ("approved", "rejected"):
            by_item.setdefault(int(row["submission_item_id"]), {})[int(row["kb_id"])] = decision
    totals: dict[int, list[int]] = {}
    for decisions in by_item.values():
        if len(decisions) < 3:
            continue
        for kb_id, decision in decisions.items():
            others = [d for k, d in decisions.items() if k != kb_id]
            approved = others.count("approved")
            if approved * 2 == len(others):
                continue  # the other knowledge bases have no majority
            majority = "approved" if approved * 2 > len(others) else "rejected"
            entry = totals.setdefault(kb_id, [0, 0])
            entry[0] += 1
            entry[1] += 1 if decision == majority else 0
    return [{"kb_id": kb_id, "decisions": count, "agreement": agreed / count} for kb_id, (count, agreed) in sorted(totals.items())]
