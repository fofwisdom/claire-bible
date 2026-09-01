"""PDF 문서 추출기 — pypdf 기반 본문, 메타데이터(/Title), 링크 추출."""

from __future__ import annotations

import io
import re
from typing import BinaryIO

import pypdf

from ...config import get_settings

_URL_RE = re.compile(r"https?://[^\s)\]\}<>\"']+")

APPENDIX_PATTERNS = [
    # 1. Appendix / Appendices / APPENDIX / APPENDICES with optional numbering/title
    re.compile(
        r"(?:\n\s*\n|\n)(?:#{1,4}\s*|=+\s*|\*{1,2})?"
        r"(?:APPENDIX|Appendix|APPENDICES|Appendices)"
        r"(?:[^\S\n]+(?:[A-Z0-9IVX]+(?:\.[0-9]+)*|[A-Z]\b))?"
        r"(?:[^\S\n]*[:.\-–—][^\S\n]*[^\n]+|[^\S\n]+[^\n]{1,80})?"
        r"[^\S\n]*(?=\n|$)",
    ),
    # 2. Supplementary / Supplemental Material / Information
    re.compile(
        r"(?:\n\s*\n|\n)(?:#{1,4}\s*|=+\s*|\*{1,2})?"
        r"(?:Supplementary|Supplemental|SUPPLEMENTARY|SUPPLEMENTAL)[^\S\n]+"
        r"(?:Material|Materials|Information|Note|Notes|Appendix|Appendices|MATERIAL|MATERIALS|INFORMATION)"
        r"(?:[^\S\n]*[:.\-–—][^\S\n]*[^\n]+|[^\S\n]+[^\n]{1,80})?"
        r"[^\S\n]*(?=\n|$)",
    ),
    # 3. Korean 부록 patterns
    re.compile(
        r"(?:\n\s*\n|\n)(?:#{1,4}\s*|=+\s*|\*{1,2})?"
        r"(?:부[^\S\n]*록|\[부록\]|【부록】|<부록>)"
        r"(?:[^\S\n]+(?:[A-Z0-9가-힣]+(?:\.[0-9]+)*|[A-Z]\b))?"
        r"(?:[^\S\n]*[:.\-–—][^\S\n]*[^\n]+|[^\S\n]+[^\n]{1,80})?"
        r"[^\S\n]*(?=\n|$)",
    ),
]


def find_appendix_split(text: str) -> tuple[int, str] | None:
    """PDF 본문에서 부록(Appendix) 섹션 시작 위치 검출.

    반환: (split_index: int, matched_heading: str) | None
    - 인라인 문장 내 언급(False Positive)을 배제하고 단독 섹션 헤더만 매칭.
    - 본문 시작 직후(0자)나 본문이 비어있게 되는 경우는 제외.
    """
    if not text:
        return None
    matches: list[tuple[int, str]] = []
    for pat in APPENDIX_PATTERNS:
        for m in pat.finditer(text):
            # 문서 시작 지점(0자)이 아니며, 앞부분에 본문이 존재하는 경우
            if m.start() > 0:
                header = m.group(0).strip()
                matches.append((m.start(), header))
    if not matches:
        return None
    matches.sort(key=lambda x: x[0])
    return matches[0]


