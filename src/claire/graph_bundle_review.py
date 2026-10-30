"""Review evidence export for graph bundles.

A System 1 decision engine (TypeSafe AI Jev) is still under adoption review, so Jev never decides.
The review is verified against the current resolution pipeline instead: this module copies that
pipeline's own decision evidence into review_* tables of graph_export.db so that a candidate engine
can be replayed offline against it. When Jev shadow evaluation is enabled, its per-candidate
probabilities recorded next to the pipeline's own outcomes are flattened into review_shadow_pairs.
Raw embedding vectors and secrets stay out of the bundle.
See docs/design/GRAPH_BUNDLE_REVIEW_EVIDENCE_DESIGN.md and docs/design/JEV_SHADOW_EVALUATION_DESIGN.md.
"""

from __future__ import annotations

import heapq
import json
import logging
import operator
import sqlite3
import time
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit, urlunsplit

from .extract.resolver import ACRONYM_MIN, AUTO_MERGE, CANDIDATE_FLOOR, MAX_JUDGE
from .store import db as dbm
from .store.telemetry import get_telemetry_db_path
from .store.vectors import looks_like_hash_fallback, unpack_vector

logger = logging.getLogger(__name__)

REVIEW_SCHEMA_VERSION = 2
SHADOW_AGREEMENT_THRESHOLDS = (0.5, 0.9)
LLM_VERDICT_OUTCOMES = {"judge_same": True, "judge_different": False}
# Heatmaps labelled "jev" before the shadow client existed came from a local difflib stub; no Jev call was made
HEATMAP_GENERATOR_NOTES = {
    "jev": "legacy local stub (difflib + recorded decisions); no Jev API call was made, not Jev evidence",
}
PAIR_NEIGHBORS = 10
PAIR_ENTITY_LIMIT = 20000
PAIR_MATRIX_BYTES_LIMIT = 128 * 1024 * 1024
PURE_PYTHON_PAIR_LIMIT = 400
_PAIR_BLOCK_ROWS = 256
JUDGE_CALL_TYPES = ("judge_same_entity", "judge_relationship")

_REVIEW_SCHEMA = """
CREATE TABLE review_meta (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL
);
CREATE TABLE review_decisions (
    document_id TEXT NOT NULL,
    seq INTEGER NOT NULL,
    entity TEXT,
    stage TEXT,
    decision TEXT,
    candidate TEXT,
    score REAL,
    reason TEXT,
    target_entity_id TEXT,
    source_entity_id TEXT,
    timestamp REAL,
    rollback_payload TEXT,
    candidates TEXT,
    input_snapshot TEXT,
    gate_params TEXT,
    shadow TEXT,
    PRIMARY KEY (document_id, seq)
);
CREATE TABLE review_heatmaps (
    document_id TEXT PRIMARY KEY,
    generated_by TEXT,
    rows_count INTEGER,
    cols_count INTEGER,
    threshold_auto_merge REAL,
    threshold_borderline REAL,
    created_at REAL
);
CREATE TABLE review_candidate_pairs (
    entity_a TEXT NOT NULL,
    entity_b TEXT NOT NULL,
    score REAL NOT NULL,
    band TEXT NOT NULL,
    PRIMARY KEY (entity_a, entity_b)
);
CREATE TABLE review_judge_calls (
    id INTEGER PRIMARY KEY,
    timestamp REAL,
    document_id TEXT,
    provider TEXT,
    model TEXT,
    call_type TEXT,
    input_chars INTEGER,
    duration_ms INTEGER,
    status TEXT
);
CREATE TABLE review_shadow_pairs (
    document_id TEXT NOT NULL,
    seq INTEGER NOT NULL,
    candidate_id TEXT NOT NULL,
    entity TEXT,
    entity_type TEXT,
    candidate_name TEXT,
    candidate_type TEXT,
    origin TEXT,
    vector_score REAL,
    outcome TEXT,
    judge_ms REAL,
    jev_noul REAL,
    jev_status TEXT,
    jev_model TEXT,
    final_stage TEXT,
    final_decision TEXT,
    merged INTEGER NOT NULL,
    PRIMARY KEY (document_id, seq, candidate_id)
);
CREATE INDEX idx_review_decisions_stage ON review_decisions(stage);
CREATE INDEX idx_review_decisions_target ON review_decisions(target_entity_id);
CREATE INDEX idx_review_pairs_band ON review_candidate_pairs(band);
CREATE INDEX idx_review_pairs_b ON review_candidate_pairs(entity_b);
CREATE INDEX idx_review_judge_calls_type ON review_judge_calls(call_type);
CREATE INDEX idx_review_shadow_outcome ON review_shadow_pairs(outcome);
"""

