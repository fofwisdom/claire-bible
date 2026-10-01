#!/usr/bin/env python3
"""TypeSafe AI Jev (System 1 Engine) 실측 벤치마크 및 검증 스크립트.

설계 문서 레퍼런스:
  - docs/origin/design/DECISION_STREAM_AND_HEATMAP_MATRIX_DESIGN.md (§6.4 ~ §6.5)
  - docs/origin/design/MULTI_PROVIDER_DESIGN.md (§6)

동작:
  1. 프로덕션 환경의 Jev API Key 및 Base URL 자동 탐색 (.env, providers.json, 환경변수)
  2. 엔드포인트 연결성 및 프로토콜 자동 감지 (/v1/systemone vs /v1/classify)
  3. 정본 골든 데이터셋(14개 케이스) 전수 실측 평가
  4. Go / No-Go 판정 게이트 (FPR 0.00%, Precision >= 98.0%, P95 Latency) 산출
  5. JSON 결과 보고서 파일(jev_benchmark_report.json) 저장 및 요약 출력
"""

from __future__ import annotations

import json
import os
import re
import sys
import time
import urllib.error
import urllib.request
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any


# --- 1. 정본 골든 데이터셋 (Golden Dataset) ---

@dataclass
class ResolutionBenchmarkCase:
    id: str
    new_name: str
    new_type: str
    candidate_name: str
    candidate_type: str
    expected_merge: bool
    category: str
    description: str = ""
    new_aliases: list[str] = field(default_factory=list)
    new_observations: list[str] = field(default_factory=list)
    candidate_aliases: list[str] = field(default_factory=list)
    candidate_observations: list[str] = field(default_factory=list)


GOLDEN_CASES: list[ResolutionBenchmarkCase] = [
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
    # 5) Acronym Type Mis-matches (False)
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


# --- 2. 환경설정 탐색 헬퍼 ---

def discover_jev_config() -> dict[str, Any]:
    api_key = os.environ.get("CLAIRE_JEV_API_KEY", "").strip()
    base_url = os.environ.get("CLAIRE_JEV_BASE_URL", "https://api.typesafe.ai/v1").strip()
    timeout = float(os.environ.get("CLAIRE_JEV_TIMEOUT", "15.0"))
    key_source = "environment" if api_key else ""

    # 1) providers.json 탐색
    if not api_key:
        for p in [Path("data/providers.json"), Path("/app/data/providers.json"), Path("../data/providers.json")]:
            if p.is_file():
                try:
                    data = json.loads(p.read_text(encoding="utf-8"))
                    jev = data.get("providers", {}).get("jev", {})
                    k = str(jev.get("api_key") or "").strip()
                    if k and k != "••••••••":
                        api_key = k
                        base_url = str(jev.get("base_url") or base_url).strip()
                        timeout = float(jev.get("timeout") or timeout)
                        key_source = str(p)
                        break
                except Exception:
                    pass

    # 2) .env 파일 탐색
    if not api_key:
        for env_path in [Path(".env"), Path("/app/.env"), Path(".env.dev"), Path("../.env")]:
            if env_path.is_file():
                try:
                    for line in env_path.read_text(encoding="utf-8").splitlines():
                        line = line.strip()
                        if line.startswith("#") or "=" not in line:
                            continue
                        k, v = line.split("=", 1)
                        k = k.strip()
                        v = v.strip().strip("'\"")
                        if k == "CLAIRE_JEV_API_KEY" and v:
                            api_key = v
                            key_source = str(env_path)
                        elif k == "CLAIRE_JEV_BASE_URL" and v:
                            base_url = v
                        elif k == "CLAIRE_JEV_TIMEOUT" and v:
                            timeout = float(v)
                    if api_key:
                        break
                except Exception:
                    pass

    return {
        "api_key": api_key,
        "base_url": base_url.rstrip("/"),
        "timeout": timeout,
        "key_source": key_source,
    }


def mask_token(token: str) -> str:
    if not token:
        return "<EMPTY>"
    if len(token) <= 8:
        return "***"
    return f"{token[:4]}...{token[-4:]}"


# --- 3. HTTP 통신 및 Jev 호출기 ---

def http_post_json(url: str, headers: dict[str, str], payload: dict[str, Any], timeout: float) -> tuple[int, dict[str, Any], float]:
    body_bytes = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(url, data=body_bytes, headers=headers, method="POST")
    t0 = time.perf_counter()
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            elapsed_ms = (time.perf_counter() - t0) * 1000.0
            data = json.loads(resp.read().decode("utf-8"))
            return resp.status, data, elapsed_ms
    except urllib.error.HTTPError as e:
        elapsed_ms = (time.perf_counter() - t0) * 1000.0
        try:
            err_body = json.loads(e.read().decode("utf-8"))
        except Exception:
            err_body = {"raw": e.reason}
        return e.code, err_body, elapsed_ms
    except Exception as e:
        elapsed_ms = (time.perf_counter() - t0) * 1000.0
        return 0, {"error": str(e)}, elapsed_ms


def probe_jev_protocol(base_url: str, api_key: str, timeout: float) -> dict[str, Any]:
    """Jev 엔드포인트 규격 자동 진단 (/v1/systemone vs /v1/classify)."""
    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
        "User-Agent": "claire-jev-verifier/1.0",
    }

    # 1) TypeSafe AI System One 공식 규격 검사
    sysone_url = f"{base_url}/systemone"
    sysone_payload = {
        "model": "jev-latest",
        "state": "Probe test: Checking connectivity and latency of TypeSafe AI Jev System One.",
        "questions": {
            "is_alive": {
                "type": "noul",
                "instructions": "Is this a valid probe request?"
            }
        }
    }
    s_code, s_resp, s_lat = http_post_json(sysone_url, headers, sysone_payload, timeout)

    if s_code == 200 and isinstance(s_resp, dict) and "is_alive" in s_resp:
        return {
            "mode": "systemone",
            "url": sysone_url,
            "latency_ms": round(s_lat, 2),
            "sample_response": s_resp,
            "status_code": 200,
        }

    # 2) 설계 문서 §6.5 간이 규격(/classify) 검사
    classify_url = f"{base_url}/classify"
    classify_payload = {"source": "Postgres", "target": "PostgreSQL"}
    c_code, c_resp, c_lat = http_post_json(classify_url, headers, classify_payload, timeout)

    if c_code == 200 and isinstance(c_resp, dict) and "probability" in c_resp:
        return {
            "mode": "classify",
            "url": classify_url,
            "latency_ms": round(c_lat, 2),
            "sample_response": c_resp,
            "status_code": 200,
        }

    return {
        "mode": "unknown",
        "url": base_url,
        "systemone_result": {"status": s_code, "resp": s_resp, "lat_ms": round(s_lat, 2)},
        "classify_result": {"status": c_code, "resp": c_resp, "lat_ms": round(c_lat, 2)},
        "status_code": s_code or c_code or 0,
    }


