"""YouTube fetcher — 자막(transcript) + 영상 ID. (네트워크 필요)"""

from __future__ import annotations

import re

from ...config import get_settings
from ...extract.table_budget import slice_document_text
from ...ontology.base import Document
from ..normalize import content_hash
from .base import FetchError

_ID_RES = [
    re.compile(r"[?&]v=([\w-]{11})"),
    re.compile(r"youtu\.be/([\w-]{11})"),
    re.compile(r"/shorts/([\w-]{11})"),
    re.compile(r"/embed/([\w-]{11})"),
]


def video_id(url: str) -> str | None:
    for r in _ID_RES:
        m = r.search(url)
        if m:
            return m.group(1)
    return None


def fetch_video_title(vid: str) -> str | None:
    """oEmbed(API 키 불필요)로 실제 영상 제목을 가져온다. 실패하면 None(호출측이 폴백)."""
    import httpx

    try:
        resp = httpx.get(
            "https://www.youtube.com/oembed",
            params={"url": f"https://www.youtube.com/watch?v={vid}", "format": "json"},
            timeout=8,
        )
        if resp.status_code == 200:
            title = (resp.json() or {}).get("title")
            if title:
                return title.strip()
    except Exception:  # noqa: BLE001
        pass
    return None


def fetch_youtube(url: str) -> Document:
    vid = video_id(url)
    if not vid:
        raise FetchError(f"no youtube video id in {url}")

    transcript = ""
    try:
        from youtube_transcript_api import YouTubeTranscriptApi

        api = YouTubeTranscriptApi()
        # 한국어/영어 우선, 없으면 자동(언어 미지정).
        try:
            fetched = api.fetch(vid, languages=["ko", "en"])
        except Exception:  # noqa: BLE001
            fetched = api.fetch(vid)
        # 1.x: FetchedTranscript(이터러블, snippet.text). 0.x dict 도 방어.
        parts = []
        for snip in fetched:
            txt = getattr(snip, "text", None)
            if txt is None and isinstance(snip, dict):
                txt = snip.get("text", "")
            if txt:
                parts.append(txt)
        transcript = " ".join(parts)
    except Exception as e:
        raise FetchError(f"transcript unavailable for {vid}: {e}") from e

    text = transcript.strip()
    if not text:
        raise FetchError(f"empty transcript for {vid}")

    title = fetch_video_title(vid) or f"YouTube {vid}"

    settings = get_settings()
    raw_text, is_truncated, orig_chars, raw_chars = slice_document_text(
        text or "", settings.raw_char_budget, strategy=settings.slicing_strategy
    )
    return Document(
        url=url,
        canonical_url=f"https://youtube.com/watch?v={vid}",
        title=title,
        raw_text=raw_text,
        source_type="youtube",
        content_hash=content_hash(text),
        meta={
            "video_id": vid,
            "raw_truncated": is_truncated,
            "orig_chars": orig_chars,
            "raw_chars": raw_chars,
        },
    )
