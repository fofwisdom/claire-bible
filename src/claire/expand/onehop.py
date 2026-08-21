"""1홉 자동 확장 — 적재한 문서에서 관련 외부 링크 후보를 뽑는다.

설계(PLAN/사용자): 자동 fetch 가 아니라 **후보 제안**이 기본. 텔레그램에서 confirm
하면 그때 fetch+ingest. 내부 연결(기존 그래프와의 관계)은 이미 파이프라인에서 자동.
비용 통제: 자료당 상한(expand_max) + 이미 적재된 URL 제외 + dedup.
"""

from __future__ import annotations

import re
import sqlite3
from urllib.parse import urlsplit

from ..ingest.normalize import canonicalize_url
from ..ontology.base import Document

_URL_RE = re.compile(r"https?://[^\s)\]\}<>\"']+")

# 본문에 흔히 섞이는 비콘텐츠/잡음 호스트는 후보에서 제외(정확 일치).
_SKIP_HOSTS = {
    "twitter.com", "x.com",  # v1 partial
    "youtube.com", "youtu.be",  # 후보로는 보류(소비형)
    "facebook.com", "linkedin.com", "instagram.com",
    "google.com", "accounts.google.com", "policies.google.com",
    "w3.org", "schema.org", "gravatar.com",
}

# 서브도메인까지 통째로 잡음인 호스트(suffix 매칭). 사이트 헤더/푸터의 기관·운영 링크.
#   - arxiv.org 본체는 막지 않는다(/abs 논문은 좋은 후보) — info.arxiv.org 만 차단.
#   - cornell.edu: arXiv 운영기관 푸터 링크(Cornell University/Tech)로 매번 섞임.
_SKIP_HOST_SUFFIXES = (
    "info.arxiv.org",
    "cornell.edu",
    "creativecommons.org",
    "doi.org",  # 후보로는 보류(메타 리졸버)
)

# 호스트 무관 boilerplate 경로 prefix(헤더/푸터의 about/help/donate/약관/로그인 류).
# 1홉은 '제안'일 뿐이라 과소제안의 해는 작고, 잡음 제안이 더 거슬린다(사용자 지적).
_SKIP_PATH_PREFIXES = (
    "/about", "/help", "/donate", "/login", "/signin", "/signup", "/register",
    "/privacy", "/terms", "/tos", "/legal", "/policies", "/contact", "/abuse",
    "/subscribe", "/newsletter", "/rss", "/feed", "/sitemap", "/static",
    "/assets", "/cdn-cgi",
)


def _host(url: str) -> str:
    h = urlsplit(url).netloc.lower()
    return h.removeprefix("www.")


def _is_blocked(url: str, host: str) -> bool:
    """잡음 호스트(정확/서브도메인) 또는 boilerplate 경로면 후보에서 제외."""
    if not host or host in _SKIP_HOSTS:
        return True
    if any(host == s or host.endswith("." + s) for s in _SKIP_HOST_SUFFIXES):
        return True
    path = urlsplit(url).path.rstrip("/").lower() or "/"
    return any(path == p or path.startswith(p + "/") for p in _SKIP_PATH_PREFIXES)


def find_candidates(
    conn: sqlite3.Connection, doc: Document, *, limit: int = 5
) -> list[str]:
    """문서 본문에서 1홉 후보 URL 목록(정규화, dedup, 상한)."""
    seen_canon: set[str] = set()
    if doc.canonical_url:
        seen_canon.add(doc.canonical_url)
    if doc.url:
        seen_canon.add(canonicalize_url(doc.url))

    # href 로 추출된 링크(web fetcher)가 있으면 우선, 없으면 본문 텍스트 스캔.
    raw_links = list(doc.meta.get("links", [])) if doc.meta else []
    raw_links += _URL_RE.findall(doc.raw_text or "")

    out: list[str] = []
    for raw in raw_links:
        raw = raw.rstrip(".,;")
        host = _host(raw)
        if _is_blocked(raw, host):
            continue
        canon = canonicalize_url(raw)
        if canon in seen_canon:
            continue
        # 이미 그래프에 적재된 문서면 제외
        if _already_ingested(conn, canon):
            seen_canon.add(canon)
            continue
        seen_canon.add(canon)
        out.append(raw)
        if len(out) >= limit:
            break
    return out


def _already_ingested(conn: sqlite3.Connection, canonical_url: str) -> bool:
    row = conn.execute(
        "SELECT 1 FROM documents WHERE canonical_url=? LIMIT 1", (canonical_url,)
    ).fetchone()
    if row is not None:
        return True
    # 1홉 병합(ONEHOP_MERGE_DESIGN.md)은 새 Document 를 안 만들어 위 색인으로는 못 잡힘 —
    # 이미 어떤 문서에 부가 출처로 흡수된 URL 도 재제안 방지 대상.
    from ..store.db import find_document_by_extra_source

    return find_document_by_extra_source(conn, canonical_url) is not None