def call_jev_case(
    mode: str,
    url: str,
    api_key: str,
    timeout: float,
    case: ResolutionBenchmarkCase,
) -> tuple[float, float, dict[str, Any]]:
    """단일 케이스에 대해 Jev API를 호출하여 (확률, 지연시간_ms, 원시응답) 반환."""
    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
        "User-Agent": "claire-jev-verifier/1.0",
    }

    if mode == "systemone":
        state_parts = [
            f"Entity A: '{case.new_name}' (Type: {case.new_type})",
            f"Entity B: '{case.candidate_name}' (Type: {case.candidate_type})"
        ]
        if case.new_aliases:
            state_parts.append(f"Entity A Aliases: {', '.join(case.new_aliases)}")
        if case.candidate_aliases:
            state_parts.append(f"Entity B Aliases: {', '.join(case.candidate_aliases)}")
        if case.new_observations:
            state_parts.append(f"Entity A Context: {' '.join(case.new_observations)}")
        if case.candidate_observations:
            state_parts.append(f"Entity B Context: {' '.join(case.candidate_observations)}")

        payload = {
            "model": "jev-latest",
            "state": " | ".join(state_parts),
            "questions": {
                "should_merge": {
                    "type": "noul",
                    "instructions": (
                        "Are Entity A and Entity B referring to the exact same software tool, concept, or library "
                        "that should be unified/merged into a single entity in a knowledge graph? "
                        "Version differences or rival products must NOT be merged."
                    )
                }
            }
        }
        code, resp, lat = http_post_json(url, headers, payload, timeout)
        prob = 0.0
        if code == 200 and isinstance(resp, dict):
            q_res = resp.get("should_merge", {})
            if isinstance(q_res, dict):
                prob = float(q_res.get("noul", 0.0))
            elif isinstance(q_res, (int, float)):
                prob = float(q_res)
        return prob, lat, {"status": code, "response": resp}

    else:  # classify mode
        payload = {
            "source": case.new_name,
            "target": case.candidate_name,
            "source_type": case.new_type,
            "target_type": case.candidate_type,
        }
        code, resp, lat = http_post_json(url, headers, payload, timeout)
        prob = 0.0
        if code == 200 and isinstance(resp, dict):
            prob = float(resp.get("probability", 0.0))
        return prob, lat, {"status": code, "response": resp}


