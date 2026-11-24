"""Unit and app-level tests for the 17-tool clean-slate MCP surface in mcp_tools.py."""

from __future__ import annotations

import json
from pathlib import Path
import sqlite3

from starlette.testclient import TestClient

from claire.api.mcp_tools import (
    DEFAULT_DOCUMENTS_LIMIT,
    MAX_DOCUMENTS,
    MAX_NODE_DOCUMENTS,
    _iso_utc,
    _parse_since,
    build_mcp_app,
    doc_get_impl,
    doc_list_impl,
    entity_get_impl,
    entity_search_impl,
    get_mcp_tools_metadata,
    graph_context_impl,
    graph_neighbors_impl,
    graph_path_impl,
    kb_overview_impl,
)
from claire.core.config import Settings
from claire.ontology.base import Document, Entity, Relation
from claire.store import db as dbm
from claire.store.knowledge_base import KnowledgeBaseManager
from tests.kb_paths import kb_number


def _db() -> sqlite3.Connection:
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    dbm.init_db(conn)
    return conn


def _seed_graph(conn: sqlite3.Connection) -> None:
    dbm.insert_document(conn, Document(id="d1", url="https://example.com/x", title="X", raw_text="MCP", source_type="web", content_hash="h1"))
    dbm.upsert_entity(conn, Entity(id="e_mcp", type="Concept", name="MCP", aliases=["Model Context Protocol"], observations=["a", "b", "c", "d"], sources=["d1"]))
    dbm.upsert_entity(conn, Entity(id="e_cc", type="Product", name="Claude Code", sources=["d1"]))
    dbm.upsert_entity(conn, Entity(id="e_an", type="Org", name="Anthropic", sources=["d1"]))
    dbm.upsert_entity(conn, Entity(id="e_far", type="Product", name="Unrelated Thing", sources=["d1"]))
    dbm.upsert_relation(conn, Relation(id="r1", type="uses", source_id="e_cc", target_id="e_mcp"))
    dbm.upsert_relation(conn, Relation(id="r2", type="develops", source_id="e_an", target_id="e_cc"))


def test_entity_search_exact_alias_and_fts() -> None:
    conn = _db()
    _seed_graph(conn)
    r = entity_search_impl(conn, "MCP")
    assert [m["id"] for m in r["exact_matches"]] == ["e_mcp"]
    r2 = entity_search_impl(conn, "Model Context Protocol")
    assert [m["id"] for m in r2["exact_matches"]] == ["e_mcp"]
    r3 = entity_search_impl(conn, "Claude")
    assert "e_cc" in [h["id"] for h in r3["hits"]]
    r4 = entity_search_impl(conn, "NonexistentEntityZZZ")
    assert r4["exact_matches"] == [] and r4["hits"] == []


def test_entity_search_filters_and_truncation() -> None:
    conn = _db()
    _seed_graph(conn)
    r_match = entity_search_impl(conn, "Anthropic", entity_type="Org")
    assert [h["id"] for h in r_match["hits"]] == ["e_an"]
    assert [m["id"] for m in r_match["exact_matches"]] == ["e_an"]

    r_mismatch = entity_search_impl(conn, "Anthropic", entity_type="Product")
    assert r_mismatch["hits"] == [] and r_mismatch["exact_matches"] == []

    r_near_miss = entity_search_impl(conn, "Anthropic", near_ids=["e_mcp"])
    assert r_near_miss["hits"] == [] and r_near_miss["exact_matches"] == []

    r_near_hit = entity_search_impl(conn, "Anthropic", near_ids=["e_cc"])
    assert any(h["id"] == "e_an" for h in r_near_hit["hits"])

    r_trunc = entity_search_impl(conn, "Anthropic", limit=0)
    assert r_trunc["hits"] == [] and r_trunc["truncated"] is True and r_trunc["omitted"] >= 1


def test_graph_neighbors_union_exclude_and_truncation() -> None:
    conn = _db()
    _seed_graph(conn)
    r = graph_neighbors_impl(conn, ["e_mcp", "e_an"])
    assert [n["id"] for n in r["neighbors"]] == ["e_cc"]
    assert r["neighbors"][0]["degree"] == 2

    r_excl = graph_neighbors_impl(conn, "e_cc", exclude_ids=["e_an"])
    assert [n["id"] for n in r_excl["neighbors"]] == ["e_mcp"]

    r_self = graph_neighbors_impl(conn, "e_mcp")
    assert "e_mcp" not in {n["id"] for n in r_self["neighbors"]}

    r_lim = graph_neighbors_impl(conn, "e_cc", limit=1)
    assert r_lim["truncated"] is True and r_lim["omitted"] == 1 and len(r_lim["neighbors"]) == 1


