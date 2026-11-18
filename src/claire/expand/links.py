"""Link expansion: the link filters that the expansion proposal and the automatic expansion share.

The filters remove noise hosts, boilerplate paths, and URLs that the knowledge base already has (a document, a tombstone, or an extra source of a same-subject source merge). Terms: docs/implementation/GLOSSARY.md.
"""

from __future__ import annotations

import re
import sqlite3
from urllib.parse import urlsplit

URL_RE = re.compile(r"https?://[^\s)\]\}<>\"']+")

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
# 확장 제안은 '제안'일 뿐이라 과소제안의 해는 작고, 잡음 제안이 더 거슬린다(사용자 지적).
_SKIP_PATH_PREFIXES = (
    "/about", "/help", "/donate", "/login", "/signin", "/signup", "/register",
    "/privacy", "/terms", "/tos", "/legal", "/policies", "/contact", "/abuse",
    "/subscribe", "/newsletter", "/rss", "/feed", "/sitemap", "/static",
    "/assets", "/cdn-cgi",
)


def host_of(url: str) -> str:
    h = urlsplit(url).netloc.lower()
    return h.removeprefix("www.")


def is_blocked(url: str, host: str) -> bool:
    """잡음 호스트(정확/서브도메인) 또는 boilerplate 경로면 후보에서 제외."""
    if not host or host in _SKIP_HOSTS:
        return True
    if any(host == s or host.endswith("." + s) for s in _SKIP_HOST_SUFFIXES):
        return True
    path = urlsplit(url).path.rstrip("/").lower() or "/"
    return any(path == p or path.startswith(p + "/") for p in _SKIP_PATH_PREFIXES)


def already_ingested(conn: sqlite3.Connection, canonical_url: str, kb_id: int | None = None) -> bool:
    from ..store.db import find_document_by_extra_source, is_tombstoned

    if is_tombstoned(conn, canonical_url=canonical_url, kb_id=kb_id):
        return True
    if kb_id is not None:
        row = conn.execute(
            "SELECT 1 FROM documents WHERE canonical_url=? AND kb_id=? LIMIT 1", (canonical_url, kb_id)
        ).fetchone()
    else:
        row = conn.execute(
            "SELECT 1 FROM documents WHERE canonical_url=? LIMIT 1", (canonical_url,)
        ).fetchone()
    if row is not None:
        return True
    # 동일 주제 출처 병합(docs/implementation/EXPANSION.md)은 새 Document 를 안 만들어 위 색인으로는 못 잡힘 —
    # 이미 어떤 문서에 부가 출처로 흡수된 URL 도 재제안 방지 대상.
    return find_document_by_extra_source(conn, canonical_url, kb_id=kb_id) is not None
