"""End-to-End (E2E) test for uttu.merui.net story ingestion pipeline."""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from claire.config import Settings
from claire.ingest.service import IngestService
from claire.store import db as dbm


def test_e2e_uttu_story_ingestion(tmp_path: Path):
    """E2E test verifying complete pipeline for uttu.merui.net story:
    classification -> domain adapter priority execution -> clean text/metadata extraction -> DB persistence and vault export.
    """
    db_file = tmp_path / "claire_e2e_uttu.db"
    vault_dir = tmp_path / "vault"
    vault_dir.mkdir()

    settings = Settings(
        db_path=str(db_file),
        vault_path=str(vault_dir),
        provider="mock",
    )

    story_url = "https://uttu.merui.net/story/trails/101039/kr/?part=1"

    # Mock chaptersData with substantial story content (> 300 characters for MIN_CONTENT threshold)
    dialogues = []
    for i in range(15):
        dialogues.append(
            f"<b>연구원 {i}:</b> 외골격 야외 실험 제{i}구역 관측 결과 이상 없음. "
            f"수치가 점진적으로 상승하고 있으며 특이 동향은 감지되지 않았습니다.<br>"
        )
    dialogue_content = "".join(dialogues)

    mock_chapters_data = {
        "chapters": [
            {
                "number": "1",
                "textPartNumber": "1",
                "title": "야외 실험",
                "content": (
                    '<img id="bg-1" class="bg-image-block" src="https://images.merui.net/storybg/smallbg/exp.webp" '
                    'data-modal-src="https://images.merui.net/storybg/exp_full.webp" alt="실험 구역">\n'
                    '<img id="bg-heiping" src="https://images.merui.net/storybg/heiping.webp">\n'
                    '<div class="location-label">야외 실험장 제3구역</div>\n'
                    '<div class="music-cue" data-music-id="lab_bgm"><span class="music-cue-text">Lab Theme</span></div>\n'
                    + dialogue_content +
                    '<button type="button" class="enemy-info-trigger" data-enemy-groups=\'[{"monsters": [{"name": "실험체 A", "description": "외골격 폭주 개체"}]}]\'>\n'
                    '  <img src="/ui/enemy/ring.webp">\n'
                    '</button>\n'
                ),
            }
        ],
        "frontmatter": {
            "title": "Outdoor Experiment",
            "localeTitle": "야외 실험",
        },
        "type": "trails",
        "name": "101039",
    }

    mock_page_html = f"""<!DOCTYPE html>
    <html>
      <head>
        <title>Outdoor Experiment | Reverse: 1999</title>
      </head>
      <body>
        <div id="wipBanner">Under Construction</div>
        <script type="text/plain" id="chaptersData">{json.dumps(mock_chapters_data)}</script>
        <a href="/story/trails/101039/kr/?part=1">Part 1 Link</a>
      </body>
    </html>"""

    mock_resp = MagicMock()
    mock_resp.status_code = 200
    mock_resp.text = mock_page_html
    mock_resp.url = story_url

    with patch("claire.ingest.fetchers.http.SafeHttpClient.get", return_value=mock_resp):
        service = IngestService(settings=settings)
        report = service.ingest(story_url, source="e2e-uttu-test")

        # 1. Pipeline execution status
        assert report.error is None
        assert report.document_id is not None
        assert report.title == "야외 실험 - 1. 야외 실험 | Reverse: 1999"

        # 2. Database persistence verification
        conn = dbm.connect(str(db_file))
        try:
            doc = dbm.get_document(conn, report.document_id)
            assert doc is not None
            assert doc.title == "야외 실험 - 1. 야외 실험 | Reverse: 1999"
            assert doc.meta.get("fetch_via") == "uttu"
            assert doc.meta.get("page_type") == "story"

            # Check extracted markdown structure
            assert "### [장소: 야외 실험장 제3구역]" in doc.raw_text
            assert "[전투 등장: 실험체 A — 외골격 폭주 개체]" in doc.raw_text
            assert "외골격 야외 실험" in doc.raw_text
            assert "Lab Theme" not in doc.raw_text  # UI cue excluded

            # Check images
            doc_images = doc.meta.get("images", [])
            assert len(doc_images) == 1
            assert doc_images[0]["url"] == "https://images.merui.net/storybg/exp_full.webp"
            assert doc_images[0]["alt"] == "실험 구역"
        finally:
            conn.close()

        # 3. Vault markdown export file check
        vault_files = list(vault_dir.glob("*.md"))
        assert len(vault_files) >= 1
        content = vault_files[0].read_text(encoding="utf-8")
        assert "야외 실험 - 1. 야외 실험 | Reverse: 1999" in content
