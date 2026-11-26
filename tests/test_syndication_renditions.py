"""Tests for `/syndication` stage 3 renditions, opinion summaries, publish dialog, and actor view."""

from __future__ import annotations

import sqlite3
import time
from pathlib import Path

from starlette.testclient import TestClient

from claire.api.server import create_app
from claire.ingest.source_key import source_key
from claire.store import accounts as accounts_store
from claire.store import client_db
from claire.store import db as dbm
from claire.store import document_renditions as renditions_store
from claire.store import federation as federation_store
from claire.store import federation_opinions as opinions_store
from claire.store import permalinks
from tests.test_public_directory import _setup_network_env
from tests.test_silvercord_rendering import _make_caller

KEY = source_key("https://arxiv.org/abs/2101.00001")


def _insert_opinion(conn: sqlite3.Connection, object_iri: str, opinion_iri: str, *, hidden_at: float | None = None) -> None:
    """Insert one opinion row for unit and E2E tests."""
    now = time.time()
    conn.execute(
        "INSERT INTO federation_opinions (object_iri, opinion_iri, parent_iri, kind, source, author_iri, author_name, author_handle, url, content_html, attachments_json, language, summary, sensitive, published, updated, fetched_at, hidden_at, hidden_by) "
        "VALUES (?, ?, ?, 'reply', 'fetched', 'https://remote.example/users/carol', 'Carol', '@carol@remote.example', ?, '<p>opinion</p>', '[]', 'en', NULL, 0, ?, NULL, ?, ?, NULL)",
        (object_iri, opinion_iri, object_iri, opinion_iri, now, now, hidden_at),
    )


def test_count_visible_opinions_counts_non_hidden_rows(tmp_path: Path) -> None:
    """Verify count_visible_opinions excludes hidden rows and scopes by origin when supplied."""
    conn = sqlite3.connect(tmp_path / "client.db")
    try:
        opinions_store.ensure_schema(conn)
        origin = "https://claire.example"
        _insert_opinion(conn, f"{origin}/ap/objects/101", "https://remote.example/n/1")
        _insert_opinion(conn, f"{origin}/ap/objects/101", "https://remote.example/n/2")
        _insert_opinion(conn, f"{origin}/ap/objects/101", "https://remote.example/n/3", hidden_at=1000.0)
        _insert_opinion(conn, f"{origin}/ap/objects/102", "https://remote.example/n/4")
        _insert_opinion(conn, "https://other.example/ap/objects/102", "https://remote.example/n/5")
        _insert_opinion(conn, f"{origin}/ap/objects/103", "https://remote.example/n/6", hidden_at=1000.0)
        conn.commit()

        assert opinions_store.count_visible_opinions(conn, []) == {}
        assert opinions_store.count_visible_opinions(conn, [101, 102, 103, 104], origin=origin) == {101: 2, 102: 1, 103: 0, 104: 0}
        assert opinions_store.count_visible_opinions(conn, [101, 102, 103, 104]) == {101: 2, 102: 2, 103: 0, 104: 0}
    finally:
        conn.close()


