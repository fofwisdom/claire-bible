"""End-to-end exchange of renditions between two real Claire servers and their real sidecars (DOCUMENT_RENDITIONS_AND_PUBLICATION_DESIGN.md phases E4, E5, and E9).

A (``https://a.claire.test``) publishes a document with the renditions "standard" and "concise". B (``https://b.claire.test``) has a document of the same source and knows A. The test runs two Claire processes (each with its own database and its internal federation socket) and ``sidecar/fedify/e2e/renditions_exchange_e2e.ts`` for three phases. The sidecar code is real; the network between the two sidecars is in the Deno process (a request to the origin of one server goes to the handle function of its sidecar).

1. ``read``: the read queue of B gets the source index, the objects, the bodies, and the account from A with signed requests; B shows the two renditions, and A records B as a reader of each body.
2. ``plus``: an account of B gives a +1 to the rendition "concise"; the knowledge outbox of B delivers it; A stores the mark.
3. ``opinions``: carol of B replied to the document of A (A got the reply), and dan of B replied to carol (A did not get it). A fetches the opinions of its object with requests that its server actor signs: it reads the reply of carol from B and finds the reply of dan in the ``replies`` collection of carol. The 의견 route of A shows the two.
4. ``delete``: A retracts the document; the knowledge outbox of A sends one ``Delete`` for each object to the server actor of B only; B removes its copies.

The same as ``tests/test_fedify_sidecar.py``: it needs Deno, and it is skipped without it.
"""

from __future__ import annotations

import os
import secrets
import shutil
import subprocess
import sys
import time
from pathlib import Path

import httpx
import pytest

from claire.api.federation_internal import federation_socket_path, service_key_path
from claire.federation import plus_one_marks, remote_renditions
from claire.federation.knowledge import known_servers
from claire.ingest.source_key import source_key
from claire.store import accounts as accounts_store
from claire.store import client_db
from claire.store import db as dbm
from claire.store import document_renditions as renditions_store
from claire.store import federation as federation_store
from claire.store import permalinks
from tests.test_fedify_sidecar import _clean_env, _free_port, _stop

DENO = shutil.which("deno")
pytestmark = pytest.mark.skipif(DENO is None, reason="deno is not installed")
SIDECAR_DIR = Path(__file__).resolve().parents[1] / "sidecar" / "fedify"
KEY = source_key("https://arxiv.org/abs/2101.00001")


