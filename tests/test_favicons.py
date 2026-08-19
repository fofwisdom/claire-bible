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
    assert len(data["icons"]) >= 9

    resp_webmanifest = client.get("/site.webmanifest")
    assert resp_webmanifest.status_code == 200


def test_browserconfig_xml(client: TestClient) -> None:
    resp = client.get("/browserconfig.xml")
    assert resp.status_code == 200
    assert "xml" in resp.headers.get("content-type", "")


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
    from claire.graphview import GRAPH_HTML
    assert 'rel="icon"' in GRAPH_HTML
    assert 'href="/favicon.svg"' in GRAPH_HTML
    assert 'href="/favicon.ico"' in GRAPH_HTML
    assert 'href="/apple-touch-icon.png"' in GRAPH_HTML
    assert 'href="/manifest.json"' in GRAPH_HTML
