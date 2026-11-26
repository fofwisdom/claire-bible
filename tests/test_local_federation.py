"""Federation between two accounts of one Claire server (owner decision RD30, FEDERATION_REMOTE_READING_DESIGN.md Section 6.7).

One Claire process and its real sidecar code (``sidecar/fedify/e2e/local_federation_e2e.ts``) with the loopback of ``main.ts``: a request of Fedify to the origin of the server goes to the request handler of the same sidecar. Erin follows Alice; Alice writes a note; Erin gets it, replies, likes, boosts, and quotes it; Alice gets the reply as a mention with a notification, the counts, and the quote request. Each step uses the routes of the federation client, as for an account of another server.

The same as ``tests/test_fedify_sidecar.py``: it needs Deno, and it is skipped without it.
"""

from __future__ import annotations

import json
import subprocess
import time
from pathlib import Path

import httpx
import pytest

from claire.api.federation_internal import federation_socket_path, service_key_path
from claire.store import accounts as accounts_store
from claire.store import client_db
from claire.store import document_renditions as renditions_store
from claire.store import federation as federation_store
from claire.store import permalinks
from tests.test_fedify_sidecar import _clean_env
from tests.test_renditions_exchange import DENO, SIDECAR_DIR, _account, _Server

pytestmark = pytest.mark.skipif(DENO is None, reason="deno is not installed")


def _run_tasks(a: _Server) -> list[str]:
    """Run the sidecar for the tasks that wait, and return its traffic to its own origin."""
    sock = federation_socket_path(a.data_dir)
    env = {**_clean_env(), "NO_COLOR": "1", "CLAIRE_A_SOCKET": str(sock), "CLAIRE_A_KEY_FILE": str(service_key_path(a.data_dir))}
    command = [DENO, "test", "--frozen", f"--allow-net=claire.internal:80,unix:{sock}", "--allow-env", f"--allow-read={SIDECAR_DIR},{a.data_dir}", f"--allow-write={sock}", "e2e/local_federation_e2e.ts"]
    run = subprocess.run(command, cwd=SIDECAR_DIR, env=env, capture_output=True, text=True, timeout=300)
    assert run.returncode == 0, run.stdout[-6000:] + run.stderr[-3000:]
    line = next(line for line in run.stdout.splitlines() if "TRAFFIC " in line)
    return json.loads(line.split("TRAFFIC ", 1)[1])


