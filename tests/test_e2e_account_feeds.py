"""End-to-end test of the account feeds (FEDERATION_REMOTE_READING_DESIGN.md Section 1.3 rule 7, units FP4-F1 and FP4-F2).

The real ``server.create_app`` and real ``Settings``. A local HTTP server serves an RSS 2.0 feed (with ``ETag``) and an Atom 1.0 feed (with ``Last-Modified``) and answers conditional requests with ``304``. All servers are local; the test sets ``CLAIRE_ALLOW_PRIVATE_NETWORKS`` so that the private address check allows the loopback address.
"""

from __future__ import annotations

import os
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from types import SimpleNamespace

import pytest
from lxml import etree
from starlette.testclient import TestClient

from claire.api import server
from claire.config import Settings
from claire.ontology.base import Document
from claire.store import accounts as accounts_store
from claire.store import db as dbm
from claire.store.secrets import reset_secret_service
from claire.store.theme import ThemeManager

PUBLIC_URL = "http://127.0.0.1:8765"
ORIGIN = PUBLIC_URL
RSS_ETAG = '"rss-v1"'
ATOM_LAST_MODIFIED = "Wed, 11 Nov 2026 10:00:00 GMT"

RSS = b"""<?xml version="1.0" encoding="UTF-8"?>
<rss version="2.0" xmlns:atom="http://www.w3.org/2005/Atom" xmlns:content="http://purl.org/rss/1.0/modules/content/">
  <channel>
    <title>Garden News</title>
    <link>https://garden.example/</link>
    <atom:link rel="hub" href="https://hub.example/"/>
    <atom:link rel="self" href="https://garden.example/rss.xml"/>
    <item>
      <title>Tomatoes</title>
      <link>https://garden.example/tomatoes</link>
      <guid isPermaLink="false">garden-1</guid>
      <pubDate>Tue, 10 Nov 2026 09:00:00 +0000</pubDate>
      <category>vegetables</category>
      <category>Summer &amp; Sun</category>
      <description>Short text</description>
      <content:encoded><![CDATA[<p>Full <b>text</b></p>]]></content:encoded>
    </item>
    <item>
      <title>Roses</title>
      <link>https://garden.example/roses</link>
      <guid>https://garden.example/roses</guid>
      <pubDate>Mon, 09 Nov 2026 09:00:00 +0000</pubDate>
    </item>
  </channel>
</rss>
"""

ATOM = b"""<?xml version="1.0" encoding="utf-8"?>
<feed xmlns="http://www.w3.org/2005/Atom">
  <title>Tool Shed</title>
  <link rel="alternate" href="https://shed.example/"/>
  <id>urn:uuid:shed</id>
  <updated>2026-11-11T10:00:00Z</updated>
  <entry>
    <title>Spades</title>
    <id>urn:uuid:spades</id>
    <link href="/spades"/>
    <updated>2026-11-11T10:00:00Z</updated>
    <category term="tools"/>
    <content type="xhtml"><div xmlns="http://www.w3.org/1999/xhtml"><p>Dig</p></div></content>
  </entry>
</feed>
"""


class _FeedServer:
    """A local feed server. It records the conditional headers of each request."""

    def __init__(self) -> None:
        self.requests: list[tuple[str, str | None, str | None]] = []
        owner = self

        class Handler(BaseHTTPRequestHandler):
            def do_GET(self) -> None:  # noqa: N802
                owner.requests.append((self.path, self.headers.get("If-None-Match"), self.headers.get("If-Modified-Since")))
                if self.path == "/rss.xml":
                    if self.headers.get("If-None-Match") == RSS_ETAG:
                        self._send(304, b"", {"ETag": RSS_ETAG})
                    else:
                        self._send(200, RSS, {"ETag": RSS_ETAG, "Content-Type": "application/rss+xml"})
                elif self.path == "/atom.xml":
                    if self.headers.get("If-Modified-Since") == ATOM_LAST_MODIFIED:
                        self._send(304, b"", {})
                    else:
                        self._send(200, ATOM, {"Last-Modified": ATOM_LAST_MODIFIED, "Content-Type": "application/atom+xml"})
                else:
                    self._send(404, b"not found", {})

            def _send(self, status: int, body: bytes, headers: dict[str, str]) -> None:
                self.send_response(status)
                for key, value in headers.items():
                    self.send_header(key, value)
                if status != 304:
                    self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                if status != 304:
                    self.wfile.write(body)

            def log_message(self, *_args) -> None:
                return

        self.httpd = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.base = f"http://127.0.0.1:{self.httpd.server_address[1]}"
        self.thread = threading.Thread(target=self.httpd.serve_forever, daemon=True)
        self.thread.start()

    def close(self) -> None:
        self.httpd.shutdown()
        self.httpd.server_close()