REVIEW_TABLES = (
    "review_meta",
    "review_decisions",
    "review_heatmaps",
    "review_candidate_pairs",
    "review_judge_calls",
    "review_shadow_pairs",
)


def _json_or_none(value: Any) -> str | None:
    """Serialize nested evidence fields; keep SQL NULL for absent values."""
    if value is None:
        return None
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"))


def _float_or_none(value: Any) -> float | None:
    try:
        return float(value) if value is not None else None
    except (TypeError, ValueError):
        return None


def _safe_url(raw: Any) -> str:
    """Drop userinfo, query and fragment so a configured URL cannot carry credentials."""
    text = str(raw or "").strip()
    if not text:
        return ""
    try:
        parts = urlsplit(text)
    except ValueError:
        return ""
    host = parts.hostname or ""
    if parts.port:
        host = f"{host}:{parts.port}"
    return urlunsplit((parts.scheme, host, parts.path, "", ""))


def review_thresholds(settings: Any) -> dict[str, Any]:
    """Resolution thresholds currently in effect (resolver defaults when the setting is absent)."""
    return {
        "auto": float(getattr(settings, "sim_tier_auto_merge", AUTO_MERGE)),
        "borderline": float(getattr(settings, "sim_tier_borderline", CANDIDATE_FLOOR)),
        "relational": float(getattr(settings, "sim_tier_relational", 0.70)),
        "multihop": float(getattr(settings, "sim_tier_multihop", 0.55)),
        "adaptive_center": bool(getattr(settings, "vector_adaptive_centering", False)),
        "max_judge": MAX_JUDGE,
        "acronym_min": ACRONYM_MIN,
    }


def _pipeline_snapshot(settings: Any) -> dict[str, Any]:
    """Configuration facts a reviewer needs; secrets are reduced to presence flags."""
    effective = None
    try:
        effective = getattr(settings, "effective_provider", None)
    except Exception as exc:  # noqa: BLE001
        logger.warning("effective_provider lookup failed: %s", type(exc).__name__)
    return {
        "provider": getattr(settings, "provider", None),
        "effective_provider": effective,
        "enable_jev": bool(getattr(settings, "enable_jev", False)),
        "jev_api_key_configured": bool(str(getattr(settings, "jev_api_key", "") or "").strip()),
        "jev_base_url": _safe_url(getattr(settings, "jev_base_url", "")),
        "jev_model": getattr(settings, "jev_model", None),
        "jev_role": "shadow",
        "gemini_api_key_configured": bool(str(getattr(settings, "gemini_api_key", "") or "").strip()),
        "embed_model": getattr(settings, "gemini_embed_model", None),
        "embed_task_type": getattr(settings, "embed_task_type", None),
    }


def _band(score: float, thresholds: dict[str, Any]) -> str:
    if score >= thresholds["auto"]:
        return "auto_merge"
    if score >= thresholds["borderline"]:
        return "borderline"
    if score >= thresholds["relational"]:
        return "relational"
    return "multihop"


