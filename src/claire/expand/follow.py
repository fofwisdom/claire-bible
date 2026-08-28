"""1홉 자동확장 — 부모 문서의 링크를 LLM 이 선별→fetch→판정→통과 시 적재.

사용자 요구: 링크를 받으면 그 콘텐츠에서 찾은 추가 링크를 **1 depth 만** 더 파고들어
자동으로 함께 지식에 쌓되, **파고들지 여부와 쌓을지 여부를 모두 LLM 이 결정**한다.

흐름:
  부모 문서 → 후보 링크(앵커 포함) 수집 + 휴리스틱 사전필터(잡음/기존적재 제외, 토큰 절약)
  → provider.select_followups(맥락, 후보)            [파고들지 = LLM]
  → 선택 링크 fetch → provider.judge_research(부모 맥락 대비 relevance·quality)
     [쌓을지 = LLM, 맥락조사 게이트(research) 재사용]
  → relevance≥RELEVANCE_MIN ∧ quality≥QUALITY_MIN 통과 시에만 일반 ingest 로 적재
     (source='onehop:<부모>', expand_max=0 → 재귀 없이 깊이 1 고정).
미달이면 폐기 — 잡음/다의어가 그래프를 오염시키지 않게(보수적).

판정 게이트는 expand/research.py 의 임계를 재사용한다(동일 정책: 오염>빈약).
"""

from __future__ import annotations

import sqlite3

from ..ingest.normalize import canonicalize_url
from ..ontology.base import Document
from ..store import db as dbm
from .onehop import _already_ingested, _host, _is_blocked
from .research import QUALITY_MIN, RELEVANCE_MIN

# LLM 선별 전에 제시할 후보 상한. 너무 많으면 토큰 낭비, 너무 적으면 선택지 부족.
PREFILTER_CAP = 30


def build_candidates(conn: sqlite3.Connection, doc: Document, *, limit: int = PREFILTER_CAP
                     ) -> list[dict]:
    """부모 문서 meta 에서 (url, anchor) 후보 — 휴리스틱 사전필터 + dedup + 상한.

    LLM 선별 전 단계: 명백한 잡음 호스트/경로(_is_blocked)와 이미 적재된 URL 을 미리
    쳐내 토큰을 아낀다. 앵커 텍스트는 link_anchors(신규 fetch) 에서, 없으면 빈 문자열.
    """
    seen: set[str] = set()
    if doc.canonical_url:
        seen.add(doc.canonical_url)
    if doc.url:
        seen.add(canonicalize_url(doc.url))

    anchors = {a.get("url"): a.get("anchor", "")
               for a in (doc.meta.get("link_anchors") or []) if a.get("url")}
    raw_links = list(doc.meta.get("links", [])) if doc.meta else []

    out: list[dict] = []
    for raw in raw_links:
        raw = raw.rstrip(".,;")
        host = _host(raw)
        if _is_blocked(raw, host):
            continue
        canon = canonicalize_url(raw)
        if canon in seen:
            continue
        seen.add(canon)
        if _already_ingested(conn, canon):
            continue
        out.append({"url": raw, "anchor": anchors.get(raw, "")})
        if len(out) >= limit:
            break
    return out


def build_parent_context(conn: sqlite3.Connection, doc: Document) -> str:
    """선별·판정용 부모 맥락 = 제목 + 추출 요약 + 가독 렌더(detail) 일부."""
    summary = dbm.latest_extraction_summary(conn, doc.id) or ""
    detail = dbm.get_document_detail(conn, doc.id) or ""
    parts = [f"제목: {doc.title or '(제목 없음)'}"]
    if summary:
        parts.append(f"요약: {summary}")
    if detail:
        parts.append(detail[:2000])
    elif doc.raw_text:
        parts.append(doc.raw_text[:2000])
    return "\n".join(parts)


def passes_gate(relevance: float, quality: float) -> bool:
    """store 게이트(research 와 동일 정책). relevance(맥락 일치)가 더 엄격."""
    return relevance >= RELEVANCE_MIN and quality >= QUALITY_MIN
