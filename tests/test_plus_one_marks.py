"""+1 of the accounts of this server (DOCUMENT_RENDITIONS_AND_PUBLICATION_DESIGN.md Section 5.12, phase E9b; claire.db version 56).

End to end through the route ``POST /api/renditions/plus-one`` and the page data of the Silvercord page, in one app: a mark for a rendition of this server, a mark for a cached rendition of another Claire server (the activity in the outbox of the knowledge federation), the take back, and the refusals.
"""

from __future__ import annotations

import hashlib
import json
import time
from pathlib import Path

from starlette.testclient import TestClient

from claire.api.server import create_app
from claire.federation import plus_one_marks
from claire.federation.knowledge import vocabulary
from claire.ingest.source_key import source_key
from claire.store import accounts as accounts_store
from claire.store import db as dbm
from claire.store import document_renditions as renditions_store
from claire.store import federation as federation_store
from claire.store import permalinks
from tests.test_public_directory import _setup_network_env
from tests.test_silvercord_rendering import _extract_docdata, _make_caller

KEY = source_key("https://arxiv.org/abs/2101.00001")
REMOTE = "https://b.example/ap/objects/9002"
REMOTE_AUTHOR = "https://b.example/ap/actors/7001"


def _world(tmp_path: Path):
    s, env = _setup_network_env(tmp_path)
    conn = dbm.connect_existing(env["db_file"])
    try:
        now = time.time()
        sessions = {name: accounts_store.create_account_session(conn, account_id=env[f"{name}_id"], scope="owner") for name in ("alice", "bob")}
        conn.execute("UPDATE documents SET source_key=?, source_id_version=1, detail='= 기본\n\n본문.' WHERE id IN ('doc1', 'doc2')", (KEY,))
        for doc_id in ("doc1", "doc2"):
            renditions_store.record_primary(conn, doc_id, 10, lineage={}, now=now)
            permalinks.ensure_document(conn, doc_id, 10)
        renditions_store.store_additional(conn, "doc1", 10, template="concise", detail="= 간결\n\n본문.", lineage={}, now=now)
        federation_store.publish_document(conn, account_id=env["alice_id"], kb_id=10, document_id="doc1", published_by_account_id=env["alice_id"], salt=accounts_store.AccountStore(conn).id_salt(), now=now)
        body = "= 학술\n\n본문."
        digest = hashlib.sha256(body.encode()).hexdigest()
        conn.execute(
            "INSERT INTO remote_renditions (iri, domain, source_key, template, template_version, rendition_of, account_iri, account_handle, account_name, name, url, updated, body_iri, body_hash, cached_body_hash, body_html, body_source, fetched_at, expires_at, plus_total, plus_score) "
            "VALUES (?, 'b.example', ?, 'academic', 'academic-v1', NULL, ?, '@carol@b.example', 'Carol', 'Same paper', NULL, '2027-01-15T08:05:20Z', ?, ?, ?, '<p>본문.</p>', ?, ?, ?, 4, 4)",
            (REMOTE, KEY, REMOTE_AUTHOR, f"{REMOTE}/body", digest, digest, body, now - 10, now + 3600),
        )
        conn.commit()
        tokens = {doc_id: permalinks.token_of(conn, "document", doc_id) for doc_id in ("doc1", "doc2")}
    finally:
        conn.close()
    client = TestClient(create_app(s), base_url=s.public_url, follow_redirects=False)
    origin = s.public_url.rstrip("/")
    callers = {"anon": _make_caller(client, origin), **{name: _make_caller(client, origin, token) for name, token in sessions.items()}}
    return s, env, tokens, callers


def _mark(caller, token: str, rendition: str, on: bool):
    return caller("POST", "/api/renditions/plus-one", json={"token": token, "rendition": rendition, "on": on})


def _plus(caller, token: str) -> dict:
    return _extract_docdata(caller("GET", f"/silvercord/{token}").text)["silvercord"]


