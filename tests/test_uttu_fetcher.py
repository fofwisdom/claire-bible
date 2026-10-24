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
