"""Unit tests for HoYoLAB post collector, web adapter, and VLM fallback."""

from __future__ import annotations

import json

import httpx
from unittest.mock import MagicMock, patch

import pytest

from claire.config import Settings
from claire.plugin_sdk import FetchError, WebAdapterResult
from claire.ingest.registry import registry
from claire.ingest.router import classify, fetch
from claire.ontology.base import Document

_NAMES = {"hoyolab": ("HoYoLabClient", "format_post_to_text", "parse_hoyolab_url", "try_hoyolab_adapter")}

from pathlib import Path

from claire.plugin_sdk.testing import load_plugin

FOLDER = Path(__file__).resolve().parents[1]
MOD = "claire_plugins.ingest_adapter.claire_hoyolab"


@pytest.fixture(autouse=True)
def plugin(monkeypatch):
    """Load claire.hoyolab for each test and use its entries in the registry (patches by module name then reach the router)."""
    loaded = load_plugin(FOLDER)
    loaded.install(monkeypatch)
    for module_name, names in _NAMES.items():
        module = loaded.module(module_name)
        for name in names:
            monkeypatch.setitem(globals(), name, getattr(module, name))
    return loaded


def test_parse_hoyolab_url():
    """Verify URL parsing extracts post_id correctly."""
    assert parse_hoyolab_url("https://www.hoyolab.com/article/46898605") == "46898605"
    assert parse_hoyolab_url("https://hoyolab.com/article/46898605?utm_source=sns") == "46898605"
    assert parse_hoyolab_url("https://m.hoyolab.com/article/123456") == "123456"

    # Non-article URLs
    assert parse_hoyolab_url("https://www.hoyolab.com/home") is None
    assert parse_hoyolab_url("https://wiki.hoyolab.com/pc/genshin/entry/11702") is None
    assert parse_hoyolab_url("https://example.com/article/123") is None


def test_hoyolab_client_io_success(plugin, monkeypatch):
    """Verify HoYoLabClient HTTP I/O operation and JSON payload parsing."""
    mock_payload = {
        "retcode": 0,
        "message": "OK",
        "data": {
            "post": {
                "post": {
                    "post_id": "46898605",
                    "subject": "Guardian Info",
                    "content": "Short description",
                },
                "user": {"nickname": "StarRail"},
                "image_list": [{"url": "https://upload.example.com/card1.jpg"}],
            }
        },
    }
    plugin.mock_network(monkeypatch, lambda request: httpx.Response(200, json=mock_payload))
    client = HoYoLabClient()
    post_data = client.get_post("46898605")
    assert post_data["post"]["subject"] == "Guardian Info"
    assert post_data["user"]["nickname"] == "StarRail"


def test_hoyolab_client_io_error_handling(plugin, monkeypatch):
    """Verify HoYoLabClient handles non-zero retcode and network failures."""
    error_payload = {"retcode": 1001, "message": "Post not found"}
    plugin.mock_network(monkeypatch, lambda request: httpx.Response(200, json=error_payload))
    client = HoYoLabClient()
    with pytest.raises(FetchError, match="HoYoLAB API error for post 999: Post not found"):
        client.get_post("999")


def test_format_post_triggers_vlm_when_text_minimal():
    """Verify format_post_to_text calls VLM transcription when text < 300 chars."""
    mock_post_data = {
        "post": {
            "post_id": "46898605",
            "subject": "[동방의 흙모래] 가디언 정보",
            "content": "",
            "structured_content": json.dumps([
                {"insert": "Short intro.\n"},
                {"insert": {"image": "https://upload.example.com/card1.jpg"}},
            ]),
            "created_at": 1727500000,
        },
        "user": {"nickname": "Honkai: Star Rail"},
        "topics": [{"name": "Honkai: Star Rail"}],
        "image_list": [
            {"url": "https://upload.example.com/card1.jpg", "width": 900, "height": 1200},
            {"url": "https://upload.example.com/card2.jpg", "width": 900, "height": 1200},
        ],
    }

    settings = Settings(enable_vlm_transcription=True, environment="development")
    subject, text_doc, links, anchors, images, meta_extras = format_post_to_text(
        mock_post_data, settings=settings
    )

    assert subject == "[동방의 흙모래] 가디언 정보"
    assert meta_extras["vlm_transcribed"] is True
    assert meta_extras["vlm_images_count"] == 2
    assert "Infographic & Image Transcription" in text_doc
    assert "Card 1" in text_doc
    assert "Card 2" in text_doc


