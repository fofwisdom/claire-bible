"""Expansion proposal: the candidate links of an ingested document, when automatic expansion is off.

The Telegram bot shows the candidates as a button. When the user confirms, the bot ingests each URL with ``source="expand-proposal"`` and ``expand_max=0``, without an LLM gate. Cost control: a limit for each document (``expand_max``), no URL that the knowledge base already has, and no duplicate.
"""

from __future__ import annotations

import sqlite3

from ..ingest.normalize import canonicalize_url
from ..ontology.base import Document
from .links import URL_RE, already_ingested, host_of, is_blocked


def find_candidates(
    conn: sqlite3.Connection, doc: Document, *, limit: int = 5, kb_id: int | None = None,
) -> list[str]:
    """문서 본문에서 확장 제안 후보 URL 목록(정규화, dedup, 상한)."""
    seen_canon: set[str] = set()
    if doc.canonical_url:
        seen_canon.add(doc.canonical_url)
    if doc.url:
        seen_canon.add(canonicalize_url(doc.url))

    # href 로 추출된 링크(web fetcher)가 있으면 우선, 없으면 본문 텍스트 스캔.
    raw_links = list(doc.meta.get("links", [])) if doc.meta else []
    raw_links += URL_RE.findall(doc.raw_text or "")
    from ..ingest.fetchers.odt import prioritize_odt_links

    raw_links = prioritize_odt_links(raw_links)

    out: list[str] = []
    for raw in raw_links:
        raw = raw.rstrip(".,;")
        host = host_of(raw)
        if is_blocked(raw, host):
            continue
        canon = canonicalize_url(raw)
        if canon in seen_canon:
            continue
        # 이미 그래프에 적재된 문서면 제외
        if already_ingested(conn, canon, kb_id=kb_id):
            seen_canon.add(canon)
            continue
        seen_canon.add(canon)
        out.append(raw)
        if len(out) >= limit:
            break
    return out
