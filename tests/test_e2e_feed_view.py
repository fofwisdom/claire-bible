"""End-to-end test of the feed view routes (FEDERATION_REMOTE_READING_DESIGN.md Sections 1.3 rule 7, 4.4, and 8, unit FP4-F3).

The real ``server.create_app`` and real ``Settings``. A local HTTP server serves a site page with a ``<link rel="alternate">`` and an RSS feed with hostile HTML. The test subscribes to the feed, reads an item (the body renders in the sandbox page), and ingests the item into a theme. All servers are local.
"""

from __future__ import annotations

import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from types import SimpleNamespace
from urllib.parse import urlsplit

import pytest
from starlette.testclient import TestClient

from claire.api import server, usercontent
from claire.ingest.pipeline import IngestReport
from claire.ingest.service import IngestServicePool
from claire.store import client_db
from claire.store import db as dbm
from tests.test_e2e_account_feeds import PUBLIC_URL, _call, _isolate, _make_env  # noqa: F401  (_isolate is an autouse fixture)

USERCONTENT = "127.0.0.1:8767"
HOSTILE = '<p>Hello <b>garden</b><script>alert(1)</script><img src="/pic.png" onerror="alert(2)"><a href="/more" onclick="alert(3)">more</a><a href="javascript:alert(4)">bad</a><iframe src="https://evil.example/"></iframe></p>'
SITE = b'<!doctype html><html><head><title>Garden</title><link rel="alternate" type="application/rss+xml" title="Garden feed" href="/feed.xml"><link rel="stylesheet" href="/x.css"></head><body>Garden</body></html>'


def _rss(base: str) -> bytes:
    return f"""<?xml version="1.0" encoding="UTF-8"?>
<rss version="2.0" xmlns:content="http://purl.org/rss/1.0/modules/content/">
  <channel>
    <title>Garden News</title>
    <link>{base}/</link>
    <item>
      <title>Tomatoes &lt;script&gt;</title>
      <link>{base}/posts/tomatoes</link>
      <guid isPermaLink="false">garden-1</guid>
      <pubDate>Tue, 10 Nov 2026 09:00:00 +0000</pubDate>
      <category>vegetables</category>
      <description>Short &lt;b&gt;text&lt;/b&gt;</description>
      <content:encoded><![CDATA[{HOSTILE}]]></content:encoded>
    </item>
    <item>
      <title>No link</title>
      <guid isPermaLink="false">garden-2</guid>
      <pubDate>Mon, 09 Nov 2026 09:00:00 +0000</pubDate>
    </item>
  </channel>
</rss>
""".encode()


class _Site:
    def __init__(self) -> None:
        owner = self

        class Handler(BaseHTTPRequestHandler):
            def do_GET(self) -> None:  # noqa: N802
                if self.path == "/":
                    self._send(200, SITE, "text/html; charset=utf-8")
                elif self.path == "/feed.xml":
                    self._send(200, _rss(owner.base), "application/rss+xml")
                else:
                    self._send(404, b"not found", "text/plain")

            def _send(self, status: int, body: bytes, content_type: str) -> None:
                self.send_response(status)
                self.send_header("Content-Type", content_type)
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def log_message(self, *_args) -> None:
                return

        self.httpd = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.base = f"http://127.0.0.1:{self.httpd.server_address[1]}"
        threading.Thread(target=self.httpd.serve_forever, daemon=True).start()

    def close(self) -> None:
        self.httpd.shutdown()
        self.httpd.server_close()


@pytest.fixture
def site():
    srv = _Site()
    yield srv
    srv.close()


def _ingest_stub(monkeypatch) -> list[tuple[str, dict]]:
    calls: list[tuple[str, dict]] = []

    class _Service:
        def ingest(self, payload, **kwargs):
            calls.append((payload, kwargs))
            return IngestReport(document_id="doc_from_feed", title="From the feed")

    monkeypatch.setattr(IngestServicePool, "get_service", lambda self, *args, **kwargs: _Service())
    return calls


def _subscribe(tc: TestClient, headers: dict[str, str], site: _Site) -> int:
    """Find the feed of the site, subscribe to it, and poll it. Return the feed ID."""
    found = _call(tc, "POST", "/api/feeds/discover", headers, json={"url": site.base + "/"})
    assert found.status_code == 200, found.text
    assert found.json()["feeds"] == [{"url": f"{site.base}/feed.xml", "title": "Garden feed", "format": "rss", "subscribed": False}]
    created = _call(tc, "POST", "/api/feeds", headers, json={"url": found.json()["feeds"][0]["url"], "folder": "Garden"})
    assert created.status_code == 201, created.text
    feed_id = created.json()["feed"]["id"]
    polled = _call(tc, "POST", "/api/feeds/poll", headers, json={"feed_id": feed_id})
    assert polled.status_code == 200 and polled.json()["outcomes"][0]["added"] == 2, polled.text
    return feed_id