def test_format_post_skips_vlm_when_text_sufficient():
    """Verify format_post_to_text does not invoke VLM when text already exceeds 300 chars."""
    long_content = "This is a detailed analysis of the guardian enemy. " * 15
    mock_post_data = {
        "post": {
            "post_id": "111",
            "subject": "Detailed Article",
            "content": long_content,
            "created_at": 1727500000,
        },
        "user": {"nickname": "Author"},
        "image_list": [{"url": "https://upload.example.com/thumb.jpg"}],
    }

    settings = Settings(enable_vlm_transcription=True)
    with patch("claire_plugins.ingest_adapter.claire_hoyolab.hoyolab.get_vision_provider") as mock_prov_fn:
        _, text_doc, _, _, _, meta_extras = format_post_to_text(
            mock_post_data, settings=settings
        )
        assert meta_extras["vlm_transcribed"] is False
        mock_prov_fn.assert_not_called()


def test_hoyolab_web_adapter_interface():
    """Verify try_hoyolab_adapter returns WebAdapterResult object."""
    mock_post_data = {
        "post": {
            "post_id": "46898605",
            "subject": "Adapter Post",
            "content": "A sufficiently long body content that describes the post in full detail. " * 6,
        },
        "user": {"nickname": "Tester"},
        "image_list": [],
    }

    mock_client = MagicMock()
    mock_client.get_post.return_value = mock_post_data

    res = try_hoyolab_adapter("https://www.hoyolab.com/article/46898605", client=mock_client)
    assert isinstance(res, WebAdapterResult)
    assert res.title == "Adapter Post"
    assert res.doc_type == "hoyolab"
    assert len(res.text) > 300


def test_registry_classification_and_routing():
    """Verify registry classification and router dispatch for HoYoLAB URLs."""
    article_url = "https://www.hoyolab.com/article/46898605"
    wiki_url = "https://wiki.hoyolab.com/pc/genshin/entry/11702"

    assert classify(article_url) == "hoyolab"
    assert classify(wiki_url) == "hoyowiki"

    # Router fetch delegation
    with patch("claire_plugins.ingest_adapter.claire_hoyolab.hoyolab.fetch_hoyolab") as mock_fetch:
        mock_fetch.return_value = Document(
            url=article_url,
            canonical_url=article_url,
            title="Mock Article",
            raw_text="Mock content",
            source_type="hoyolab",
        )
        doc = fetch(article_url)
        assert doc.source_type == "hoyolab"
        mock_fetch.assert_called_once()


def test_hoyolab_language_resolution_and_client_request(plugin, monkeypatch):
    """Verify HoYoLAB language mapping and localized request headers/query."""
    import json
    resolve_hoyolab_language = plugin.module("hoyolab").resolve_hoyolab_language

    s_ko = Settings(preferred_languages="ko")
    assert resolve_hoyolab_language(s_ko) == "ko-kr"

    s_ja = Settings(preferred_languages="ja")
    assert resolve_hoyolab_language(s_ja) == "ja-jp"

    s_en = Settings(preferred_languages="en")
    assert resolve_hoyolab_language(s_en) == "en-us"

    client = HoYoLabClient()
    captured_req = None

    def handler(request):
        nonlocal captured_req
        captured_req = request
        return httpx.Response(200, json={
            "retcode": 0,
            "message": "OK",
            "data": {
                "post": {
                    "post": {"subject": "아하의 대답", "post_id": "46898605"},
                    "user": {"nickname": "붕괴: 스타레일"},
                    "image_list": [],
                }
            }
        })

    plugin.mock_network(monkeypatch, handler)
    post = client.get_post("46898605", lang="ko-kr")
    assert post["post"]["subject"] == "아하의 대답"
    assert captured_req is not None
    assert "lang=ko-kr" in str(captured_req.url)
    assert captured_req.headers.get("x-rpc-language") == "ko-kr"
    assert captured_req.headers.get("accept-language") == "ko-kr"
