"""입력 라우팅 — payload 를 적절한 fetcher 로 보내 Document 를 만든다.

redirect(google share 등)는 최종 URL 로 해석 후 재라우팅한다.
fetch 함수들은 lazy 하게 호출되므로 무거운 의존성(scrapling 등)은 필요할 때만 로드된다.
"""

from __future__ import annotations

import os
import re
from pathlib import Path
from urllib.parse import urlsplit

from ..ontology.base import Document
from .fetchers.base import FetchError

_URL_RE = re.compile(r"https?://[^\s)\]\}<>\"']+")
_TRUSTED_LOCAL_FILE_SOURCES = {
    "cli",
    "cli-expand",
    "replay-cli",
    "manual-retry-cli",
    "recover-cli",
}


def _local_path(payload: str) -> Path:
    """현재 라우터와 동일한 의미로 로컬 파일 payload를 Path로 변환한다."""
    value = (payload or "").strip()
    if value.lower().startswith("file://"):
        value = value[7:]
    return Path(value)


def is_trusted_local_file_source(source: str) -> bool:
    """서버 로컬 파일을 열 수 있는 내부 source 값을 정확히 판별한다."""
    return source in _TRUSTED_LOCAL_FILE_SOURCES


def validate_ingest_file_access(
    payload: str,
    *,
    source: str,
    file_ref: str | None = None,
    data_dir: Path | None = None,
) -> None:
    """원격 텍스트가 서버 로컬 경로로 승격되지 않도록 ingest 경계를 검증한다.

    CLI의 명시적 로컬 파일 적재는 유지한다. Telegram 업로드 및 그 replay/recover 경로는
    raw/files 아래에 서버가 기록한 ``file_ref``와 payload가 정확히 일치할 때만 허용한다.
    """
    if classify(payload) != "file":
        return
    if is_trusted_local_file_source(source):
        return
    if not file_ref or data_dir is None:
        raise FetchError("local file paths are allowed only from CLI or verified uploads")

    candidate = _local_path(payload)
    recorded = Path(file_ref)
    try:
        candidate_resolved = candidate.resolve(strict=True)
        recorded_resolved = recorded.resolve(strict=True)
        allowed_root = (Path(data_dir) / "raw" / "files").resolve(strict=True)
    except (OSError, RuntimeError) as exc:
        raise FetchError("verified upload path is unavailable") from exc

    if candidate_resolved != recorded_resolved:
        raise FetchError("payload does not match the verified upload")
    if not candidate_resolved.is_relative_to(allowed_root):
        raise FetchError("verified upload is outside the upload directory")
    if candidate.is_symlink() or not candidate_resolved.is_file():
        raise FetchError("verified upload must be a regular file")


def leading_url(t: str) -> str:
    """'URL + 캡션'(URL 이 먼저, 뒤에 제목/설명이 붙는 공유) 텍스트에서 URL 만 뽑는다.

    반대 패턴('제목 + 트레일링 URL')은 extract_shared_url 이 처리. 이 정리 없이 전체
    텍스트를 URL 로 오인하면 fetch 단계에서 개행 등 URL 에 못 쓰는 문자 때문에 실패한다
    (실관측: inbox#276, httpx "Invalid non-printable ASCII character in URL, '\\n'").
    """
    m = _URL_RE.match(t)
    return m.group(0) if m else t


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
        host = urlsplit(leading_url(t)).netloc.lower()
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
    if low.startswith("file://"):
        return "file"
    return "text"


def fetch(payload: str, *, _depth: int = 0) -> Document:
    """라우팅 + fetch. redirect 는 1회 재귀로 최종 URL 재라우팅."""
    t = payload.strip()
    if t.lower().startswith(("http://", "https://")):
        # 'URL + 캡션' 공유(URL 먼저, 뒤에 제목/설명) → URL 만 남긴다.
        t = leading_url(t)
    else:
        # '제목 + 트레일링 링크' 공유 텍스트면 URL 을 실제 자료로 취급해 fetch.
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

        return fetch_file(t[len("file://"):] if t.startswith("file://") else t)

    if kind == "web":
        from .fetchers.web import fetch_web

        return fetch_web(t)

    from .fetchers.textfile import fetch_text

    return fetch_text(t)
