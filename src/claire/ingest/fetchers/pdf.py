"""PDF 문서 추출기 — 선택형 파서(pypdf / docling), 부록 및 참고문헌 제외, 서지 메타데이터 추출."""

from __future__ import annotations

import io
import logging
import re
from typing import Any, BinaryIO

import pypdf

from ...config import get_settings

logger = logging.getLogger("claire.ingest.pdf")

_URL_RE = re.compile(r"https?://[^\s)\]\}<>\"']+")

APPENDIX_PATTERNS = [
    # 1. Appendix / Appendices / APPENDIX / APPENDICES with optional numbering/title
    re.compile(
        r"(?:\n\s*\n|\n)(?:#{1,4}\s*|=+\s*|\*{1,2})?"
        r"(?:APPENDIX|Appendix|APPENDICES|Appendices)"
        r"(?:[^\S\n]+(?:[A-Z0-9IVX]+(?:\.[0-9]+)*|[A-Z]\b))?"
        r"(?:[^\S\n]*[:.\-–—][^\S\n]*[^\n]*|[^\S\n]+[^\n]{1,80})?"
        r"[^\S\n]*(?=\n|$)",
    ),
    # 2. Supplementary / Supplemental Material / Information
    re.compile(
        r"(?:\n\s*\n|\n)(?:#{1,4}\s*|=+\s*|\*{1,2})?"
        r"(?:Supplementary|Supplemental|SUPPLEMENTARY|SUPPLEMENTAL)[^\S\n]+"
        r"(?:Material|Materials|Information|Note|Notes|Appendix|Appendices|MATERIAL|MATERIALS|INFORMATION)"
        r"(?:[^\S\n]*[:.\-–—][^\S\n]*[^\n]*|[^\S\n]+[^\n]{1,80})?"
        r"[^\S\n]*(?=\n|$)",
    ),
    # 3. Korean 부록 patterns
    re.compile(
        r"(?:\n\s*\n|\n)(?:#{1,4}\s*|=+\s*|\*{1,2})?"
        r"(?:부[^\S\n]*록|\[부록\]|【부록】|<부록>)"
        r"(?:[^\S\n]+(?:[A-Z0-9가-힣]+(?:\.[0-9]+)*|[A-Z]\b))?"
        r"(?:[^\S\n]*[:.\-–—][^\S\n]*[^\n]*|[^\S\n]+[^\n]{1,80})?"
        r"[^\S\n]*(?=\n|$)",
    ),
]

