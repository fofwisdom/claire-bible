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
import re
import shutil
import sqlite3
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

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
    The body of the copy (``detail``, ``detail_html``) has the image paths of the source (``images/<source ID>_<n>``, also in the form ``/image?p=images/...``). For each image file that is copied, these paths change to the path of the copy; a path of a file that is not copied stays.
    """
    data_dir = Path(data_dir)
    copied = 0
    renamed: dict[str, str] = {}
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
                renamed[local] = target_local
                copied += 1
            else:
                image.pop("local", None)
        conn.execute("UPDATE documents SET meta=? WHERE id=? AND kb_id=?", (json.dumps(meta, ensure_ascii=False), target_document_id, int(kb_id)))
    if renamed:
        _rewrite_image_paths(conn, document_id=target_document_id, kb_id=int(kb_id), renamed=renamed)
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


def _rewrite_image_paths(conn: sqlite3.Connection, *, document_id: str, kb_id: int, renamed: dict[str, str]) -> None:
    """Replace each image path of ``renamed`` (old path → new path) in the body columns of one document. A longer file name that starts with an old path is not changed."""
    body = conn.execute("SELECT detail, detail_html FROM documents WHERE id=? AND kb_id=?", (document_id, kb_id)).fetchone()
    if body is None:
        return
    pattern = re.compile("(?:" + "|".join(re.escape(old) for old in sorted(renamed, key=len, reverse=True)) + r")(?![\w-]|\.\w)")
    for column in ("detail", "detail_html"):
        text = body[column]
        if not isinstance(text, str) or not text:
            continue
        changed = pattern.sub(lambda m: renamed[m.group(0)], text)
        if changed != text:
            conn.execute(f"UPDATE documents SET {column}=? WHERE id=? AND kb_id=?", (changed, document_id, kb_id))


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


def check_new_source(conn: sqlite3.Connection, kb_id: int, *, canonical_url: str | None, content_hash: str | None) -> None:
    """The duplicate and tombstone checks of a new copy in K (Sections 9.1 and 9.6): ``ContributionError(409, "contribution_duplicate")`` with the document, or ``ContributionError(409, "contribution_tombstoned")``."""
    duplicate = find_duplicate(conn, kb_id, canonical_url=canonical_url, content_hash=content_hash)
    if duplicate is not None:
        hidden = conn.execute("SELECT hidden FROM documents WHERE id=? AND kb_id=?", (duplicate, int(kb_id))).fetchone()
        raise ContributionError(409, "contribution_duplicate", document=duplicate, visible=hidden is not None and not hidden[0])
    # A removed contribution leaves a tombstone: the same source does not come back (Section 9.6).
    by_url = bool(canonical_url)
    if dbm.is_tombstoned(conn, canonical_url=canonical_url if by_url else None, content_hash=None if by_url else content_hash, kb_id=int(kb_id)):
        raise ContributionError(409, "contribution_tombstoned")


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
    return _add_copy(
        conn, kb_id=kb_id, source_kb_id=source_kb_id, source_document_id=source_document_id, origin=origin, contributor_account_id=contributor_account_id, curator_account_id=None,
        root_kb_id=source_kb_id, root_document_id=source_document_id, approved_by_account_id=contributor_account_id if approved else None, pending=pending, submission_item_id=submission_item_id, ts=ts,
    )


def _add_copy(
    conn: sqlite3.Connection,
    *,
    kb_id: int,
    source_kb_id: int,
    source_document_id: str,
    origin: str,
    contributor_account_id: int | None,
    curator_account_id: int | None,
    root_kb_id: int | None,
    root_document_id: str | None,
    approved_by_account_id: int | None,
    pending: bool,
    submission_item_id: int | None,
    ts: float,
) -> dict[str, Any]:
    """The common part of a direct contribution, a contribution to a theme, and a curation: the duplicate and tombstone checks, the hidden copy, the contribution, and its merge job (not for a pending one)."""
    source = conn.execute("SELECT canonical_url, content_hash FROM documents WHERE id=? AND kb_id=?", (source_document_id, int(source_kb_id))).fetchone()
    if source is None:
        raise ContributionError(404, "document_not_found")
    check_new_source(conn, kb_id, canonical_url=source["canonical_url"], content_hash=source["content_hash"])
    # The copy is hidden until the merge job completes: a document without a graph is not a visible contribution.
    target_id = copy_document(conn, source_document_id=source_document_id, source_kb_id=source_kb_id, target_kb_id=kb_id, hidden=True)
    public_id = kb_collab.add_contribution(
        conn,
        kb_id=kb_id,
        document_id=target_id,
        origin=origin,
        state="pending" if pending else "merging",
        contributor_account_id=contributor_account_id,
        curator_account_id=curator_account_id,
        source_kb_id=source_kb_id,
        source_document_id=source_document_id,
        root_kb_id=root_kb_id,
        root_document_id=root_document_id,
        approved_at=ts if approved_by_account_id is not None else None,
        approved_by_account_id=approved_by_account_id,
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


def build_graph_from_extraction(conn: sqlite3.Connection, provider: Any, settings: Any, *, kb_id: int, document_id: str, heartbeat: Callable[[], None] | None = None, decisions: list[Any] | None = None) -> tuple[int, int]:
    """Build the graph of ``kb_id`` for one copied document from its stored extraction. No LLM call (CT24).

    ``provider`` is the provider of the server: only its embedding function is used, and only when its embedding model is the fixed model of the knowledge base (else ``ContributionError(409, "embedding_model_unavailable")``). Entities merge by the rules that need no judge (name, alias, and the automatic threshold); relations come from the extraction only.
    Return the counts of touched entities and of new relations. Each entity and each relation is its own commit (``upsert_entity``, ``upsert_relation``), so the function is not one transaction.
    The log of the entity decisions goes into ``documents.meta`` also when the function fails part-way, so that the rollback of the copy can undo the merges that were made. ``decisions``: the caller gets each decision in this list (also on a failure). ``heartbeat`` is called after each entity and each relation.
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
    decisions = [] if decisions is None else decisions
    # Authority identifiers of the entities of a copy from another server (federation design Section 10.4 rule 2, KD7). An extraction of this server has none.
    same_as = _stored_authority_ids(str(extraction["raw_response"] or ""))
    try:
        for extracted in result.entities:
            etype, provisional = classify_entity_type(extracted.type)
            authority = same_as.get(extracted.name, [])
            # An entity of K with the same authority identifier is matched first: its name goes to the resolver, which then merges by the name; the name of the extraction becomes an alias. Without such an entity the keys decide, as for each other entity.
            matched_name = _entity_with_authority_id(conn, kb_id, authority) if authority else None
            embed_fn = None
            if has_embed:
                def _embed(extracted=extracted):
                    from .extract.frame import format_entity_frame

                    return provider.embed(format_entity_frame(extracted), task_type="RETRIEVAL_DOCUMENT", title=extracted.name)

                embed_fn = _embed
            try:
                resolved = resolve_or_create(
                    conn, vstore,
                    name=matched_name or extracted.name, etype=etype, aliases=extracted.aliases if matched_name is None else [*extracted.aliases, extracted.name], observations=extracted.observations,
                    document_id=document_id, embed_fn=embed_fn, judge_fn=None, provisional=provisional, shadow=None,
                    kb_id=kb_id, embed_model=pin.model, embed_provider=pin.provider,
                )
            except EmbeddingModelMismatch as exc:
                raise ContributionError(409, "embedding_model_unavailable") from exc
            name_to_id[extracted.name] = resolved.entity.id
            if authority:
                _add_authority_ids(conn, kb_id, resolved.entity.id, authority)
            if getattr(resolved, "decision", None) is not None:
                decisions.append(resolved.decision)
            if heartbeat is not None:
                heartbeat()
    finally:
        # The log lets a later purge of this copy undo its merges (``rollback_document_graph_contributions``). It is written also on a failure: the merges that were made are committed. An old log (of a try before) is replaced, also by an empty one.
        if decisions or (doc.meta or {}).get("resolution_log"):
            from .extract.decision import attach_resolution_meta

            attach_resolution_meta(doc, decisions)
            dbm.update_document_meta(conn, document_id, doc.meta, kb_id=kb_id)
            conn.commit()

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
        # A relation that is there gets this document as one more source and does not count.
        if dbm.upsert_relation(conn, Relation(type=rtype, source_id=source_id, target_id=target_id, sources=[document_id], provisional=verdict.provisional), kb_id=kb_id):
            relations += 1
        if heartbeat is not None:
            heartbeat()
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
    data_dir = Path(settings.data_dir)
    row = kb_collab.get_contribution(conn, contribution)
    if row is None:
        # The contribution was withdrawn. The withdrawal purges the copies; a revision copy that is still there (its row did not go with the contribution) is purged here.
        waiting = kb_collab.get_revision(conn, int(revision)) if revision is not None else None
        if waiting is not None and waiting["state"] == "merging":
            kb_collab.complete_revision(conn, int(revision), state="failed")
        _finish_job(conn, job_id, error="contribution_not_found")
        conn.commit()
        if waiting is not None and waiting["state"] == "merging":
            _drop_copy(conn, data_dir, document_id=str(waiting["document_id"]), decisions=[], reason="contribution_revision_dropped")
        return MergeResult("removed", "contribution_not_found")
    kb_id = int(row["kb_id"])
    if revision is not None:
        return _run_revision(conn, provider, settings, row=row, revision_id=int(revision), job_id=job_id, data_dir=data_dir)
    if row["state"] not in ("merging", "failed"):
        # The job is not for this state (for example the contribution was withdrawn or is pending): nothing to do.
        _finish_job(conn, job_id)
        conn.commit()
        return MergeResult(str(row["state"]))
    document_id, from_state = str(row["document_id"]), str(row["state"])
    decisions: list[Any] = []
    code = ""
    entities = relations = 0
    try:
        # A retry starts from a clean graph: the first try can have left some entities of this copy.
        dbm.rollback_document_graph_contributions(conn, [document_id], prune_orphans=True, kb_id=kb_id)
        if row["source_document_id"]:
            # A copy of a document of another server has no source files on this server.
            copy_document_files(conn, data_dir, source_document_id=str(row["source_document_id"]), target_document_id=document_id, kb_id=kb_id)
        entities, relations = build_graph_from_extraction(conn, provider, settings, kb_id=kb_id, document_id=document_id, heartbeat=_heartbeat(conn, job_id), decisions=decisions)
    except ContributionError as exc:
        conn.rollback()
        code = exc.code
    except Exception as exc:  # noqa: BLE001 - the job records each failure; the server continues
        conn.rollback()
        log.warning("merge job of contribution %s failed: %s", contribution, type(exc).__name__)
        code = "merge_failed"
    # The graph steps commit for each row, so the contribution can change while the job runs (a withdrawal, a removal). The state changes only when the contribution is still the one that the job started with.
    if not kb_collab.finish_merge(conn, contribution, from_state=from_state, document_id=document_id, state="failed" if code else "visible", decision_reason=code or None):
        _finish_job(conn, job_id, error="contribution_changed")
        conn.commit()
        _drop_copy(conn, data_dir, document_id=document_id, decisions=decisions, reason="contribution_merge_dropped")
        current = kb_collab.get_contribution(conn, contribution)
        return MergeResult(str(current["state"]) if current is not None else "removed", "contribution_changed")
    if code:
        _finish_job(conn, job_id, error=code)
        conn.commit()
        return MergeResult("failed", code)
    current = kb_collab.get_contribution(conn, contribution)
    if current is not None and current["origin_removed_at"] is not None and current["origin_decided_at"] is None:
        # The origin removed the document while the job ran (federation design Section 10.3): the copy stays hidden and waits in the review queue.
        kb_collab.update_contribution(conn, contribution, state="hidden")
        _finish_job(conn, job_id)
        conn.commit()
        return MergeResult("hidden", entities=entities, relations=relations)
    conn.execute("UPDATE documents SET hidden=0 WHERE id=? AND kb_id=?", (document_id, kb_id))
    _finish_job(conn, job_id)
    conn.commit()
    return MergeResult("visible", entities=entities, relations=relations)


