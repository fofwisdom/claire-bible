"""Factory for vision transcription providers."""

from __future__ import annotations

from typing import Any

from ...config import get_settings
from .base import VisionTranscriptProvider
from .mock_vision import MockVisionProvider


def get_vision_provider(settings: Any = None) -> VisionTranscriptProvider:
    """Return configured VisionTranscriptProvider based on effective_vlm_provider."""
    s = settings or get_settings()
    eff = getattr(s, "effective_vlm_provider", "mock")

    if eff == "gemini":
        from .gemini_vision import GeminiVisionProvider

        return GeminiVisionProvider(s)

    return MockVisionProvider(s)
