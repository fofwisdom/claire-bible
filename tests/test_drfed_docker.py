"""Development test only: scenarios against DrFed 0.1.0 in Docker (FEDERATION_REMOTE_READING_DESIGN.md unit FP4-10).

The stack of ``tests/drfed_stack.py`` is a model of a remote ActivityPub server: DrFed is behind a TLS gateway with a wildcard certificate of a private certificate authority, and each DrFed server has a subdomain of the root origin. DrFed 0.1.0 records the deliveries that arrive at an inbox; it does not send activities to a remote inbox.

The tests start containers and build an image from the npm registry, thus they run only with ``CB_DRFED_TEST=1`` and a Docker daemon.
"""

from __future__ import annotations

import json
import os
import secrets
import socket
import subprocess
import sys
import time
from pathlib import Path

import httpx
import pytest

from claire.api.federation_internal import federation_socket_path
from claire.store import accounts as accounts_store
from claire.store import client_db
from claire.store import db as dbm
from claire.store import federation as federation_store
from tests import drfed_stack

REPO_ROOT = Path(__file__).resolve().parent.parent

pytestmark = pytest.mark.skipif(os.environ.get("CB_DRFED_TEST") != "1", reason="set CB_DRFED_TEST=1 to run the DrFed tests in Docker")

AS_JSON = {"accept": "application/activity+json"}
PUBLIC = "https://www.w3.org/ns/activitystreams#Public"
CREATE_OBJECT = "mutation($actor: ID!, $html: String!, $to: [URL!]!) { createObject(actor: $actor, contentHtml: $html, addressing: {to: $to}) { __typename ... on Object { id iri } ... on CreateObjectError { type message } } }"


@pytest.fixture(scope="module")
def stack(tmp_path_factory):
    if not drfed_stack.docker_available():
        pytest.skip("Docker is not available")
    running = drfed_stack.start(tmp_path_factory.mktemp("drfed") / "run", project=f"claire-drfed-pytest-{secrets.token_hex(4)}")
    yield running
    drfed_stack.stop(running.run_dir, running.project)


# --- The stack ------------------------------------------------------------------


def test_helper_signs_in_and_makes_an_server_with_actors(stack):
    """The helper does all steps without the web UI: the login link comes from the log of DrFed, and GraphQL makes the server and the actors."""
    assert stack.access_token and stack.server_id and len(stack.actors) == 2
    viewer = stack.graphql("{ viewer { email } }")["viewer"]
    assert viewer["email"] == stack.email
    # Without the access token DrFed knows no account.
    assert stack.graphql("{ viewer { email } }", token=False)["viewer"] is None
    for actor in stack.actors:
        assert actor.iri.startswith(stack.server_origin + "/") and actor.inbox.startswith(stack.server_origin + "/") and actor.handle.endswith(f"@{stack.server_slug}.{stack.root_name}")


def test_each_name_below_the_root_resolves_and_only_the_gateway_port_is_open(stack):
    """A container on the network ``edge`` with the DNS server of the stack: each name below the root origin is the gateway, and only port 443 is open. DrFed does not listen on its address of ``edge``."""
    names = stack.probe(f"for n in {stack.root_name} {stack.server_slug}.{stack.root_name} a-new-name.{stack.root_name}; do nslookup $n | awk '/^Address/ && !/:53/ {{print $2}}' | tail -1; done").stdout.split()
    assert names == [stack.gateway_ip] * 3
    ports = stack.probe(f"for p in 443 80 8888; do nc -z -w 2 any.{stack.root_name} $p; echo $p=$?; done; nc -z -w 2 {stack.edge_prefix}.4 8888; echo drfed=$?").stdout.split()
    assert ports == ["443=0", "80=1", "8888=1", "drfed=1"]
    published = stack.compose("ps", "--format", "json").stdout
    assert "0.0.0.0" not in published and "[::]" not in published and f"127.0.0.1:{stack.loopback_port}" in published


