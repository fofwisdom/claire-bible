from __future__ import annotations

import hashlib
import json
import math
import re
import sqlite3
import time
from collections import Counter
from typing import TYPE_CHECKING, Any, Iterable, NamedTuple

from ..extract.same_entity_rule import SAME_ENTITY_RULE_VERSION

if TYPE_CHECKING:
    from ..extract.decisions_prelabel import Prelabel
    from ..extract.resolver import ResolutionResult

REASONS = ("disagree", "invariant", "merged", "near_variant", "sample")
VERDICTS = ("same", "different", "unsure")
JUDGE_OUTCOMES = {"judge_same": True, "judge_different": False}
JEV_SAME_AT = 0.5
SAMPLE_PER_MILLE = 50
BLIND_PER_TEN = 3
CONSENSUS_MIN_DECISIVE_VERDICTS = 2
CONSENSUS_SHARE = 2 / 3
REPORT_THRESHOLDS = (0.5, 0.7, 0.9)
GATE_PRECISION_LOW = 0.98
NOTE_MAX = 500

_VERSION_MARK = re.compile(r"\b(v?\d+(?:\.\d+)*[a-z]?|x\d+|pro|max|mini|lite|flash-lite|preview|beta|alpha)\b")
_NON_WORD = re.compile(r"[^0-9a-z가-힣]+")


def _norm(name: str) -> str:
    return _NON_WORD.sub(" ", (name or "").lower()).strip()


def is_near_variant(a: str, b: str) -> bool:
    na, nb = _norm(a), _norm(b)
    if not na or not nb or na == nb:
        return bool(na and na == nb)
    ta, tb = set(na.split()), set(nb.split())
    if ta <= tb or tb <= ta or len(ta & tb) / len(ta | tb) >= 0.5:
        return True
    base_a, base_b = _VERSION_MARK.sub(" ", na).split(), _VERSION_MARK.sub(" ", nb).split()
    return bool(base_a) and base_a == base_b


def _in_sample(key: str) -> bool:
    return int(hashlib.sha256(key.encode("utf-8")).hexdigest()[:8], 16) % 1000 < SAMPLE_PER_MILLE


def queue_reasons(*, outcome: str | None, merged: bool, jev_noul: float | None, new_name: str, cand_name: str, key: str) -> list[str]:
    reasons: list[str] = []
    if jev_noul is not None and outcome in JUDGE_OUTCOMES and (jev_noul >= JEV_SAME_AT) != JUDGE_OUTCOMES[outcome]:
        reasons.append("disagree")
    if jev_noul is not None and outcome == "invariant_reject" and jev_noul >= JEV_SAME_AT:
        reasons.append("invariant")
    if merged:
        reasons.append("merged")
    if is_near_variant(new_name, cand_name):
        reasons.append("near_variant")
    if not reasons and jev_noul is not None and _in_sample(key):
        reasons.append("sample")
    return reasons


def _snapshot(name: Any, etype: Any, aliases: Any = None, observations: Any = None) -> dict[str, Any]:
    return {
        "name": str(name or ""),
        "type": str(etype or ""),
        "aliases": [str(a) for a in (aliases or []) if a][:10],
        "observations": [str(o)[:280] for o in (observations or []) if o][:6],
    }


