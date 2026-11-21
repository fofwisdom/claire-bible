"""Media proxy of the federation client (FEDERATION_REMOTE_READING_DESIGN.md Sections 7.2 and 9.1, unit FP4-7): the fetch of remote media against a local HTTP server (external interface), the media token, and the cache records in ``client.db`` (I/O)."""

from __future__ import annotations

import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import httpx
import pytest

from claire.federation import media_proxy
from claire.federation.media_proxy import MediaFetcher, create_media_token, verify_media_token
from claire.store import client_db
from claire.store import federation_media
from claire.store.secrets import SecretService

PNG = b"\x89PNG\r\n\x1a\n" + b"p" * 200


class MediaServer:
    """A local HTTP server with media files. ``hits`` has the paths of the requests, and ``headers`` the headers of the last request."""

    def __init__(self, files: dict[str, tuple[str, bytes]], redirects: dict[str, str] | None = None) -> None:
        self.files, self.redirects, self.hits, self.headers = files, redirects or {}, [], {}
        owner = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *args) -> None:
                pass

            def do_GET(self) -> None:
                owner.hits.append(self.path)
                owner.headers = {k.lower(): v for k, v in self.headers.items()}
                if self.path in owner.redirects:
                    self.send_response(302)
                    self.send_header("Location", owner.redirects[self.path])
                    self.send_header("Set-Cookie", "track=1; Path=/")
                    self.send_header("Content-Length", "0")
                    self.end_headers()
                    return
                if self.path not in owner.files:
                    self.send_response(404)
                    self.send_header("Content-Length", "0")
                    self.end_headers()
                    return
                media_type, data = owner.files[self.path]
                self.send_response(200)
                self.send_header("Content-Type", media_type)
                if not self.path.startswith("/stream"):
                    self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)

        self._server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self._thread = threading.Thread(target=self._server.serve_forever, daemon=True)

    @property
    def base(self) -> str:
        return f"http://127.0.0.1:{self._server.server_address[1]}"

    def start(self) -> "MediaServer":
        self._thread.start()
        return self

    def stop(self) -> None:
        self._server.shutdown()
        self._server.server_close()
        self._thread.join(timeout=5)


@pytest.fixture
def media_server():
    server = MediaServer(
        {
            "/a.png": ("image/png; charset=binary", PNG),
            "/b.svg": ("image/svg+xml", b"<svg xmlns='http://www.w3.org/2000/svg'/>"),
            "/page.html": ("text/html", b"<html></html>"),
            "/big.png": ("image/png", b"x" * 2000),
            "/stream-big.png": ("image/png", b"x" * 2000),
            "/v.mp4": ("video/mp4", b"v" * 1500),
            "/v.mkv": ("video/x-matroska", b"v" * 10),
            "/s.ogg": ("audio/ogg", b"s" * 10),
        },
        {"/r1": "/r2", "/r2": "/r3", "/r3": "/a.png", "/r0": "/r1"},
    ).start()
    yield server
    server.stop()


def _fetcher(**changes) -> MediaFetcher:
    values = {"image_max_bytes": 1000, "av_max_bytes": 1800, "user_agent": "ClaireBible-MediaProxy/1.0 (+https://claire.example)", "allow_private": True, **changes}
    return MediaFetcher(**values)


def test_fetch_rules_types_and_sizes(media_server):
    base = media_server.base
    ok = _fetcher().fetch(f"{base}/a.png")
    assert (ok.state, ok.media_type, ok.body) == ("stored", "image/png", PNG)
    # The request has the user agent of the server, and no cookie and no credential.
    assert media_server.headers["user-agent"].startswith("ClaireBible-MediaProxy/") and "cookie" not in media_server.headers and "authorization" not in media_server.headers
    # Allowed types: image except SVG, video/mp4, video/webm, audio.
    assert _fetcher().fetch(f"{base}/b.svg").state == "failed" and _fetcher().fetch(f"{base}/b.svg").error == "type_not_allowed"
    assert _fetcher().fetch(f"{base}/page.html").error == "type_not_allowed"
    assert _fetcher().fetch(f"{base}/v.mkv").error == "type_not_allowed"
    assert _fetcher().fetch(f"{base}/v.mp4").state == "stored" and _fetcher().fetch(f"{base}/s.ogg").state == "stored"
    # The size limit of the kind: by the declared length, and by the bytes that arrive when no length is declared.
    assert _fetcher().fetch(f"{base}/big.png").state == "too_large"
    assert _fetcher().fetch(f"{base}/stream-big.png").state == "too_large"
    assert _fetcher(av_max_bytes=1000).fetch(f"{base}/v.mp4").state == "too_large"
    assert _fetcher().fetch(f"{base}/none.png").error == "http_404"
    # Three redirects are followed, and a cookie of a redirect answer does not go to the next request. Four redirects fail.
    media_server.hits.clear()
    assert _fetcher().fetch(f"{base}/r1").state == "stored" and media_server.hits == ["/r1", "/r2", "/r3", "/a.png"] and "cookie" not in media_server.headers
    assert _fetcher().fetch(f"{base}/r0").error == "too_many_redirects"
    # A private address is refused before a request goes out.
    media_server.hits.clear()
    assert _fetcher(allow_private=False).fetch(f"{base}/a.png").error == "blocked_address" and media_server.hits == []


def test_fetch_checks_the_address_of_each_redirect_hop():
    requests = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(str(request.url))
        return httpx.Response(302, headers={"location": "http://internal.test/secret.png"})

    addresses = {"public.test": {"93.184.216.34"}, "internal.test": {"10.0.0.7"}}
    fetcher = _fetcher(allow_private=False, resolver=lambda host: addresses[host], transport=httpx.MockTransport(handler))
    assert fetcher.fetch("https://public.test/a.png").error == "blocked_address" and requests == ["https://public.test/a.png"]