def test_tls_uses_the_wildcard_certificate_of_the_private_authority(stack):
    with stack.client(token=False) as trusted:
        assert trusted.get(stack.server_origin + "/.well-known/nodeinfo").status_code == 200
    # A client that does not know the certificate authority stops before a request goes out.
    with httpx.Client(transport=httpx.HTTPTransport(), timeout=10) as unknown, pytest.raises(httpx.ConnectError):
        unknown.get(f"https://{stack.gateway_ip}/", headers={"host": stack.root_name})


def test_the_gateway_keeps_the_host_header(stack):
    """DrFed selects the surface from the Host header: a server name serves ActivityPub only, and the root name serves GraphQL."""
    actor = stack.actors[0]
    with stack.client(token=False) as client:
        assert client.get(actor.iri, headers=AS_JSON).json()["id"] == actor.iri
        assert client.post(stack.server_origin + "/graphql", json={"query": "{ __typename }"}).status_code == 404
        assert client.post(stack.root_origin + "/graphql", json={"query": "{ __typename }"}).status_code == 200


# --- What a remote server reads from DrFed ----------------------------------------


def test_actor_webfinger_and_a_public_note_are_readable(stack):
    actor = stack.actors[0]
    user = actor.handle.strip("@").split("@")[0]
    with stack.client(token=False) as client:
        finger = client.get(stack.server_origin + "/.well-known/webfinger", params={"resource": f"acct:{user}@{stack.server_slug}.{stack.root_name}"}).json()
        assert {"rel": "self", "href": actor.iri, "type": "application/activity+json"} in finger["links"]
        document = client.get(actor.iri, headers=AS_JSON).json()
        assert document["type"] == "Person" and document["inbox"] == actor.inbox and document["preferredUsername"] == user
        assert document["publicKey"]["owner"] == actor.iri and "BEGIN PUBLIC KEY" in document["publicKey"]["publicKeyPem"]
    made = stack.graphql(CREATE_OBJECT, {"actor": actor.id, "html": "<p>A public note of DrFed.</p>", "to": [PUBLIC]})["createObject"]
    assert made["__typename"] == "Object", made
    with stack.client(token=False) as client:
        note = client.get(made["iri"], headers=AS_JSON).json()
        assert note["type"] == "Note" and note["attributedTo"] == actor.iri and note["content"] == "<p>A public note of DrFed.</p>"
        # DrFed writes the public collection in the short form. The three forms have the same meaning (design Section 6.2 rule 1).
        assert {PUBLIC, "as:Public", "Public"} & set([note["to"]] if isinstance(note["to"], str) else note["to"])
        # DrFed 0.1.0 gives no ``replies`` and no ``context`` collection: the thread fetch (unit FP4-6) has nothing to read there. A later DrFed release that adds one makes this line fail; add the thread fetch to the scenarios then.
        assert "replies" not in note and "context" not in note
        outbox = client.get(document["outbox"], headers=AS_JSON).json()
        assert outbox["type"] == "OrderedCollection" and outbox["totalItems"] >= 1


# --- Claire and DrFed -----------------------------------------------------------------
#
# A real Claire process runs on the host in the development environment with CLAIRE_ALLOW_PRIVATE_NETWORKS on (contract 1.8: the sidecar can then read documents at the private addresses of the stack). Its Fedify sidecar is the service ``sidecar`` of the stack. The public origin of Claire is ``http://{gateway}:8080`` (the development rule of Claire: http and an IPv4 address).
# DrFed 0.1.0 sends no activity to a remote inbox: no ``Accept`` for a follow, and no inbound mention, favourite, boost, automatic notice, or quote request. Those are outside the range of these scenarios.

ALICE_PUBLIC_ID = 116412345678901234
THEME_NUMBER = 516412345678901234
DELIVERIES = "query($id: ID!, $type: String) { node(id: $id) { ... on Instance { activityDeliveries(first: 50, filter: {direction: inbound, type: $type}) { edges { node { status verificationResult remoteActorIri signedKeyIri error payload } } } } } }"


