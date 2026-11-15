"""End-to-end test of the media proxy of the federation client (FEDERATION_REMOTE_READING_DESIGN.md Sections 8 and 9.1, unit FP4-7).

A real app in network mode and a local HTTP server as the remote server. A post with media arrives from the sidecar; the body has the media with the addresses of the proxy; an image arrives through the proxy and is read from the store at the second request; a private address and an SVG are refused; a file above the admin limit is ``too_large``; a deleted post removes its media entries; and the cleanup command evicts old entries.
"""

from __future__ import annotations

import json
import re
from types import SimpleNamespace
from urllib.parse import urlsplit

import pytest
from starlette.testclient import TestClient

from claire import cli
from claire.api import server
from claire.store import accounts as accounts_store
from claire.store import client_db
from claire.store import federation_media
from tests.test_e2e_federation_following import _client_rows, _Sidecar
from tests.test_e2e_federation_timeline import CAROL, _event, _object
from tests.test_federation_console import PUBLIC_URL, _Client, _db, _isolate, _prepare, _session  # noqa: F401  (_isolate is an autouse fixture)
from tests.test_media_proxy import PNG, MediaServer

TIMELINE = "/api/federation/timeline"
USERCONTENT = "127.0.0.1:8767"
MEDIA = "127.0.0.1:8768"
MIB = 1024 * 1024
MP4 = bytes(range(256)) * 8


@pytest.fixture
def remote():
    srv = MediaServer({"/a.png": ("image/png", PNG), "/b.svg": ("image/svg+xml", b"<svg xmlns='http://www.w3.org/2000/svg'/>"), "/big.png": ("image/png", b"x" * (MIB + 1)), "/v.mp4": ("video/mp4", MP4), "/later.png": ("image/png", PNG)}).start()
    yield srv
    srv.stop()


@pytest.fixture(params=[False, True], ids=["app-origin fallback", "role hosts"])
def env(tmp_path, request, remote):
    settings, alpha, beta = _prepare(tmp_path)
    settings.allow_private_networks = True
    if request.param:
        settings.usercontent_fqdn = USERCONTENT
        settings.media_fqdn = MEDIA
    _db(settings, lambda conn: accounts_store.AccountStore(conn).set_federation_state("on", updated_by=1))
    _db(settings, lambda conn: conn.execute("UPDATE accounts SET profile_public=1 WHERE id IN (?, ?)", (alpha, beta)))
    app = server.create_app(settings, SimpleNamespace(provider=SimpleNamespace(name="stub")))
    with TestClient(app, base_url=PUBLIC_URL) as tc:
        client = client_db.connect_client_existing(client_db.client_db_path(settings.db_file))
        try:
            for account_id in (alpha, beta):
                client.execute("INSERT INTO federation_following (account_id, request, actor_iri, state, created_at, updated_at) VALUES (?, ?, ?, 'accepted', 1, 1)", (account_id, CAROL, CAROL))
            client.commit()
        finally:
            client.close()
        yield SimpleNamespace(client=_Client(tc), raw=tc, sidecar=_Sidecar(settings), settings=settings, tmp_path=tmp_path, alpha=alpha, beta=beta, admin=_session(settings, 1), alpha_session=_session(settings, alpha), beta_session=_session(settings, beta), role_hosts=request.param, remote=remote)


def _post(env, n: int, content_html: str, attachments: list[dict] | None = None) -> None:
    obj = _object(f"{CAROL}/notes/{n}", CAROL, content_html=content_html, attachments=attachments or [])
    assert env.sidecar.call("POST", "/timeline-events", _event("Create", CAROL, obj)).json()["decision"] == "accepted"


def _attachment(url: str, media_type: str, name: str) -> dict:
    return {"url": url, "media_type": media_type, "name": name, "width": None, "height": None, "blurhash": None}


def _body(env, n: int, *, account: str = "alpha") -> str:
    """The body of post ``n`` in the way of the frame: on the usercontent host without a cookie, or on the app origin with the session."""
    account_id, session = (env.alpha, env.alpha_session) if account == "alpha" else (env.beta, env.beta_session)
    item_id = _client_rows(env.settings, "SELECT id FROM federation_timeline_items WHERE account_id=? AND object_iri=?", (account_id, f"{CAROL}/notes/{n}"))[0][0]
    render_url = env.client.request("GET", f"{TIMELINE}/{item_id}", session=session).json()["item"]["render_url"]
    parts = urlsplit(render_url)
    target = f"{parts.path}?{parts.query}"
    res = env.raw.get(target, headers={"host": USERCONTENT}, follow_redirects=False) if env.role_hosts else env.client.request("GET", target, session=session)
    assert res.status_code == 200, res.text
    expected = f"img-src http://{MEDIA}; media-src http://{MEDIA};" if env.role_hosts else "img-src 'self'; media-src 'self';"
    assert expected in res.headers["content-security-policy"] and "default-src 'none'" in res.headers["content-security-policy"]
    return res.text