def test_media_token_binds_account_url_and_time():
    service = SecretService(master_key=b"m" * 32)
    token = create_media_token(7, "a" * 64, secret_service=service, now=1000.0)
    assert verify_media_token(token, "a" * 64, secret_service=service, now=1001.0) == 7
    assert verify_media_token(token, "b" * 64, secret_service=service, now=1001.0) is None
    assert verify_media_token(token, "a" * 64, secret_service=service, now=1000.0 + media_proxy.MEDIA_TOKEN_TTL + 1) is None
    assert verify_media_token(token, "a" * 64, secret_service=SecretService(master_key=b"n" * 32), now=1001.0) is None
    # Another account in the token changes the signature.
    assert verify_media_token("8." + token.split(".", 1)[1], "a" * 64, secret_service=service, now=1001.0) is None
    for bad in ("", "x", "1.2", "a.b.c", token + ".1"):
        assert verify_media_token(bad, "a" * 64, secret_service=service, now=1001.0) is None


def _item(conn, object_iri: str, content_html: str, attachments: str = "[]") -> None:
    conn.execute(
        "INSERT INTO federation_timeline_items (account_id, object_iri, object_type, author_iri, author_json, content_html, attachments_json, received_at) VALUES (1, ?, 'Note', 'https://r.example/u', '{}', ?, ?, 1)",
        (object_iri, content_html, attachments),
    )


def test_cache_records_orphans_and_eviction(tmp_path):
    conn = client_db.connect_client_db(tmp_path / "client.db")
    try:
        assert client_db.stored_client_version(conn) == client_db.CLIENT_SCHEMA_VERSION >= 3
        a, b, c = "https://r.example/a.png?x=1&y=2", "https://r.example/b.mp4", "https://r.example/c.png"
        _item(conn, "https://r.example/1", '<p><img alt="x" src="https://r.example/a.png?x=1&amp;y=2"><img src="data:image/png;base64,AA"><img src=\'javascript:x\'></p>', '[{"url": "https://r.example/b.mp4", "media_type": "video/mp4"}, {"url": "ftp://r.example/z"}]')
        _item(conn, "https://r.example/2", "<p>no media</p>", '[{"url": "https://r.example/b.mp4", "media_type": "video/mp4; codecs=avc1"}]')
        assert federation_media.item_media_urls('<img src="https://r.example/a.png?x=1&amp;y=2">', [{"url": b, "media_type": "video/mp4"}, {"url": b, "media_type": "video/mp4"}, {"url": c, "media_type": "application/pdf"}, {"url": c}]) == [b, a]
        entries = federation_media.register_urls(conn, [a, b, c, "ftp://r.example/z"], now=100.0)
        assert set(entries) == {a, b, c} and all(e.state == "new" and e.last_access_at == 100.0 for e in entries.values())
        # A second registration changes nothing.
        assert federation_media.register_urls(conn, [a], now=900.0)[a].last_access_at == 100.0
        federation_media.set_result(conn, federation_media.url_hash(a), state="stored", media_type="image/png", byte_size=10, now=200.0)
        stored = federation_media.get_entry(conn, federation_media.url_hash(a))
        assert (stored.state, stored.store_key, stored.byte_size, stored.fetched_at) == ("stored", f"remote/{federation_media.url_hash(a)}", 10, 200.0)
        federation_media.set_result(conn, federation_media.url_hash(b), state="too_large", media_type="video/mp4", byte_size=None, now=200.0)
        assert federation_media.get_entry(conn, federation_media.url_hash(b)).store_key is None
        # No item uses c. After the first item goes, a has no item; b stays with the second item.
        assert federation_media.orphan_hashes(conn) == [federation_media.url_hash(c)]
        conn.execute("DELETE FROM federation_timeline_items WHERE object_iri='https://r.example/1'")
        assert federation_media.orphan_hashes(conn) == sorted([federation_media.url_hash(a), federation_media.url_hash(c)])
        # Eviction by the time of the last access.
        assert federation_media.evictable_hashes(conn, before=150.0) == [federation_media.url_hash(c)]
        federation_media.touch(conn, federation_media.url_hash(c), now=300.0)
        assert federation_media.evictable_hashes(conn, before=150.0) == []
        assert federation_media.delete_entries(conn, [federation_media.url_hash(a), "0" * 64]) == [f"remote/{federation_media.url_hash(a)}"]
        assert federation_media.get_entry(conn, federation_media.url_hash(a)) is None
    finally:
        conn.close()


def test_client_db_version_2_gets_the_media_cache_table(tmp_path):
    """A ``client.db`` of version 2 gets the table at the next start, and its rows stay (the migration completes on its own)."""
    path = tmp_path / "client.db"
    conn = client_db.connect_client_db(path)
    _item(conn, "https://r.example/1", "<p>x</p>")
    conn.execute("DROP TABLE federation_media_cache")
    conn.execute("UPDATE client_meta SET value='2' WHERE key='schema_version'")
    conn.commit()
    conn.close()
    conn = client_db.connect_client_db(path)
    try:
        assert client_db.stored_client_version(conn) == client_db.CLIENT_SCHEMA_VERSION
        assert conn.execute("SELECT COUNT(*) FROM federation_media_cache").fetchone()[0] == 0
        assert conn.execute("SELECT COUNT(*) FROM federation_timeline_items").fetchone()[0] == 1
    finally:
        conn.close()
