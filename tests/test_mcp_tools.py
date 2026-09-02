"""MCP 툴 구현(mcp_tools.py) — 순수 함수 단위 테스트. 프로토콜/인증 경계는
test_api_server.py / test_api_security.py 에서 별도로 검증한다."""

from __future__ import annotations

import sqlite3

from claire.api.mcp_tools import (
    MAX_DOCUMENTS,
    MAX_NODE_DOCUMENTS,
    _iso_utc,
    _parse_since,
    context_impl,
    document_impl,
    documents_impl,
    neighbors_impl,
    node_impl,
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
    assert r["hits"] == []
    assert r["truncated"] is True
    assert r["omitted"] >= 1


def test_neighbors_multi_seed_union_and_degree_sort():
    conn = _db()
    _seed_graph(conn)
    # e_mcp와 e_an 둘 다 시드로 주면 그들의 이웃(e_cc)이 합집합으로 한 번에 나와야 함.
    r = neighbors_impl(conn, ["e_mcp", "e_an"])
    ids = [n["id"] for n in r["neighbors"]]
    assert ids == ["e_cc"]
    # degree 포함
    assert r["neighbors"][0]["degree"] == 2


def test_neighbors_exclude_ids_breaks_cycles():
    conn = _db()
    _seed_graph(conn)
    # e_cc의 이웃은 e_mcp, e_an 둘 다. exclude_ids에 e_an을 넣으면 e_mcp만 나와야 함.
    r = neighbors_impl(conn, "e_cc", exclude_ids=["e_an"])
    ids = [n["id"] for n in r["neighbors"]]
    assert ids == ["e_mcp"]
    assert "e_an" not in ids


def test_neighbors_seed_itself_is_excluded_from_result():
    conn = _db()
    _seed_graph(conn)
    # e_mcp -> e_cc(이웃) -> e_mcp(시드 본인). 시드 본인은 이웃 목록에 안 나옴.
    r = neighbors_impl(conn, "e_mcp")
    assert "e_mcp" not in {n["id"] for n in r["neighbors"]}
    # 다중 시드에서도 시드 중 어느 것도 이웃 목록에 안 나옴.
    r2 = neighbors_impl(conn, ["e_mcp", "e_an"])
    assert "e_mcp" not in {n["id"] for n in r2["neighbors"]}
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
    document_detail만 호출해 이 부작용을 상속하지 않는다(docs/origin/design/MCP_SUPPORT.md)."""
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


def test_iso_utc_includes_explicit_offset():
    # 서버가 임의 지역(KST 등)을 가정하지 않고 항상 명시적 오프셋을 준다
    s = _iso_utc(1786751445.79)
    assert s is not None and (s.endswith("+00:00") or s.endswith("Z"))
    assert _iso_utc(None) is None


def test_parse_since_handles_date_and_z_suffix():
    assert _parse_since(None) is None
    ts_date = _parse_since("2026-08-15")
    ts_z = _parse_since("2026-08-15T00:00:00Z")
    assert ts_date == ts_z  # 둘 다 UTC 자정으로 해석


def test_document_impl_fetched_at_is_iso_with_offset():
    conn = _db()
    dbm.insert_document(conn, Document(
        id="d1", url="https://example.com/x", title="X", raw_text="MCP",
        source_type="web", content_hash="h1", fetched_at=1786751445.79))
    rep = document_impl(conn, "d1")
    assert rep["fetched_at"] == _iso_utc(1786751445.79)


def test_node_impl_document_fetched_at_is_iso_with_offset():
    conn = _db()
    dbm.insert_document(conn, Document(
        id="d1", url="https://example.com/x", title="X", raw_text="MCP",
        source_type="web", content_hash="h1", fetched_at=1786751445.79))
    dbm.upsert_entity(conn, Entity(id="e1", type="Concept", name="MCP", sources=["d1"]))
    rep = node_impl(conn, "e1")
    assert rep["documents"][0]["fetched_at"] == _iso_utc(1786751445.79)


def test_node_impl_not_found():
    conn = _db()
    assert node_impl(conn, "no-such-entity") == {"error": "not found"}


def test_node_impl_default_drops_full_detail_keeps_summary():
    conn = _db()
    dbm.insert_document(conn, Document(
        id="d1", url="https://example.com/x", title="X", raw_text="raw",
        source_type="web", content_hash="h1"))
    dbm.set_document_detail(conn, "d1", "매우 긴 상세" * 100)
    dbm.upsert_entity(conn, Entity(id="e1", type="Concept", name="MCP", sources=["d1"]))
    rep = node_impl(conn, "e1")
    assert "detail" not in rep["documents"][0]
    assert "summary" in rep["documents"][0]


def test_node_impl_full_true_includes_detail():
    conn = _db()
    dbm.insert_document(conn, Document(
        id="d1", url="https://example.com/x", title="X", raw_text="raw",
        source_type="web", content_hash="h1"))
    dbm.set_document_detail(conn, "d1", "상세")
    dbm.upsert_entity(conn, Entity(id="e1", type="Concept", name="MCP", sources=["d1"]))
    rep = node_impl(conn, "e1", full=True)
    assert rep["documents"][0]["detail"] == "상세"


def test_node_impl_documents_capped_and_flagged():
    conn = _db()
    n = MAX_NODE_DOCUMENTS + 5
    ids = []
    for i in range(n):
        did = f"d{i}"
        ids.append(did)
        dbm.insert_document(conn, Document(
            id=did, url=f"https://example.com/{i}", title=f"Doc {i}",
            raw_text="x", source_type="web", content_hash=f"h{i}"))
    dbm.upsert_entity(conn, Entity(id="e1", type="Concept", name="Hub", sources=ids))
    rep = node_impl(conn, "e1")
    assert len(rep["documents"]) == MAX_NODE_DOCUMENTS
    assert rep["documents_truncated"] is True
    assert rep["documents_omitted"] == 5


def _seed_many_documents(conn, n: int, *, base_ts: float = 1_700_000_000.0):
    for i in range(n):
        dbm.insert_document(conn, Document(
            id=f"d{i}", url=f"https://example.com/{i}", title=f"Doc {i}",
            raw_text="x", source_type="web", content_hash=f"h{i}",
            fetched_at=base_ts + i))


def test_documents_impl_truncation_not_silent():
    conn = _db()
    _seed_many_documents(conn, 5)
    r = documents_impl(conn, limit=2)
    assert len(r["documents"]) == 2
    assert r["truncated"] is True
    assert r["omitted"] == 3


def test_documents_impl_hard_cap_enforced_even_if_requested_higher():
    conn = _db()
    _seed_many_documents(conn, MAX_DOCUMENTS + 20)
    r = documents_impl(conn, limit=MAX_DOCUMENTS + 20)  # 더 크게 요청해도
    assert len(r["documents"]) == MAX_DOCUMENTS
    assert r["truncated"] is True


def test_documents_impl_since_filter():
    conn = _db()
    _seed_many_documents(conn, 5, base_ts=1_700_000_000.0)  # d0..d4, 1초 간격
    since_iso = _iso_utc(1_700_000_002.0)  # d2부터
    r = documents_impl(conn, limit=10, since=since_iso)
    ids = {d["id"] for d in r["documents"]}
    assert ids == {"d2", "d3", "d4"}


def test_documents_impl_query_filter_title_and_url():
    conn = _db()
    dbm.insert_document(conn, Document(
        id="d1", url="https://example.com/unique-slug", title="아무 제목",
        raw_text="x", source_type="web", content_hash="h1"))
    dbm.insert_document(conn, Document(
        id="d2", url="https://example.com/other", title="다른 문서",
        raw_text="x", source_type="web", content_hash="h2"))
    r = documents_impl(conn, query="unique-slug")
    assert [d["id"] for d in r["documents"]] == ["d1"]
    r2 = documents_impl(conn, query="다른")
    assert [d["id"] for d in r2["documents"]] == ["d2"]


def test_documents_impl_invalid_since_returns_error_not_exception():
    conn = _db()
    r = documents_impl(conn, since="last week")
    assert r["error"]
    assert r["got"] == "last week"