def _media(env, address: str, *, session: str | None = None, headers: dict | None = None):
    """Get a proxy address of a body: on the media host without a cookie, or on the app origin with the session of the owner."""
    parts = urlsplit(address.replace("&amp;", "&"))
    target = f"{parts.path}?{parts.query}"
    if env.role_hosts:
        assert f"{parts.scheme}://{parts.netloc}" == f"http://{MEDIA}"
        return env.raw.get(target, headers={"host": MEDIA, **(headers or {})}, follow_redirects=False)
    assert parts.netloc == ""
    return env.client.request("GET", target, session=session or env.alpha_session, headers=headers)


def _proxy_addresses(body: str) -> list[str]:
    return re.findall(r'src="([^"]*/media/remote/[0-9a-f]{64}\?t=[^"]+)"', body)


def test_e2e_media_arrives_through_the_proxy(env, monkeypatch, capsys):
    base = env.remote.base
    _post(env, 1, f'<p>Look <img src="{base}/a.png" alt="a cat" onerror="alert(1)"><video src="{base}/v.mp4"></video></p>', [_attachment(f"{base}/v.mp4", "video/mp4", "a clip"), _attachment(f"{base}/b.svg", "image/png", "a drawing"), _attachment(f"{base}/big.png", "image/png", "a large picture"), _attachment(f"{base}/doc.pdf", "application/pdf", "a paper")])

    # The body has the image and the clip with proxy addresses. No media element has a remote address, and the render made no request to the remote server.
    body = _body(env, 1)
    addresses = _proxy_addresses(body)
    assert len(addresses) == 4 and "onerror" not in body
    assert re.search(r'<img src="[^"]*/media/remote/[0-9a-f]{64}\?t=[^"]+" alt="a cat"', body)
    assert not re.search(r'<(?:img|video|audio|source)[^>]+(?:src|poster)="https?://127\.0\.0\.1:%d' % urlsplit(base).port, body)
    assert env.remote.hits == []
    by_hash = {federation_media.url_hash(f"{base}{path}"): path for path in ("/a.png", "/v.mp4", "/b.svg", "/big.png")}
    address_of = {by_hash[re.search(r"/media/remote/([0-9a-f]{64})", a).group(1)]: a for a in addresses}
    assert set(address_of) == {"/a.png", "/v.mp4", "/b.svg", "/big.png"}
    # An attachment that is not media is a link only; each attachment has its link to the remote server.
    assert f'<li data-media-kind="link"><span class="cb-remote-attachment-link"><a href="{base}/doc.pdf"' in body
    assert f'<video src="{address_of["/v.mp4"].replace("&", "&amp;")}" controls preload="none"' in body or f'<video src="{address_of["/v.mp4"]}" controls preload="none"' in body

    # An image arrives through the proxy with the headers of Section 9.1 rule 5. The second request reads the store.
    res = _media(env, address_of["/a.png"])
    assert res.status_code == 200 and res.content == PNG and res.headers["content-type"] == "image/png"
    assert res.headers["x-content-type-options"] == "nosniff" and res.headers["content-security-policy"] == "sandbox; default-src 'none'" and res.headers["cache-control"] == "private" and "cross-origin-resource-policy" in res.headers
    assert not env.role_hosts or "set-cookie" not in res.headers
    assert env.remote.hits == ["/a.png"]
    assert _media(env, address_of["/a.png"]).content == PNG and env.remote.hits == ["/a.png"]
    image_hash = federation_media.url_hash(f"{base}/a.png")
    assert (env.tmp_path / "media" / "remote" / image_hash).read_bytes() == PNG
    assert _client_rows(env.settings, "SELECT state, store_key, media_type, byte_size FROM federation_media_cache WHERE url_hash=?", (image_hash,)) == [("stored", f"remote/{image_hash}", "image/png", len(PNG))]
    # A byte range of the clip.
    part = _media(env, address_of["/v.mp4"], headers={"Range": "bytes=100-199"})
    assert part.status_code == 206 and part.content == MP4[100:200] and part.headers["content-range"] == f"bytes 100-199/{len(MP4)}" and part.headers["content-type"] == "video/mp4"

    # An SVG is refused, and a file above the admin limit is too_large. The next render shows only their links.
    assert env.client.request("PUT", "/api/admin/federation/reading", session=env.admin, json={"media_image_max_bytes": MIB}).status_code == 200
    assert _media(env, address_of["/b.svg"]).status_code == 502
    assert _media(env, address_of["/big.png"]).status_code == 413
    states = dict(_client_rows(env.settings, "SELECT remote_url, state FROM federation_media_cache"))
    assert states == {f"{base}/a.png": "stored", f"{base}/v.mp4": "stored", f"{base}/b.svg": "failed", f"{base}/big.png": "too_large"}
    assert not (env.tmp_path / "media" / "remote" / federation_media.url_hash(f"{base}/b.svg")).exists()
    again = _body(env, 1)
    assert len(_proxy_addresses(again)) == 2 and f'<a href="{base}/big.png"' in again and f'<a href="{base}/b.svg"' in again

    # The token binds the account and the URL: a changed token, a token of another URL, and a path without a token give 404. The cache is not in the public media route.
    hash_path = urlsplit(address_of["/a.png"]).path
    token = address_of["/a.png"].split("?t=")[1]
    for bad in (f"{hash_path}", f"{hash_path}?t={token[:-1]}0" if not token.endswith("0") else f"{hash_path}?t={token[:-1]}1", urlsplit(address_of["/v.mp4"]).path + f"?t={token}", f"/media/remote/{'0' * 64}?t={token}"):
        target = bad if not env.role_hosts else f"http://{MEDIA}{bad}"
        assert _media(env, target).status_code == 404, bad
    if env.role_hosts:
        # With a media host, the app origin has no proxy path.
        assert env.client.request("GET", f"{hash_path}?t={token}", session=env.alpha_session).status_code == 404
    else:
        # On the app origin the caller is the owner of the account of the token.
        assert _media(env, address_of["/a.png"], session=env.beta_session).status_code == 404
        assert env.client.request("GET", f"{hash_path}?t={token}").status_code in (401, 404)

    # A private address is refused when private networks are not allowed (production): no request goes out.
    env.settings.allow_private_networks = False
    _post(env, 2, f'<p><img src="{base}/later.png" alt="later"></p>')
    later = _proxy_addresses(_body(env, 2))
    env.remote.hits.clear()
    assert len(later) == 1 and _media(env, later[0]).status_code == 502 and env.remote.hits == []
    env.settings.allow_private_networks = True

    # Beta has the same post: one cache entry for both accounts. A Delete of the post removes its entries and files; the entry of the other post stays.
    assert len(_proxy_addresses(_body(env, 1, account="beta"))) == 2
    assert _client_rows(env.settings, "SELECT COUNT(*) FROM federation_media_cache")[0][0] == 5
    removed = env.sidecar.call("POST", "/timeline-events", _event("Delete", CAROL, object_iri=f"{CAROL}/notes/1", public=None)).json()
    assert removed["decision"] == "accepted" and removed["items"] == 2
    assert [row[0] for row in _client_rows(env.settings, "SELECT remote_url FROM federation_media_cache")] == [f"{base}/later.png"]
    assert not list((env.tmp_path / "media" / "remote").iterdir())
    assert _media(env, address_of["/a.png"]).status_code == 404

    # The cleanup command: a dry run counts, and --apply evicts the entries without an access in media_evict_days.
    monkeypatch.setattr(cli, "get_settings", lambda: env.settings)
    conn = client_db.connect_client_existing(client_db.client_db_path(env.settings.db_file))
    conn.execute("UPDATE federation_media_cache SET last_access_at=1")
    conn.commit()
    conn.close()
    capsys.readouterr()
    args = cli.build_parser().parse_args(["federation", "prune", "--json"])
    assert args.func(args) == 0 and json.loads(capsys.readouterr().out) == {"applied": False, "retention_days": 90, "expired_items": 0, "evict_days": 30, "evicted": 1, "orphans": 0}
    assert _client_rows(env.settings, "SELECT COUNT(*) FROM federation_media_cache")[0][0] == 1
    args = cli.build_parser().parse_args(["federation", "prune", "--apply", "--json"])
    assert args.func(args) == 0 and json.loads(capsys.readouterr().out)["evicted"] == 1
    assert _client_rows(env.settings, "SELECT COUNT(*) FROM federation_media_cache")[0][0] == 0