def test_graph_path_and_context() -> None:
    conn = _db()
    _seed_graph(conn)
    r = graph_path_impl(conn, "e_an", "e_mcp")
    assert r["found"] is True
    assert [n["id"] for n in r["path"]] == ["e_an", "e_cc", "e_mcp"]
    assert [rel["type"] for rel in r["relations"]] == ["develops", "uses"]

    assert graph_path_impl(conn, "e_an", "e_far")["found"] is False
    assert len(graph_path_impl(conn, "e_mcp", "e_mcp")["path"]) == 1

    r_compact = graph_context_impl(conn, ["e_mcp"], include_hidden=True)
    assert " d" not in r_compact["context"].split("관찰:")[1].split("\n")[0]
    r_full = graph_context_impl(conn, ["e_mcp"], compact=False, include_hidden=True)
    assert " d" in r_full["context"].split("관찰:")[1].split("\n")[0]

    r_cap = graph_context_impl(conn, ["e_mcp", "e_cc", "e_an", "e_far"] * 5, include_hidden=True)
    assert r_cap["truncated"] is True and r_cap["omitted"] == 10


def test_kb_overview_includes_stats_when_requested() -> None:
    conn = _db()
    _seed_graph(conn)
    with_stats = kb_overview_impl(conn, include_stats=True)
    types = {t["type"]: t["count"] for t in with_stats["entity_types"]}
    assert types.get("Product") == 2 and types.get("Concept") == 1 and types.get("Org") == 1
    assert with_stats["hubs"][0]["degree"] >= with_stats["hubs"][-1]["degree"]
    assert with_stats["stats"]["documents"] == 1 and with_stats["stats"]["entities"] == 4

    without_stats = kb_overview_impl(conn, include_stats=False)
    assert "stats" not in without_stats


def test_doc_get_and_entity_get_contracts() -> None:
    conn = _db()
    _seed_graph(conn)
    dbm.set_document_seen(conn, "d1", seen=False)
    rep = doc_get_impl(conn, "d1", include_hidden=True)
    doc_row = dbm.get_document_row(conn, "d1")
    assert rep["id"] == "d1" and doc_row is not None and doc_row["seen"] == 0
    assert doc_get_impl(conn, "missing", include_hidden=True) == {"error": "not_found"}
    assert entity_get_impl(conn, "missing", include_hidden=True) == {"error": "not_found"}


def test_iso_utc_and_since_helpers() -> None:
    s = _iso_utc(1786751445.79)
    assert s is not None and (s.endswith("+00:00") or s.endswith("Z"))
    assert _iso_utc(None) is None
    assert _parse_since(None) is None
    assert _parse_since("2026-08-15") == _parse_since("2026-08-15T00:00:00Z")


def test_doc_get_and_entity_get_timestamps_detail_and_caps() -> None:
    conn = _db()
    n = MAX_NODE_DOCUMENTS + 5
    doc_ids = []
    for i in range(n):
        did = f"d{i}"
        doc_ids.append(did)
        dbm.insert_document(conn, Document(id=did, url=f"https://example.com/{i}", title=f"Doc {i}", raw_text="raw", source_type="web", content_hash=f"h{i}", fetched_at=1786751445.79))
    dbm.set_document_detail(conn, "d0", "Very long detail " * 20)
    dbm.upsert_entity(conn, Entity(id="e1", type="Concept", name="Hub", sources=doc_ids))

    rep_compact = entity_get_impl(conn, "e1", include_hidden=True)
    assert len(rep_compact["documents"]) == MAX_NODE_DOCUMENTS
    assert rep_compact["documents_truncated"] is True and rep_compact["documents_omitted"] == 5
    assert rep_compact["documents"][0]["fetched_at"] == _iso_utc(1786751445.79)
    assert all("detail" not in d for d in rep_compact["documents"])

    rep_full = entity_get_impl(conn, "e1", full=True, include_hidden=True)
    d0 = next(d for d in rep_full["documents"] if d["id"] == "d0")
    assert "Very long detail" in d0["detail"]