@pytest.mark.parametrize("usercontent_host", [False, True], ids=["app-origin fallback", "usercontent host"])
def test_e2e_single_mode_subscribe_read_and_ingest(tmp_path, site, monkeypatch, usercontent_host):
    if usercontent_host:
        monkeypatch.setenv("CLAIRE_USERCONTENT_FQDN", USERCONTENT)
    settings, _members, session = _make_env(tmp_path, network=False)
    calls = _ingest_stub(monkeypatch)
    owner = session(1)
    app = server.create_app(settings, SimpleNamespace(provider=SimpleNamespace(name="stub")))
    with TestClient(app, base_url=PUBLIC_URL) as tc:
        # The page has the feed view in single mode.
        page = _call(tc, "GET", "/syndication", owner)
        assert page.status_code == 200 and 'id="client-feeds"' in page.text and "ClaireSyndicationFeeds" in page.text

        feed_id = _subscribe(tc, owner, site)
        # A second discovery says that the account has the feed. The feed address itself is a feed too.
        again = _call(tc, "POST", "/api/feeds/discover", owner, json={"url": f"{site.base}/feed.xml"}).json()
        assert again["feeds"] == [{"url": f"{site.base}/feed.xml", "title": "Garden News", "format": "rss", "subscribed": True}]
        assert _call(tc, "POST", "/api/feeds/discover", owner, json={"url": "ftp://x.example/"}).status_code == 400
        assert _call(tc, "POST", "/api/feeds/discover", owner, json={"url": f"{site.base}/missing"}).json() == {"url": f"{site.base}/missing", "feeds": [], "error": "http_404"}

        # The list has plain text only: no element of the feed is in it.
        listed = _call(tc, "GET", f"/api/feeds/items?feed_id={feed_id}", owner).json()["items"]
        assert [i["title"] for i in listed] == ["No link", "Tomatoes <script>"]
        item = listed[1]
        assert item["text"] == "Short text" and item["unread"] is True and item["linked"] is None and item["timeline_item_id"] is None
        assert "content" not in item

        # Read the item: the detail has the render URL, and the body is the sanitized page for the sandbox frame.
        detail = _call(tc, "GET", f"/api/feeds/items/{item['id']}", owner)
        assert detail.status_code == 200, detail.text
        view = detail.json()["item"]
        assert view["has_body"] is True and view["categories"] == ["vegetables"]
        parts = urlsplit(view["render_url"])
        target = parts.path + "?" + parts.query
        assert parts.path == f"/render/feed/{item['id']}"
        if usercontent_host:
            assert f"{parts.scheme}://{parts.netloc}" == f"http://{USERCONTENT}"
            body = tc.get(target, headers={"host": USERCONTENT}, follow_redirects=False)
            # With a usercontent host the path does not exist on the app origin.
            assert _call(tc, "GET", target, owner).status_code == 404
            other_kind = tc.get(f"/render/feed/{item['id']}?t={usercontent.create_remote_token(1, item['id'])}", headers={"host": USERCONTENT}, follow_redirects=False)
            no_token = tc.get(f"/render/feed/{item['id']}", headers={"host": USERCONTENT}, follow_redirects=False)
        else:
            assert parts.netloc == ""
            body = _call(tc, "GET", target, owner)
            # The fallback on the app origin needs the session of the owner.
            assert _call(tc, "GET", target, {}).status_code in (401, 404)
            other_kind = _call(tc, "GET", f"/render/feed/{item['id']}?t={usercontent.create_remote_token(1, item['id'])}", owner)
            no_token = _call(tc, "GET", f"/render/feed/{item['id']}", owner)
        assert body.status_code == 200, body.text
        # A token of a timeline item (kind "remote") is not valid for a feed item, and a request without a token gets 404.
        assert other_kind.status_code == 404 and no_token.status_code == 404
        csp = body.headers["content-security-policy"]
        assert "default-src 'none'" in csp and "sandbox allow-popups allow-popups-to-escape-sandbox" in csp and "script-src" not in csp
        html = body.text
        assert "Hello <b>garden</b>" in html
        for bad in ("<script", "onerror", "onclick", "javascript:", "<iframe", "alert("):
            assert bad not in html, bad
        # A relative link of the feed is absolute in the frame, and it gives the site no referrer.
        assert f'href="{site.base}/more"' in html and 'rel="nofollow noopener noreferrer"' in html and 'target="_blank"' in html
        # The image address is the media proxy on the app origin, or (usercontent host without a media host) no image.
        assert f"{site.base}/pic.png" not in html
        assert ("/media/remote/" in html) is (not usercontent_host)

        # The item without content has no body. The item without a link cannot go into a theme.
        bare = _call(tc, "GET", f"/api/feeds/items/{listed[0]['id']}", owner).json()["item"]
        assert bare["has_body"] is False and bare["url"] is None
        assert _call(tc, "GET", "/api/feeds/items/999999", owner).status_code == 404

        # Read state and star.
        assert _call(tc, "PATCH", "/api/feeds/items", owner, json={"item_ids": [item["id"]], "unread": False, "starred": True}).json() == {"changed": 1}
        starred = _call(tc, "GET", "/api/feeds/items?starred=1", owner).json()["items"]
        assert [(i["id"], i["unread"]) for i in starred] == [(item["id"], False)]

        # Ingest of one item into the personal knowledge base of the owner (CT23); a ``theme`` field is ignored.
        path = f"/api/feeds/items/{item['id']}/ingest"
        assert _call(tc, "POST", "/api/feeds/items/999999/ingest", owner, json={"theme": "0"}).status_code == 404
        assert _call(tc, "POST", f"/api/feeds/items/{listed[0]['id']}/ingest", owner, json={"theme": "0"}).status_code == 409
        assert calls == []
        res = _call(tc, "POST", path, owner, json={"theme": "0"})
        assert res.status_code == 200, res.text
        linked = res.json()["linked"]
        assert res.json()["document_id"] == "doc_from_feed" and linked["document_id"] == "doc_from_feed"
        # The existing URL ingest got the address of the item.
        assert calls == [(f"{site.base}/posts/tomatoes", {"source": "feed"})]
        # The item stays in the feed and has the link to the document.
        assert _call(tc, "GET", f"/api/feeds/items/{item['id']}", owner).json()["item"]["linked"] == linked
        assert len(_call(tc, "GET", f"/api/feeds/items?feed_id={feed_id}", owner).json()["items"]) == 2


