"""전체 재추출(reextract) — 저장된 raw_text 로 그래프 재구축 (네트워크 없이).

프롬프트 변경(예: 한글화)을 기존 문서에 반영하는 경로. rebuild 는 먼저 그래프를 비워
관찰이 누적(영문+한글 혼재)되지 않게 한다.
"""

from __future__ import annotations

from claire.config import Settings
from claire.ontology.base import Document
from claire.store import db as dbm
from claire.ingest import service as svcmod
from claire.ingest import pipeline as pipemod
from claire.ingest.service import IngestService


def _mem(monkeypatch, tmp_path):
    monkeypatch.setenv("CLAIRE_DB_PATH", str(tmp_path / "r.db"))
    monkeypatch.setenv("CLAIRE_VAULT_PATH", str(tmp_path / "vault"))
    monkeypatch.setenv("CLAIRE_PROVIDER", "mock")
    return Settings()


def test_reset_graph_keeps_documents():
    import sqlite3
    conn = sqlite3.connect(":memory:"); conn.row_factory = sqlite3.Row
    dbm.init_db(conn)
    dbm.insert_document(conn, Document(id="d1", url="https://x/1", raw_text="body",
                                       source_type="web", content_hash="h"))
    from claire.ontology.base import Entity
    dbm.upsert_entity(conn, Entity(type="Tool", name="Foo", sources=["d1"]))
    assert dbm.counts(conn)["entities"] == 1
    dbm.reset_graph(conn)
    assert dbm.counts(conn)["entities"] == 0
    assert dbm.counts(conn)["documents"] == 1          # 문서는 보존
    assert conn.execute("SELECT count(*) FROM entities_fts").fetchone()[0] == 0


def test_reextract_rebuild_no_observation_accumulation(monkeypatch, tmp_path):
    s = _mem(monkeypatch, tmp_path)
    svc = IngestService(s)
    doc = Document(url="https://github.com/acme/widget", title="Widget",
                   raw_text="acme widget tool " * 20, source_type="web",
                   content_hash="hw")
    monkeypatch.setattr(svcmod, "default_fetch", lambda p: doc)
    monkeypatch.setattr(pipemod, "default_fetch", lambda p: doc)
    rep = svc.ingest("https://github.com/acme/widget", source="test")
    assert rep.document_id

    conn = dbm.connect(s.db_file); dbm.init_db(conn)
    ents_before = dbm.counts(conn)["entities"]
    obs_before = {e.name: len(e.observations) for e in dbm.all_entities(conn)}
    conn.close()
    assert ents_before > 0

    # rebuild 재추출 — 같은 MockProvider 라 결과는 같아야 하고, 관찰이 두 배로 누적되면 안 됨.
    out = svc.reextract_all(rebuild=True)
    assert out["docs"] == 1 and out["ok"] == 1 and out["failed"] == 0

    conn = dbm.connect(s.db_file); dbm.init_db(conn)
    assert dbm.counts(conn)["entities"] == ents_before   # 중복 노드 생기지 않음
    obs_after = {e.name: len(e.observations) for e in dbm.all_entities(conn)}
    conn.close()
    assert obs_after == obs_before                        # 관찰 누적 없음(깨끗한 재구축)


def test_reextract_no_rebuild_merges(monkeypatch, tmp_path):
    """--no-rebuild 는 기존 그래프에 머지(관찰 누적 가능) — 동작 자체는 성공."""
    s = _mem(monkeypatch, tmp_path)
    svc = IngestService(s)
    doc = Document(url="https://github.com/acme/widget", title="Widget",
                   raw_text="acme widget tool " * 20, source_type="web", content_hash="hw")
    monkeypatch.setattr(svcmod, "default_fetch", lambda p: doc)
    monkeypatch.setattr(pipemod, "default_fetch", lambda p: doc)
    svc.ingest("https://github.com/acme/widget", source="test")
    out = svc.reextract_all(rebuild=False)
    assert out["docs"] == 1 and out["failed"] == 0