@pytest.fixture(autouse=True)
def _isolate(monkeypatch: pytest.MonkeyPatch):
    for key in list(os.environ):
        if key.startswith("CLAIRE_"):
            monkeypatch.delenv(key, raising=False)
    monkeypatch.setenv("CLAIRE_SECRET_KEY", "45" * 32)
    reset_secret_service()
    yield
    reset_secret_service()


@pytest.fixture
def feed_server():
    srv = _FeedServer()
    yield srv
    srv.close()


def _make_env(tmp_path: Path, *, network: bool):
    data_dir = tmp_path / "data"
    data_dir.mkdir()
    settings = Settings(
        _env_file=None,
        CLAIRE_DB_PATH=str(data_dir / "claire.db"),
        CLAIRE_DATA_DIR=str(data_dir),
        CLAIRE_PROVIDER="mock",
        CLAIRE_ENVIRONMENT="development",
        CLAIRE_PUBLIC_URL=PUBLIC_URL,
        CLAIRE_MULTI_THEME=True,
        CLAIRE_ALLOW_PRIVATE_NETWORKS=True,
    )
    conn = dbm.connect(settings.db_file)
    try:
        dbm.init_db(conn)
        store = accounts_store.AccountStore(conn)
        store.complete_setup(1, username="operator_feeds", display_name="Operator")
        members: dict[str, int] = {}
        if network:
            store.set_setting("mode", "network", updated_by=1)
            for index, name in enumerate(("alice_feeds", "bob_feeds")):
                members[name] = int(conn.execute("INSERT INTO accounts (public_id, username, role, status, display_name, created_at, updated_at) VALUES (?, ?, 'member', 'active', ?, 0, 0)", (323456789000 + index, name, name)).lastrowid)
        conn.commit()
    finally:
        conn.close()

    def session(account_id: int) -> dict[str, str]:
        c = dbm.connect_existing(settings.db_file)
        try:
            token = accounts_store.create_account_session(c, account_id=account_id, scope="owner", now=time.time())
        finally:
            c.close()
        return {"Cookie": f"claire_session={token}", "Origin": ORIGIN}

    return settings, members, session


def _call(client: TestClient, method: str, path: str, headers: dict[str, str], **kwargs):
    client.cookies.clear()
    response = client.request(method, path, headers=headers, follow_redirects=False, **kwargs)
    client.cookies.clear()
    return response


TTRSS_OPML = """<?xml version="1.0" encoding="utf-8"?>
<opml version="1.0">
  <head>
    <dateCreated>Wed, 11 Nov 2026 10:00:00 +0000</dateCreated>
    <title>Tiny Tiny RSS Feed Export</title>
  </head>
  <body>
    <outline text="Garden">
      <outline type="rss" text="Garden News" xmlUrl="{rss}" htmlUrl="https://garden.example/"/>
      <outline text="Tools">
        <outline type="rss" text="Tool Shed" title="Tool Shed (Atom)" xmlUrl="{atom}"/>
        <outline type="rss" text="Seeds" xmlUrl="https://seeds.example/feed"/>
      </outline>
    </outline>
    <outline type="rss" text="Top News" xmlUrl="https://news.example/rss"/>
    <outline type="rss" text="Broken" xmlUrl="ftp://broken.example/feed"/>
    <outline text="tt-rss-prefs" schema-version="141">
      <outline pref-name="DEFAULT_UPDATE_INTERVAL" value="30"/>
      <outline pref-name="PURGE_OLD_DAYS" value="60"/>
    </outline>
    <outline text="tt-rss-labels" schema-version="141">
      <outline label-name="Later" label-fg-color="" label-bg-color=""/>
    </outline>
    <outline text="tt-rss-filters" schema-version="141">
      <outline filter-type="2"><![CDATA[{{"enabled":true}}]]></outline>
    </outline>
  </body>
</opml>
"""


