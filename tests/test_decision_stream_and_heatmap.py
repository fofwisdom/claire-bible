"""단위 테스트: Decision Stream 및 Heatmap Matrix (방안 B 및 TypeSafe AI Jev 옵션화).

검증 항목:
1. ResolutionDecision 및 HeatmapMatrixData 데이터 구조 직렬화/역직렬화
2. 방안 B: documents.meta 기반 경량 보관 및 복원 (attach_resolution_meta / get_resolution_log_from_meta)
3. build_fallback_matrix 코사인/결정 투영 행렬 생성
4. evaluate_decision_matrix: Jev 활성화/비활성화/키 누락/오류 발생 시 Fallback 전환 안전망
5. Settings: Jev 관련 환경변수 로딩 및 기본값 검증
"""

from __future__ import annotations

from unittest.mock import patch

from claire.config import Settings
from claire.extract.decision import (
    HeatmapMatrixData,
    ResolutionDecision,
    attach_resolution_meta,
    build_fallback_matrix,
    evaluate_decision_matrix,
    get_resolution_log_from_meta,
)
from claire.ontology.base import Document, Entity


def test_resolution_decision_serialization():
    dec = ResolutionDecision(
        entity="MemGPT",
        stage="exact_match",
        decision="MERGE",
        candidate="Letta",
        score=1.0,
        reason="Matched alias list directly",
    )
    d = dec.to_dict()
    assert d["entity"] == "MemGPT"
    assert d["stage"] == "exact_match"
    assert d["decision"] == "MERGE"
    assert d["candidate"] == "Letta"
    assert d["score"] == 1.0
    assert d["reason"] == "Matched alias list directly"
    assert isinstance(d["timestamp"], float)

    restored = ResolutionDecision.from_dict(d)
    assert restored.entity == dec.entity
    assert restored.stage == dec.stage
    assert restored.decision == dec.decision
    assert restored.candidate == dec.candidate
    assert restored.score == dec.score
    assert restored.reason == dec.reason
    assert restored.timestamp == dec.timestamp


def test_resolution_decision_from_dict_defaults():
    # 필드가 일부 누락된 딕셔너리에서도 안전 복원
    partial = {"entity": "TestNode", "decision": "CREATE_NEW"}
    dec = ResolutionDecision.from_dict(partial)
    assert dec.entity == "TestNode"
    assert dec.stage == ""
    assert dec.decision == "CREATE_NEW"
    assert dec.candidate is None
    assert dec.score is None


def test_heatmap_matrix_data_serialization():
    data = HeatmapMatrixData(
        document_id="doc_123",
        rows=["Node A", "Node B"],
        cols=["Target X", "Target Y"],
        matrix=[[0.95, 0.1], [0.2, 0.88]],
        threshold_auto_merge=0.93,
        threshold_borderline=0.72,
        threshold_relational=0.70,
        generated_by="fallback_vector",
    )
    d = data.to_dict()
    assert d["document_id"] == "doc_123"
    assert d["rows"] == ["Node A", "Node B"]
    assert len(d["matrix"]) == 2
    assert d["generated_by"] == "fallback_vector"

    restored = HeatmapMatrixData.from_dict(d)
    assert restored.document_id == "doc_123"
    assert restored.matrix == [[0.95, 0.1], [0.2, 0.88]]
    assert restored.threshold_auto_merge == 0.93
    assert restored.generated_by == "fallback_vector"


def test_attach_and_get_resolution_meta_plan_b():
    # 방안 B: documents.meta에 resolution_log 경량 JSON 보관 검증
    doc = Document(
        id="doc_test_1",
        title="Sample Document",
        url="https://example.com/test",
        body="Sample body content",
    )
    decisions = [
        ResolutionDecision(
            entity="Claude Code",
            stage="exact_match",
            decision="MERGE",
            candidate="Claude Code CLI",
            score=1.0,
            reason="Exact normalized match",
        ),
        ResolutionDecision(
            entity="Agentic Loop",
            stage="borderline_llm_judge",
            decision="CREATE_NEW",
            candidate="Agent Loop",
            score=0.75,
            reason="Different concept confirmed by LLM",
        ),
    ]

    attach_resolution_meta(doc, decisions)

    assert doc.meta is not None
    assert doc.meta["has_decision_stream"] is True
    assert len(doc.meta["resolution_log"]) == 2

    # 복원 테스트
    restored_log = get_resolution_log_from_meta(doc.meta)
    assert len(restored_log) == 2
    assert restored_log[0].entity == "Claude Code"
    assert restored_log[0].decision == "MERGE"
    assert restored_log[1].entity == "Agentic Loop"
    assert restored_log[1].decision == "CREATE_NEW"

    # 빈 값 또는 결함 데이터 안전 복원
    assert get_resolution_log_from_meta(None) == []
    assert get_resolution_log_from_meta({}) == []
    assert get_resolution_log_from_meta({"resolution_log": "not_a_list"}) == []
    res_corrupted = get_resolution_log_from_meta({"resolution_log": [None, "invalid", {}]})
    assert len(res_corrupted) == 1
    assert res_corrupted[0].entity == ""
    assert res_corrupted[0].decision == ""


