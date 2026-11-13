"""Tests for usercontent sub-application, capability viewing tokens, nh3 sanitization, and CSP (설계 §5.1–§5.2, §15 S1, §16.4).

Requirements tested:
1. Capability viewing tokens (1-hour / 3600s TTL, bound to account_id, theme_id, doc_id, visibility_epoch, exp).
2. nh3 HTML sanitization (stripping user inline scripts, event handlers onerror/onload/onclick, iframes, etc.).
3. Static profile (/render/doc/{id}) rendering and CSP (frame-ancestors, sandbox allow-popups...).
4. Deck profile (/p/presentation) rendering and CSP (script-src {usercontent} 'nonce-{nonce}', frame-ancestors).
5. Access control: private themes/documents require valid viewing token; public content renders without token.
6. Cookie boundary: no Set-Cookie emitted, incoming Cookie header ignored and stripped.
7. App-only routes (/admin/accounts, /api/..., /health) blocked with 404 on usercontent host.
8. Opaque origin sandbox (Origin: null) allowed on usercontent host.
9. App CSP reflects frame-src {scheme}://{usercontent}.
10. E2E full lifecycle test.
"""

from __future__ import annotations

import asyncio

import os
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
from starlette.applications import Starlette
from starlette.responses import PlainTextResponse
from starlette.routing import Route

from claire.api import security
from claire.api.host_router import HostRoles
from claire.api.usercontent import (
    DEFAULT_VIEWING_TOKEN_TTL,
    create_usercontent_app,
    create_viewing_token,
    sanitize_usercontent_html,
    verify_viewing_token,
)
from claire.store import db as dbm
from claire.store.secrets import SecretService

DEV_IP = "192.0.2.10"
APP_HOST = f"{DEV_IP}:8766"
APP_ORIGIN = f"http://{APP_HOST}"
UC_HOST = f"{DEV_IP}:8767"
UC_ORIGIN = f"http://{UC_HOST}"
MEDIA_HOST = f"{DEV_IP}:8768"
TEST_MASTER_KEY = b"0123456789abcdef0123456789abcdef"


