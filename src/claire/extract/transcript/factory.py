"""STT 프로바이더 팩토리."""

from __future__ import annotations

from typing import Any

from ...config import get_settings
from .base import TranscriptProvider
from .mock_stt import MockTranscriptProvider


class UnavailableTranscriptProvider:
    """운영 환경에서 STT 프로바이더를 쓸 수 없을 때 mock 전사 대신 실패시킨다."""

    name = "unavailable"

    def __init__(self, reason: str) -> None:
        self.reason = reason

    def transcribe(self, audio_path, *, language=None, timestamps=True):
        from ...config import ProviderUnavailableError

        raise ProviderUnavailableError(f"STT 프로바이더를 쓸 수 없다: {self.reason}")


def get_transcript_provider(settings: Any = None) -> TranscriptProvider:
    """설정된 effective_stt_provider에 따라 적절한 TranscriptProvider 인스턴스 반환."""
    s = settings or get_settings()
    eff = s.effective_stt_provider

    if eff == "unavailable":
        return UnavailableTranscriptProvider(s.provider_unavailable_reason("stt") or "")

    if eff == "gemini":
        from .gemini_stt import GeminiTranscriptProvider

        return GeminiTranscriptProvider(s)

    # mock 또는 미지원 STT 프로바이더 (antigravity 등)는 MockTranscriptProvider 반환
    return MockTranscriptProvider(s)
