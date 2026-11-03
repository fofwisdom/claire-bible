"""STT 프로바이더 팩토리."""

from __future__ import annotations

from typing import Any

from ...config import get_settings
from .base import TranscriptProvider
from .mock_stt import MockTranscriptProvider


class UnavailableTranscriptProvider:
    """운영 환경에서 STT 프로바이더를 쓸 수 없을 때 mock 전사 대신 실패시킨다."""

    name = "unavailable"

    def __init__(self, reason: str, *, error: Exception | None = None) -> None:
        self.reason = reason
        self.error = error

    def transcribe(self, audio_path, *, language=None, timestamps=True):
        if self.error is not None:
            from ..provider import raise_policy_error

            raise_policy_error(self.error)
        from ...config import ProviderUnavailableError

        raise ProviderUnavailableError(f"STT 프로바이더를 쓸 수 없다: {self.reason}")


def get_transcript_provider(settings: Any = None) -> TranscriptProvider:
    """설정된 effective_stt_provider에 따라 적절한 TranscriptProvider 인스턴스 반환.

    멤버 컨텍스트는 프로바이더가 ``none``이면 ``ProviderNotConfigured``, 등급에서 ``stt``를 끄면 ``ProviderNotAllowed``를 내는
    자리표시자를 받고, mock으로 대체되지 않는다(설계 §10.2).
    """
    from ...accounts.provider_policy import context_of

    s = settings or get_settings()
    ctx = context_of(s)
    if ctx is not None and not ctx.is_admin:
        from ..provider import member_policy_unavailable

        err = member_policy_unavailable(ctx, "stt")
        if err is not None:
            return UnavailableTranscriptProvider(str(err), error=err)
        if s.effective_stt_provider != "gemini":
            return UnavailableTranscriptProvider(s.provider_unavailable_reason("stt") or "stt disabled")
        from .gemini_stt import GeminiTranscriptProvider

        return GeminiTranscriptProvider(s)

    eff = s.effective_stt_provider

    if eff == "unavailable":
        return UnavailableTranscriptProvider(s.provider_unavailable_reason("stt") or "")

    if eff == "gemini":
        from .gemini_stt import GeminiTranscriptProvider

        return GeminiTranscriptProvider(s)

    # mock 또는 미지원 STT 프로바이더 (antigravity 등)는 MockTranscriptProvider 반환
    return MockTranscriptProvider(s)