def test_e2e_federation_objects_includes_renditions_and_visible_opinions(tmp_path: Path) -> None:
    """Verify GET /api/federation/objects returns per-rendition scores, opinion counts, and total opinions."""
    s, env = _setup_network_env(tmp_path)
    conn = dbm.connect_existing(env["db_file"])
    try:
        now = time.time()
        sessions = {name: accounts_store.create_account_session(conn, account_id=env[f"{name}_id"], scope="owner") for name in ("alice", "bob")}
        conn.execute("UPDATE documents SET source_key=?, source_id_version=1, detail='= 기본\n\n본문.' WHERE id='doc1'", (KEY,))
        renditions_store.record_primary(conn, "doc1", 10, lineage={}, now=now)
        permalinks.ensure_document(conn, "doc1", 10)
        renditions_store.store_additional(conn, "doc1", 10, template="concise", detail="= 간결\n\n본문.", lineage={}, now=now)
        doc_obj, _, _ = federation_store.publish_document(conn, account_id=env["alice_id"], kb_id=10, document_id="doc1", published_by_account_id=env["alice_id"], salt=accounts_store.AccountStore(conn).id_salt(), now=now)
        rend_objs = federation_store.rendition_objects_of(conn, doc_obj.public_id)
        assert [r.template for r in rend_objs] == ["concise"]
        concise_obj = rend_objs[0]
        conn.commit()
        tokens = {"doc1": permalinks.token_of(conn, "document", "doc1")}
    finally:
        conn.close()

    origin = s.public_url.rstrip("/")
    tc = TestClient(create_app(s), base_url=s.public_url, follow_redirects=False)
    alice = _make_caller(tc, origin, sessions["alice"])
    bob = _make_caller(tc, origin, sessions["bob"])

    client_conn = client_db.connect_client_existing(client_db.client_db_path(env["db_file"]))
    try:
        _insert_opinion(client_conn, f"{origin}/ap/objects/{doc_obj.public_id}", "https://remote.example/op/1")
        _insert_opinion(client_conn, f"{origin}/ap/objects/{doc_obj.public_id}", "https://remote.example/op/2")
        _insert_opinion(client_conn, f"{origin}/ap/objects/{doc_obj.public_id}", "https://remote.example/op/3", hidden_at=now)
        _insert_opinion(client_conn, f"{origin}/ap/objects/{concise_obj.public_id}", "https://remote.example/op/4")
        _insert_opinion(client_conn, f"{origin}/ap/objects/{concise_obj.public_id}", "https://remote.example/op/5", hidden_at=now)
        client_conn.commit()
    finally:
        client_conn.close()

    assert bob("POST", "/api/renditions/plus-one", json={"token": tokens["doc1"], "rendition": "concise", "on": True}).status_code == 200
    resp = alice("GET", "/api/federation/objects")
    assert resp.status_code == 200
    [item] = resp.json()["items"]
    assert set(item) >= {"id", "kb_id", "document_id", "published_at", "updated_at", "likes", "announces", "quotes", "plus_ones", "renditions", "opinions"}
    assert item["id"] == str(doc_obj.public_id)
    assert item["plus_ones"] == [{"template": "standard", "score": 0}, {"template": "concise", "score": 1}]
    assert item["renditions"] == [
        {"template": "standard", "public_id": str(doc_obj.public_id), "score": 0, "opinions": 2},
        {"template": "concise", "public_id": str(concise_obj.public_id), "score": 1, "opinions": 1},
    ]
    assert item["opinions"] == 3


def test_e2e_syndication_serves_updated_scripts_styles_and_locale_keys(tmp_path: Path) -> None:
    """Verify /syndication HTML, static assets, and /static/locales JSON expose stage 3 and actor view updates."""
    s, env = _setup_network_env(tmp_path)
    conn = dbm.connect_existing(env["db_file"])
    try:
        alice_session = accounts_store.create_account_session(conn, account_id=env["alice_id"], scope="owner")
    finally:
        conn.close()

    origin = s.public_url.rstrip("/")
    tc = TestClient(create_app(s), base_url=s.public_url, follow_redirects=False)
    alice = _make_caller(tc, origin, alice_session)

    page = alice("GET", "/syndication")
    assert page.status_code == 200
    for marker in (
        "bridge.onPublished",
        "dispatch-native-renditions",
        "synd-actor-bar",
        "synd-badge-filled",
        "synd-follow-row.is-active",
        "synd-publish-rendition-chip",
    ):
        assert marker in page.text

    expected_keys = (
        "synd.kb.opinions_count",
        "synd.kb.renditions",
        "synd.kb.rendition_plus",
        "synd.kb.publish.renditions_label",
        "synd.kb.publish.renditions_note",
        "synd.client.actor_fill_note",
        "synd.client.back_to_home",
    )
    for locale in ("ko", "en"):
        res = alice("GET", f"/static/locales/{locale}.json")
        assert res.status_code == 200
        catalog = res.json()
        for key in expected_keys:
            assert key in catalog and catalog[key]
