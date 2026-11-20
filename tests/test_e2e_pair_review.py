from __future__ import annotations

import json
import sqlite3
import time
from pathlib import Path
from typing import Any

import pytest
from google.genai.decisions import CreateDecisionResponse, NoulAnswer
from starlette.testclient import TestClient

from claire import cli
from claire.api import server
from claire.config import Settings, get_settings
from claire.extract import decisions_prelabel
from claire.ontology.base import Document
from claire.store import accounts as accounts_store
from claire.store import db as dbm
from claire.store.knowledge_base import KnowledgeBaseManager

PUBLIC_URL = "http://127.0.0.1:8765"
SERVER_KEY = "AIzaServerKeyForPairReview0123456789"


def _log_item(entity: str, cand_id: str, cand: str, outcome: str, noul: float, *, ts: float, merged: bool = False) -> dict[str, Any]:
    return {
        "entity": entity, "stage": "borderline_llm_judge" if merged else "no_match", "decision": "MERGE" if merged else "CREATE_NEW",
        "target_entity_id": cand_id if merged else None, "timestamp": ts,
        "input_snapshot": {"name": entity, "type": "Tool", "aliases": [], "observations": [f"about {entity}"]},
        "candidates": [{"id": cand_id, "name": cand, "type": "Tool", "outcome": outcome, "jev_noul": noul}],
        "shadow": {"engine": "jev", "role": "shadow", "status": "ok", "model": "jev-1.14.0", "latency_ms": 210.0},
    }


@pytest.fixture()
def prelabel_calls(monkeypatch: pytest.MonkeyPatch) -> list[dict[str, Any]]:
    calls: list[dict[str, Any]] = []

    def factory(api_key: str):
        def create(**kwargs: Any) -> Any:
            calls.append({"api_key": api_key, **kwargs})
            return CreateDecisionResponse(answers={k: NoulAnswer(type="noul", noul=0.91, confidence=0.91) for k in kwargs["questions"]})

        return create

    monkeypatch.setattr(decisions_prelabel, "_client_create", factory)
    return calls


