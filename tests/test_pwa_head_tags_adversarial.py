"""Adversarial challenge test suite for PWA head tags, template injection, and credential form isolation.

Empirically tests:
1. GET / in network mode (explore_accounts.html): PWA manifest and pwa-register.js present.
2. GET /login: PWA manifest present, strictly NO <script> tags (credential form isolation).
3. GET /setup: PWA manifest present, strictly NO <script> tags (credential form isolation).
4. GET / in single mode: PWA manifest and pwa-register.js present in workspace HTML.
5. GET /static/js/pwa-register.js: 200 OK, JavaScript MIME type, syntax and runtime safety.
6. console_shell and share templates: PWA manifest and pwa-register.js present.
7. inject_favicon_head_tags edge cases: uppercase tags, fallback, placeholder replacement, script toggle.
"""

from __future__ import annotations

import re
import subprocess
import time
from pathlib import Path
from typing import Any

import pytest
from starlette.testclient import TestClient

from claire.api import server
from claire.api.accounts_routes.common import render_template
from claire.config import Settings
from claire.presentation.favicon import (
    FAVICON_PLACEHOLDER,
    PWA_REGISTER_SCRIPT_PATH,
    inject_favicon_head_tags,
    render_favicon_head_tags,
)
from claire.store import accounts as accounts_store
from claire.store import auth_db
from claire.store import db as dbm


@pytest.fixture
def base_settings(tmp_path: Path) -> Settings:
    return Settings(
        CLAIRE_DB_PATH=str(tmp_path / "claire.db"),
        CLAIRE_VAULT_PATH=str(tmp_path / "vault"),
        CLAIRE_PUBLIC_URL="http://127.0.0.1:8000",
        CLAIRE_ENVIRONMENT="development",
    )


def test_network_mode_explore_accounts_pwa_head_tags(tmp_path: Path) -> None:
    """Check GET / in network mode (explore_accounts.html).

    Verifies <link rel="manifest"> and <script src="/static/js/pwa-register.js" defer></script>.
    """
    net_db = tmp_path / "net_claire.db"
    conn = dbm.connect(net_db)
    dbm.init_db(conn)
    conn.execute(
        "UPDATE accounts SET username='adminuser', status='active', activated_at=?, profile_public=1 WHERE id=1",
        (time.time(),),
    )
    store = accounts_store.AccountStore(conn)
    store.set_setting("mode", "network", updated_by=1)
    conn.commit()
    conn.close()

    settings = Settings(
        CLAIRE_DB_PATH=str(net_db),
        CLAIRE_VAULT_PATH=str(tmp_path / "vault"),
        CLAIRE_PUBLIC_URL="http://127.0.0.1:8000",
        CLAIRE_ENVIRONMENT="development",
    )
    app = server.create_app(settings)
    client = TestClient(app, base_url="http://127.0.0.1:8000")

    # 1. GET / in network mode -> renders explore_accounts.html
    resp = client.get("/", headers={"Accept": "text/html"})
    assert resp.status_code == 200
    assert "text/html" in resp.headers.get("content-type", "")
    html = resp.text

    # Manifest and PWA register script checks
    assert '<link rel="manifest" href="/manifest.json"/>' in html
    assert '<script src="/static/js/pwa-register.js" defer></script>' in html

    # Mobile capable tags and theme color
    assert '<meta name="mobile-web-app-capable" content="yes"/>' in html
    assert '<meta name="apple-mobile-web-app-capable" content="yes"/>' in html
    assert '<meta name="theme-color" content="#0e1116"/>' in html
    assert '<link rel="apple-touch-icon" sizes="180x180" href="/apple-touch-icon.png"/>' in html

    # 2. GET /explore/accounts directly
    resp_explore = client.get("/explore/accounts", headers={"Accept": "text/html"})
    assert resp_explore.status_code == 200
    assert '<link rel="manifest" href="/manifest.json"/>' in resp_explore.text
    assert '<script src="/static/js/pwa-register.js" defer></script>' in resp_explore.text

    # 3. HEAD / in network mode
    head_resp = client.head("/")
    assert head_resp.status_code == 200
    assert "text/html" in head_resp.headers.get("content-type", "")


