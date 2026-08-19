"""Tests for Claire Bible magic barrier favicon and webmanifest endpoints."""

from __future__ import annotations

from pathlib import Path
from typing import Any
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
    from claire.graphview import GRAPH_HTML, _SHARED_HTML
    for html in (GRAPH_HTML, _SHARED_HTML):
        assert 'name="application-name" content="Claire Bible"' in html
        assert 'name="apple-mobile-web-app-title" content="Claire Bible"' in html
        assert 'rel="icon"' in html
        assert 'href="/favicon.svg"' in html
        assert 'href="/icon?p=android-chrome-192x192.png"' in html
        assert 'href="/favicon.ico"' in html
        assert 'href="/apple-touch-icon.png"' in html
        assert 'href="/manifest.json"' in html