def _items_of_decision(
    conn: sqlite3.Connection,
    *,
    kb_id: int,
    document_id: str,
    seq: int,
    item: dict[str, Any],
    candidate_snapshots: dict[str, dict[str, Any]] | None,
) -> list[tuple[Any, ...]]:
    shadow = item.get("shadow")
    candidates = item.get("candidates")
    if not isinstance(shadow, dict) or not isinstance(candidates, list):
        return []
    snap = item.get("input_snapshot") if isinstance(item.get("input_snapshot"), dict) else {}
    new_entity = _snapshot(snap.get("name") or item.get("entity"), snap.get("type"), snap.get("aliases"), snap.get("observations"))
    decided_at = float(item.get("timestamp") or 0.0)
    merged_id = item.get("target_entity_id") if item.get("decision") == "MERGE" else None
    now = time.time()
    rows: list[tuple[Any, ...]] = []
    for cand in candidates:
        if not isinstance(cand, dict) or not cand.get("id"):
            continue
        cand_id = str(cand["id"])
        noul = cand.get("jev_noul")
        noul = float(noul) if isinstance(noul, (int, float)) and math.isfinite(noul) else None
        merged = merged_id is not None and merged_id == cand_id
        source = "decision"
        cand_snap = (candidate_snapshots or {}).get(cand_id)
        if cand_snap is None:
            source = "current"
            row = conn.execute("SELECT name, type, aliases, observations FROM entities WHERE id=?", (cand_id,)).fetchone()
            if row is not None:
                cand_snap = _snapshot(row[0], row[1], json.loads(row[2] or "[]"), json.loads(row[3] or "[]"))
            elif cand.get("name"):
                cand_snap = _snapshot(cand.get("name"), cand.get("type"))
            else:
                continue
        reasons = queue_reasons(
            outcome=cand.get("outcome"), merged=merged, jev_noul=noul,
            new_name=new_entity["name"], cand_name=cand_snap["name"],
            key=f"{document_id}:{seq}:{decided_at}:{cand_id}",
        )
        if not reasons:
            continue
        rows.append((
            kb_id, document_id, seq, decided_at, cand_id, json.dumps(reasons),
            json.dumps(new_entity, ensure_ascii=False), json.dumps(cand_snap, ensure_ascii=False), source,
            cand.get("outcome"), int(merged), noul, shadow.get("model"),
            float(shadow["latency_ms"]) if isinstance(shadow.get("latency_ms"), (int, float)) else None,
            SAME_ENTITY_RULE_VERSION, now,
        ))
    return rows


_INSERT_ITEM = (
    "INSERT OR IGNORE INTO pair_review_items (kb_id, document_id, seq, decided_at, candidate_id, reasons, new_entity, candidate, "
    "snapshot_source, outcome, merged, jev_noul, jev_model, jev_latency_ms, rule_version, created_at) "
    "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)"
)


def enqueue_results(conn: sqlite3.Connection, *, kb_id: int, document_id: str, results: Iterable[ResolutionResult]) -> int:
    rows: list[tuple[Any, ...]] = []
    for seq, res in enumerate(r for r in results if r.decision is not None):
        rows.extend(_items_of_decision(
            conn, kb_id=kb_id, document_id=document_id, seq=seq, item=res.decision.to_dict(),
            candidate_snapshots=res.candidate_snapshots,
        ))
    before = conn.total_changes
    conn.executemany(_INSERT_ITEM, rows)
    return conn.total_changes - before


def backfill_from_records(conn: sqlite3.Connection) -> int:
    rows: list[tuple[Any, ...]] = []
    for doc in conn.execute("SELECT id, kb_id, meta FROM documents WHERE meta LIKE '%\"shadow\"%'").fetchall():
        try:
            meta = json.loads(doc[2]) if isinstance(doc[2], str) else doc[2]
        except (TypeError, ValueError):
            continue
        log = meta.get("resolution_log") if isinstance(meta, dict) else None
        if not isinstance(log, list):
            continue
        for seq, item in enumerate(log):
            if isinstance(item, dict):
                rows.extend(_items_of_decision(conn, kb_id=int(doc[1] or 0), document_id=doc[0], seq=seq, item=item, candidate_snapshots=None))
    before = conn.total_changes
    conn.executemany(_INSERT_ITEM, rows)
    return conn.total_changes - before


def consensus(counts: dict[str, int]) -> str | None:
    same, different = int(counts.get("same", 0)), int(counts.get("different", 0))
    total = same + different
    if total < CONSENSUS_MIN_DECISIVE_VERDICTS:
        return None
    if same / total >= CONSENSUS_SHARE:
        return "same"
    if different / total >= CONSENSUS_SHARE:
        return "different"
    return None


def _verdict_counts(conn: sqlite3.Connection, item_ids: list[int] | None = None) -> dict[int, dict[str, int]]:
    sql = "SELECT item_id, verdict, COUNT(*) FROM pair_review_verdicts"
    params: list[Any] = []
    if item_ids is not None:
        if not item_ids:
            return {}
        sql += f" WHERE item_id IN ({','.join('?' for _ in item_ids)})"
        params = list(item_ids)
    out: dict[int, dict[str, int]] = {}
    for item_id, verdict, n in conn.execute(sql + " GROUP BY item_id, verdict", params):
        out.setdefault(int(item_id), {})[verdict] = int(n)
    return out


