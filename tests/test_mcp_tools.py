"""MCP 툴 구현(mcp_tools.py) — 순수 함수 단위 테스트. 프로토콜/인증 경계는
test_api.py(아이오홉프 게이트)에서 별도로 검증한다."""

from __future__ import annotations

import sqlite3

from claire.api.mcp_tools import (
    context_impl,
    document_impl,
    neighbors_impl,
    overview_impl,
    path_impl,
    resolve_entity_impl,
    search_impl,
)
from claire.ontology.base import Document, Entity, Relation
from claire.store import db as dbm


def _db():
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    dbm.init_db(conn)
    return conn


def _seed_graph(conn):
    dbm.insert_document(conn, Document(
        id="d1", url="https://example.com/x", title="X",
        raw_text="MCP", source_type="web", content_hash="h1"))
    dbm.upsert_entity(conn, Entity(id="e_mcp", type="Concept", name="MCP",
                                   aliases=["Model Context Protocol"],
                                   observations=["a", "b", "c", "d"], sources=["d1"]))
    dbm.upsert_entity(conn, Entity(id="e_cc", type="Product", name="Claude Code",
                                   sources=["d1"]))
    dbm.upsert_entity(conn, Entity(id="e_an", type="Org", name="Anthropic", sources=["d1"]))
    dbm.upsert_entity(conn, Entity(id="e_far", type="Product", name="Unrelated Thing",
                                   sources=["d1"]))
    dbm.upsert_relation(conn, Relation(id="r1", type="uses",
                                        source_id="e_cc", target_id="e_mcp"))
    dbm.upsert_relation(conn, Relation(id="r2", type="develops",
                                        source_id="e_an", target_id="e_cc"))


def test_resolve_entity_exact_and_alias():
    conn = _db()
    _seed_graph(conn)
    r = resolve_entity_impl(conn, "MCP")
    assert [m["id"] for m in r["matches"]] == ["e_mcp"]
    r2 = resolve_entity_impl(conn, "Model Context Protocol")
    assert [m["id"] for m in r2["matches"]] == ["e_mcp"]


def test_resolve_entity_fuzzy_fallback():
    conn = _db()
    _seed_graph(conn)
    r = resolve_entity_impl(conn, "Claude")  # 정확/별칭 매치 없음 -> FTS 폴백
    ids = [m["id"] for m in r["matches"]]
    assert "e_cc" in ids


def test_resolve_entity_no_match():
    conn = _db()
    _seed_graph(conn)
    r = resolve_entity_impl(conn, "존재하지않는이름ZZZ")
    assert r["matches"] == []


def test_search_entity_type_filter():
    conn = _db()
    _seed_graph(conn)
    # "Anthropic" 자체는 Org 타입 — 타입 필터가 일치하면 나오고 불일치하면 걸러짐.
    r_match = search_impl(conn, "Anthropic", entity_type="Org")
    assert [h["id"] for h in r_match["hits"]] == ["e_an"]
    r_mismatch = search_impl(conn, "Anthropic", entity_type="Product")
    assert r_mismatch["hits"] == []


def test_search_near_ids_filter():
    conn = _db()
    _seed_graph(conn)
    # "Anthropic" 은 e_mcp 의 이웃이 아님(e_cc 를 통해서만 연결) -> near_ids=[e_mcp] 로는 안 잡힘
    r = search_impl(conn, "Anthropic", near_ids=["e_mcp"])
    assert r["hits"] == []
    # e_cc 를 근방에 포함하면 잡힘
    r2 = search_impl(conn, "Anthropic", near_ids=["e_cc"])
    assert any(h["id"] == "e_an" for h in r2["hits"])


def test_search_truncation_flag_not_silent():
    conn = _db()
    _seed_graph(conn)
    r = search_impl(conn, "Anthropic", limit=0)
    # limit=0 이라도 매치 자체는 있었다는 게 truncated/omitted 로 드러나야 함
    assert r["hits"] == [] and r["truncated"] is True and r["omitted"] == 1


def test_search_widens_headroom_when_filters_set(monkeypatch):
    """advisor 지적 회귀 테스트 — entity_type/near_ids 필터를 걸 때 FTS 후보
    풀(headroom)이 좁으면 실제로 있는 매치가 상위 랭크 밖으로 밀려 거짓음성
    (0건인데 실제로는 있음)이 날 수 있다. BM25 랭킹 자체는 흔한 단어보다
    희귀 단어를 더 우대하는 경향이라 end-to-end로 거짓음성을 결정론적으로
    재현하기 어려워, `dbm.fts_search`에 전달되는 limit(=headroom)이 필터
    유무에 따라 실제로 넓어지는지를 직접 스파이로 확인한다."""
    conn = _db()
    _seed_graph(conn)
    calls = []
    real_fts_search = dbm.fts_search

    def spy(conn_, query, limit=20):
        calls.append(limit)
        return real_fts_search(conn_, query, limit=limit)

    monkeypatch.setattr(dbm, "fts_search", spy)

    search_impl(conn, "Anthropic", limit=8)  # 필터 없음
    assert calls[-1] < 200

    search_impl(conn, "Anthropic", entity_type="Org", limit=8)
    assert calls[-1] >= 200

    search_impl(conn, "Anthropic", near_ids=["e_cc"], limit=8)
    assert calls[-1] >= 200


