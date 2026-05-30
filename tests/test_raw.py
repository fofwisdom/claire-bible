"""재적재(raw preservation) 3-tier 검증 — 알고리즘 변경 시 재생 가능해야 함."""

from __future__ import annotations

import sqlite3
from pathlib import Path

from claire.ontology.base import Document
from claire.store import db as dbm
from claire.store.raw import save_artifact, load_artifact, raw_disk_usage
from claire.store.vectors import VectorStore
from claire.extract.provider import MockProvider
from claire.ingest.pipeline import ingest, _guess_kind


def _db():
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    dbm.init_db(conn)
    return conn


def _fetch(doc):
    return lambda p: doc


def test_guess_kind():
    assert _guess_kind("https://x.com") == "url"
    assert _guess_kind("file:///a/b") == "file"
    assert _guess_kind("just text") == "text"


def test_inbox_recorded_before_processing_even_on_failure():
    conn = _db()
    vstore = VectorStore(conn, "brute")

    def boom(_p):
        raise RuntimeError("net down")

    rep = ingest("https://dead.link", conn=conn, provider=MockProvider(),
                 vstore=vstore, fetch_fn=boom, source="test")
    # 실패해도 inbox 에는 원본이 남아야 재생 가능
    rows = dbm.all_inbox(conn)
    assert len(rows) == 1
    assert rows[0]["payload"] == "https://dead.link"
    assert rows[0]["status"] == "error"
    assert rows[0]["kind"] == "url"
    assert rep.inbox_id == rows[0]["id"]


def test_inbox_status_done_and_duplicate():
    conn = _db()
    vstore = VectorStore(conn, "brute")
    doc = Document(title="D", raw_text="body text", source_type="text", content_hash="h1")
    p = MockProvider()
    r1 = ingest("payload-x", conn=conn, provider=p, vstore=vstore, fetch_fn=_fetch(doc), source="test")
    r2 = ingest("payload-x", conn=conn, provider=p, vstore=vstore, fetch_fn=_fetch(doc), source="test")
    rows = dbm.all_inbox(conn)
    assert len(rows) == 2                       # 원본은 항상 2건 보관
    assert rows[0]["status"] == "done"
    assert rows[1]["status"] == "duplicate"     # 2번째는 dedup
    assert r2.duplicate


def test_extraction_raw_json_stored():
    conn = _db()
    vstore = VectorStore(conn, "brute")
    doc = Document(title="Graphify", url="https://github.com/safishamsi/graphify",
                   raw_text="kg gen", source_type="web", content_hash="hg")
    ingest("u", conn=conn, provider=MockProvider(), vstore=vstore, fetch_fn=_fetch(doc), source="test")
    rows = conn.execute("SELECT * FROM extractions").fetchall()
    assert len(rows) == 1
    assert rows[0]["model"] == "mock"
    assert rows[0]["prompt_version"] == "mock-1"
    assert "graphify" in rows[0]["raw_response"].lower()  # raw JSON 재생용


def test_layer2_artifact_saved_and_loadable(tmp_path: Path):
    conn = _db()
    vstore = VectorStore(conn, "brute")
    doc = Document(title="T", raw_text="the original fetched body", source_type="web",
                   content_hash="ha")
    rep = ingest("u", conn=conn, provider=MockProvider(), vstore=vstore,
                 fetch_fn=_fetch(doc), source="test", data_dir=tmp_path)
    # gzip artifact 가 doc id 로 저장되어 원문 복원 가능
    back = load_artifact(tmp_path, rep.document_id)
    assert back == "the original fetched body"
    usage = raw_disk_usage(tmp_path)
    assert usage["artifacts"] > 0


def test_save_artifact_roundtrip(tmp_path: Path):
    save_artifact(tmp_path, "doc_1", "héllo 안녕 <b>x</b>")
    assert load_artifact(tmp_path, "doc_1") == "héllo 안녕 <b>x</b>"
    assert load_artifact(tmp_path, "missing") is None
