"""Tests for Claire Bible magic barrier favicon and webmanifest endpoints."""

from __future__ import annotations

from pathlib import Path

import pytest
from starlette.testclient import TestClient

from claire.api import server
from claire.config import Settings


@pytest.fixture
def client(tmp_path: Path) -> TestClient:
    settings = Settings(
        environment="development",
        public_url="http://127.0.0.1:8000",
        inject_host="127.0.0.1",
        inject_port=8000,
        inject_token="x" * 32,
        db_file=tmp_path / "claire.db",
        data_dir=tmp_path / "data",
    )
    app = server.create_app(settings)
    return TestClient(app, base_url="http://127.0.0.1:8000")


def test_favicon_ico(client: TestClient) -> None:
    resp = client.get("/favicon.ico")
    assert resp.status_code == 200
    assert "image/x-icon" in resp.headers.get("content-type", "")
    assert len(resp.content) > 0


def test_favicon_svg(client: TestClient) -> None:
    resp = client.get("/favicon.svg")
    assert resp.status_code == 200
    assert "image/svg+xml" in resp.headers.get("content-type", "")
    assert b"<svg" in resp.content


def test_apple_touch_icon(client: TestClient) -> None:
    resp = client.get("/apple-touch-icon.png")
    assert resp.status_code == 200
    assert "image/png" in resp.headers.get("content-type", "")
    assert resp.content[:8] == b"\x89PNG\r\n\x1a\n"

    resp_pre = client.get("/apple-touch-icon-precomposed.png")
    assert resp_pre.status_code == 200
    assert "image/png" in resp_pre.headers.get("content-type", "")


def test_manifest_json(client: TestClient) -> None:
    for endpoint in ("/manifest.json", "/site.webmanifest"):
        resp = client.get(endpoint)
        assert resp.status_code == 200
        assert "application/manifest+json" in resp.headers.get("content-type", "")
        data = resp.json()
        assert data["id"] == "/"
        assert data["scope"] == "/"
        assert data["start_url"] == "/?pwa=1"
        assert data["display"] == "standalone"
        assert data["orientation"] == "any"
        assert "Claire" in data["name"]
        assert "Claire" in data["short_name"]
        assert len(data["icons"]) >= 9

        icons_192 = [i for i in data["icons"] if i.get("sizes") == "192x192"]
        assert len(icons_192) >= 2
        purposes_192 = {i.get("purpose") for i in icons_192}
        assert "any" in purposes_192
        assert "maskable" in purposes_192

        icons_512 = [i for i in data["icons"] if i.get("sizes") == "512x512"]
        assert len(icons_512) >= 2
        purposes_512 = {i.get("purpose") for i in icons_512}
        assert "any" in purposes_512
        assert "maskable" in purposes_512

        for icon_entry in data["icons"]:
            icon_url = icon_entry["src"]
            icon_resp = client.get(icon_url)
            assert icon_resp.status_code == 200, f"Failed to fetch {icon_url}"
            assert "image/png" in icon_resp.headers.get("content-type", "")


def test_browserconfig_xml(client: TestClient) -> None:
    resp = client.get("/browserconfig.xml")
    assert resp.status_code == 200
    assert "xml" in resp.headers.get("content-type", "")
    assert 'src="/icon?p=mstile-' in resp.text


def test_icon_query_endpoint(client: TestClient) -> None:
    # Query android-chrome-192x192.png
    resp = client.get("/icon?p=android-chrome-192x192.png")
    assert resp.status_code == 200
    assert "image/png" in resp.headers.get("content-type", "")

    # Query apple-touch-icon-1024x1024.png
    resp_lg = client.get("/icon?p=apple-touch-icon-1024x1024.png")
    assert resp_lg.status_code == 200
    assert "image/png" in resp_lg.headers.get("content-type", "")

    # Non-existent or invalid name
    resp_404 = client.get("/icon?p=nonexistent.png")
    assert resp_404.status_code == 404

    resp_invalid = client.get("/icon?p=../../etc/passwd")
    assert resp_invalid.status_code == 404


