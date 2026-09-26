"""엔티티 해소(Entity Resolution) 실측 벤치마크 하니스 모듈.

System 2 (Gemini LLM Judge)와 System 1 (TypeSafe AI Jev) 간의
정밀도(Precision), 재현율(Recall), 거짓 병합률(False Positive Rate), 지연시간(P95 Latency)을
정량적으로 실측하고 비교하기 위한 골든 데이터셋 및 평가 엔진을 제공합니다.
"""

from __future__ import annotations

import time
from dataclasses import asdict, dataclass, field
from typing import Any, Callable


@dataclass
class ResolutionBenchmarkCase:
    """단일 엔티티 해소 평가 케이스."""

    id: str
    new_name: str
    new_type: str
    candidate_name: str
    candidate_type: str
    expected_merge: bool  # Ground Truth: True=동일체 병합, False=독립 노드 분리
    category: str  # 'exact', 'alias', 'acronym', 'synonym', 'rival_tool', 'version_split', 'polysemy'
    description: str = ""
    new_aliases: list[str] = field(default_factory=list)
    new_observations: list[str] = field(default_factory=list)
    candidate_aliases: list[str] = field(default_factory=list)
    candidate_observations: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class BenchmarkMetrics:
    """벤치마크 정량 측정 결과."""

    engine_name: str
    total_cases: int
    true_positives: int  # 올바른 병합
    true_negatives: int  # 올바른 분리
    false_positives: int  # 잘못된 병합 (치명적 DB 오염 위험)
    false_negatives: int  # 잘못된 분리 (미병합)
    precision: float
    recall: float
    f1_score: float
    false_positive_rate: float  # fp / (fp + tn)
    avg_latency_ms: float
    p95_latency_ms: float
    failed_cases: list[dict[str, Any]] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