def _export_decisions_and_heatmaps(
    source_conn: sqlite3.Connection,
    export_conn: sqlite3.Connection,
) -> dict[str, Any]:
    """Flatten documents.meta resolution_log and heatmap provenance into review tables."""
    doc_cols = {r["name"] for r in source_conn.execute("PRAGMA table_info(documents)").fetchall()}
    if "meta" not in doc_cols:
        return {"status": "missing", "reason": "documents.meta column not found"}

    decision_rows: list[tuple[Any, ...]] = []
    heatmap_rows: list[tuple[Any, ...]] = []
    shadow_rows: list[tuple[Any, ...]] = []
    shadow_metas: list[dict[str, Any]] = []
    docs_with_log = 0
    with_candidates = 0
    outcomes: Counter[str] = Counter()
    generators: Counter[str] = Counter()

    cursor = source_conn.execute(
        "SELECT id, meta FROM documents "
        "WHERE meta LIKE '%\"resolution_log\"%' OR meta LIKE '%\"heatmap_matrix\"%'"
    )
    for row in cursor:
        try:
            meta = json.loads(row["meta"]) if isinstance(row["meta"], str) else row["meta"]
        except (TypeError, ValueError):
            continue
        if not isinstance(meta, dict):
            continue

        log = meta.get("resolution_log")
        if isinstance(log, list) and log:
            docs_with_log += 1
            for seq, item in enumerate(log):
                if not isinstance(item, dict):
                    continue
                candidates = item.get("candidates") if isinstance(item.get("candidates"), list) else None
                if candidates:
                    with_candidates += 1
                    outcomes.update(
                        str(c.get("outcome")) for c in candidates if isinstance(c, dict)
                    )
                decision_rows.append((
                    row["id"],
                    seq,
                    item.get("entity"),
                    item.get("stage"),
                    item.get("decision"),
                    item.get("candidate"),
                    _float_or_none(item.get("score")),
                    item.get("reason"),
                    item.get("target_entity_id"),
                    item.get("source_entity_id"),
                    _float_or_none(item.get("timestamp")),
                    _json_or_none(item.get("rollback_payload")),
                    _json_or_none(candidates),
                    _json_or_none(item.get("input_snapshot")),
                    _json_or_none(item.get("gate_params")),
                    _json_or_none(item.get("shadow")),
                ))
                shadow = item.get("shadow")
                if isinstance(shadow, dict):
                    shadow_metas.append(shadow)
                    shadow_rows.extend(_shadow_pair_rows(row["id"], seq, item, shadow, candidates or []))

        heatmap = meta.get("heatmap_matrix")
        if isinstance(heatmap, dict):
            generated_by = str(heatmap.get("generated_by") or "")
            generators[generated_by] += 1
            heatmap_rows.append((
                row["id"],
                generated_by,
                len(heatmap.get("rows") or []),
                len(heatmap.get("cols") or []),
                _float_or_none(heatmap.get("threshold_auto_merge")),
                _float_or_none(heatmap.get("threshold_borderline")),
                _float_or_none(heatmap.get("created_at")),
            ))

    export_conn.executemany(
        "INSERT INTO review_decisions VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        decision_rows,
    )
    export_conn.executemany(
        "INSERT OR IGNORE INTO review_shadow_pairs VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        shadow_rows,
    )
    export_conn.executemany(
        "INSERT INTO review_heatmaps VALUES (?, ?, ?, ?, ?, ?, ?)",
        heatmap_rows,
    )
    return {
        "status": "ok",
        "documents_with_resolution_log": docs_with_log,
        "decisions": len(decision_rows),
        "decisions_with_candidates": with_candidates,
        "candidate_outcomes": dict(sorted(outcomes.items())),
        "heatmaps": len(heatmap_rows),
        "heatmaps_by_generator": dict(sorted(generators.items())),
        "heatmap_generator_notes": {g: HEATMAP_GENERATOR_NOTES[g] for g in generators if g in HEATMAP_GENERATOR_NOTES},
        "shadow": _shadow_summary(shadow_metas, shadow_rows),
    }


