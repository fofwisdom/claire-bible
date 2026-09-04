"""VMware Explore Presentation PDF 발견·검증·추출·비디오 문서 결합."""

from __future__ import annotations

import hashlib
import ipaddress
import re
import socket
from collections.abc import Callable, Iterable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Literal
from urllib.parse import unquote, urljoin, urlsplit, urlunsplit

import httpx
from lxml import html as lxml_html

from ...config import Settings
from ...ontology.base import Document, SourceAttachment
from ..normalize import content_hash
from .base import FetchError
from .http_policy import BROWSER_USER_AGENT
from .pdf import extract_pdf_bytes, slice_pdf_text
from .web import render_html_cdp

_VMWARE_VIDEO_PATH_RE = re.compile(r"^/explore/video/(\d+)/?$")
_SESSION_CODE_RE = re.compile(r"\b[A-Z]{3,}\d{3,}[A-Z]*\b")
_ALLOWED_PRESENTATION_HOSTS = frozenset({"static.rainfocus.com"})
_PDF_CONTENT_TYPES = frozenset(
    {
        "application/pdf",
        "application/x-pdf",
        "application/vnd.pdf",
        "application/acrobat",
        "text/pdf",
    }
)
_MAX_REDIRECTS = 5
_MAX_PRESENTATIONS = 3


class PresentationFetchError(FetchError):
    """Presentation 처리 단계와 복구 가능한 오류 코드를 노출한다."""

    def __init__(self, status: str, detail: str):
        self.status = status
        super().__init__(f"presentation_pdf.{status}: {detail}")


@dataclass(frozen=True)
class PresentationCandidate:
    url: str
    title: str = "Presentation PDF"


@dataclass(frozen=True)
class PresentationDiscovery:
    status: Literal["absent", "available", "discovery_failed"]
    candidates: list[PresentationCandidate] = field(default_factory=list)
    rendered: bool = False
    error: str | None = None


@dataclass(frozen=True)
class PresentationExtract:
    attachment: SourceAttachment
    text: str
    extracted_title: str | None
    links: list[str]
    biblio: dict
    parser_requested: str
    parser_used: str
    parser_fallback: bool
    parser_fallback_reason: str | None
    orig_chars: int
    raw_chars: int
    truncated: bool


def vmware_explore_video_id(url: str) -> str | None:
    """정확한 VMware Explore 숫자형 상세 URL이면 비디오 ID를 반환한다."""
    parsed = urlsplit(url)
    if parsed.scheme.lower() != "https":
        return None
    if (parsed.hostname or "").lower() not in {"vmware.com", "www.vmware.com"}:
        return None
    match = _VMWARE_VIDEO_PATH_RE.fullmatch(parsed.path)
    return match.group(1) if match else None


def select_presentation_candidates(
    rendered_html: str,
    *,
    base_url: str = "https://www.vmware.com/",
) -> list[PresentationCandidate]:
    """`.presentation-details`의 명시적인 Presentation PDF 링크만 선택한다."""
    if not rendered_html:
        return []
    try:
        tree = lxml_html.fromstring(rendered_html)
    except Exception:  # noqa: BLE001
        return []

    sections = tree.xpath(
        "//*[contains(concat(' ', normalize-space(@class), ' '), "
        "' presentation-details ')]"
    )
    out: list[PresentationCandidate] = []
    seen: set[str] = set()
    for section in sections:
        headings = section.xpath(".//*[self::h1 or self::h2 or self::h3 or self::h4]")
        has_exact_title = any(
            " ".join(h.text_content().split()).casefold() == "presentation pdf"
            for h in headings
        )
        if not has_exact_title:
            continue
        for anchor in section.xpath(".//a[@href]"):
            href = urljoin(base_url, (anchor.get("href") or "").strip())
            parsed = urlsplit(href)
            if parsed.scheme.lower() != "https" or not parsed.hostname:
                continue
            if href in seen:
                continue
            seen.add(href)
            out.append(PresentationCandidate(url=href))
            if len(out) >= _MAX_PRESENTATIONS:
                return out
    return out