def test_graph_ui_contains_favicon_tags(client: TestClient) -> None:
    # Even without auth, graph UI template has the head tags
    from claire.graphview import _SHARED_HTML, GRAPH_HTML
    for html in (GRAPH_HTML, _SHARED_HTML):
        assert 'name="application-name" content="Claire Bible"' in html
        assert 'name="apple-mobile-web-app-title" content="Claire Bible"' in html
        assert 'rel="icon"' in html
        assert 'href="/favicon.svg"' in html
        assert 'href="/icon?p=android-chrome-192x192.png"' in html
        assert 'href="/favicon.ico"' in html
        assert 'href="/apple-touch-icon.png"' in html
        assert 'href="/manifest.json"' in html
        assert 'src="/static/js/pwa-register.js"' in html


def test_shared_favicon_module_interface() -> None:
    from claire.presentation.favicon import (
        FAVICON_PLACEHOLDER,
        FAVICON_SVG_PATH,
        FAVICON_TAGS_HTML,
        get_scalar_favicon_config,
        inject_favicon_head_tags,
        render_favicon_head_tags,
    )

    rendered = render_favicon_head_tags()
    assert FAVICON_SVG_PATH in rendered
    assert "/manifest.json" in rendered
    assert "/icon?p=android-chrome-192x192.png" in rendered
    assert "/favicon.ico" in rendered
    assert "/static/js/pwa-register.js" in rendered
    assert rendered == FAVICON_TAGS_HTML

    # Test prefix support
    custom_prefix = render_favicon_head_tags(base_url="https://cdn.example.com/")
    assert 'href="https://cdn.example.com/favicon.svg"' in custom_prefix
    assert 'src="https://cdn.example.com/static/js/pwa-register.js"' in custom_prefix

    # Test injection with placeholder
    html_with_placeholder = f"<html><head>{FAVICON_PLACEHOLDER}</head><body></body></html>"
    injected_placeholder = inject_favicon_head_tags(html_with_placeholder)
    assert FAVICON_PLACEHOLDER not in injected_placeholder
    assert 'href="/favicon.svg"' in injected_placeholder

    # Test injection before </head>
    html_raw = "<html><head><title>Test</title></head><body></body></html>"
    injected_raw = inject_favicon_head_tags(html_raw)
    assert 'href="/favicon.svg"' in injected_raw
    assert injected_raw.index('href="/favicon.svg"') < injected_raw.index("</head>")

    # Test scalar config
    scalar_cfg = get_scalar_favicon_config()
    assert scalar_cfg == {"favicon": "/favicon.svg"}


def test_reference_docs_favicon_integration(client: TestClient) -> None:
    import json

    resp = client.get("/reference")
    assert resp.status_code == 200
    html = resp.text

    # Verify head tags
    assert 'href="/favicon.svg"' in html
    assert 'href="/favicon.ico"' in html
    assert 'href="/icon?p=android-chrome-192x192.png"' in html
    assert 'href="/manifest.json"' in html

    # Verify Scalar data-configuration includes favicon
    assert 'id="api-reference"' in html
    # Extract data-configuration JSON
    marker = 'data-configuration=\''
    start = html.find(marker)
    assert start != -1, "data-configuration not found in /reference"
    start += len(marker)
    end = html.find("'>", start)
    config_str = html[start:end]
    config = json.loads(config_str)
    assert config.get("favicon") == "/favicon.svg"


def test_oauth_and_presentation_views_favicon_integration() -> None:
    from claire.api.oauth import _render_authorize_page
    from claire.presentation.hud import inject_hud_toolbar
    from claire.presentation.views import render_unready_presentation_page

    # OAuth login template
    login_html = _render_authorize_page(
        client_name="Test MCP",
        client_id="cid-1",
        redirect_uri="http://example.com/callback",
        state="s1",
        code_challenge="cc",
        code_challenge_method="S256",
        scope="read",
        has_valid_cookie=False,
    )
    assert 'href="/favicon.svg"' in login_html
    assert 'href="/favicon.ico"' in login_html
    assert 'href="/manifest.json"' in login_html

    # Presentation unready page
    unready_html = render_unready_presentation_page(title="Slide Deck", status="composing")
    assert 'href="/favicon.svg"' in unready_html
    assert 'href="/favicon.ico"' in unready_html
    assert 'href="/manifest.json"' in unready_html

    # Reveal.js presentation HUD injection
    mock_pres_html = "<html><head><title>Pres</title></head><body><div class='reveal'></div></body></html>"
    pres_hud_html = inject_hud_toolbar(mock_pres_html)
    assert 'href="/favicon.svg"' in pres_hud_html


