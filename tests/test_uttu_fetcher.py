"""Unit tests for uttu.merui.net web adapter."""

from __future__ import annotations

import json
from unittest.mock import MagicMock, patch

import pytest

from claire.ingest.fetchers.base import WebAdapterResult
from claire.ingest.fetchers.uttu import (
    UttuAdapter,
    _format_chapter_content,
    resolve_uttu_language,
)
from claire.ingest.registry import registry


def test_registry_get_matching_web_adapters_domain_only():
    """Verify registry adapter filtering with domain_only flag."""
    uttu_url = "https://uttu.merui.net/story/trails/101039/kr/"
    discourse_url = "https://community.example.com/t/topic/1234"

    domain_adapters = registry.get_matching_web_adapters(uttu_url, domain_only=True)
    assert any(a.name() == "uttu" for a in domain_adapters)

    # Discourse adapter has no domains configured, so domain_only=True should exclude it
    domain_adapters_discourse = registry.get_matching_web_adapters(discourse_url, domain_only=True)
    assert not any(a.name() == "discourse" for a in domain_adapters_discourse)

    all_adapters_discourse = registry.get_matching_web_adapters(discourse_url, domain_only=False)
    assert any(a.name() == "discourse" for a in all_adapters_discourse)


def test_resolve_uttu_language():
    """Verify language resolution from URL path, query, and system settings."""
    assert resolve_uttu_language("https://uttu.merui.net/story/character/37/kr/") == "kr"
    assert resolve_uttu_language("https://uttu.merui.net/story/character/37/en/") == "en"
    assert resolve_uttu_language("https://uttu.merui.net/enemies/?lang=kr") == "kr"
    assert resolve_uttu_language("https://uttu.merui.net/enemies/?lang=ko") == "kr"
    assert resolve_uttu_language("https://uttu.merui.net/enemies/?lang=ja") == "jp"

    mock_settings = MagicMock()
    mock_settings.effective_preferred_languages = ["ko-KR"]
    mock_settings.preferred_languages = "ko"
    assert resolve_uttu_language("https://uttu.merui.net/about/", settings=mock_settings) == "kr"


def test_format_chapter_content():
    """Verify chapter HTML formatting to markdown, combat triggers, cues, and image extraction."""
    raw_html = """
    <img id="bg-1" class="bg-image-block" src="https://images.merui.net/smallbg.webp" data-modal-src="https://images.merui.net/highres_bg.webp" alt="Background Scene">
    <img id="bg-black" src="https://images.merui.net/storybg/bg/heiping.webp" alt="Black screen">
    <img id="bg-white" src="https://images.merui.net/storybg/bg/baiping.webp" alt="White screen">
    <div class="location-label">라플라스 박물관</div>
    <div class="music-cue" data-music-id="cue1"><span class="music-cue-text">Cheerful Music</span></div>
    <div class="video-cue" data-video-id="v1">Video Clip</div>
    <b>37:</b> 기하학적 선들이 마음에 들어.<br>
    <b data-headicon="620520">관람객Ⅰ:</b> 어디 보자, 특별 전시 구역은... C구역이네.<br>
    <button type="button" class="enemy-info-trigger" data-enemy-groups='[{"monsters": [{"name": "제이미", "description": "악담을 퍼붓는 아이"}]}]'>
        <img src="/ui/enemy/ring.webp" alt="Ring badge">
    </button>
    <p data-screen="1">박물관의 하루가 끝났다.</p>
    """

    text, images = _format_chapter_content(raw_html, base_url="https://uttu.merui.net/story/101/")

    # 1. Location formatting
    assert "### [장소: 라플라스 박물관]" in text

    # 2. Cues removed
    assert "Cheerful Music" not in text
    assert "Video Clip" not in text

    # 3. Combat triggers parsed with em-dash
    assert "[전투 등장: 제이미 — 악담을 퍼붓는 아이]" in text

    # 4. Dialogues on separate lines
    assert "37: 기하학적 선들이 마음에 들어." in text
    assert "관람객Ⅰ: 어디 보자, 특별 전시 구역은... C구역이네." in text
    lines = text.splitlines()
    dialogue_indices = [i for i, line in enumerate(lines) if "관람객Ⅰ" in line or "37:" in line]
    assert len(dialogue_indices) == 2
    assert dialogue_indices[0] != dialogue_indices[1]

    # 5. Narration p formatted
    assert "박물관의 하루가 끝났다." in text

    # 6. Images collected and excluded properly
    assert len(images) == 1
    assert images[0]["url"] == "https://images.merui.net/highres_bg.webp"
    assert images[0]["alt"] == "Background Scene"


