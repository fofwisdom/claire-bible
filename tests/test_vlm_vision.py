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

        # Now test with explicit language="ja"
        res_ja = provider.transcribe_images(
            [{"url": "https://example.com/c1.jpg"}],
            title="Aha's Answer",
            language="ja",
        )
        assert res_ja.language == "ja"
        assert "into ja" in captured_parts[0]