def test_login_page_pwa_head_tags_and_strict_script_isolation(base_settings: Settings, tmp_path: Path) -> None:
    """Check GET /login and POST /login.

    Verifies <link rel="manifest"> is present, but <script> tags are STRICTLY ABSENT.
    Preserves credential form isolation.
    """
    app = server.create_app(base_settings)
    client = TestClient(app, base_url="http://127.0.0.1:8000")

    # 1. GET /login
    resp = client.get("/login")
    assert resp.status_code == 200
    assert "text/html" in resp.headers.get("content-type", "")
    html = resp.text

    # PWA head tags MUST be present
    assert '<link rel="manifest" href="/manifest.json"/>' in html
    assert '<meta name="mobile-web-app-capable" content="yes"/>' in html
    assert '<meta name="apple-mobile-web-app-capable" content="yes"/>' in html
    assert '<meta name="apple-mobile-web-app-status-bar-style" content="black-translucent"/>' in html
    assert '<meta name="theme-color" content="#0e1116"/>' in html
    assert '<link rel="apple-touch-icon" sizes="180x180" href="/apple-touch-icon.png"/>' in html

    # STRICT SCRIPT ISOLATION: No <script> tags whatsoever
    assert re.search(r"<\s*script", html, re.IGNORECASE) is None, "Found script tag in /login!"
    assert "/static/js/pwa-register.js" not in html
    assert "onclick=" not in html and "onsubmit=" not in html and "onload=" not in html

    # Credential page security headers
    assert resp.headers.get("referrer-policy") == "same-origin"
    assert "no-store" in resp.headers.get("cache-control", "")

    # 2. POST /login failure (re-rendered login page with error)
    resp_post = client.post(
        "/login",
        data={"username": "nonexistent", "password": "wrongpassword"},
        headers={"Origin": "http://127.0.0.1:8000"},
    )
    assert resp_post.status_code in (200, 400, 401)
    post_html = resp_post.text
    # PWA manifest must still be present on re-rendered error view
    assert '<link rel="manifest" href="/manifest.json"/>' in post_html
    # Strictly script-free
    assert re.search(r"<\s*script", post_html, re.IGNORECASE) is None
    assert "/static/js/pwa-register.js" not in post_html
    assert resp_post.headers.get("referrer-policy") == "same-origin"

    # 3. GET /login with reflected query params or XSS attempt
    resp_xss = client.get('/login?username=<script>alert(1)</script>')
    assert resp_xss.status_code == 200
    assert "<script>alert(1)</script>" not in resp_xss.text
    assert re.search(r"<\s*script", resp_xss.text, re.IGNORECASE) is None


