"""복합 적재(Composite Ingestion) 코어 모듈.

첨부 파일(PDF, ODT, Text 등)과 외부 하이퍼링크(YouTube, Video, Web 등)가 하나의 텔레그램 메시지 또는
단일 복합 요청으로 들어왔을 때 두 원천을 각각 수집하여 하나의 지식 문서(Document)로 통합 결합한다.
"""

from __future__ import annotations

import hashlib
from pathlib import Path
from urllib.parse import urlsplit

from ..config import Settings, get_settings
from ..ontology.base import Document, SourceAttachment
from .fetchers.textfile import fetch_file
from .normalize import content_hash
from .router import fetch as router_fetch


def fetch_composite_components(
    file_path: Path,
    file_name: str,
    url: str,
    settings: Settings | None = None,
    full_content: bool = False,
) -> tuple[Document, Document, bytes]:
    """첨부 파일과 원격 URL을 각각 fetch하여 (file_doc, url_doc, file_bytes) 반환.

    원자적 무결성 원칙(Zero Speculation):
    둘 중 하나라도 파일 부재, 0바이트, 수집 실패, 빈 본문일 경우 즉시 FetchError를 발생시켜
    부분 수집에 의한 단독 적재(fallback)를 원천 차단한다.
    """
    from .fetchers.base import FetchError

    if not file_path.is_file():
        raise FetchError(f"첨부 파일을 찾을 수 없습니다: {file_path}")

    file_bytes = file_path.read_bytes()
    if not file_bytes:
        raise FetchError(f"첨부 파일이 비어 있습니다 (0 bytes): {file_name}")

    file_doc = fetch_file(str(file_path), full_content=full_content)
    if file_bytes and file_doc.meta is not None:
        file_doc.meta.setdefault("byte_length", len(file_bytes))

    if not file_doc.raw_text or not file_doc.raw_text.strip():
        raise FetchError(f"첨부 파일 텍스트 추출 결과가 비어 있습니다: {file_name}")

    url_doc = router_fetch(url, full_content=full_content)
    if not url_doc.raw_text or not url_doc.raw_text.strip():
        raise FetchError(f"원격 링크 수집 결과 본문이 비어 있습니다: {url}")

    return file_doc, url_doc, file_bytes