def rendered_session_is_ready(rendered_html: str, video_id: str) -> bool:
    """Presentation 부재를 확정할 수 있을 만큼 세션 상세가 렌더링됐는지 판정한다."""
    if not rendered_html or video_id not in rendered_html:
        return False
    try:
        tree = lxml_html.fromstring(rendered_html)
    except Exception:  # noqa: BLE001
        return False
    body_text = " ".join(tree.text_content().split())
    if len(body_text) < 100:
        return False
    labels = sum(label in body_text for label in ("Details", "Speakers", "Share"))
    headings = tree.xpath("//h1[normalize-space()] | //h2[normalize-space()]")
    return bool(headings and labels >= 2)


def _fetch_static_html(url: str) -> str:
    try:
        with httpx.Client(
            follow_redirects=True,
            timeout=30,
            headers={"User-Agent": BROWSER_USER_AGENT},
        ) as client:
            response = client.get(url)
        if response.status_code >= 400:
            return ""
        return response.text
    except Exception:  # noqa: BLE001
        return ""


def discover_presentations(
    url: str,
    *,
    static_html: str | None = None,
    render_fn: Callable[[str], str] | None = None,
) -> PresentationDiscovery:
    """정적 HTML을 먼저 보고, 부재 판정에는 반드시 렌더링된 DOM을 사용한다."""
    video_id = vmware_explore_video_id(url)
    if video_id is None:
        raise ValueError("Presentation discovery only supports VMware Explore video URLs")

    initial_html = _fetch_static_html(url) if static_html is None else static_html
    candidates = select_presentation_candidates(initial_html, base_url=url)
    if candidates:
        return PresentationDiscovery(status="available", candidates=candidates)

    if render_fn is None:
        rendered_html = render_html_cdp(url, click_tab_label="Presentation")
    else:
        rendered_html = render_fn(url)
    if not rendered_html:
        return PresentationDiscovery(
            status="discovery_failed",
            rendered=True,
            error="render_failed",
        )
    candidates = select_presentation_candidates(rendered_html, base_url=url)
    if candidates:
        return PresentationDiscovery(
            status="available",
            candidates=candidates,
            rendered=True,
        )
    if not rendered_session_is_ready(rendered_html, video_id):
        return PresentationDiscovery(
            status="discovery_failed",
            rendered=True,
            error="session_not_ready",
        )
    return PresentationDiscovery(status="absent", rendered=True)


def _redact_url(url: str) -> str:
    """서명·추적 쿼리와 fragment를 보존 메타데이터에서 제거한다."""
    parsed = urlsplit(url)
    return urlunsplit((parsed.scheme, parsed.netloc, parsed.path, "", ""))


def _validate_presentation_url(url: str) -> tuple[str, int]:
    parsed = urlsplit(url)
    if parsed.scheme.lower() != "https" or not parsed.hostname:
        raise PresentationFetchError("invalid", "HTTPS URL required")
    if parsed.username or parsed.password:
        raise PresentationFetchError("invalid", "URL user information is forbidden")
    host = parsed.hostname.lower().rstrip(".")
    if host not in _ALLOWED_PRESENTATION_HOSTS:
        raise PresentationFetchError("invalid", "presentation host is not allowed")
    return host, parsed.port or 443


def _assert_public_dns(
    host: str,
    port: int,
    *,
    resolver: Callable[..., Iterable] = socket.getaddrinfo,
) -> None:
    try:
        addresses = resolver(host, port, type=socket.SOCK_STREAM)
    except Exception as exc:  # noqa: BLE001
        raise PresentationFetchError("download_failed", "DNS resolution failed") from exc
    found = False
    for item in addresses:
        try:
            address = item[4][0]
            ip = ipaddress.ip_address(address)
        except (IndexError, TypeError, ValueError):
            continue
        found = True
        if (
            ip.is_private
            or ip.is_loopback
            or ip.is_link_local
            or ip.is_multicast
            or ip.is_reserved
            or ip.is_unspecified
        ):
            raise PresentationFetchError("invalid", "non-public destination rejected")
    if not found:
        raise PresentationFetchError("download_failed", "DNS returned no usable address")


