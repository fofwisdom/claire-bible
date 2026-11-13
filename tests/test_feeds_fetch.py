"""Feed fetch and poll against a local HTTP server (I/O): private address check, redirects, size limit, and backoff on ``429`` and ``5xx``."""

from __future__ import annotations

import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest

from claire.feeds import poller
from claire.feeds.fetch import FeedFetcher, parse_retry_after
from claire.store import client_db
from claire.store import db as dbm
from claire.store import feeds as feeds_store

NOW = 1_800_000_000.0
RSS = b'<rss version="2.0"><channel><title>T</title><item><guid>a</guid><title>A</title></item></channel></rss>'


class _Server:
    def __init__(self, routes: dict[str, tuple[int, dict[str, str], bytes]]) -> None:
        self.routes = routes
        self.paths: list[str] = []
        owner = self

        class Handler(BaseHTTPRequestHandler):
            def do_GET(self) -> None:  # noqa: N802
                owner.paths.append(self.path)
                status, headers, body = owner.routes.get(self.path, (404, {}, b""))
                self.send_response(status)
                for key, value in headers.items():
                    self.send_header(key, value)
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
def srv():
    server = _Server({
        "/feed": (200, {"Content-Type": "application/rss+xml", "ETag": '"e1"'}, RSS),
        "/to-feed": (302, {"Location": "/feed"}, b""),
        "/to-metadata": (302, {"Location": "http://169.254.169.254/latest"}, b""),
        "/busy": (429, {"Retry-After": "90000"}, b""),
        "/down": (503, {}, b""),
        "/page": (200, {"Content-Type": "text/html"}, b"<html><body>no feed</body></html>"),
        "/big": (200, {}, b"x" * 4096),
    })
    yield server
    server.close()


@pytest.fixture
def db_file(tmp_path: Path) -> Path:
    """The ``client.db`` path (RD27). ``claire.db`` is in the same folder: its accounts decide which feeds are due."""
    path = tmp_path / "claire.db"
    conn = dbm.connect(path)
    try:
        dbm.init_db(conn)
    finally:
        conn.close()
    client = client_db.client_db_path(path)
    client_db.connect_client_db(client).close()
    return client


def _add(db_file: Path, url: str) -> dict:
    conn = client_db.connect_client_existing(db_file)
    try:
        feed, _ = feeds_store.add_feed(conn, 1, url, now=NOW)
        conn.commit()
        return feed
    finally:
        conn.close()


def _feed(db_file: Path, feed_id: int) -> dict:
    conn = client_db.connect_client_existing(db_file)
    try:
        return feeds_store.get_feed(conn, 1, feed_id)
    finally:
        conn.close()


def test_private_address_is_refused_by_default(srv: _Server) -> None:
    result = FeedFetcher().fetch(f"{srv.base}/feed")
    assert (result.status, result.error) == (0, "blocked_address")
    assert srv.paths == []


def test_each_redirect_hop_gets_the_address_check(srv: _Server) -> None:
    fetcher = FeedFetcher(allow_private=True)
    ok = fetcher.fetch(f"{srv.base}/to-feed")
    assert (ok.status, ok.final_url, ok.etag, ok.body) == (200, f"{srv.base}/feed", '"e1"', RSS)
    # The metadata address is refused also with allow_private_networks.
    blocked = fetcher.fetch(f"{srv.base}/to-metadata")
    assert (blocked.status, blocked.error, blocked.final_url) == (0, "blocked_address", "http://169.254.169.254/latest")


def test_settings_give_the_private_network_rule() -> None:
    class S:
        allow_private_networks = True
        private_network_allowlist = "10.0.0.0/8, .corp.local"

    fetcher = FeedFetcher.from_settings(S())
    assert fetcher.allow_private is True and fetcher.allowlist == ["10.0.0.0/8", ".corp.local"]
    assert fetcher.check_address("ftp://x.example/") == "invalid_url"


def test_body_above_the_limit_is_refused(srv: _Server) -> None:
    result = FeedFetcher(allow_private=True, max_bytes=1024).fetch(f"{srv.base}/big")
    assert (result.status, result.error, result.body) == (200, "too_large", None)


def test_poll_backoff_on_429_and_5xx_and_reset_on_success(srv: _Server, db_file: Path) -> None:
    fetcher = FeedFetcher(allow_private=True)
    busy = _add(db_file, f"{srv.base}/busy")
    down = _add(db_file, f"{srv.base}/down")
    page = _add(db_file, f"{srv.base}/page")
    outcomes = {o.feed_id: o for o in poller.poll_feeds(db_file, [busy, down, page], fetcher=fetcher, now=NOW)}
    # 429: the Retry-After value (25 hours) is longer than the backoff (2 hours) and wins.
    assert (outcomes[busy["id"]].result, outcomes[busy["id"]].error, outcomes[busy["id"]].next_poll_at) == ("error", "http_429", NOW + 90000)
    assert (outcomes[down["id"]].error, outcomes[down["id"]].next_poll_at) == ("http_503", NOW + 2 * poller.POLL_INTERVAL_SECONDS)
    assert outcomes[page["id"]].error == "parse:not_a_feed"
    # A second failure doubles the delay.
    second = poller.poll_feeds(db_file, [_feed(db_file, down["id"])], fetcher=fetcher, now=NOW + 10)
    assert second[0].next_poll_at == NOW + 10 + 4 * poller.POLL_INTERVAL_SECONDS and _feed(db_file, down["id"])["failure_count"] == 2
    # A poll on demand does not fetch a feed in backoff.
    report = poller.poll_account(db_file, 1, fetcher=fetcher, feed_id=down["id"], now=NOW + 20)
    assert [o["result"] for o in report["outcomes"]] == ["backoff"] and srv.paths.count("/down") == 2
    # After a success the failure count is 0 again.
    srv.routes["/down"] = (200, {}, RSS)
    poller.poll_feeds(db_file, [_feed(db_file, down["id"])], fetcher=fetcher, now=NOW + 30)
    stored = _feed(db_file, down["id"])
    assert (stored["failure_count"], stored["last_error"], stored["next_poll_at"]) == (0, None, NOW + 30 + poller.POLL_INTERVAL_SECONDS)


def test_due_feeds_respect_the_schedule(srv: _Server, db_file: Path) -> None:
    fetcher = FeedFetcher(allow_private=True)
    feed = _add(db_file, f"{srv.base}/feed")
    claire = db_file.parent / "claire.db"
    assert [o.added for o in poller.poll_due(db_file, claire_db_file=claire, fetcher=fetcher, now=NOW)] == [1]
    assert poller.poll_due(db_file, claire_db_file=claire, fetcher=fetcher, now=NOW + 60) == []
    assert _feed(db_file, feed["id"])["etag"] == '"e1"'


@pytest.mark.parametrize(("value", "expected"), [("120", 120.0), ("99999999", 7 * 24 * 3600.0), ("Fri, 15 Jan 2027 09:00:00 GMT", 3600.0), ("Fri, 15 Jan 2027 07:00:00 GMT", 0.0), ("soon", None), (None, None)])
def test_retry_after_forms(value, expected) -> None:
    # NOW is 2027-01-15T08:00:00Z.
    assert parse_retry_after(value, now=NOW) == expected