def compose_composite_document(
    file_doc: Document,
    url_doc: Document,
    file_name: str,
    focus: str | None = None,
    settings: Settings | None = None,
    file_bytes: bytes | None = None,
) -> Document:
    """첨부 파일 문서와 원격 URL 문서를 결합하여 단일 복합 Document 생성.

    규칙:
    1. 시청각 미디어(video, youtube)가 존재하면 미디어를 Primary 앵커로 채택하고,
       PDF 문서를 presentation_pdf(발표자료) 컴포넌트로 결합한다.
    2. 일반 웹 URL + 첨부 문서인 경우, 영구 URL을 가진 url_doc을 Primary로 채택하고,
       첨부 문서를 supporting source로 결합한다.
    """
    is_video_primary = url_doc.source_type in ("video", "youtube")
    primary = url_doc
    supporting = file_doc

    if primary.meta is None:
        primary.meta = {}
    if supporting.meta is None:
        supporting.meta = {}

    is_pdf = file_name.lower().endswith(".pdf") or supporting.source_type == "pdf"
    base_text = primary.raw_text or ""
    supp_text = supporting.raw_text or ""

    if is_video_primary and is_pdf:
        # 비디오 + PDF 결합: Claire 표준 Presentation PDF 스키마 준수
        block = f"\n\n---\n[발표자료 PDF — {file_name}]\n출처: {file_name}\n\n{supp_text}"
        combined_text = base_text + block

        transcript_start = 0
        transcript_end = len(base_text)
        block_start = transcript_end
        block_end = len(combined_text)

        components: list[dict] = [
            {
                "kind": "transcript",
                "start": transcript_start,
                "end": transcript_end,
                "language": primary.meta.get("caption_language"),
                "content_sha256": primary.meta.get("caption_content_hash")
                or content_hash(base_text),
                "raw_chars": transcript_end - transcript_start,
                "orig_chars": int(primary.meta.get("orig_chars") or transcript_end),
            },
            {
                "kind": "presentation_pdf",
                "start": block_start,
                "end": block_end,
                "content_sha256": supporting.content_hash,
                "text_sha256": content_hash(supp_text),
                "orig_chars": int(supporting.meta.get("orig_chars") or len(supp_text)),
                "raw_chars": len(supp_text),
            },
        ]

        item_meta = {
            "status": "available",
            "public_url": f"file://{file_name}",
            "source_host": "telegram",
            "session_title": primary.title,
            "extracted_title": supporting.title or file_name,
            "filename": file_name,
            "media_type": "application/pdf",
            "byte_length": len(file_bytes) if file_bytes else int(supporting.meta.get("byte_length", 0)),
            "content_sha256": supporting.content_hash,
            "text_sha256": content_hash(supp_text),
            "raw_chars": len(supp_text),
            "orig_chars": int(supporting.meta.get("orig_chars") or len(supp_text)),
            "raw_truncated": bool(supporting.meta.get("raw_truncated")),
            "parser_requested": supporting.meta.get("pdf_parser_requested", "Docling"),
            "parser_used": supporting.meta.get("pdf_parser_used", "Docling"),
            "parser_fallback": bool(supporting.meta.get("pdf_parser_fallback")),
            "parser_fallback_reason": supporting.meta.get("pdf_parser_fallback_reason"),
            "links": [],
            "artifact_path": None,
        }

        primary.meta["presentation_pdf"] = item_meta
        primary.meta["presentation_pdfs"] = [item_meta]
        primary.meta["content_components"] = components

        if file_bytes:
            sha256_digest = hashlib.sha256(file_bytes).hexdigest()
            primary.attachments.append(
                SourceAttachment(
                    kind="presentation_pdf",
                    source_url=f"file://{file_name}",
                    canonical_url=f"file://{file_name}",
                    filename=file_name,
                    media_type="application/pdf",
                    byte_length=len(file_bytes),
                    content_sha256=sha256_digest,
                    content=file_bytes,
                    required=False,
                )
            )
    else:
        # 일반 웹/문서 결합
        label = "발표자료 PDF" if is_pdf else "첨부 보조 자료"
        block = f"\n\n---\n[{label} — {file_name}]\n출처: {file_name}\n\n{supp_text}"
        combined_text = base_text + block

        if is_pdf:
            item_meta = {
                "status": "available",
                "public_url": f"file://{file_name}",
                "source_host": "telegram",
                "session_title": primary.title,
                "extracted_title": supporting.title or file_name,
                "filename": file_name,
                "media_type": "application/pdf",
                "byte_length": len(file_bytes) if file_bytes else int(supporting.meta.get("byte_length", 0)),
                "content_sha256": supporting.content_hash,
                "text_sha256": content_hash(supp_text),
                "raw_chars": len(supp_text),
                "orig_chars": int(supporting.meta.get("orig_chars") or len(supp_text)),
                "raw_truncated": bool(supporting.meta.get("raw_truncated")),
                "parser_requested": supporting.meta.get("pdf_parser_requested", "Docling"),
                "parser_used": supporting.meta.get("pdf_parser_used", "Docling"),
                "parser_fallback": bool(supporting.meta.get("pdf_parser_fallback")),
                "parser_fallback_reason": supporting.meta.get("pdf_parser_fallback_reason"),
                "links": [],
                "artifact_path": None,
            }
            primary.meta["presentation_pdf"] = item_meta
            primary.meta["presentation_pdfs"] = [item_meta]

            if file_bytes:
                sha256_digest = hashlib.sha256(file_bytes).hexdigest()
                primary.attachments.append(
                    SourceAttachment(
                        kind="presentation_pdf",
                        source_url=f"file://{file_name}",
                        canonical_url=f"file://{file_name}",
                        filename=file_name,
                        media_type="application/pdf",
                        byte_length=len(file_bytes),
                        content_sha256=sha256_digest,
                        content=file_bytes,
                        required=False,
                    )
                )

    # extra_sources 추적
    extra_sources = list(primary.meta.get("extra_sources") or [])
    primary_canonical = primary.canonical_url or primary.url
    if primary_canonical and not any(s.get("canonical_url") == primary_canonical for s in extra_sources):
        extra_sources.append(
            {
                "url": primary.url or primary_canonical,
                "canonical_url": primary_canonical,
                "source_type": primary.source_type,
                "title": primary.title or "주 소스",
                "content_hash": primary.content_hash,
            }
        )
    extra_sources.append(
        {
            "url": f"file://{file_name}",
            "canonical_url": f"file://{file_name}",
            "source_type": supporting.source_type,
            "title": f"{file_name} — {'발표자료 PDF' if is_pdf else '첨부 문서'}",
            "content_hash": supporting.content_hash,
        }
    )

    primary.meta["extra_sources"] = extra_sources
    primary.meta["composite_ingest"] = True
    primary.meta["composite_kind"] = "attachment_with_link"
    if focus and focus.strip():
        primary.meta["focus"] = focus.strip()

    primary_orig = int(primary.meta.get("orig_chars") or len(base_text))
    supp_orig = int(supporting.meta.get("orig_chars") or len(supp_text))
    primary.meta["orig_chars"] = primary_orig + supp_orig
    primary.meta["raw_chars"] = len(combined_text)
    primary.raw_text = combined_text
    primary.content_hash = content_hash(combined_text)
    primary.partial = bool(primary.partial or supporting.partial)

    return primary