def is_blind(item_id: int, account_id: int) -> bool:
    digest = hashlib.sha256(f"pair-review:{item_id}:{account_id}".encode("utf-8")).hexdigest()
    return int(digest[:8], 16) % 10 < BLIND_PER_TEN


def _row_dict(row: sqlite3.Row) -> dict[str, Any]:
    return {k: row[k] for k in row.keys()}


def item_view(row: dict[str, Any], *, account_id: int, counts: dict[str, int] | None = None, own: dict[str, Any] | None = None) -> dict[str, Any]:
    blind = is_blind(int(row["id"]), account_id)
    show_prelabel = own is not None or not blind
    prelabel: dict[str, Any] | None = None
    if row.get("prelabel_status"):
        prelabel = {"status": row["prelabel_status"], "engine": row.get("prelabel_engine"), "model": row.get("prelabel_model")}
        if row["prelabel_status"] == "ok":
            prelabel.update(noul=row.get("prelabel_noul"), confidence=row.get("prelabel_confidence"))
    view = {
        "id": int(row["id"]),
        "kb_id": int(row["kb_id"]),
        "document_id": row["document_id"],
        "decided_at": row["decided_at"],
        "reasons": json.loads(row["reasons"]),
        "new_entity": json.loads(row["new_entity"]),
        "candidate": json.loads(row["candidate"]),
        "snapshot_source": row["snapshot_source"],
        "rule_version": row["rule_version"],
        "prelabel_hidden": not show_prelabel,
        "prelabel": prelabel if show_prelabel else None,
        "verdict_counts": counts or {},
        "consensus": consensus(counts or {}),
    }
    if own is not None:
        view["my_verdict"] = own
    return view


def pending_items_for(conn: sqlite3.Connection, *, account_id: int, readable_kb_ids: Iterable[int], limit: int) -> list[dict[str, Any]]:
    kb_ids = sorted({int(k) for k in readable_kb_ids})
    if not kb_ids or limit <= 0:
        return []
    marks = ",".join("?" for _ in kb_ids)
    rows = [_row_dict(r) for r in conn.execute(
        f"SELECT i.* FROM pair_review_items i WHERE i.kb_id IN ({marks}) "
        "AND NOT EXISTS (SELECT 1 FROM pair_review_verdicts v WHERE v.item_id = i.id AND v.account_id = ?) "
        "ORDER BY i.created_at, i.id",
        (*kb_ids, int(account_id)),
    )]
    counts = _verdict_counts(conn, [int(r["id"]) for r in rows])
    open_rows = [r for r in rows if consensus(counts.get(int(r["id"]), {})) is None]

    def priority(r: dict[str, Any]) -> tuple[int, int, float, int]:
        c = counts.get(int(r["id"]), {})
        decided = c.get("same", 0) + c.get("different", 0)
        reasons = json.loads(r["reasons"])
        rank = min((REASONS.index(x) for x in reasons if x in REASONS), default=len(REASONS))
        return (0 if decided else 1, rank, float(r["created_at"]), int(r["id"]))

    open_rows.sort(key=priority)
    return [dict(r, _counts=counts.get(int(r["id"]), {})) for r in open_rows[:limit]]


def get_item(conn: sqlite3.Connection, item_id: int) -> dict[str, Any] | None:
    row = conn.execute("SELECT * FROM pair_review_items WHERE id=?", (int(item_id),)).fetchone()
    return _row_dict(row) if row is not None else None


def item_counts(conn: sqlite3.Connection, item_id: int) -> dict[str, int]:
    return _verdict_counts(conn, [int(item_id)]).get(int(item_id), {})


def own_verdict(conn: sqlite3.Connection, item_id: int, account_id: int) -> dict[str, Any] | None:
    row = conn.execute(
        "SELECT verdict, note, blind, rule_version, created_at, updated_at FROM pair_review_verdicts WHERE item_id=? AND account_id=?",
        (int(item_id), int(account_id)),
    ).fetchone()
    if row is None:
        return None
    return {"verdict": row[0], "note": row[1] or "", "blind": bool(row[2]), "rule_version": row[3], "created_at": row[4], "updated_at": row[5]}