def _heartbeat(conn: sqlite3.Connection, job_id: int | None) -> Callable[[], None] | None:
    """The progress signal of a running job: each call sets ``jobs.updated_at`` and commits. Thus only a job with no progress for ``STALE_JOB_SECONDS`` is taken again."""
    if job_id is None:
        return None

    def _beat() -> None:
        conn.execute("UPDATE jobs SET updated_at=? WHERE id=? AND status='running'", (time.time(), int(job_id)))
        conn.commit()

    return _beat


def _drop_copy(conn: sqlite3.Connection, data_dir: Path, *, document_id: str, decisions: list[Any], reason: str) -> None:
    """Remove a copy that a job made or built while its contribution went away: undo the merges of ``decisions`` (the document row and its log can be gone), then delete the graph sources, the rows, and the files of the copy. Commits.

    The copy is in the knowledge base of its document row; when the row is gone, the entities that have the document as a source tell the knowledge base. A copy that a contribution points to is not touched.
    """
    from .extract.decision import rollback_resolution

    for decision in reversed(decisions):
        rollback_resolution(conn, decision)
    owners = {int(r[0]) for r in conn.execute("SELECT kb_id FROM documents WHERE id=?", (document_id,))}
    owners.update(int(r[0]) for r in conn.execute("SELECT DISTINCT kb_id FROM entities WHERE sources LIKE ?", (f'%"{document_id}"%',)))
    for kb_id in sorted(owners):
        if kb_collab.get_contribution_for_document(conn, kb_id, document_id) is not None:
            log.warning("copy %s is not dropped: a contribution has it", document_id)
            continue
        _purge_copy(conn, data_dir, kb_id=kb_id, document_id=document_id, tombstone=False, reason=reason)


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
    # The step before cleaned the graph of this copy, so the purge runs no graph heal; and a review action runs no ``VACUUM`` of the database.
    dbm.purge_document_cascade(conn, data_dir=Path(data_dir), target_ids=[document_id], reason=reason, tombstone=tombstone, kb_id=kb_id, heal=False, vacuum=False)


