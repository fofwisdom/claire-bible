from __future__ import annotations

import time
from pathlib import Path
from types import SimpleNamespace

import pytest
from starlette.testclient import TestClient

from claire.api import server
from claire.config import Settings
from claire.store import db as dbm

OWNER_TOKEN = "owner-token-secret-12345678901234"
COLLAB_TOKEN = "collab-token-secret-1234567890123"
READONLY_TOKEN = "readonly-token-secret-123456789012"


@pytest.fixture
def syndication_e2e_env(tmp_path: Path):
    db_file = tmp_path / "claire_syndication_e2e.db"
    conn = dbm.connect(db_file)
    dbm.init_db(conn)
    conn.close()

    settings = Settings(
        _env_file=None,
        db_path=str(db_file),
        data_path=str(tmp_path),
        vault_path=str(tmp_path / "vault"),
        environment="development",
        public_url="http://127.0.0.1:8765/",
        fqdn="",
        inject_token=OWNER_TOKEN,
        collaborator_token=COLLAB_TOKEN,
        readonly_token=READONLY_TOKEN,
        anonymous_readonly=True,
        multi_theme=False,
        robots_policy="public",
        block_ai_crawlers=True,
        sitemap_enabled=True,
        sitemap_cache_ttl=900,
        site_name="Claire Bible Syndication E2E",
    )
    service = SimpleNamespace(provider=SimpleNamespace(name="stub"))
    app = server.create_app(settings, service)
    return settings, app, db_file


def test_syndication_e2e_workflow(syndication_e2e_env) -> None:
    _, app, db_file = syndication_e2e_env

    now = time.time()
    conn = dbm.connect(db_file)
    conn.execute("INSERT INTO documents (id, url, canonical_url, title, author, published_at, fetched_at, raw_text, source_type, content_hash, detail, detail_html, hidden) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)", ("doc_synd_e2e_01", "https://arxiv.org/abs/2610.09102", "https://arxiv.org/abs/2610.09102", "Deterministic Ontology Resolution in Distributed Knowledge Graphs", "Claire Research Lab", "2026-10-28T06:30:00Z", now, "Full paper text on deterministic ontology resolution.", "rss", "hash_synd_e2e_01", "# Deterministic Ontology Resolution\n\nVerified knowledge graph ingestion.", "<p>Verified knowledge graph ingestion.</p>", 0))
    conn.execute("INSERT INTO entities (id, type, name, norm_name, aliases, props, observations, sources, provisional, created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)", ("ent_synd_e2e_01", "Concept", "Entity Resolution", "entity resolution", '["Deterministic Resolution"]', "{}", "[]", '["doc_synd_e2e_01"]', 0, now, now))
    conn.commit()
    conn.close()

    with TestClient(app, base_url="http://127.0.0.1:8765") as client:
        r_pub = client.get("/syndication")
        assert r_pub.status_code == 200
        assert "Claire Bible — Syndication" in r_pub.text
        assert 'data-layout="flatlist"' in r_pub.text
        assert "수집 원문" in r_pub.text
        assert "적재 본문" in r_pub.text
        assert "부스트 / 인용 발행" in r_pub.text
        assert "엔티티 (= 태그)" in r_pub.text

        r_prefs = client.get("/syndication/preferences?theme=0")
        assert r_prefs.status_code == 200
        assert "Syndication 상세 환경설정" in r_prefs.text
        assert "클레어바이블의 태그는 곧 온톨로지 엔티티" in r_prefs.text
        assert 'href="/syndication"' in r_prefs.text

        r_robots = client.get("/robots.txt")
        assert r_robots.status_code == 200
        assert "Disallow: /syndication" in r_robots.text

        r_docs = client.get("/documents")
        assert r_docs.status_code == 200
        assert any(d["id"] == "doc_synd_e2e_01" for d in r_docs.json()["documents"])

        r_doc_detail = client.get("/document?id=doc_synd_e2e_01")
        assert r_doc_detail.status_code == 200
        assert "Verified knowledge graph ingestion." in r_doc_detail.json()["detail_html"]