def test_try_fetch_story_single_part():
    """Verify story viewer page parsing with part query parameter."""
    url = "https://uttu.merui.net/story/anecdote/37/kr/?part=2"
    chapters_data = {
        "chapters": [
            {
                "number": "1",
                "textPartNumber": "1",
                "title": "결석",
                "content": "<div class=\"location-label\">장소 1</div><b>A:</b> 대사 1<br>",
            },
            {
                "number": "2",
                "textPartNumber": "2",
                "title": "플라톤의 입체",
                "content": "<div class=\"location-label\">장소 2</div><b>B:</b> 대사 2<br>",
            },
        ],
        "frontmatter": {
            "title": "A Day Plotted Logarithmically",
            "localeTitle": "곱셈축 위의 하루",
        },
        "type": "anecdote",
        "name": "37",
    }

    page_html = f"""<!DOCTYPE html>
    <html>
      <head><title>A Day Plotted Logarithmically | Reverse: 1999</title></head>
      <body>
        <script type="text/plain" id="chaptersData">{json.dumps(chapters_data)}</script>
        <a href="/story/anecdote/37/kr/?part=1">Part 1</a>
      </body>
    </html>"""

    mock_resp = MagicMock()
    mock_resp.status_code = 200
    mock_resp.text = page_html

    with patch("claire.ingest.fetchers.uttu.SafeHttpClient.get", return_value=mock_resp):
        res = UttuAdapter.try_fetch(url)

        assert res is not None
        assert isinstance(res, WebAdapterResult)
        assert res.title == "곱셈축 위의 하루 - 2. 플라톤의 입체 | Reverse: 1999"
        assert "### [장소: 장소 2]" in res.text
        assert "B: 대사 2" in res.text
        assert "장소 1" not in res.text
        assert res.parser_info["adapter"] == "uttu"
        assert res.parser_info["part"] == "2"


def test_try_fetch_story_full():
    """Verify full story formatting when part query parameter is absent."""
    url = "https://uttu.merui.net/story/anecdote/37/kr/"
    chapters_data = {
        "chapters": [
            {
                "number": "1",
                "textPartNumber": "1",
                "title": "결석",
                "content": "<b>A:</b> 첫 대사<br>",
            },
            {
                "number": "2",
                "textPartNumber": "2",
                "title": "플라톤의 입체",
                "content": "<b>B:</b> 둘째 대사<br>",
            },
        ],
        "frontmatter": {
            "title": "A Day Plotted Logarithmically",
            "localeTitle": "곱셈축 위의 하루",
        },
        "type": "anecdote",
        "name": "37",
    }

    page_html = f"""<!DOCTYPE html>
    <html>
      <head><title>A Day Plotted Logarithmically | Reverse: 1999</title></head>
      <body>
        <script type="text/plain" id="chaptersData">{json.dumps(chapters_data)}</script>
      </body>
    </html>"""

    mock_resp = MagicMock()
    mock_resp.status_code = 200
    mock_resp.text = page_html

    with patch("claire.ingest.fetchers.uttu.SafeHttpClient.get", return_value=mock_resp):
        res = UttuAdapter.try_fetch(url)

        assert res is not None
        assert res.title == "곱셈축 위의 하루 | Reverse: 1999"
        assert "## 1. 결석" in res.text
        assert "첫 대사" in res.text
        assert "## 2. 플라톤의 입체" in res.text
        assert "둘째 대사" in res.text
        assert res.parser_info["part"] is None


