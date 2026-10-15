"""End-to-End (E2E) test for HoYoLAB article ingestion with VLM image transcription."""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from claire.config import Settings
from claire.ingest.service import IngestService
from claire.store import db as dbm


def test_e2e_hoyolab_article_vlm_ingestion(tmp_path: Path):
    """E2E test verifying complete pipeline: classification, HoYoLAB fetch, VLM transcription, and vault export."""
    db_file = tmp_path / "claire_e2e.db"
    vault_dir = tmp_path / "vault"
    vault_dir.mkdir()

    settings = Settings(
        db_path=str(db_file),
        vault_path=str(vault_dir),
        provider="mock",
        enable_vlm_transcription=True,
    )

    article_url = "https://www.hoyolab.com/article/46898605"
    mock_post_payload = {
        "post": {
            "post_id": "46898605",
            "subject": "[동방의 흙모래] 가디언 정보",
            "content": "",
            "structured_content": json.dumps([
                {"insert": "Record of answers.\n"},
                {"insert": {"image": "https://upload-os-bbs.hoyolab.com/upload/community/card1.jpg"}},
                {"insert": {"image": "https://upload-os-bbs.hoyolab.com/upload/community/card2.jpg"}},
            ]),
            "created_at": 1727500000,
        },
        "user": {"nickname": "Honkai: Star Rail"},
        "topics": [{"name": "Honkai: Star Rail"}],
        "image_list": [
            {"url": "https://upload-os-bbs.hoyolab.com/upload/community/card1.jpg", "width": 900, "height": 1200},
            {"url": "https://upload-os-bbs.hoyolab.com/upload/community/card2.jpg", "width": 900, "height": 1200},
        ],
    }

    mock_client = MagicMock()
    mock_client.get_post.return_value = mock_post_payload

    with patch("claire.ingest.fetchers.hoyolab.HoYoLabClient", return_value=mock_client):
        service = IngestService(settings=settings)
        report = service.ingest(article_url, source="e2e-test")

        mock_client.get_post.assert_called_once_with("46898605", lang="ko-kr")

        # 1. Pipeline execution status
        assert report.error is None
        assert report.source_type == "hoyolab"
        assert report.document_id is not None
        assert report.title == "[동방의 흙모래] 가디언 정보"
        assert report.vlm_transcribed is True
        assert report.vlm_images_count == 2
        assert report.vlm_language == "ko"
        assert "🖼️ 이미지 전사(VLM) (2장) [ko]" in report.telegram_summary()

        # 2. Database persistence verification
        conn = dbm.connect(str(db_file))
        try:
            doc = dbm.get_document(conn, report.document_id)
            assert doc is not None
            assert doc.source_type == "hoyolab"
            assert doc.canonical_url == "https://hoyolab.com/article/46898605"
            assert "Infographic & Image Transcription" in doc.raw_text
            assert "Card 1" in doc.raw_text
            assert "Card 2" in doc.raw_text
            assert doc.meta.get("vlm_transcribed") is True
            assert doc.meta.get("vlm_images_count") == 2
            assert doc.meta.get("vlm_language") == "ko"

            # 2-1. Docmeta presentation contract & UI verification
            from claire.store.queries import document_detail
            from claire.graphview import shared_html

            detail = document_detail(conn, report.document_id)
            assert detail is not None
            assert detail["vlm_transcribed"] is True
            assert detail["vlm_language"] == "ko"
            rendered_page = shared_html(detail)
            assert ".vlm-tag" in rendered_page
            assert "🖼️ 이미지 전사" in rendered_page
            assert '"vlm_language": "ko"' in rendered_page

            import subprocess
            from claire.graphview import GRAPH_HTML

            start = GRAPH_HTML.index("function docMetaHtml(dc){")
            end = GRAPH_HTML.index("function renderReader(dc){")
            fn = GRAPH_HTML[start:end]
            js = f"""
            function esc(s){{ return String(s||'').replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;').replace(/"/g,'&quot;'); }}
            {fn}
            const d = {json.dumps(detail)};
            console.log(docMetaHtml(d));
            """
            res = subprocess.run(["node", "-e", js], capture_output=True, text=True, check=True)
            assert "🖼️ 이미지 전사 (2장)" in res.stdout
            assert "vlm-tag" in res.stdout
            assert "이미지 전사(VLM: ko)" in res.stdout

            # Entities and relationships created
            cur = conn.cursor()
            cur.execute("SELECT COUNT(*) FROM entities")
            entity_count = cur.fetchone()[0]
            assert entity_count > 0
        finally:
            conn.close()

        # 3. Obsidian vault markdown document verification
        exported_files = list(vault_dir.rglob("*.md"))
        assert len(exported_files) > 0
        content = exported_files[0].read_text(encoding="utf-8")
        assert len(content) > 0