@pytest.fixture(autouse=True)
def _isolate_env(monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
    """Isolate environment and prevent writing to root data/."""
    for name in list(os.environ):
        if name.startswith("CLAIRE_"):
            monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("CLAIRE_SECRET_KEY", TEST_MASTER_KEY.hex())


def _create_settings(tmp_path: Path, **overrides: Any) -> SimpleNamespace:
    values = dict(
        environment="development",
        public_url=APP_ORIGIN,
        cors_allowed_origins="",
        inject_token="owner-" + "o" * 32,
        readonly_token="",
        anonymous_readonly=False,
        db_file=tmp_path / "claire.db",
        data_dir=tmp_path / "data",
        site_domain="",
        usercontent_fqdn=UC_HOST,
        media_fqdn=MEDIA_HOST,
    )
    values.update(overrides)
    return SimpleNamespace(**values)


def _init_db(db_path: Path) -> None:
    conn = dbm.connect(db_path)
    dbm.init_db(conn)
    conn.commit()
    conn.close()


async def _call_asgi(
    app: Any,
    path: str,
    *,
    host: str = UC_HOST,
    method: str = "GET",
    query: str = "",
    headers: list[tuple[str, str]] | None = None,
) -> tuple[int, dict[str, str], bytes]:
    raw_headers = [(b"host", host.encode("ascii"))]
    if headers:
        for k, v in headers:
            raw_headers.append((k.lower().encode("ascii"), v.encode("latin-1")))
    messages: list[dict[str, Any]] = []
    request_sent = False

    async def receive() -> dict[str, Any]:
        # The body comes once. After it, a real server gives no message until the client disconnects; Starlette 1.10 file responses wait for that message while they stream.
        nonlocal request_sent
        if not request_sent:
            request_sent = True
            return {"type": "http.request", "body": b"", "more_body": False}
        await asyncio.Event().wait()
        return {"type": "http.disconnect"}

    async def send(message: dict[str, Any]) -> None:
        messages.append(message)

    scope = {
        "type": "http",
        "asgi": {"version": "3.0"},
        "http_version": "1.1",
        "method": method,
        "scheme": "http",
        "path": path,
        "raw_path": path.encode("ascii"),
        "query_string": query.encode("latin-1"),
        "headers": raw_headers,
        "client": ("198.51.100.20", 50000),
        "server": (DEV_IP, int(host.split(":")[1]) if ":" in host else 80),
        "state": {},
    }
    await app(scope, receive, send)
    start = next(m for m in messages if m["type"] == "http.response.start")
    body = b"".join(m.get("body", b"") for m in messages if m["type"] == "http.response.body")
    decoded_headers: dict[str, str] = {}
    for k, v in start.get("headers", []):
        decoded_headers[k.decode("ascii").lower()] = v.decode("latin-1")
    return start["status"], decoded_headers, body


# --- 1. nh3 HTML 살균 검증 (외부 라이브러리 호출) --------------------------------


def test_nh3_html_sanitization_removes_scripts_and_handlers():
    """Verify nh3.clean removes script tags and inline event handlers while preserving safe structure."""
    dirty_html = """
    <article class="content" id="article-1">
        <h1>Clean Header</h1>
        <script>alert("attack-script")</script>
        <img src="https://example.com/test.jpg" onerror="alert('attack-onerror')" onload="alert('attack-onload')">
        <a href="javascript:alert('xss')" onclick="alert('attack-onclick')">Safe text link</a>
        <div onmouseover="alert('attack-hover')">Hover box</div>
        <iframe src="https://malicious.example.com"></iframe>
        <p>Paragraph with <strong>bold</strong> and <em>emphasis</em>.</p>
    </article>
    """
    cleaned = sanitize_usercontent_html(dirty_html)

    # Malicious elements must be stripped
    assert "<script" not in cleaned
    assert "attack-script" not in cleaned
    assert "onerror" not in cleaned
    assert "onload" not in cleaned
    assert "onclick" not in cleaned
    assert "onmouseover" not in cleaned
    assert "javascript:" not in cleaned
    assert "<iframe" not in cleaned

    # Safe elements and attributes must be preserved
    assert "Clean Header" in cleaned
    assert 'class="content"' in cleaned
    assert 'id="article-1"' in cleaned
    assert "Safe text link" in cleaned
    assert "Paragraph with" in cleaned
    assert "<strong>bold</strong>" in cleaned


# --- 2. 1시간 열람 capability 토큰 검증 (SecretService 인터페이스) -------------


def test_viewing_token_creation_and_verification():
    """Verify 1-hour capability viewing token creation, signature verification, and edge cases."""
    sec = SecretService(master_key=TEST_MASTER_KEY)

    token = create_viewing_token(
        account_id=1,
        theme_id=10,
        doc_id=42,
        visibility_epoch=1,
        expires_in=DEFAULT_VIEWING_TOKEN_TTL,
        secret_service=sec,
        now=1000.0,
    )
    assert "." in token
    exp_part, sig_part = token.split(".", 1)
    assert int(exp_part) == 1000 + 3600
    assert len(sig_part) == 64

    # Valid token verification
    assert verify_viewing_token(token, account_id=1, theme_id=10, doc_id=42, visibility_epoch=1, secret_service=sec, now=2000.0) is True

    # Expired token verification (now > exp)
    assert verify_viewing_token(token, account_id=1, theme_id=10, doc_id=42, visibility_epoch=1, secret_service=sec, now=5000.0) is False

    # Visibility epoch mismatch verification (e.g. changed from 1 to 2)
    assert verify_viewing_token(token, account_id=1, theme_id=10, doc_id=42, visibility_epoch=2, secret_service=sec, now=2000.0) is False

    # Mismatched IDs verification
    assert verify_viewing_token(token, account_id=2, theme_id=10, doc_id=42, visibility_epoch=1, secret_service=sec, now=2000.0) is False
    assert verify_viewing_token(token, account_id=1, theme_id=11, doc_id=42, visibility_epoch=1, secret_service=sec, now=2000.0) is False
    assert verify_viewing_token(token, account_id=1, theme_id=10, doc_id=43, visibility_epoch=1, secret_service=sec, now=2000.0) is False

    # Tampered or invalid token verification
    assert verify_viewing_token("invalid-format", account_id=1, theme_id=10, doc_id=42, visibility_epoch=1, secret_service=sec) is False
    assert verify_viewing_token(f"{exp_part}.tampered_signature_000000000000000000000000000000000000000000000000", account_id=1, theme_id=10, doc_id=42, visibility_epoch=1, secret_service=sec) is False
    assert verify_viewing_token("", account_id=1, theme_id=10, doc_id=42, visibility_epoch=1, secret_service=sec) is False


# --- 3. Static 프로필 렌더링 및 CSP 검증 (I/O & 통합) ----------------------------


@pytest.mark.asyncio
async def test_static_profile_rendering_and_csp(tmp_path: Path):
    """Verify static profile (/render/doc/{id}) renders sanitized HTML with required CSP."""
    db_path = tmp_path / "claire.db"
    _init_db(db_path)

    conn = dbm.connect(db_path)
    conn.execute(
        "INSERT INTO themes (id, seq, label, account_id, visibility_epoch, is_public, created_at, updated_at) VALUES (1, 1, 'Public Theme', 1, 1, 1, 1000.0, 1000.0)"
    )
    conn.execute(
        "INSERT INTO documents (id, theme_id, title, detail_html, hidden) VALUES ('10', 1, 'Article 10', '<p>Hello <script>alert(1)</script><img src=x onerror=alert(2)></p>', 0)"
    )
    conn.commit()
    conn.close()

    settings = _create_settings(tmp_path)
    app = security.wrap_web_app(Starlette(), settings)

    status, headers, body = await _call_asgi(app, "/render/doc/10", host=UC_HOST)
    assert status == 200
    body_text = body.decode("utf-8")
    assert "<title>Article 10</title>" in body_text
    assert "<script" not in body_text
    assert "onerror" not in body_text
    assert "Hello" in body_text

    # Verify CSP
    csp = headers.get("content-security-policy", "")
    assert f"frame-ancestors {APP_ORIGIN}" in csp
    assert "sandbox allow-popups allow-popups-to-escape-sandbox" in csp
    assert headers.get("x-content-type-options") == "nosniff"


# --- 4. Deck 프로필 렌더링 및 Nonce CSP 검증 (I/O & 통합) ------------------------


@pytest.mark.asyncio
async def test_deck_profile_rendering_and_nonce_csp(tmp_path: Path):
    """Verify deck profile (/p/presentation) renders reveal.js runtime with nonce CSP."""
    db_path = tmp_path / "claire.db"
    _init_db(db_path)

    conn = dbm.connect(db_path)
    conn.execute(
        "INSERT INTO themes (id, seq, label, account_id, visibility_epoch, is_public, created_at, updated_at) VALUES (1, 1, 'Public Theme', 1, 1, 1, 1000.0, 1000.0)"
    )
    conn.execute(
        "INSERT INTO documents (id, theme_id, title, detail_html, hidden) VALUES ('20', 1, 'Presentation Title', '<p>Slide content</p>', 0)"
    )
    conn.commit()
    conn.close()

    settings = _create_settings(tmp_path)
    app = security.wrap_web_app(Starlette(), settings)

    status, headers, body = await _call_asgi(app, "/p/presentation", host=UC_HOST, query="id=20")
    assert status == 200
    body_text = body.decode("utf-8")
    assert "class=\"reveal\"" in body_text
    assert "class=\"slides\"" in body_text
    assert "reveal.js" in body_text

    # Verify deck CSP
    csp = headers.get("content-security-policy", "")
    assert f"script-src {UC_ORIGIN} 'nonce-" in csp
    assert f"frame-ancestors {APP_ORIGIN}" in csp

    # Verify script tag in body contains the same nonce
    nonce_part = csp.split("'nonce-")[1].split("'")[0]
    assert f'nonce="{nonce_part}"' in body_text


# --- 5. 비공개 테마/문서 1시간 열람 capability 토큰 통제 검증 ------------------


@pytest.mark.asyncio
async def test_viewing_token_access_control_on_private_content(tmp_path: Path):
    """Verify private content blocks unauthorized requests and admits valid capability tokens."""
    db_path = tmp_path / "claire.db"
    _init_db(db_path)
    sec = SecretService(master_key=TEST_MASTER_KEY)

    conn = dbm.connect(db_path)
    # Theme 2 is private (is_public=0)
    conn.execute(
        "INSERT INTO themes (id, seq, label, account_id, visibility_epoch, is_public, created_at, updated_at) VALUES (2, 2, 'Private Theme', 1, 1, 0, 1000.0, 1000.0)"
    )
    conn.execute(
        "INSERT INTO documents (id, theme_id, title, detail_html, hidden) VALUES ('30', 2, 'Confidential Report', '<p>Classified</p>', 0)"
    )
    conn.commit()
    conn.close()

    settings = _create_settings(tmp_path)
    app = security.wrap_web_app(Starlette(), settings, secret_service=sec)

    # 1. Missing token -> 403 Forbidden
    status, _, _ = await _call_asgi(app, "/render/doc/30", host=UC_HOST)
    assert status == 403

    # 2. Forged token -> 403 Forbidden
    status, _, _ = await _call_asgi(app, "/render/doc/30", host=UC_HOST, query="token=99999999.forged_signature")
    assert status == 403

    # 3. Expired token -> 403 Forbidden
    expired_token = create_viewing_token(account_id=1, theme_id=2, doc_id=30, visibility_epoch=1, expires_in=-60, secret_service=sec)
    status, _, _ = await _call_asgi(app, "/render/doc/30", host=UC_HOST, query=f"token={expired_token}")
    assert status == 403

    # 4. Valid token -> 200 OK
    valid_token = create_viewing_token(account_id=1, theme_id=2, doc_id=30, visibility_epoch=1, expires_in=3600, secret_service=sec)
    status, _, body = await _call_asgi(app, "/render/doc/30", host=UC_HOST, query=f"token={valid_token}")
    assert status == 200
    assert "Classified" in body.decode("utf-8")

    # 5. Visibility epoch bumped (theme visibility changed / doc purged) -> old token invalidated (403)
    conn = dbm.connect(db_path)
    conn.execute("UPDATE themes SET visibility_epoch = 2 WHERE id = 2")
    conn.commit()
    conn.close()

    status, _, _ = await _call_asgi(app, "/render/doc/30", host=UC_HOST, query=f"token={valid_token}")
    assert status == 403

    # 6. New token matching visibility_epoch=2 -> 200 OK
    new_token = create_viewing_token(account_id=1, theme_id=2, doc_id=30, visibility_epoch=2, expires_in=3600, secret_service=sec)
    status, _, body = await _call_asgi(app, "/render/doc/30", host=UC_HOST, query=f"token={new_token}")
    assert status == 200
    assert "Classified" in body.decode("utf-8")


# --- 6. 쿠키 경계 및 쿠키 무시 검증 ----------------------------------------------


@pytest.mark.asyncio
async def test_cookieless_boundary_and_cookie_ignore(tmp_path: Path):
    """Verify usercontent host never sets Set-Cookie and incoming Cookie headers are ignored."""
    db_path = tmp_path / "claire.db"
    _init_db(db_path)

    conn = dbm.connect(db_path)
    conn.execute(
        "INSERT INTO themes (id, seq, label, account_id, visibility_epoch, is_public, created_at, updated_at) VALUES (1, 1, 'Public Theme', 1, 1, 1, 1000.0, 1000.0)"
    )
    conn.execute(
        "INSERT INTO documents (id, theme_id, title, detail_html, hidden) VALUES ('40', 1, 'Doc 40', '<p>Body</p>', 0)"
    )
    conn.commit()
    conn.close()

    settings = _create_settings(tmp_path)
    app = security.wrap_web_app(Starlette(), settings)

    status, headers, _ = await _call_asgi(
        app,
        "/render/doc/40",
        host=UC_HOST,
        headers=[("Cookie", "claire_session=evil_cookie_value; __Host-claire_session=evil")],
    )
    assert status == 200
    assert "set-cookie" not in headers


# --- 7. App 전용 경로(/admin/accounts 등) 차단 검증 -----------------------------


@pytest.mark.asyncio
async def test_app_exclusive_routes_blocked_on_usercontent_host(tmp_path: Path):
    """Verify app-only routes are blocked with 404 on usercontent host."""
    settings = _create_settings(tmp_path)
    inner = Starlette(
        routes=[
            Route("/admin/accounts", lambda r: PlainTextResponse("admin")),
            Route("/health", lambda r: PlainTextResponse("ok")),
            Route("/api/me", lambda r: PlainTextResponse("me")),
        ]
    )
    app = security.wrap_web_app(inner, settings)

    for path in ("/admin/accounts", "/api/me", "/health", "/setup"):
        status, _, _ = await _call_asgi(app, path, host=UC_HOST)
        assert status == 404, f"Path {path} should return 404 on usercontent host"


# --- 8. 불투명 iframe sandbox Origin: null 허용 검증 ----------------------------


@pytest.mark.asyncio
async def test_opaque_sandbox_origin_null_allowed(tmp_path: Path):
    """Verify opaque origin sandbox (Origin: null) is accepted on usercontent host."""
    db_path = tmp_path / "claire.db"
    _init_db(db_path)

    conn = dbm.connect(db_path)
    conn.execute(
        "INSERT INTO themes (id, seq, label, account_id, visibility_epoch, is_public, created_at, updated_at) VALUES (1, 1, 'Public Theme', 1, 1, 1, 1000.0, 1000.0)"
    )
    conn.execute(
        "INSERT INTO documents (id, theme_id, title, detail_html, hidden) VALUES ('50', 1, 'Doc 50', '<p>Body</p>', 0)"
    )
    conn.commit()
    conn.close()

    settings = _create_settings(tmp_path)
    inner = Starlette(routes=[Route("/health", lambda r: PlainTextResponse("ok"))])
    app = security.wrap_web_app(inner, settings)

    # 1. usercontent host allows Origin: null
    status, headers, _ = await _call_asgi(
        app,
        "/render/doc/50",
        host=UC_HOST,
        headers=[("Origin", "null")],
    )
    assert status == 200
    assert headers.get("access-control-allow-origin") == "*"

    # 2. Static vendor asset allows Origin: null
    asset_status, asset_headers, _ = await _call_asgi(
        app,
        "/static/vendor/reveal.js/dist/reveal.js",
        host=UC_HOST,
        headers=[("Origin", "null")],
    )
    assert asset_status == 200
    assert asset_headers.get("access-control-allow-origin") == "*"

    # 3. Conversely, app host rejects Origin: null with 403
    app_status, _, _ = await _call_asgi(
        app,
        "/health",
        host=APP_HOST,
        headers=[("Origin", "null")],
    )
    assert app_status == 403


# --- 9. App CSP에 frame-src 반영 검증 -------------------------------------------


@pytest.mark.asyncio
async def test_app_csp_reflects_frame_src_usercontent(tmp_path: Path):
    """Verify App host response CSP contains frame-src {scheme}://{usercontent}."""
    settings = _create_settings(tmp_path)
    inner = Starlette(routes=[Route("/health", lambda r: PlainTextResponse("ok"))])
    app = security.wrap_web_app(inner, settings)

    status, headers, _ = await _call_asgi(app, "/health", host=APP_HOST)
    assert status == 200
    app_csp = headers.get("content-security-policy", "")
    assert f"frame-src {UC_ORIGIN}" in app_csp


# --- 10. 최소 1개 E2E 테스트: 전체 라이프사이클 렌더링 여정 -------------------


@pytest.mark.asyncio
async def test_e2e_full_lifecycle_rendering_journey(tmp_path: Path):
    """End-to-End test of the complete usercontent viewing journey:
    1. A private document is stored in a private theme.
    2. The app issues a 1-hour capability viewing token.
    3. An opaque sandbox iframe (Origin: null) on the usercontent host loads the rendered document.
    4. Malicious script injections are sanitized via nh3.
    5. Required static CSP headers (frame-ancestors, sandbox allow-popups) are strictly enforced.
    6. The deck profile presentation is loaded with matching script nonce and deck CSP.
    7. Visibility epoch is updated, immediately revoking previous capability tokens.
    """
    db_path = tmp_path / "claire.db"
    _init_db(db_path)
    sec = SecretService(master_key=TEST_MASTER_KEY)

    conn = dbm.connect(db_path)
    conn.execute(
        "INSERT INTO themes (id, seq, label, account_id, visibility_epoch, is_public, created_at, updated_at) VALUES (5, 5, 'E2E Private Theme', 1, 1, 0, 1000.0, 1000.0)"
    )
    evil_html = (
        "<h1>Project Roadmap</h1>"
        "<script>window.pwned = true;</script>"
        "<p>Quarterly targets and milestones.</p>"
        "<img src=\"chart.png\" onerror=\"maliciousCode()\">"
    )
    conn.execute(
        "INSERT INTO documents (id, theme_id, title, detail_html, hidden) VALUES ('100', 5, 'E2E Roadmap', ?, 0)",
        (evil_html,),
    )
    conn.commit()
    conn.close()

    settings = _create_settings(tmp_path)
    inner_app = Starlette(routes=[Route("/health", lambda r: PlainTextResponse("ok"))])
    full_app = security.wrap_web_app(inner_app, settings, secret_service=sec)

    # Step 1: Request without capability token must be rejected with 403
    unauth_status, _, _ = await _call_asgi(full_app, "/render/doc/100", host=UC_HOST, headers=[("Origin", "null")])
    assert unauth_status == 403

    # Step 2: Issue 1-hour capability token
    token = create_viewing_token(
        account_id=1,
        theme_id=5,
        doc_id=100,
        visibility_epoch=1,
        expires_in=3600,
        secret_service=sec,
    )

    # Step 3: Fetch static render profile using capability token from opaque sandbox
    render_status, render_headers, render_body = await _call_asgi(
        full_app,
        "/render/doc/100",
        host=UC_HOST,
        query=f"token={token}",
        headers=[("Origin", "null"), ("Cookie", "unwanted_session=test")],
    )
    assert render_status == 200
    render_html = render_body.decode("utf-8")

    # Step 4: Verify nh3 sanitization
    assert "Project Roadmap" in render_html
    assert "Quarterly targets and milestones." in render_html
    assert "<script" not in render_html
    assert "window.pwned" not in render_html
    assert "onerror" not in render_html

    # Step 5: Verify static CSP and cookieless headers
    static_csp = render_headers.get("content-security-policy", "")
    assert f"frame-ancestors {APP_ORIGIN}" in static_csp
    assert "sandbox allow-popups allow-popups-to-escape-sandbox" in static_csp
    assert render_headers.get("x-content-type-options") == "nosniff"
    assert render_headers.get("cache-control") == "private, no-store"
    assert "set-cookie" not in render_headers

    # Step 6: Fetch deck profile using capability token
    deck_status, deck_headers, deck_body = await _call_asgi(
        full_app,
        "/p/presentation",
        host=UC_HOST,
        query=f"id=100&token={token}",
        headers=[("Origin", "null")],
    )
    assert deck_status == 200
    deck_csp = deck_headers.get("content-security-policy", "")
    assert f"script-src {UC_ORIGIN} 'nonce-" in deck_csp
    assert f"frame-ancestors {APP_ORIGIN}" in deck_csp
    deck_nonce = deck_csp.split("'nonce-")[1].split("'")[0]
    assert f'nonce="{deck_nonce}"' in deck_body.decode("utf-8")

    # Step 7: Bump visibility epoch and verify instant invalidation
    conn = dbm.connect(db_path)
    conn.execute("UPDATE themes SET visibility_epoch = 2 WHERE id = 5")
    conn.commit()
    conn.close()

    revoked_status, _, _ = await _call_asgi(
        full_app,
        "/render/doc/100",
        host=UC_HOST,
        query=f"token={token}",
        headers=[("Origin", "null")],
    )
    assert revoked_status == 403
