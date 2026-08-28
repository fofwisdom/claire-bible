"""문서 유형 판별기 — 무료 어댑터 우선 1차 논문 판정."""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from ..config import Settings
    from ..ontology.base import Document
    from .provider import Provider

from ..config import find_agy_executable, get_settings

logger = logging.getLogger("claire.classifier")


def parse_effort_score(effort: str | int | None) -> float:
    """effort 문자열/숫자를 비교 가능한 점수로 변환 (낮을수록 낮은 자원/추론 레벨)."""
    if effort is None:
        return 2.0  # 기본 medium
    s = str(effort).strip().lower()
    mapping = {
        "none": 0.0,
        "off": 0.0,
        "0": 0.0,
        "minimal": 0.5,
        "low": 1.0,
        "medium": 2.0,
        "high": 3.0,
    }
    if s in mapping:
        return mapping[s]
    if s.isdigit() or (s.startswith("-") and s[1:].isdigit()):
        val = int(s)
        if val <= 0:
            return 0.0
        if val <= 2048:
            return 1.0
        if val <= 8192:
            return 2.0
        return 3.0
    return 2.0


def get_lowest_effort_provider(settings: Settings | None = None) -> Provider:
    """환경변수(.env/Settings)에 선언된 여러 프로바이더 중 effort 레벨이 가장 낮은 프로바이더 반환.

    - mock/test 인 경우: MockProvider() 즉시 반환
    - 사용 가능한 후보 프로바이더 목록 수집:
      1) Antigravity CLI (agy): 바이너리 존재 시 agy_effort 기준
      2) Gemini API: API 키 존재 시 gemini_effort 기준
    - 후보 중 effort score가 가장 낮은 프로바이더 선택.
      동점인 경우 무료/로컬 CLI 어댑터인 Antigravity를 우선.
    - 선언된 후보가 없으면 기본 get_provider(settings) 반환.
    """
    from .antigravity_provider import AntigravityProvider
    from .gemini_provider import GeminiProvider
    from .provider import MockProvider, get_provider

    s = settings or get_settings()
    if getattr(s, "provider", "") in ("mock", "test"):
        return MockProvider()

    candidates: list[tuple[float, int, Provider]] = []
    # tie-breaker 우선순위: antigravity(0) > gemini(1)

    # 1) Antigravity 후보 검사
    raw_bin = getattr(s, "agy_bin", "agy")
    if find_agy_executable(raw_bin) is not None or getattr(s, "provider", "") in ("antigravity", "agy"):
        eff_str = getattr(s, "agy_effort", "medium")
        score = parse_effort_score(eff_str)
        try:
            candidates.append((score, 0, AntigravityProvider(s)))
        except Exception as e:
            logger.warning("Failed to initialize AntigravityProvider: %s", e)

    # 2) Gemini 후보 검사
    if getattr(s, "gemini_api_key", None) or getattr(s, "provider", "") == "gemini":
        eff_str = getattr(s, "gemini_effort", "medium")
        score = parse_effort_score(eff_str)
        try:
            candidates.append((score, 1, GeminiProvider(s)))
        except Exception as e:
            logger.warning("Failed to initialize GeminiProvider: %s", e)

    if candidates:
        # score 오름차순, 동점 시 tie_breaker 오름차순 (antigravity 우선)
        candidates.sort(key=lambda x: (x[0], x[1]))
        return candidates[0][2]

    return get_provider(s)


get_free_or_default_provider = get_lowest_effort_provider


def classify_paper(
    doc: Document,
    settings: Settings | None = None,
    *,
    provider: Provider | None = None,
) -> tuple[bool, str]:
    """선언된 프로바이더 중 최저 effort 프로바이더를 선택하여 학술 논문 여부 1차 판정.

    반환: (is_paper: bool, reason: str)
    """
    from ..config import get_settings

    s = settings or get_settings()
    prov = provider or get_lowest_effort_provider(s)

    # classify_paper 메서드가 구현되어 있으면 호출
    fn = getattr(prov, "classify_paper", None)
    eff = getattr(s, "pdf_classifier_effort", "low") or "low"
    if fn is not None:
        try:
            return fn(doc, effort=eff)
        except Exception as e:  # noqa: BLE001
            logger.warning("classify_paper provider call failed: %s", e)

    # 폴백 휴리스틱 (URL 및 텍스트 단서)
    blob = (
        (doc.title or "") + " " + (doc.url or "") + " " + (doc.raw_text or "")[:2000]
    ).lower()
    paper_kws = (
        "arxiv.org", "nber.org", "biorxiv.org", "medrxiv.org", "openreview.net",
        "working paper", "conference on", "proceedings of", "abstract\n",
        "abstract:\n", "ieee", "acm", "journal of", "doi.org"
    )
    is_paper = any(k in blob for k in paper_kws)
    reason = "heuristic keyword match" if is_paper else "heuristic non-paper"
    return is_paper, reason
