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
        vault_dir=tmp_path / "vault",
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
    resp = client.get("/manifest.json")
    assert resp.status_code == 200
    data = resp.json()
    assert data["name"] == "Claire Bible"
    assert data["short_name"] == "Claire Bible"
    assert len(data["icons"]) >= 9
    for icon_entry in data["icons"]:
        icon_url = icon_entry["src"]
        icon_resp = client.get(icon_url)
        assert icon_resp.status_code == 200, f"Failed to fetch {icon_url}"
        assert "image/png" in icon_resp.headers.get("content-type", "")

    resp_webmanifest = client.get("/site.webmanifest")
    assert resp_webmanifest.status_code == 200
    webmanifest_data = resp_webmanifest.json()
    assert webmanifest_data["name"] == "Claire Bible"
    assert webmanifest_data["short_name"] == "Claire Bible"


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
    assert rendered == FAVICON_TAGS_HTML

    # Test prefix support
    custom_prefix = render_favicon_head_tags(base_url="https://cdn.example.com/")
    assert 'href="https://cdn.example.com/favicon.svg"' in custom_prefix

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