def _safe_filename(url: str) -> str:
    raw = unquote(Path(urlsplit(url).path).name)
    safe = "".join(ch for ch in raw if ch.isalnum() or ch in "._-")[:180]
    if not safe.lower().endswith(".pdf"):
        safe = f"{safe or 'presentation'}.pdf"
    return safe


def download_presentation(
    candidate: PresentationCandidate,
    settings: Settings,
    *,
    client_factory: Callable[..., object] = httpx.Client,
    resolver: Callable[..., Iterable] = socket.getaddrinfo,
) -> SourceAttachment:
    """허용 호스트·공개 IP·리다이렉트·크기·MIME·매직을 검증하며 PDF를 받는다."""
    current_url = candidate.url
    max_bytes = settings.presentation_pdf_max_bytes
    try:
        with client_factory(
            follow_redirects=False,
            timeout=30,
            headers={"User-Agent": BROWSER_USER_AGENT, "Accept": "application/pdf"},
        ) as client:
            for redirect_count in range(_MAX_REDIRECTS + 1):
                host, port = _validate_presentation_url(current_url)
                _assert_public_dns(host, port, resolver=resolver)
                with client.stream("GET", current_url) as response:
                    if response.status_code in {301, 302, 303, 307, 308}:
                        location = response.headers.get("location")
                        if not location:
                            raise PresentationFetchError(
                                "download_failed", "redirect location missing"
                            )
                        if redirect_count >= _MAX_REDIRECTS:
                            raise PresentationFetchError(
                                "download_failed", "too many redirects"
                            )
                        current_url = urljoin(current_url, location)
                        continue
                    if response.status_code >= 400:
                        raise PresentationFetchError(
                            "download_failed", f"HTTP {response.status_code}"
                        )

                    media_type = response.headers.get("content-type", "").split(";", 1)[0].strip().lower()
                    if media_type not in _PDF_CONTENT_TYPES:
                        raise PresentationFetchError("invalid", "response is not a PDF")
                    length_header = response.headers.get("content-length")
                    if length_header:
                        try:
                            declared_length = int(length_header)
                        except ValueError as exc:
                            raise PresentationFetchError(
                                "invalid", "invalid Content-Length"
                            ) from exc
                        if declared_length < 0 or declared_length > max_bytes:
                            raise PresentationFetchError(
                                "invalid", "presentation exceeds size limit"
                            )

                    chunks: list[bytes] = []
                    downloaded = 0
                    for chunk in response.iter_bytes():
                        if not chunk:
                            continue
                        downloaded += len(chunk)
                        if downloaded > max_bytes:
                            raise PresentationFetchError(
                                "invalid", "presentation exceeds size limit"
                            )
                        chunks.append(chunk)
                    data = b"".join(chunks)
                    if not data.startswith(b"%PDF-"):
                        raise PresentationFetchError("invalid", "PDF signature missing")
                    public_url = _redact_url(current_url)
                    return SourceAttachment(
                        kind="presentation_pdf",
                        source_url=public_url,
                        canonical_url=public_url,
                        filename=_safe_filename(current_url),
                        media_type=media_type,
                        byte_length=len(data),
                        content_sha256=hashlib.sha256(data).hexdigest(),
                        content=data,
                    )
    except PresentationFetchError:
        raise
    except Exception as exc:  # noqa: BLE001
        raise PresentationFetchError("download_failed", "network request failed") from exc
    raise PresentationFetchError("download_failed", "redirect processing failed")


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
        engine=settings.pdf_parser,
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
        biblio=getattr(result, "biblio", {}) or {},
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
            "biblio": item.biblio,
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


def compose_video_presentation(
    video_doc: Document,
    presentation: PresentationExtract,
) -> Document:
    """단일 Presentation 호환 래퍼."""
    return compose_video_presentations(video_doc, [presentation])
