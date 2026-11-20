from __future__ import annotations

import json
import sqlite3
from pathlib import Path

import pytest

from claire.extract.jev import JevShadow
from claire.extract.resolver import resolve_or_create
from claire.ontology.base import Document, Entity
from claire.store import db as dbm
from claire.store import pair_review
from claire.store.vectors import VectorStore


def _db(tmp_path: Path) -> sqlite3.Connection:
    conn = dbm.connect(tmp_path / "claire.db")
    dbm.init_db(conn)
    return conn


def _log_item(entity: str, candidates: list[dict], *, decision: str = "CREATE_NEW", target: str | None = None, ts: float = 1000.0) -> dict:
    return {
        "entity": entity, "stage": "no_match" if decision == "CREATE_NEW" else "borderline_llm_judge", "decision": decision,
        "target_entity_id": target, "timestamp": ts,
        "input_snapshot": {"name": entity, "type": "Tool", "aliases": [], "observations": ["obs of " + entity]},
        "candidates": candidates,
        "shadow": {"engine": "jev", "role": "shadow", "status": "ok", "model": "jev-1.14.0", "latency_ms": 200.0},
    }


def _seed_document(conn: sqlite3.Connection, log: list[dict], *, kb_id: int = 0) -> str:
    doc = Document(title="Doc", raw_text="text", meta={"resolution_log": log, "has_decision_stream": True})
    dbm.insert_document(conn, doc, kb_id=kb_id)
    conn.commit()
    return doc.id


def test_queue_reasons() -> None:
    reasons = pair_review.queue_reasons
    assert reasons(outcome="judge_different", merged=False, jev_noul=0.8, new_name="A", cand_name="Zeta", key="k") == ["disagree"]
    assert reasons(outcome="judge_same", merged=True, jev_noul=0.1, new_name="A", cand_name="Zeta", key="k") == ["disagree", "merged"]
    assert reasons(outcome="invariant_reject", merged=False, jev_noul=0.97, new_name="Antrea CNI", cand_name="Antrea", key="k") == ["invariant", "near_variant"]
    assert reasons(outcome="judge_different", merged=False, jev_noul=0.02, new_name="Windows 11", cand_name="Windows", key="k") == ["near_variant"]
    assert pair_review.is_near_variant("PowerShell 7", "PowerShell")
    assert pair_review.is_near_variant("Gemini 3.5 Flash", "Gemini 3.6 Flash")
    assert not pair_review.is_near_variant("Docker", "Podman")
    picked = sum(bool(reasons(outcome="judge_different", merged=False, jev_noul=0.01, new_name="A", cand_name="Zeta", key=f"k{i}")) for i in range(4000))
    assert 120 <= picked <= 280
    assert reasons(outcome="judge_cap", merged=False, jev_noul=None, new_name="A", cand_name="Zeta", key="k") in ([], ["sample"]) and not any(
        reasons(outcome="judge_cap", merged=False, jev_noul=None, new_name="A", cand_name="Zeta", key=f"k{i}") for i in range(200)
    )


def test_a_v37_database_gets_the_tables_and_one_fill_from_old_records(tmp_path: Path) -> None:
    conn = _db(tmp_path)
    cand = Entity(type="Tool", name="Antrea", aliases=["Antrea CNI plugin"], observations=["a CNI plugin"], sources=["d0"])
    dbm.upsert_entity(conn, cand)
    log = [
        _log_item("Antrea CNI", [{"id": cand.id, "name": "Antrea", "type": "Framework", "outcome": "invariant_reject", "jev_noul": 0.97}]),
        _log_item("Docker", [{"id": "ent_gone", "name": "Podman", "type": "Tool", "outcome": "judge_different", "jev_noul": 0.01}]),
    ]
    doc_id = _seed_document(conn, log)
    conn.execute("DROP TABLE pair_review_verdicts")
    conn.execute("DROP TABLE pair_review_items")
    conn.execute("DELETE FROM meta WHERE key=?", (dbm.PAIR_REVIEW_BACKFILL_KEY,))
    conn.execute("UPDATE meta SET value='37' WHERE key='schema_version'")
    conn.commit()
    conn.close()

    conn = _db(tmp_path)
    assert conn.execute("SELECT value FROM meta WHERE key='schema_version'").fetchone()[0] == str(dbm.SCHEMA_VERSION) == "38"
    rows = conn.execute("SELECT document_id, seq, candidate_id, reasons, candidate, snapshot_source, jev_model FROM pair_review_items").fetchall()
    assert len(rows) == 1
    row = rows[0]
    assert row[0] == doc_id and row[1] == 0 and row[2] == cand.id and json.loads(row[3]) == ["invariant", "near_variant"]
    assert json.loads(row[4])["aliases"] == ["Antrea CNI plugin"] and row[5] == "current" and row[6] == "jev-1.14.0"
    conn.execute("DELETE FROM pair_review_items")
    conn.commit()
    conn.close()
    conn = _db(tmp_path)
    assert conn.execute("SELECT COUNT(*) FROM pair_review_items").fetchone()[0] == 0
    conn.close()


