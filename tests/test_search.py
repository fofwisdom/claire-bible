"""검색/정리 (M5) 테스트 — mock provider 로 하이브리드 융합·컨텍스트 검증."""

from __future__ import annotations

import sqlite3
from pathlib import Path
from types import SimpleNamespace

import pytest

from claire.extract.provider import MockProvider
from claire.ingest import service as ingest_service_module
from claire.ingest.service import IngestService
from claire.ontology.base import Entity, Relation
from claire.retrieval.query import SearchHit, _build_context, _rrf_fuse, search
from claire.store import db as dbm
from claire.store.vectors import VectorStore


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


def test_fts_search_never_touches_provider_or_vector_store():
    conn = _db()
    entity = Entity(
        type="Framework",
        name="Scrapling",
        observations=["adaptive web scraping framework"],
    )
    dbm.upsert_entity(conn, entity)
    calls = {"embed": 0, "summary": 0, "vector": 0}

    class ProviderSpy:
        def embed(self, _query):
            calls["embed"] += 1
            raise AssertionError("fts mode must not embed")

        def summarize_search(self, _query, _context):
            calls["summary"] += 1
            raise AssertionError("fts mode must not summarize")

    class VectorStoreSpy:
        def search(self, _query_vec, *, limit):
            calls["vector"] += 1
            raise AssertionError("fts mode must not search vectors")

    res = search(
        conn,
        VectorStoreSpy(),
        ProviderSpy(),
        "scraping",
        summarize=False,
        mode="fts",
    )

    assert any(hit.entity.name == "Scrapling" for hit in res.hits)
    assert calls == {"embed": 0, "summary": 0, "vector": 0}


def test_fts_search_rejects_summary_and_unknown_mode():
    conn = _db()

    with pytest.raises(ValueError, match="does not support summaries"):
        search(conn, None, None, "faith", summarize=True, mode="fts")
    with pytest.raises(ValueError, match="unsupported search mode"):
        search(conn, None, None, "faith", summarize=False, mode="unknown")


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


def test_service_fts_search_is_readonly_and_has_no_initialization_side_effects(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    db_path = tmp_path / "claire.db"
    setup_conn = dbm.connect(db_path)
    dbm.init_db(setup_conn)
    dbm.upsert_entity(
        setup_conn,
        Entity(
            type="Concept",
            name="Faith",
            observations=["trust and confidence"],
        ),
    )
    before = list(setup_conn.iterdump())
    setup_conn.close()

    provider_calls = {"embed": 0, "summary": 0}

    class ProviderSpy:
        def embed(self, _query):
            provider_calls["embed"] += 1
            raise AssertionError("fts mode must not embed")

        def summarize_search(self, _query, _context):
            provider_calls["summary"] += 1
            raise AssertionError("fts mode must not summarize")

    svc = object.__new__(IngestService)
    svc.s = SimpleNamespace(db_file=db_path, vector_backend="brute")
    svc.provider = ProviderSpy()

    original_connect_existing = dbm.connect_existing
    readonly_flags: list[bool] = []

    def tracked_connect_existing(path, *, readonly=False):
        readonly_flags.append(readonly)
        return original_connect_existing(path, readonly=readonly)

    def unexpected_side_effect(*_args, **_kwargs):
        raise AssertionError("search must not initialize or create storage")

    monkeypatch.setattr(dbm, "connect_existing", tracked_connect_existing)
    monkeypatch.setattr(dbm, "connect", unexpected_side_effect)
    monkeypatch.setattr(dbm, "init_db", unexpected_side_effect)
    monkeypatch.setattr(
        ingest_service_module,
        "make_vector_store",
        unexpected_side_effect,
    )

    result = svc.search("faith", summarize=False, mode="fts")

    after_conn = original_connect_existing(db_path, readonly=True)
    try:
        after = list(after_conn.iterdump())
    finally:
        after_conn.close()

    assert any(hit.entity.name == "Faith" for hit in result.hits)
    assert readonly_flags == [True]
    assert provider_calls == {"embed": 0, "summary": 0}
    assert after == before


def test_service_hybrid_search_is_readonly_without_initialization_side_effects(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    db_path = tmp_path / "claire.db"
    setup_conn = dbm.connect(db_path)
    dbm.init_db(setup_conn)
    dbm.upsert_entity(
        setup_conn,
        Entity(
            type="Concept",
            name="Hope",
            observations=["confident expectation"],
        ),
    )
    before = list(setup_conn.iterdump())
    setup_conn.close()

    class ProviderSpy(MockProvider):
        def __init__(self):
            self.embed_calls = 0

        def embed(self, text):
            self.embed_calls += 1
            return super().embed(text)

    provider = ProviderSpy()
    svc = object.__new__(IngestService)
    svc.s = SimpleNamespace(db_file=db_path, vector_backend="brute")
    svc.provider = provider

    original_connect_existing = dbm.connect_existing
    readonly_flags: list[bool] = []

    def tracked_connect_existing(path, *, readonly=False):
        readonly_flags.append(readonly)
        return original_connect_existing(path, readonly=readonly)

    def unexpected_initialization(*_args, **_kwargs):
        raise AssertionError("search must not initialize or create storage")

    monkeypatch.setattr(dbm, "connect_existing", tracked_connect_existing)
    monkeypatch.setattr(dbm, "connect", unexpected_initialization)
    monkeypatch.setattr(dbm, "init_db", unexpected_initialization)

    result = svc.search("hope", summarize=False, mode="hybrid")

    after_conn = original_connect_existing(db_path, readonly=True)
    try:
        after = list(after_conn.iterdump())
    finally:
        after_conn.close()

    assert any(hit.entity.name == "Hope" for hit in result.hits)
    assert readonly_flags == [True]
    assert provider.embed_calls == 1
    assert after == before


def test_service_rejects_unknown_mode_before_opening_storage(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    svc = object.__new__(IngestService)
    svc.s = SimpleNamespace(
        db_file=tmp_path / "must-not-exist.db",
        vector_backend="brute",
    )
    svc.provider = MockProvider()

    def unexpected_storage_access(*_args, **_kwargs):
        raise AssertionError("invalid mode must fail before storage access")

    monkeypatch.setattr(dbm, "connect_existing", unexpected_storage_access)
    monkeypatch.setattr(
        ingest_service_module,
        "make_vector_store",
        unexpected_storage_access,
    )

    with pytest.raises(ValueError, match="unsupported search mode"):
        svc.search("faith", summarize=False, mode="unknown")

    assert not svc.s.db_file.exists()
