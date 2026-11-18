"""입력 라우팅 — payload 를 적절한 fetcher 로 보내 Document 를 만든다.

사이트 규칙은 켜진 플러그인의 레지스트리 항목에서만 온다(docs/implementation/INGESTION.md §2.2). 공유 링크(share.google 등)는 project plugin claire.share_link가 해석해 ``reroute``로 다시 라우팅한다. fetch 함수들은 lazy 하게 호출되므로 무거운 의존성(scrapling 등)은 필요할 때만 로드된다.
"""

from __future__ import annotations

import os
import re
from urllib.parse import urlsplit

from ..ontology.base import Document

_URL_RE = re.compile(r"https?://[^\s)\]\}<>\"']+")


def extract_shared_url(payload: str) -> str | None:
    """'제목 + 링크' 형태(모바일/데스크톱 공유)로 들어온 텍스트에서 URL 을 뽑는다.

    모바일 브라우저·앱의 '공유'는 보통 「기사 제목 … <URL>」처럼 본문 끝에 URL 을 붙여 보낸다. 이때 텍스트가 http 로 시작하지 않아 그동안 순수 메모(text)로 적재돼 링크가 fetch 되지 않았다(실관측: url=None 90자 thin 노드). **마지막 토큰이 URL** 일 때만 그 자료를 가리키는 공유로 보고 추출한다(본문 중간 링크가 섞인 일반 메모는 text 유지).
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

    종류: 레지스트리 fetcher 항목 이름(예: video, youtube, share_link) | web | file | text
    """
    from .registry import registry
    return registry.classify(payload)


def _clean_url(url_candidate: str) -> str:
    """URL 문자열 뒤에 공백이나 트레일링 텍스트/디렉티브가 섞여 있는 경우 순수 URL만 추출."""
    t = (url_candidate or "").strip()
    if not t.lower().startswith(("http://", "https://")):
        return t
    tokens = t.split()
    if len(tokens) > 1:
        first = tokens[0].rstrip(".,;)。")
        m = _URL_RE.fullmatch(first)
        if m:
            return m.group(0)
    return t


def fetch(payload: str, *, full_content: bool = False, _depth: int = 0) -> Document:
    """라우팅 + fetch. ``_depth``는 ``claire.plugin_sdk.reroute``의 재라우팅 깊이(최대 2단계)."""
    t = payload.strip()
    # '제목 + 트레일링 링크' 공유 텍스트면 URL 을 실제 자료로 취급해 fetch.
    if not t.lower().startswith(("http://", "https://")):
        shared = extract_shared_url(t)
        if shared:
            t = shared
    kind = classify(t)

    if t.lower().startswith(("http://", "https://")):
        t = _clean_url(t)

    def _call_fetcher(fn, *args, **kwargs):
        try:
            return fn(*args, full_content=full_content, **kwargs)
        except TypeError:
            return fn(*args, **kwargs)

    from .registry import registry
    fetcher_cls = registry.get_fetcher(t)
    if fetcher_cls is not None:
        return _call_fetcher(fetcher_cls.fetch, t)

    if kind == "video":
        from .fetchers.video import fetch_video

        return _call_fetcher(fetch_video, t)

    if kind == "file":
        from .fetchers.textfile import fetch_file

        return _call_fetcher(fetch_file, t.removeprefix("file://"))

    if kind == "web":
        from .fetchers.web import fetch_web

        return _call_fetcher(fetch_web, t)

    from .fetchers.textfile import fetch_text

    return _call_fetcher(fetch_text, t)