def test_resolver_snapshots_go_to_the_queue_with_the_pre_merge_state(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    import claire.extract.resolver as resolver_mod

    monkeypatch.setattr(resolver_mod, "_get_tier_thresholds", lambda: (0.93, 0.72, 0.70, 0.55, False))
    conn = _db(tmp_path)
    vstore = VectorStore(conn, "brute")
    target = Entity(type="Model", name="Gemini 3.8 Flash", aliases=["Gemini 3.8"], observations=["a model"], sources=["d0"])
    dbm.upsert_entity(conn, target)
    vstore.put(target.id, [0.82, 0.57, 0.0], "claire")

    class _Client:
        model, timeout = "jev-1.14.0", 1.0

        def systemone(self, state, questions):
            return {"model": "jev-1.14.0", "answers": {k: {"type": "noul", "noul": 0.79} for k in questions}, "usage": {"input_tokens": 50}}, 20.0

    class _Inline:
        def submit(self, fn, *args):
            import concurrent.futures

            f = concurrent.futures.Future()
            f.set_result(fn(*args))
            return f

    res = resolve_or_create(
        conn, vstore, name="3.8 Flash", etype="Model", aliases=[], observations=["18.2% on a benchmark"], document_id="d1",
        embed_fn=lambda: [1.0, 0.0, 0.0], judge_fn=lambda nm, et, obs, cand, aliases=None: True,
        shadow=JevShadow(_Client(), executor=_Inline()),
    )
    assert res.decision.decision == "MERGE"
    assert "candidate_snapshots" not in res.decision.to_dict() and res.candidate_snapshots
    doc = Document(title="Doc", raw_text="text")
    dbm.insert_document(conn, doc, kb_id=0)
    assert pair_review.enqueue_results(conn, kb_id=0, document_id=doc.id, results=[res]) == 1
    row = conn.execute("SELECT reasons, candidate, snapshot_source, merged, outcome FROM pair_review_items").fetchone()
    assert json.loads(row[0]) == ["merged", "near_variant"]
    assert json.loads(row[1])["observations"] == ["a model"] and row[2] == "decision" and row[3] == 1 and row[4] == "judge_same"


def test_verdicts_consensus_report_and_account_deletion(tmp_path: Path) -> None:
    conn = _db(tmp_path)
    log = [
        _log_item("Antrea CNI", [{"id": "e1", "name": "Antrea", "type": "Framework", "outcome": "invariant_reject", "jev_noul": 0.97}], ts=1.0),
        _log_item("Data Center Investment", [{"id": "e2", "name": "Data Center", "type": "Concept", "outcome": "judge_different", "jev_noul": 0.65}], ts=2.0),
        _log_item("3.8 Flash", [{"id": "e3", "name": "Gemini 3.8 Flash", "type": "Model", "outcome": "judge_same", "jev_noul": 0.79}], decision="MERGE", target="e3", ts=3.0),
    ]
    _seed_document(conn, log)
    conn.execute("DELETE FROM pair_review_items")
    pair_review.backfill_from_records(conn)
    ids = {json.loads(r[1])["name"]: int(r[0]) for r in conn.execute("SELECT id, candidate FROM pair_review_items")}
    assert set(ids) == {"Antrea", "Data Center", "Gemini 3.8 Flash"}

    with pytest.raises(ValueError):
        pair_review.set_verdict(conn, item_id=ids["Antrea"], account_id=5, verdict="maybe")
    with pytest.raises(ValueError):
        pair_review.set_verdict(conn, item_id=ids["Antrea"], account_id=5, verdict="same", note="x" * 501)
    for account in (5, 6):
        pair_review.set_verdict(conn, item_id=ids["Antrea"], account_id=account, verdict="same")
        pair_review.set_verdict(conn, item_id=ids["Data Center"], account_id=account, verdict="different")
        pair_review.set_verdict(conn, item_id=ids["Gemini 3.8 Flash"], account_id=account, verdict="same")
    pair_review.set_verdict(conn, item_id=ids["Gemini 3.8 Flash"], account_id=7, verdict="unsure")
    pair_review.set_verdict(conn, item_id=ids["Data Center"], account_id=6, verdict="same")
    pair_review.set_verdict(conn, item_id=ids["Data Center"], account_id=6, verdict="different", note="a broad and a narrow concept")
    assert pair_review.item_counts(conn, ids["Data Center"]) == {"different": 2}
    assert pair_review.consensus({"same": 1}) is None and pair_review.consensus({"same": 2, "different": 1}) == "same"
    assert pair_review.consensus({"same": 1, "different": 1}) is None and pair_review.consensus({"unsure": 5}) is None
    conn.commit()

    rep = pair_review.report(conn)
    assert rep["items"]["with_consensus"] == 3 and rep["items"]["positives"] == 2
    assert rep["verdicts"]["accounts"] == 3 and rep["verdicts"]["by_verdict"]["unsure"] == 1
    jev = rep["jev_by_model"]["jev-1.14.0"]
    assert jev["thresholds"]["0.5"]["tp"] == 2 and jev["thresholds"]["0.5"]["fp"] == 1 and jev["thresholds"]["0.9"]["fn"] == 1
    assert jev["thresholds"]["0.7"]["precision"] == 1.0 and jev["thresholds"]["0.7"]["precision_low95"] == pytest.approx(0.3424, abs=1e-3)
    assert rep["judge"] == {**rep["judge"], "tp": 1, "fp": 0, "tn": 1}
    assert rep["pipeline_merges"] == {"labeled": 1, "false_merges": 0}
    assert pair_review.report(conn, kb_ids=[99])["items"]["total"] == 0

    assert pair_review.pending_items_for(conn, account_id=8, readable_kb_ids=[0], limit=10) == []
    pair_review.delete_verdict(conn, item_id=ids["Antrea"], account_id=6)
    waiting = pair_review.pending_items_for(conn, account_id=8, readable_kb_ids=[0], limit=10)
    assert [w["id"] for w in waiting] == [ids["Antrea"]]
    assert pair_review.pending_items_for(conn, account_id=8, readable_kb_ids=[], limit=10) == []

    stats = pair_review.account_stats(conn, account_id=5, readable_kb_ids=[0])
    assert stats["verdicts"] == 3 and stats["by_verdict"] == {"same": 2, "different": 1}

    from claire.store import accounts as accounts_store

    accounts_store._gc_account_disk_artifacts(conn, 5, data_dir=tmp_path)
    conn.commit()
    assert conn.execute("SELECT COUNT(*) FROM pair_review_verdicts WHERE account_id=5").fetchone()[0] == 0
    assert conn.execute("SELECT COUNT(*) FROM pair_review_verdicts WHERE account_id=7").fetchone()[0] == 1
    conn.close()


def test_blind_share_is_three_in_ten() -> None:
    blind = sum(pair_review.is_blind(item, account) for item in range(1, 101) for account in range(1, 51))
    assert 0.27 <= blind / 5000 <= 0.33
    assert pair_review.is_blind(12, 34) == pair_review.is_blind(12, 34)


def test_item_view_hides_the_pre_label_of_a_blind_item_until_the_verdict(tmp_path: Path) -> None:
    conn = _db(tmp_path)
    _seed_document(conn, [_log_item("Antrea CNI", [{"id": "e1", "name": "Antrea", "type": "Framework", "outcome": "invariant_reject", "jev_noul": 0.97}])])
    item = conn.execute("SELECT id FROM pair_review_items").fetchone()
    if item is None:
        pair_review.backfill_from_records(conn)
        item = conn.execute("SELECT id FROM pair_review_items").fetchone()
    item_id = int(item[0])
    pair_review.store_prelabels(conn, {item_id: {"engine": "gemini-decisions", "model": "gemini-decision-1", "status": "ok", "noul": 0.93, "confidence": 0.93}})
    row = pair_review.get_item(conn, item_id)
    blind_account = next(a for a in range(1, 100) if pair_review.is_blind(item_id, a))
    shown_account = next(a for a in range(1, 100) if not pair_review.is_blind(item_id, a))
    hidden = pair_review.item_view(row, account_id=blind_account)
    assert hidden["prelabel_hidden"] is True and hidden["prelabel"] is None
    shown = pair_review.item_view(row, account_id=shown_account)
    assert shown["prelabel"]["noul"] == 0.93
    pair_review.set_verdict(conn, item_id=item_id, account_id=blind_account, verdict="same")
    after = pair_review.item_view(row, account_id=blind_account, own=pair_review.own_verdict(conn, item_id, blind_account))
    assert after["prelabel"]["noul"] == 0.93 and after["my_verdict"]["blind"] is True
    rep = pair_review.report(conn)
    assert rep["prelabel"]["anchoring"]["blind"] == {"verdicts": 1, "agree_with_prelabel": 1, "rate": 1.0}
    conn.close()
