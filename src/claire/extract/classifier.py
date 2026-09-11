"""문서 유형 판별기 — 무료 어댑터 우선 1차 논문 판정."""

from __future__ import annotations

import logging
import re
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from ..config import Settings
    from ..ontology.base import Document
    from .provider import Provider

from ..config import find_agy_executable, find_codex_executable, get_settings

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
        "xhigh": 4.0,
        "max": 5.0,
        "ultra": 6.0,
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
      1) 명시 선택된 Codex CLI: 바이너리 존재 시 codex_effort 기준
      2) Antigravity CLI (agy): 바이너리 존재 시 agy_effort 기준
      3) Gemini API: API 키 존재 시 gemini_effort 기준
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
    # tie-breaker 우선순위: 명시 선택된 codex(-1) > antigravity(0) > gemini(1)
    # Codex를 제외한 기존 후보의 상대 순서는 유지한다.

    # Codex는 운영 provider로 명시된 경우에만 분류 후보가 된다.
    raw_provider = str(getattr(s, "provider", "")).strip().lower()
    if raw_provider in ("codex", "codex-cli"):
        raw_codex_bin = getattr(s, "codex_bin", "codex")
        if find_codex_executable(raw_codex_bin) is not None:
            from .codex_provider import CodexProvider

            eff_str = getattr(s, "codex_effort", "medium")
            score = parse_effort_score(eff_str)
            try:
                candidates.append((score, -1, CodexProvider(s)))
            except Exception as e:
                logger.warning("Failed to initialize CodexProvider: %s", e)

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


class PaperClassificationResult(tuple):
    """(is_paper, reason) 2개 튜플과 하위 호환되면서 .author 및 .title 속성을 제공."""

    def __new__(
        cls,
        is_paper: bool,
        reason: str,
        author: str | None = None,
        title: str | None = None,
    ):
        inst = super().__new__(cls, (bool(is_paper), str(reason)))
        inst.is_paper = bool(is_paper)
        inst.reason = str(reason)
        inst.author = str(author).strip() if author else None
        inst.title = str(title).strip() if title else None
        return inst


_DTP_CREATORS = (
    "quarkxpress",
    "indesign",
    "coreldraw",
    "illustrator",
    "photoshop",
    "pagemaker",
    "acrobat distiller",
)

_GENERIC_ACCOUNT_RE = re.compile(
    r"^(?:admin(?:istrator)?|user|owner|guest|root|test|desktop|mac(?:book)?|windows|pc\d*|"
    r"designer|typesetter|editor|layout|dtp|print|조판|편집|디자이너|작성자)$",
    re.IGNORECASE,
)

_DTP_USERNAME_RE = re.compile(
    r"^[a-z]{2,5}[-_][a-z]{1,4}(?:\d{0,3})$",
    re.IGNORECASE,
)

_KOREAN_RESEARCHER_RE = re.compile(
    r"([가-힣]{2,4})\s*(선임연구위원|수석연구원|책임연구원|선임연구원|연구위원|연구원|초빙연구위원|연구조교수|교수|부교수|조교수|박사)\b"
)


def extract_author_heuristic(text: str) -> str | None:
    """한국어 연구 보고서/논문 헤더 텍스트에서 저자 직함 패턴 매칭."""
    if not text:
        return None
    head = text[:2500]
    m = _KOREAN_RESEARCHER_RE.search(head)
    if m:
        return m.group(0).strip()
    return None


def is_dtp_typesetter_artifact(author: str | None, pdf_meta: dict | None = None) -> bool:
    """PDF 메타데이터의 Author가 실제 저자가 아니라 DTP 조판/디자이너/시스템 계정인지 판별."""
    if not author or not str(author).strip():
        return False
    a = str(author).strip()
    if _GENERIC_ACCOUNT_RE.match(a):
        return True
    if _DTP_USERNAME_RE.match(a):
        return True
    if pdf_meta and isinstance(pdf_meta, dict):
        creator_str = str(pdf_meta.get("Creator") or pdf_meta.get("Producer") or "").lower()
        if any(dtp in creator_str for dtp in _DTP_CREATORS):
            # DTP 생성 도구로 만든 PDF의 저자가 영문 조판 계정 형태인 경우
            if re.match(r"^[a-zA-Z0-9_\-\.\s]{1,15}$", a) and not re.search(r"[가-힣]", a):
                return True
    return False


def is_dtp_title(title: str | None) -> bool:
    """PDF 메타데이터의 Title이 실제 문서 제목이 아니라 잡지 호수/임시 파일명인지 판별."""
    if not title or not str(title).strip():
        return True
    t = str(title).strip().lower()
    if re.match(r"^\d{1,4}[-_\s]+\d{1,4}$", t):  # e.g. '35 17', '35-17', '2024_01'
        return True
    if re.match(r"^(?:untitled|noname|document\d*|page\s*\d+|microsoft\s*word.*|quarkxpress.*)$", t):
        return True
    return False


