"""Unit tests for HoYoWiki (wiki.hoyolab.com) collector, adapter, and client."""

from __future__ import annotations

import json
from unittest.mock import MagicMock, patch

import pytest

from claire.ingest.fetchers.base import BaseFetcher, BaseWebAdapter, WebAdapterResult
from claire.ingest.fetchers.hoyowiki import (
    HoYoWikiClient,
    HoyowikiAdapter,
    HoyowikiFetcher,
    clean_html_to_markdown,
    fetch_hoyowiki,
    format_entry_to_markdown,
    parse_hoyowiki_url,
    resolve_embedded_entries,
    try_hoyowiki_adapter,
)
from claire.ingest.registry import registry
from claire.ingest.router import classify
from claire.ontology.base import Document


def test_parse_hoyowiki_url():
    # PC entry
    game, eid, lang = parse_hoyowiki_url("https://wiki.hoyolab.com/pc/genshin/entry/11702")
    assert game == "genshin"
    assert eid == "11702"
    assert lang is None

    # Mobile entry with language query parameter
    game, eid, lang = parse_hoyowiki_url("https://wiki.hoyolab.com/m/hsr/entry/1919?lang=ko-kr")
    assert game == "hsr"
    assert eid == "1919"
    assert lang == "ko-kr"

    # PC character route
    game, eid, lang = parse_hoyowiki_url("https://wiki.hoyolab.com/pc/zzz/character/29")
    assert game == "zzz"
    assert eid == "29"
    assert lang is None

    # PC entry preview route
    game, eid, lang = parse_hoyowiki_url("https://wiki.hoyolab.com/pc/honkai3rd/entry_preview/100")
    assert game == "honkai3rd"
    assert eid == "100"
    assert lang is None

    # Non-entry URLs
    game, eid, lang = parse_hoyowiki_url("https://wiki.hoyolab.com/pc/genshin/home")
    assert game is None
    assert eid is None

    game, eid, lang = parse_hoyowiki_url("https://example.com/page")
    assert game is None
    assert eid is None


def test_fetcher_can_handle():
    assert HoyowikiFetcher.can_handle("https://wiki.hoyolab.com/pc/genshin/entry/11702")
    assert HoyowikiFetcher.can_handle("https://wiki.hoyolab.com/m/hsr/entry/1919")
    assert HoyowikiFetcher.can_handle("https://wiki.hoyolab.com/pc/zzz/character/29")
    assert not HoyowikiFetcher.can_handle("https://wiki.hoyolab.com/pc/genshin/home")
    assert not HoyowikiFetcher.can_handle("https://other.domain.com/entry/1")


def test_clean_html_to_markdown():
    raw = """
    <h1>Title Header</h1>
    <p>Paragraph text with <span style="color:red">colored span</span> and a <br>line break.</p>
    <custom-image url="https://example.com/img.png"></custom-image>
    <table>
        <tr><th>Header 1</th><th>Header 2</th></tr>
        <tr><td>Cell 1</td><td>Cell 2</td></tr>
    </table>
    <a href="https://example.com/link">Link text</a>
    """
    md, imgs, links = clean_html_to_markdown(raw)
    assert "Title Header" in md
    assert "Paragraph text with colored span" in md
    assert "| Header 1 | Header 2 |" in md
    assert "| Cell 1 | Cell 2 |" in md
    assert len(imgs) >= 1
    assert imgs[0]["url"] == "https://example.com/img.png"
    assert "https://example.com/link" in links


def test_resolve_embedded_entries():
    sample_text = (
        '- **특제 요리**: $[{"ep_id":1887,"icon":"https://example.com/icon.png","amount":2,"name":"마법 스파게티"}]$'
    )
    res, links, anchors, imgs = resolve_embedded_entries(sample_text, "genshin")
    assert "[마법 스파게티](https://wiki.hoyolab.com/pc/genshin/entry/1887) x2" in res
    assert "https://wiki.hoyolab.com/pc/genshin/entry/1887" in links
    assert anchors["https://wiki.hoyolab.com/pc/genshin/entry/1887"] == "마법 스파게티"
    assert len(imgs) == 1
    assert imgs[0]["url"] == "https://example.com/icon.png"


def test_format_entry_to_markdown():
    page_data = {
        "id": "1",
        "name": "치치",
        "desc": "<p>약방 「불복려」의 약초꾼이자 제자.</p>",
        "icon_url": "https://example.com/qiqi.png",
        "header_img_url": "https://example.com/qiqi_header.png",
        "filter_values": {
            "vision": {
                "key": {"text": "원소 속성"},
                "values": ["얼음 원소"],
            },
            "rarity": {
                "key": {"text": "희귀도"},
                "values": ["★5"],
            },
        },
        "modules": [
            {
                "id": "1",
                "name": "속성",
                "components": [
                    {
                        "component_id": "baseInfo",
                        "data": json.dumps({
                            "list": [
                                {"key": "이름", "value": ["치치"]},
                                {"key": "생일", "value": ["3월 3일"]},
                            ]
                        }),
                    }
                ],
            },
            {
                "id": "2",
                "name": "특성",
                "components": [
                    {
                        "component_id": "talent",
                        "data": json.dumps({
                            "list": [
                                {
                                    "title": "운래 검법",
                                    "desc": "<p>검으로 최대 5번 연속 공격한다.</p>",
                                    "icon_url": "https://example.com/talent1.png",
                                    "attributes": [
                                        {"key": "Level", "values": ["Lv.1", "Lv.2"]},
                                        {"key": "1단 피해", "values": ["37.8%", "40.9%"]},
                                    ],
                                }
                            ]
                        }),
                    }
                ],
            },
            {
                "id": "3",
                "name": "스토리",
                "components": [
                    {
                        "component_id": "story",
                        "data": json.dumps({
                            "list": [
                                {"title": "캐릭터 상세정보", "desc": "불복려의 약초꾼 치치 이야기."}
                            ]
                        }),
                    }
                ],
            },
        ],
    }

    title, text, links, anchors, imgs = format_entry_to_markdown(page_data, "genshin")
    assert "치치 - 원신 (Genshin Impact)" in title
    assert "## 개요" in text
    assert "약방 「불복려」의 약초꾼이자 제자." in text
    assert "## 분류 및 특성" in text
    assert "- **원소 속성**: 얼음 원소" in text
    assert "- **희귀도**: ★5" in text
    assert "## 속성" in text
    assert "- **이름**: 치치" in text
    assert "- **생일**: 3월 3일" in text
    assert "## 특성" in text
    assert "### 운래 검법" in text
    assert "| 속성 | Lv.1 | Lv.2 |" in text
    assert "| 1단 피해 | 37.8% | 40.9% |" in text
    assert "## 스토리" in text
    assert "불복려의 약초꾼 치치 이야기." in text
    assert any(im["url"] == "https://example.com/qiqi.png" for im in imgs)
    assert any(im["url"] == "https://example.com/talent1.png" for im in imgs)


