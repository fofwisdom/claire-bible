"""Naver Clip / Shorts video fetcher with STT transcription.

Reverse-engineers Naver CreatorHub Clip Viewer API to retrieve media metadata
and direct authenticated audio/video streams for STT transcription, preventing
DOM scraping fallbacks and full hallucinations.
"""

from __future__ import annotations

import json
import logging
import re
import tempfile
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any

from ...config import Settings, get_settings
from ...extract.table_budget import slice_document_text
from ...extract.transcript.factory import get_transcript_provider
from ...ontology.base import Document
from ..normalize import canonicalize_url, content_hash
from ..registry import register_fetcher
from .base import BaseFetcher, FetchError
from .http_policy import BROWSER_USER_AGENT

logger = logging.getLogger(__name__)

_CLIP_API_ENDPOINT = "https://creatorhub-api.naver.com/api/v7.0/clipviewer/card"
_HEX_ID_RE = re.compile(r"^[A-Fa-f0-9]{36,40}$")
_MEDIA_ID_QUERY_RE = re.compile(r"[?&](?:seedMediaId|mediaId)=([A-Fa-f0-9]{36,40})", re.IGNORECASE)
_SHORTS_PATH_RE = re.compile(r"/shorts/([A-Fa-f0-9]{36,40})", re.IGNORECASE)


def is_naver_clip_url(url: str) -> bool:
    """Determine whether URL points to Naver Clip or Naver Shorts."""
    if not url:
        return False
    u = url.strip().lower()
    if "naver.me" in u:
        return True
    if "clip.naver.com" in u:
        return True
    if "m.naver.com" in u and "/shorts" in u:
        return True
    if "tv.naver.com" in u or "now.naver.com" in u:
        return True
    return False


class NaverClipClient:
    """Client for Naver Clip / CreatorHub public API."""

    def __init__(self, timeout: float = 20.0) -> None:
        self.timeout = timeout

    def resolve_media_id(self, url: str) -> str | None:
        """Resolve Naver Clip URL (including short links) to a 36-char media ID."""
        cleaned = (url or "").strip()
        m = _MEDIA_ID_QUERY_RE.search(cleaned)
        if m:
            return m.group(1).upper()

        m_path = _SHORTS_PATH_RE.search(cleaned)
        if m_path:
            return m_path.group(1).upper()

        # Follow short links (e.g. naver.me) to inspect final target
        req = urllib.request.Request(
            cleaned,
            headers={
                "User-Agent": BROWSER_USER_AGENT,
                "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
            },
        )
        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as resp:
                final_url = resp.url
                html = resp.read(65536).decode("utf-8", errors="ignore")
        except Exception as e:
            logger.warning("Failed to follow redirect for %s: %s", cleaned, e)
            return None

        m_final = _MEDIA_ID_QUERY_RE.search(final_url)
        if m_final:
            return m_final.group(1).upper()

        # Check bridge page query params or destination
        parsed = urllib.parse.urlsplit(final_url)
        qs = urllib.parse.parse_qs(parsed.query)
        if "url" in qs and qs["url"]:
            inner_url = qs["url"][0]
            m_inner = _MEDIA_ID_QUERY_RE.search(inner_url)
            if m_inner:
                return m_inner.group(1).upper()

        m_html = re.search(r"seedMediaId=([A-Fa-f0-9]{36,40})", html, re.IGNORECASE)
        if m_html:
            return m_html.group(1).upper()

        m_raw_hex = re.search(r"\"seedMediaId\":\s*\"([A-Fa-f0-9]{36,40})\"", html, re.IGNORECASE)
        if m_raw_hex:
            return m_raw_hex.group(1).upper()

        return None

    def get_card(self, media_id: str) -> dict[str, Any]:
        """Fetch card details for given mediaId from CreatorHub API."""
        params = {
            "userInteraction": "true",
            "seedType": "PERSONAL",
            "serviceType": "CLIP",
            "seedMediaId": media_id,
            "mediaType": "SHORT_FORM",
            "referer": "",
            "recType": "AIRS",
            "enableReverse": "false",
            "adAllowed": "false",
            "recentWatchedHistory": "",
            "deviceType": "html5_mo",
        }
        api_url = f"{_CLIP_API_ENDPOINT}?{urllib.parse.urlencode(params)}"
        req = urllib.request.Request(
            api_url,
            headers={
                "User-Agent": BROWSER_USER_AGENT,
                "Referer": f"https://m.naver.com/shorts/?seedMediaId={media_id}&serviceType=CLIP",
                "Origin": "https://m.naver.com",
                "Accept": "application/json, text/plain, */*",
            },
        )
        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as resp:
                data = json.loads(resp.read().decode("utf-8"))
        except Exception as e:
            raise FetchError(f"Naver CreatorHub API request failed for {media_id}: {e}") from e

        header = data.get("header") or {}
        if header.get("code") != 0:
            msg = header.get("message") or "Unknown error"
            raise FetchError(f"Naver CreatorHub API returned error: {msg} (code={header.get('code')})")

        return data

    def extract_streams(self, card_data: dict[str, Any]) -> tuple[str | None, str | None, str | None]:
        """Extract audio (.m4a), video (.mp4), and HLS (.m3u8) URLs from card playback data."""
        content = (card_data.get("body") or {}).get("card", {}).get("content", {})
        vod = content.get("vod") or {}
        vod_s = json.dumps(vod)

        m4as = re.findall(r"https?://[^\s\"'<>]+\.m4a[^\s\"'<>]*", vod_s)
        mp4s = re.findall(r"https?://[^\s\"'<>]+\.mp4[^\s\"'<>]*", vod_s)
        m3u8s = re.findall(r"https?://[^\s\"'<>]+\.m3u8[^\s\"'<>]*", vod_s)

        audio_url = m4as[0] if m4as else None
        video_url = mp4s[0] if mp4s else None
        hls_url = m3u8s[0] if m3u8s else None
        return audio_url, video_url, hls_url