def _contribution_in(conn: sqlite3.Connection, kb_id: int, contribution: int) -> dict[str, Any]:
    row = kb_collab.get_contribution(conn, contribution)
    if row is None or int(row["kb_id"]) != int(kb_id):
        raise ContributionError(404, "contribution_not_found")
    return row


def _waiting_revision_copies(conn: sqlite3.Connection, contribution: int, *, keep: int | None = None) -> list[str]:
    """Close each revision of the contribution that still waits for its merge job (state ``merging``), but not the revision ``keep``, and return the copies of the closed revisions. The caller commits and then purges the copies. The job of a closed revision does nothing."""
    copies = []
    for revision in kb_collab.list_revisions(conn, contribution):
        if revision["state"] != "merging" or (keep is not None and int(revision["id"]) == int(keep)):
            continue
        kb_collab.complete_revision(conn, int(revision["id"]), state="failed")
        copies.append(str(revision["document_id"]))
    return copies


def withdraw(conn: sqlite3.Connection, data_dir: Path, *, kb_id: int, contribution: int, account_id: int) -> dict[str, Any]:
    """The contributor withdraws its contribution while it is not approved (CT7). The copy, the copies of its revisions that wait, and the row are deleted. Commits.

    After the approval: ``ContributionError(409, "contribution_approved")``. A contribution of another account: 404.
    An automatic contribution (origin ``auto``): the source document gets the flag "never contribute", because the row that keeps the document out of the next run of the rule is deleted (design Section 9.9 rules 2 and 6). The account can clear the flag. The result tells it: ``{"contribution_blocked": bool}``.
    """
    row = _contribution_in(conn, kb_id, contribution)
    if row["contributor_account_id"] != account_id:
        raise ContributionError(404, "contribution_not_found")
    if row["approved_at"] is not None:
        raise ContributionError(409, "contribution_approved")
    if row["state"] in ("removed", "rejected"):
        raise ContributionError(409, "contribution_closed")
    copies = _waiting_revision_copies(conn, contribution)
    # The row goes first: the purge then finds no contribution of this document and leaves no state behind.
    kb_collab.delete_contribution(conn, contribution)
    blocked = row["origin"] == "auto" and bool(row["source_document_id"])
    if blocked:
        kb_collab.set_contribution_block(conn, account_id=account_id, document_id=str(row["source_document_id"]), blocked=True)
    conn.commit()
    if row["document_id"]:
        copies.append(str(row["document_id"]))
    for copy_id in copies:
        _purge_copy(conn, data_dir, kb_id=kb_id, document_id=copy_id, tombstone=False, reason="contribution_withdrawn")
    return {"contribution_blocked": blocked}


def approve(conn: sqlite3.Connection, *, kb_id: int, contribution: int, account_id: int, is_admin: bool, now: float | None = None) -> dict[str, Any]:
    """Accept the donation (Section 9.6). An approval is final. A pending contribution gets its merge job. The caller commits."""
    row = _contribution_in(conn, kb_id, contribution)
    if row["state"] in ("removed", "rejected"):
        raise ContributionError(409, "contribution_closed")
    if row["approved_at"] is not None:
        raise ContributionError(409, "contribution_approved")
    if row["contributor_account_id"] is not None and int(row["contributor_account_id"]) == int(account_id) and not is_admin:
        # A reviewer does not approve its own contribution: with the reputation ``low`` the contribution of a collaborator or a moderator waits for the approval of another reviewer (design Section 8.6).
        raise ContributionError(403, "own_contribution")
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
    changes: dict[str, Any] = {"state": "hidden" if hidden else "visible"}
    if not hidden and row["origin_removed_at"] is not None and row["origin_decided_at"] is None:
        # The decision "show again" for a copy whose origin removed the document (federation design Section 10.3): the copy leaves the review queue.
        changes["origin_decided_at"] = time.time()
    kb_collab.update_contribution(conn, contribution, **changes)
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
    copies = _waiting_revision_copies(conn, contribution)
    kb_collab.update_contribution(conn, contribution, now=ts, state="removed", decision_reason=reason, decided_at=ts, document_id=None)
    kb_collab.log_moderation(conn, kb_id=kb_id, actor_account_id=account_id, action="contribution_remove", target=str(contribution), reason=reason, now=ts)
    conn.commit()
    if document_id:
        _purge_copy(conn, data_dir, kb_id=kb_id, document_id=str(document_id), tombstone=True, reason=f"contribution_removed:{reason}")
    for copy_id in copies:
        _purge_copy(conn, data_dir, kb_id=kb_id, document_id=copy_id, tombstone=False, reason="contribution_revision_dropped")


# --- revisions (Section 9.5) -----------------------------------------------------------------------------------------


