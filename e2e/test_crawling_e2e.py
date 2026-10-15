"""End-to-End (E2E) test for public site crawling control and page indexing.

Simulates the complete crawler and visitor journey:
1. Boot server in production mode with an isolated database.
2. Ingest a knowledge document.
3. Search crawler visits /robots.txt -> verifies allow/disallow directives, AI bot blocks, and Sitemap link.
4. Search crawler visits /sitemap.xml -> parses sitemap, extracts document share URL, verifies absence of presentation URLs.
5. Search crawler fetches /p?s={token} -> verifies <meta name="robots">, Schema.org JSON-LD, and <noscript> semantic body fallback.
6. Admin hides the document -> verifies subsequent /sitemap.xml removes the hidden document.
"""

from __future__ import annotations

import json
import time
import xml.etree.ElementTree as ET
from pathlib import Path
from types import SimpleNamespace

import pytest
from starlette.testclient import TestClient

from claire.api import server
from claire.config import Settings
from claire.store import db as dbm


@pytest.fixture
def e2e_env(tmp_path: Path):
    db_file = tmp_path / "claire_e2e.db"
    conn = dbm.connect(db_file)
    dbm.init_db(conn)
    conn.close()

    server._sitemap_cache.clear()
    settings = Settings(
        _env_file=None,
        db_path=str(db_file),
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
        site_name="Claire Bible E2E",
    )
    service = SimpleNamespace(provider=SimpleNamespace(name="stub"))
    app = server.create_app(settings, service)
    return settings, app, db_file


def test_public_site_crawling_and_indexing_e2e_journey(e2e_env) -> None:
    settings, app, db_file = e2e_env
    owner_headers = {"Authorization": f"Bearer {settings.inject_token}"}

    # Step 1: Ingest / seed a public document into the database
    conn = dbm.connect(db_file)
    now = time.time()
    conn.execute(
        """
        INSERT INTO documents (
            id, url, canonical_url, title, author, published_at, fetched_at,
            raw_text, source_type, content_hash, detail, detail_html, hidden
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            "doc-e2e-seo",
            "https://example.com/distributed-systems",
            "https://example.com/distributed-systems",
            "Distributed Consensus and Raft Protocol",
            "Leslie Lamport",
            "2026-10-15T08:00:00Z",
            now - 300,
            "Raft is a consensus algorithm designed as an alternative to Paxos.",
            "web",
            "hash-12345",
            "Comprehensive breakdown of Raft leader election and log replication.",
            "<p>Comprehensive breakdown of Raft leader election and log replication.</p>",
            0,
        ),
    )
    conn.commit()
    conn.close()

    with TestClient(app, base_url="https://claire.example.com") as client:
        # Step 2: Search crawler inspects /robots.txt
        resp_robots = client.get("/robots.txt", headers={"Host": "claire.example.com"})
        assert resp_robots.status_code == 200
        assert "text/plain" in resp_robots.headers["content-type"]
        robots_txt = resp_robots.text

        # Verify allowed knowledge paths
        assert "Allow: /$" in robots_txt
        assert "Allow: /p?" in robots_txt
        assert "Allow: /static/" in robots_txt

        # Verify search DoS and administrative route protections
        assert "Disallow: /search" in robots_txt
        assert "Disallow: /ingest" in robots_txt
        assert "Disallow: /oauth/" in robots_txt
        assert "Disallow: /mcp" in robots_txt

        # Verify AI scrapers are blocked by default
        assert "User-agent: GPTBot" in robots_txt
        assert "User-agent: ClaudeBot" in robots_txt
        assert "User-agent: CCBot" in robots_txt
        assert "Disallow: /" in robots_txt

        # Verify sitemap directive exists
        expected_sitemap_url = "https://claire.example.com/sitemap.xml"
        assert f"Sitemap: {expected_sitemap_url}" in robots_txt

        # Step 3: Search crawler fetches /sitemap.xml
        resp_sitemap = client.get("/sitemap.xml", headers={"Host": "claire.example.com"})
        assert resp_sitemap.status_code == 200
        assert "application/xml" in resp_sitemap.headers["content-type"]
        assert "ETag" in resp_sitemap.headers

        # Parse sitemap XML
        root = ET.fromstring(resp_sitemap.text)
        ns = {"ns": "http://www.sitemaps.org/schemas/sitemap/0.9"}
        url_elements = root.findall(".//ns:url", ns)
        assert len(url_elements) >= 2  # Root portal + doc-e2e-seo

        # Confirm root portal entry
        locs = [u.find("ns:loc", ns).text for u in url_elements]
        assert "https://claire.example.com/" in locs

        # Confirm document share entry exists
        doc_locs = [loc for loc in locs if "/p?s=" in loc]
        assert len(doc_locs) == 1
        doc_url = doc_locs[0]
        share_token = doc_url.split("?s=")[1]
        assert len(share_token) == 16

        # Confirm presentation URLs are strictly excluded (decision 3)
        assert not any("/p/presentation" in loc for loc in locs)

        # Step 4: Search crawler fetches the shared document page
        resp_doc = client.get(f"/p?s={share_token}", headers={"Host": "claire.example.com"})
        assert resp_doc.status_code == 200
        doc_html = resp_doc.text

        # Verify SEO Meta Robots tag
        assert '<meta name="robots" content="index, follow' in doc_html

        # Verify Canonical URL
        assert f'<link rel="canonical" href="{doc_url}"/>' in doc_html

        # Verify Schema.org JSON-LD
        assert '<script type="application/ld+json">' in doc_html
        json_ld_raw = doc_html.split('<script type="application/ld+json">')[1].split("</script>")[0]
        json_ld = json.loads(json_ld_raw)
        assert json_ld["@context"] == "https://schema.org"
        assert json_ld["@type"] == "TechArticle"
        assert json_ld["headline"] == "Distributed Consensus and Raft Protocol"
        assert json_ld["author"]["name"] == "Leslie Lamport"
        assert json_ld["mainEntityOfPage"]["@id"] == doc_url

        # Verify Non-JS Crawler Semantic Fallback
        assert "<noscript>" in doc_html
        assert '<article class="cb-semantic-content"' in doc_html
        assert "Distributed Consensus and Raft Protocol" in doc_html
        assert "Comprehensive breakdown of Raft leader election and log replication." in doc_html

        # Step 5: Admin hides the document
        resp_hide = client.post(
            "/document/hide",
            headers={"Host": "claire.example.com", **owner_headers},
            json={"id": "doc-e2e-seo", "hidden": 1},
        )
        assert resp_hide.status_code == 200

        # Invalidate in-memory sitemap cache to simulate post-cache retrieval
        server._sitemap_cache.clear()

        # Step 6: Verify sitemap immediately drops the hidden document
        resp_sitemap2 = client.get("/sitemap.xml", headers={"Host": "claire.example.com"})
        assert resp_sitemap2.status_code == 200
        root2 = ET.fromstring(resp_sitemap2.text)
        locs2 = [u.find("ns:loc", ns).text for u in root2.findall(".//ns:url", ns)]
        assert not any(f"?s={share_token}" in loc for loc in locs2)
        assert "https://claire.example.com/" in locs2

        # Step 7: Anonymous access to the hidden document is denied
        resp_hidden_doc = client.get(f"/p?s={share_token}", headers={"Host": "claire.example.com"})
        assert resp_hidden_doc.status_code == 404
