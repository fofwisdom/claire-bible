"""End-to-end test of the federation client routes (FEDERATION_REMOTE_READING_DESIGN.md Sections 4.4, 5.3, and 8, unit FP4-5).

A real app in network mode. The sidecar gives timeline events through the signed internal API; the owner reads the timeline, opens a thread, renders a body, and takes one item into a theme. The view in ``/syndication`` is in ``e2e/federation_client.spec.js``.
"""

from __future__ import annotations

from types import SimpleNamespace
from urllib.parse import urlsplit

import pytest
from starlette.testclient import TestClient

from claire.api import server
from claire.api import usercontent
from claire.ingest.pipeline import IngestReport
from claire.ingest.service import IngestServicePool
from claire.store import accounts as accounts_store
from claire.store import client_db
from tests.test_e2e_federation_following import _client_rows, _Sidecar
from tests.test_e2e_federation_timeline import CAROL, STRANGER, _event, _object
from tests.test_federation_console import PUBLIC_URL, _Client, _db, _isolate, _prepare, _session  # noqa: F401  (_isolate is an autouse fixture)

TIMELINE = "/api/federation/timeline"
USERCONTENT = "127.0.0.1:8767"
ALPHA_THEME = 523456789012345
HOSTILE = '<p>Hello <b>world</b><script>alert(1)</script><img src="https://tracker.example/p.gif" onerror="alert(2)"><a href="https://remote.example/x" onclick="alert(3)">a link</a><a href="javascript:alert(4)">bad</a><iframe src="https://evil.example/"></iframe></p>'


def _make_env(tmp_path, *, usercontent_host: bool):
    settings, alpha, beta = _prepare(tmp_path)
    if usercontent_host:
        settings.usercontent_fqdn = USERCONTENT
    _db(settings, lambda conn: accounts_store.AccountStore(conn).set_federation_state("on", updated_by=1))
    _db(settings, lambda conn: conn.execute("UPDATE accounts SET profile_public=1 WHERE id IN (?, ?)", (alpha, beta)))
    _db(settings, lambda conn: conn.execute("INSERT INTO themes (id, seq, label, is_default, is_public, is_collaborator_accessible, created_at, updated_at, account_id, number) VALUES (11, 11, 'alpha theme', 1, 1, 0, 1, 1, ?, ?)", (alpha, ALPHA_THEME)))
    app = server.create_app(settings, SimpleNamespace(provider=SimpleNamespace(name="stub")))
    return settings, alpha, beta, app


@pytest.fixture(params=[False, True], ids=["app-origin fallback", "usercontent host"])
def env(tmp_path, request):
    settings, alpha, beta, app = _make_env(tmp_path, usercontent_host=request.param)
    with TestClient(app, base_url=PUBLIC_URL) as tc:
        client = client_db.connect_client_existing(client_db.client_db_path(settings.db_file))
        try:
            for account_id in (alpha, beta):
                client.execute("INSERT INTO federation_following (account_id, request, actor_iri, state, created_at, updated_at) VALUES (?, ?, ?, 'accepted', 1, 1)", (account_id, CAROL, CAROL))
            client.commit()
        finally:
            client.close()
        env = SimpleNamespace(client=_Client(tc), raw=tc, sidecar=_Sidecar(settings), settings=settings, alpha=alpha, beta=beta, alpha_session=_session(settings, alpha), beta_session=_session(settings, beta), usercontent_host=request.param)
        # Three posts of carol: a plain one with hostile HTML, one with a content warning, and a reply to a stranger with its parent.
        for event in (
            _event("Create", CAROL, _object(f"{CAROL}/notes/1", CAROL, content_html=HOSTILE, attachments=[{"url": "https://remote.example/media/1.png", "media_type": "image/png", "name": "a picture", "width": 1, "height": 1, "blurhash": None}])),
            _event("Create", CAROL, _object(f"{CAROL}/notes/2", CAROL, summary="spoiler", sensitive=True, content_html="<p>the hidden end</p>")),
            _event("Create", CAROL, _object(f"{CAROL}/notes/3", CAROL, in_reply_to=f"{STRANGER}/notes/9", content_html="<p>a reply</p>"), parents=[_object(f"{STRANGER}/notes/9", STRANGER, content_html="<p>the parent</p>", published_at=1799999000.0)]),
        ):
            assert env.sidecar.call("POST", "/timeline-events", event).json()["decision"] == "accepted"
        yield env


def _get(env, path: str, session: str | None = None):
    return env.client.request("GET", path, session=session or env.alpha_session)


