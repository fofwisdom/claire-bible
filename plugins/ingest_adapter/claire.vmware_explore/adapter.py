"""VMware Explore session videos and presentation PDFs (project plugin claire.vmware_explore).

The plugin finds the presentation PDF of a session, downloads and checks it, and gives the media part to the core service ``fetch_media`` with the Brightcove player URL. ``compose_video_presentations`` (core) makes one document with the video text and the PDF text.
"""

from __future__ import annotations

import hashlib
import re
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Literal
from urllib.parse import unquote, urljoin, urlsplit, urlunsplit

from lxml import html as lxml_html

from claire.plugin_sdk import (
    BROWSER_USER_AGENT,
    BaseFetcher,
    Document,
    FetchError,
    PluginContext,
    PresentationFetchError,
    Settings,
    SourceAttachment,
    compose_video_presentations,
    emit_progress,
    extract_presentation,
    fetch_media,
    fetch_settings,
    render_html_cdp,
)

# Broadcom / VMware Explore account of the Brightcove player (the page can name another one).
BRIGHTCOVE_ACCOUNT_ID = "6164421911001"
HOSTS = ("vmware.com", "www.vmware.com")

_ctx: PluginContext | None = None


def guarded_client(**kwargs: Any) -> Any:
    """The network client of the plugin (``ctx.http_client()``, S6): private network guard and the hosts of ``permissions.network`` on each request and redirect hop."""
    if _ctx is None:
        raise RuntimeError("register(ctx) of claire.vmware_explore was not called")
    return _ctx.http_client(**kwargs)


def register(ctx: PluginContext) -> None:
    global _ctx
    _ctx = ctx
    ctx.fetcher(VmwareExploreFetcher, name="vmware_explore", priority=110, hosts=HOSTS)

_VMWARE_VIDEO_PATH_RE = re.compile(r"^/explore/video/(\d+)/?$")
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
        with guarded_client(
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
    client_factory: Callable[..., object] | None = None,
) -> SourceAttachment:
    """허용 호스트·리다이렉트·크기·MIME·매직을 검증하며 PDF를 받는다. 공개 IP 검사는 플러그인 네트워크 클라이언트의 사설망 가드가 매 요청·매 리다이렉트마다 한다."""
    client_factory = client_factory or guarded_client
    current_url = candidate.url
    max_bytes = settings.presentation_pdf_max_bytes
    try:
        with client_factory(
            follow_redirects=False,
            timeout=30,
            headers={"User-Agent": BROWSER_USER_AGENT, "Accept": "application/pdf"},
        ) as client:
            for redirect_count in range(_MAX_REDIRECTS + 1):
                _validate_presentation_url(current_url)
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


def brightcove_player_url(video_id: str, html_text: str = "") -> str:
    """The Brightcove player URL of a VMware Explore video; yt-dlp reads it directly."""
    account_id = BRIGHTCOVE_ACCOUNT_ID
    match = re.search(r"data-account=[\"'](\d+)[\"']", html_text or "", re.IGNORECASE)
    if match:
        account_id = match.group(1)
    return f"https://players.brightcove.net/{account_id}/default_default/index.html?videoId={video_id}"


def fetch_vmware_explore(
    url: str,
    *,
    full_content: bool = False,
    preferred_languages: list[str] | None = None,
    settings: Settings | None = None,
) -> Document:
    """Presentation discovery first; a required PDF that fails stops the ingest before the media part."""
    settings = fetch_settings(settings)
    video_id = vmware_explore_video_id(url)
    if video_id is None:
        raise FetchError(f"not a VMware Explore video URL: {url}")
    emit_progress("영상 페이지 자료 탐색 중…")
    discovery = discover_presentations(url)
    if discovery.status == "discovery_failed":
        raise FetchError(f"presentation_pdf.discovery_failed: {discovery.error or 'unable to determine availability'}")
    presentations = []
    for candidate in discovery.candidates:
        emit_progress("Presentation PDF 검증·추출 중…")
        attachment = download_presentation(candidate, settings)
        presentations.append(extract_presentation(attachment, settings, full_content=full_content))
    doc = fetch_media(
        url,
        full_content=full_content,
        preferred_languages=preferred_languages,
        settings=settings,
        media_url=brightcove_player_url(video_id),
    )
    if discovery.status == "available":
        emit_progress("자막·Presentation PDF 결합 중…")
        return compose_video_presentations(doc, presentations)
    doc.meta["presentation_pdf"] = {"status": "absent"}
    return doc


class VmwareExploreFetcher(BaseFetcher):
    @classmethod
    def can_handle(cls, url: str) -> bool:
        return vmware_explore_video_id(url) is not None

    @classmethod
    def fetch(cls, url: str, **kwargs: Any) -> Document:
        return fetch_vmware_explore(url, **kwargs)
