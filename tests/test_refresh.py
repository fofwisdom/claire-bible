"""복원(refresh) 메커니즘 — 큐 + in-place 재적재 (네트워크 없이)."""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from claire.config import Settings
from claire.ontology.base import Document
from claire.store import db as dbm
from claire.ingest import service as svcmod
from claire.ingest import pipeline as pipemod
from claire.ingest.service import IngestService


def _patch_fetch(monkeypatch, fn):
    """svc.ingest 는 pipeline.default_fetch 를, refresh_document 는 svcmod.default_fetch 를
    쓰므로 둘 다 패치해야 네트워크 없이 동작한다."""
    monkeypatch.setattr(svcmod, "default_fetch", fn)
    monkeypatch.setattr(pipemod, "default_fetch", fn)


def _mem(monkeypatch, tmp_path):
    db = tmp_path / "r.db"
    monkeypatch.setenv("CLAIRE_DB_PATH", str(db))
    monkeypatch.setenv("CLAIRE_VAULT_PATH", str(tmp_path / "vault"))
    monkeypatch.setenv("CLAIRE_PROVIDER", "mock")
    return Settings()


# --- db 큐 헬퍼 ---

def test_enqueue_dedup_and_pending(tmp_path):
    conn = sqlite3.connect(":memory:"); conn.row_factory = sqlite3.Row
    dbm.init_db(conn)
    assert dbm.enqueue_refresh(conn, document_id="d1", payload="u1", reason="thin") is True
    # 같은 doc 재등록 → 신규 아님(되살림)
    assert dbm.enqueue_refresh(conn, document_id="d1", payload="u1", reason="thin") is False
    pend = dbm.pending_refresh(conn)
    assert len(pend) == 1 and pend[0]["document_id"] == "d1"
    dbm.update_refresh(conn, pend[0]["id"], status="done")
    assert dbm.pending_refresh(conn) == []
    assert dbm.refresh_status_counts(conn) == {"done": 1}


def test_thin_documents_filter():
    conn = sqlite3.connect(":memory:"); conn.row_factory = sqlite3.Row
    dbm.init_db(conn)
    dbm.insert_document(conn, Document(id="a", url="https://x.kr/1", raw_text="x"*50,
                                       source_type="web", content_hash="h1"))
    dbm.insert_document(conn, Document(id="b", url="https://x.kr/2", raw_text="y"*5000,
                                       source_type="web", content_hash="h2"))
    dbm.insert_document(conn, Document(id="c", url="https://other.com/3", raw_text="z"*50,
                                       source_type="web", content_hash="h3"))
    thin = dbm.thin_documents(conn, max_len=300)
    assert {r["id"] for r in thin} == {"a", "c"}
    thin_host = dbm.thin_documents(conn, max_len=300, host="x.kr")
    assert {r["id"] for r in thin_host} == {"a"}


# --- service refresh_document (fetch monkeypatched) ---

def test_refresh_updates_in_place(monkeypatch, tmp_path):
    s = _mem(monkeypatch, tmp_path)
    svc = IngestService(s)
    # 초기: thin 문서 적재
    thin_doc = Document(url="https://discuss.x/t/foo/1", title="T", raw_text="짧음",
                        source_type="web", content_hash="old")
    _patch_fetch(monkeypatch, lambda p: thin_doc)
    rep = svc.ingest("https://discuss.x/t/foo/1", source="test")
    doc_id = rep.document_id
    assert rep.document_id

    # 이제 fetch 가 풍부한 본문을 준다(스크래퍼 개선 가정)
    rich = Document(url="https://discuss.x/t/foo/1", title="T rich",
                    raw_text="풍부한 본문 " * 100, source_type="web", content_hash="new")
    _patch_fetch(monkeypatch, lambda p: rich)
    res = svc.refresh_document(doc_id, "https://discuss.x/t/foo/1")
    assert res["status"] == "done"
    assert res["new_len"] > res["old_len"]

    # 같은 id 로 in-place 갱신됐는지(문서 수 안 늘어남)
    conn = dbm.connect(s.db_file); dbm.init_db(conn)
    assert dbm.counts(conn)["documents"] == 1
    row = dbm.get_document_row(conn, doc_id)
    assert row["content_hash"] == "new" and len(row["raw_text"]) > 100
    conn.close()


def test_refresh_nochange_when_same_hash(monkeypatch, tmp_path):
    s = _mem(monkeypatch, tmp_path)
    svc = IngestService(s)
    doc = Document(url="https://x/1", title="T", raw_text="body", source_type="web",
                   content_hash="same")
    _patch_fetch(monkeypatch, lambda p: doc)
    rep = svc.ingest("https://x/1", source="test")
    res = svc.refresh_document(rep.document_id, "https://x/1")
    assert res["status"] == "nochange"


def test_run_refresh_queue_marks_done(monkeypatch, tmp_path):
    s = _mem(monkeypatch, tmp_path)
    svc = IngestService(s)
    thin = Document(url="https://discuss.x/t/a/1", title="T", raw_text="x",
                    source_type="web", content_hash="o")
    _patch_fetch(monkeypatch, lambda p: thin)
    rep = svc.ingest("https://discuss.x/t/a/1", source="test")
    # 큐 등록
    n = svc.mark_thin_for_refresh(max_len=300)
    assert n == 1
    # 풍부한 본문으로 교체 후 큐 실행
    rich = Document(url="https://discuss.x/t/a/1", title="T2", raw_text="본문 " * 200,
                    source_type="web", content_hash="n")
    _patch_fetch(monkeypatch, lambda p: rich)
    results = svc.run_refresh_queue(limit=10)
    assert len(results) == 1 and results[0]["status"] == "done"
    conn = dbm.connect(s.db_file); dbm.init_db(conn)
    assert dbm.refresh_status_counts(conn) == {"done": 1}
    conn.close()
