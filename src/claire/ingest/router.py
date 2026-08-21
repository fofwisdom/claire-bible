"""입력 라우팅 — payload 를 적절한 fetcher 로 보내 Document 를 만든다.

redirect(google share 등)는 최종 URL 로 해석 후 재라우팅한다.
fetch 함수들은 lazy 하게 호출되므로 무거운 의존성(scrapling 등)은 필요할 때만 로드된다.
"""

from __future__ import annotations

import os
import re
from urllib.parse import urlsplit

from ..ontology.base import Document
from .fetchers.base import FetchError

_URL_RE = re.compile(r"https?://[^\s)\]\}<>\"']+")


def extract_shared_url(payload: str) -> str | None:
    """'제목 + 링크' 형태(모바일/데스크톱 공유)로 들어온 텍스트에서 URL 을 뽑는다.

    모바일 브라우저·앱의 '공유'는 보통 「기사 제목 … <URL>」처럼 본문 끝에 URL 을 붙여
    보낸다. 이때 텍스트가 http 로 시작하지 않아 그동안 순수 메모(text)로 적재돼 링크가
    fetch 되지 않았다(실관측: url=None 90자 thin 노드). **마지막 토큰이 URL** 일 때만
    그 자료를 가리키는 공유로 보고 추출한다(본문 중간 링크가 섞인 일반 메모는 text 유지).
    """
    t = (payload or "").strip()
    if not t or t.lower().startswith(("http://", "https://")):
        return None
    tokens = t.split()
    if not tokens:
        return None
    last = tokens[-1].rstrip(".,;)。")
    m = _URL_RE.fullmatch(last)
    return m.group(0) if m else None


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
    # '제목 + 트레일링 링크' 공유 텍스트 → 그 URL 의 종류로 라우팅.
    shared = extract_shared_url(t)
    if shared:
        return classify(shared)
    # 로컬 파일 경로?
    if os.path.sep in t and os.path.exists(t):
        return "file"
    if t.startswith("file://"):
        return "file"
    return "text"


def fetch(payload: str, *, _depth: int = 0) -> Document:
    """라우팅 + fetch. redirect 는 1회 재귀로 최종 URL 재라우팅."""
    t = payload.strip()
    # '제목 + 트레일링 링크' 공유 텍스트면 URL 을 실제 자료로 취급해 fetch.
    if not t.lower().startswith(("http://", "https://")):
        shared = extract_shared_url(t)
        if shared:
            t = shared
    kind = classify(t)

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
        # fxtwitter JSON API 로 트윗 본문을 실제 스크랩(실패 시 web 폴백은 fetcher 내부).
        from .fetchers.xcom import fetch_xcom

        return fetch_xcom(t)

    if kind == "file":
        from .fetchers.textfile import fetch_file

        return fetch_file(t.removeprefix("file://"))

    if kind == "web":
        from .fetchers.web import fetch_web

        return fetch_web(t)

    from .fetchers.textfile import fetch_text

    return fetch_text(t)