def test_client_methods_mocked():
    client = HoYoWikiClient()

    # Mock SafeHttpClient resolve check
    with patch.object(client.safe_http, "_resolve_and_check") as mock_check:
        mock_check.return_value = None

        with patch("httpx.Client") as mock_client_cls:
            mock_ctx = MagicMock()
            mock_client_cls.return_value.__enter__.return_value = mock_ctx

            # 1. get_menus
            mock_resp = MagicMock()
            mock_resp.status_code = 200
            mock_resp.json.return_value = {"retcode": 0, "message": "OK", "data": {"menus": [{"id": "1", "name": "인물지"}]}}
            mock_ctx.get.return_value = mock_resp

            menus = client.get_menus("genshin")
            assert len(menus) == 1
            assert menus[0]["name"] == "인물지"

            # 2. get_entry_list
            mock_resp.json.return_value = {"retcode": 0, "message": "OK", "data": {"total": 1, "list": [{"entry_page_id": "1", "name": "치치"}]}}
            mock_ctx.post.return_value = mock_resp

            entry_list = client.get_entry_list("genshin", "2", page_num=1, page_size=10)
            assert entry_list["total"] == 1
            assert entry_list["list"][0]["name"] == "치치"

            # 3. get_entry
            mock_resp.json.return_value = {
                "retcode": 0,
                "message": "OK",
                "data": {"page": {"id": "1", "name": "치치", "desc": "치치 상세"}},
            }
            mock_ctx.get.return_value = mock_resp

            page = client.get_entry("1", "genshin")
            assert page["name"] == "치치"

            # 4. search
            mock_resp.json.return_value = {
                "retcode": 0,
                "message": "OK",
                "data": {"list": [{"entry_page_id": "49", "name": "라이덴 쇼군"}]},
            }
            mock_ctx.get.return_value = mock_resp

            res = client.search("라이덴", "genshin")
            assert len(res) == 1
            assert res[0]["name"] == "라이덴 쇼군"


def test_fetch_hoyowiki_end_to_end_mocked():
    sample_page = {
        "id": "49",
        "name": "라이덴 쇼군",
        "desc": "<p>이나즈마를 다스리는 전능한 나루카미 쇼군 바알세불.</p>",
        "icon_url": "https://example.com/raiden.png",
        "modules": [],
    }

    with patch.object(HoYoWikiClient, "get_entry", return_value=sample_page):
        doc = fetch_hoyowiki("https://wiki.hoyolab.com/pc/genshin/entry/49")
        assert isinstance(doc, Document)
        assert doc.source_type == "hoyowiki"
        assert doc.title == "라이덴 쇼군 - 원신 (Genshin Impact)"
        assert doc.canonical_url == "https://wiki.hoyolab.com/pc/genshin/entry/49"
        assert doc.author == "HoYoWiki"
        assert doc.meta["game"] == "genshin"
        assert doc.meta["entry_page_id"] == "49"
        assert "바알세불" in doc.raw_text


def test_web_adapter_try_fetch_mocked():
    sample_page = {
        "id": "1919",
        "name": "아케론",
        "desc": "<p>은하를 유랑하는 자칭 갤럭시 레인저 순해의 방랑자.</p>",
        "icon_url": "https://example.com/acheron.png",
        "modules": [],
    }

    with patch.object(HoYoWikiClient, "get_entry", return_value=sample_page):
        res = try_hoyowiki_adapter("https://wiki.hoyolab.com/pc/hsr/entry/1919")
        assert isinstance(res, WebAdapterResult)
        assert "아케론" in res.title
        assert "갤럭시 레인저" in res.text
        assert res.doc_type == "hoyowiki"


def test_registry_integration():
    url = "https://wiki.hoyolab.com/pc/zzz/entry/29"
    # Classify
    kind = classify(url)
    assert kind == "hoyowiki"

    # Get fetcher
    fetcher_cls = registry.get_fetcher(url)
    assert fetcher_cls is HoyowikiFetcher
    assert issubclass(fetcher_cls, BaseFetcher)

    # Get web adapters
    adapters = registry.get_matching_web_adapters(url)
    assert HoyowikiAdapter in adapters
    assert issubclass(HoyowikiAdapter, BaseWebAdapter)