def test_e2e_network_mode_merge_by_url_and_account_isolation(tmp_path, site, monkeypatch):
    settings, members, session = _make_env(tmp_path, network=True)
    calls = _ingest_stub(monkeypatch)
    alice, bob = session(members["alice_feeds"]), session(members["bob_feeds"])
    alice_id = members["alice_feeds"]
    conn = dbm.connect_existing(settings.db_file)
    try:
        conn.execute("INSERT INTO knowledge_bases (id, seq, label, is_personal, is_public, created_at, updated_at, account_id, number) VALUES (11, 11, 'alice theme', 1, 1, 1, 1, ?, 523456789012345)", (alice_id,))
        conn.commit()
    finally:
        conn.close()
    app = server.create_app(settings, SimpleNamespace(provider=SimpleNamespace(name="stub")))
    with TestClient(app, base_url=PUBLIC_URL) as tc:
        feed_id = _subscribe(tc, alice, site)
        item_url = f"{site.base}/posts/tomatoes"
        # The account follows the author of the feed: the timeline has the same post (the same URL).
        client = client_db.connect_client_existing(client_db.client_db_path(settings.db_file))
        try:
            timeline_id = client.execute(
                "INSERT INTO federation_timeline_items (account_id, object_iri, url, object_type, author_iri, author_json, content_html, received_at) VALUES (?, ?, ?, 'Article', ?, '{}', '<p>x</p>', 1)",
                (alice_id, f"{site.base}/ap/objects/1", item_url, f"{site.base}/ap/actor"),
            ).lastrowid
            client.commit()
        finally:
            client.close()

        items = _call(tc, "GET", f"/api/feeds/items?feed_id={feed_id}", alice).json()["items"]
        item = next(i for i in items if i["url"] == item_url)
        assert item["timeline_item_id"] == str(timeline_id)
        assert next(i for i in items if i["url"] is None)["timeline_item_id"] is None

        # One state in the two lists: feed item to timeline item, and the reverse.
        assert _call(tc, "PATCH", "/api/feeds/items", alice, json={"item_ids": [item["id"]], "unread": False}).status_code == 200
        timeline_item = _call(tc, "GET", f"/api/federation/timeline/{timeline_id}", alice).json()["item"]
        assert timeline_item["unread"] is False and timeline_item["starred"] is False
        assert _call(tc, "PATCH", f"/api/federation/timeline/{timeline_id}", alice, json={"starred": True, "unread": True}).status_code == 200
        item = _call(tc, "GET", f"/api/feeds/items/{item['id']}", alice).json()["item"]
        assert item["starred"] is True and item["unread"] is True
        # "Mark all as read" of the feed marks the timeline item too.
        assert _call(tc, "PATCH", "/api/feeds/items", alice, json={"feed_id": feed_id, "unread": False}).json() == {"changed": 2}
        assert _call(tc, "GET", f"/api/federation/timeline/{timeline_id}", alice).json()["item"]["unread"] is False

        # An item of a different account gives the same answer as an unknown item.
        assert _call(tc, "GET", f"/api/feeds/items/{item['id']}", bob).status_code == 404
        assert _call(tc, "POST", f"/api/feeds/items/{item['id']}/ingest", bob, json={"theme": "523456789012345"}).status_code == 404
        render = urlsplit(item["render_url"])
        assert _call(tc, "GET", render.path + "?" + render.query, bob).status_code == 404
        assert _call(tc, "GET", render.path + "?" + render.query, alice).status_code == 200
        assert calls == []

        # The ingest of the feed item gives the document link to the timeline item too.
        res = _call(tc, "POST", f"/api/feeds/items/{item['id']}/ingest", alice, json={"theme": "523456789012345"})
        assert res.status_code == 200, res.text
        assert res.json()["linked"] == {"kb_id": 11, "document_id": "doc_from_feed"}
        assert calls == [(item_url, {"source": "feed"})]
        assert _call(tc, "GET", f"/api/federation/timeline/{timeline_id}", alice).json()["item"]["linked"] == {"kb_id": 11, "document_id": "doc_from_feed"}