def _render(env, render_url: str, *, session: str | None = None):
    """Get the body of an item in the way of the frame: on the usercontent host without a cookie, or on the app origin with the session."""
    parts = urlsplit(render_url)
    target = parts.path + "?" + parts.query
    if env.usercontent_host:
        assert f"{parts.scheme}://{parts.netloc}" == f"http://{USERCONTENT}"
        return env.raw.get(target, headers={"host": USERCONTENT}, follow_redirects=False)
    assert parts.netloc == ""
    return env.client.request("GET", target, session=session)


def test_e2e_timeline_list_thread_and_flags(env):
    page = _get(env, TIMELINE).json()
    # Newest first. The reply to a stranger and its parent are items of the thread only.
    assert [item["object_iri"] for item in page["items"]] == [f"{CAROL}/notes/2", f"{CAROL}/notes/1"]
    assert page["counts"] == {"total": 2, "unread": 2, "starred": 0, "mentions": 0, "unread_mentions": 0, "ingested": 0, "private_messages": 0, "published": 0}
    plain, warned = page["items"][1], page["items"][0]
    # The list has plain text only: no tag of the remote HTML, and no text of an item with a content warning.
    assert plain["text"] == "Hello worlda linkbad" and "<" not in plain["text"]
    assert (warned["summary"], warned["sensitive"], warned["text"]) == ("spoiler", True, "")
    assert plain["author"]["handle"] == "@carol@remote.example" and plain["attachments"] == 1
    assert "render_url" not in plain and "content_html" not in plain

    # Cursor pages and filters.
    first = _get(env, f"{TIMELINE}?limit=1").json()
    assert [item["id"] for item in first["items"]] == [warned["id"]] and first["next_max_id"] == warned["id"]
    assert [item["id"] for item in _get(env, f"{TIMELINE}?limit=1&max_id={first['next_max_id']}").json()["items"]] == [plain["id"]]
    assert _get(env, f"{TIMELINE}?starred=1").json()["items"] == []
    for bad in ("?limit=0", "?limit=81", "?max_id=x", "?unread=yes"):
        assert _get(env, TIMELINE + bad).status_code == 400

    # Flags: read and starred. An unknown field or a value that is not a boolean is refused.
    res = env.client.request("PATCH", f"{TIMELINE}/{plain['id']}", session=env.alpha_session, json={"unread": False, "starred": True})
    assert res.status_code == 200 and (res.json()["item"]["unread"], res.json()["item"]["starred"]) == (False, True)
    assert res.json()["counts"]["unread"] == 1 and res.json()["counts"]["starred"] == 1
    assert [item["id"] for item in _get(env, f"{TIMELINE}?starred=1").json()["items"]] == [plain["id"]]
    assert [item["id"] for item in _get(env, f"{TIMELINE}?unread=1").json()["items"]] == [warned["id"]]
    for body in ({}, {"unread": 1}, {"starred": True, "mention": True}):
        assert env.client.request("PATCH", f"{TIMELINE}/{plain['id']}", session=env.alpha_session, json=body).status_code == 400

    # Thread view: all items of the thread, oldest first, also those that are not in the timeline.
    reply_id = _client_rows(env.settings, "SELECT id FROM federation_timeline_items WHERE account_id=? AND object_iri=?", (env.alpha, f"{CAROL}/notes/3"))[0][0]
    detail = _get(env, f"{TIMELINE}/{reply_id}").json()
    assert [(entry["object_iri"], entry["in_timeline"]) for entry in detail["thread"]] == [(f"{STRANGER}/notes/9", False), (f"{CAROL}/notes/3", False)]
    assert detail["item"]["in_reply_to"] == f"{STRANGER}/notes/9" and all("render_url" in entry for entry in detail["thread"])

    # The items belong to the account: the same ID gives 404 for another owner, and beta has its own items.
    assert _get(env, f"{TIMELINE}/{plain['id']}", env.beta_session).status_code == 404
    assert env.client.request("PATCH", f"{TIMELINE}/{plain['id']}", session=env.beta_session, json={"starred": True}).status_code == 404
    assert _get(env, TIMELINE, env.beta_session).json()["counts"]["unread"] == 2
    assert _get(env, f"{TIMELINE}/999999").status_code == 404
    # The published documents of the account (stage 3): none yet.
    assert _get(env, "/api/federation/objects").json() == {"total": 0, "items": [], "next_max_id": None}
    # Without a session there is no timeline.
    assert env.client.request("GET", TIMELINE).status_code in (401, 404)