def test_e2e_an_account_gives_a_plus_one_to_a_rendition_of_this_server_and_takes_it_back(tmp_path: Path) -> None:
    s, env, tokens, callers = _world(tmp_path)
    token = tokens["doc1"]
    # A reader that is an actor gets the route; the author sees the count; a visitor gets the sign-in address.
    bob = _plus(callers["bob"], token)
    assert bob["plus_one"] == {"url": "/api/renditions/plus-one", "sign_in": None}
    assert [(r["template"], r["plus"]) for r in bob["renditions"]] == [("standard", {"score": 0, "mine": False, "own": False}), ("concise", {"score": 0, "mine": False, "own": False})]
    assert _plus(callers["alice"], token)["renditions"][0]["plus"]["own"] is True
    anon = _plus(callers["anon"], token)
    assert anon["plus_one"] == {"url": None, "sign_in": f"/login?next=%2Fsilvercord%2F{token}"}

    given = _mark(callers["bob"], token, "concise", True)
    assert (given.status_code, given.json()) == (200, {"on": True, "score": 1})
    # A second mark of the same account changes nothing; the 대표 정리본 has its own count.
    assert _mark(callers["bob"], token, "concise", True).json() == {"on": True, "score": 1}
    assert [r["plus"] for r in _plus(callers["bob"], token)["renditions"]] == [{"score": 0, "mine": False, "own": False}, {"score": 1, "mine": True, "own": False}]
    # The author cannot give a +1 to an own rendition; a visitor has no route; a document that is not published has no +1.
    assert (_mark(callers["alice"], token, "concise", True).status_code, _mark(callers["alice"], token, "concise", True).json()) == (409, {"error": "own_rendition"})
    assert _mark(callers["anon"], token, "concise", True).status_code in (401, 403, 404)
    assert _mark(callers["bob"], tokens["doc2"], "standard", True).json() == {"error": "rendition_not_found"}
    assert _plus(callers["bob"], tokens["doc2"])["renditions"][0]["plus"] is None
    assert _mark(callers["bob"], token, "academic", True).status_code == 404
    assert _mark(callers["bob"], token, "concise", "yes").status_code == 400
    assert _mark(callers["bob"], token, "concise", False).json() == {"on": False, "score": 0}

    # An account gives at most 60 marks in one hour.
    conn = dbm.connect_existing(env["db_file"])
    try:
        bob_public = conn.execute("SELECT public_id FROM accounts WHERE id=?", (env["bob_id"],)).fetchone()[0]
        actor = f"{s.public_url.rstrip('/')}/ap/actors/{bob_public}"
        conn.executemany("INSERT INTO rendition_plus_ones (object_public_id, actor_iri, actor_host, activity_iri, created_at) VALUES (?, ?, 'x', ?, ?)", [(n, actor, f"{actor}#p/{n}", time.time()) for n in range(1, plus_one_marks.ACCOUNT_HOURLY_LIMIT + 1)])
        conn.commit()
    finally:
        conn.close()
    limited = _mark(callers["bob"], token, "standard", True)
    assert (limited.status_code, limited.json()) == (429, {"error": "rate_limited"})


def test_e2e_a_plus_one_for_a_rendition_of_another_server_goes_to_its_account(tmp_path: Path) -> None:
    s, env, tokens, callers = _world(tmp_path)
    token = tokens["doc1"]
    remote_key = hashlib.sha256(REMOTE.encode()).hexdigest()[:16]
    before = _plus(callers["bob"], token)["remote_renditions"]
    assert [(r["key"], r["plus"]) for r in before] == [(remote_key, {"score": 4, "mine": False, "own": False})]
    given = _mark(callers["bob"], token, f"remote:{remote_key}", True)
    # The count of the origin and the new mark of the account, which the origin did not count at the last read.
    assert (given.status_code, given.json()) == (200, {"on": True, "score": 5})
    assert _plus(callers["bob"], token)["remote_renditions"][0]["plus"] == {"score": 5, "mine": True, "own": False}
    assert _mark(callers["bob"], token, f"remote:{remote_key}", True).json() == {"on": True, "score": 5}
    conn = dbm.connect_existing(env["db_file"])
    try:
        rows = conn.execute("SELECT t.kind, t.signer, t.recipient, a.payload FROM kf_outbox t JOIN kf_outbox_activities a ON a.activity_iri = t.activity_iri ORDER BY t.id").fetchall()
        assert [(r[0], r[2]) for r in rows] == [("plus_one", REMOTE_AUTHOR)]
        activity = json.loads(rows[0][3])
        bob_public = conn.execute("SELECT public_id FROM accounts WHERE id=?", (env["bob_id"],)).fetchone()[0]
        assert rows[0][1] == f"account:{bob_public}"
        assert vocabulary.validate_activity(activity) == "plus_one"
        assert (activity["type"], activity["object"], activity["to"], activity["actor"].endswith(f"/ap/actors/{bob_public}")) == (["Activity", "claire:PlusOne"], REMOTE, [REMOTE_AUTHOR], True)
    finally:
        conn.close()
    # The take back sends the Undo with the mark embedded.
    assert _mark(callers["bob"], token, f"remote:{remote_key}", False).json() == {"on": False, "score": 4}
    conn = dbm.connect_existing(env["db_file"])
    try:
        kinds = [(r[0], json.loads(r[1])) for r in conn.execute("SELECT t.kind, a.payload FROM kf_outbox t JOIN kf_outbox_activities a ON a.activity_iri = t.activity_iri ORDER BY t.id")]
        assert [k for k, _ in kinds] == ["plus_one", "undo_plus_one"]
        assert kinds[1][1]["object"]["id"] == activity["id"]
        assert conn.execute("SELECT COUNT(*) FROM account_plus_ones").fetchone()[0] == 0
        # A blocked domain gets no mark.
        federation_store.add_domain_block(conn, "b.example", created_by=1, now=time.time())
        conn.commit()
    finally:
        conn.close()
    assert _mark(callers["bob"], token, f"remote:{remote_key}", True).status_code == 404
    assert _mark(callers["bob"], token, "remote:0000000000000000", True).status_code == 404
