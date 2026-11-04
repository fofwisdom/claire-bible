import asyncio
import time
from types import SimpleNamespace
from starlette.responses import PlainTextResponse
from starlette.types import Scope, Receive, Send

import pytest
from claire.api import security
from claire.api.host_router import HostRoles, HostRouter
from claire.api.media import create_media_app
from claire.api.usercontent import create_usercontent_app, create_viewing_token
from claire.store import db as dbm
from claire.store.media import LocalMediaStore, create_media_capability_token
from claire.store.secrets import SecretService
from claire.store.theme import bump_theme_visibility_epoch


async def _call_asgi(app, path, host="127.0.0.1:8766", headers=None, method="GET"):
    sent_messages = []
    header_list = [(b"host", host.encode("ascii"))]
    if headers:
        for k, v in headers:
            header_list.append((k.lower().encode("latin1"), v.encode("latin1")))

    scope = {
        "type": "http",
        "asgi": {"version": "3.0"},
        "http_version": "1.1",
        "method": method,
        "scheme": "http",
        "path": path.split("?")[0],
        "raw_path": path.split("?")[0].encode("ascii"),
        "query_string": path.split("?")[1].encode("ascii") if "?" in path else b"",
        "headers": header_list,
        "client": ("127.0.0.1", 12345),
        "server": ("127.0.0.1", 8766),
    }

    async def receive():
        return {"type": "http.request", "body": b"", "more_body": False}

    async def send(message):
        sent_messages.append(message)

    await app(scope, receive, send)

    status = None
    resp_headers = {}
    body = b""
    for msg in sent_messages:
        if msg["type"] == "http.response.start":
            status = msg["status"]
            resp_headers = {k.decode("latin1").lower(): v.decode("latin1") for k, v in msg.get("headers", [])}
        elif msg["type"] == "http.response.body":
            body += msg.get("body", b"")

    return SimpleNamespace(status=status, headers=resp_headers, body=body)


