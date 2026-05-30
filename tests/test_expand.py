"""1홉 자동 확장 후보 탐지 (M4) 테스트 — 네트워크 불필요."""

from __future__ import annotations

import sqlite3

from claire.ontology.base import Document
from claire.store import db as dbm
from claire.expand.onehop import find_candidates
from claire.ingest.pipeline import ingest
from claire.extract.provider import MockProvider
from claire.store.vectors import VectorStore


def _db():
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    dbm.init_db(conn)
    return conn


def test_find_candidates_filters_and_dedup():
    conn = _db()
    doc = Document(
        url="https://geeknews.io/post/1",
        canonical_url="https://geeknews.io/post/1",
        raw_text=(
            "See https://github.com/D4Vinci/Scrapling and "
            "https://example.com/article?utm_source=x . "
            "Ignore https://x.com/foo and https://youtube.com/watch?v=abc and "
            "the self link https://www.geeknews.io/post/1 ."
        ),
        source_type="web",
        content_hash="h1",
    )
    cands = find_candidates(conn, doc, limit=5)
    # github, example.com 만 남고 x.com/youtube/self 는 제외
    assert any("github.com/D4Vinci/Scrapling" in c for c in cands)
    assert any("example.com/article" in c for c in cands)
    assert not any("x.com" in c for c in cands)
    assert not any("youtube.com" in c for c in cands)
    assert not any("geeknews.io/post/1" in c for c in cands)


def test_candidates_exclude_already_ingested():
    conn = _db()
    # 기존에 example.com/known 적재됨
    dbm.insert_document(conn, Document(
        url="https://example.com/known",
        canonical_url="https://example.com/known",
        raw_text="x", source_type="web", content_hash="known",
    ))
    doc = Document(
        url="https://src/post", canonical_url="https://src/post",
        raw_text="link https://example.com/known and https://example.com/fresh",
        source_type="web", content_hash="h2",
    )
    cands = find_candidates(conn, doc, limit=5)
    assert any("example.com/fresh" in c for c in cands)
    assert not any("example.com/known" in c for c in cands)


def test_pipeline_reports_candidates_when_expand_max_set():
    conn = _db()
    vstore = VectorStore(conn, "brute")
    doc = Document(
        url="https://geeknews.io/p", canonical_url="https://geeknews.io/p",
        title="post", raw_text="ref https://github.com/a/b here",
        source_type="web", content_hash="hp",
    )
    rep = ingest("x", conn=conn, provider=MockProvider(), vstore=vstore,
                 fetch_fn=lambda p: doc, expand_max=3)
    assert any("github.com/a/b" in c for c in rep.candidates)
    assert "관련 링크" in rep.telegram_summary()


def test_pipeline_no_candidates_when_expand_max_zero():
    conn = _db()
    vstore = VectorStore(conn, "brute")
    doc = Document(
        url="https://geeknews.io/p", raw_text="ref https://github.com/a/b",
        source_type="web", content_hash="hp0",
    )
    rep = ingest("x", conn=conn, provider=MockProvider(), vstore=vstore,
                 fetch_fn=lambda p: doc, expand_max=0)
    assert rep.candidates == []
