"""검색 + LLM 정리 (M5).

흐름(search.md 축소판):
  query → 하이브리드 후보(FTS BM25 + 벡터 cosine, RRF 융합)
        → 그래프 이웃 1홉 확장(연결 맥락 보강)
        → Gemini 정리(검색된 컨텍스트만 사용, 인용 포함)

provider 가 embed/generate 를 못 하면(mock) LLM 정리는 생략하고 후보 리스트만 반환.
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass, field
from typing import Literal

from ..ontology.base import Entity
from ..store import db as dbm
from ..store.vectors import VectorStore

# Reciprocal Rank Fusion 상수.
RRF_K = 60
SearchMode = Literal["hybrid", "fts"]


@dataclass
class SearchHit:
    entity: Entity
    score: float
    via: list[str] = field(default_factory=list)  # 'fts' | 'vec'


@dataclass
class SearchResult:
    query: str
    hits: list[SearchHit] = field(default_factory=list)
    answer: str | None = None  # LLM 정리(있으면)
    citations: list[str] = field(default_factory=list)

    def telegram_text(self) -> str:
        if not self.hits:
            return f"'{self.query}' 검색 결과 없음."
        parts = []
        if self.answer:
            parts.append(self.answer.strip())
            parts.append("")
        parts.append("📚 관련 항목:")
        for h in self.hits[:8]:
            parts.append(f"• [{h.entity.type}] {h.entity.name}")
        return "\n".join(parts)


def _rrf_fuse(fts_ids: list[str], vec_ranked: list[tuple[str, float]]) -> dict[str, float]:
    """FTS 랭킹과 벡터 랭킹을 Reciprocal Rank Fusion 으로 융합."""
    scores: dict[str, float] = {}
    for rank, eid in enumerate(fts_ids):
        scores[eid] = scores.get(eid, 0.0) + 1.0 / (RRF_K + rank + 1)
    for rank, (eid, _s) in enumerate(vec_ranked):
        scores[eid] = scores.get(eid, 0.0) + 1.0 / (RRF_K + rank + 1)
    return scores


def search(
    conn: sqlite3.Connection,
    vstore: VectorStore | None,
    provider,  # noqa: ANN001
    query: str,
    *,
    limit: int = 8,
    summarize: bool = True,
    mode: SearchMode = "hybrid",
    include_hidden: bool = True,
) -> SearchResult:
    if mode not in {"hybrid", "fts"}:
        raise ValueError(f"unsupported search mode: {mode}")
    if mode == "fts" and summarize:
        raise ValueError("fts search does not support summaries")

    res = SearchResult(query=query)

    # 1) FTS 후보
    fts_ids = dbm.fts_search(conn, query, limit=20)

    # 2) 벡터 후보 (provider 가 임베딩 가능할 때만)
    vec_ranked: list[tuple[str, float]] = []
    if mode == "hybrid":
        if vstore is None or provider is None:
            raise ValueError("hybrid search requires a provider and vector store")
        try:
            qvec = provider.embed(query)
            vec_ranked = vstore.search(qvec, limit=20)
        except Exception:  # noqa: BLE001
            vec_ranked = []

    via_map: dict[str, list[str]] = {}
    for eid in fts_ids:
        via_map.setdefault(eid, []).append("fts")
    for eid, _ in vec_ranked:
        via_map.setdefault(eid, []).append("vec")

    fused = _rrf_fuse(fts_ids, vec_ranked)
    if not fused:
        return res

    hidden_doc_ids = set() if include_hidden else dbm.hidden_document_ids(conn)
    ordered = sorted(fused.items(), key=lambda kv: kv[1], reverse=True)
    for eid, score in ordered:
        ent = dbm.get_entity(conn, eid)
        if ent:
            if not include_hidden and hidden_doc_ids and ent.sources:
                if not any(s not in hidden_doc_ids for s in ent.sources):
                    continue
            res.hits.append(SearchHit(entity=ent, score=score, via=via_map.get(eid, [])))
            if len(res.hits) >= limit:
                break

    # 3) LLM 정리 (옵션) — 검색된 엔티티 + 1홉 이웃을 컨텍스트로
    if (
        mode == "hybrid"
        and summarize
        and res.hits
        and hasattr(provider, "summarize_search")
    ):
        context = _build_context(conn, res.hits, include_hidden=include_hidden)
        try:
            answer = provider.summarize_search(query, context)
            res.answer = answer
        except Exception:  # noqa: BLE001
            res.answer = None
    return res


def _build_context(
    conn: sqlite3.Connection,
    hits: list[SearchHit],
    *,
    include_hidden: bool = True,
) -> str:
    """검색된 엔티티 + 1홉 이웃을 인용 가능한 텍스트 블록으로."""
    lines = []
    hidden_doc_ids = set() if include_hidden else dbm.hidden_document_ids(conn)
    for h in hits:
        e = h.entity
        lines.append(f"[{e.name}] (type={e.type})")
        for o in e.observations[:4]:
            lines.append(f"  - {o}")
        # 1홉 이웃(관계)
        for r in dbm.neighbors(conn, e.id)[:6]:
            other_id = r.target_id if r.source_id == e.id else r.source_id
            other = dbm.get_entity(conn, other_id)
            if other:
                if not include_hidden and hidden_doc_ids and other.sources:
                    if not any(s not in hidden_doc_ids for s in other.sources):
                        continue
                arrow = "->" if r.source_id == e.id else "<-"
                lines.append(f"  rel: {e.name} {arrow}{r.type}{arrow} {other.name}")
    return "\n".join(lines)
