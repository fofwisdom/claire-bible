"""Unit tests for VLM vision transcription interfaces and providers."""

from __future__ import annotations

import pytest
from unittest.mock import MagicMock, patch

from claire.config import Settings
from claire.extract.vision.base import VisionTranscriptProvider, VisionTranscriptResult
from claire.extract.vision.factory import get_vision_provider
from claire.extract.vision.mock_vision import MockVisionProvider
from claire.extract.vision.gemini_vision import GeminiVisionProvider


def test_mock_vision_provider_contract():
    """Verify MockVisionProvider satisfies VisionTranscriptProvider contract."""
    provider = MockVisionProvider()
    assert isinstance(provider, VisionTranscriptProvider)

    # Empty images
    res_empty = provider.transcribe_images([])
    assert res_empty.images_processed == 0
    assert res_empty.text == ""

    # Multiple images
    images = [
        {"url": "https://example.com/card1.jpg", "desc": "Question Card"},
        {"url": "https://example.com/card2.jpg", "desc": "Answer Card"},
    ]
    res = provider.transcribe_images(images, title="Test Infographic")
    assert res.images_processed == 2
    assert "Test Infographic" in res.text
    assert "Card 1" in res.text
    assert "Card 2" in res.text
    assert res.error is None


def test_vision_factory_selection():
    """Verify factory returns appropriate provider according to settings."""
    # When VLM is disabled
    s_disabled = Settings(enable_vlm_transcription=False)
    assert isinstance(get_vision_provider(s_disabled), MockVisionProvider)

    # When Gemini key is empty, falls back to mock
    s_no_key = Settings(enable_vlm_transcription=True, gemini_api_key="", vlm_provider="gemini")
    assert isinstance(get_vision_provider(s_no_key), MockVisionProvider)

    # When Gemini key is provided
    s_gemini = Settings(enable_vlm_transcription=True, gemini_api_key="valid-key", vlm_provider="gemini")
    assert isinstance(get_vision_provider(s_gemini), GeminiVisionProvider)


def test_gemini_vision_provider_missing_key():
    """Verify GeminiVisionProvider raises ValueError on missing API key."""
    s = Settings(gemini_api_key="")
    provider = GeminiVisionProvider(s)
    with pytest.raises(ValueError, match="GEMINI_API_KEY is not configured"):
        provider.transcribe_images([{"url": "https://example.com/img.jpg"}])


def test_vlm_language_handling_and_korean_prompt():
    """Verify language parameter and Korean translation prompt instructions."""
    # 1. Mock provider language verification
    mock_p = MockVisionProvider()
    res_default = mock_p.transcribe_images([{"url": "https://example.com/c1.jpg"}])
    assert res_default.language == "ko"

    res_en = mock_p.transcribe_images([{"url": "https://example.com/c1.jpg"}], language="en")
    assert res_en.language == "en"

    # 2. Gemini provider prompt verification with Korean default
    s = Settings(gemini_api_key="mock-key", vlm_language="ko")
    provider = GeminiVisionProvider(s)
    assert provider.language == "ko"

    captured_parts = []
    mock_client = MagicMock()
    def mock_generate_content(model, contents):
        nonlocal captured_parts
        captured_parts = contents
        resp = MagicMock()
        resp.text = "### Card 1\n한국어 전사 내용"
        return resp

    mock_client.models.generate_content.side_effect = mock_generate_content

    with patch.object(provider, "_get_client", return_value=mock_client), \
         patch.object(provider, "_download_image", return_value=(b"fake-bytes", "image/jpeg")):
        res = provider.transcribe_images(
            [{"url": "https://example.com/c1.jpg"}],
            title="Aha's Answer",
        )
        assert res.language == "ko"
        assert res.images_processed == 1
        assert "한국어 전사 내용" in res.text

        # Verify system instruction contains Korean translation requirements
        sys_prompt = captured_parts[0]
        assert "Korean (한국어)" in sys_prompt
        assert "translate it faithfully into Korean" in sys_prompt

        # If model returned Korean text, detected language is 'ko' even if 'ja' was requested (prevent false claim)
        res_ja_ko = provider.transcribe_images(
            [{"url": "https://example.com/c1.jpg"}],
            title="Aha's Answer",
            language="ja",
        )
        assert res_ja_ko.language == "ko"
        assert "into ja" in captured_parts[0]

        # When model actually returns Japanese text
        def mock_gen_ja(model, contents):
            resp = MagicMock()
            resp.text = "### カード 1\n日本語の説明とテキストです。"
            return resp

        mock_client.models.generate_content.side_effect = mock_gen_ja
        res_ja = provider.transcribe_images(
            [{"url": "https://example.com/c1.jpg"}],
            title="Aha's Answer",
            language="ja",
        )
        assert res_ja.language == "ja"

        # When model returns English text, language is detected as 'en' instead of falsely claiming 'ko'
        def mock_gen_en(model, contents):
            resp = MagicMock()
            resp.text = "### Card 1\nQuestion: What is the weakness of the Lord Ravager?\nAnswer: Love, hope, and courage."
            return resp

        mock_client.models.generate_content.side_effect = mock_gen_en
        res_en_detected = provider.transcribe_images(
            [{"url": "https://example.com/c1.jpg"}],
            title="Aha's Answer",
            language="ko",
        )
        assert res_en_detected.language == "en"


def test_hoyolab_language_resolution_and_client_request():
    """Verify HoYoLAB language mapping and localized request headers/query."""
    import json
    from claire.ingest.fetchers.hoyolab import HoYoLabClient, resolve_hoyolab_language

    s_ko = Settings(vlm_language="ko")
    assert resolve_hoyolab_language(s_ko) == "ko-kr"

    s_ja = Settings(vlm_language="ja")
    assert resolve_hoyolab_language(s_ja) == "ja-jp"

    s_en = Settings(vlm_language="en")
    assert resolve_hoyolab_language(s_en) == "en-us"

    client = HoYoLabClient()
    captured_req = None

    def mock_urlopen(req, timeout=None):
        nonlocal captured_req
        captured_req = req
        mock_resp = MagicMock()
        mock_resp.read.return_value = json.dumps({
            "retcode": 0,
            "message": "OK",
            "data": {
                "post": {
                    "post": {"subject": "아하의 대답", "post_id": "46898605"},
                    "user": {"nickname": "붕괴: 스타레일"},
                    "image_list": [],
                }
            }
        }).encode("utf-8")
        mock_resp.__enter__.return_value = mock_resp
        return mock_resp

    with patch("urllib.request.urlopen", side_effect=mock_urlopen):
        post = client.get_post("46898605", lang="ko-kr")
        assert post["post"]["subject"] == "아하의 대답"
        assert captured_req is not None
        assert "lang=ko-kr" in captured_req.full_url
        assert captured_req.headers.get("X-rpc-language") == "ko-kr"
        assert captured_req.headers.get("Accept-language") == "ko-kr"
