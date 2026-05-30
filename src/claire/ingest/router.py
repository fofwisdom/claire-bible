"""입력 라우팅 — payload 를 적절한 fetcher 로 보내 Document 를 만든다.

redirect(google share 등)는 최종 URL 로 해석 후 재라우팅한다.
fetch 함수들은 lazy 하게 호출되므로 무거운 의존성(scrapling 등)은 필요할 때만 로드된다.
"""

from __future__ import annotations

import os
from urllib.parse import urlsplit

from ..ontology.base import Document
from .fetchers.base import FetchError


def classify(payload: str) -> str:
    """payload → 라우팅 종류. telegram_bot.classify_input 과 정합.

    종류: youtube | xcom | redirect | web | file | text
    """
    t = (payload or "").strip()
    if not t:
        return "text"
    low = t.lower()
    if low.startswith("http://") or low.startswith("https://"):
        host = urlsplit(low).netloc
        if "youtube.com" in host or "youtu.be" in host:
            return "youtube"
        if "x.com" in host or "twitter.com" in host:
            return "xcom"
        if "share.google" in host or host.startswith("share."):
            return "redirect"
        return "web"
    # 로컬 파일 경로?
    if os.path.sep in t and os.path.exists(t):
        return "file"
    if t.startswith("file://"):
        return "file"
    return "text"


def fetch(payload: str, *, _depth: int = 0) -> Document:
    """라우팅 + fetch. redirect 는 1회 재귀로 최종 URL 재라우팅."""
    kind = classify(payload)
    t = payload.strip()

    if kind == "youtube":
        from .fetchers.youtube import fetch_youtube

        return fetch_youtube(t)

    if kind == "redirect":
        if _depth > 2:
            raise FetchError("too many redirects")
        from .fetchers.redirect import resolve_redirect

        final = resolve_redirect(t)
        if final and final != t:
            return fetch(final, _depth=_depth + 1)
        # 해석 실패 시 web 으로 시도
        from .fetchers.web import fetch_web

        return fetch_web(t)

    if kind == "xcom":
        # v1: 부분 처리. 본문 스크랩 대신 URL 자체를 partial Note 로 보관.
        from .normalize import canonicalize_url, content_hash

        return Document(
            url=t,
            canonical_url=canonicalize_url(t),
            title=f"x.com post",
            raw_text=t,
            source_type="xcom",
            content_hash=content_hash(t),
            partial=True,
        )

    if kind == "file":
        from .fetchers.textfile import fetch_file

        return fetch_file(t[len("file://"):] if t.startswith("file://") else t)

    if kind == "web":
        from .fetchers.web import fetch_web

        return fetch_web(t)

    from .fetchers.textfile import fetch_text

    return fetch_text(t)