def test_setup_page_pwa_head_tags_and_strict_script_isolation(tmp_path: Path) -> None:
    """Check GET /setup and POST /setup.

    Verifies <link rel="manifest"> is present, but <script> tags are STRICTLY ABSENT.
    Preserves credential form isolation.
    """
    db_file = tmp_path / "claire.db"
    conn = dbm.connect(db_file)
    dbm.init_db(conn)
    # Ensure account 1 is in setup_required status
    conn.execute("UPDATE accounts SET status='setup_required' WHERE id=1")
    conn.commit()

    # Generate admin session for setup
    session_token = accounts_store.create_account_session(conn, account_id=accounts_store.ADMIN_ACCOUNT_ID)
    conn.close()

    settings = Settings(
        CLAIRE_DB_PATH=str(db_file),
        CLAIRE_VAULT_PATH=str(tmp_path / "vault"),
        CLAIRE_PUBLIC_URL="http://127.0.0.1:8000",
        CLAIRE_ENVIRONMENT="development",
    )
    app = server.create_app(settings)
    client = TestClient(app, base_url="http://127.0.0.1:8000")

    # 1. Unauthenticated GET /setup -> must return 404 (stealth isolation)
    assert client.get("/setup").status_code == 404

    # 2. Authenticated GET /setup with admin session in setup_required
    resp = client.get("/setup", headers={"Cookie": f"claire_session={session_token}"})
    assert resp.status_code == 200
    assert "text/html" in resp.headers.get("content-type", "")
    html = resp.text

    # PWA head tags MUST be present
    assert '<link rel="manifest" href="/manifest.json"/>' in html
    assert '<meta name="mobile-web-app-capable" content="yes"/>' in html
    assert '<meta name="apple-mobile-web-app-capable" content="yes"/>' in html
    assert '<meta name="apple-mobile-web-app-status-bar-style" content="black-translucent"/>' in html
    assert '<meta name="theme-color" content="#0e1116"/>' in html
    assert '<link rel="apple-touch-icon" sizes="180x180" href="/apple-touch-icon.png"/>' in html

    # STRICT SCRIPT ISOLATION: No <script> tags whatsoever
    assert re.search(r"<\s*script", html, re.IGNORECASE) is None, "Found script tag in /setup!"
    assert "/static/js/pwa-register.js" not in html
    assert "onclick=" not in html and "onsubmit=" not in html and "onload=" not in html

    # Credential page security headers
    assert resp.headers.get("referrer-policy") == "same-origin"
    assert "no-store" in resp.headers.get("cache-control", "")

    # 3. POST /setup failure (validation error, e.g. mismatched password)
    resp_post = client.post(
        "/setup",
        data={
            "username": "admin",
            "display_name": "Admin",
            "password": "pass",
            "password_confirm": "mismatch",
            "ack": "on",
        },
        headers={
            "Cookie": f"claire_session={session_token}",
            "Origin": "http://127.0.0.1:8000",
        },
    )
    assert resp_post.status_code in (200, 400)
    post_html = resp_post.text
    # PWA manifest must still be present
    assert '<link rel="manifest" href="/manifest.json"/>' in post_html
    # Strictly script-free
    assert re.search(r"<\s*script", post_html, re.IGNORECASE) is None
    assert "/static/js/pwa-register.js" not in post_html
    assert resp_post.headers.get("referrer-policy") == "same-origin"


def test_single_mode_workspace_pwa_head_tags(base_settings: Settings) -> None:
    """Check GET / in single mode.

    Verifies PWA head tags and register script in workspace HTML.
    """
    app = server.create_app(base_settings)
    client = TestClient(app, base_url="http://127.0.0.1:8000")

    resp = client.get("/")
    assert resp.status_code == 200
    assert "text/html" in resp.headers.get("content-type", "")
    html = resp.text

    assert '<link rel="manifest" href="/manifest.json"/>' in html
    assert '<script src="/static/js/pwa-register.js" defer></script>' in html
    assert '<meta name="mobile-web-app-capable" content="yes"/>' in html
    assert '<meta name="apple-mobile-web-app-capable" content="yes"/>' in html
    assert '<meta name="theme-color" content="#0e1116"/>' in html
    assert '<link rel="apple-touch-icon" sizes="180x180" href="/apple-touch-icon.png"/>' in html