def test_try_fetch_database_page_multilingual_and_modals():
    """Verify database page handling: modal removal, data-lang filtering, and table conversion."""
    url = "https://uttu.merui.net/enemies/?lang=kr"
    page_html = """<!DOCTYPE html>
    <html>
      <head><title>Enemy List | Reverse: 1999</title></head>
      <body>
        <div id="wipBanner">Work in progress</div>
        <div id="loading-screen">Loading...</div>
        <div id="nav__container"><a href="/home">Home</a></div>
        <div id="uttuCopyright">Copyright 2026</div>

        <div data-lang="en">English Enemy Lore: Tommy is strong.</div>
        <div data-lang="kr">한국어 적 정보: 토미는 힘이 세다.</div>
        <div data-lang="jp">日本語のエネミー情報</div>

        <table>
          <caption>적 상세 스탯</caption>
          <tr><th>이름</th><th>체력</th><th>공격력</th></tr>
          <tr><td>제이미</td><td>600</td><td>139</td></tr>
        </table>

        <a href="/enemies/tommy">토미 상세 보기</a>
      </body>
    </html>"""

    mock_resp = MagicMock()
    mock_resp.status_code = 200
    mock_resp.text = page_html

    with patch("claire.ingest.fetchers.uttu.SafeHttpClient.get", return_value=mock_resp):
        res = UttuAdapter.try_fetch(url)

        assert res is not None
        assert res.title == "Enemy List | Reverse: 1999"

        # UI modals and copyright removed
        assert "Work in progress" not in res.text
        assert "Loading..." not in res.text
        assert "Copyright 2026" not in res.text

        # Multilingual filtering: only Korean text retained
        assert "한국어 적 정보: 토미는 힘이 세다." in res.text
        assert "English Enemy Lore" not in res.text
        assert "日本語のエネミー情報" not in res.text

        # Table formatted to markdown
        assert "| 이름 | 체력 | 공격력 |" in res.text
        assert "| 제이미 | 600 | 139 |" in res.text

        # Links extracted
        assert "https://uttu.merui.net/enemies/tommy" in res.links
        assert res.anchors.get("https://uttu.merui.net/enemies/tommy") == "토미 상세 보기"


def test_combat_dialogue_speaker_resolution():
    """Verify combat dialogue speaker resolution with exact ID, 4-digit prefix, and static fallback."""
    raw_html = """
    <b data-headicon="620520">관람객Ⅰ:</b> 어디 보자...<br>
    <button type="button" class="enemy-info-trigger" data-enemy-groups='[{"monsters": [{"name": "실험체 A", "headIconId": 771234}]}]'></button>
    <img class="dialog-portrait-icon" src="https://images.merui.net/headicon_small/620520.webp" alt="620520"> 정확한 매칭 대사.<br>
    <img class="dialog-portrait-icon" src="https://images.merui.net/headicon_small/771234.webp" alt="771234"> 적 정보 매칭 대사.<br>
    <img class="dialog-portrait-icon" src="https://images.merui.net/headicon_small/308001.webp" alt="308001"> 자, 심호흡하고...<br>
    <img class="dialog-portrait-icon" src="/headicon_small/306510.webp" alt="306510"> ...!<br>
    <img class="dialog-portrait-icon" src="/headicon_small/999901.webp" alt="999901"> 미등록 화자 대사.<br>
    <img id="scene-bg" src="https://images.merui.net/storybg/scene.webp" alt="Scene Background">
    """

    text, images = _format_chapter_content(raw_html, base_url="https://uttu.merui.net/story/101/")

    # 1. Exact ID match from <b data-headicon="...">
    assert "관람객Ⅰ: 정확한 매칭 대사." in text

    # 2. Match from enemy-info-trigger
    assert "실험체 A: 적 정보 매칭 대사." in text

    # 3. 4-digit prefix match from static fallback map (3080 -> 카카니아, 3065 -> 마커스)
    assert "카카니아: 자, 심호흡하고..." in text
    assert "마커스: ...!" in text

    # 4. Unregistered ID fallback
    assert "[대사(999901)]: 미등록 화자 대사." in text

    # 5. Verify dialog-portrait-icon images are NOT collected into images
    assert len(images) == 1
    assert images[0]["url"] == "https://images.merui.net/storybg/scene.webp"
    for img in images:
        assert "headicon" not in img["url"]


def test_try_fetch_story_cross_chapter_headicon_resolution():
    """Verify combat dialogue speaker resolution using cross-chapter pre-scanned headicon maps."""
    url = "https://uttu.merui.net/story/chapter/101/kr/?part=2"
    chapters_data = {
        "chapters": [
            {
                "number": "1",
                "textPartNumber": "1",
                "title": "전조",
                "content": "<b data-headicon=\"512101\">호프만:</b> 준비는 끝났나?<br>",
            },
            {
                "number": "2",
                "textPartNumber": "2",
                "title": "교전",
                "content": (
                    '<img class="dialog-portrait-icon" src="https://images.merui.net/headicon_small/512101.webp"> 작전 개시.<br>'
                    '<img class="dialog-portrait-icon" src="/headicon_small/308001.webp"> 자, 심호흡하고...<br>'
                ),
            },
        ],
        "frontmatter": {
            "title": "Operation",
            "localeTitle": "작전",
        },
        "type": "chapter",
        "name": "101",
    }

    page_html = f"""<!DOCTYPE html>
    <html>
      <head><title>Operation | Reverse: 1999</title></head>
      <body>
        <script type="text/plain" id="chaptersData">{json.dumps(chapters_data)}</script>
      </body>
    </html>"""

    mock_resp = MagicMock()
    mock_resp.status_code = 200
    mock_resp.text = page_html

    with patch("claire.ingest.fetchers.uttu.SafeHttpClient.get", return_value=mock_resp):
        res = UttuAdapter.try_fetch(url)

        assert res is not None
        assert "호프만: 작전 개시." in res.text
        assert "카카니아: 자, 심호흡하고..." in res.text
        assert len(res.images) == 0