def test_single_mode_subscribe_poll_read_and_opml_import(tmp_path: Path, feed_server: _FeedServer):
    settings, _members, session = _make_env(tmp_path, network=False)
    rss_url, atom_url = f"{feed_server.base}/rss.xml", f"{feed_server.base}/atom.xml"
    app = server.create_app(settings, SimpleNamespace(provider=SimpleNamespace(name="mock")))
    with TestClient(app, base_url=PUBLIC_URL) as client:
        owner = session(1)
        assert client.get("/whoami", headers=owner).json()["instance"]["mode"] == "single"

        # 1. Subscribe through the route. The same URL again is not added.
        r = _call(client, "POST", "/api/feeds", owner, json={"url": rss_url, "folder": "Garden"})
        assert r.status_code == 201, r.text
        rss_id = r.json()["feed"]["id"]
        r = _call(client, "POST", "/api/feeds", owner, json={"url": atom_url})
        assert r.status_code == 201, r.text
        atom_id = r.json()["feed"]["id"]
        again = _call(client, "POST", "/api/feeds", owner, json={"url": rss_url})
        assert again.status_code == 200 and again.json() == {"feed": again.json()["feed"], "created": False}
        assert _call(client, "POST", "/api/feeds", owner, json={"url": "file:///etc/passwd"}).json()["error"] == "invalid_url"

        # 2. Poll now: both feeds give items, and the poll stores the cache fields and the WebSub hub.
        r = _call(client, "POST", "/api/feeds/poll", owner, json={})
        assert r.status_code == 200, r.text
        outcomes = {o["feed_id"]: o for o in r.json()["outcomes"]}
        assert outcomes[rss_id]["result"] == "updated" and outcomes[rss_id]["added"] == 2
        assert outcomes[atom_id]["result"] == "updated" and outcomes[atom_id]["added"] == 1
        feeds = {f["id"]: f for f in _call(client, "GET", "/api/feeds", owner).json()["feeds"]}
        assert feeds[rss_id]["title"] == "Garden News" and feeds[rss_id]["format"] == "rss" and feeds[rss_id]["websub_hub"] == "https://hub.example/"
        assert feeds[atom_id]["title"] == "Tool Shed" and feeds[atom_id]["format"] == "atom" and feeds[atom_id]["unread"] == 1

        # 3. Read the items: newest row first; categories stay raw tags; the content comes only with content=1.
        items = _call(client, "GET", "/api/feeds/items", owner).json()["items"]
        assert len(items) == 3
        tomatoes = next(i for i in items if i["title"] == "Tomatoes")
        assert tomatoes["categories"] == ["vegetables", "Summer & Sun"] and tomatoes["unread"] is True and "content" not in tomatoes
        spades = _call(client, "GET", f"/api/feeds/items?feed_id={atom_id}&content=1", owner).json()["items"]
        assert [(i["url"], i["content"]) for i in spades] == [(f"{feed_server.base}/spades", "<p>Dig</p>")]

        # 4. Mark read and starred.
        r = _call(client, "PATCH", "/api/feeds/items", owner, json={"item_ids": [tomatoes["id"]], "unread": False, "starred": True})
        assert r.json() == {"changed": 1}
        starred = _call(client, "GET", "/api/feeds/items?starred=1", owner).json()["items"]
        assert [(i["id"], i["unread"]) for i in starred] == [(tomatoes["id"], False)]
        assert len(_call(client, "GET", "/api/feeds/items?unread=1", owner).json()["items"]) == 2

        # 5. Poll again: the server gets the stored ETag and Last-Modified and answers 304. No new rows; the read state stays.
        before = len(feed_server.requests)
        r = _call(client, "POST", "/api/feeds/poll", owner, json={})
        assert {o["feed_id"]: (o["result"], o["added"]) for o in r.json()["outcomes"]} == {rss_id: ("not_modified", 0), atom_id: ("not_modified", 0)}
        conditional = {path: (etag, since) for path, etag, since in feed_server.requests[before:]}
        assert conditional == {"/rss.xml": (RSS_ETAG, None), "/atom.xml": (None, ATOM_LAST_MODIFIED)}
        items_after = _call(client, "GET", "/api/feeds/items", owner).json()["items"]
        assert [i["id"] for i in items_after] == [i["id"] for i in items]
        assert next(i for i in items_after if i["id"] == tomatoes["id"])["starred"] is True

        # 6. OPML import in the Tiny Tiny RSS style: categories become folders, deeper names are joined, settings outlines are skipped and listed.
        opml = TTRSS_OPML.format(rss=rss_url, atom=atom_url)
        r = _call(client, "POST", "/api/feeds/opml", owner, json={"opml": opml})
        assert r.status_code == 200, r.text
        report = r.json()
        assert report["counts"] == {"added": 2, "skipped": 3, "duplicate": 2, "failed": 1}
        assert {(a["url"], a["folder"], a["title"]) for a in report["added"]} == {("https://seeds.example/feed", "Garden › Tools", "Seeds"), ("https://news.example/rss", "", "Top News")}
        assert sorted(s["title"] for s in report["skipped"]) == ["tt-rss-filters", "tt-rss-labels", "tt-rss-prefs"]
        assert {d["url"] for d in report["duplicate"]} == {rss_url, atom_url}
        assert report["failed"] == [{"url": "ftp://broken.example/feed", "title": "Broken", "reason": "invalid_url"}]
        # A second import adds nothing.
        second = _call(client, "POST", "/api/feeds/opml", owner, json={"opml": opml}).json()
        assert second["counts"] == {"added": 0, "skipped": 3, "duplicate": 4, "failed": 1}
        listing = _call(client, "GET", "/api/feeds", owner).json()
        assert listing["folders"] == ["Garden", "Garden › Tools"] and len(listing["feeds"]) == 4

        # 7. OPML export gives the feeds with their folders; an import of the export adds nothing.
        exported = _call(client, "GET", "/api/feeds/opml", owner)
        assert exported.status_code == 200 and exported.headers["content-type"].startswith("text/x-opml")
        root = etree.fromstring(exported.content)
        folders = {o.get("text"): sorted(c.get("xmlUrl") for c in o) for o in root.find("body") if o.get("xmlUrl") is None}
        assert folders == {"Garden": [rss_url], "Garden › Tools": ["https://seeds.example/feed"]}
        assert _call(client, "POST", "/api/feeds/opml", owner, json={"opml": exported.text}).json()["counts"]["added"] == 0

        # 8. Unsubscribe deletes the feed and its items.
        r = _call(client, "DELETE", "/api/feeds", owner, json={"feed_id": atom_id})
        assert r.json() == {"removed": True, "feed_id": atom_id, "items": 1}
        assert len(_call(client, "GET", "/api/feeds/items", owner).json()["items"]) == 2