def fetch_naver_clip(
    url: str,
    *,
    full_content: bool = False,
    preferred_languages: list[str] | None = None,
    settings: Settings | None = None,
) -> Document:
    """Fetch Naver Clip metadata and audio stream, transcribing with STT."""
    settings = settings or get_settings()
    client = NaverClipClient()

    media_id = client.resolve_media_id(url)
    if not media_id:
        raise FetchError(f"네이버 클립 미디어 ID를 식별할 수 없습니다: {url}")

    card_data = client.get_card(media_id)
    content = (card_data.get("body") or {}).get("card", {}).get("content", {})
    if not content:
        raise FetchError(f"네이버 클립 본문 데이터가 비어 있습니다: {url}")

    title = (content.get("title") or content.get("description") or "네이버 클립").strip()
    description = (content.get("description") or "").strip()
    uploader = (content.get("profile") or {}).get("nickname") or "NAVER CLIP"

    vq = content.get("videoQuality") or {}
    duration_val = vq.get("duration")
    duration_sec = float(duration_val) if duration_val is not None else 0.0

    audio_url, video_url, hls_url = client.extract_streams(card_data)
    stream_url = audio_url or video_url

    transcript_text: str | None = None
    segments_data: list[dict] = []
    is_stt = False

    effective_data_dir = getattr(settings, "data_dir", None)
    cached_used = False
    cached_file: Path | None = None

    if settings.enable_video_transcription and stream_url:
        from ...store.video_cache import (
            delete_cached_video_file,
            get_cached_video_file,
            save_video_file_to_cache,
        )

        canonical_media_url = f"https://m.naver.com/shorts/?seedMediaId={media_id}"
        if effective_data_dir:
            cached_file = get_cached_video_file(
                effective_data_dir,
                url,
                canonical_url=canonical_media_url,
                max_age_sec=getattr(settings, "video_cache_ttl_sec", 259200),
            )
            if cached_file:
                logger.info("Using cached Naver Clip audio: %s", cached_file)
                cached_used = True

        target_langs = preferred_languages or settings.effective_preferred_languages
        lang_code = settings.stt_language or (target_langs[0] if target_langs else "ko")

        if cached_file:
            try:
                stt_provider = get_transcript_provider(settings)
                stt_result = stt_provider.transcribe(cached_file, language=lang_code, timestamps=True)
                transcript_text = stt_result.full_text
                segments_data = [s.model_dump() for s in stt_result.segments]
                if transcript_text:
                    is_stt = True
                    delete_cached_video_file(effective_data_dir, url, canonical_url=canonical_media_url)
            except Exception as e:
                logger.warning("Cached Naver Clip STT failed: %s", e)
                delete_cached_video_file(effective_data_dir, url, canonical_url=canonical_media_url)
                cached_file = None

        if not transcript_text:
            ext = ".m4a" if audio_url else ".mp4"
            with tempfile.TemporaryDirectory(prefix="claire_naver_clip_") as tmp_dir:
                tmp_out = Path(tmp_dir) / f"audio{ext}"
                try:
                    req = urllib.request.Request(
                        stream_url,
                        headers={"User-Agent": BROWSER_USER_AGENT},
                    )
                    with urllib.request.urlopen(req, timeout=60.0) as resp, open(tmp_out, "wb") as f:
                        while chunk := resp.read(1024 * 1024):
                            f.write(chunk)

                    if tmp_out.is_file() and tmp_out.stat().st_size > 0:
                        stt_provider = get_transcript_provider(settings)
                        stt_result = stt_provider.transcribe(tmp_out, language=lang_code, timestamps=True)
                        transcript_text = stt_result.full_text
                        segments_data = [s.model_dump() for s in stt_result.segments]
                        if transcript_text:
                            is_stt = True
                        elif effective_data_dir:
                            save_video_file_to_cache(
                                effective_data_dir,
                                url,
                                tmp_out,
                                canonical_url=canonical_media_url,
                            )
                except Exception as e:
                    logger.warning("Naver Clip audio download/STT failed for %s: %s", url, e)

    # Strict Zero-Tolerance Hallucination Gate:
    # If transcript is empty and description is not substantive (>= 500 chars), fail immediately.
    has_substantive_desc = bool(description and len(description.strip()) >= 500)
    if not transcript_text and not has_substantive_desc:
        raise FetchError(
            f"video_transcript_missing: 네이버 클립 음성 전사(STT) 자막이 없고 상세 설명이 부족하여 "
            f"할루시네이션 방지를 위해 적재가 차단되었습니다: {url}"
        )

    sections: list[str] = []
    if duration_sec > 0:
        mins = int(duration_sec // 60)
        secs = int(duration_sec % 60)
        sections.append(f"재생 시간: {mins}분 {secs}초 ({duration_sec:.1f}초)")

    if transcript_text:
        sections.append(f"[영상 음성 전사 (STT)]\n{transcript_text}")
    elif description:
        sections.append(f"[영상 설명]\n{description}")

    full_text_blob = "\n\n".join(sections).strip()
    video_budget = getattr(settings, "video_max_extract_chars", 200000)
    budget = 0 if full_content else video_budget
    raw_text, is_truncated, orig_chars, raw_chars = slice_document_text(
        full_text_blob, budget, strategy=settings.slicing_strategy
    )

    canonical = canonicalize_url(f"https://m.naver.com/shorts/?seedMediaId={media_id}")

    return Document(
        url=url,
        canonical_url=canonical,
        title=title,
        author=uploader,
        raw_text=raw_text,
        source_type="video",
        content_hash=content_hash(full_text_blob),
        partial=bool(not transcript_text or is_truncated),
        meta={
            "video_channel": uploader,
            "duration_sec": duration_sec,
            "is_stt": is_stt,
            "stt_segments": segments_data,
            "has_transcript": bool(transcript_text),
            "orig_chars": orig_chars,
            "raw_chars": raw_chars,
            "media_id": media_id,
            "stream_url": stream_url,
            "fetch_via": "naver_clip",
        },
    )


@register_fetcher("naver_clip", priority=120)
class NaverClipFetcher(BaseFetcher):
    """Fetcher for Naver Clip and Shorts videos."""

    @classmethod
    def can_handle(cls, url: str) -> bool:
        return is_naver_clip_url(url)

    @classmethod
    def name(cls) -> str:
        return "video"

    @classmethod
    def fetch(cls, url: str, **kwargs: Any) -> Document:
        return fetch_naver_clip(url, **kwargs)