@pytest.mark.asyncio
async def test_e2e_media_delivery_and_capability_lifecycle(tmp_path):
    sec = SecretService(b"e" * 32)
    db_file = tmp_path / "claire.db"
    media_dir = tmp_path / "media"
    media_dir.mkdir(parents=True)
    images_dir = tmp_path / "images"
    images_dir.mkdir(parents=True)

    # Initialize DB
    conn = dbm.connect(db_file)
    dbm.init_db(conn)

    # Insert public theme 10 and private theme 20
    now = time.time()
    conn.execute(
        """
        INSERT INTO themes (id, seq, label, db_path, is_default, is_public, account_id, number, visibility_epoch, created_at, updated_at)
        VALUES (10, 10, 'Public Theme', ?, 0, 1, 1, 10000000000000010, 1, ?, ?)
        """,
        (str(db_file), now, now),
    )
    conn.execute(
        """
        INSERT INTO themes (id, seq, label, db_path, is_default, is_public, account_id, number, visibility_epoch, created_at, updated_at)
        VALUES (20, 20, 'Private Theme', ?, 0, 0, 1, 10000000000000020, 1, ?, ?)
        """,
        (str(db_file), now, now),
    )

    # Insert public document and private document
    conn.execute(
        """
        INSERT INTO documents (id, title, url, theme_id, detail_html, hidden, fetched_at)
        VALUES ('101', 'Public Doc', 'https://example.com/101', 10, '<p>Public doc with <img src="images/pub.png"></p>', 0, ?)
        """,
        (now,),
    )
    conn.execute(
        """
        INSERT INTO documents (id, title, url, theme_id, detail_html, hidden, fetched_at)
        VALUES ('201', 'Private Doc', 'https://example.com/201', 20, '<p>Private doc with <img src="images/priv.png"></p>', 0, ?)
        """,
        (now,),
    )
    conn.commit()

    # Store media files
    store = LocalMediaStore(media_dir, secret_service=sec)
    pub_content = b"\x89PNG\r\npublicimagecontent12345"
    priv_content = b"\x89PNG\r\nprivateimagecontent67890"
    await store.put("pub.png", pub_content, "image/png")
    await store.put("priv.png", priv_content, "image/png")

    # Setup RoleHosts and App
    APP_HOST = "127.0.0.1:8766"
    UC_HOST = "127.0.0.1:8767"
    MEDIA_HOST = "127.0.0.1:8768"

    settings = SimpleNamespace(
        environment="development",
        site_domain="",
        public_url=f"http://{APP_HOST}",
        cors_allowed_origins="",
        inject_token="t" * 32,
        readonly_token="",
        anonymous_readonly=False,
        fqdn=APP_HOST,
        usercontent_fqdn=UC_HOST,
        media_fqdn=MEDIA_HOST,
        db_file=db_file,
        data_dir=tmp_path,
        secret_service=sec,
    )
    roles = HostRoles.from_settings(settings, environment="development", app_authority=APP_HOST)

    async def dummy_app(scope, receive, send):
        await PlainTextResponse("App OK")(scope, receive, send)

    wrapped_app = security.wrap_web_app(dummy_app, settings)

    # 1. Render Public Doc from Usercontent Host
    uc_pub_resp = await _call_asgi(wrapped_app, "/render/doc/101", host=UC_HOST)
    assert uc_pub_resp.status == 200
    html_text = uc_pub_resp.body.decode("utf-8")
    assert f"http://{MEDIA_HOST}/media/pub.png" in html_text
    assert f"http://{MEDIA_HOST}" in uc_pub_resp.headers["content-security-policy"]

    # 2. Fetch Public Media from Media Host
    media_pub_resp = await _call_asgi(wrapped_app, "/media/pub.png", host=MEDIA_HOST)
    assert media_pub_resp.status == 200
    assert media_pub_resp.body == pub_content
    assert "public" in media_pub_resp.headers["cache-control"]
    assert "immutable" in media_pub_resp.headers["cache-control"]
    assert "set-cookie" not in media_pub_resp.headers

    # 3. Stream Public Media with HTTP Range
    range_resp = await _call_asgi(
        wrapped_app,
        "/media/pub.png",
        host=MEDIA_HOST,
        headers=[("Range", "bytes=0-7")],
    )
    assert range_resp.status == 206
    assert range_resp.body == pub_content[:8]
    assert range_resp.headers["content-range"] == f"bytes 0-7/{len(pub_content)}"

    # 4. Render Private Doc from Usercontent Host with Doc Viewing Token
    doc_token = create_viewing_token(
        account_id=1,
        theme_id=20,
        doc_id=201,
        visibility_epoch=1,
        secret_service=sec,
    )
    uc_priv_resp = await _call_asgi(wrapped_app, f"/render/doc/201?token={doc_token}", host=UC_HOST)
    assert uc_priv_resp.status == 200
    priv_html = uc_priv_resp.body.decode("utf-8")
    assert f"http://{MEDIA_HOST}/media/priv.png?" in priv_html
    assert "token=" in priv_html
    assert "visibility_epoch=1" in priv_html

    # Extract media capability URL query parameters
    import html
    import urllib.parse
    img_tag_start = priv_html.find(f"http://{MEDIA_HOST}/media/priv.png?")
    img_tag_end = priv_html.find('"', img_tag_start)
    media_priv_url = priv_html[img_tag_start:img_tag_end]
    parsed_url = urllib.parse.urlparse(html.unescape(media_priv_url))
    media_query = parsed_url.query

    # 5. Fetch Private Media with capability URL
    media_priv_resp = await _call_asgi(wrapped_app, f"/media/priv.png?{media_query}", host=MEDIA_HOST)
    assert media_priv_resp.status == 200
    assert media_priv_resp.body == priv_content
    assert media_priv_resp.headers["cache-control"] == "private, no-store"

    # 6. Bump visibility_epoch on Theme 20
    bump_theme_visibility_epoch(conn, 20)

    # 7. Old capability URL is immediately rejected (403)
    media_revoked_resp = await _call_asgi(wrapped_app, f"/media/priv.png?{media_query}", host=MEDIA_HOST)
    assert media_revoked_resp.status == 403

    conn.close()