class _Claire:
    """A Claire process with its sidecar, and the session of the account ``alice``."""

    def __init__(self, stack, root: Path) -> None:
        self.stack, self.root, self.data = stack, root, root / "data"
        self.port = _free_port()
        self.origin = stack.claire_origin
        self.actor = f"{self.origin}/ap/actors/{ALICE_PUBLIC_ID}"
        self.proc: subprocess.Popen | None = None
        self.session = ""
        self.account_id = 0

    def start(self) -> None:
        env = {k: v for k, v in os.environ.items() if not k.startswith(("CLAIRE_", "CB_"))}
        env.update(CLAIRE_APP_ROOT=str(self.root), CLAIRE_ENVIRONMENT="development", CLAIRE_PROVIDER="mock", CLAIRE_DB_PATH=str(self.data / "claire.db"), CB_DATA_DIR=str(self.data), CLAIRE_INJECT_HOST="127.0.0.1", CLAIRE_INJECT_PORT=str(self.port), CLAIRE_FQDN=self.origin.split("//", 1)[1], CLAIRE_ALLOW_PRIVATE_NETWORKS="1", CLAIRE_INJECT_TOKEN=secrets.token_urlsafe(32), PYTHONDONTWRITEBYTECODE="1", PYTHONPATH=str(REPO_ROOT / "src"))
        migrate = subprocess.run([sys.executable, "-m", "claire_bible.cli", "migrate"], cwd=self.root, env=env, capture_output=True, text=True)
        assert migrate.returncode == 0, migrate.stdout + migrate.stderr
        target = self.stack.actors[0]
        conn = dbm.connect_existing(self.data / "claire.db")
        try:
            store = accounts_store.AccountStore(conn)
            store.complete_setup(1, username="admin_kim", display_name="Admin Kim")
            store.set_setting("mode", "network", updated_by=1)
            conn.execute("INSERT INTO accounts (public_id, username, role, status, display_name, profile_public, created_at, activated_at, updated_at) VALUES (?, 'alice', 'member', 'active', 'Alice Kim', 1, 0, 1800000100.5, 0)", (ALICE_PUBLIC_ID,))
            self.account_id = store.find_by_username("alice").id
            conn.execute("INSERT INTO knowledge_bases (id, seq, label, is_personal, is_public, created_at, updated_at, account_id, number) VALUES (31, 31, 'alice public', 0, 1, 1, 1, ?, ?)", (self.account_id, THEME_NUMBER))
            conn.execute("INSERT INTO documents (id, kb_id, title, detail, detail_format, hidden, fetched_at) VALUES ('doc-1', 31, 'First Post', 'The body stays on Claire.', 'adoc', 0, 1)")
            # DrFed cannot send a Follow. The follower row is as if the DrFed actor followed alice: a published document and a note go to the followers.
            federation_store.upsert_follower(conn, account_id=self.account_id, actor_iri=target.iri, inbox_iri=target.inbox, shared_inbox_iri=None, salt=store.id_salt(), now=time.time())
            conn.commit()
            self.session = accounts_store.create_account_session(conn, account_id=self.account_id, scope="owner", now=time.time())
        finally:
            conn.close()
        self.log = open(self.root / "claire.log", "w")
        self.proc = subprocess.Popen([sys.executable, "-m", "claire_bible.cli", "serve-api"], cwd=self.root, env=env, stdout=self.log, stderr=subprocess.STDOUT)
        socket_path = federation_socket_path(self.data)
        self._wait(lambda: self.proc.poll() is None and socket_path.exists() and self.api("GET", "/health").status_code == 200, "the Claire process did not start")
        self.stack.start_sidecar(self.data)
        self._wait(lambda: self.public(f"/ap/actors/{ALICE_PUBLIC_ID}").status_code == 200, "the sidecar of Claire did not answer through the gateway")

    def _wait(self, check, message: str, seconds: float = 120.0) -> None:
        deadline = time.monotonic() + seconds
        while time.monotonic() < deadline:
            try:
                if check():
                    return
            except httpx.HTTPError:
                pass
            time.sleep(0.5)
        pytest.fail(f"{message}: {(self.root / 'claire.log').read_text()[-1500:]} sidecar: {self.stack.logs('sidecar')[-1500:]}")

    def api(self, method: str, path: str, body: dict | None = None) -> httpx.Response:
        """A request of the owner to the app (the loopback port of the process, with the public authority as the Host header)."""
        headers = {"host": self.origin.split("//")[1], "Origin": self.origin, "Cookie": f"claire_session={self.session}"}
        return httpx.request(method, f"http://127.0.0.1:{self.port}{path}", json=body, headers=headers, timeout=20)

    def public(self, path: str) -> httpx.Response:
        """A request of a remote server to the public origin of Claire (the gateway and the sidecar)."""
        return httpx.get(f"{self.origin}{path}", headers=AS_JSON, timeout=10)

    def add_item(self, object_iri: str, author: drfed_stack.DrFedActor, content_html: str) -> str:
        """Store a public note of DrFed as an item of the timeline, as if it arrived in an inbox (DrFed sends no activity). Return the item ID."""
        conn = client_db.connect_client_existing(client_db.client_db_path(self.data / "claire.db"))
        try:
            profile = {"iri": author.iri, "type": "Person", "handle": author.handle, "name": author.handle, "url": author.iri, "avatar": None}
            cur = conn.execute("INSERT INTO federation_timeline_items (account_id, object_iri, url, object_type, author_iri, author_json, content_html, received_at, published_at) VALUES (?, ?, ?, 'Note', ?, ?, ?, ?, ?)", (self.account_id, object_iri, object_iri, author.iri, json.dumps(profile, sort_keys=True), content_html, time.time(), time.time()))
            conn.commit()
            return str(cur.lastrowid)
        finally:
            conn.close()

    def deliveries(self, activity_type: str, count: int = 1, seconds: float = 90.0) -> list[dict]:
        """Wait until DrFed has ``count`` inbound deliveries of one activity type from alice. Oldest first."""
        deadline = time.monotonic() + seconds
        found: list[dict] = []
        while time.monotonic() < deadline:
            edges = self.stack.graphql(DELIVERIES, {"id": self.stack.server_id, "type": activity_type})["node"]["activityDeliveries"]["edges"]
            found = [edge["node"] for edge in reversed(edges) if edge["node"]["remoteActorIri"] == self.actor]
            if len(found) >= count:
                return found
            time.sleep(1.0)
        pytest.fail(f"DrFed has {len(found)} deliveries of {activity_type}, not {count}. sidecar: {self.stack.logs('sidecar')[-2000:]}")

    def stop(self) -> None:
        self.stack.stop_sidecar()
        if self.proc is not None:
            self.proc.terminate()
            try:
                self.proc.wait(timeout=20)
            except subprocess.TimeoutExpired:
                self.proc.kill()
            self.log.close()


