"""Unit tests for public site crawling control and page indexing (robots.txt and sitemap.xml).

Tests focus strictly on:
- Database I/O operations (list_public_sitemap_documents, token generation, hidden document filtering)
- Interface contracts between server routes and external crawler clients
- Schema.org JSON-LD and HTML meta robots generation
"""

from __future__ import annotations

import json
import sqlite3
import time
import xml.etree.ElementTree as ET
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
from starlette.testclient import TestClient

from claire.api import server
from claire.config import Settings
from claire.graphview import render_open_graph_tags, shared_html
from claire.store import db as dbm


@pytest.fixture
def tmp_db(tmp_path: Path) -> Path:
    db_file = tmp_path / "test.db"
    conn = dbm.connect(db_file)
    dbm.init_db(conn)
    conn.close()
    return db_file


@pytest.fixture
def app_settings(tmp_path: Path, tmp_db: Path) -> Settings:
    server._sitemap_cache.clear()
    return Settings(
        _env_file=None,
        db_path=str(tmp_db),
        data_path=str(tmp_path),
        vault_path=str(tmp_path / "vault"),
        environment="production",
        public_url="https://claire.example.com",
        fqdn="claire.example.com",
        inject_token="owner-token-secret-12345678901234",
        readonly_token="readonly-token-secret-123456789012",
        anonymous_readonly=True,
        multi_theme=False,
        robots_policy="public",
        block_ai_crawlers=True,
        sitemap_enabled=True,
        sitemap_cache_ttl=900,
        site_name="Claire Bible Test Portal",
    )


class StubIngestService:
    def __init__(self) -> None:
        self.provider = SimpleNamespace(name="stub")


def test_list_public_sitemap_documents_io_and_filtering(tmp_db: Path) -> None:
    """Test database I/O: ensures non-hidden documents receive shares while hidden documents are filtered."""
    conn = dbm.connect(tmp_db)
    now = time.time()

    # 1. Document with no prior share (hidden=0) -> should get auto-created share
    conn.execute(
        "INSERT INTO documents (id, title, fetched_at, hidden) VALUES (?, ?, ?, ?)",
        ("doc-1", "Public Document 1", now - 100, 0),
    )

    # 2. Hidden document (hidden=1) -> must never appear in sitemap
    conn.execute(
        "INSERT INTO documents (id, title, fetched_at, hidden) VALUES (?, ?, ?, ?)",
        ("doc-hidden", "Secret Document", now - 200, 1),
    )

    # 3. Document with existing active share (hidden=0) -> should reuse existing share token
    conn.execute(
        "INSERT INTO documents (id, title, fetched_at, hidden) VALUES (?, ?, ?, ?)",
        ("doc-2", "Public Document 2", now - 50, 0),
    )
    conn.execute(
        "INSERT INTO doc_shares (token, document_id, created_at, expires_at) VALUES (?, ?, ?, ?)",
        ("token-existing-12", "doc-2", now - 40, None),
    )

    # 4. Document with expired share (hidden=0) -> should get newly refreshed unexpired share
    conn.execute(
        "INSERT INTO documents (id, title, fetched_at, hidden) VALUES (?, ?, ?, ?)",
        ("doc-3", "Public Document 3", now - 300, 0),
    )
    conn.execute(
        "INSERT INTO doc_shares (token, document_id, created_at, expires_at) VALUES (?, ?, ?, ?)",
        ("token-expired-old", "doc-3", now - 500, now - 10),
    )
    conn.commit()

    docs = dbm.list_public_sitemap_documents(conn, ensure_shares=True)
    conn.close()

    doc_ids = {d["document_id"] for d in docs}
    assert "doc-hidden" not in doc_ids
    assert "doc-1" in doc_ids
    assert "doc-2" in doc_ids
    assert "doc-3" in doc_ids

    # doc-2 must reuse its existing token
    doc_2_entry = next(d for d in docs if d["document_id"] == "doc-2")
    assert doc_2_entry["token"] == "token-existing-12"

    # doc-3 must have a fresh valid token, not the expired one
    doc_3_entry = next(d for d in docs if d["document_id"] == "doc-3")
    assert doc_3_entry["token"] != "token-expired-old"
    assert len(doc_3_entry["token"]) == 16


def test_robots_txt_public_rules_and_ai_blocking(app_settings: Settings) -> None:
    """Test robots.txt HTTP interface: Allow rules, Disallow rules, AI agent blocking, and Sitemap directive."""
    app = server.create_app(app_settings, StubIngestService())
    with TestClient(app, base_url="https://claire.example.com") as client:
        resp = client.get("/robots.txt", headers={"Host": "claire.example.com"})
        assert resp.status_code == 200
        assert "text/plain" in resp.headers["content-type"]
        text = resp.text

        # Allow directives
        assert "Allow: /$" in text
        assert "Allow: /p?" in text
        assert "Allow: /static/" in text
        assert "Allow: /images/" in text

        # Disallow high-cost and mutate endpoints
        assert "Disallow: /search" in text
        assert "Disallow: /ingest" in text
        assert "Disallow: /oauth/" in text
        assert "Disallow: /document/presentation/generate" in text

        # AI scraper bot blocks (default True)
        assert "User-agent: GPTBot" in text
        assert "User-agent: ClaudeBot" in text
        assert "User-agent: CCBot" in text
        assert "User-agent: Bytespider" in text
        assert "Disallow: /" in text

        # Sitemap directive
        assert "Sitemap: https://claire.example.com/sitemap.xml" in text


