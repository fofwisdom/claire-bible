"""STT 프로바이더 팩토리."""

from __future__ import annotations

from typing import Any

from ...config import get_settings
from .base import TranscriptProvider
from .mock_stt import MockTranscriptProvider


def get_transcript_provider(settings: Any = None) -> TranscriptProvider:
    """설정된 effective_stt_provider에 따라 적절한 TranscriptProvider 인스턴스 반환."""
    s = settings or get_settings()
    eff = s.effective_stt_provider

    if eff == "antigravity":
        from .antigravity_stt import AntigravityTranscriptProvider

        return AntigravityTranscriptProvider(s)

    # 향후 whisper / groq / gcp 등 추가 확장 지점
    return MockTranscriptProvider(s)