def test_network_mode_feeds_are_per_account_and_purge_keeps_documents(tmp_path: Path, feed_server: _FeedServer):
    settings, members, session = _make_env(tmp_path, network=True)
    alice_id, bob_id = members["alice_feeds"], members["bob_feeds"]
    # The ingest target of the item: a theme that the purge of Alice does not delete.
    theme = ThemeManager(settings).define_theme("Shared Garden", account_id=1, is_default=False)
    app = server.create_app(settings, SimpleNamespace(provider=SimpleNamespace(name="mock")))
    with TestClient(app, base_url=PUBLIC_URL) as client:
        alice, bob = session(alice_id), session(bob_id)
        rss_id = _call(client, "POST", "/api/feeds", alice, json={"url": f"{feed_server.base}/rss.xml"}).json()["feed"]["id"]
        assert _call(client, "POST", "/api/feeds/poll", alice, json={"feed_id": rss_id}).json()["outcomes"][0]["added"] == 2
        alice_items = _call(client, "GET", "/api/feeds/items", alice).json()["items"]

        # Bob sees no feed of Alice and cannot change one.
        assert _call(client, "GET", "/api/feeds", bob).json()["feeds"] == []
        assert _call(client, "GET", "/api/feeds/items", bob).json()["items"] == []
        assert _call(client, "PATCH", "/api/feeds/items", bob, json={"item_ids": [alice_items[0]["id"]], "starred": True}).json() == {"changed": 0}
        assert _call(client, "DELETE", "/api/feeds", bob, json={"feed_id": rss_id}).status_code == 404
        assert _call(client, "POST", "/api/feeds/poll", bob, json={"feed_id": rss_id}).status_code == 404
        # A cookie request from a different origin is refused; a request without a credential gets 404.
        assert _call(client, "POST", "/api/feeds", {**alice, "Origin": "https://evil.example"}, json={"url": "https://x.example/feed"}).status_code == 403
        assert _call(client, "GET", "/api/feeds", {}).status_code == 404

    # An ingested document is linked to one item. The purge of the account deletes the feed data and keeps the document.
    conn = dbm.connect_existing(settings.db_file)
    try:
        dbm.insert_document(conn, Document(id="feed-doc", url="https://garden.example/tomatoes", raw_text="tomatoes", source_type="web", content_hash="hf"), theme_id=theme.id)
        conn.execute("UPDATE feed_items SET linked_theme_id=?, linked_doc_id='feed-doc' WHERE id=?", (theme.id, alice_items[0]["id"]))
        conn.commit()
        assert dbm.get_document_row(conn, "feed-doc", theme_id=theme.id) is not None
        from claire.store import feeds as feeds_store

        # The purge touches only the feed data of Alice: the feed of Bob stays.
        conn.execute("INSERT INTO feed_subscriptions (account_id, url, created_at, updated_at) VALUES (?, 'https://bob.example/feed', 0, 0)", (bob_id,))
        conn.commit()
        accounts_store.AccountStore(conn).purge(alice_id, data_dir=settings.data_dir)
        assert conn.execute("SELECT COUNT(*) FROM feed_subscriptions WHERE account_id=?", (alice_id,)).fetchone()[0] == 0
        assert conn.execute("SELECT COUNT(*) FROM feed_items WHERE account_id=?", (alice_id,)).fetchone()[0] == 0
        assert feeds_store.count_feeds(conn, bob_id) == 1
        assert dbm.get_document_row(conn, "feed-doc", theme_id=theme.id) is not None
    finally:
        conn.close()