def slice_pdf_text(
    text: str,
    limit: int,
    *,
    strategy: str = "table-exemption",
    exclude_appendix: bool = True,
) -> tuple[str, bool, bool, int, int]:
    """PDF 텍스트 슬라이싱 및 Appendix 제외 처리.

    반환: (sliced_text, is_truncated, appendix_truncated, orig_chars, raw_chars)
    - exclude_appendix=True 시 본문에서 부록(Appendix) 섹션을 검출하여 제외.
    - 부록만 잘려나가고 본문이 온전한 경우: is_truncated=True, appendix_truncated=True.
    - 본문도 예산(limit)을 초과하여 추가 절단된 경우: is_truncated=True, appendix_truncated=False.
    - 부록이 없고 예산 내인 경우: is_truncated=False, appendix_truncated=False.
    """
    from ...extract.table_budget import slice_document_text

    if not text:
        return "", False, False, 0, 0

    orig_chars = len(text)
    if limit <= 0 and not exclude_appendix:
        return text, False, False, orig_chars, orig_chars

    # 1. Appendix 제외 검사
    if exclude_appendix:
        app_split = find_appendix_split(text)
        if app_split is not None:
            app_idx, _ = app_split
            main_text = text[:app_idx].rstrip()
            if main_text:  # 본문이 존재하는 경우에만 부록 제외
                if limit <= 0:
                    return main_text, True, True, orig_chars, len(main_text)
                sliced_text, is_main_trunc, _, _ = slice_document_text(
                    main_text, limit, strategy=strategy
                )
                is_truncated = True
                appendix_truncated = not is_main_trunc
                return sliced_text, is_truncated, appendix_truncated, orig_chars, len(sliced_text)

    # 2. 일반 예산 슬라이싱 (부록 없거나 미제외)
    sliced_text, is_truncated, _, _ = slice_document_text(
        text, limit, strategy=strategy
    )
    return sliced_text, is_truncated, False, orig_chars, len(sliced_text)


def extract_pdf_stream(
    stream: BinaryIO,
    url: str | None = None,
    fallback_title: str | None = None,
) -> tuple[str | None, str, list[str], dict[str, str], str | None, list[dict]]:
    """(title, text, links, anchors, error, images).

    PDF 스트림에서 텍스트, 메타데이터(/Title), 링크를 추출한다.
    실패 시 error 메시지와 함께 빈 결과를 반환한다.
    """
    try:
        reader = pypdf.PdfReader(stream)
        if reader.is_encrypted:
            try:
                reader.decrypt("")
            except Exception:
                return None, "", [], {}, "encrypted PDF", []

        title: str | None = None
        if reader.metadata:
            t = reader.metadata.get("/Title") or getattr(reader.metadata, "title", None)
            if t and isinstance(t, str) and t.strip():
                clean_t = t.strip().replace("\x00", "")
                if clean_t:
                    title = clean_t[:200]

        pages_text: list[str] = []
        links: list[str] = []
        anchors: dict[str, str] = {}
        seen_links: set[str] = set()

        total_len = 0
        for page in reader.pages:
            try:
                pt = page.extract_text() or ""
            except Exception:
                pt = ""
            pt = pt.strip()
            if pt:
                pages_text.append(pt)
                total_len += len(pt)

            # PDF 어노테이션 링크(URI Action) 추출
            try:
                annots = page.get("/Annots")
                if annots:
                    for a in annots:
                        obj = a.get_object() if hasattr(a, "get_object") else a
                        if isinstance(obj, dict):
                            action = obj.get("/A")
                            if isinstance(action, dict) and action.get("/S") == "/URI":
                                uri = str(action.get("/URI") or "").strip()
                                if uri.startswith(("http://", "https://")) and uri not in seen_links:
                                    seen_links.add(uri)
                                    links.append(uri)
            except Exception:
                pass

            # 대용량 DoS 방어 안전 상한 (1,000페이지 또는 1,000만 자)
            if len(pages_text) >= 1000 or total_len >= 10_000_000:
                break

        full_text = "\n\n".join(pages_text).replace("\x00", "")
        if not full_text.strip():
            return None, "", [], {}, "empty PDF content", []

        if not title:
            lines = [line.strip() for line in full_text.splitlines() if line.strip()]
            if lines:
                title = " ".join(lines[:2])[:200]
            elif fallback_title:
                title = fallback_title[:200]
            else:
                title = "PDF Document"

        # 본문 텍스트 내 URL 정규식 추출
        for m in _URL_RE.finditer(full_text):
            link = m.group(0).rstrip(".,;)")
            if link not in seen_links:
                seen_links.add(link)
                links.append(link)
                if len(links) >= 50:
                    break

        return title, full_text, links[:50], anchors, None, []
    except Exception as e:  # noqa: BLE001
        return None, "", [], {}, f"PDF extraction failed: {e}", []


def extract_pdf_bytes(
    data: bytes,
    url: str | None = None,
    fallback_title: str | None = None,
) -> tuple[str | None, str, list[str], dict[str, str], str | None, list[dict]]:
    return extract_pdf_stream(io.BytesIO(data), url=url, fallback_title=fallback_title)