# --- 1. 정본 골든 데이터셋 (Golden Dataset) ---
GOLDEN_RESOLUTION_CASES: list[ResolutionBenchmarkCase] = [
    # 1) Exact & Case-insensitive Matches (True)
    ResolutionBenchmarkCase(
        id="exact-01",
        new_name="claude code",
        new_type="Tool",
        candidate_name="Claude Code",
        candidate_type="Tool",
        expected_merge=True,
        category="exact",
        description="대소문자 정규화 동일체",
    ),
    ResolutionBenchmarkCase(
        id="exact-02",
        new_name="Scrapling",
        new_type="Framework",
        candidate_name="scrapling",
        candidate_type="Framework",
        expected_merge=True,
        category="exact",
        description="동일 프레임워크 대소문자 변형",
    ),
    # 2) Alias Matches (True)
    ResolutionBenchmarkCase(
        id="alias-01",
        new_name="MemGPT",
        new_type="Framework",
        candidate_name="Letta",
        candidate_type="Framework",
        candidate_aliases=["MemGPT"],
        expected_merge=True,
        category="alias",
        description="과거 명칭 및 리브랜딩 별칭 수렴",
    ),
    # 3) Acronym Matches (True)
    ResolutionBenchmarkCase(
        id="acronym-01",
        new_name="MCP",
        new_type="Concept",
        candidate_name="Model Context Protocol",
        candidate_type="Concept",
        expected_merge=True,
        category="acronym",
        description="공식 프로토콜 약어 수렴",
    ),
    ResolutionBenchmarkCase(
        id="acronym-02",
        new_name="Model Context Protocol",
        new_type="Concept",
        candidate_name="MCP",
        candidate_type="Concept",
        expected_merge=True,
        category="acronym",
        description="약어 노드에 풀네임 유입 역방향 수렴",
    ),
    # 4) True Synonyms (True)
    ResolutionBenchmarkCase(
        id="synonym-01",
        new_name="K8s",
        new_type="Tool",
        candidate_name="Kubernetes",
        candidate_type="Tool",
        expected_merge=True,
        category="synonym",
        description="널리 통용되는 축약 동의어",
    ),
    ResolutionBenchmarkCase(
        id="synonym-02",
        new_name="Postgres",
        new_type="Tool",
        candidate_name="PostgreSQL",
        candidate_type="Tool",
        expected_merge=True,
        category="synonym",
        description="데이터베이스 통용 약칭 동의어",
    ),
    ResolutionBenchmarkCase(
        id="synonym-03",
        new_name="Agent Memory Server",
        new_type="Framework",
        candidate_name="Letta",
        candidate_type="Framework",
        expected_merge=True,
        category="synonym",
        new_observations=["LLM 기반 에이전트 상태 및 메모리 관리 서버"],
        candidate_observations=["Letta는 에이전트의 장기 메모리를 관리하는 서버 프레임워크"],
        description="문맥적 기능 설명 동의어",
    ),
    # 5) Acronym Type Mis-matches & Short Acronyms (False - FPR 방지)
    ResolutionBenchmarkCase(
        id="acronym-reject-01",
        new_name="MCP",
        new_type="Tool",
        candidate_name="Model Context Protocol",
        candidate_type="Concept",
        expected_merge=False,
        category="acronym",
        description="약어와 풀네임의 온톨로지 타입 불일치 분리",
    ),
    ResolutionBenchmarkCase(
        id="acronym-reject-02",
        new_name="AI",
        new_type="Concept",
        candidate_name="Artificial Intelligence",
        candidate_type="Concept",
        expected_merge=False,
        category="acronym",
        description="2글자 모호 약어 결정론적 자동 수렴 배제",
    ),
    # 6) Rival & Adjacent Tools (False - 치명적 거짓 양성 FPR 방지)
    ResolutionBenchmarkCase(
        id="rival-01",
        new_name="LlamaIndex",
        new_type="Framework",
        candidate_name="Letta",
        candidate_type="Framework",
        expected_merge=False,
        category="rival_tool",
        new_observations=["RAG 및 지식 인덱싱 라이브러리"],
        candidate_observations=["에이전트 메모리 서버 프레임워크"],
        description="동일 도메인의 경쟁 프레임워크 분리",
    ),
    ResolutionBenchmarkCase(
        id="rival-02",
        new_name="Podman",
        new_type="Tool",
        candidate_name="Docker",
        candidate_type="Tool",
        expected_merge=False,
        category="rival_tool",
        description="유사 기능의 대체 컨테이너 런타임 분리",
    ),
    ResolutionBenchmarkCase(
        id="rival-03",
        new_name="Starlette",
        new_type="Framework",
        candidate_name="FastAPI",
        candidate_type="Framework",
        expected_merge=False,
        category="rival_tool",
        description="기반 라이브러리와 파생 프레임워크 간 오병합 방지",
    ),
    ResolutionBenchmarkCase(
        id="rival-04",
        new_name="Memgraph",
        new_type="Tool",
        candidate_name="Neo4j",
        candidate_type="Tool",
        expected_merge=False,
        category="rival_tool",
        description="인메모리 그래프 DB와 Neo4j 분리",
    ),
    ResolutionBenchmarkCase(
        id="rival-05",
        new_name="Claude 3.5 Sonnet",
        new_type="Model",
        candidate_name="Claude Code",
        candidate_type="Tool",
        expected_merge=False,
        category="rival_tool",
        description="추론 모델과 CLI 에이전트 도구 간 분리",
    ),
    # 7) Version Splitting (False - FPR 방지)
    ResolutionBenchmarkCase(
        id="version-01",
        new_name="vSphere 8.0",
        new_type="Product",
        candidate_name="vSphere 7.0",
        candidate_type="Product",
        expected_merge=False,
        category="version_split",
        description="메이저 버전 간 고유 식별 분리",
    ),
    ResolutionBenchmarkCase(
        id="version-02",
        new_name="ESXi 8.0 Update 2",
        new_type="Product",
        candidate_name="ESXi",
        candidate_type="Product",
        expected_merge=False,
        category="version_split",
        description="부모 개념과 세부 업데이트 릴리즈 간 독립 노드 분할",
    ),
    # 8) Polysemy & Homonyms (False - 다의어 오병합 방지)
    ResolutionBenchmarkCase(
        id="polysemy-01",
        new_name="Python",
        new_type="Language",
        candidate_name="Python",
        candidate_type="Animal",
        expected_merge=False,
        category="polysemy",
        new_observations=["객체지향 인터프리터 프로그래밍 언어"],
        candidate_observations=["파이톤과에 속하는 비독성 대형 뱀"],
        description="동음이의어(프로그래밍 언어 vs 파충류) 분리",
    ),
    ResolutionBenchmarkCase(
        id="polysemy-02",
        new_name="Apple",
        new_type="Organization",
        candidate_name="Apple",
        candidate_type="Food",
        expected_merge=False,
        category="polysemy",
        new_observations=["쿠퍼티노에 본사를 둔 IT 기술 기업"],
        candidate_observations=["사과나무의 식용 열매"],
        description="동음이의어(기업 vs 과일) 분리",
    ),
    # 9) Distinct & Irrelevant (False)
    ResolutionBenchmarkCase(
        id="distinct-01",
        new_name="Mimalloc",
        new_type="Tool",
        candidate_name="Letta",
        candidate_type="Framework",
        expected_merge=False,
        category="distinct",
        description="전혀 무관한 메모리 할당자 라이브러리 분리",
    ),
]