def _shadow_pair_rows(
    document_id: str,
    seq: int,
    item: dict[str, Any],
    shadow: dict[str, Any],
    candidates: list[Any],
) -> list[tuple[Any, ...]]:
    """One row per traced candidate of a decision that carries a Jev shadow record."""
    snapshot = item.get("input_snapshot") if isinstance(item.get("input_snapshot"), dict) else {}
    merged_id = item.get("target_entity_id") if item.get("decision") == "MERGE" else None
    rows: list[tuple[Any, ...]] = []
    for cand in candidates:
        if not isinstance(cand, dict) or not cand.get("id"):
            continue
        rows.append((
            document_id,
            seq,
            cand["id"],
            item.get("entity"),
            snapshot.get("type"),
            cand.get("name"),
            cand.get("type"),
            cand.get("origin"),
            _float_or_none(cand.get("score")),
            cand.get("outcome"),
            _float_or_none(cand.get("judge_ms")),
            _float_or_none(cand.get("jev_noul")),
            shadow.get("status"),
            shadow.get("model"),
            item.get("stage"),
            item.get("decision"),
            int(merged_id is not None and merged_id == cand["id"]),
        ))
    return rows


def _shadow_summary(metas: list[dict[str, Any]], rows: list[tuple[Any, ...]]) -> dict[str, Any]:
    """Jev shadow coverage, latency and agreement with the System 2 judge on the same pairs."""
    if not metas:
        return {"status": "none", "decisions_with_shadow": 0}
    latencies = [
        m["latency_ms"] for m in metas
        if m.get("status") in ("ok", "partial") and isinstance(m.get("latency_ms"), (int, float))
    ]
    tokens = [m["input_tokens"] for m in metas if isinstance(m.get("input_tokens"), int)]
    scored = [(r[9], r[11]) for r in rows if r[11] is not None]

    agreement: dict[str, dict[str, int]] = {}
    for threshold in SHADOW_AGREEMENT_THRESHOLDS:
        cell: Counter[str] = Counter()
        for outcome, noul in scored:
            if outcome not in LLM_VERDICT_OUTCOMES:
                continue
            llm_same = LLM_VERDICT_OUTCOMES[outcome]
            jev_same = noul >= threshold
            cell[("both_same" if jev_same else "jev_different_llm_same") if llm_same
                 else ("jev_same_llm_different" if jev_same else "both_different")] += 1
        agreement[f"{threshold:g}"] = dict(sorted(cell.items()))

    by_outcome: dict[str, dict[str, int]] = {}
    for outcome, noul in scored:
        bucket = by_outcome.setdefault(str(outcome), {"scored": 0, "jev_same_at_0.5": 0})
        bucket["scored"] += 1
        bucket["jev_same_at_0.5"] += int(noul >= 0.5)

    return {
        "status": "ok",
        "engine": "jev",
        "role": "shadow (no decision authority)",
        "decisions_with_shadow": len(metas),
        "by_status": dict(sorted(Counter(str(m.get("status")) for m in metas).items())),
        "models": dict(sorted(Counter(str(m.get("model")) for m in metas if m.get("model")).items())),
        "latency_ms_p50": _percentile(latencies, 0.50),
        "latency_ms_p95": _percentile(latencies, 0.95),
        "latency_ms_max": max(latencies, default=None),
        "input_tokens_total": sum(tokens),
        "pairs": len(rows),
        "pairs_scored": len(scored),
        "agreement_with_llm_judge": agreement,
        "scored_by_outcome": dict(sorted(by_outcome.items())),
    }


def _top_k_pairs_numpy(
    ids: list[str],
    vectors: Any,
    *,
    k: int,
    floor: float,
    center: bool,
) -> dict[tuple[str, str], float]:
    import numpy as np

    matrix = np.asarray(vectors, dtype=np.float32)
    if center:
        matrix = matrix - matrix.mean(axis=0, keepdims=True)
    norms = np.linalg.norm(matrix, axis=1)
    norms[norms == 0] = 1.0
    matrix = matrix / norms[:, None]

    n = len(ids)
    k_eff = min(k, n - 1)
    pairs: dict[tuple[str, str], float] = {}
    for start in range(0, n, _PAIR_BLOCK_ROWS):
        stop = min(start + _PAIR_BLOCK_ROWS, n)
        sims = matrix[start:stop] @ matrix.T
        local = np.arange(stop - start)
        sims[local, start + local] = -np.inf
        top = np.argpartition(-sims, k_eff - 1, axis=1)[:, :k_eff]
        for r in range(stop - start):
            i = start + r
            for j in top[r]:
                score = float(sims[r, j])
                if score < floor:
                    continue
                key = (ids[i], ids[j]) if ids[i] < ids[j] else (ids[j], ids[i])
                if score > pairs.get(key, float("-inf")):
                    pairs[key] = score
    return pairs


