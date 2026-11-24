"""Video and presentation PDF composition: a format service of the core (docs/implementation/INGESTION.md §2.2).

A plugin that finds presentation PDFs for a video (for example claire.vmware_explore) downloads them, then uses ``extract_presentation`` and ``compose_video_presentations`` through ``claire.plugin_sdk``. Design: VIDEO_PRESENTATION_BUNDLE_INGESTION_DESIGN.md.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from urllib.parse import urlsplit

from ...core.config import Settings
from ...ontology.base import Document, SourceAttachment
from ..normalize import content_hash
from .base import FetchError
from .pdf import extract_pdf_bytes, slice_pdf_text

_SESSION_CODE_RE = re.compile(r"\b[A-Z]{3,}\d{3,}[A-Z]*\b")


class PresentationFetchError(FetchError):
    """Presentation 처리 단계와 복구 가능한 오류 코드를 노출한다."""

    def __init__(self, status: str, detail: str):
        self.status = status
        super().__init__(f"presentation_pdf.{status}: {detail}")


@dataclass(frozen=True)
class PresentationExtract:
    attachment: SourceAttachment
    text: str
    extracted_title: str | None
    links: list[str]
    parser_requested: str
    parser_used: str
    parser_fallback: bool
    parser_fallback_reason: str | None
    orig_chars: int
    raw_chars: int
    truncated: bool


def extract_presentation(
    attachment: SourceAttachment,
    settings: Settings,
    *,
    full_content: bool = False,
) -> PresentationExtract:
    """기존 PDF 엔진을 재사용하되 발표자료에는 부록·참고문헌 제외를 적용하지 않는다."""
    result = extract_pdf_bytes(
        attachment.content,
        url=attachment.canonical_url,
        fallback_title=attachment.filename,
        engine="pypdfium2",
    )
    extracted_title, text, links, _anchors, error, _images = result[:6]
    if error or not text.strip():
        raise PresentationFetchError("extract_failed", error or "empty PDF content")

    limit = 0 if full_content else settings.pdf_max_extract_chars
    sliced, truncated, _app, _refs, orig_chars, raw_chars = slice_pdf_text(
        text,
        limit,
        strategy=settings.slicing_strategy,
        exclude_appendix=False,
        exclude_references=False,
    )
    return PresentationExtract(
        attachment=attachment,
        text=sliced,
        extracted_title=extracted_title,
        links=links,
        parser_requested=getattr(result, "parser_requested", "pypdf"),
        parser_used=getattr(result, "parser_used", "pypdf"),
        parser_fallback=bool(getattr(result, "parser_fallback", False)),
        parser_fallback_reason=getattr(result, "parser_fallback_reason", None),
        orig_chars=orig_chars,
        raw_chars=raw_chars,
        truncated=truncated,
    )


def _transcript_span(text: str) -> tuple[int, int] | None:
    markers = ("[영상 음성 전사 (STT)]\n", "[영상 자막]\n")
    found = [(text.find(marker), marker) for marker in markers if text.find(marker) >= 0]
    if not found:
        return None
    start, marker = min(found, key=lambda item: item[0])
    end = len(text)
    for next_marker in ("\n\n[영상 설명]\n", "\n\n[태그]\n"):
        pos = text.find(next_marker, start + len(marker))
        if pos >= 0:
            end = min(end, pos)
    return start, end


def _presentation_code(filename: str, index: int) -> str:
    match = _SESSION_CODE_RE.search(filename.upper())
    return match.group(0) if match else f"presentation-{index}"


def compose_video_presentations(
    video_doc: Document,
    presentations: list[PresentationExtract],
) -> Document:
    """비디오 텍스트에 하나 이상의 Presentation을 출처 경계와 함께 결합한다."""
    if not presentations:
        video_doc.meta["presentation_pdf"] = {"status": "absent"}
        return video_doc
    if not (video_doc.meta or {}).get("has_transcript"):
        raise PresentationFetchError(
            "bundle_incomplete_media", "presentation exists but media text is unavailable"
        )

    base_text = (video_doc.raw_text or "").rstrip()
    transcript_span = _transcript_span(base_text)
    if transcript_span is None:
        raise PresentationFetchError(
            "bundle_incomplete_media", "media transcript boundary is unavailable"
        )
    transcript_start, transcript_end = transcript_span

    insertion_parts: list[str] = []
    presentation_meta: list[dict] = []
    extra_sources = list((video_doc.meta or {}).get("extra_sources") or [])
    components: list[dict] = [
        {
            "kind": "transcript",
            "start": transcript_start,
            "end": transcript_end,
            "language": (video_doc.meta or {}).get("caption_language"),
            "content_sha256": (video_doc.meta or {}).get("caption_content_hash")
            or content_hash(base_text[transcript_start:transcript_end]),
        }
    ]
    relative_offset = 0
    for index, item in enumerate(presentations, 1):
        code = _presentation_code(item.attachment.filename, index)
        block = (
            f"\n\n---\n[발표자료 PDF — {code}]\n"
            f"출처: {item.attachment.canonical_url}\n\n{item.text}"
        )
        block_content_start = transcript_end + relative_offset
        insertion_parts.append(block)
        relative_offset += len(block)
        component = {
            "kind": "presentation_pdf",
            "start": block_content_start,
            "end": transcript_end + relative_offset,
            "content_sha256": item.attachment.content_sha256,
            "text_sha256": content_hash(item.text),
            "orig_chars": item.orig_chars,
            "raw_chars": item.raw_chars,
        }
        components.append(component)
        item_meta = {
            "status": "available",
            "public_url": item.attachment.canonical_url,
            "source_host": urlsplit(item.attachment.canonical_url).hostname,
            "session_title": video_doc.title,
            "extracted_title": item.extracted_title,
            "filename": item.attachment.filename,
            "media_type": item.attachment.media_type,
            "byte_length": item.attachment.byte_length,
            "content_sha256": item.attachment.content_sha256,
            "text_sha256": component["text_sha256"],
            "raw_chars": item.raw_chars,
            "orig_chars": item.orig_chars,
            "raw_truncated": item.truncated,
            "parser_requested": item.parser_requested,
            "parser_used": item.parser_used,
            "parser_fallback": item.parser_fallback,
            "parser_fallback_reason": item.parser_fallback_reason,
            "links": item.links,
            "artifact_path": None,
        }
        presentation_meta.append(item_meta)
        extra_sources.append(
            {
                "url": item.attachment.canonical_url,
                "canonical_url": item.attachment.canonical_url,
                "source_type": "pdf",
                "title": f"{video_doc.title or code} — Presentation PDF",
                "content_hash": item.attachment.content_sha256,
            }
        )

    insertion = "".join(insertion_parts)
    combined = base_text[:transcript_end] + insertion + base_text[transcript_end:]
    video_meta = dict(video_doc.meta or {})
    video_orig_chars = int(video_meta.get("orig_chars") or len(base_text))
    presentation_raw_chars = sum(len(item.text) for item in presentations)
    insertion_overhead = len(insertion) - presentation_raw_chars
    components[0]["raw_chars"] = transcript_end - transcript_start
    components[0]["orig_chars"] = len(
        base_text[transcript_start:transcript_end]
    )
    video_meta.update(
        {
            "presentation_pdf": presentation_meta[0],
            "presentation_pdfs": presentation_meta,
            "content_components": components,
            "extra_sources": extra_sources,
            "raw_truncated": bool(
                video_meta.get("raw_truncated")
                or any(item.truncated for item in presentations)
            ),
            "orig_chars": video_orig_chars
            + sum(item.orig_chars for item in presentations)
            + insertion_overhead,
            "raw_chars": len(combined),
        }
    )
    video_doc.raw_text = combined
    video_doc.content_hash = content_hash(combined)
    video_doc.partial = bool(
        video_doc.partial or any(item.truncated for item in presentations)
    )
    video_doc.meta = video_meta
    video_doc.attachments.extend(item.attachment for item in presentations)
    return video_doc