def _free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


@pytest.fixture(scope="module")
def claire(stack, tmp_path_factory):
    server = _Claire(stack, tmp_path_factory.mktemp("claire"))
    try:
        server.start()
        yield server
    finally:
        server.stop()


def _verified(delivery: dict, claire) -> dict:
    """DrFed took the delivery and verified its signature with the key of alice. Return the JSON-LD body."""
    assert delivery["status"] == "received" and delivery["verificationResult"] == "verified" and delivery["error"] is None, delivery
    assert delivery["signedKeyIri"].startswith(claire.actor + "#")
    assert delivery["payload"]["actor"] == claire.actor
    return delivery["payload"]


def _ids(value) -> list[str]:
    values = value if isinstance(value, list) else [value]
    return [v if isinstance(v, str) else v.get("id") or v.get("href") for v in values if v is not None]


def test_follow_arrives_with_a_valid_signature_and_the_sidecar_resolves_the_handle(stack, claire):
    target = stack.actors[0]
    made = claire.api("POST", "/api/federation/following", {"target": target.handle})
    assert made.status_code == 202, made.text
    follow = _verified(claire.deliveries("Follow")[0], claire)
    assert follow["type"] == "Follow" and _ids(follow["object"]) == [target.iri] and follow["id"].startswith(claire.actor + "#follows/")
    # The sidecar read the WebFinger answer and the actor document of DrFed (TLS with the private certificate authority, a private address).
    row = claire.api("GET", "/api/federation/following").json()["items"][0]
    assert (row["actor_iri"], row["actor_type"]) == (target.iri, "Person")
    # DrFed 0.1.0 sends no Accept: the follow stays pending.
    assert row["state"] == "pending"


