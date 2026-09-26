"""단위 테스트: 엔티티 해소 실측 벤치마크 하니스 및 가역적 롤백 페이로드 검증.

검증 항목:
1. GOLDEN_RESOLUTION_CASES 데이터 무결성 및 카테고리 분포
2. run_resolution_benchmark 정확도, 재현율, 지연시간, 오병합(FPR) 계산 정밀도
3. 오병합(False Positive) 발생 시 위험 감지 및 보고서 출력
4. ResolutionDecision 롤백 페이로드(target_entity_id, source_entity_id, rollback_payload) 직렬화/역직렬화 검증
"""

from __future__ import annotations

from claire.extract.benchmark import (
    GOLDEN_RESOLUTION_CASES,
    BenchmarkMetrics,
    ResolutionBenchmarkCase,
    format_benchmark_report,
    run_resolution_benchmark,
)
from claire.extract.decision import ResolutionDecision


def test_golden_dataset_integrity():
    assert len(GOLDEN_RESOLUTION_CASES) >= 15
    ids = [c.id for c in GOLDEN_RESOLUTION_CASES]
    assert len(ids) == len(set(ids)), "케이스 ID는 중복 없이 유일해야 합니다."

    categories = {c.category for c in GOLDEN_RESOLUTION_CASES}
    assert "exact" in categories
    assert "alias" in categories
    assert "acronym" in categories
    assert "synonym" in categories
    assert "rival_tool" in categories
    assert "version_split" in categories
    assert "polysemy" in categories

    true_cases = [c for c in GOLDEN_RESOLUTION_CASES if c.expected_merge]
    false_cases = [c for c in GOLDEN_RESOLUTION_CASES if not c.expected_merge]
    assert len(true_cases) >= 6
    assert len(false_cases) >= 8


def test_perfect_judge_benchmark_run():
    # 완벽한 판정 엔진 시뮬레이션
    def perfect_judge(case: ResolutionBenchmarkCase) -> bool:
        return case.expected_merge

    metrics = run_resolution_benchmark(perfect_judge, engine_name="Perfect_Oracle")
    assert metrics.precision == 1.0
    assert metrics.recall == 1.0
    assert metrics.f1_score == 1.0
    assert metrics.false_positive_rate == 0.0
    assert metrics.false_positives == 0
    assert metrics.false_negatives == 0
    assert len(metrics.failed_cases) == 0

    report = format_benchmark_report(metrics)
    assert "PASS (0.00%)" in report
    assert "100.00%" in report


def test_over_merging_fatal_fpr_detection():
    # 모든 후보를 무조건 병합(Over-merging)하는 위험한 엔진 시뮬레이션
    def aggressive_merge_judge(case: ResolutionBenchmarkCase) -> bool:
        return True

    metrics = run_resolution_benchmark(aggressive_merge_judge, engine_name="Aggressive_Engine")
    assert metrics.recall == 1.0
    # FPR이 0보다 큼 (치명적 오병합 탐지)
    assert metrics.false_positive_rate > 0.0
    assert metrics.false_positives > 0

    report = format_benchmark_report(metrics)
    assert "CRITICAL FAIL" in report
    assert "False Positive (Fatal Over-Merge)" in report


def test_under_merging_conservative_detection():
    # 모든 후보를 절대 병합하지 않는 엔진 시뮬레이션
    def conservative_judge(case: ResolutionBenchmarkCase) -> bool:
        return False

    metrics = run_resolution_benchmark(conservative_judge, engine_name="Conservative_Engine")
    assert metrics.recall == 0.0
    assert metrics.false_positive_rate == 0.0
    assert metrics.false_positives == 0
    assert metrics.false_negatives > 0


def test_resolution_decision_with_rollback_payload():
    decision = ResolutionDecision(
        entity="MemGPT",
        stage="exact_match",
        decision="MERGE",
        candidate="Letta",
        score=1.0,
        reason="Matched registered alias",
        target_entity_id="ent_letta_001",
        source_entity_id="ent_memgpt_temp",
        rollback_payload={
            "added_aliases": ["MemGPT"],
            "repointed_relations": ["rel_1", "rel_2"],
            "previous_observations_count": 2,
        },
    )

    d = decision.to_dict()
    assert d["target_entity_id"] == "ent_letta_001"
    assert d["source_entity_id"] == "ent_memgpt_temp"
    assert d["rollback_payload"]["added_aliases"] == ["MemGPT"]

    restored = ResolutionDecision.from_dict(d)
    assert restored.target_entity_id == "ent_letta_001"
    assert restored.source_entity_id == "ent_memgpt_temp"
    assert restored.rollback_payload == decision.rollback_payload
