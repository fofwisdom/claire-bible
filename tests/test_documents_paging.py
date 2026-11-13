"""Keyset paging of the document list: store functions and the GET /documents contract (unit LP-5)."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import pytest
from starlette.testclient import TestClient

from claire.api.server import create_app
from claire.ontology.base import Document
from claire.store import db as dbm
from claire.store import queries

OWNER_TOKEN = "owner-" + ("o" * 32)
READONLY_TOKEN = "readonly-" + ("r" * 32)
OWNER_HEADERS = {"Authorization": f"Bearer {OWNER_TOKEN}"}


@dataclass
class _Settings:
    db_file: Path
    data_dir: Path
    vault_dir: Path
    environment: str = "development"
    public_url: str = "http://127.0.0.1:8765"
    inject_token: str = OWNER_TOKEN
    readonly_token: str = READONLY_TOKEN
    cors_allowed_origins: str = ""
    anonymous_readonly: bool = True
    vector_backend: str = "brute"
    effective_provider: str = "mock"


def _add(conn, doc_id: str, fetched_at: float, *, hidden: bool = False, pinned: bool = False, title: str | None = None) -> None:
    dbm.insert_document(conn, Document(id=doc_id, url=f"https://example.test/{doc_id}", title=title or f"Title {doc_id}", raw_text="text", source_type="web", content_hash=f"hash-{doc_id}", fetched_at=fetched_at))
    if hidden:
        dbm.set_document_hidden(conn, doc_id, True)
    if pinned:
        dbm.set_document_pinned(conn, doc_id, True)


def _seed(tmp_path: Path) -> tuple[_Settings, list[str]]:
    """Ten documents. d05 and d06 have the same fetched_at. d03 and d08 are hidden, and d02 is pinned. The result list is the expected order of all documents."""
    data_dir = tmp_path / "data"
    vault_dir = tmp_path / "vault"
    data_dir.mkdir()
    vault_dir.mkdir()
    conn = dbm.connect(data_dir / "claire.db")
    dbm.init_db(conn)
    times = {"d01": 1000.5, "d02": 990.0, "d03": 980.0, "d04": 970.0, "d05": 960.25, "d06": 960.25, "d07": 950.0, "d08": 940.0, "d09": 930.0, "d10": 920.0}
    for doc_id, ts in times.items():
        _add(conn, doc_id, ts, hidden=doc_id in ("d03", "d08"), pinned=doc_id == "d02")
    dbm.log_extraction(conn, document_id="d09", provider="mock", model="m", prompt_version="v", raw_response='{"summary": "keyword-in-summary text"}')
    dbm.log_extraction(conn, document_id="d10", provider="mock", model="m", prompt_version="v", raw_response="not json keyword-in-summary")
    conn.commit()
    conn.close()
    # The tie-break is id descending, thus d06 is before d05.
    return _Settings(db_file=data_dir / "claire.db", data_dir=data_dir, vault_dir=vault_dir), ["d01", "d02", "d03", "d04", "d06", "d05", "d07", "d08", "d09", "d10"]


def _all_pages(fetch, limit: int) -> tuple[list[str], list[dict]]:
    ids: list[str] = []
    pages: list[dict] = []
    before = None
    for _ in range(50):
        page = fetch(limit, before)
        pages.append(page)
        ids.extend(d["id"] for d in page["documents"])
        before = page["next_before"]
        if before is None:
            return ids, pages
    raise AssertionError("the paging did not end")


def test_documents_page_order_tie_break_end_and_total(tmp_path: Path) -> None:
    settings, order = _seed(tmp_path)
    conn = dbm.connect(settings.db_file)
    try:
        ids, pages = _all_pages(lambda limit, before: queries.documents_page(conn, limit, before=before, include_hidden=True), 3)
        assert ids == order
        assert [len(p["documents"]) for p in pages] == [3, 3, 3, 1]
        assert pages[-1]["next_before"] is None and all(p["next_before"] for p in pages[:-1])
        assert {p["total"] for p in pages} == {10}
        # A page that ends between the two documents with the same fetched_at continues with the other one.
        first = queries.documents_page(conn, 5, include_hidden=True)
        assert [d["id"] for d in first["documents"]] == order[:5] and first["next_before"] == "960.25:d06"
        assert [d["id"] for d in queries.documents_page(conn, 5, before=first["next_before"], include_hidden=True)["documents"]] == order[5:]
        # A page with exactly the remaining documents is the last page.
        assert queries.documents_page(conn, 10, include_hidden=True)["next_before"] is None
        # The default call gives the same documents as documents_list().
        assert queries.documents_page(conn, include_hidden=True)["documents"] == queries.documents_list(conn, limit=300, include_hidden=True)
        with pytest.raises(ValueError):
            queries.documents_page(conn, 3, before="not-a-cursor")
    finally:
        conn.close()


def test_documents_page_has_no_duplicate_and_no_gap_when_a_document_is_inserted_between_requests(tmp_path: Path) -> None:
    settings, order = _seed(tmp_path)
    conn = dbm.connect(settings.db_file)
    try:
        first = queries.documents_page(conn, 4, include_hidden=True)
        _add(conn, "d00-new", 2000.0)
        conn.commit()
        rest, _pages = _all_pages(lambda limit, before: queries.documents_page(conn, limit, before=before or first["next_before"], include_hidden=True), 4)
        assert [d["id"] for d in first["documents"]] + rest == order
        assert queries.documents_page(conn, 4, include_hidden=True)["documents"][0]["id"] == "d00-new"
    finally:
        conn.close()


def test_documents_page_null_fetched_at_is_last(tmp_path: Path) -> None:
    settings, order = _seed(tmp_path)
    conn = dbm.connect(settings.db_file)
    try:
        conn.execute("UPDATE documents SET fetched_at = NULL WHERE id IN ('d01', 'd04')")
        conn.commit()
        expected = [i for i in order if i not in ("d01", "d04")] + ["d04", "d01"]
        ids, pages = _all_pages(lambda limit, before: queries.documents_page(conn, limit, before=before, include_hidden=True), 3)
        assert ids == expected
        assert pages[-2]["next_before"] == ":d04"
    finally:
        conn.close()


def test_get_documents_default_response_is_unchanged_plus_additive_fields(tmp_path: Path) -> None:
    settings, order = _seed(tmp_path)
    conn = dbm.connect(settings.db_file)
    expected_owner = queries.documents_list(conn, limit=300, include_hidden=True, theme_id=0)
    conn.close()
    with TestClient(create_app(settings), base_url=settings.public_url) as client:
        body = client.get("/documents", headers=OWNER_HEADERS).json()
        assert set(body) == {"documents", "format_status", "theme_id", "theme_label", "next_before", "total"}
        assert body["documents"] == expected_owner
        assert [d["id"] for d in body["documents"]] == order
        assert body["next_before"] is None and body["total"] == 10
        anon = client.get("/documents").json()
        assert [d["id"] for d in anon["documents"]] == [i for i in order if i not in ("d03", "d08")]
        assert anon["total"] == 8 and anon["next_before"] is None


def test_get_documents_pages_exclude_hidden_documents_for_the_anonymous_scope(tmp_path: Path) -> None:
    settings, order = _seed(tmp_path)
    visible = [i for i in order if i not in ("d03", "d08")]
    with TestClient(create_app(settings), base_url=settings.public_url) as client:
        def fetch(headers):
            def _fetch(limit, before):
                params = {"limit": limit}
                if before:
                    params["before"] = before
                res = client.get("/documents", params=params, headers=headers)
                assert res.status_code == 200
                return res.json()
            return _fetch

        anon_ids, anon_pages = _all_pages(fetch({}), 3)
        assert anon_ids == visible
        assert {p["total"] for p in anon_pages} == {8}
        # A cursor that points at a hidden document does not show hidden documents to the anonymous scope.
        after_hidden = client.get("/documents", params={"limit": 3, "before": "980.0:d03"}).json()
        assert [d["id"] for d in after_hidden["documents"]] == ["d04", "d06", "d05"]
        assert client.get("/documents", params={"hidden": "1"}).json()["documents"] == []
        assert client.get("/documents", params=[("id", "d03"), ("id", "d04")]).json()["documents"][0]["id"] == "d04"
        assert len(client.get("/documents", params=[("id", "d03"), ("id", "d04")]).json()["documents"]) == 1

        owner_ids, _ = _all_pages(fetch(OWNER_HEADERS), 3)
        assert owner_ids == order


def test_get_documents_filters_and_parameter_errors(tmp_path: Path) -> None:
    settings, order = _seed(tmp_path)
    with TestClient(create_app(settings), base_url=settings.public_url) as client:
        def ids(**params):
            res = client.get("/documents", params=params, headers=OWNER_HEADERS)
            assert res.status_code == 200
            return [d["id"] for d in res.json()["documents"]], res.json()["total"]

        assert ids(pinned="1") == (["d02"], 1)
        assert ids(hidden="1") == (["d03", "d08"], 2)
        # The list of the WebUI: not hidden and not pinned.
        assert ids(hidden="0", pinned="0", limit="4") == (["d01", "d04", "d06", "d05"], 7)
        # q matches the title or the summary of the latest extraction. An extraction that is not JSON has no summary. The characters % and _ are literal.
        assert ids(q="Title d07") == (["d07"], 1)
        assert ids(q="keyword-in-summary") == (["d09"], 1)
        assert ids(q="%") == ([], 0)
        assert ids(detail="missing")[1] == 10
        assert ids(detail="mismatched") == ([], 0)
        assert ids(id="d05") == (["d05"], 1)
        for params in ({"limit": "0"}, {"limit": "301"}, {"limit": "x"}, {"before": "abc"}, {"before": "nan:d01"}, {"pinned": "yes"}, {"detail": "other"}):
            assert client.get("/documents", params=params, headers=OWNER_HEADERS).status_code == 400, params


def test_documents_page_cursor_id_with_colon_literal_like_characters_and_theme_scope(tmp_path: Path) -> None:
    settings, _order = _seed(tmp_path)
    conn = dbm.connect(settings.db_file)
    try:
        # Ids with ':' and the same fetched_at: only the first ':' of the cursor separates the parts.
        for doc_id in ("x:1", "x:2", "x:3:z"):
            _add(conn, doc_id, 5000.0, title=f"colon {doc_id}")
        # Titles with the literal LIKE characters '_' and '\'.
        _add(conn, "u1", 10.0, title="snake_case title")
        _add(conn, "u2", 9.0, title="snakeXcase title")
        _add(conn, "b1", 8.0, title="back\\slash title")
        _add(conn, "b2", 7.0, title="backXslash title")
        # A document of another knowledge theme. The theme row is not necessary for this store test.
        conn.execute("PRAGMA foreign_keys = OFF")
        dbm.insert_document(conn, Document(id="t7", url="https://example.test/t7", title="colon other theme", raw_text="text", source_type="web", content_hash="hash-t7", fetched_at=6000.0), theme_id=7)
        conn.commit()
        conn.execute("PRAGMA foreign_keys = ON")

        def page(limit, before, **filters):
            return queries.documents_page(conn, limit, before=before, include_hidden=True, theme_id=0, **filters)

        ids, pages = _all_pages(lambda limit, before: page(limit, before), 1)
        assert ids[:3] == ["x:3:z", "x:2", "x:1"]
        assert pages[0]["next_before"] == "5000.0:x:3:z"
        assert len(ids) == len(set(ids)) == 17 and "t7" not in ids
        assert {p["total"] for p in pages} == {17}

        def q_ids(q):
            return [d["id"] for d in page(300, None, text=q)["documents"]]

        assert q_ids("snake_case") == ["u1"]
        assert q_ids("back\\slash") == ["b1"]
        # theme scope on the q filter and the total.
        assert q_ids("colon") == ["x:3:z", "x:2", "x:1"]
        assert page(300, None, text="colon")["total"] == 3
        other = queries.documents_page(conn, 300, include_hidden=True, theme_id=7)
        assert [d["id"] for d in other["documents"]] == ["t7"] and other["total"] == 1
    finally:
        conn.close()


def test_get_documents_parameter_errors_have_fixed_messages(tmp_path: Path) -> None:
    settings, _order = _seed(tmp_path)
    with TestClient(create_app(settings), base_url=settings.public_url) as client:
        for value in ("abc", "1.5", "9" * 5000, "0", "301"):
            res = client.get("/documents", params={"limit": value}, headers=OWNER_HEADERS)
            assert res.status_code == 400 and res.json() == {"error": "limit must be an integer 1..300"}, value
        for value in ("garbage", "abc:def", "nan:x", "9" * 5000 + ":x", "1:"):
            res = client.get("/documents", params={"before": value}, headers=OWNER_HEADERS)
            assert res.status_code == 400 and res.json() == {"error": "before must be <fetched_at>:<id>"}, value