class _Server:
    """One Claire process with its data folder."""

    def __init__(self, tmp_path: Path, name: str) -> None:
        self.root = tmp_path / name
        self.data_dir = self.root / "data"
        self.port = _free_port()
        self.host = f"{name}.claire.test"
        # Decision CD4: a development server with a name below ".test" has an https origin.
        self.origin = f"https://{self.host}"
        self.env = _clean_env()
        self.env.update(
            CLAIRE_APP_ROOT=str(self.root),
            CLAIRE_ENVIRONMENT="development",
            CLAIRE_PROVIDER="mock",
            CLAIRE_DB_PATH=str(self.data_dir / "claire.db"),
            CB_DATA_DIR=str(self.data_dir),
            CLAIRE_INJECT_HOST="127.0.0.1",
            CLAIRE_INJECT_PORT=str(self.port),
            CLAIRE_FQDN=self.host,
            # The development switch: the two servers have names that no DNS resolves (FEDERATION.md, contract 1.8).
            CLAIRE_ALLOW_PRIVATE_NETWORKS="true",
            CLAIRE_INJECT_TOKEN=secrets.token_urlsafe(32),
            PYTHONDONTWRITEBYTECODE="1",
        )
        self.root.mkdir(parents=True)
        migrate = subprocess.run([sys.executable, "-m", "claire_bible.cli", "migrate"], cwd=self.root, env=self.env, capture_output=True, text=True)
        assert migrate.returncode == 0, migrate.stdout + migrate.stderr
        self.proc: subprocess.Popen | None = None

    def db(self) -> dbm.sqlite3.Connection:  # type: ignore[name-defined]
        return dbm.connect_existing(self.data_dir / "claire.db")

    def start(self) -> None:
        self.proc = subprocess.Popen([sys.executable, "-m", "claire_bible.cli", "serve-api"], cwd=self.root, env=self.env, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
        deadline = time.monotonic() + 60
        sock = federation_socket_path(self.data_dir)
        while True:
            assert self.proc.poll() is None, self.proc.stdout.read() if self.proc.stdout else ""
            try:
                if httpx.get(f"http://127.0.0.1:{self.port}/health", headers={"Host": self.host}, timeout=2).status_code == 200 and sock.exists():
                    return
            except httpx.HTTPError:
                pass
            assert time.monotonic() < deadline, "serve-api did not start in time"
            time.sleep(0.2)

    def stop(self) -> str:
        return "" if self.proc is None else _stop(self.proc)


def _account(conn, username: str, public_id: int) -> int:
    conn.execute("INSERT INTO accounts (public_id, username, role, status, display_name, profile_public, created_at, activated_at, updated_at) VALUES (?, ?, 'member', 'active', ?, 1, 0, 1, 0)", (public_id, username, username.title()))
    return accounts_store.AccountStore(conn).find_by_username(username).id


def _prepare(a: _Server, b: _Server) -> dict[str, int]:
    now = time.time()
    conn = a.db()
    try:
        store = accounts_store.AccountStore(conn)
        store.complete_setup(1, username="admin_a", display_name="Admin A")
        store.set_setting("mode", "network", updated_by=1)
        alice = _account(conn, "alice", 116412345678901234)
        conn.execute("INSERT INTO knowledge_bases (id, seq, label, is_personal, is_public, created_at, updated_at, account_id, number) VALUES (31, 31, 'alice public', 1, 1, 1, 1, ?, 516412345678901234)", (alice,))
        conn.execute("INSERT INTO documents (id, kb_id, url, title, raw_text, hidden, fetched_at, source_key, source_id_version, detail) VALUES ('doc-a', 31, 'https://arxiv.org/abs/2101.00001', 'Same paper', 'Text', 0, 1, ?, 1, '= 기본\n\n기본 본문.')", (KEY,))
        renditions_store.record_primary(conn, "doc-a", 31, lineage={}, now=now)
        renditions_store.store_additional(conn, "doc-a", 31, template="concise", detail="= 간결\n\n== 핵심\n\n* 간결 본문.", lineage={}, now=now)
        permalinks.ensure_document(conn, "doc-a", 31)
        # Published one hour ago: a fetch of its opinions is due (an object older than five minutes, DOCUMENT_RENDITIONS_AND_PUBLICATION_DESIGN.md Section 5.11 B2).
        obj, _task, _made = federation_store.publish_document(conn, account_id=alice, kb_id=31, document_id="doc-a", published_by_account_id=alice, salt=store.id_salt(), now=now - 3600)
        conn.commit()
        concise = federation_store.rendition_objects_of(conn, obj.public_id)[0].public_id
    finally:
        conn.close()
    conn = b.db()
    try:
        store = accounts_store.AccountStore(conn)
        store.complete_setup(1, username="admin_b", display_name="Admin B")
        store.set_setting("mode", "network", updated_by=1)
        carol = _account(conn, "carol", 216412345678901234)
        conn.execute("INSERT INTO knowledge_bases (id, seq, label, is_personal, is_public, created_at, updated_at, account_id, number) VALUES (31, 31, 'carol public', 1, 1, 1, 1, ?, 526412345678901234)", (carol,))
        conn.execute("INSERT INTO documents (id, kb_id, url, title, raw_text, hidden, fetched_at, source_key, source_id_version, detail) VALUES ('doc-b', 31, 'https://arxiv.org/pdf/2101.00001v2', 'Same paper', 'Text', 0, 1, ?, 1, '= 기본\n\n다른 본문.')", (KEY,))
        renditions_store.record_primary(conn, "doc-b", 31, lineage={}, now=now)
        known_servers.note_server(conn, a.host, source="admin", added_by=1, now=now)
        # A reader opens the Silvercord page of the document of B: the page asks A for its renditions of the source (the page calls the same function).
        assert remote_renditions.request_set(conn, KEY, origin=b.origin, now=now) == 1
        conn.commit()
    finally:
        conn.close()
    return {"document": obj.public_id, "concise": concise, "alice": alice, "carol": carol}


def _phase(a: _Server, b: _Server, phase: str, **extra: str) -> None:
    sockets = [federation_socket_path(a.data_dir), federation_socket_path(b.data_dir)]
    env = {**_clean_env(), "NO_COLOR": "1", "E2E_PHASE": phase, "CLAIRE_A_SOCKET": str(sockets[0]), "CLAIRE_A_KEY_FILE": str(service_key_path(a.data_dir)), "CLAIRE_B_SOCKET": str(sockets[1]), "CLAIRE_B_KEY_FILE": str(service_key_path(b.data_dir)), **extra}
    command = [DENO, "test", "--frozen", "--allow-net=claire.internal:80," + ",".join(f"unix:{s}" for s in sockets), "--allow-env", f"--allow-read={SIDECAR_DIR},{a.data_dir},{b.data_dir}", "--allow-write=" + ",".join(str(s) for s in sockets), "e2e/renditions_exchange_e2e.ts"]
    run = subprocess.run(command, cwd=SIDECAR_DIR, env=env, capture_output=True, text=True, timeout=300)
    assert run.returncode == 0, f"phase {phase}:\n" + run.stdout[-6000:] + run.stderr[-3000:]


def test_e2e_two_claire_servers_exchange_renditions_plus_ones_and_deletes(tmp_path: Path) -> None:
    a, b = _Server(tmp_path, "a"), _Server(tmp_path, "b")
    ids = _prepare(a, b)
    try:
        a.start()
        b.start()

        # 1. B reads the renditions of A.
        _phase(a, b, "read")
        conn = b.db()
        try:
            shown = remote_renditions.renditions_of(conn, KEY, now=time.time())
        finally:
            conn.close()
        assert [(r.iri, r.template, r.account_handle, r.rendition_of) for r in shown] == [
            (f"{a.origin}/ap/objects/{ids['document']}", "standard", "@alice@a.claire.test", None),
            (f"{a.origin}/ap/objects/{ids['concise']}", "concise", "@alice@a.claire.test", f"{a.origin}/ap/objects/{ids['document']}"),
        ]
        assert "간결 본문" in shown[1].body_html and shown[1].body_source.startswith("= 간결")
        conn = a.db()
        try:
            readers = sorted(tuple(r) for r in conn.execute("SELECT object_public_id, server_actor_iri FROM federation_body_readers"))
        finally:
            conn.close()
        assert readers == sorted([(ids["document"], f"{b.origin}/ap/actors/server"), (ids["concise"], f"{b.origin}/ap/actors/server")])

        # 2. carol of B gives a +1 to the rendition "concise" of A.
        conn = b.db()
        try:
            assert plus_one_marks.give_remote(conn, account_id=ids["carol"], account_public_id=216412345678901234, rendition=shown[1], origin=b.origin, now=time.time()) is True
            conn.commit()
        finally:
            conn.close()
        _phase(a, b, "plus", E2E_RENDITION=str(ids["concise"]))
        conn = a.db()
        try:
            marks = [tuple(r) for r in conn.execute("SELECT object_public_id, actor_iri, actor_host FROM rendition_plus_ones")]
            journal = [tuple(r) for r in conn.execute("SELECT kind, status, error_code FROM kf_inbox_journal")]
        finally:
            conn.close()
        assert journal == [("plus_one", "processed", None)]
        assert marks == [(ids["concise"], f"{b.origin}/ap/actors/216412345678901234", "b.claire.test")]

        # 3. The opinions of the document of A from B.
        object_iri = f"{a.origin}/ap/objects/{ids['document']}"
        carol_note, dan_note = 916412345678901001, 916412345678901002
        conn = b.db()
        try:
            dan = _account(conn, "dan", 316412345678901234)
            conn.commit()
        finally:
            conn.close()
        client = client_db.connect_client_existing(client_db.client_db_path(b.data_dir / "claire.db"))
        try:
            for public_id, account_id, parent, text in ((carol_note, ids["carol"], object_iri, "Carol on the paper"), (dan_note, dan, f"{b.origin}/ap/objects/{carol_note}", "Dan answers Carol")):
                client.execute("INSERT INTO federation_notes (public_id, account_id, in_reply_to_iri, content_text, content_html, published_at, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?)", (public_id, account_id, parent, text, f"<p>{text}</p>", time.time() - 120, time.time() - 120))
            client.commit()
        finally:
            client.close()
        client = client_db.connect_client_existing(client_db.client_db_path(a.data_dir / "claire.db"))
        try:
            # The delivery of the reply of carol to alice (the timeline of alice has it); a page of the document asks for a fetch.
            client.execute("INSERT INTO federation_timeline_items (account_id, object_iri, object_type, author_iri, author_json, content_html, in_reply_to_iri, received_at) VALUES (?, ?, 'Note', ?, '{}', '<p>Carol on the paper</p>', ?, ?)", (ids["alice"], f"{b.origin}/ap/objects/{carol_note}", f"{b.origin}/ap/actors/216412345678901234", object_iri, time.time()))
            client.commit()
        finally:
            client.close()
        conn = a.db()
        try:
            token = permalinks.token_of(conn, "document", "doc-a")
        finally:
            conn.close()
        page = httpx.get(f"http://127.0.0.1:{a.port}/silvercord/{token}/opinions", headers={"Host": a.host}, timeout=10).json()
        assert page["refresh"]["status"] == "running", page
        _phase(a, b, "opinions")
        page = httpx.get(f"http://127.0.0.1:{a.port}/silvercord/{token}/opinions", headers={"Host": a.host}, timeout=10).json()
        assert page["refresh"]["status"] == "finished" and page["refresh"]["result_count"] == 2, page
        assert [(o["iri"], o["parent"], o["author"]["handle"], o["text"]) for o in page["opinions"]] == [
            (f"{b.origin}/ap/objects/{carol_note}", object_iri, "@carol@b.claire.test", [[{"text": "Carol on the paper"}]]),
            (f"{b.origin}/ap/objects/{dan_note}", f"{b.origin}/ap/objects/{carol_note}", "@dan@b.claire.test", [[{"text": "Dan answers Carol"}]]),
        ]

        # 4. alice of A retracts the document: B, the reader of the two bodies, gets one Delete for each object and removes its copies.
        conn = a.db()
        try:
            federation_store.retract_object(conn, federation_store.get_object(conn, ids["document"]), now=time.time())
            conn.commit()
        finally:
            conn.close()
        _phase(a, b, "delete")
        conn = b.db()
        try:
            journal = sorted(tuple(r) for r in conn.execute("SELECT kind, status, error_code FROM kf_inbox_journal"))
            left = conn.execute("SELECT COUNT(*) FROM remote_renditions").fetchone()[0]
        finally:
            conn.close()
        assert journal == [("delete_rendition", "processed", None), ("delete_rendition", "processed", None)]
        assert left == 0
    except BaseException:
        print("A:", a.stop()[-3000:])
        print("B:", b.stop()[-3000:])
        raise
    finally:
        a.stop()
        b.stop()