def test_e2e_body_renders_in_a_sandbox_and_no_script_runs(env):
    item_id = _client_rows(env.settings, "SELECT id FROM federation_timeline_items WHERE account_id=? AND object_iri=?", (env.alpha, f"{CAROL}/notes/1"))[0][0]
    render_url = _get(env, f"{TIMELINE}/{item_id}").json()["item"]["render_url"]
    res = _render(env, render_url, session=env.alpha_session)
    assert res.status_code == 200, res.text
    # The policy permits no script, and the page has an opaque origin. Media loads only from the media proxy (unit FP4-7): this origin when the instance has no media host, and nothing on a usercontent host without a media host.
    csp = res.headers["content-security-policy"]
    assert ("img-src" not in csp) if env.usercontent_host else ("img-src 'self'; media-src 'self';" in csp)
    assert "default-src 'none'" in csp and "script-src" not in csp and "sandbox allow-popups allow-popups-to-escape-sandbox" in csp and "allow-scripts" not in csp
    assert f"frame-ancestors {PUBLIC_URL}" in csp if env.usercontent_host else "frame-ancestors 'self'" in csp
    body = res.text
    for forbidden in ("<script", "onerror", "onclick", "javascript:", "<iframe", "tracker.example", "evil.example"):
        assert forbidden not in body, forbidden
    # No element has a remote address: an image has the address of the media proxy, or is absent.
    assert 'src="http' not in body and (("<img" not in body) if env.usercontent_host else ('<img src="/media/remote/' in body))
    assert "Hello <b>world</b>" in body
    assert '<a href="https://remote.example/x" target="_blank" rel="nofollow noopener noreferrer">a link</a>' in body
    # The attachment has a link to the remote server: the viewer contacts the remote server only by a click.
    assert '<a href="https://remote.example/media/1.png" target="_blank" rel="nofollow noopener noreferrer">a picture</a>' in body
    # The usercontent host sets no cookie.
    assert not env.usercontent_host or "set-cookie" not in res.headers

    # A token is for one item and one account, and it expires.
    other_id = _client_rows(env.settings, "SELECT id FROM federation_timeline_items WHERE account_id=? AND object_iri=?", (env.alpha, f"{CAROL}/notes/2"))[0][0]
    path, _, token = render_url.partition("?t=")
    base = path.rsplit("/", 1)[0]
    assert _render(env, f"{base}/{other_id}?t={token}", session=env.alpha_session).status_code == 404
    assert _render(env, f"{path}?t=", session=env.alpha_session).status_code == 404
    assert _render(env, f"{path}?t={usercontent.create_remote_token(env.beta, item_id)}", session=env.alpha_session).status_code == 404
    assert _render(env, f"{path}?t={usercontent.create_remote_token(env.alpha, item_id, expires_in=-10)}", session=env.alpha_session).status_code == 404
    # A token of the kind ``remote`` is not a document token, and the reverse.
    assert not usercontent.verify_viewing_token(token, env.alpha, 0, item_id, 1)

    app_path = urlsplit(render_url).path + "?t=" + token
    if env.usercontent_host:
        # With a usercontent host the body renders on that origin only: the app origin has no such page.
        assert env.client.request("GET", app_path, session=env.alpha_session).status_code == 404
    else:
        # The fallback on the app origin needs the session of the owner of the item.
        assert env.client.request("GET", app_path).status_code in (401, 404)
        assert env.client.request("GET", app_path, session=env.beta_session).status_code == 404


def test_e2e_ingest_of_one_item_links_the_document(env, monkeypatch):
    calls: list[tuple[str, dict]] = []

    class _Service:
        def ingest(self, payload, **kwargs):
            calls.append((payload, kwargs))
            return IngestReport(document_id="doc_from_item", title="From the timeline")

    monkeypatch.setattr(IngestServicePool, "get_service", lambda self, *args, **kwargs: _Service())
    item_id = _client_rows(env.settings, "SELECT id FROM federation_timeline_items WHERE account_id=? AND object_iri=?", (env.alpha, f"{CAROL}/notes/1"))[0][0]
    path = f"{TIMELINE}/{item_id}/ingest"
    # The theme is necessary, and it must be a theme of the caller.
    assert env.client.request("POST", path, session=env.alpha_session, json={}).status_code == 400
    assert env.client.request("POST", path, session=env.beta_session, json={"theme": str(ALPHA_THEME)}).status_code == 404
    assert env.client.request("POST", f"{TIMELINE}/999999/ingest", session=env.alpha_session, json={"theme": str(ALPHA_THEME)}).status_code == 404
    assert calls == []

    res = env.client.request("POST", path, session=env.alpha_session, json={"theme": str(ALPHA_THEME)})
    assert res.status_code == 200, res.text
    assert res.json()["document_id"] == "doc_from_item" and res.json()["linked"] == {"theme_id": 11, "document_id": "doc_from_item"}
    # The existing URL ingest got the address of the post.
    assert calls == [(f"{CAROL}/notes/1", {"source": "federation"})]
    # The item stays in the timeline and has the link to the document.
    item = _get(env, f"{TIMELINE}/{item_id}").json()["item"]
    assert item["linked"] == {"theme_id": 11, "document_id": "doc_from_item"}
    assert _get(env, TIMELINE).json()["counts"]["ingested"] == 1