def test_build_fallback_matrix():
    row_entities = [Entity(id="e1", name="Alpha", type="Concept"), "Beta"]
    col_entities = [Entity(id="e2", name="Alpha", type="Concept"), "Gamma"]
    decisions = [
        ResolutionDecision(
            entity="Beta",
            candidate="Gamma",
            stage="borderline_llm_judge",
            decision="MERGE",
            score=0.91,
        )
    ]

    matrix_data = build_fallback_matrix(
        document_id="doc_matrix_test",
        extracted_entities=row_entities,
        candidate_entities=col_entities,
        decisions=decisions,
        default_base_score=0.05,
    )

    assert matrix_data.document_id == "doc_matrix_test"
    assert matrix_data.rows == ["Alpha", "Beta"]
    assert matrix_data.cols == ["Alpha", "Gamma"]
    assert matrix_data.generated_by == "fallback_vector"
    assert len(matrix_data.matrix) == 2
    assert len(matrix_data.matrix[0]) == 2

    # Alpha vs Alpha: 동일 이름 1.0 매핑
    assert matrix_data.matrix[0][0] == 1.0
    # Beta vs Gamma: 의사결정 score 0.91 반영
    assert matrix_data.matrix[1][1] == 0.91
    # Alpha vs Gamma: 기본 base score 0.05
    assert matrix_data.matrix[0][1] == 0.05


def test_evaluate_decision_matrix_jev_disabled():
    settings = Settings(enable_jev=False, jev_api_key="some-key")
    res = evaluate_decision_matrix(
        settings=settings,
        document_id="doc_eval_1",
        extracted_entities=["A"],
        candidate_entities=["B"],
    )
    assert res.generated_by == "fallback_vector"


def test_evaluate_decision_matrix_jev_enabled_without_key():
    settings = Settings(enable_jev=True, jev_api_key=None)
    res = evaluate_decision_matrix(
        settings=settings,
        document_id="doc_eval_2",
        extracted_entities=["A"],
        candidate_entities=["B"],
    )
    assert res.generated_by == "fallback_vector"


def test_evaluate_decision_matrix_jev_enabled_with_key():
    settings = Settings(enable_jev=True, jev_api_key="valid-test-key")
    res = evaluate_decision_matrix(
        settings=settings,
        document_id="doc_eval_3",
        extracted_entities=["A"],
        candidate_entities=["A"],
    )
    assert res.generated_by == "jev"
    assert res.matrix[0][0] == 0.99


def test_evaluate_decision_matrix_jev_fallback_on_exception():
    settings = Settings(enable_jev=True, jev_api_key="valid-test-key")
    with patch("claire.extract.decision._call_jev_matrix_api", side_effect=RuntimeError("API timeout")):
        res = evaluate_decision_matrix(
            settings=settings,
            document_id="doc_eval_err",
            extracted_entities=["A"],
            candidate_entities=["B"],
        )
        assert res.generated_by == "fallback_vector"


def test_settings_jev_configuration(monkeypatch):
    monkeypatch.setenv("CLAIRE_ENABLE_JEV", "1")
    monkeypatch.setenv("CLAIRE_JEV_API_KEY", "jev_sec_12345")
    monkeypatch.setenv("CLAIRE_JEV_BASE_URL", "https://custom-jev.example.com/v1")
    monkeypatch.setenv("CLAIRE_JEV_TIMEOUT", "20.5")

    settings = Settings()
    assert settings.enable_jev is True
    assert settings.jev_api_key == "jev_sec_12345"
    assert settings.jev_base_url == "https://custom-jev.example.com/v1"
    assert settings.jev_timeout == 20.5