REFERENCES_PATTERNS = [
    # 1. English References / Bibliography / Works Cited / Literature Cited
    re.compile(
        r"(?:\n\s*\n|\n)(?:#{1,4}\s*|=+\s*|\*{1,2})?"
        r"(?:REFERENCES|References|BIBLIOGRAPHY|Bibliography|WORKS CITED|Works Cited|LITERATURE CITED|Literature Cited)"
        r"(?:[^\S\n]+(?:[A-Z0-9IVX]+(?:\.[0-9]+)*|[A-Z]\b))?"
        r"(?:[^\S\n]*[:.\-–—][^\S\n]*[^\n]*|[^\S\n]+[^\n]{1,80})?"
        r"[^\S\n]*(?=\n|$)",
    ),
    # 2. Korean 참고문헌 patterns
    re.compile(
        r"(?:\n\s*\n|\n)(?:#{1,4}\s*|=+\s*|\*{1,2})?"
        r"(?:참[^\S\n]*고[^\S\n]*문[^\S\n]*헌|\[참고문헌\]|【참고문헌】|<참고문헌>)"
        r"(?:[^\S\n]+(?:[A-Z0-9가-힣]+(?:\.[0-9]+)*|[A-Z]\b))?"
        r"(?:[^\S\n]*[:.\-–—][^\S\n]*[^\n]*|[^\S\n]+[^\n]{1,80})?"
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
            if m.start() > 0:
                header = m.group(0).strip()
                matches.append((m.start(), header))
    if not matches:
        return None
    matches.sort(key=lambda x: x[0])
    return matches[0]


def find_references_split(text: str) -> tuple[int, str] | None:
    """PDF 본문에서 참고문헌(References/Bibliography) 섹션 시작 위치 검출.

    반환: (split_index: int, matched_heading: str) | None
    - 인라인 문장 내 언급(False Positive)을 배제하고 단독 섹션 헤더만 매칭.
    - 본문 시작 직후(0자)나 본문이 비어있게 되는 경우는 제외.
    """
    if not text:
        return None
    matches: list[tuple[int, str]] = []
    for pat in REFERENCES_PATTERNS:
        for m in pat.finditer(text):
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
    exclude_references: bool = True,
) -> tuple[str, bool, bool, bool, int, int]:
    """PDF 텍스트 슬라이싱, 부록(Appendix) 및 참고문헌(References) 제외 처리.

    반환: (sliced_text, is_truncated, appendix_truncated, references_truncated, orig_chars, raw_chars)
    - exclude_appendix=True 시 부록 섹션 검출 제외
    - exclude_references=True 시 참고문헌 섹션 검출 제외
    - 부록/참고문헌 중 본문 뒤에서 더 일찍 시작하는 지점을 기준으로 본문 분리
    """
    from ...extract.table_budget import slice_document_text

    if not text:
        return "", False, False, False, 0, 0

    orig_chars = len(text)
    if limit <= 0 and not exclude_appendix and not exclude_references:
        return text, False, False, False, orig_chars, orig_chars

    # 1. 부록 및 참고문헌 분리 지점 탐색
    splits: list[tuple[int, str, str]] = []  # (index, header, type)
    if exclude_appendix:
        app = find_appendix_split(text)
        if app is not None:
            splits.append((app[0], app[1], "appendix"))
    if exclude_references:
        ref = find_references_split(text)
        if ref is not None:
            splits.append((ref[0], ref[1], "references"))

    if splits:
        splits.sort(key=lambda x: x[0])
        first_cut_idx = splits[0][0]
        main_text = text[:first_cut_idx].rstrip()
        if main_text:
            types = {item[2] for item in splits}
            has_app = "appendix" in types
            has_ref = "references" in types

            if limit <= 0:
                return main_text, True, has_app, has_ref, orig_chars, len(main_text)

            sliced_text, is_main_trunc, _, _ = slice_document_text(
                main_text, limit, strategy=strategy
            )
            is_truncated = True
            appendix_truncated = has_app if not is_main_trunc else False
            references_truncated = has_ref if not is_main_trunc else False
            return sliced_text, is_truncated, appendix_truncated, references_truncated, orig_chars, len(sliced_text)

    # 2. 일반 예산 슬라이싱 (제외 섹션 없거나 미제외)
    sliced_text, is_truncated, _, _ = slice_document_text(
        text, limit, strategy=strategy
    )
    return sliced_text, is_truncated, False, False, orig_chars, len(sliced_text)


def extract_bibliographic_metadata(
    text: str,
    metadata: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """PDF 메타데이터 및 본문 텍스트로부터 서지 메타데이터(저자, 발행일, DOI, arXiv) 추출."""
    biblio: dict[str, Any] = {}
    author: str | None = None
    published_at: str | None = None

    if metadata:
        # 1. Author
        raw_author = metadata.get("/Author") or getattr(metadata, "author", None)
        if raw_author and isinstance(raw_author, str) and raw_author.strip():
            clean_author = raw_author.strip().replace("\x00", "")
            if clean_author:
                author = clean_author[:200]

        # 2. Date
        raw_date = metadata.get("/CreationDate") or metadata.get("/ModDate")
        if raw_date and isinstance(raw_date, str):
            m = re.search(r"(\d{4})[-/.]?(\d{2})[-/.]?(\d{2})", raw_date)
            if m:
                published_at = f"{m.group(1)}-{m.group(2)}-{m.group(3)}"

    # 3. DOI pattern (head sample)
    head_sample = (text or "")[:10000]
    doi_match = re.search(
        r"(?:doi(?:\.org)?[:/\s]*|https?://(?:dx\.)?doi\.org/)(10\.\d{4,9}/[-._;()/:A-Za-z0-9]+)",
        head_sample,
        re.IGNORECASE,
    )
    if doi_match:
        doi = doi_match.group(1).rstrip(".,;)")
        biblio["doi"] = doi

    # 4. arXiv ID pattern
    arxiv_match = re.search(
        r"(?:arXiv[:\s]*|https?://arxiv\.org/abs/)(\d{4}\.\d{4,5}(?:v\d+)?)",
        head_sample,
        re.IGNORECASE,
    )
    if arxiv_match:
        arxiv_id = arxiv_match.group(1)
        biblio["arxiv_id"] = arxiv_id
        if not published_at:
            yy = arxiv_id[:2]
            mm = arxiv_id[2:4]
            published_at = f"20{yy}-{mm}"

    if author:
        biblio["author"] = author
    if published_at:
        biblio["published_at"] = published_at

    return biblio


class PdfExtractResult(tuple):
    """6개 튜플(title, text, links, anchors, error, images)과 완벽 호환되면서 .biblio 및 파서 실행 이력 속성을 제공."""

    def __new__(
        cls,
        title: str | None,
        text: str,
        links: list[str],
        anchors: dict[str, str],
        error: str | None,
        images: list[dict],
        biblio: dict[str, Any] | None = None,
        parser_requested: str = "pypdf",
        parser_used: str = "pypdf",
        parser_fallback: bool = False,
        parser_fallback_reason: str | None = None,
    ):
        instance = super().__new__(cls, (title, text, links, anchors, error, images))
        instance.biblio = biblio or {}
        instance.parser_requested = parser_requested
        instance.parser_used = parser_used
        instance.parser_fallback = parser_fallback
        instance.parser_fallback_reason = parser_fallback_reason
        return instance

    @property
    def title(self) -> str | None:
        return self[0]

    @property
    def text(self) -> str:
        return self[1]

    @property
    def links(self) -> list[str]:
        return self[2]

    @property
    def anchors(self) -> dict[str, str]:
        return self[3]

    @property
    def error(self) -> str | None:
        return self[4]

    @property
    def images(self) -> list[dict]:
        return self[5]


def classify_docling_failure(exc: Exception) -> str:
    """컨테이너 및 서버 환경에서 Docling 실패 원인을 정밀 진단하여 인지 가능한 사유로 정제."""
    err_str = str(exc)
    err_type = type(exc).__name__

    # 1. 컨테이너 메모리 부족 (OOM)
    if isinstance(exc, MemoryError) or "outofmemory" in err_str.lower() or "cuda out of memory" in err_str.lower():
        return "컨테이너 메모리(RAM) 부족 (OOM)"

    # 2. CPU 추론 시간 초과
    if isinstance(exc, TimeoutError) or ("timeout" in err_type.lower() and "connect" not in err_type.lower()):
        return "CPU 추론 시간 초과 (Timeout)"

    # 3. AI 모델 가중치(Weights) 다운로드 및 외부 네트워크 장애
    if (
        any(k in err_str.lower() for k in ("hfhub", "huggingface", "connectionerror", "connecttimeout", "getaddrinfo failed", "max retries exceeded"))
        or "Connection" in err_type
        or "ConnectTimeout" in err_type
    ):
        return "AI 모델 가중치(Weights) 다운로드 실패 (외부 네트워크 차단 또는 HuggingFace 연결 불가)"

    # 4. 라이브러리 또는 C++ 의존성 누락
    if isinstance(exc, (ImportError, ModuleNotFoundError)):
        return f"Docling 또는 C++ 런타임 라이브러리 누락 ({exc})"

    # 5. 기타 런타임 변환 오류
    clean_msg = err_str.strip().replace("\n", " ")[:150]
    return f"문서 변환 중 런타임 오류 ({err_type}: {clean_msg})"


def extract_pdf_stream_pypdf(
    stream: BinaryIO,
    url: str | None = None,
    fallback_title: str | None = None,
) -> PdfExtractResult:
    """pypdf 기반 경량 PDF 텍스트 및 메타데이터 추출."""
    try:
        reader = pypdf.PdfReader(stream)
        if reader.is_encrypted:
            try:
                reader.decrypt("")
            except Exception:
                return PdfExtractResult(None, "", [], {}, "encrypted PDF", [], {})

        title: str | None = None
        raw_meta = dict(reader.metadata) if reader.metadata else {}
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
            return PdfExtractResult(None, "", [], {}, "empty PDF content", [], {})

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

        biblio = extract_bibliographic_metadata(full_text, raw_meta)
        return PdfExtractResult(title, full_text, links[:50], anchors, None, [], biblio)
    except Exception as e:  # noqa: BLE001
        return PdfExtractResult(None, "", [], {}, f"PDF extraction failed: {e}", [], {})


def extract_pdf_stream_docling(
    stream: BinaryIO,
    url: str | None = None,
    fallback_title: str | None = None,
) -> PdfExtractResult:
    """docling 기반 레이아웃 분석 및 마크다운 텍스트 추출 (선택형 고성능 파서)."""
    try:
        from docling.datamodel.base_models import DocumentStream
        from docling.document_converter import DocumentConverter
    except ImportError as e:
        raise ImportError(f"docling is not installed. Run: pip install 'claire[docling]' ({e})") from e

    doc_stream = DocumentStream(name=fallback_title or "document.pdf", stream=stream)
    converter = DocumentConverter()
    result = converter.convert(doc_stream)
    doc = result.document

    full_text = doc.export_to_markdown()
    if not full_text.strip():
        return PdfExtractResult(None, "", [], {}, "empty docling PDF content", [], {})

    title = getattr(doc, "name", None)
    if not title or title.endswith(".pdf"):
        lines = [line.strip() for line in full_text.splitlines() if line.strip()]
        if lines:
            title = lines[0].lstrip("#").strip()[:200]
        else:
            title = fallback_title[:200] if fallback_title else "PDF Document"

    links: list[str] = []
    seen_links: set[str] = set()
    for m in _URL_RE.finditer(full_text):
        link = m.group(0).rstrip(".,;)")
        if link not in seen_links:
            seen_links.add(link)
            links.append(link)
            if len(links) >= 50:
                break

    biblio = extract_bibliographic_metadata(full_text)
    return PdfExtractResult(title, full_text, links[:50], {}, None, [], biblio)


def extract_pdf_stream(
    stream: BinaryIO,
    url: str | None = None,
    fallback_title: str | None = None,
    engine: str | None = None,
) -> PdfExtractResult:
    """(title, text, links, anchors, error, images) 6개 튜플 호환 객체(biblio 속성 포함).

    선택된 엔진(pypdf 또는 docling)으로 PDF를 추출한다.
    docling 실패 또는 미설치 시 정밀한 원인을 진단/기록하고 pypdf로 안전하게 자동 폴백한다.
    """
    settings = get_settings()
    selected_engine = (engine or getattr(settings, "pdf_parser", "pypdf") or "pypdf").lower().strip()

    if selected_engine == "docling":
        try:
            res = extract_pdf_stream_docling(stream, url=url, fallback_title=fallback_title)
            res.parser_requested = "docling"
            res.parser_used = "docling"
            res.parser_fallback = False
            return res
        except Exception as e:
            reason = classify_docling_failure(e)
            logger.warning(
                "Docling PDF extraction failed: %s (%s). Falling back to pypdf.",
                reason, e,
            )
            try:
                stream.seek(0)
            except Exception:
                pass
            pypdf_res = extract_pdf_stream_pypdf(stream, url=url, fallback_title=fallback_title)
            pypdf_res.parser_requested = "docling"
            pypdf_res.parser_used = "pypdf"
            pypdf_res.parser_fallback = True
            pypdf_res.parser_fallback_reason = reason
            return pypdf_res

    res = extract_pdf_stream_pypdf(stream, url=url, fallback_title=fallback_title)
    res.parser_requested = "pypdf"
    res.parser_used = "pypdf"
    res.parser_fallback = False
    return res


def extract_pdf_bytes(
    data: bytes,
    url: str | None = None,
    fallback_title: str | None = None,
    engine: str | None = None,
) -> PdfExtractResult:
    return extract_pdf_stream(io.BytesIO(data), url=url, fallback_title=fallback_title, engine=engine)

