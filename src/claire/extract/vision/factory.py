"""Factory for vision transcription providers."""

from __future__ import annotations

from typing import Any

from ...config import get_settings
from .base import VisionTranscriptProvider
from .mock_vision import MockVisionProvider


class UnavailableVisionProvider(VisionTranscriptProvider):
    """운영 환경에서 VLM 프로바이더를 쓸 수 없을 때 mock 전사 대신 실패시킨다."""

    def __init__(self, settings: Any, reason: str) -> None:
        super().__init__(settings)
        self.reason = reason

    def transcribe_images(self, images, *, title=None, language=None, prompt=None):
        from ...config import ProviderUnavailableError

        raise ProviderUnavailableError(f"VLM 프로바이더를 쓸 수 없다: {self.reason}")


def get_vision_provider(settings: Any = None) -> VisionTranscriptProvider:
    """Return configured VisionTranscriptProvider based on effective_vlm_provider."""
    s = settings or get_settings()
    eff = getattr(s, "effective_vlm_provider", "mock")

    if eff == "unavailable":
        return UnavailableVisionProvider(s, s.provider_unavailable_reason("vlm") or "")

    if eff == "gemini":
        from .gemini_vision import GeminiVisionProvider

        return GeminiVisionProvider(s)

    return MockVisionProvider(s)
