"""End-to-End (E2E) test for uttu.merui.net story ingestion pipeline."""

from __future__ import annotations

import html
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
                    '<button type="button" class="enemy-info-trigger" data-enemy-groups=\'[{"monsters": [{"name": "실험체 A", "headIconId": 771234, "description": "외골격 폭주 개체"}]}]\'>\n'
                    '  <img src="/ui/enemy/ring.webp">\n'
                    '</button>\n'
                    '<img class="dialog-portrait-icon" src="https://images.merui.net/headicon_small/308001.webp"> 자, 심호흡하고...<br>\n'
                    '<img class="dialog-portrait-icon" src="https://images.merui.net/headicon_small/771234.webp"> 실험체 접근 중.<br>\n'
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
            assert "카카니아: 자, 심호흡하고..." in doc.raw_text
            assert "실험체 A: 실험체 접근 중." in doc.raw_text
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


def test_e2e_uttu_event_trail_ingestion(tmp_path: Path):
    """E2E test verifying complete pipeline for uttu.merui.net event story trail:
    classification -> uttu adapter -> trail fragment extraction -> DB persistence and vault export.
    """
    db_file = tmp_path / "claire_e2e_uttu_trail.db"
    vault_dir = tmp_path / "vault_trail"
    vault_dir.mkdir()

    settings = Settings(
        db_path=str(db_file),
        vault_path=str(vault_dir),
        provider="mock",
    )

    trail_url = "https://uttu.merui.net/story/event/3_1/?lang=kr&part=21&trail=21-0&view=detail"

    story_data_trails = {
        "3110112": {
            "title": [
                "To the UTTU Editorial Team",
                "《UTTU》编辑部收",
                "《UTTU》編輯部收",
                "『UTTU』編集部御中",
                "<UTTU> 편집부 수신",
            ],
            "image": "v3a1_fragmenticon_011",
            "description": [
                "Dear colleagues,\n\nRegarding Corvus, the conductress of the Danube Dawn. As you all know, she was once one of us and my most trusted partner. Thus, for personal reasons as well as curiosity, I have attempted to glimpse traces of the partner I once knew. I must admit, she's changed. There is barely a trace of the person I remember. The glimmer in her eyes remains, yes, but where that gaze now falls ... no longer has anything to do with me.",
                "致亲爱的同事们：\n\n关于“多瑙黎明号”的列车长告死鸟，你们都知道，过去她曾是我们中的一员，甚至还是我最好的搭档。因此，在这趟行程中，出于个人原因，以及最起码的好奇心，我试图从她身上捕捉到旧日搭档的影子。",
                "致親愛的同事們：\n\n關於“多瑙黎明號”的列車長告死鳥，你們都知道，過去她曾是我們中的一員，甚至還是我最好的搭檔。因此，在這趟行程中，出於個人原因，以及最起碼的好奇心，我試圖從她身上捕捉到舊日搭檔的影子。",
                "親愛なる同僚たちへ：\n\n「ドナウ・デイブレイク号」の車掌を務める告死鳥について。皆様ご存じの通り、かつて彼女は我々の仲間であり、わたくしにとってもかけがえのないパートナーでした。",
                "친애하는 동료들에게:\n\n‘도나우 여명호’의 열차장 코르부스에 대해 다들 아시겠지만 과거 그녀는 저희의 일원이었습니다. 심지어 저와 가장 가까운 파트너였죠. 그래서 이번 여정에서는 개인적인 이유와 호기심으로, 그녀에게서 옛 파트너의 흔적을 찾아보려 합니다. 물론, 저도 인정해야겠죠. 그녀가 예전과 완전히 다르다는 것을. 저도 그 부분은 알고 있습니다. 마치 빗물에 씻겨진 유리창처럼 아무 흔적도 남지 않았겠죠. 그녀의 빛나는 눈은 예전과 같지만, 시선이 향하는 방향은 이미 저와 아무런 관계가 없는 것 같습니다.\n\n그래도 어떤 관점에서 보면 우리가 향하는 길의 결말은 큰 차이가 없을 수도 있습니다. 그러니 잠깐이지만 그녀와 함께 했던 이 여정을 소중히 여기려 합니다.\n\n판도라 윌슨 님에 대해서는, 그녀가 우리의 시야에서 사라진지 꽤 오랜 시간이 흘렀습니다. 그때 이후로 저는 '바람소리'를 통해 그녀의 존재를 감지하지 못했습니다. 마치 어떤 힘에 의해 그녀의 모든 흔적이 가볍게 지워져 버린 것처럼 말입니다. 그런데 최근 들어 '바람소리'와 비슷한 메아리가 희미하게 들려왔고, 여러 차례 그녀와 다시 연락을 시도했지만 아무런 응답이 없었습니다. 그럼에도 저는 그녀가 어딘가에 있다고 믿습니다.\n\n이런 와중에도 <UTTU> 편집부는 '판도라 윌슨'의 이름과 말투로 계속 칼럼을 작성하고 있습니다. 저는 그 이유를 이해합니다. 그녀를 잃는다는 건 곧 <UTTU>의 정신적 핵심을 잃는 것과도 같으니까요. 그런 상황은 감당할 수 없기에, 제가 먼저 나서 그녀의 말투로 외부와 소통하는 역할을 맡았습니다.\n\n<UTTU> 편집자 베리티\n‘도나우 여명호’에서 작성",
            ],
        }
    }

    trail_btn_la_kr = json.dumps({
        "data-trail": json.dumps({
            "id": 3110115,
            "fragment_id": 3110112,
            "title": "‘바람’의 메아리",
            "trail_type": 1,
            "reference": "EpisodeFinish=3110121",
            "description": "무수히 많은 실이 ‘바람’에 따라 흔들린다. ‘그물’ 안쪽에서도, ‘그물’ 바깥쪽에서도.",
            "button": "봉투 열기",
            "after": None,
        })
    })

    raw_trail_json = json.dumps({
        "id": 3110115,
        "fragment_id": 3110112,
        "title": 'Echoes of the "Wind"',
    })

    mock_page_html = f"""<!DOCTYPE html>
    <html>
      <head>
        <title>The Midnight Whistle | Reverse: 1999</title>
      </head>
      <body>
        <div id="storyBlock" data-titles="{html.escape(json.dumps({'en': 'The Midnight Whistle', 'kr': '한밤의 기적소리'}), quote=True)}"></div>
        <div id="storyData" data-trails="{html.escape(json.dumps(story_data_trails), quote=True)}"></div>
        <div class="accordion-item">
          <button class="accordion-trigger" data-chapter="21" data-url-part="21">
            <span data-l="" data-l-kr="TMW-21/태양 아래 대지" data-l-en="TMW-21/A Land Under the Sun">TMW-21/A Land Under the Sun</span>
          </button>
          <div class="accordion-content">
            <button class="trail-link" data-trail="{html.escape(raw_trail_json, quote=True)}" data-la-kr="{html.escape(trail_btn_la_kr, quote=True)}">
              • Echoes of the "Wind"
            </button>
          </div>
        </div>
      </body>
    </html>"""

    mock_resp = MagicMock()
    mock_resp.status_code = 200
    mock_resp.text = mock_page_html
    mock_resp.url = trail_url
    mock_resp.headers = {"content-type": "text/html; charset=utf-8"}
    mock_resp.content = mock_page_html.encode("utf-8")

    with patch("claire.ingest.fetchers.http.SafeHttpClient.get", return_value=mock_resp):
        service = IngestService(settings=settings)
        report = service.ingest(trail_url, source="e2e-event-trail-test")

        # 1. Pipeline execution status
        assert report.error is None
        assert report.document_id is not None
        assert report.title == "한밤의 기적소리 - <UTTU> 편집부 수신 | Reverse: 1999"

        # 2. Database persistence verification
        conn = dbm.connect(str(db_file))
        try:
            doc = dbm.get_document(conn, report.document_id)
            assert doc is not None
            assert doc.title == "한밤의 기적소리 - <UTTU> 편집부 수신 | Reverse: 1999"
            assert doc.meta.get("fetch_via") == "uttu"
            assert doc.meta.get("page_type") == "event_trail"
            assert doc.meta.get("fragment_id") == 3110112

            # Check markdown body content
            assert "# <UTTU> 편집부 수신" in doc.raw_text
            assert "[단서: ‘바람’의 메아리]" in doc.raw_text
            assert "무수히 많은 실이 ‘바람’에 따라 흔들린다." in doc.raw_text
            assert "친애하는 동료들에게:" in doc.raw_text
            assert "<UTTU> 편집자 베리티" in doc.raw_text

            # Check images
            doc_images = doc.meta.get("images", [])
            assert len(doc_images) == 1
            assert doc_images[0]["url"] == "https://assets.merui.net/dungeon/fragmenticon/v3a1_fragmenticon_011.webp"
            assert doc_images[0]["alt"] == "<UTTU> 편집부 수신"
        finally:
            conn.close()

        # 3. Vault markdown export file check
        vault_files = list(vault_dir.glob("*.md"))
        assert len(vault_files) >= 1
        content = vault_files[0].read_text(encoding="utf-8")
        assert "한밤의 기적소리 - <UTTU> 편집부 수신 | Reverse: 1999" in content
        assert "친애하는 동료들에게:" in content
