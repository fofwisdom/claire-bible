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