def start_revisions(conn: sqlite3.Connection, data_dir: Path, *, root_kb_id: int, root_document_id: str) -> list[int]:
    """The content of a root document changed: start the revision of each contribution that follows it (Section 9.5 rules 2 to 4). Commits. Return the IDs of the new merge jobs.

    - A contribution with stopped updates, and a closed one (``removed``, ``rejected``), keeps its version.
    - A visible or hidden contribution: the new version and its extraction are copied into K without graph; the merge job applies it. No approval; the state stays.
    - A contribution in ``merging``: its merge job can run now, so its copy is not replaced. The new version waits as a revision; its job runs after the merge (``_run_revision`` puts the job back while the state is ``merging``).
    - A pending contribution: the new version replaces the pending copy.
    - A contribution in ``failed``: its copy has no complete graph and no job, so the new version replaces the copy and a new merge job starts.
    - The newest version wins: a revision of the contribution that still waits is closed and its copy is purged, but a revision that waits with this same version stays and no second one starts.
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
        old_copy = str(row["document_id"])
        public_id = int(row["public_id"])
        current = conn.execute("SELECT content_hash FROM documents WHERE id=? AND kb_id=?", (row["document_id"], kb_id)).fetchone()
        same_as_copy = current is not None and str(current["content_hash"] or "") == content_hash
        # The revision that waits with this version, when the copy does not have it: it stays.
        queued = None if same_as_copy else next((int(r["id"]) for r in kb_collab.list_revisions(conn, public_id) if r["state"] == "merging" and str(r["content_hash"] or "") == content_hash), None)
        stale = _waiting_revision_copies(conn, public_id, keep=queued)
        if same_as_copy or queued is not None:
            # The copy has this version (Section 9.5 rule 1: the same content makes no revision), or its revision waits.
            conn.commit()
            for copy_id in stale:
                _purge_copy(conn, data_dir, kb_id=kb_id, document_id=copy_id, tombstone=False, reason="contribution_revision_dropped")
            continue
        try:
            new_copy = copy_document(conn, source_document_id=root_document_id, source_kb_id=root_kb_id, target_kb_id=kb_id, hidden=True)
        except ContributionError as exc:
            conn.rollback()
            log.warning("revision of contribution %s did not start: %s", public_id, exc.code)
            continue
        if row["state"] in ("visible", "hidden", "merging"):
            revision = kb_collab.add_revision(conn, contribution_public_id=public_id, previous_document_id=old_copy, document_id=new_copy, content_hash=content_hash)
            jobs.append(add_merge_job(conn, kb_id=kb_id, contribution=public_id, revision=revision))
            conn.commit()
        else:
            # pending, failed: the copy has no complete graph and no job runs for it. The new version replaces it.
            kb_collab.update_contribution(conn, public_id, document_id=new_copy, revised_at=time.time(), **({"state": "merging", "decision_reason": ""} if row["state"] == "failed" else {}))
            if row["state"] == "failed":
                jobs.append(add_merge_job(conn, kb_id=kb_id, contribution=public_id))
            conn.commit()
            stale.append(old_copy)
        for copy_id in stale:
            _purge_copy(conn, data_dir, kb_id=kb_id, document_id=copy_id, tombstone=False, reason="contribution_revised")
    return jobs


def _run_revision(conn: sqlite3.Connection, provider: Any, settings: Any, *, row: dict[str, Any], revision_id: int, job_id: int | None, data_dir: Path) -> MergeResult:
    """Apply one revision: build the graph of the new copy, point the contribution to it, and purge the old copy. A failure keeps the old copy."""
    public_id, kb_id = int(row["public_id"]), int(row["kb_id"])
    revision = next((r for r in kb_collab.list_revisions(conn, public_id) if int(r["id"]) == revision_id), None)
    if revision is None or revision["state"] != "merging":
        _finish_job(conn, job_id)
        conn.commit()
        if revision is not None and revision["state"] == "failed":
            # A revision that was closed before its job ran. The code that closes a revision in a request purges its copy; a handler of the inbox journal cannot (it must not commit), so the copy is removed here. A copy that is gone, or that a contribution has, does not change.
            _drop_copy(conn, data_dir, document_id=str(revision["document_id"]), decisions=[], reason="contribution_revision_dropped")
        return MergeResult(str(row["state"]))
    new_copy, old_copy = str(revision["document_id"]), revision["previous_document_id"]
    if row["state"] == "merging" and not row["updates_frozen"] and row["document_id"] == old_copy:
        # The first merge of the contribution is not complete: the revision waits. The job goes back and the next sweep takes it.
        if job_id is not None:
            conn.execute("UPDATE jobs SET status='pending', updated_at=? WHERE id=?", (time.time(), int(job_id)))
        conn.commit()
        return MergeResult("merging", "revision_waits")
    if row["state"] not in ("visible", "hidden") or row["updates_frozen"] or row["document_id"] != old_copy:
        # The contribution changed after the revision started (removed, updates stopped, a failed merge, or a newer revision): this new copy is not used.
        kb_collab.complete_revision(conn, revision_id, state="failed")
        _finish_job(conn, job_id, error="revision_superseded")
        conn.commit()
        _purge_copy(conn, data_dir, kb_id=kb_id, document_id=new_copy, tombstone=False, reason="contribution_revision_dropped")
        return MergeResult(str(row["state"]), "revision_superseded")
    decisions: list[Any] = []
    try:
        if row["root_document_id"]:
            # A copy of a document of another server has no root files on this server.
            copy_document_files(conn, data_dir, source_document_id=str(row["root_document_id"]), target_document_id=new_copy, kb_id=kb_id)
        entities, relations = build_graph_from_extraction(conn, provider, settings, kb_id=kb_id, document_id=new_copy, heartbeat=_heartbeat(conn, job_id), decisions=decisions)
    except Exception as exc:  # noqa: BLE001 - the old copy stays; the job records the failure
        conn.rollback()
        code = exc.code if isinstance(exc, ContributionError) else "merge_failed"
        kb_collab.complete_revision(conn, revision_id, state="failed")
        _finish_job(conn, job_id, error=code)
        conn.commit()
        _drop_copy(conn, data_dir, document_id=new_copy, decisions=decisions, reason="contribution_revision_failed")
        return MergeResult(str(row["state"]), code)
    ts = time.time()
    # The graph steps commit for each row, so the contribution and the revision can change while the job runs. The contribution points to the new copy only when both are still as the job found them; this is before the purge of the old copy, so the purge finds no contribution of the old document.
    if not kb_collab.apply_revision(conn, revision_id, now=ts):
        _finish_job(conn, job_id, error="revision_superseded")
        conn.commit()
        _drop_copy(conn, data_dir, document_id=new_copy, decisions=decisions, reason="contribution_revision_dropped")
        current = kb_collab.get_contribution(conn, public_id)
        return MergeResult(str(current["state"]) if current is not None else "removed", "revision_superseded")
    state = str((kb_collab.get_contribution(conn, public_id) or row)["state"])
    conn.execute("UPDATE documents SET hidden=? WHERE id=? AND kb_id=?", (1 if state == "hidden" else 0, new_copy, kb_id))
    _finish_job(conn, job_id)
    conn.commit()
    if old_copy:
        _purge_copy(conn, data_dir, kb_id=kb_id, document_id=str(old_copy), tombstone=False, reason="contribution_revised")
    return MergeResult(state, entities=entities, relations=relations)


# --- contribution to a theme (Section 9.2) ---------------------------------------------------------------------------

# Admin setting in ``server_settings``: the documents of one submission. The limit of the pending contributions of one account in one knowledge base comes from the reputation (``reputation.pending_limit``).
LIMITS = {"submission_max_documents": 20}


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


def write_limit(conn: sqlite3.Connection, key: str, value: Any, *, updated_by: int | None) -> None:
    """Set one limit of contributions. ``ValueError`` for an unknown key or a value that is not an integer of 1 or more. No commit."""
    if key not in LIMITS:
        raise ValueError(f"unknown contribution limit: {key!r}")
    if isinstance(value, bool) or not isinstance(value, int) or value < 1:
        raise ValueError(f"{key} must be an integer of 1 or more")
    conn.execute(
        "INSERT INTO server_settings (key, value, updated_at, updated_by) VALUES (?, ?, ?, ?) ON CONFLICT(key) DO UPDATE SET value=excluded.value, updated_at=excluded.updated_at, updated_by=excluded.updated_by",
        (key, str(value), time.time(), updated_by),
    )


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
    from . import reputation, theme_judgement
    from .store import themes as themes_store

    ts = time.time() if now is None else now
    limits = read_limits(conn)
    level = reputation.level(conn, account_id, now=ts)
    pending_limit = reputation.pending_limit(reputation.read_settings(conn), level)
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
            # With the reputation ``low`` the contribution of a collaborator or a moderator also waits for approval (Section 8.6). The admin has no reputation rule.
            reviewer = is_admin or (membership is not None and membership["state"] == "active" and membership["role"] in ("collaborator", "moderator") and not reputation.needs_approval(level))
            if not reviewer and kb_collab.pending_count(conn, kb_id=kb_id, account_id=account_id) >= pending_limit:
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
    """For each shared knowledge base: the agreement of its decisions with the majority decision of the other knowledge bases on the same submission items, for items with 3 or more decisions (Section 9.3 rule 5).

    Only a decision of another account counts (``kb_collab.approved_by_another``): the approval that the contribution of a reviewer gets when it is made is no decision of that knowledge base.
    """
    rows = conn.execute("SELECT submission_item_id, kb_id, state, approved_at, decided_at, approved_by_account_id, contributor_account_id FROM kb_contributions WHERE submission_item_id IS NOT NULL").fetchall()
    by_item: dict[int, dict[int, str]] = {}
    for row in rows:
        decision = decision_of({"state": row["state"], "approved_at": row["approved_at"]})
        if decision == "rejected" or (decision == "approved" and kb_collab.approved_by_another(row)):
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


# --- curation (Section 9.7) ------------------------------------------------------------------------------------------


def curation_source(conn: sqlite3.Connection, *, kb_id: int, source_kb_id: int) -> dict[str, Any]:
    """The knowledge base S that a reviewer of K curates from: another knowledge base of a theme of K, shared or a public personal one (CT14).

    A private personal knowledge base gets 404 ``source_not_found`` (the same answer as for no knowledge base). A knowledge base that shares no theme with K gets 409 ``source_not_in_theme``.
    """
    from .store import themes as themes_store

    row = conn.execute("SELECT id, is_personal, is_public, account_id FROM knowledge_bases WHERE id=?", (int(source_kb_id),)).fetchone()
    if row is None or (row["is_personal"] and not row["is_public"]):
        raise ContributionError(404, "source_not_found")
    if int(row["id"]) == int(kb_id):
        raise ContributionError(400, "source_invalid")
    if not themes_store.share_a_theme(conn, int(kb_id), int(source_kb_id)):
        raise ContributionError(409, "source_not_in_theme")
    if row["is_personal"] and row["account_id"] is not None:
        from . import reputation

        # With the reputation ``low`` the personal knowledge base has no theme attachment, thus it is no curation source (Section 8.6). The check here applies the level before the next judgement run.
        if not reputation.attaches_to_themes(reputation.level(conn, int(row["account_id"]))):
            raise ContributionError(409, "source_not_in_theme")
    return {"id": int(row["id"]), "is_personal": bool(row["is_personal"]), "account_id": row["account_id"]}


def curate(conn: sqlite3.Connection, *, kb_id: int, source: dict[str, Any], document_id: str, curator_account_id: int, now: float | None = None) -> dict[str, Any]:
    """Copy one visible document of S (``curation_source``) into K as a contribution with the origin ``curation`` (design Section 9.7 rules 3 and 4). The curator approves it; its merge job waits. The caller commits.

    The credit: for a shared S, the contributor of the document in S; for a personal S, the owner of S. The root: for a shared S, the root of the contribution in S; for a personal S, the document itself.
    """
    ts = time.time() if now is None else now
    source_kb_id = int(source["id"])
    visible = conn.execute("SELECT 1 FROM documents WHERE id=? AND kb_id=? AND hidden=0", (document_id, source_kb_id)).fetchone()
    if visible is None:
        raise ContributionError(404, "document_not_found")
    if source["is_personal"]:
        credit, root_kb_id, root_document_id = source["account_id"], source_kb_id, document_id
    else:
        origin = kb_collab.get_contribution_for_document(conn, source_kb_id, document_id)
        if origin is None or origin["state"] != "visible":
            raise ContributionError(404, "document_not_found")
        credit, root_kb_id, root_document_id = origin["contributor_account_id"], origin["root_kb_id"], origin["root_document_id"]
    row = _add_copy(
        conn, kb_id=kb_id, source_kb_id=source_kb_id, source_document_id=document_id, origin="curation", contributor_account_id=credit, curator_account_id=curator_account_id,
        root_kb_id=root_kb_id, root_document_id=root_document_id, approved_by_account_id=curator_account_id, pending=False, submission_item_id=None, ts=ts,
    )
    kb_collab.log_moderation(conn, kb_id=kb_id, actor_account_id=curator_account_id, action="curate", target=str(row["public_id"]), now=ts)
    return row


# --- copies of documents of another server (KNOWLEDGE_FEDERATION_DESIGN.md Sections 10 and 11, unit KF3) -------------------

# ``documents.source_type`` of a copy of a document of another server.
REMOTE_SOURCE_TYPE = "federation"
# Limits for one document of another server (federation design KS2).
REMOTE_CONTENT_MAX_BYTES = 512 * 1024
REMOTE_TITLE_MAX = 500
REMOTE_ENTITIES_MAX = 500
REMOTE_RELATIONS_MAX = 2000
# The key of the stored extraction of a copy that keeps the authority identifiers of its entities: ``{entity name: [identifier, ...]}``. ``_parse_extraction`` does not read it.
_SAME_AS_KEY = "same_as"
# The key of ``entities.props`` with the authority identifiers of an entity.
_ENTITY_SAME_AS = "same_as"


def _stored_authority_ids(raw_response: str) -> dict[str, list[str]]:
    try:
        data = json.loads(raw_response)
    except ValueError:
        return {}
    value = data.get(_SAME_AS_KEY) if isinstance(data, dict) else None
    if not isinstance(value, dict):
        return {}
    return {str(name): [str(v) for v in found] for name, found in value.items() if isinstance(found, list) and found}


def _entities_with_authority_ids(conn: sqlite3.Connection, kb_id: int) -> list[tuple[str, str, str, list[str], list[str]]]:
    """The entities of K that have authority identifiers: ``(id, name, norm_name, aliases, identifiers)``."""
    out = []
    for row in conn.execute("SELECT id, name, norm_name, aliases, props FROM entities WHERE kb_id=? AND props LIKE ?", (int(kb_id), f'%"{_ENTITY_SAME_AS}"%')).fetchall():
        try:
            props = json.loads(row["props"] or "{}")
            aliases = json.loads(row["aliases"] or "[]")
        except ValueError:
            continue
        found = props.get(_ENTITY_SAME_AS) if isinstance(props, dict) else None
        if isinstance(found, list) and found:
            out.append((str(row["id"]), str(row["name"]), str(row["norm_name"]), [str(a) for a in aliases] if isinstance(aliases, list) else [], [str(v) for v in found]))
    return out


def _entity_with_authority_id(conn: sqlite3.Connection, kb_id: int, identifiers: list[str]) -> str | None:
    """The name of an entity of K that has one of the authority identifiers, or None (the entity with the lowest ID when there is more than one)."""
    wanted = set(identifiers)
    for _id, name, _norm, _aliases, found in sorted(_entities_with_authority_ids(conn, kb_id)):
        if wanted & set(found):
            return name
    return None


def _add_authority_ids(conn: sqlite3.Connection, kb_id: int, entity_id: str, identifiers: list[str]) -> None:
    """Add authority identifiers to an entity of K. Commits, as each other step of the graph build."""
    row = conn.execute("SELECT props FROM entities WHERE id=? AND kb_id=?", (entity_id, int(kb_id))).fetchone()
    if row is None:
        return
    try:
        props = json.loads(row["props"] or "{}")
    except ValueError:
        props = {}
    if not isinstance(props, dict):
        props = {}
    current = props.get(_ENTITY_SAME_AS) if isinstance(props.get(_ENTITY_SAME_AS), list) else []
    merged = sorted({str(v) for v in current} | set(identifiers))
    if merged != current:
        props[_ENTITY_SAME_AS] = merged
        conn.execute("UPDATE entities SET props=? WHERE id=? AND kb_id=?", (json.dumps(props, ensure_ascii=False), entity_id, int(kb_id)))
        conn.commit()


def authority_ids_of(conn: sqlite3.Connection, kb_id: int, names: list[str]) -> dict[str, list[str]]:
    """For the entity names of an extraction of K: the authority identifiers of the entity of K with that name or alias (``vocabulary.build_extraction``). v1 sends an identifier only when the entity has one (federation design Section 10.4 rule 2)."""
    from .ontology.base import normalize_name

    by_key: dict[str, list[str]] = {}
    for _id, _name, norm, aliases, found in _entities_with_authority_ids(conn, kb_id):
        for key in {norm, *(normalize_name(a) for a in aliases)}:
            by_key.setdefault(key, found)
    return {name: by_key[normalize_name(name)] for name in names if normalize_name(name) in by_key}


def remote_article_refusal(article: dict[str, Any]) -> str | None:
    """The limit that a document of another server is over (KS2), or None. The caller validated the ``Article`` with ``vocabulary.validate_object``."""
    from .knowledge_federation import vocabulary

    if len(str(article["content"]).encode("utf-8")) > REMOTE_CONTENT_MAX_BYTES:
        return "document_too_large"
    if len(str(article["name"])) > REMOTE_TITLE_MAX:
        return "title_too_long"
    extraction = article[vocabulary.EXTRACTION_OF]
    if len(extraction[vocabulary.ENTITIES]) > REMOTE_ENTITIES_MAX or len(extraction[vocabulary.RELATIONS]) > REMOTE_RELATIONS_MAX:
        return "extraction_too_large"
    return None


def copy_remote_document(conn: sqlite3.Connection, *, article: dict[str, Any], target_kb_id: int, hidden: bool, now: float | None = None) -> str:
    """Make one ``documents`` row and its ``extractions`` row in ``target_kb_id`` from the ``Article`` of another server with its ``claire:Extraction``. No graph, no files, no commit.

    The caller validated the ``Article`` (``vocabulary.validate_object`` gives ``document``, with the extraction) and its limits (``remote_article_refusal``). The HTML goes through the ``nh3`` allowlist sanitizer here (KS2). The stored extraction has the form of ``ExtractionResult``, so the merge job builds the graph without an LLM call; the authority identifiers of the entities are in the key ``same_as``.
    """
    import nh3

    from .knowledge_federation import vocabulary

    ts = time.time() if now is None else now
    extraction = article[vocabulary.EXTRACTION_OF]
    html = nh3.clean(str(article["content"]))
    result = {
        "summary": extraction["summary"],
        "key_claims": list(extraction[vocabulary.KEY_CLAIMS]),
        "entities": [{"name": e["name"], "type": e["type"], "aliases": list(e["aliases"]), "observations": list(e["observations"]), "proposed_type": e.get("proposed_type")} for e in extraction[vocabulary.ENTITIES]],
        "relations": [{"source": r["source"], "target": r["target"], "type": r["type"], "proposed_type": r.get("proposed_type")} for r in extraction[vocabulary.RELATIONS]],
        _SAME_AS_KEY: {e["name"]: list(e["sameAs"]) for e in extraction[vocabulary.ENTITIES] if e.get("sameAs")},
    }
    target_id = new_id("doc")
    meta = {"federation": {"origin": article["id"], "root": article[vocabulary.ROOT], "attributed_to": article.get("attributedTo")}}
    url = article.get("url")
    conn.execute(
        "INSERT INTO documents (id, kb_id, url, canonical_url, title, published_at, fetched_at, raw_text, source_type, content_hash, partial, meta, detail_format, detail_html, seen, watch_enabled, pinned, hidden) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 0, ?, 'adoc', ?, 1, 0, 0, ?)",
        (target_id, int(target_kb_id), url, url, article["name"], article["published"], ts, re.sub(r"\s+", " ", re.sub(r"<[^>]*>", " ", html)).strip(), REMOTE_SOURCE_TYPE, article[vocabulary.CONTENT_HASH], json.dumps(meta, ensure_ascii=False), html, 1 if hidden else 0),
    )
    conn.execute(
        "INSERT INTO extractions (kb_id, document_id, provider, model, prompt_version, raw_response, created_at) VALUES (?, ?, ?, NULL, NULL, ?, ?)",
        (int(target_kb_id), target_id, REMOTE_SOURCE_TYPE, json.dumps(result, ensure_ascii=False), ts),
    )
    return target_id


def curate_remote(conn: sqlite3.Connection, *, kb_id: int, source_kb_iri: str, article: dict[str, Any], curator_account_id: int | None, now: float | None = None) -> dict[str, Any]:
    """Copy one document of a shared knowledge base of another server into K as a contribution with the origin ``curation`` (federation design Section 11 rule 2). The curator approves it; its merge job waits. The caller commits.

    ``article`` is the ``Article`` with its extraction that the sidecar read with a signed GET; the caller validated it. The credit is the actor of ``attributedTo`` (it can be absent); the root is ``claire:root``; ``origin`` of the copy is the ``id`` of the article. The duplicate and tombstone checks are those of a local curation; a document that K has a contribution of gives ``contribution_duplicate``, and one that K removed gives ``contribution_tombstoned``.
    """
    from .knowledge_federation import vocabulary

    ts = time.time() if now is None else now
    known = kb_collab.remote_sources_in_kb(conn, kb_id).get(str(article["id"]))
    if known is not None:
        if known["state"] == "removed":
            raise ContributionError(409, "contribution_tombstoned")
        raise ContributionError(409, "contribution_duplicate", document=known["document_id"], visible=known["state"] == "visible")
    check_new_source(conn, kb_id, canonical_url=article.get("url"), content_hash=article[vocabulary.CONTENT_HASH])
    target_id = copy_remote_document(conn, article=article, target_kb_id=kb_id, hidden=True, now=ts)
    public_id = kb_collab.add_contribution(
        conn, kb_id=kb_id, document_id=target_id, origin="curation", state="merging", contributor_account_id=None, curator_account_id=curator_account_id,
        approved_at=ts, approved_by_account_id=curator_account_id,
        remote={"source_kb_iri": source_kb_iri, "source_iri": str(article["id"]), "root_iri": str(article[vocabulary.ROOT]), "credit_iri": article.get("attributedTo")}, now=ts,
    )
    add_merge_job(conn, kb_id=kb_id, contribution=public_id, now=ts)
    kb_collab.log_moderation(conn, kb_id=kb_id, actor_account_id=curator_account_id, action="curate", target=str(public_id), now=ts)
    row = kb_collab.get_contribution(conn, public_id)
    assert row is not None
    return row


def start_remote_revisions(conn: sqlite3.Connection, *, source_kb_iri: str, article: dict[str, Any], now: float | None = None) -> list[int]:
    """The origin sent a revision of a document (``Update``, federation design Section 10.2): start the revision of each copy of that document on this server. No approval. No commit: a handler of the inbox journal calls this function. Return the IDs of the new merge jobs.

    The rules are those of ``start_revisions``: a contribution with stopped updates and a closed one keep their version; the state stays (a hidden copy stays hidden); the same content makes no revision; the newest version wins. Differences: only a copy from the knowledge base actor ``source_kb_iri`` changes; a revision that is closed here keeps its copy until its merge job removes it (``_run_revision``); a contribution in ``failed`` keeps its version until a retry completes its merge.
    """
    from .knowledge_federation import vocabulary

    ts = time.time() if now is None else now
    content_hash = str(article[vocabulary.CONTENT_HASH])
    jobs: list[int] = []
    for row in kb_collab.contributions_of_remote_source(conn, str(article["id"])):
        if row["remote_source_kb_iri"] != source_kb_iri or row["updates_frozen"] or row["state"] not in ("visible", "hidden", "merging") or not row["document_id"]:
            continue
        kb_id, public_id = int(row["kb_id"]), int(row["public_id"])
        current = conn.execute("SELECT content_hash FROM documents WHERE id=? AND kb_id=?", (row["document_id"], kb_id)).fetchone()
        same_as_copy = current is not None and str(current["content_hash"] or "") == content_hash
        queued = None if same_as_copy else next((int(r["id"]) for r in kb_collab.list_revisions(conn, public_id) if r["state"] == "merging" and str(r["content_hash"] or "") == content_hash), None)
        _waiting_revision_copies(conn, public_id, keep=queued)
        if same_as_copy or queued is not None:
            continue
        new_copy = copy_remote_document(conn, article=article, target_kb_id=kb_id, hidden=True, now=ts)
        revision = kb_collab.add_revision(conn, contribution_public_id=public_id, previous_document_id=str(row["document_id"]), document_id=new_copy, content_hash=content_hash, now=ts)
        jobs.append(add_merge_job(conn, kb_id=kb_id, contribution=public_id, revision=revision, now=ts))
    return jobs


def origin_removed(conn: sqlite3.Connection, *, source_kb_iri: str, source_iri: str, reason: str, now: float | None = None) -> int:
    """The origin removed a document (``Delete``, federation design Section 10.3, KD1): hide each copy of it on this server at once and put it in the review queue of its knowledge base, with the reason code of the origin. No copy is removed. No commit. Return the number of copies.

    The review queue has a contribution with ``origin_removed_at`` and no ``origin_decided_at``. The reviewers decide: ``set_hidden(hidden=False)`` shows the copy again, ``remove`` removes it. A contribution in ``merging`` gets the mark; its merge job then leaves it hidden.
    """
    if reason not in REASON_CODES:
        raise ContributionError(400, "reason_invalid")
    ts = time.time() if now is None else now
    count = 0
    for row in kb_collab.contributions_of_remote_source(conn, source_iri):
        if row["remote_source_kb_iri"] != source_kb_iri or row["state"] in ("removed", "rejected") or row["origin_removed_at"] is not None:
            continue
        changes: dict[str, Any] = {"origin_removed_at": ts, "origin_removed_reason": reason, "origin_decided_at": None}
        if row["state"] == "visible":
            conn.execute("UPDATE documents SET hidden=1 WHERE id=? AND kb_id=?", (row["document_id"], int(row["kb_id"])))
            changes["state"] = "hidden"
        kb_collab.update_contribution(conn, int(row["public_id"]), now=ts, **changes)
        kb_collab.log_moderation(conn, kb_id=int(row["kb_id"]), actor_account_id=None, action="origin_removed", target=str(row["public_id"]), reason=reason, now=ts)
        count += 1
    return count


def in_origin_review(row: dict[str, Any]) -> bool:
    """True when the contribution waits in the review queue because its origin removed the document (federation design Section 10.3)."""
    return row["origin_removed_at"] is not None and row["origin_decided_at"] is None and row["state"] not in ("removed", "rejected")


# --- fork (Section 9.8) ----------------------------------------------------------------------------------------------


def _columns(conn: sqlite3.Connection, table: str) -> list[str]:
    return [str(r[1]) for r in conn.execute(f"PRAGMA table_info({table})")]


def _mapped_sources(raw: Any, doc_map: dict[str, str]) -> list[str]:
    try:
        sources = json.loads(raw or "[]")
    except (TypeError, ValueError):
        sources = []
    return [doc_map[str(d)] for d in sources if str(d) in doc_map] if isinstance(sources, list) else []


def fork_copy(conn: sqlite3.Connection, data_dir: Path, *, source_kb_id: int, target_kb_id: int, account_id: int, now: float | None = None) -> dict[str, int]:
    """Copy the visible documents of the shared knowledge base ``source_kb_id`` into the new shared knowledge base ``target_kb_id`` with their extractions, entities, relations, embeddings, and FTS rows (design Section 9.8 rules 3 to 6). No LLM call and no new embedding. Commits.

    A document is copied when it is not hidden and its contribution is ``visible``. An entity or a relation is copied when one of its source documents is copied; its sources become the copies. Each copied document gets a contribution with the origin ``fork``, the credit and the root of the source contribution, and the approval at the fork time. The fork gets the embedding model and the themes of its source.

    The order: (1) all rows, in one transaction, then the commit; (2) the files of each copy, with one short transaction for each document (the image paths in its ``meta`` and its body). The write lock of the database is thus not held while files are copied. A failure in step 1 rolls back and leaves nothing. A failure in step 2 deletes the files that this call copied and raises: the rows of the fork are committed then, and the caller deletes the new knowledge base with its rows (a fork that did not complete does not stay). When the process stops in step 2, the documents without their own files still show the image files of their source documents.
    """
    from .store import themes as themes_store

    ts = time.time() if now is None else now
    source_kb_id, target_kb_id = int(source_kb_id), int(target_kb_id)
    data_dir = Path(data_dir)
    doc_map: dict[str, str] = {}
    for origin in kb_collab.list_contributions(conn, source_kb_id, state="visible"):
        document_id = origin["document_id"]
        if not document_id or conn.execute("SELECT 1 FROM documents WHERE id=? AND kb_id=? AND hidden=0", (document_id, source_kb_id)).fetchone() is None:
            continue
        try:
            copy_id = copy_document(conn, source_document_id=str(document_id), source_kb_id=source_kb_id, target_kb_id=target_kb_id, hidden=False)
        except ContributionError:
            continue  # a document with no extraction has no graph to copy
        doc_map[str(document_id)] = copy_id
        kb_collab.add_contribution(
            conn, kb_id=target_kb_id, document_id=copy_id, origin="fork", state="visible", contributor_account_id=origin["contributor_account_id"], curator_account_id=account_id,
            source_kb_id=source_kb_id, source_document_id=str(document_id), root_kb_id=origin["root_kb_id"], root_document_id=origin["root_document_id"],
            approved_at=ts, approved_by_account_id=account_id, now=ts,
            # A copy of a document of another server keeps its origin, its root, and its credit in the fork, so that it gets the revisions and the removal of the origin (federation design Section 10.1).
            remote=None if not origin["remote_source_iri"] else {"source_kb_iri": origin["remote_source_kb_iri"], "source_iri": origin["remote_source_iri"], "root_iri": origin["remote_root_iri"], "credit_iri": origin["remote_credit_iri"]},
        )

    entity_columns = _columns(conn, "entities")
    entity_map: dict[str, str] = {}
    for row in conn.execute(f"SELECT {', '.join(entity_columns)} FROM entities WHERE kb_id=?", (source_kb_id,)).fetchall():
        values = dict(zip(entity_columns, tuple(row)))
        sources = _mapped_sources(values["sources"], doc_map)
        if not sources:
            continue
        copy_id = new_id("ent")
        entity_map[str(values["id"])] = copy_id
        values.update({"id": copy_id, "kb_id": target_kb_id, "sources": json.dumps(sources)})
        conn.execute(f"INSERT INTO entities ({', '.join(entity_columns)}) VALUES ({', '.join('?' for _ in entity_columns)})", tuple(values[c] for c in entity_columns))
        conn.execute("INSERT INTO entities_fts (entity_id, name, body) SELECT ?, name, body FROM entities_fts WHERE entity_id=?", (copy_id, str(row["id"])))
        conn.execute(
            "INSERT INTO embeddings (owner_id, kb_id, dim, vector, model, updated_at) SELECT ?, ?, dim, vector, model, updated_at FROM embeddings WHERE owner_id=? AND kb_id=?",
            (copy_id, target_kb_id, str(row["id"]), source_kb_id),
        )

    relation_columns = _columns(conn, "relations")
    relations = 0
    for row in conn.execute(f"SELECT {', '.join(relation_columns)} FROM relations WHERE kb_id=?", (source_kb_id,)).fetchall():
        values = dict(zip(relation_columns, tuple(row)))
        source_id, target_id = entity_map.get(str(values["source_id"])), entity_map.get(str(values["target_id"]))
        sources = _mapped_sources(values["sources"], doc_map)
        if source_id is None or target_id is None or not sources:
            continue
        values.update({"id": new_id("rel"), "kb_id": target_kb_id, "source_id": source_id, "target_id": target_id, "sources": json.dumps(sources)})
        conn.execute(f"INSERT INTO relations ({', '.join(relation_columns)}) VALUES ({', '.join('?' for _ in relation_columns)})", tuple(values[c] for c in relation_columns))
        relations += 1

    # The log of the entity decisions of each copy points to the entities of the fork, so that a later purge of the copy can undo them. One walk of each log with the ID map: each string that is the ID of a copied entity (a value or a key, in each field of ``ResolutionDecision``) becomes the ID of its copy.
    for document_id, copy_id in doc_map.items():
        row = conn.execute("SELECT meta FROM documents WHERE id=? AND kb_id=?", (document_id, source_kb_id)).fetchone()
        try:
            source_meta = json.loads(row["meta"] or "{}")
        except (TypeError, ValueError):
            continue
        log_rows = source_meta.get("resolution_log") if isinstance(source_meta, dict) else None
        if not isinstance(log_rows, list) or not log_rows:
            continue
        copy = conn.execute("SELECT meta FROM documents WHERE id=? AND kb_id=?", (copy_id, target_kb_id)).fetchone()
        meta = json.loads(copy["meta"] or "{}")
        meta["resolution_log"] = _remap_ids(log_rows, entity_map)
        conn.execute("UPDATE documents SET meta=? WHERE id=? AND kb_id=?", (json.dumps(meta, ensure_ascii=False), copy_id, target_kb_id))

    conn.execute(
        "UPDATE knowledge_bases SET embed_provider=(SELECT embed_provider FROM knowledge_bases WHERE id=?), embed_model=(SELECT embed_model FROM knowledge_bases WHERE id=?), forked_from_kb_id=?, forked_at=? WHERE id=?",
        (source_kb_id, source_kb_id, source_kb_id, ts, target_kb_id),
    )
    for theme_id in themes_store.themes_of_kb(conn, source_kb_id):
        themes_store.add_theme_member(conn, theme_id=theme_id, kb_id=target_kb_id, source="fork", now=ts)
    conn.commit()

    # Step 2: the files, after the commit of the rows.
    try:
        for document_id, copy_id in doc_map.items():
            copy_document_files(conn, data_dir, source_document_id=document_id, target_document_id=copy_id, kb_id=target_kb_id)
            conn.commit()
    except Exception:
        conn.rollback()
        for copy_id in doc_map.values():
            _delete_copy_files(data_dir, copy_id)
        raise
    return {"documents": len(doc_map), "entities": len(entity_map), "relations": relations}


def _remap_ids(value: Any, id_map: dict[str, str]) -> Any:
    """A copy of a JSON value where each string that is a key of ``id_map`` (as a value or as a key of an object) is replaced."""
    if isinstance(value, str):
        return id_map.get(value, value)
    if isinstance(value, list):
        return [_remap_ids(item, id_map) for item in value]
    if isinstance(value, dict):
        return {id_map.get(key, key): _remap_ids(item, id_map) for key, item in value.items()}
    return value


def _delete_copy_files(data_dir: Path, document_id: str) -> None:
    """Delete the files of one copy (the names that ``copy_document_files`` writes). A file that is not there is no error."""
    data_dir = Path(data_dir)
    images = data_dir / "images"
    paths = list(images.glob(f"{document_id}_*")) if images.is_dir() else []
    paths += [data_dir / "raw" / "artifacts" / f"{document_id}{suffix}" for suffix in (".txt.zst", ".txt.gz")]
    for path in paths:
        if path.is_file():
            path.unlink()
    attachments = data_dir / "raw" / "attachments" / document_id
    if attachments.is_dir():
        shutil.rmtree(attachments)


def direct_mode(conn: sqlite3.Connection, *, kb_id: int, account_id: int, is_reviewer: bool, is_admin: bool, now: float | None = None) -> tuple[bool, bool]:
    """How a direct contribution of the account enters K: ``(approved, pending)`` (design Sections 6.2 rule 2 and 8.6).

    With the reputation ``low`` each contribution waits for approval, also the one of a collaborator or a moderator; the pending limit of the level applies (409 ``pending_limit``). Else a member contributes directly, and a reviewer needs no approval. The admin has no reputation rule.
    """
    from . import reputation

    if is_admin:
        return True, False
    level = reputation.level(conn, account_id, now=now)
    if not reputation.needs_approval(level):
        return is_reviewer, False
    limit = reputation.pending_limit(reputation.read_settings(conn), level)
    if kb_collab.pending_count(conn, kb_id=kb_id, account_id=account_id) >= limit:
        raise ContributionError(409, "pending_limit", limit=limit)
    return False, True