def test_neighbors_degree_sort_and_via():
    conn = _db()
    _seed_graph(conn)
    r = neighbors_impl(conn, "e_cc")
    ids = [n["id"] for n in r["neighbors"]]
    assert set(ids) == {"e_mcp", "e_an"}
    assert r["truncated"] is False and r["omitted"] == 0


def test_neighbors_exclude_ids_and_seed_not_returned():
    conn = _db()
    _seed_graph(conn)
    r = neighbors_impl(conn, ["e_cc", "e_mcp"])
    ids = {n["id"] for n in r["neighbors"]}
    assert "e_cc" not in ids and "e_mcp" not in ids  # 시드 자신은 제외
    assert "e_an" in ids
    r2 = neighbors_impl(conn, "e_cc", exclude_ids=["e_an"])
    assert "e_an" not in {n["id"] for n in r2["neighbors"]}


def test_neighbors_truncation_flag_not_silent():
    conn = _db()
    _seed_graph(conn)
    r = neighbors_impl(conn, "e_cc", limit=1)
    assert r["truncated"] is True
    assert r["omitted"] == 1
    assert len(r["neighbors"]) == 1


def test_path_found_and_directions():
    conn = _db()
    _seed_graph(conn)
    r = path_impl(conn, "e_an", "e_mcp")
    assert r["found"] is True
    assert [n["id"] for n in r["path"]] == ["e_an", "e_cc", "e_mcp"]
    assert [rel["type"] for rel in r["relations"]] == ["develops", "uses"]


def test_path_not_found():
    conn = _db()
    _seed_graph(conn)
    r = path_impl(conn, "e_an", "e_far")
    assert r["found"] is False


def test_path_same_node():
    conn = _db()
    _seed_graph(conn)
    r = path_impl(conn, "e_mcp", "e_mcp")
    assert r["found"] is True and len(r["path"]) == 1


def test_context_compact_default_true_trims_observations_and_drops_summary():
    conn = _db()
    _seed_graph(conn)
    r_compact = context_impl(conn, ["e_mcp"])  # 기본 compact=True
    assert r_compact["context"].count("관찰:") == 1
    # 관찰 4개 중 앞 3개만(compact) -> "d"(4번째)는 텍스트에 없어야 함
    assert " d" not in r_compact["context"].split("관찰:")[1].split("\n")[0]
    r_full = context_impl(conn, ["e_mcp"], compact=False)
    assert " d" in r_full["context"].split("관찰:")[1].split("\n")[0]


def test_context_caps_entity_count():
    conn = _db()
    _seed_graph(conn)
    many_ids = ["e_mcp", "e_cc", "e_an", "e_far"] * 5  # 20개, 상한 10
    r = context_impl(conn, many_ids)
    assert r["truncated"] is True
    assert r["omitted"] == 10


def test_overview_counts():
    conn = _db()
    _seed_graph(conn)
    r = overview_impl(conn)
    types = {t["type"]: t["count"] for t in r["entity_types"]}
    assert types.get("Product") == 2 and types.get("Concept") == 1 and types.get("Org") == 1
    assert r["hubs"][0]["degree"] >= r["hubs"][-1]["degree"]  # 내림차순


def test_document_impl_does_not_mark_seen():
    """읽기전용 원칙 — 사람용 웹 라우트(server.py document_detail_route)는
    조회 시 set_document_seen(seen=True)를 같이 부르지만, MCP 툴은 graphview.
    document_detail만 호출해 이 부작용을 상속하지 않는다(docs/MCP_SUPPORT.md §6)."""
    conn = _db()
    _seed_graph(conn)
    dbm.set_document_seen(conn, "d1", seen=False)  # 안읽음 상태로 명시 설정
    row_before = dbm.get_document_row(conn, "d1")
    assert row_before["seen"] == 0

    rep = document_impl(conn, "d1")
    assert rep["id"] == "d1"

    row_after = dbm.get_document_row(conn, "d1")
    assert row_after["seen"] == 0  # 여전히 0 — document_impl이 안 건드림


def test_document_impl_not_found():
    conn = _db()
    _seed_graph(conn)
    assert document_impl(conn, "no-such-doc") == {"error": "not found"}