def set_verdict(conn: sqlite3.Connection, *, item_id: int, account_id: int, verdict: str, note: str = "") -> None:
    if verdict not in VERDICTS:
        raise ValueError(f"verdict must be one of {', '.join(VERDICTS)}")
    note = str(note or "")
    if len(note) > NOTE_MAX:
        raise ValueError(f"note is longer than {NOTE_MAX} characters")
    now = time.time()
    conn.execute(
        "INSERT INTO pair_review_verdicts (item_id, account_id, verdict, note, blind, rule_version, created_at, updated_at) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?) ON CONFLICT(item_id, account_id) DO UPDATE SET "
        "verdict=excluded.verdict, note=excluded.note, rule_version=excluded.rule_version, updated_at=excluded.updated_at",
        (int(item_id), int(account_id), verdict, note, int(is_blind(item_id, account_id)), SAME_ENTITY_RULE_VERSION, now, now),
    )


def delete_verdict(conn: sqlite3.Connection, *, item_id: int, account_id: int) -> bool:
    cur = conn.execute("DELETE FROM pair_review_verdicts WHERE item_id=? AND account_id=?", (int(item_id), int(account_id)))
    return cur.rowcount == 1


def account_stats(conn: sqlite3.Connection, *, account_id: int, readable_kb_ids: Iterable[int]) -> dict[str, Any]:
    by_verdict = dict(conn.execute(
        "SELECT verdict, COUNT(*) FROM pair_review_verdicts WHERE account_id=? GROUP BY verdict", (int(account_id),)
    ).fetchall())
    waiting = len(pending_items_for(conn, account_id=account_id, readable_kb_ids=readable_kb_ids, limit=10**9))
    return {"verdicts": sum(by_verdict.values()), "by_verdict": by_verdict, "waiting": waiting}


def items_without_prelabel(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [it for it in items if not it.get("prelabel_status")]


def store_prelabels(conn: sqlite3.Connection, results: dict[int, Prelabel]) -> None:
    now = time.time()
    conn.executemany(
        "UPDATE pair_review_items SET prelabel_engine=?, prelabel_model=?, prelabel_status=?, prelabel_noul=?, prelabel_confidence=?, prelabel_at=? WHERE id=?",
        [(r["engine"], r["model"], r["status"], r.get("noul"), r.get("confidence"), now, int(item_id)) for item_id, r in results.items()],
    )


def wilson_low(k: int, n: int, z: float = 1.96) -> float | None:
    if n <= 0:
        return None
    p = k / n
    d = 1 + z * z / n
    return round((p + z * z / (2 * n) - z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n))) / d, 4)


class Outcome(NamedTuple):
    predicted_same: bool
    labeled_same: bool


def _confusion(pairs: list[Outcome]) -> dict[str, Any]:
    tp = sum(1 for p, y in pairs if p and y)
    fp = sum(1 for p, y in pairs if p and not y)
    fn = sum(1 for p, y in pairs if not p and y)
    tn = sum(1 for p, y in pairs if not p and not y)
    return {
        "tp": tp, "fp": fp, "fn": fn, "tn": tn,
        "precision": round(tp / (tp + fp), 4) if tp + fp else None,
        "precision_low95": wilson_low(tp, tp + fp),
        "recall": round(tp / (tp + fn), 4) if tp + fn else None,
        "false_positive_rate": round(fp / (fp + tn), 6) if fp + tn else None,
    }


def _percentile(values: list[float], q: float) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    return ordered[min(len(ordered) - 1, int(round(q * (len(ordered) - 1))))]


