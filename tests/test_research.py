"""맥락 확장 조사(expand/research) — 조사→판정 게이트→통과 시에만 그래프 적재.

MockProvider 훅: '조사불가' 포함 쿼리 → INSUFFICIENT, '모호' 포함 쿼리 → 저품질 판정.
실제 조사/판정 품질은 실 Gemini 로 검증하고, 여기선 게이트 배선과 적재 경로를 본다.
"""

from __future__ import annotations

from claire.config import Settings
from claire.expand.research import build_context, contextual_research
from claire.extract.provider import MockProvider
from claire.ontology.base import Document, Entity
from claire.store import db as dbm


def _settings(monkeypatch, tmp_path):
    monkeypatch.setenv("CLAIRE_DB_PATH", str(tmp_path / "r.db"))
    monkeypatch.setenv("CLAIRE_VAULT_PATH", str(tmp_path / "vault"))
    monkeypatch.setenv("CLAIRE_PROVIDER", "mock")
    return Settings()


def _seed(s):
    conn = dbm.connect(s.db_file)
    dbm.init_db(conn)
    dbm.insert_document(conn, Document(id="d1", title="OpenSkill 소개 글",
                                       raw_text="rating system", source_type="web",
                                       content_hash="h1"))
    dbm.upsert_entity(conn, Entity(id="e1", type="Tool", name="OpenSkill",
                                   observations=["순위 산정 라이브러리"], sources=["d1"]))
    conn.commit()
    conn.close()


def test_research_pass_gate_ingests_document(monkeypatch, tmp_path):
    s = _settings(monkeypatch, tmp_path)
    _seed(s)
    out = contextual_research(s, MockProvider(), query="TrueSkill 차이", node_id="e1")
    assert out["added"] is True and out["verdict"] == "그래프에 추가됨"
    assert out["relevance"] >= 0.7 and out["quality"] >= 0.6
    assert "[mock-research]" in out["report"]
    assert out["context_focus"] == "OpenSkill"  # 맥락 대표 이름
    ing = out["ingest"]
    assert ing["error"] is None and ing["entities_created"] >= 1
    # 적재된 문서: source_type=research, 본문에 보고서+맥락 헤더 보존
    conn = dbm.connect(s.db_file)
    row = dbm.get_document_row(conn, ing["document_id"])
    assert row["source_type"] == "research"
    assert row["title"].startswith("조사:")
    assert "[맥락 조사]" in row["raw_text"] and "OpenSkill" in row["raw_text"]
    conn.close()


def test_research_gate_rejects_low_relevance(monkeypatch, tmp_path):
    """판정 미달 → 보고서는 반환하되 그래프에는 추가하지 않음(다의어 오염 방지)."""
    s = _settings(monkeypatch, tmp_path)
    _seed(s)
    out = contextual_research(s, MockProvider(), query="모호한 키워드", node_id="e1")
    assert out["added"] is False and out["verdict"].startswith("보류")
    assert out["report"]  # 보고서 자체는 사용자에게 보여준다
    conn = dbm.connect(s.db_file)
    n = conn.execute("SELECT COUNT(*) FROM documents WHERE source_type='research'").fetchone()[0]
    assert n == 0  # research 문서 미생성
    conn.close()


def test_research_insufficient_report_not_added(monkeypatch, tmp_path):
    """조사가 INSUFFICIENT 선언(맥락 일치 자료 없음) → 판정 없이 보류."""
    s = _settings(monkeypatch, tmp_path)
    _seed(s)
    out = contextual_research(s, MockProvider(), query="조사불가 항목", node_id="e1")
    assert out["added"] is False and "불충분" in out["verdict"]


def test_research_requires_context_and_query(monkeypatch, tmp_path):
    s = _settings(monkeypatch, tmp_path)
    _seed(s)
    assert "error" in contextual_research(s, MockProvider(), query="x")  # 맥락 없음
    assert "error" in contextual_research(s, MockProvider(), query="", node_id="e1")


def test_build_context_doc_only(monkeypatch, tmp_path):
    """노드 없이 문서만 활성인 경우 — 문서 제목/요약이 맥락이 된다."""
    s = _settings(monkeypatch, tmp_path)
    _seed(s)
    conn = dbm.connect(s.db_file)
    ctx, focus = build_context(conn, node_id=None, doc_id="d1")
    assert "OpenSkill 소개 글" in ctx and focus == "OpenSkill 소개 글"
    conn.close()
