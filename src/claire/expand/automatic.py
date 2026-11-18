"""Automatic expansion: the LLM selects links of the parent document, and each selected link that passes the expansion gate is ingested.

Flow: the candidate links of the parent (with anchor text) → the heuristic filter (``expand.links``: noise, documents that the knowledge base has) → ``provider.select_followups`` (the LLM selects what to follow) → fetch → ``provider.judge_gate`` (relevance and quality against the parent context) → ``expand.gate.passes_gate`` → a normal ingest with ``source="expand:<parent id>"`` and ``expand_max=0``. The depth is always one. A candidate that does not pass is discarded.

``IngestService.expand_document`` runs the flow. Current behavior: docs/implementation/EXPANSION.md.
"""

from __future__ import annotations

import sqlite3

from ..ingest.normalize import canonicalize_url
from ..ontology.base import Document
from ..store import db as dbm
from .links import already_ingested, host_of, is_blocked

# LLM 선별 전에 제시할 후보 상한. 너무 많으면 토큰 낭비, 너무 적으면 선택지 부족.
PREFILTER_CAP = 30


def build_candidates(conn: sqlite3.Connection, doc: Document, *, limit: int = PREFILTER_CAP, kb_id: int | None = None
                     ) -> list[dict]:
    """부모 문서 meta 에서 (url, anchor) 후보 — 휴리스틱 사전필터 + dedup + 상한.

    LLM 선별 전 단계: 명백한 잡음 호스트/경로(is_blocked)와 이미 적재된 URL 을 미리 쳐내 토큰을 아낀다. 앵커 텍스트는 link_anchors(신규 fetch) 에서, 없으면 빈 문자열.
    """
    seen: set[str] = set()
    if doc.canonical_url:
        seen.add(doc.canonical_url)
    if doc.url:
        seen.add(canonicalize_url(doc.url))

    anchors = {a.get("url"): a.get("anchor", "")
               for a in (doc.meta.get("link_anchors") or []) if a.get("url")}
    raw_links = list(doc.meta.get("links", [])) if doc.meta else []
    from ..ingest.fetchers.odt import prioritize_odt_links

    raw_links = prioritize_odt_links(raw_links)

    out: list[dict] = []
    for raw in raw_links:
        raw = raw.rstrip(".,;")
        host = host_of(raw)
        if is_blocked(raw, host):
            continue
        canon = canonicalize_url(raw)
        if canon in seen:
            continue
        seen.add(canon)
        if already_ingested(conn, canon, kb_id=kb_id):
            continue
        out.append({"url": raw, "anchor": anchors.get(raw, "")})
        if len(out) >= limit:
            break
    return out


def build_parent_context(conn: sqlite3.Connection, doc: Document, kb_id: int | None = None) -> str:
    """선별·판정용 부모 맥락 = 제목 + 추출 요약 + 가독 렌더(detail) 일부."""
    summary = dbm.latest_extraction_summary(conn, doc.id, kb_id=kb_id) or ""
    detail = dbm.get_document_detail(conn, doc.id, kb_id=kb_id) or ""
    parts = [f"제목: {doc.title or '(제목 없음)'}"]
    if summary:
        parts.append(f"요약: {summary}")
    if detail:
        parts.append(detail[:2000])
    elif doc.raw_text:
        parts.append(doc.raw_text[:2000])
    return "\n".join(parts)