# --- 4. 메인 벤치마크 루프 및 레포트 산출 ---

def main() -> int:
    print("=" * 72)
    print("      TypeSafe AI Jev (System 1 Engine) 실측 벤치마크 및 검증")
    print("=" * 72)

    cfg = discover_jev_config()
    api_key = cfg["api_key"]
    base_url = cfg["base_url"]
    timeout = cfg["timeout"]

    print(f"• Base URL      : {base_url}")
    print(f"• API Key       : {mask_token(api_key)} (출처: {cfg['key_source'] or '미발견'})")
    print(f"• Timeout       : {timeout}s")
    print(f"• 평가 케이스   : 정본 골든 데이터셋 {len(GOLDEN_CASES)}건")
    print("-" * 72)

    if not api_key:
        print("\n[오류] Jev API Key(CLAIRE_JEV_API_KEY 또는 providers.json)를 찾을 수 없습니다.")
        print("실행 시 다음 중 하나를 확인하세요:")
        print("  1) export CLAIRE_JEV_API_KEY='your-key'")
        print("  2) data/providers.json 내 jev.api_key 확인")
        print("  3) .env 파일 내 CLAIRE_JEV_API_KEY 확인")
        return 1

    print("\n[1단계] Jev API 엔드포인트 연결성 및 프로토콜 진단 중...")
    probe = probe_jev_protocol(base_url, api_key, timeout)
    mode = probe["mode"]

    if mode == "unknown":
        print("❌ [연결 실패] Jev 엔드포인트 호출에 실패했습니다.")
        print(f"진단 결과:\n{json.dumps(probe, indent=2, ensure_ascii=False)}")
        return 2

    print(f"✅ [연결 성공] 프로토콜 감지: '{mode}' (RTT: {probe.get('latency_ms')}ms)")
    active_url = probe["url"]

    print("\n[2단계] 골든 데이터셋 실측 수행 중 (N=14)...")
    print("-" * 72)
    print(f"{'ID':<18} | {'Category':<14} | {'Expected':<8} | {'Jev Prob':<9} | {'Pred':<6} | {'RTT (ms)':<8} | {'결과'}")
    print("-" * 72)

    results: list[dict[str, Any]] = []
    latencies: list[float] = []
    tp = tn = fp = fn = 0
    threshold = 0.93  # sim_tier_auto_merge

    for case in GOLDEN_CASES:
        prob, lat, raw_resp = call_jev_case(mode, active_url, api_key, timeout, case)
        latencies.append(lat)
        pred_merge = (prob >= threshold)

        is_correct = (pred_merge == case.expected_merge)
        if pred_merge and case.expected_merge:
            tp += 1
            verdict = "✅ TP"
        elif not pred_merge and not case.expected_merge:
            tn += 1
            verdict = "✅ TN"
        elif pred_merge and not case.expected_merge:
            fp += 1
            verdict = "🚨 FP (오병합)"
        else:
            fn += 1
            verdict = "⚠️ FN (미병합)"

        print(
            f"{case.id:<18} | {case.category:<14} | "
            f"{str(case.expected_merge):<8} | {prob:<9.4f} | {str(pred_merge):<6} | "
            f"{lat:<8.1f} | {verdict}"
        )

        results.append({
            "case_id": case.id,
            "category": case.category,
            "new_name": case.new_name,
            "candidate_name": case.candidate_name,
            "expected_merge": case.expected_merge,
            "probability": prob,
            "predicted_merge": pred_merge,
            "latency_ms": round(lat, 2),
            "verdict": verdict,
            "raw_response": raw_resp,
        })

    # 지표 집계
    total = len(GOLDEN_CASES)
    precision = (tp / (tp + fp)) if (tp + fp) > 0 else 0.0
    recall = (tp / (tp + fn)) if (tp + fn) > 0 else 0.0
    f1 = (2 * precision * recall / (precision + recall)) if (precision + recall) > 0 else 0.0
    fpr = (fp / (fp + tn)) if (fp + tn) > 0 else 0.0

    latencies_sorted = sorted(latencies)
    avg_lat = sum(latencies) / len(latencies) if latencies else 0.0
    p50_lat = latencies_sorted[int(len(latencies_sorted) * 0.50)] if latencies_sorted else 0.0
    p95_lat = latencies_sorted[int(len(latencies_sorted) * 0.95)] if latencies_sorted else 0.0

    print("-" * 72)
    print("\n[3단계] 정량 측정 집계 및 Go / No-Go 판정 게이트")
    print("=" * 72)
    print(f"• 총 평가 건수          : {total}건 (양성 {tp+fn}건 / 음성 {tn+fp}건)")
    print(f"• TP (올바른 병합)      : {tp}건")
    print(f"• TN (올바른 분리)      : {tn}건")
    print(f"• FP (치명적 오병합)    : {fp}건  <- [핵심 무결성 지표]")
    print(f"• FN (보수적 미병합)    : {fn}건")
    print("-" * 72)
    print(f"• FPR (거짓 병합률)     : {fpr * 100:.2f}%  (목표: 0.00% Zero-Tolerance)")
    print(f"• Precision (정밀도)    : {precision * 100:.2f}%  (목표: >= 98.0%)")
    print(f"• Recall (재현율)       : {recall * 100:.2f}%  (목표: >= 88.0%)")
    print(f"• F1-Score              : {f1 * 100:.2f}%")
    print(f"• 지연시간 Avg / P50 / P95: {avg_lat:.1f}ms / {p50_lat:.1f}ms / {p95_lat:.1f}ms")
    print("=" * 72)

    gate_fpr_pass = (fp == 0)
    gate_prec_pass = (precision >= 0.98)
    gate_lat_pass = (p95_lat < 500.0)  # 클라우드 API 허용 한계

    print("\n[프로덕션 투입 기준 (Go / No-Go Gate) 검증]")
    print(f"1. 거짓 병합 0건 (FPR == 0.00%)     : {'[PASS] ✅' if gate_fpr_pass else '[FAIL] ❌ (오병합 발생 - 치명적)'}")
    print(f"2. 정밀도 기준 (Precision >= 98.0%): {'[PASS] ✅' if gate_prec_pass else '[FAIL] ❌'}")
    print(f"3. 지연시간 기준 (P95 < 500ms)      : {'[PASS] ✅' if gate_lat_pass else '[FAIL] ❌'}")

    verdict_summary = "GO" if (gate_fpr_pass and gate_prec_pass) else "NO_GO_RESTRICTED"
    if verdict_summary == "GO":
        recommendation = "TypeSafe AI Jev 엔진을 단독 엔티티 해소 판정기로 프로덕션에 안전하게 투입할 수 있습니다."
    else:
        recommendation = (
            "TypeSafe AI Jev는 'Heatmap Matrix 시각화 공급자' 및 '1차 후보 여과 필터'로만 역할을 제한해야 하며, "
            "최종 엔티티 병합 권한은 Multi-Tier Safe Gating(규칙+System 2)에 위임해야 합니다."
        )

    print(f"\n최종 판정: {verdict_summary}")
    print(f"권고사항: {recommendation}\n")

    # 결과 보고서 저장
    report_data = {
        "timestamp": time.time(),
        "created_at": time.strftime("%Y-%m-%d %H:%M:%S UTC", time.gmtime()),
        "base_url": base_url,
        "mode": mode,
        "threshold": threshold,
        "metrics": {
            "total_cases": total,
            "true_positives": tp,
            "true_negatives": tn,
            "false_positives": fp,
            "false_negatives": fn,
            "false_positive_rate": round(fpr, 4),
            "precision": round(precision, 4),
            "recall": round(recall, 4),
            "f1_score": round(f1, 4),
            "avg_latency_ms": round(avg_lat, 2),
            "p50_latency_ms": round(p50_lat, 2),
            "p95_latency_ms": round(p95_lat, 2),
        },
        "gates": {
            "gate_fpr_zero": gate_fpr_pass,
            "gate_precision_98": gate_prec_pass,
            "gate_latency_acceptable": gate_lat_pass,
            "verdict": verdict_summary,
            "recommendation": recommendation,
        },
        "probe": probe,
        "cases": results,
    }

    out_file = Path("jev_benchmark_report.json")
    out_file.write_text(json.dumps(report_data, indent=2, ensure_ascii=False), encoding="utf-8")
    print(f"📄 상세 JSON 보고서가 저장되었습니다: {out_file.resolve()}")
    print("=" * 72)

    return 0


if __name__ == "__main__":
    sys.exit(main())