def _top_k_pairs_python(
    ids: list[str],
    vectors: list[list[float]],
    *,
    k: int,
    floor: float,
    center: bool,
) -> dict[tuple[str, str], float]:
    dim = len(vectors[0])
    if center:
        mean = [sum(v[d] for v in vectors) / len(vectors) for d in range(dim)]
        vectors = [[x - m for x, m in zip(v, mean)] for v in vectors]
    unit: list[list[float]] = []
    for v in vectors:
        norm = sum(x * x for x in v) ** 0.5 or 1.0
        unit.append([x / norm for x in v])

    pairs: dict[tuple[str, str], float] = {}
    for i, vi in enumerate(unit):
        sims = (
            (sum(map(operator.mul, vi, vj)), j) for j, vj in enumerate(unit) if j != i
        )
        for score, j in heapq.nlargest(k, sims):
            if score < floor:
                continue
            key = (ids[i], ids[j]) if ids[i] < ids[j] else (ids[j], ids[i])
            if score > pairs.get(key, float("-inf")):
                pairs[key] = score
    return pairs


def _export_candidate_pairs(
    source_conn: sqlite3.Connection,
    export_conn: sqlite3.Connection,
    entity_ids: set[str],
    thresholds: dict[str, Any],
) -> tuple[dict[str, Any], dict[str, Any]]:
    """Offline nearest-neighbour candidate pairs from stored entity embeddings (no vectors exported)."""
    tables = {r[0] for r in source_conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    floor = thresholds["multihop"]
    pair_meta: dict[str, Any] = {
        "k": PAIR_NEIGHBORS,
        "floor": floor,
        "centering": thresholds["adaptive_center"],
        "scope": "stored entity embeddings, hash fallback excluded, majority dimension only",
    }
    if "embeddings" not in tables:
        return {"status": "missing"}, {**pair_meta, "status": "skipped", "reason": "embeddings table not found"}

    dims: Counter[int] = Counter()
    hash_fallback = 0
    # Keep raw float32 blobs: unpacking thousands of 768~3072-dim vectors into Python lists costs hundreds of MB
    groups: dict[int, list[tuple[str, bytes]]] = {}
    for row in source_conn.execute("SELECT owner_id, vector FROM embeddings"):
        owner_id = row["owner_id"]
        blob = row["vector"]
        if owner_id not in entity_ids or not blob:
            continue
        blob = bytes(blob[: len(blob) // 4 * 4])
        if not blob:
            continue
        dim = len(blob) // 4
        dims[dim] += 1
        if looks_like_hash_fallback(memoryview(blob).cast("f")):
            hash_fallback += 1
            continue
        groups.setdefault(dim, []).append((owner_id, blob))

    pair_dim = max(groups, key=lambda d: len(groups[d])) if groups else 0
    members = sorted(groups.get(pair_dim, []))
    embedding_meta = {
        "entities_exported": len(entity_ids),
        "with_embedding": sum(dims.values()),
        "dims": {str(d): c for d, c in sorted(dims.items())},
        "hash_fallback": hash_fallback,
        "pair_dim": pair_dim,
        "used_for_pairs": len(members),
        "excluded_other_dim": sum(len(v) for d, v in groups.items() if d != pair_dim),
    }

    n = len(members)
    if n < 2:
        return embedding_meta, {**pair_meta, "status": "skipped", "reason": "fewer than 2 usable embeddings"}
    if n > PAIR_ENTITY_LIMIT or n * pair_dim * 4 > PAIR_MATRIX_BYTES_LIMIT:
        return embedding_meta, {
            **pair_meta, "status": "skipped",
            "reason": f"{n} x {pair_dim} matrix exceeds limits ({PAIR_ENTITY_LIMIT} entities, "
                      f"{PAIR_MATRIX_BYTES_LIMIT} bytes)",
        }

    ids = [m[0] for m in members]
    started = time.perf_counter()
    try:
        import numpy as np
    except ImportError:
        np = None
    if np is not None:
        matrix = np.frombuffer(b"".join(m[1] for m in members), dtype=np.float32).reshape(n, pair_dim)
        pairs = _top_k_pairs_numpy(ids, matrix, k=PAIR_NEIGHBORS, floor=floor, center=pair_meta["centering"])
        backend = "numpy"
    elif n > PURE_PYTHON_PAIR_LIMIT:
        return embedding_meta, {
            **pair_meta, "status": "skipped",
            "reason": f"numpy unavailable and {n} entities exceed pure-Python limit {PURE_PYTHON_PAIR_LIMIT}",
        }
    else:
        vectors = [unpack_vector(m[1]) for m in members]
        pairs = _top_k_pairs_python(ids, vectors, k=PAIR_NEIGHBORS, floor=floor, center=pair_meta["centering"])
        backend = "python"
    elapsed_ms = int((time.perf_counter() - started) * 1000)

    rows = [
        (a, b, round(score, 6), _band(score, thresholds))
        for (a, b), score in sorted(pairs.items())
    ]
    export_conn.executemany("INSERT INTO review_candidate_pairs VALUES (?, ?, ?, ?)", rows)
    bands = Counter(r[3] for r in rows)
    return embedding_meta, {
        **pair_meta,
        "status": "ok",
        "backend": backend,
        "pairs": len(rows),
        "by_band": dict(sorted(bands.items())),
        "elapsed_ms": elapsed_ms,
    }


def _export_judge_calls(
    data_dir: Path | str | None,
    export_conn: sqlite3.Connection,
    document_ids: set[str],
) -> dict[str, Any]:
    """Copy System 2 judge call latency/size telemetry for exported documents only (theme isolation)."""
    tel_path = get_telemetry_db_path(data_dir)
    if not tel_path.is_file():
        return {"status": "missing"}
    conn = dbm.connect_existing(tel_path, readonly=True)
    try:
        tables = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        if "provider_telemetry" not in tables:
            return {"status": "missing"}
        placeholders = ",".join("?" for _ in JUDGE_CALL_TYPES)
        rows: list[tuple[Any, ...]] = []
        unattributed: dict[str, list[tuple[int | None, int | None]]] = {}
        for r in conn.execute(
            "SELECT id, timestamp, document_id, provider, model, call_type, input_chars, duration_ms, status "
            f"FROM provider_telemetry WHERE call_type IN ({placeholders}) ORDER BY id",
            JUDGE_CALL_TYPES,
        ):
            if r["document_id"] in document_ids:
                rows.append((
                    r["id"], r["timestamp"], r["document_id"], r["provider"], r["model"],
                    r["call_type"], r["input_chars"], r["duration_ms"], r["status"],
                ))
            elif not r["document_id"] and r["status"] == "SUCCESS":
                unattributed.setdefault(r["call_type"], []).append((r["duration_ms"], r["input_chars"]))
    finally:
        conn.close()

    export_conn.executemany("INSERT INTO review_judge_calls VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)", rows)
    return {
        "status": "ok",
        "rows": len(rows),
        "by_call_type": dict(sorted(Counter(r[5] for r in rows).items())),
        "first_timestamp": min((r[1] for r in rows if r[1] is not None), default=None),
        "last_timestamp": max((r[1] for r in rows if r[1] is not None), default=None),
        # Calls without document_id cannot be attributed to this theme; only aggregates leave the server
        "unattributed_success": {
            call_type: _latency_aggregate(samples) for call_type, samples in sorted(unattributed.items())
        },
        "retention_note": "telemetry.db keeps roughly 30 days",
    }


def _percentile(values: list[Any], q: float) -> Any:
    if not values:
        return None
    ordered = sorted(values)
    return ordered[min(len(ordered) - 1, int(len(ordered) * q))]


def _latency_aggregate(samples: list[tuple[int | None, int | None]]) -> dict[str, Any]:
    durations = [d for d, _ in samples if d is not None]
    sizes = [s for _, s in samples if s is not None]
    return {
        "count": len(samples),
        "duration_ms_p50": _percentile(durations, 0.50),
        "duration_ms_p95": _percentile(durations, 0.95),
        "duration_ms_max": max(durations, default=None),
        "input_chars_p50": _percentile(sizes, 0.50),
        "input_chars_p95": _percentile(sizes, 0.95),
    }


def _guarded(section: str, fn: Any, *args: Any) -> Any:
    """Run one evidence section; a failure is recorded instead of aborting the graph bundle."""
    try:
        return fn(*args)
    except Exception as exc:  # noqa: BLE001
        logger.warning("Graph bundle review section %s failed: %s", section, exc)
        return {"status": "error", "error": type(exc).__name__}


def export_review_evidence(
    source_conn: sqlite3.Connection,
    export_conn: sqlite3.Connection,
    *,
    settings: Any,
    data_dir: Path | str | None,
    entity_ids: set[str],
    document_ids: set[str],
    now_epoch: float,
) -> dict[str, Any]:
    """Create review_* tables in the export database and return the manifest review summary."""
    export_conn.executescript(_REVIEW_SCHEMA)
    thresholds = review_thresholds(settings)

    decisions = _guarded("decisions", _export_decisions_and_heatmaps, source_conn, export_conn)
    pairs_result = _guarded("candidate_pairs", _export_candidate_pairs, source_conn, export_conn, entity_ids, thresholds)
    if isinstance(pairs_result, tuple):
        embeddings, pairs = pairs_result
    else:
        embeddings, pairs = pairs_result, pairs_result
    judge_calls = _guarded("judge_calls", _export_judge_calls, data_dir, export_conn, document_ids)

    shadow = decisions.pop("shadow", None) or {"status": decisions.get("status", "missing")}
    meta = {
        "review_schema_version": REVIEW_SCHEMA_VERSION,
        "generated_at": datetime.fromtimestamp(now_epoch, timezone.utc).isoformat(),
        "thresholds": thresholds,
        "pipeline": _pipeline_snapshot(settings),
        "coverage": {"documents": len(document_ids), **decisions},
        "embeddings": embeddings,
        "candidate_pairs": pairs,
        "judge_calls": judge_calls,
        "shadow": shadow,
    }
    export_conn.executemany(
        "INSERT INTO review_meta (key, value) VALUES (?, ?)",
        [(key, json.dumps(value, ensure_ascii=False)) for key, value in meta.items()],
    )

    return {
        "schema_version": REVIEW_SCHEMA_VERSION,
        "decisions": decisions.get("decisions", 0),
        "decisions_with_candidates": decisions.get("decisions_with_candidates", 0),
        "heatmaps": decisions.get("heatmaps", 0),
        "candidate_pairs": pairs.get("pairs", 0),
        "candidate_pairs_status": pairs.get("status"),
        "judge_calls": judge_calls.get("rows", 0),
        "judge_calls_status": judge_calls.get("status"),
        "shadow_decisions": shadow.get("decisions_with_shadow", 0),
        "shadow_pairs_scored": shadow.get("pairs_scored", 0),
    }


def count_review_tables(conn: sqlite3.Connection) -> dict[str, int]:
    """Row counts of review_* tables present in a bundle database (empty for format v1 bundles)."""
    present = {
        r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table' AND name LIKE 'review_%'")
    }
    return {
        name: conn.execute(f"SELECT count(*) FROM {name}").fetchone()[0]
        for name in REVIEW_TABLES
        if name in present
    }