def test_doc_list_truncation_cap_since_and_query() -> None:
    conn = _db()
    for i in range(MAX_DOCUMENTS + 10):
        dbm.insert_document(conn, Document(id=f"d{i}", url=f"https://example.com/{i}", title=f"Doc {i}", raw_text="x", source_type="web", content_hash=f"h{i}", fetched_at=1_700_000_000.0 + i))
    capped = doc_list_impl(conn, limit=MAX_DOCUMENTS + 10)
    assert len(capped["documents"]) == MAX_DOCUMENTS and capped["truncated"] is True and capped["omitted"] == 10

    r_bad = doc_list_impl(conn, since="last week")
    assert r_bad["error"] and r_bad["got"] == "last week"

    r_q = doc_list_impl(conn, query="example.com/109")
    assert [d["id"] for d in r_q["documents"]] == ["d109"]


def test_build_mcp_app_metadata_and_kb_isolation(tmp_path: Path) -> None:
    db_file = tmp_path / "claire.db"
    vault = tmp_path / "vault"
    vault.mkdir()
    s = Settings(CLAIRE_DB_PATH=str(db_file))
    conn0 = dbm.connect(s.db_file)
    dbm.init_db(conn0)
    conn0.close()

    tm = KnowledgeBaseManager(s)
    t1 = tm.define_kb("Isolated Shared KB", knowledge_type="real")
    t1_s = tm.get_settings_for_kb(t1.id, s)
    conn1 = dbm.connect(t1_s.db_file)
    dbm.init_db(conn1)
    dbm.insert_document(conn1, Document(id="iso_d1", url="https://example.com/iso1", title="Isolated Doc", raw_text="iso1 text", source_type="web", content_hash="hiso1"), kb_id=t1.id)
    conn1.close()

    meta = get_mcp_tools_metadata()
    assert len(meta) == 17
    assert all(isinstance(item["parameters"], list) and item["description"] and item["title"] for item in meta)

    app = build_mcp_app(s, theme_mgr=tm)
    with TestClient(app, base_url="http://localhost:8765") as client:
        listed = client.post("/mcp", json={"jsonrpc": "2.0", "id": 1, "method": "tools/list"}).json()["result"]["tools"]
        assert len(listed) == 17
        by_name = {t["name"]: t for t in listed}
        assert by_name["kb_overview"]["annotations"]["readOnlyHint"] is True
        assert by_name["extract_submit"]["annotations"]["readOnlyHint"] is False

        s0 = json.loads(client.post("/mcp", json={"jsonrpc": "2.0", "id": 2, "method": "tools/call", "params": {"name": "kb_overview", "arguments": {"kb": kb_number(s, 0)}}}).json()["result"]["content"][0]["text"])
        assert s0["stats"]["documents"] == 0

        s1 = json.loads(client.post("/mcp", json={"jsonrpc": "2.0", "id": 3, "method": "tools/call", "params": {"name": "kb_overview", "arguments": {"kb": kb_number(s, 1)}}}).json()["result"]["content"][0]["text"])
        assert s1["stats"]["documents"] == 1

        d1 = json.loads(client.post("/mcp", json={"jsonrpc": "2.0", "id": 4, "method": "tools/call", "params": {"name": "doc_list", "arguments": {"kb": kb_number(s, 1)}}}).json()["result"]["content"][0]["text"])
        assert [doc["id"] for doc in d1["documents"]] == ["iso_d1"]


def test_build_mcp_app_default_kb_not_found(tmp_path: Path) -> None:
    db_file = tmp_path / "claire.db"
    s = Settings(CLAIRE_DB_PATH=str(db_file))

    conn0 = dbm.connect(s.db_file)
    dbm.init_db(conn0)
    dbm.insert_document(conn0, Document(id="single_doc", url="https://example.com/single", title="Single Mode Doc", raw_text="single text", source_type="web", content_hash="h_single"))
    conn0.close()

    app = build_mcp_app(s)
    with TestClient(app, base_url="http://localhost:8765") as client:
        r_found = client.post("/mcp", json={"jsonrpc": "2.0", "id": 1, "method": "tools/call", "params": {"name": "doc_get", "arguments": {"document_id": "single_doc", "kb": kb_number(s, 0)}}})
        assert json.loads(r_found.json()["result"]["content"][0]["text"])["id"] == "single_doc"

        r_not_found = client.post("/mcp", json={"jsonrpc": "2.0", "id": 2, "method": "tools/call", "params": {"name": "doc_get", "arguments": {"document_id": "nonexistent_doc", "kb": kb_number(s, 0)}}})
        assert json.loads(r_not_found.json()["result"]["content"][0]["text"]) == {"error": "not_found"}