def test_published_document_arrives_as_create_and_update(stack, claire):
    first = claire.api("POST", "/api/federation/publish", {"theme": str(THEME_NUMBER), "document_id": "doc-1"})
    assert first.status_code == 202, first.text
    object_iri = first.json()["object"]["url"]
    create = _verified(claire.deliveries("Create")[0], claire)
    article = create["object"]
    assert create["type"] == "Create" and article["type"] == "Article" and article["id"] == object_iri and article["name"] == "First Post" and article["attributedTo"] == claire.actor
    # The object has the title (no summary yet) and the link to the share page, not the body (RD21).
    assert "First Post" in article["content"] and f"{claire.origin}/p?s=" in article["content"] and "The body stays on Claire." not in article["content"]
    assert PUBLIC in _ids(create["to"]) or "as:Public" in _ids(create["to"])
    assert claire.api("POST", "/api/federation/publish", {"theme": str(THEME_NUMBER), "document_id": "doc-1"}).status_code == 202
    update = _verified(claire.deliveries("Update")[0], claire)
    assert update["object"]["id"] == object_iri and update["object"]["type"] == "Article"
    # A remote server reads the object at its address.
    assert claire.public(object_iri.removeprefix(claire.origin)).json()["id"] == object_iri


def test_reply_edit_favourite_boost_and_delete_arrive(stack, claire):
    author = stack.actors[0]
    made = stack.graphql(CREATE_OBJECT, {"actor": author.id, "html": "<p>A note to answer.</p>", "to": [PUBLIC]})["createObject"]
    item = claire.add_item(made["iri"], author, "<p>A note to answer.</p>")
    creates_before = len(claire.deliveries("Create", 1))

    # Reply: Create(Note) with inReplyTo and a Mention of the author of the parent.
    reply = claire.api("POST", "/api/federation/notes", {"content": "An answer from Claire #drfed", "in_reply_to": item, "summary": "test", "language": "en"})
    assert reply.status_code == 201, reply.text
    note = reply.json()["note"]
    create = _verified(claire.deliveries("Create", creates_before + 1)[-1], claire)
    sent = create["object"]
    assert sent["type"] == "Note" and sent["id"] == note["url"] and sent["inReplyTo"] == made["iri"] and sent["attributedTo"] == claire.actor
    tags = sent["tag"] if isinstance(sent["tag"], list) else [sent["tag"]]
    assert {"Mention", "Hashtag"} <= {tag["type"] for tag in tags} and author.iri in [tag.get("href") for tag in tags if tag["type"] == "Mention"]
    assert sent["summary"] == "test" and sent["sensitive"] is True and sent["contentMap"] == {"en": sent["content"]} and "An answer from Claire" in sent["content"]
    assert author.iri in _ids(sent.get("cc", [])) + _ids(sent.get("to", []))

    # Edit: Update(Note) with `updated`.
    assert claire.api("PATCH", f"/api/federation/notes/{note['id']}", {"content": "A changed answer"}).status_code == 200
    update = _verified([d for d in claire.deliveries("Update", 2) if d["payload"]["object"]["type"] == "Note"][-1], claire)
    assert update["object"]["id"] == note["url"] and "A changed answer" in update["object"]["content"] and update["object"]["updated"]

    # Favourite and boost of the DrFed note.
    assert claire.api("POST", f"/api/federation/timeline/{item}/favourite").json()["favourited"] is True
    like = _verified(claire.deliveries("Like")[0], claire)
    assert _ids(like["object"]) == [made["iri"]] and like["id"].startswith(claire.actor + "#likes/")
    assert claire.api("POST", f"/api/federation/timeline/{item}/boost").json()["boosted"] is True
    announce = _verified(claire.deliveries("Announce")[0], claire)
    assert _ids(announce["object"]) == [made["iri"]] and (PUBLIC in _ids(announce["to"]) or "as:Public" in _ids(announce["to"]))
    assert claire.api("DELETE", f"/api/federation/timeline/{item}/favourite").json()["favourited"] is False
    undo = _verified(claire.deliveries("Undo")[0], claire)
    assert undo["object"]["type"] == "Like" and undo["object"]["id"] == like["id"]

    # Delete: Delete with a Tombstone, and the object address gives 410.
    assert claire.api("DELETE", f"/api/federation/notes/{note['id']}").status_code == 200
    delete = _verified(claire.deliveries("Delete")[0], claire)
    assert delete["object"]["type"] == "Tombstone" and delete["object"]["id"] == note["url"]
    gone = claire.public(note["url"].removeprefix(claire.origin))
    assert gone.status_code == 410 and gone.json()["type"] == "Tombstone" and gone.json()["formerType"] == "Note"


