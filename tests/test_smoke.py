"""M0 스모크 테스트 — 외부 키/네트워크 없이 핵심 배선 검증."""

from __future__ import annotations

import sqlite3

from claire.extract.provider import MockProvider
from claire.ontology.base import Document, Entity, Relation, normalize_name
from claire.ontology.registry import (
    classify_entity_type,
    classify_relation_type,
    ontology_prompt_block,
    validate_relation,
)
from claire.store import db as dbm
from claire.store.vectors import VectorStore, pack_vector, unpack_vector
from claire.telegram_bot import classify_input

# --- ontology / registry ---

def test_normalize_name():
    assert normalize_name("  Claude   Code ") == "claude code"
    assert normalize_name("GPT-4") == "gpt-4"           # 내부 하이픈 보존
    assert normalize_name("Scrapling]") == "scrapling"  # 양끝 구두점 노이즈 제거
    assert normalize_name('"Letta".') == "letta"


def test_classify_known_and_provisional_types():
    assert classify_entity_type("Tool") == ("Tool", False)
    assert classify_entity_type("tool") == ("Tool", False)  # 대소문자 무시
    name, prov = classify_entity_type("Spaceship")
    assert prov and name == "Spaceship"

    assert classify_relation_type("uses") == ("uses", False)
    name, prov = classify_relation_type("rivals_with")
    assert prov and name == "rivals_with"


def test_validate_relation_domain_range():
    # authored_by 의 range 는 Person/Org. Tool 타겟은 위반.
    bad = validate_relation("authored_by", "Repo", "Tool")
    assert not bad.ok
    good = validate_relation("authored_by", "Repo", "Person")
    assert good.ok and not good.provisional
    # provisional 관계는 통과
    prov = validate_relation("forks_from", "Repo", "Repo")
    assert prov.ok and prov.provisional


def test_ontology_prompt_block_mentions_types():
    block = ontology_prompt_block()
    assert "Tool" in block and "authored_by" in block


# --- provider (mock) ---

def test_mock_extract_github():
    doc = Document(
        url="https://github.com/safishamsi/graphify",
        title="Graphify",
        raw_text="A knowledge graph generator.",
        source_type="web",
    )
    res = MockProvider().extract(doc, "")
    names = {e.name for e in res.entities}
    types = {e.type for e in res.entities}
    assert "Repo" in types
    assert "safishamsi" in names  # owner -> Org
    assert any(r.type == "authored_by" for r in res.relations)


def test_mock_embed_deterministic():
    p = MockProvider()
    a = p.embed("hello")
    b = p.embed("hello")
    c = p.embed("world")
    assert a == b
    assert a != c
    assert len(a) == MockProvider.EMBED_DIM


# --- store: db ---

def _mem_db() -> sqlite3.Connection:
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    dbm.init_db(conn)
    return conn


def test_db_init_and_counts():
    conn = _mem_db()
    c = dbm.counts(conn)
    assert set(c) == {"documents", "entities", "relations", "embeddings", "proposals",
                      "jobs", "raw_inbox", "extractions", "refresh_queue", "purged_tombstones"}
    assert all(v == 0 for v in c.values())


def test_entity_roundtrip_and_dedup_lookup():
    conn = _mem_db()
    ent = Entity(type="Tool", name="Claude Code", observations=["cli agent"])
    dbm.upsert_entity(conn, ent)
    got = dbm.get_entity(conn, ent.id)
    assert got is not None and got.name == "Claude Code"
    # 정규화 이름으로 재발견 (해소의 기반)
    found = dbm.find_entities_by_norm(conn, normalize_name("claude code"))
    assert len(found) == 1 and found[0].id == ent.id


def test_relation_unique_constraint():
    conn = _mem_db()
    a = Entity(type="Repo", name="graphify")
    b = Entity(type="Org", name="safishamsi")
    dbm.upsert_entity(conn, a)
    dbm.upsert_entity(conn, b)
    r1 = Relation(type="authored_by", source_id=a.id, target_id=b.id)
    r2 = Relation(type="authored_by", source_id=a.id, target_id=b.id)
    dbm.upsert_relation(conn, r1)
    dbm.upsert_relation(conn, r2)  # 중복 -> IGNORE
    assert dbm.counts(conn)["relations"] == 1
    nb = dbm.neighbors(conn, a.id)
    assert len(nb) == 1


def test_fts_search():
    conn = _mem_db()
    e = Entity(type="Framework", name="Scrapling", observations=["adaptive web scraping framework"])
    dbm.upsert_entity(conn, e)
    hits = dbm.fts_search(conn, "scraping")
    assert e.id in hits


def test_document_dedup_by_hash():
    conn = _mem_db()
    doc = Document(url="https://x", raw_text="abc", content_hash="h1")
    dbm.insert_document(conn, doc)
    assert dbm.find_document_by_hash(conn, "h1") == doc.id
    assert dbm.find_document_by_hash(conn, "nope") is None


# --- store: vectors ---

def test_vector_pack_unpack():
    v = [0.1, -0.5, 1.0, 0.0]
    out = unpack_vector(pack_vector(v))
    assert len(out) == 4
    assert abs(out[0] - 0.1) < 1e-6


def test_vector_search_brute():
    conn = _mem_db()
    vs = VectorStore(conn, backend="brute")
    p = MockProvider()
    vs.put("ent_a", p.embed("claude code agent"), "mock")
    vs.put("ent_b", p.embed("banana smoothie recipe"), "mock")
    q = p.embed("claude code agent")
    res = vs.search(q, limit=2)
    assert res[0][0] == "ent_a"  # 동일 텍스트가 1위
    assert res[0][1] > res[1][1]


# --- telegram input classification ---

def test_classify_input():
    assert classify_input("https://youtube.com/watch?v=x") == "youtube"
    assert classify_input("https://x.com/a/status/1") == "xcom"
    assert classify_input("https://share.google/abc") == "redirect"
    assert classify_input("https://example.com/post") == "web"
    assert classify_input("just a keyword") == "text"
    assert classify_input("") == "empty"