def test_pwa_register_script_route_and_syntax(base_settings: Settings) -> None:
    """Check GET /static/js/pwa-register.js.

    Verifies 200 OK, JavaScript content type, valid syntax and registration semantics.
    """
    app = server.create_app(base_settings)
    client = TestClient(app, base_url="http://127.0.0.1:8000")

    # 1. GET request
    resp = client.get("/static/js/pwa-register.js")
    assert resp.status_code == 200
    content_type = resp.headers.get("content-type", "")
    assert "javascript" in content_type
    script_text = resp.text
    assert len(script_text) > 0

    # 2. HEAD request
    head_resp = client.head("/static/js/pwa-register.js")
    assert head_resp.status_code == 200
    assert "javascript" in head_resp.headers.get("content-type", "")

    # 3. Node.js syntax check
    js_path = Path(__file__).resolve().parent.parent / "src" / "claire" / "static" / "js" / "pwa-register.js"
    assert js_path.is_file()
    check = subprocess.run(["node", "-c", str(js_path)], capture_output=True, text=True)
    assert check.returncode == 0, f"pwa-register.js syntax error: {check.stderr}"

    # 4. Semantic inspection
    assert "'use strict'" in script_text or '"use strict"' in script_text
    assert "serviceWorker" in script_text
    assert "register('/sw.js', { scope: '/' })" in script_text or 'register("/sw.js", { scope: "/" })' in script_text
    assert ".catch(" in script_text
    assert "document.readyState === 'complete'" in script_text
    assert "addEventListener('load'" in script_text or 'addEventListener("load"' in script_text


def test_console_shell_and_share_pwa_head_tags() -> None:
    """Verify console_shell and share templates include PWA manifest and pwa-register.js."""
    # Console shell via render_template
    rendered_console = render_template(
        "console_shell.html",
        text={
            "page_title": "관리 콘솔",
            "storage_key": "1",
            "active_area": "admin",
            "active_section": "instance",
            "graph_href": "/",
            "syndication_href": "/syndication",
            "scope_badge": "",
            "section_title": "관리 콘솔",
            "section_badge": "",
            "section_desc": "",
        },
        markup={
            "theme_selector_html": "",
            "sidebar_nav_html": "",
            "section_body_html": "<div>console content</div>",
            "bootstrap_json": "{}",
            "extra_scripts_html": "",
            "admin_scripts_html": "",
        },
    )
    console_html = rendered_console.body.decode("utf-8")
    assert '<link rel="manifest" href="/manifest.json"/>' in console_html
    assert '<script src="/static/js/pwa-register.js" defer></script>' in console_html

    # Share template in graphview
    from claire.graphview import _SHARED_HTML
    assert '<link rel="manifest" href="/manifest.json"/>' in _SHARED_HTML
    assert '<script src="/static/js/pwa-register.js" defer></script>' in _SHARED_HTML


def test_inject_favicon_head_tags_adversarial_variations() -> None:
    """Stress test inject_favicon_head_tags against diverse HTML structures."""
    # Case 1: Uppercase </HEAD>
    upper_html = "<html><HEAD><title>Upper</title></HEAD><body>Content</body></html>"
    injected_upper = inject_favicon_head_tags(upper_html)
    assert '<link rel="manifest" href="/manifest.json"/>' in injected_upper
    assert '<script src="/static/js/pwa-register.js" defer></script>' in injected_upper
    assert injected_upper.index('<link rel="manifest"') < injected_upper.index("</HEAD>")

    # Case 2: No <head> tag at all (fallback prepend)
    bare_html = "<div>No head fragment</div>"
    injected_bare = inject_favicon_head_tags(bare_html)
    assert '<link rel="manifest" href="/manifest.json"/>' in injected_bare
    assert injected_bare.endswith(bare_html)

    # Case 3: Explicit placeholder replacement
    placeholder_html = f"<!doctype html><html><head><meta charset='utf-8'/>{FAVICON_PLACEHOLDER}</head></html>"
    injected_ph = inject_favicon_head_tags(placeholder_html, include_pwa_register=False)
    assert FAVICON_PLACEHOLDER not in injected_ph
    assert '<link rel="manifest" href="/manifest.json"/>' in injected_ph
    assert '<script src="/static/js/pwa-register.js"' not in injected_ph

    # Case 4: Base URL prefixing
    custom_injected = render_favicon_head_tags(base_url="https://pwa.claire.test", include_pwa_register=True)
    assert '<link rel="manifest" href="https://pwa.claire.test/manifest.json"/>' in custom_injected
    assert '<script src="https://pwa.claire.test/static/js/pwa-register.js" defer></script>' in custom_injected