def test_robots_txt_private_mode_and_disallow_all(app_settings: Settings) -> None:
    """Test robots.txt fail-closed mode when anonymous readonly is disabled or policy is disallow_all."""
    # 1. robots_policy = "disallow_all"
    app_settings.robots_policy = "disallow_all"
    app = server.create_app(app_settings, StubIngestService())
    with TestClient(app, base_url="https://claire.example.com") as client:
        resp = client.get("/robots.txt", headers={"Host": "claire.example.com"})
        assert resp.status_code == 200
        assert resp.text.strip() == "User-agent: *\nDisallow: /"

    # 2. anonymous_readonly = False
    app_settings.robots_policy = "public"
    app_settings.anonymous_readonly = False
    app2 = server.create_app(app_settings, StubIngestService())
    with TestClient(app2, base_url="https://claire.example.com") as client2:
        resp2 = client2.get("/robots.txt", headers={"Host": "claire.example.com"})
        assert resp2.status_code == 200
        assert resp2.text.strip() == "User-agent: *\nDisallow: /"


def test_sitemap_xml_generation_and_etag_caching(app_settings: Settings, tmp_db: Path) -> None:
    """Test sitemap.xml structure, XML namespace, ETag generation, and 304 Not Modified."""
    conn = dbm.connect(tmp_db)
    conn.execute(
        "INSERT INTO documents (id, title, fetched_at, hidden) VALUES (?, ?, ?, ?)",
        ("doc-100", "Architecture Guide", 1729000000.0, 0),
    )
    conn.commit()
    conn.close()

    app = server.create_app(app_settings, StubIngestService())
    with TestClient(app, base_url="https://claire.example.com") as client:
        resp = client.get("/sitemap.xml", headers={"Host": "claire.example.com"})
        assert resp.status_code == 200
        assert "application/xml" in resp.headers["content-type"]
        etag = resp.headers.get("ETag")
        assert etag is not None

        # Parse XML
        root = ET.fromstring(resp.text)
        ns = {"ns": "http://www.sitemaps.org/schemas/sitemap/0.9"}
        urls = [loc.text for loc in root.findall(".//ns:loc", ns)]

        # Must contain root portal
        assert "https://claire.example.com/" in urls

        # Must contain document share URL
        doc_urls = [u for u in urls if "/p?s=" in u]
        assert len(doc_urls) == 1

        # Must NOT contain presentation URLs (decision 3: exclude presentations)
        pres_urls = [u for u in urls if "/p/presentation" in u]
        assert len(pres_urls) == 0

        # Conditional GET with ETag -> 304 Not Modified
        resp_cached = client.get(
            "/sitemap.xml",
            headers={"Host": "claire.example.com", "If-None-Match": etag},
        )
        assert resp_cached.status_code == 304


def test_sitemap_xml_disabled_and_private_returns_404(app_settings: Settings) -> None:
    """Test that sitemap.xml returns 404 when disabled or under private access."""
    app_settings.sitemap_enabled = False
    app = server.create_app(app_settings, StubIngestService())
    with TestClient(app, base_url="https://claire.example.com") as client:
        resp = client.get("/sitemap.xml", headers={"Host": "claire.example.com"})
        assert resp.status_code == 404


def test_seo_metadata_jsonld_and_semantic_fallback() -> None:
    """Test Schema.org JSON-LD generation and <noscript> semantic body fallback for search crawlers."""
    doc = {
        "id": "doc-alpha",
        "title": "Quantum Computing Fundamentals",
        "summary": "Introduction to quantum bits and entanglement algorithms.",
        "author": "Dr. Alice",
        "published_at": "2026-10-15T10:00:00Z",
        "fetched_at": 1729000000.0,
        "detail_html": "<p>Detailed quantum physics content.</p>",
    }

    # 1. Meta tags and JSON-LD
    og_snippet = render_open_graph_tags(doc, base_url="https://claire.example.com", share_token="share-alpha")
    assert '<meta name="robots" content="index, follow' in og_snippet
    assert '<link rel="canonical" href="https://claire.example.com/p?s=share-alpha"/>' in og_snippet
    assert '<script type="application/ld+json">' in og_snippet

    # Parse and validate JSON-LD payload
    start_tag = '<script type="application/ld+json">'
    end_tag = "</script>"
    json_str = og_snippet.split(start_tag, 1)[1].split(end_tag, 1)[0]
    json_data = json.loads(json_str)
    assert json_data["@context"] == "https://schema.org"
    assert json_data["@type"] == "TechArticle"
    assert json_data["headline"] == "Quantum Computing Fundamentals"
    assert json_data["author"]["name"] == "Dr. Alice"

    # 2. SSR Semantic Fallback in shared_html
    html = shared_html(doc, base_url="https://claire.example.com", share_token="share-alpha")
    assert "<noscript>" in html
    assert '<article class="cb-semantic-content"' in html
    assert "Quantum Computing Fundamentals" in html
    assert "Detailed quantum physics content." in html
