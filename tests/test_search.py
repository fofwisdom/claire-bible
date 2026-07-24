"""검색/정리 (M5) 테스트 — mock provider 로 하이브리드 융합·컨텍스트 검증."""

from __future__ import annotations

import sqlite3

from claire.ontology.base import Entity, Relation
from claire.store import db as dbm
from claire.store.vectors import VectorStore
from claire.extract.provider import MockProvider
from claire.retrieval.query import search, _rrf_fuse, _build_context, SearchHit


def _db():
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    dbm.init_db(conn)
    return conn


def test_rrf_fuse_combines_ranks():
    fused = _rrf_fuse(["a", "b"], [("b", 0.9), ("c", 0.5)])
    # b 는 양쪽에 있으니 최상위
    top = max(fused, key=fused.get)
    assert top == "b"
    assert set(fused) == {"a", "b", "c"}


def test_search_returns_hits_via_fts():
    conn = _db()
    vstore = VectorStore(conn, "brute")
    e = Entity(type="Framework", name="Scrapling",
               observations=["adaptive web scraping framework"])
    dbm.upsert_entity(conn, e)

    res = search(conn, vstore, MockProvider(), "scraping", summarize=False)
    assert any(h.entity.name == "Scrapling" for h in res.hits)
    assert any("fts" in h.via for h in res.hits)


def test_search_summarize_uses_provider_when_available():
    conn = _db()
    vstore = VectorStore(conn, "brute")
    e = Entity(type="Tool", name="Claude Code", observations=["cli coding agent"])
    dbm.upsert_entity(conn, e)

    class P(MockProvider):
        def summarize_search(self, query, context):
            return f"SUMMARY of {query}; ctx_has_claude={'Claude Code' in context}"

    res = search(conn, vstore, P(), "claude", summarize=True)
    assert res.answer and res.answer.startswith("SUMMARY")
    assert "ctx_has_claude=True" in res.answer


def test_build_context_includes_neighbors():
    conn = _db()
    a = Entity(type="Repo", name="graphify", observations=["kg generator"])
    b = Entity(type="Org", name="safishamsi")
    dbm.upsert_entity(conn, a)
    dbm.upsert_entity(conn, b)
    dbm.upsert_relation(conn, Relation(type="authored_by", source_id=a.id, target_id=b.id))

    ctx = _build_context(conn, [SearchHit(entity=a, score=1.0, via=["fts"])])
    assert "graphify" in ctx
    assert "authored_by" in ctx
    assert "safishamsi" in ctx


def test_search_empty_when_no_match():
    conn = _db()
    vstore = VectorStore(conn, "brute")
    res = search(conn, vstore, MockProvider(), "nonexistent-xyz", summarize=False)
    assert res.hits == []
    assert "없음" in res.telegram_text()