def _setup(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> tuple[Path, Settings, dict[str, int]]:
    data_dir = tmp_path / "data"
    data_dir.mkdir()
    db_file = data_dir / "claire.db"
    monkeypatch.setenv("CLAIRE_DB_PATH", str(db_file))
    monkeypatch.setenv("CB_DATA_DIR", str(data_dir))
    get_settings.cache_clear()
    settings = Settings(
        _env_file=None, CLAIRE_DB_PATH=str(db_file), CLAIRE_DATA_DIR=str(data_dir), CLAIRE_PROVIDER="mock",
        CLAIRE_ENVIRONMENT="development", CLAIRE_PUBLIC_URL=PUBLIC_URL, CLAIRE_MULTI_THEME=True, GEMINI_API_KEY=SERVER_KEY,
    )
    conn = dbm.connect(db_file)
    try:
        dbm.init_db(conn)
        store = accounts_store.AccountStore(conn)
        store.complete_setup(1, username="admin_pr", display_name="Admin")
        members = {name: int(store.create_member(username=name, display_name=name).id) for name in ("member_a", "member_b", "member_c")}
        conn.execute("UPDATE knowledge_bases SET is_public=1 WHERE id=0")
        conn.execute(
            "INSERT INTO knowledge_bases (id, seq, label, account_id, is_personal, is_public, created_at, updated_at) VALUES (77, 77, 'private of c', ?, 1, 0, 1, 1)",
            (members["member_c"],),
        )
        public_log = [
            _log_item("Antrea CNI", "ent_antrea", "Antrea", "invariant_reject", 0.97, ts=1.0),
            _log_item("Data Center Investment", "ent_dc", "Data Center", "judge_different", 0.65, ts=2.0),
            _log_item("3.8 Flash", "ent_flash", "Gemini 3.8 Flash", "judge_same", 0.79, ts=3.0, merged=True),
        ]
        dbm.insert_document(conn, Document(title="Public doc", raw_text="x", meta={"resolution_log": public_log}), kb_id=0)
        private_log = [_log_item("Secret Tool", "ent_secret", "Secret Tool 2", "judge_different", 0.80, ts=4.0)]
        dbm.insert_document(conn, Document(title="Private doc", raw_text="y", meta={"resolution_log": private_log}), kb_id=77)
        conn.execute("DELETE FROM pair_review_items")
        conn.execute("DELETE FROM meta WHERE key=?", (dbm.PAIR_REVIEW_BACKFILL_KEY,))
        conn.commit()
        dbm.init_db(conn)
        conn.commit()
    finally:
        conn.close()
    KnowledgeBaseManager(settings)
    return db_file, settings, members


def _headers(db_file: Path, account_id: int) -> dict[str, str]:
    conn = dbm.connect_existing(db_file)
    try:
        session = accounts_store.create_account_session(conn, account_id=account_id, scope="owner", now=time.time())
    finally:
        conn.close()
    return {"Cookie": f"claire_session={session}", "Origin": PUBLIC_URL}


def test_e2e_members_give_verdicts_and_the_admin_reads_the_report(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, prelabel_calls, capsys) -> None:
    db_file, settings, members = _setup(tmp_path, monkeypatch)
    app = server.create_app(settings=settings)
    with TestClient(app, base_url=PUBLIC_URL) as client:
        assert client.get("/api/me/pair-review/items").status_code == 404

        a = _headers(db_file, members["member_a"])
        got = client.get("/api/me/pair-review/items?limit=20", headers=a)
        assert got.status_code == 200, got.text
        body = got.json()
        assert body["rule"]["version"] == "2026-11-20" and body["verdicts"] == ["same", "different", "unsure"]
        names = {it["candidate"]["name"]: it for it in body["items"]}
        assert set(names) == {"Antrea", "Data Center", "Gemini 3.8 Flash"}
        assert names["Antrea"]["reasons"] == ["invariant", "near_variant"]
        assert len(prelabel_calls) == 3 and {c["api_key"] for c in prelabel_calls} == {SERVER_KEY}
        for it in body["items"]:
            assert (it["prelabel"] is None) == it["prelabel_hidden"]
            if not it["prelabel_hidden"]:
                assert it["prelabel"] == {"status": "ok", "engine": "gemini-decisions", "model": "gemini-decision-1", "noul": 0.91, "confidence": 0.91}
        assert client.get("/api/me/pair-review/items?limit=21", headers=a).status_code == 400

        client.get("/api/me/pair-review/items", headers=a)
        assert len(prelabel_calls) == 3

        antrea = names["Antrea"]["id"]
        bad = client.put(f"/api/me/pair-review/items/{antrea}/verdict", headers=a, json={"verdict": "yes"})
        assert bad.status_code == 400 and "verdict_invalid" in bad.text
        put = client.put(f"/api/me/pair-review/items/{antrea}/verdict", headers=a, json={"verdict": "same", "note": "Antrea is a CNI plugin"})
        assert put.status_code == 200, put.text
        item = put.json()["item"]
        assert item["prelabel"]["noul"] == 0.91 and item["my_verdict"]["verdict"] == "same" and item["verdict_counts"] == {"same": 1}
        assert item["consensus"] is None

        private_id = _private_item_id(db_file)
        assert client.put(f"/api/me/pair-review/items/{private_id}/verdict", headers=a, json={"verdict": "same"}).status_code == 404

        b = _headers(db_file, members["member_b"])
        waiting_b = [it["id"] for it in client.get("/api/me/pair-review/items", headers=b).json()["items"]]
        assert waiting_b[0] == antrea
        done = client.put(f"/api/me/pair-review/items/{antrea}/verdict", headers=b, json={"verdict": "same"}).json()["item"]
        assert done["consensus"] == "same"
        for name, verdict in (("Data Center", "different"), ("Gemini 3.8 Flash", "same")):
            for h in (a, b):
                assert client.put(f"/api/me/pair-review/items/{names[name]['id']}/verdict", headers=h, json={"verdict": verdict}).status_code == 200
        stats = client.get("/api/me/pair-review/stats", headers=a).json()
        assert stats == {"verdicts": 3, "by_verdict": {"same": 2, "different": 1}, "waiting": 0}
        c = _headers(db_file, members["member_c"])
        assert [it["candidate"]["name"] for it in client.get("/api/me/pair-review/items?limit=20", headers=c).json()["items"]] == ["Secret Tool 2"]
        assert client.delete(f"/api/me/pair-review/items/{names['Data Center']['id']}/verdict", headers=b).status_code == 200
        assert client.delete(f"/api/me/pair-review/items/{names['Data Center']['id']}/verdict", headers=b).status_code == 404
        assert client.put(f"/api/me/pair-review/items/{names['Data Center']['id']}/verdict", headers=b, json={"verdict": "different"}).status_code == 200

        assert client.get("/api/admin/pair-review/report", headers=a).status_code == 404
        admin = _headers(db_file, 1)
        report = client.get("/api/admin/pair-review/report", headers=admin).json()
        assert report["items"]["total"] == 4 and report["items"]["with_consensus"] == 3 and report["items"]["positives"] == 2
        assert report["verdicts"]["accounts"] == 2
        assert report["jev_by_model"]["jev-1.14.0"]["thresholds"]["0.5"] == {**report["jev_by_model"]["jev-1.14.0"]["thresholds"]["0.5"], "tp": 2, "fp": 1}
        assert report["pipeline_merges"] == {"labeled": 1, "false_merges": 0}

    args = cli.build_parser().parse_args(["pair-review", "report", "--json"])
    assert args.func(args) == 0
    printed = json.loads(capsys.readouterr().out)
    assert printed["items"]["with_consensus"] == 3

    from claire.graph_bundle_review import export_review_evidence

    source = dbm.connect_existing(db_file)
    source.row_factory = sqlite3.Row
    export = sqlite3.connect(":memory:")
    export.row_factory = sqlite3.Row
    try:
        doc_ids = {r[0] for r in source.execute("SELECT id FROM documents WHERE kb_id=0")}
        summary = export_review_evidence(source, export, settings=settings, data_dir=db_file.parent, entity_ids=set(), document_ids=doc_ids, now_epoch=time.time())
        assert summary["schema_version"] == 4 and summary["pair_review_items"] == 3
        cols = {r[1] for r in export.execute("PRAGMA table_info(review_pair_verdicts)")}
        assert "account_id" not in cols and "note" not in cols
        assert {r[0] for r in export.execute("SELECT contributor FROM review_pair_verdicts")} == {1, 2}
        meta = json.loads(export.execute("SELECT value FROM review_meta WHERE key='pair_review'").fetchone()[0])
        assert meta["status"] == "ok" and meta["items"]["with_consensus"] == 3
    finally:
        source.close()
        export.close()


def _private_item_id(db_file: Path) -> int:
    conn = dbm.connect_existing(db_file)
    try:
        return int(conn.execute("SELECT id FROM pair_review_items WHERE kb_id=77").fetchone()[0])
    finally:
        conn.close()
