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
    re.compile(r"/live/([\w-]{11})"),
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
    details = fetch_video_details(vid)
    return details.get("title") or None


def fetch_video_details(vid: str) -> dict[str, str | list[str]]:
    """YouTube 웹 페이지(ytInitialPlayerResponse) 또는 oEmbed에서 제목, 채널명, 설명문, 태그 추출."""
    import json
    import httpx

    info: dict[str, str | list[str]] = {
        "title": "",
        "author": "",
        "description": "",
        "keywords": [],
    }

    # 1. YouTube 웹 페이지 파싱 (상세 설명, 채널명, 키워드 확보)
    try:
        resp = httpx.get(
            f"https://www.youtube.com/watch?v={vid}",
            headers={
                "User-Agent": (
                    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
                ),
                "Accept-Language": "ko,en-US;q=0.9,en;q=0.8",
            },
            timeout=8,
        )
        if resp.status_code == 200:
            m = re.search(r"ytInitialPlayerResponse\s*=\s*({.+?});", resp.text)
            if m:
                data = json.loads(m.group(1))
                vd = data.get("videoDetails", {})
                if vd.get("title"):
                    info["title"] = str(vd["title"]).strip()
                if vd.get("author"):
                    info["author"] = str(vd["author"]).strip()
                if vd.get("shortDescription"):
                    info["description"] = str(vd["shortDescription"]).strip()
                if vd.get("keywords") and isinstance(vd["keywords"], list):
                    info["keywords"] = vd["keywords"]
    except Exception:  # noqa: BLE001
        pass

    # 2. oEmbed 폴백 (제목/작성자가 아직 없는 경우)
    if not info["title"] or not info["author"]:
        try:
            resp = httpx.get(
                "https://www.youtube.com/oembed",
                params={"url": f"https://www.youtube.com/watch?v={vid}", "format": "json"},
                timeout=8,
            )
            if resp.status_code == 200:
                data = resp.json() or {}
                if not info["title"] and data.get("title"):
                    info["title"] = str(data["title"]).strip()
                if not info["author"] and data.get("author_name"):
                    info["author"] = str(data["author_name"]).strip()
        except Exception:  # noqa: BLE001
            pass

    return info


def fetch_transcript(vid: str, *, preferred_languages: list[str] | None = None) -> str:
    """youtube-transcript-api 기반 자막 추출 (수동/자동 자막, 선호 언어 우선, 전체 언어 탐색)."""
    if preferred_languages is None:
        settings = get_settings()
        target_langs = settings.effective_preferred_languages
    else:
        target_langs = [
            lang.strip().lower() for lang in preferred_languages if lang.strip().lower() != "en"
        ] + ["en"]

    try:
        from youtube_transcript_api import YouTubeTranscriptApi

        api = YouTubeTranscriptApi()

        # 1. list() 지원 시 (1.x 이상 권장)
        if hasattr(api, "list"):
            try:
                tl = api.list(vid)
                transcript_obj = None
                # 선호 언어 + en 우선 검색
                try:
                    transcript_obj = tl.find_transcript(target_langs)
                except Exception:  # noqa: BLE001
                    pass

                # 수동 자막 전체 중 첫 번째
                if not transcript_obj and getattr(tl, "_manually_created_transcripts", None):
                    transcript_obj = next(iter(tl._manually_created_transcripts.values()), None)
                # 자동 생성 자막 전체 중 첫 번째
                if not transcript_obj and getattr(tl, "_generated_transcripts", None):
                    transcript_obj = next(iter(tl._generated_transcripts.values()), None)

                if transcript_obj:
                    snippets = transcript_obj.fetch()
                    parts = []
                    for snip in snippets:
                        txt = getattr(snip, "text", None)
                        if txt is None and isinstance(snip, dict):
                            txt = snip.get("text", "")
                        if txt:
                            parts.append(txt)
                    if parts:
                        return " ".join(parts).strip()
            except Exception:  # noqa: BLE001
                pass

        # 2. fetch() 폴백 (0.x 또는 list 실패 방어)
        try:
            try:
                fetched = api.fetch(vid, languages=target_langs)
            except Exception:  # noqa: BLE001
                fetched = api.fetch(vid)
            parts = []
            for snip in fetched:
                txt = getattr(snip, "text", None)
                if txt is None and isinstance(snip, dict):
                    txt = snip.get("text", "")
                if txt:
                    parts.append(txt)
            if parts:
                return " ".join(parts).strip()
        except Exception:  # noqa: BLE001
            pass

    except Exception:  # noqa: BLE001
        pass

    return ""


def fetch_youtube(
    url: str,
    *,
    full_content: bool = False,
    preferred_languages: list[str] | None = None,
) -> Document:
    vid = video_id(url)
    if not vid:
        raise FetchError(f"no youtube video id in {url}")

    details = fetch_video_details(vid)
    transcript = fetch_transcript(vid, preferred_languages=preferred_languages)

    title = str(details.get("title") or "").strip() or f"YouTube {vid}"
    author = str(details.get("author") or "").strip()
    description = str(details.get("description") or "").strip()
    keywords = details.get("keywords") or []

    # 본문 텍스트 구성 (자막 + 설명문 결합, 또는 자막/설명문 단독)
    text_sections: list[str] = []
    if author:
        text_sections.append(f"채널: {author}")

    if transcript:
        text_sections.append(f"[영상 자막]\n{transcript}")

    if description:
        text_sections.append(f"[영상 설명]\n{description}")

    if keywords and isinstance(keywords, list):
        text_sections.append(f"[태그]\n{', '.join(keywords)}")

    text = "\n\n".join(text_sections).strip()
    if not text:
        raise FetchError(f"empty transcript and details for {vid}")

    settings = get_settings()
    budget = 0 if full_content else settings.raw_char_budget
    raw_text, is_truncated, orig_chars, raw_chars = slice_document_text(
        text or "", budget, strategy=settings.slicing_strategy
    )
    return Document(
        url=url,
        canonical_url=f"https://youtube.com/watch?v={vid}",
        title=title,
        author=author or None,
        raw_text=raw_text,
        source_type="youtube",
        content_hash=content_hash(text),
        meta={
            "video_id": vid,
            "raw_truncated": is_truncated,
            "orig_chars": orig_chars,
            "raw_chars": raw_chars,
            "has_transcript": bool(transcript),
        },
    )