def test_service_worker_route(client: TestClient) -> None:
    # GET /sw.js
    resp = client.get("/sw.js")
    assert resp.status_code == 200
    assert "application/javascript" in resp.headers.get("content-type", "")
    assert resp.headers.get("service-worker-allowed") == "/"
    assert "no-cache" in resp.headers.get("cache-control", "")

    body = resp.text
    assert "skipWaiting" in body
    assert "claim" in body
    assert "respondWith" in body

    # HEAD /sw.js
    head_resp = client.head("/sw.js")
    assert head_resp.status_code == 200
    assert "application/javascript" in head_resp.headers.get("content-type", "")
    assert head_resp.headers.get("service-worker-allowed") == "/"


def test_pwa_register_script_route(client: TestClient) -> None:
    resp = client.get("/static/js/pwa-register.js")
    assert resp.status_code == 200
    assert "javascript" in resp.headers.get("content-type", "")
    body = resp.text
    assert "serviceWorker" in body
    assert "register('/sw.js'" in body or 'register("/sw.js"' in body
    assert "scope" in body


def test_template_entry_points_pwa_head_tags(client: TestClient, tmp_path: Path) -> None:
    import time
    from claire.api.accounts_routes.common import render_template
    from claire.store import db as dbm

    # Login template (script-free for credential isolation, includes full PWA head tags)
    resp_login = client.get("/login")
    assert resp_login.status_code == 200
    assert 'href="/manifest.json"' in resp_login.text
    assert 'name="apple-mobile-web-app-capable"' in resp_login.text
    assert 'name="theme-color"' in resp_login.text

    # Setup template (script-free for credential isolation, includes full PWA head tags)
    rendered_setup = render_template(
        "setup.html",
        text={
            "username": "",
            "display_name": "",
            "min_password_length": 8,
        },
        markup={"errors": ""},
    )
    setup_html = rendered_setup.body.decode("utf-8")
    assert 'href="/manifest.json"' in setup_html
    assert 'name="apple-mobile-web-app-capable"' in setup_html

    # Console shell template
    rendered_console = render_template(
        "console_shell.html",
        text={
            "page_title": "콘솔",
            "storage_key": "1",
            "active_area": "admin",
            "active_section": "instance",
            "graph_href": "/",
            "syndication_href": "/syndication",
            "scope_badge": "",
            "section_title": "콘솔",
            "section_badge": "",
            "section_desc": "",
        },
        markup={
            "theme_selector_html": "",
            "sidebar_nav_html": "",
            "section_body_html": "<div>test</div>",
            "bootstrap_json": "{}",
            "extra_scripts_html": "",
            "admin_scripts_html": "",
        },
    )
    console_html = rendered_console.body.decode("utf-8")
    assert 'href="/manifest.json"' in console_html
    assert '/static/js/pwa-register.js' in console_html

    # Explore accounts (public directory in network mode)
    from claire.store import accounts as accounts_store
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
    net_settings = Settings(
        CLAIRE_DB_PATH=str(net_db),
        CLAIRE_VAULT_PATH=str(tmp_path / "vault"),
        CLAIRE_PUBLIC_URL="http://127.0.0.1:8000",
        CLAIRE_ENVIRONMENT="development",
    )
    net_app = server.create_app(net_settings)
    net_client = TestClient(net_app, base_url="http://127.0.0.1:8000")
    resp_explore = net_client.get("/explore/accounts", headers={"Accept": "text/html"})
    assert resp_explore.status_code == 200
    assert 'href="/manifest.json"' in resp_explore.text
    assert '/static/js/pwa-register.js' in resp_explore.text

    # Workspace index template
    resp_index = client.get("/")
    assert resp_index.status_code == 200
    assert 'href="/manifest.json"' in resp_index.text
    assert '/static/js/pwa-register.js' in resp_index.text


