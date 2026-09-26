"""지식 노드 판단 시각화 및 TypeSafe AI Jev 연동 데이터 모듈.

  - ResolutionDecision: 단일 엔티티/관계의 상태 전이 의사결정 레코드
  - DecisionStreamLog: 문서 단위의 전체 인과 추적 로그 (방안 B: documents.meta 보관)
  - HeatmapMatrixData: 본문/엔티티 × 전역 지식 노드 간 전수 유사도 확률 행렬 (일회성 소비)
  - JevClientConfig / evaluate_matrix: TypeSafe AI Jev (System 1) 연동 및 Fallback 파이프라인
"""

from __future__ import annotations

import time
from dataclasses import asdict, dataclass, field
from typing import Any

from ..ontology.base import Document, Entity


@dataclass
class ResolutionDecision:
    """단일 엔티티 또는 관계의 상태 전이 의사결정 레코드."""

    entity: str
    stage: str  # 'exact_match' | 'acronym_match' | 'borderline_llm_judge' | 'cross_link'
    decision: str  # 'MERGE' | 'CREATE_NEW' | 'CROSS_LINK' | 'REJECT'
    candidate: str | None = None
    score: float | None = None
    reason: str = ""
    timestamp: float = field(default_factory=time.time)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> ResolutionDecision:
        return cls(
            entity=str(data.get("entity") or ""),
            stage=str(data.get("stage") or ""),
            decision=str(data.get("decision") or ""),
            candidate=data.get("candidate"),
            score=float(data["score"]) if data.get("score") is not None else None,
            reason=str(data.get("reason") or ""),
            timestamp=float(data.get("timestamp") or time.time()),
        )


@dataclass
class HeatmapMatrixData:
    """본문 구절/엔티티 × 전역 지식 노드 간의 전수 유사도 확률 매트릭스."""

    document_id: str
    rows: list[str]  # 본문 구절 또는 추출 엔티티 목록
    cols: list[str]  # 전역 지식 베이스 후보 노드 목록
    matrix: list[list[float]]  # [len(rows)][len(cols)] 0.0 ~ 1.0 확률 텐서
    threshold_auto_merge: float = 0.93
    threshold_borderline: float = 0.72
    threshold_relational: float = 0.70
    generated_by: str = "fallback_vector"  # 'jev' | 'fallback_vector'
    created_at: float = field(default_factory=time.time)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> HeatmapMatrixData:
        return cls(
            document_id=str(data.get("document_id") or ""),
            rows=list(data.get("rows") or []),
            cols=list(data.get("cols") or []),
            matrix=list(data.get("matrix") or []),
            threshold_auto_merge=float(data.get("threshold_auto_merge") or 0.93),
            threshold_borderline=float(data.get("threshold_borderline") or 0.72),
            threshold_relational=float(data.get("threshold_relational") or 0.70),
            generated_by=str(data.get("generated_by") or "fallback_vector"),
            created_at=float(data.get("created_at") or time.time()),
        )


# --- 방안 B: 경량 메타데이터(documents.meta) 헬퍼 ---


def attach_resolution_meta(
    doc: Document,
    decisions: list[ResolutionDecision],
) -> None:
    """엔티티 해소 의사결정 이력을 문서 메타데이터(documents.meta)에 경량 JSON으로 첨부 (방안 B)."""
    if doc.meta is None:
        doc.meta = {}
    doc.meta["resolution_log"] = [d.to_dict() for d in decisions]
    doc.meta["has_decision_stream"] = bool(decisions)


def get_resolution_log_from_meta(meta: dict[str, Any] | None) -> list[ResolutionDecision]:
    """문서 메타데이터에서 의사결정 이력(Decision Stream) 목록을 복원."""
    if not meta:
        return []
    raw_list = meta.get("resolution_log")
    if not isinstance(raw_list, list):
        return []
    results: list[ResolutionDecision] = []
    for item in raw_list:
        if isinstance(item, dict):
            try:
                results.append(ResolutionDecision.from_dict(item))
            except Exception:
                continue
    return results


# --- Heatmap Matrix 생성 엔진 (Jev 및 Fallback) ---


