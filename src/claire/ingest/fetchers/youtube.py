"""YouTube fetcher — 자막(transcript) + 영상 ID. (네트워크 필요)"""

from __future__ import annotations

import re

from ...ontology.base import Document
from ..normalize import canonicalize_url, content_hash
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
    except Exception as e:  # noqa: BLE001
        raise FetchError(f"transcript unavailable for {vid}: {e}") from e

    text = transcript.strip()
    if not text:
        raise FetchError(f"empty transcript for {vid}")

    return Document(
        url=url,
        canonical_url=f"https://youtube.com/watch?v={vid}",
        title=f"YouTube {vid}",
        raw_text=text[:20000],
        source_type="youtube",
        content_hash=content_hash(text),
        meta={"video_id": vid},
    )