def test_e2e_two_accounts_of_one_server_follow_reply_like_boost_and_quote(tmp_path: Path) -> None:
    a = _Server(tmp_path, "a")
    conn = a.db()
    try:
        store = accounts_store.AccountStore(conn)
        store.complete_setup(1, username="admin_a", display_name="Admin A")
        store.set_setting("mode", "network", updated_by=1)
        alice = _account(conn, "alice", 116412345678901234)
        erin = _account(conn, "erin", 136412345678901234)
        # A published document of Alice, for the reply and the like from the 의견 tab.
        conn.execute("INSERT INTO knowledge_bases (id, seq, label, is_personal, is_public, created_at, updated_at, account_id, number) VALUES (31, 31, 'alice public', 1, 1, 1, 1, ?, 516412345678901234)", (alice,))
        conn.execute("INSERT INTO documents (id, kb_id, url, title, raw_text, hidden, fetched_at, detail) VALUES ('doc-a', 31, 'https://example.org/paper', 'A paper', 'Text', 0, 1, '= 기본\n\n본문.')")
        renditions_store.record_primary(conn, "doc-a", 31, lineage={}, now=time.time())
        permalinks.ensure_document(conn, "doc-a", 31)
        document, _task, _made = federation_store.publish_document(conn, account_id=alice, kb_id=31, document_id="doc-a", published_by_account_id=alice, salt=store.id_salt(), now=time.time() - 3600)
        permalinks_token = permalinks.token_of(conn, "document", "doc-a")
        sessions = {account: accounts_store.create_account_session(conn, account_id=account, scope="owner") for account in (alice, erin)}
        conn.commit()
    finally:
        conn.close()
    alice_actor, erin_actor = f"{a.origin}/ap/actors/116412345678901234", f"{a.origin}/ap/actors/136412345678901234"
    a.start()
    try:
        def call(account: int, method: str, path: str, body: dict | None = None) -> httpx.Response:
            headers = {"Host": a.host, "Cookie": f"claire_session={sessions[account]}"}
            if method != "GET":
                headers["Origin"] = a.origin
            return httpx.request(method, f"http://127.0.0.1:{a.port}{path}", headers=headers, json=body, timeout=10)

        def rows(sql: str, params: tuple = ()) -> list[tuple]:
            client = client_db.connect_client_existing(client_db.client_db_path(a.data_dir / "claire.db"))
            try:
                return [tuple(r) for r in client.execute(sql, params)]
            finally:
                client.close()

        # 1. Erin follows Alice by her handle: Follow and Accept go through the loopback.
        assert call(erin, "POST", "/api/federation/following", {"target": "@alice@a.claire.test"}).status_code == 202
        traffic = _run_tasks(a)
        assert any(line.startswith("POST a.claire.test/ap/actors/116412345678901234/inbox") or line == "POST a.claire.test/ap/inbox" for line in traffic), traffic
        assert rows("SELECT actor_iri, state FROM federation_following WHERE account_id=?", (erin,)) == [(alice_actor, "accepted")]
        conn = a.db()
        try:
            assert [r[0] for r in conn.execute("SELECT actor_iri FROM federation_followers WHERE account_id=?", (alice,))] == [erin_actor]
        finally:
            conn.close()

        # 2. Alice writes a note: it reaches the home stream of Erin.
        note = call(alice, "POST", "/api/federation/notes", {"content": "Hello from Alice"})
        assert note.status_code == 201
        note_iri = note.json()["note"]["url"]
        _run_tasks(a)
        assert rows("SELECT in_timeline, mention FROM federation_timeline_items WHERE account_id=? AND object_iri=?", (erin, note_iri)) == [(1, 0)]
        item = str(rows("SELECT id FROM federation_timeline_items WHERE account_id=? AND object_iri=?", (erin, note_iri))[0][0])

        # 3. Erin replies, likes, and boosts: Alice gets the reply as a mention with a notification, and the counts of her note.
        reply = call(erin, "POST", "/api/federation/notes", {"content": "@alice@a.claire.test Hello, Alice", "in_reply_to": item})
        assert reply.status_code == 201
        assert call(erin, "POST", f"/api/federation/timeline/{item}/favourite").json()["favourited"] is True
        assert call(erin, "POST", f"/api/federation/timeline/{item}/boost").json()["boosted"] is True
        _run_tasks(a)
        assert rows("SELECT in_timeline, mention FROM federation_timeline_items WHERE account_id=? AND object_iri=?", (alice, reply.json()["note"]["url"])) == [(1, 1)]
        # The new follower and the reply notify Alice, as from another server.
        assert [r[0] for r in rows("SELECT kind FROM notifications WHERE account_id=? ORDER BY id", (alice,))] == ["ap_follower", "ap_mention"]
        note_id = int(note.json()["note"]["id"])
        assert sorted(rows("SELECT kind, actor_iri FROM federation_object_reactions WHERE object_public_id=?", (note_id,))) == [("announce", erin_actor), ("like", erin_actor)]

        # 4. From the 의견 tab of the document of Alice: Erin replies to the document (in_reply_to_object) and likes the post (the favourite of the item of the object). The reply mentions Alice, so its delivery gives her the item and the notification; the Like gives the count.
        document_iri = f"{a.origin}/ap/objects/{document.public_id}"
        on_paper = call(erin, "POST", "/api/federation/notes", {"content": "On the paper", "in_reply_to_object": document_iri})
        assert on_paper.status_code == 201
        post_item = call(erin, "POST", "/api/federation/opinions/item", {"object": document_iri, "opinion": document_iri}).json()
        assert call(erin, "POST", f"/api/federation/timeline/{post_item['item_id']}/favourite").json()["favourited"] is True
        _run_tasks(a)
        assert rows("SELECT in_timeline, mention FROM federation_timeline_items WHERE account_id=? AND object_iri=?", (alice, on_paper.json()["note"]["url"])) == [(1, 1)]
        assert rows("SELECT kind, count FROM notifications WHERE account_id=? ORDER BY id", (alice,)) == [("ap_follower", 1), ("ap_mention", 2)]
        assert rows("SELECT kind, actor_iri FROM federation_object_reactions WHERE object_public_id=?", (document.public_id,)) == [("like", erin_actor)]

        # The opinions of the document: the fetch reads the reply of Erin through the loopback and stores it as an opinion, which Alice can hide as each opinion (Section 5.11 D5).
        page = httpx.get(f"http://127.0.0.1:{a.port}/silvercord/{permalinks_token}/opinions", headers={"Host": a.host}, timeout=10).json()
        assert page["refresh"]["status"] == "running", page
        _run_tasks(a)
        assert rows("SELECT opinion_iri, author_iri FROM federation_opinions WHERE object_iri=?", (document_iri,)) == [(on_paper.json()["note"]["url"], erin_actor)]
        assert call(alice, "POST", "/api/federation/opinions/hide", {"object": document_iri, "opinion": on_paper.json()["note"]["url"], "hidden": True}).json() == {"hidden": True}
        assert httpx.get(f"http://127.0.0.1:{a.port}/silvercord/{permalinks_token}/opinions", headers={"Host": a.host}, timeout=10).json()["opinions"] == []

        # 5. Erin quotes the note: the quote request reaches Alice, and her automatic approval comes back.
        quote = call(erin, "POST", "/api/federation/notes", {"content": "Worth a read", "quote_of": item})
        assert quote.status_code == 201
        _run_tasks(a)
        assert rows("SELECT quote_state FROM federation_notes WHERE public_id=?", (int(quote.json()["note"]["id"]),)) == [("approved",)]
    except BaseException:
        print("A:", a.stop()[-4000:])
        raise
    finally:
        a.stop()
