"""맥락 확장 조사(expand/research) — 조사→판정 게이트→통과 시에만 그래프 적재.

MockProvider 훅: '조사불가' 포함 쿼리 → INSUFFICIENT, '모호' 포함 쿼리 → 저품질 판정.
실제 조사/판정 품질은 실 Gemini 로 검증하고, 여기선 게이트 배선과 적재 경로를 본다.
"""

from __future__ import annotations

from claire.config import Settings
from claire.expand.research import (
    build_context, contextual_research, plan_research_from_synthesis,
    run_planned_research,
)
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


def test_research_progress_events_in_order(monkeypatch, tmp_path):
    """진행 이벤트(stage,msg)가 단계 순서대로 흐른다 — UI 실시간 표시용 스트림 계약."""
    s = _settings(monkeypatch, tmp_path)
    _seed(s)
    events = []
    out = contextual_research(s, MockProvider(), query="TrueSkill 차이", node_id="e1",
                              progress=events.append)
    assert out["added"] is True
    stages = [e["stage"] for e in events]
    # context → research(시작·완료) → judge(시작·완료) → ingest(시작·완료)
    assert stages == ["context", "research", "research", "judge", "judge",
                      "ingest", "ingest"]
    assert "OpenSkill" in events[0]["msg"]          # 맥락 구성에 focus 표기
    assert "보고서" in events[2]["msg"]              # 조사 완료에 보고서 길이
    assert "맥락일치" in events[4]["msg"]            # 판정 점수
    assert "적재 완료" in events[6]["msg"]


def test_research_progress_callback_error_is_harmless(monkeypatch, tmp_path):
    """progress 콜백이 던져도 본 흐름은 영향 없음(전시용 채널은 비필수)."""
    s = _settings(monkeypatch, tmp_path)
    _seed(s)

    def bad(_ev):
        raise RuntimeError("boom")

    out = contextual_research(s, MockProvider(), query="TrueSkill 차이", node_id="e1",
                              progress=bad)
    assert out["added"] is True


def test_build_context_doc_only(monkeypatch, tmp_path):
    """노드 없이 문서만 활성인 경우 — 문서 제목/요약이 맥락이 된다."""
    s = _settings(monkeypatch, tmp_path)
    _seed(s)
    conn = dbm.connect(s.db_file)
    ctx, focus = build_context(conn, node_id=None, doc_id="d1")
    assert "OpenSkill 소개 글" in ctx and focus == "OpenSkill 소개 글"
    conn.close()


# --- 종합(synthesis) → 조사계획 → 다중 웹조사 → 문서합성 → 적재 -------------------


def test_mock_provider_plan_research_and_compose():
    """provider 계약 자체(개수·반환 키) — 배선 검증용, 실제 품질은 Gemini 로 확인."""
    p = MockProvider()
    items = p.plan_research("맥락", "종합답변", 3)
    assert len(items) == 3
    assert all("question" in it and "rationale" in it for it in items)
    out = p.compose_document("맥락", "종합답변", [{"question": "q1", "report": "r1", "sources": []}])
    assert out["title"] and "q1" in out["body"] and "r1" in out["body"]


def test_plan_research_from_synthesis_returns_default_count(monkeypatch, tmp_path):
    s = _settings(monkeypatch, tmp_path)
    _seed(s)
    out = plan_research_from_synthesis(s, MockProvider(), entity_ids=["e1"],
                                       synth_answer="OpenSkill 종합 답변")
    assert "error" not in out
    assert len(out["questions"]) == 3  # CLAIRE_RESEARCH_PLAN_MAX 기본값
    assert out["entities"] == ["OpenSkill"]


def test_plan_research_from_synthesis_respects_env_override(monkeypatch, tmp_path):
    monkeypatch.setenv("CLAIRE_RESEARCH_PLAN_MAX", "5")
    s = _settings(monkeypatch, tmp_path)
    _seed(s)
    out = plan_research_from_synthesis(s, MockProvider(), entity_ids=["e1"], synth_answer="x")
    assert len(out["questions"]) == 5


def test_plan_research_from_synthesis_requires_entity_ids(monkeypatch, tmp_path):
    s = _settings(monkeypatch, tmp_path)
    _seed(s)
    assert "error" in plan_research_from_synthesis(s, MockProvider(), entity_ids=[],
                                                    synth_answer="x")


def test_run_planned_research_passes_and_ingests(monkeypatch, tmp_path):
    s = _settings(monkeypatch, tmp_path)
    _seed(s)
    out = run_planned_research(s, MockProvider(), entity_ids=["e1"], synth_answer="x",
                               questions=["TrueSkill 비교", "역사"])
    assert out["added"] is True
    assert len(out["passed"]) == 2 and not out["rejected"]
    assert out["document"]["title"]
    conn = dbm.connect(s.db_file)
    row = dbm.get_document_row(conn, out["ingest"]["document_id"])
    assert row["source_type"] == "synthesis_research"
    conn.close()


def test_run_planned_research_partial_gate_rejection(monkeypatch, tmp_path):
    """일부 질문(MockProvider 의 '모호' 훅)만 게이트 탈락해도 통과분으로 진행."""
    s = _settings(monkeypatch, tmp_path)
    _seed(s)
    out = run_planned_research(s, MockProvider(), entity_ids=["e1"], synth_answer="x",
                               questions=["모호한 질문", "정상 질문"])
    assert out["added"] is True
    assert len(out["passed"]) == 1 and len(out["rejected"]) == 1


def test_run_planned_research_all_rejected_fails(monkeypatch, tmp_path):
    s = _settings(monkeypatch, tmp_path)
    _seed(s)
    out = run_planned_research(s, MockProvider(), entity_ids=["e1"], synth_answer="x",
                               questions=["모호한 질문1", "모호한 질문2"])
    assert out["added"] is False and "error" in out
    conn = dbm.connect(s.db_file)
    n = conn.execute(
        "SELECT COUNT(*) FROM documents WHERE source_type='synthesis_research'").fetchone()[0]
    assert n == 0
    conn.close()


def test_run_planned_research_requires_entity_ids_and_questions(monkeypatch, tmp_path):
    s = _settings(monkeypatch, tmp_path)
    _seed(s)
    assert run_planned_research(s, MockProvider(), entity_ids=[], synth_answer="x",
                                questions=["q"])["added"] is False
    assert run_planned_research(s, MockProvider(), entity_ids=["e1"], synth_answer="x",
                                questions=[])["added"] is False


def test_run_planned_research_progress_events(monkeypatch, tmp_path):
    s = _settings(monkeypatch, tmp_path)
    _seed(s)
    events = []
    out = run_planned_research(s, MockProvider(), entity_ids=["e1"], synth_answer="x",
                               questions=["TrueSkill 비교"], progress=events.append)
    assert out["added"] is True
    stages = [e["stage"] for e in events]
    assert stages == ["research", "research", "compose", "ingest", "ingest"]