def test_try_fetch_event_trail_detail_fragment():
    """Verify event story trail detail extraction with secret document fragment."""
    url = "https://uttu.merui.net/story/event/3_1/?lang=kr&part=21&trail=21-0&view=detail"

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
                "Dear colleagues,\n\nRegarding Corvus...",
                "致亲爱的同事们：\n\n...",
                "致親愛的同事們：\n\n...",
                "親愛なる同僚たちへ：\n\n...",
                "친애하는 동료들에게:\n\n‘도나우 여명호’의 열차장 코르부스에 대해 다들 아시겠지만 과거 그녀는 저희의 일원이었습니다.\n\n<UTTU> 편집자 베리티\n‘도나우 여명호’에서 작성",
            ],
        }
    }

    trail_btn_la_kr = json.dumps({
        "data-trail": json.dumps({
            "id": 3110115,
            "fragment_id": 3110112,
            "title": "‘바람’의 메아리",
            "trail_type": 1,
            "description": "무수히 많은 실이 ‘바람’에 따라 흔들린다. ‘그물’ 안쪽에서도, ‘그물’ 바깥쪽에서도.",
            "button": "봉투 열기",
            "after": None,
        })
    })

    page_html = f"""<!DOCTYPE html>
    <html>
      <head><title>The Midnight Whistle | Reverse: 1999</title></head>
      <body>
        <div id="storyBlock" data-titles='{{"en": "The Midnight Whistle", "kr": "한밤의 기적소리"}}'></div>
        <div id="storyData" data-trails='{json.dumps(story_data_trails)}'></div>
        <div class="accordion-item">
          <button class="accordion-trigger" data-chapter="21" data-url-part="21">
            <span data-l="" data-l-kr="TMW-21/태양 아래 대지" data-l-en="TMW-21/A Land Under the Sun">TMW-21/A Land Under the Sun</span>
          </button>
          <div class="accordion-content">
            <button class="trail-link" data-trail='{{"id": 3110115, "fragment_id": 3110112, "title": "Echoes of the \\"Wind\\""}}' data-la-kr='{trail_btn_la_kr}'>
              • Echoes of the "Wind"
            </button>
          </div>
        </div>
      </body>
    </html>"""

    mock_resp = MagicMock()
    mock_resp.status_code = 200
    mock_resp.text = page_html

    with patch("claire.ingest.fetchers.uttu.SafeHttpClient.get", return_value=mock_resp):
        res = UttuAdapter.try_fetch(url)

        assert res is not None
        assert isinstance(res, WebAdapterResult)
        assert res.title == "한밤의 기적소리 - <UTTU> 편집부 수신 | Reverse: 1999"
        assert res.parser_info["adapter"] == "uttu"
        assert res.parser_info["page_type"] == "event_trail"
        assert res.parser_info["fragment_id"] == 3110112

        # Check content
        assert "# <UTTU> 편집부 수신" in res.text
        assert "[단서: ‘바람’의 메아리]" in res.text
        assert "무수히 많은 실이 ‘바람’에 따라 흔들린다." in res.text
        assert "친애하는 동료들에게:" in res.text
        assert "<UTTU> 편집자 베리티" in res.text

        # Check images
        assert len(res.images) == 1
        assert res.images[0]["url"] == "https://assets.merui.net/dungeon/fragmenticon/v3a1_fragmenticon_011.webp"
        assert res.images[0]["alt"] == "<UTTU> 편집부 수신"