def test_thread_fetch_reads_the_note_of_drfed_and_finds_no_collection(stack, claire):
    """Unit FP4-6 against DrFed: the sidecar reads the object at DrFed. DrFed 0.1.0 gives no ``context`` and no ``replies`` collection, thus the fetch stores nothing and the task ends."""
    author = stack.actors[1]
    made = stack.graphql(CREATE_OBJECT, {"actor": author.id, "html": "<p>The start of a thread.</p>", "to": [PUBLIC]})["createObject"]
    item = claire.add_item(made["iri"], author, "<p>The start of a thread.</p>")
    asked = claire.api("POST", f"/api/federation/timeline/{item}/context")
    assert asked.status_code == 202 and asked.json() == {"state": "queued", "pending": True}, asked.text
    claire._wait(lambda: claire.api("POST", f"/api/federation/timeline/{item}/context").json() == {"state": "recent", "pending": False}, "the thread fetch task did not end", seconds=60)
    thread = claire.api("GET", f"/api/federation/timeline/{item}").json()["thread"]
    assert [entry["id"] for entry in thread] == [item]


def test_quote_request_arrives_and_the_quote_stays_pending(stack, claire):
    """Unit FP4-9Q against DrFed: the request and the note with the quote arrive. DrFed 0.1.0 sends no answer, thus the quote stays ``pending``."""
    author = stack.actors[1]
    made = stack.graphql(CREATE_OBJECT, {"actor": author.id, "html": "<p>A note to quote.</p>", "to": [PUBLIC]})["createObject"]
    item = claire.add_item(made["iri"], author, "<p>A note to quote.</p>")
    quoted = claire.api("POST", "/api/federation/notes", {"content": "A quote from Claire", "quote_of": item})
    assert quoted.status_code == 201, quoted.text
    note = quoted.json()["note"]
    request = _verified(claire.deliveries("QuoteRequest")[0], claire)
    assert _ids(request["object"]) == [made["iri"]] and _ids(request["instrument"]) == [note["url"]]
    create = _verified([d for d in claire.deliveries("Create", 2) if d["payload"]["object"].get("id") == note["url"]][-1], claire)
    assert create["object"]["quote"] == made["iri"] and made["iri"] in create["object"]["content"]
    assert claire.api("GET", f"/api/federation/timeline/{quoted.json()['item_id']}").json()["item"]["note"]["quote"] == {"iri": made["iri"], "state": "pending"}