def report(conn: sqlite3.Connection, *, kb_ids: Iterable[int] | None = None) -> dict[str, Any]:
    where, params = "", []
    if kb_ids is not None:
        ids = sorted({int(k) for k in kb_ids})
        if not ids:
            ids = [-1]
        where = f" WHERE kb_id IN ({','.join('?' for _ in ids)})"
        params = ids
    items = [_row_dict(r) for r in conn.execute("SELECT * FROM pair_review_items" + where, params)]
    counts = _verdict_counts(conn, [int(i["id"]) for i in items])
    labels = {int(i["id"]): consensus(counts.get(int(i["id"]), {})) for i in items}
    labeled = [i for i in items if labels[int(i["id"])] is not None]

    by_reason: Counter[str] = Counter()
    for it in items:
        by_reason.update(json.loads(it["reasons"]))

    item_ids = [int(i["id"]) for i in items]
    verdict_rows = []
    if item_ids:
        verdict_rows = conn.execute(
            f"SELECT item_id, account_id, verdict, blind FROM pair_review_verdicts WHERE item_id IN ({','.join('?' for _ in item_ids)})",
            item_ids,
        ).fetchall()

    jev_by_model: dict[str, Any] = {}
    for model in sorted({str(i.get("jev_model")) for i in labeled if i.get("jev_noul") is not None}):
        group = [i for i in labeled if i.get("jev_noul") is not None and str(i.get("jev_model")) == model]
        latencies = sorted({(i["document_id"], i["seq"], i["decided_at"]): i["jev_latency_ms"] for i in group if i.get("jev_latency_ms") is not None}.values())
        jev_by_model[model] = {
            "labeled_pairs": len(group),
            "positives": sum(1 for i in group if labels[int(i["id"])] == "same"),
            "latency_ms_p95": _percentile(latencies, 0.95),
            "thresholds": {
                f"{t:g}": _confusion([Outcome(float(i["jev_noul"]) >= t, labels[int(i["id"])] == "same") for i in group])
                for t in REPORT_THRESHOLDS
            },
        }

    judged = [i for i in labeled if i.get("outcome") in JUDGE_OUTCOMES]
    merges = [i for i in labeled if i.get("merged")]
    prelabeled = [i for i in labeled if i.get("prelabel_status") == "ok" and i.get("prelabel_noul") is not None]
    pre_vs_judge = [i for i in items if i.get("prelabel_status") == "ok" and i.get("outcome") in JUDGE_OUTCOMES]

    pre_by_item = {int(i["id"]): (float(i["prelabel_noul"]) >= 0.5) for i in items if i.get("prelabel_status") == "ok" and i.get("prelabel_noul") is not None}
    anchoring: dict[str, dict[str, Any]] = {}
    for blind_flag, name in ((1, "blind"), (0, "shown")):
        rows = [r for r in verdict_rows if int(r[3]) == blind_flag and r[2] in ("same", "different") and int(r[0]) in pre_by_item]
        agree = sum(1 for r in rows if (r[2] == "same") == pre_by_item[int(r[0])])
        anchoring[name] = {"verdicts": len(rows), "agree_with_prelabel": agree, "rate": round(agree / len(rows), 4) if rows else None}

    positives = sum(1 for i in labeled if labels[int(i["id"])] == "same")
    return {
        "rule_version": SAME_ENTITY_RULE_VERSION,
        "consensus_rule": {"min_verdicts": CONSENSUS_MIN_DECISIVE_VERDICTS, "share": round(CONSENSUS_SHARE, 4), "unsure_counts": False},
        "items": {
            "total": len(items),
            "by_reason": dict(sorted(by_reason.items())),
            "with_consensus": len(labeled),
            "positives": positives,
            "without_consensus": len(items) - len(labeled),
            "with_prelabel": sum(1 for i in items if i.get("prelabel_status") == "ok"),
        },
        "verdicts": {
            "total": len(verdict_rows),
            "accounts": len({int(r[1]) for r in verdict_rows}),
            "by_verdict": dict(sorted(Counter(r[2] for r in verdict_rows).items())),
            "blind": sum(1 for r in verdict_rows if int(r[3])),
        },
        "jev_by_model": jev_by_model,
        "judge": _confusion([Outcome(i["outcome"] == "judge_same", labels[int(i["id"])] == "same") for i in judged]),
        "pipeline_merges": {"labeled": len(merges), "false_merges": sum(1 for i in merges if labels[int(i["id"])] == "different")},
        "prelabel": {
            "vs_consensus": _confusion([Outcome(float(i["prelabel_noul"]) >= 0.5, labels[int(i["id"])] == "same") for i in prelabeled]),
            "agreement_with_judge": (
                round(sum(1 for i in pre_vs_judge if (float(i["prelabel_noul"]) >= 0.5) == (i["outcome"] == "judge_same")) / len(pre_vs_judge), 4)
                if pre_vs_judge else None
            ),
            "anchoring": anchoring,
        },
        "gate": {
            "precision_low95_needed": GATE_PRECISION_LOW,
            "positives_needed_with_zero_errors": 150,
            "positives_now": positives,
        },
    }