def test_try_fetch_event_trail_dialog_steps():
    """Verify event story trail with structured dialogSteps formatting."""
    url = "https://uttu.merui.net/story/event/3_1/?lang=kr&part=12&trail=12-0"

    dialog_steps = {
        "dialog_id": 49,
        "steps": [
            {"step_id": 49001, "speaker": "이글리카", "dialog": "그래서, 어디서 왔지?"},
            {"step_id": 49002, "speaker": "피로스", "dialog": "유격대의 새 대원에 대한 기본 정보를 파악하려는 거야?"},
            {"step_id": 49003, "speaker": "", "dialog": "대화 종료"},
        ],
    }

    trail_btn_la_kr = json.dumps({
        "data-trail": json.dumps({
            "id": 3110109,
            "fragment_id": 0,
            "title": "잔향은 과거에서 온다.",
            "trail_type": 18,
            "description": "과거를 회상하는 건 자신을 향한 가장 직접적이며 단순한 공격 방식입니다.",
            "dialogSteps": dialog_steps,
        })
    })

    page_html = f"""<!DOCTYPE html>
    <html>
      <head><title>The Midnight Whistle | Reverse: 1999</title></head>
      <body>
        <div id="storyBlock" data-titles='{{"en": "The Midnight Whistle", "kr": "한밤의 기적소리"}}'></div>
        <div id="storyData" data-trails='{{}}'></div>
        <div class="accordion-item">
          <button class="accordion-trigger" data-chapter="12" data-url-part="12">
            <span>TMW-12</span>
          </button>
          <div class="accordion-content">
            <button class="trail-link" data-trail='{{"id": 3110109, "fragment_id": 0, "title": "Echoes"}}' data-la-kr='{trail_btn_la_kr}'>
              • Echoes
            </button>
          </div>
        </div>
      </body>
    </html>"""

    mock_resp = MagicMock()
    mock_resp.status_code = 200
    mock_resp.text = page_html

    with patch("claire.ingest.fetchers.uttu.SafeHttpClient.get", return_value=mock_resp):
        res = UttuAdapter.try_fetch(url)

        assert res is not None
        assert res.title == "한밤의 기적소리 - 잔향은 과거에서 온다. | Reverse: 1999"
        assert res.parser_info["page_type"] == "event_trail"
        assert "### [대화 기록]" in res.text
        assert "**이글리카**: 그래서, 어디서 왔지?" in res.text
        assert "**피로스**: 유격대의 새 대원에 대한 기본 정보를 파악하려는 거야?" in res.text
        assert "대화 종료" not in res.text


def test_try_fetch_event_part_only():
    """Verify event stage overview parsing when part query parameter is present without trail."""
    url = "https://uttu.merui.net/story/event/3_1/?lang=kr&part=21"

    story_data_trails = {
        "3110112": {
            "title": ["To Editorial", "", "", "", "<UTTU> 편집부 수신"],
            "description": ["", "", "", "", "본문"],
        }
    }

    trail_btn_la_kr = json.dumps({
        "data-trail": json.dumps({
            "id": 3110115,
            "fragment_id": 3110112,
            "title": "‘바람’의 메아리",
            "description": "무수히 많은 실이 ‘바람’에 따라 흔들린다.",
        })
    })

    page_html = f"""<!DOCTYPE html>
    <html>
      <head><title>The Midnight Whistle | Reverse: 1999</title></head>
      <body>
        <div id="storyBlock" data-titles='{{"en": "The Midnight Whistle", "kr": "한밤의 기적소리"}}'></div>
        <div id="storyData" data-trails='{json.dumps(story_data_trails)}'></div>
        <div class="accordion-item">
          <button class="accordion-trigger" data-chapter="21" data-url-part="21">
            <span data-l="" data-l-kr="TMW-21 / 태양 아래 대지" data-l-en="TMW-21 / A Land Under the Sun">TMW-21 / A Land Under the Sun</span>
          </button>
          <div class="accordion-content">
            <p>모두, 안녕히. 즐거운 여정 되시길.</p>
            <button class="trail-link" data-trail='{{"id": 3110115, "fragment_id": 3110112, "title": "Echoes"}}' data-la-kr='{trail_btn_la_kr}'>
              • Echoes
            </button>
          </div>
        </div>
      </body>
    </html>"""

    mock_resp = MagicMock()
    mock_resp.status_code = 200
    mock_resp.text = page_html

    with patch("claire.ingest.fetchers.uttu.SafeHttpClient.get", return_value=mock_resp):
        res = UttuAdapter.try_fetch(url)

        assert res is not None
        assert res.title == "한밤의 기적소리 - TMW-21 / 태양 아래 대지 | Reverse: 1999"
        assert res.parser_info["page_type"] == "event_part"
        assert "모두, 안녕히. 즐거운 여정 되시길." in res.text
        assert "### [단서 목록]" in res.text
        assert "- **‘바람’의 메아리** (<UTTU> 편집부 수신): 무수히 많은 실이 ‘바람’에 따라 흔들린다." in res.text