# --- 2. 벤치마크 실행 및 메트릭 산출기 ---


def run_resolution_benchmark(
    judge_fn: Callable[[ResolutionBenchmarkCase], bool],
    cases: list[ResolutionBenchmarkCase] | None = None,
    engine_name: str = "custom_engine",
) -> BenchmarkMetrics:
    """주어진 판정 함수(judge_fn)를 골든 데이터셋에 적용하여 벤치마크 메트릭을 산출."""
    target_cases = cases if cases is not None else GOLDEN_RESOLUTION_CASES
    latencies: list[float] = []
    tp = tn = fp = fn = 0
    failed: list[dict[str, Any]] = []

    for case in target_cases:
        t0 = time.perf_counter()
        try:
            pred_merge = bool(judge_fn(case))
        except Exception as e:
            pred_merge = False
            failed.append({
                "case_id": case.id,
                "error": str(e),
                "expected": case.expected_merge,
                "predicted": pred_merge,
            })
        t1 = time.perf_counter()
        latency_ms = (t1 - t0) * 1000.0
        latencies.append(latency_ms)

        expected = case.expected_merge
        if expected and pred_merge:
            tp += 1
        elif not expected and not pred_merge:
            tn += 1
        elif not expected and pred_merge:
            fp += 1  # 치명적 오병합
            failed.append({
                "case_id": case.id,
                "reason": "False Positive (Fatal Over-Merge)",
                "category": case.category,
                "new": f"{case.new_name} ({case.new_type})",
                "cand": f"{case.candidate_name} ({case.candidate_type})",
            })
        elif expected and not pred_merge:
            fn += 1  # 미병합
            failed.append({
                "case_id": case.id,
                "reason": "False Negative (Under-Merge)",
                "category": case.category,
                "new": f"{case.new_name} ({case.new_type})",
                "cand": f"{case.candidate_name} ({case.candidate_type})",
            })

    total = len(target_cases)
    precision = (tp / (tp + fp)) if (tp + fp) > 0 else 0.0
    recall = (tp / (tp + fn)) if (tp + fn) > 0 else 0.0
    f1 = (2 * precision * recall / (precision + recall)) if (precision + recall) > 0 else 0.0
    fpr = (fp / (fp + tn)) if (fp + tn) > 0 else 0.0

    avg_lat = sum(latencies) / total if total > 0 else 0.0
    sorted_lat = sorted(latencies)
    p95_idx = int(0.95 * len(sorted_lat)) if sorted_lat else 0
    p95_lat = sorted_lat[min(p95_idx, len(sorted_lat) - 1)] if sorted_lat else 0.0

    return BenchmarkMetrics(
        engine_name=engine_name,
        total_cases=total,
        true_positives=tp,
        true_negatives=tn,
        false_positives=fp,
        false_negatives=fn,
        precision=round(precision, 4),
        recall=round(recall, 4),
        f1_score=round(f1, 4),
        false_positive_rate=round(fpr, 4),
        avg_latency_ms=round(avg_lat, 2),
        p95_latency_ms=round(p95_lat, 2),
        failed_cases=failed,
    )


def format_benchmark_report(metrics: BenchmarkMetrics) -> str:
    """벤치마크 메트릭을 가독성 높은 Markdown 보고서로 포맷팅."""
    fpr_status = "PASS (0.00%)" if metrics.false_positive_rate == 0.0 else f"CRITICAL FAIL ({metrics.false_positive_rate * 100:.2f}%)"
    lines = [
        f"### 해소 벤치마크 실측 보고서: {metrics.engine_name}",
        f"- **총 평가 케이스**: {metrics.total_cases}건",
        f"- **거짓 병합률 (FPR)**: **{fpr_status}** (오병합 {metrics.false_positives}건)",
        f"- **정밀도 (Precision)**: {metrics.precision * 100:.2f}%",
        f"- **재현율 (Recall)**: {metrics.recall * 100:.2f}%",
        f"- **F1-Score**: {metrics.f1_score:.4f}",
        f"- **평균 지연시간**: {metrics.avg_latency_ms:.2f} ms",
        f"- **P95 지연시간**: {metrics.p95_latency_ms:.2f} ms",
        f"- **혼동 행렬**: TP={metrics.true_positives}, TN={metrics.true_negatives}, FP={metrics.false_positives}, FN={metrics.false_negatives}",
    ]
    if metrics.failed_cases:
        lines.append("\n#### 불일치/실패 케이스 분석:")
        for fc in metrics.failed_cases:
            lines.append(f"  - [{fc.get('case_id')}] {fc.get('reason', fc.get('error'))}: {fc.get('new')} vs {fc.get('cand')}")
    return "\n".join(lines)