def extract_title_heuristic(text: str) -> str | None:
    """본문 첫 페이지 텍스트에서 논문/연구 제목 휴리스틱 추출."""
    if not text:
        return None
    lines = [line.strip() for line in text[:1500].splitlines() if line.strip()]
    header_skip = ("korea institute", "financial brief", "금융브리프", "논단", "초록", "abstract", "요약", "issn", "vol.")
    candidates = []
    for line in lines:
        if len(line) < 4:
            continue
        line_lower = line.lower()
        if any(skip in line_lower for skip in header_skip):
            continue
        if re.match(r"^\d{1,4}[-_\s\.~]+\d{1,4}", line):
            continue
        candidates.append(line)
        if len(candidates) >= 2:
            break
    if candidates:
        return " ".join(candidates[:2])[:200]
    return None


def classify_paper(
    doc: Document,
    settings: Settings | None = None,
    *,
    provider: Provider | None = None,
) -> PaperClassificationResult:
    """선언된 프로바이더 중 최저 effort 프로바이더를 선택하여 학술 논문 여부 1차 판정.

    반환: PaperClassificationResult (is_paper: bool, reason: str, author: str | None, title: str | None)
    """
    from ..config import get_settings

    s = settings or get_settings()
    prov = provider or get_lowest_effort_provider(s)

    # classify_paper 메서드가 구현되어 있으면 호출
    fn = getattr(prov, "classify_paper", None)
    eff = getattr(s, "pdf_classifier_effort", "low") or "low"
    if fn is not None:
        try:
            res = fn(doc, effort=eff)
            if isinstance(res, PaperClassificationResult):
                return res
            if isinstance(res, (tuple, list)):
                author = getattr(res, "author", None)
                title = getattr(res, "title", None)
                return PaperClassificationResult(res[0], res[1], author=author, title=title)
        except Exception as e:  # noqa: BLE001
            logger.warning("classify_paper provider call failed: %s", e)

    # 폴백 휴리스틱 (URL 및 텍스트 단서)
    blob = (
        (doc.title or "") + " " + (doc.url or "") + " " + (doc.raw_text or "")[:2000]
    ).lower()
    paper_kws = (
        "arxiv.org", "nber.org", "biorxiv.org", "medrxiv.org", "openreview.net",
        "working paper", "conference on", "proceedings of", "abstract\n",
        "abstract:\n", "ieee", "acm", "journal of", "doi.org", "논문", "논단"
    )
    is_paper = any(k in blob for k in paper_kws)
    reason = "heuristic keyword match" if is_paper else "heuristic non-paper"
    author = extract_author_heuristic(doc.raw_text or "")
    title = extract_title_heuristic(doc.raw_text or "") if is_dtp_title(doc.title) else None
    return PaperClassificationResult(is_paper, reason, author=author, title=title)


def reconcile_pdf_metadata(
    doc: Document,
    settings: Settings | None = None,
    *,
    provider: Provider | None = None,
) -> PaperClassificationResult:
    """PDF 문서의 저자/제목 메타데이터를 본문 분석 및 판별기 결과와 비교·교정한다.

    1. 1차 논문 판별기(classify_paper) 및 텍스트 서지 휴리스틱으로 본문 내 실제 저자/제목 추출.
    2. 메타데이터 저자가 DTP 조판자(park-sy 등)이거나 결함인 경우 본문 실제 저자로 대체 또는 제거.
    3. 메타데이터 제목이 조판 임시 파일명('35 17' 등)인 경우 본문 실제 제목으로 교정.
    """
    if doc.meta is None:
        doc.meta = {}

    res = classify_paper(doc, settings, provider=provider)
    is_paper, reason = res[:2]
    extracted_author = getattr(res, "author", None)
    extracted_title = getattr(res, "title", None)

    # 텍스트 휴리스틱 보강
    if not extracted_author:
        extracted_author = extract_author_heuristic(doc.raw_text or "")
    if not extracted_title and is_dtp_title(doc.title):
        extracted_title = extract_title_heuristic(doc.raw_text or "")

    pdf_meta = doc.meta.get("pdf_metadata") or {}
    author_is_dtp = is_dtp_typesetter_artifact(doc.author, pdf_meta)

    # 저자 교정 절차
    if extracted_author:
        if doc.author != extracted_author:
            if doc.author:
                doc.meta["meta_author_raw"] = doc.author
            doc.author = extracted_author
            doc.meta["author_reconciled"] = True
            doc.meta["author_source"] = "body_text"
    elif author_is_dtp:
        # 본문에서 저자를 특정하지 못했으나 메타데이터 저자가 DTP 조판자 계정인 경우 오염 차단
        doc.meta["meta_author_raw"] = doc.author
        doc.author = None
        doc.meta["author_rejected"] = "dtp_typesetter_artifact"

    # 제목 교정 절차
    if is_dtp_title(doc.title) and extracted_title:
        doc.meta["meta_title_raw"] = doc.title
        doc.title = extracted_title
        doc.meta["title_reconciled"] = True

    doc.meta["paper_classification"] = {
        "is_paper": is_paper,
        "reason": reason,
        "extracted_author": extracted_author,
        "extracted_title": extracted_title,
        "raw_chars": len(doc.raw_text or ""),
    }
    return PaperClassificationResult(is_paper, reason, author=extracted_author, title=extracted_title)