def build_fallback_matrix(
    document_id: str,
    extracted_entities: list[Entity | str],
    candidate_entities: list[Entity | str],
    decisions: list[ResolutionDecision] | None = None,
    default_base_score: float = 0.05,
) -> HeatmapMatrixData:
    """Jev 미사용 시 기존 코사인 유사도 및 어휘 일치 결정을 투영한 Fallback 매트릭스 생성.

    수치나 텍스트 없이 프론트엔드가 순수 색조 농도로 렌더링할 수 있도록 0.0~1.0 계조를 구성합니다.
    """
    row_names = [e.name if isinstance(e, Entity) else str(e) for e in extracted_entities]
    col_names = [e.name if isinstance(e, Entity) else str(e) for e in candidate_entities]

    # 기본 그리드 초기화
    matrix: list[list[float]] = [
        [default_base_score for _ in range(len(col_names))] for _ in range(len(row_names))
    ]

    # 결정 기록 반영
    if decisions:
        dec_map: dict[tuple[str, str], float] = {}
        for d in decisions:
            if d.candidate:
                score = d.score if d.score is not None else (1.0 if d.decision == "MERGE" else 0.85)
                dec_map[(d.entity, d.candidate)] = score

        for r_idx, r_name in enumerate(row_names):
            for c_idx, c_name in enumerate(col_names):
                if (r_name, c_name) in dec_map:
                    matrix[r_idx][c_idx] = dec_map[(r_name, c_name)]
                elif r_name == c_name:
                    matrix[r_idx][c_idx] = 1.0

    return HeatmapMatrixData(
        document_id=document_id,
        rows=row_names,
        cols=col_names,
        matrix=matrix,
        generated_by="fallback_vector",
    )


def evaluate_decision_matrix(
    settings: Any,
    document_id: str,
    extracted_entities: list[Entity | str],
    candidate_entities: list[Entity | str],
    decisions: list[ResolutionDecision] | None = None,
) -> HeatmapMatrixData:
    """설정에 따라 TypeSafe AI Jev (System 1) 또는 Fallback 엔진을 통해 HeatmapMatrixData를 생성."""
    enable_jev = bool(getattr(settings, "enable_jev", False))
    jev_key = getattr(settings, "jev_api_key", None)

    if enable_jev and jev_key:
        try:
            # TypeSafe AI Jev 호출 분기 (API 연동 인터페이스 스텁)
            return _call_jev_matrix_api(
                settings=settings,
                document_id=document_id,
                extracted_entities=extracted_entities,
                candidate_entities=candidate_entities,
            )
        except Exception:
            # Jev 호출 실패 시 시스템 중단 없이 안전하게 Fallback 매트릭스로 전환
            pass

    return build_fallback_matrix(
        document_id=document_id,
        extracted_entities=extracted_entities,
        candidate_entities=candidate_entities,
        decisions=decisions,
    )


def _call_jev_matrix_api(
    settings: Any,
    document_id: str,
    extracted_entities: list[Entity | str],
    candidate_entities: list[Entity | str],
) -> HeatmapMatrixData:
    """TypeSafe AI Jev 비-자기회귀 의사결정 API 호출 스텁 (System 1 Engine)."""
    # 실제 연동 시 HTTP 클라이언트(httpx)를 통해 Jev /v1/classify-matrix 엔드포인트 호출
    row_names = [e.name if isinstance(e, Entity) else str(e) for e in extracted_entities]
    col_names = [e.name if isinstance(e, Entity) else str(e) for e in candidate_entities]

    # Jev 규격 결과 텐서 생성
    matrix: list[list[float]] = [
        [0.08 for _ in range(len(col_names))] for _ in range(len(row_names))
    ]
    for r_idx, r_name in enumerate(row_names):
        for c_idx, c_name in enumerate(col_names):
            if r_name == c_name:
                matrix[r_idx][c_idx] = 0.99

    return HeatmapMatrixData(
        document_id=document_id,
        rows=row_names,
        cols=col_names,
        matrix=matrix,
        generated_by="jev",
    )
