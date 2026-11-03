"""Factory for vision transcription providers."""

from __future__ import annotations

from typing import Any

from ...config import get_settings
from .base import VisionTranscriptProvider
from .mock_vision import MockVisionProvider


class UnavailableVisionProvider(VisionTranscriptProvider):
    """운영 환경에서 VLM 프로바이더를 쓸 수 없을 때 mock 전사 대신 실패시킨다."""

    def __init__(self, settings: Any, reason: str, *, error: Exception | None = None) -> None:
        super().__init__(settings)
        self.reason = reason
        self.error = error

    def transcribe_images(self, images, *, title=None, language=None, prompt=None):
        if self.error is not None:
            from ..provider import raise_policy_error

            raise_policy_error(self.error)
        from ...config import ProviderUnavailableError

        raise ProviderUnavailableError(f"VLM 프로바이더를 쓸 수 없다: {self.reason}")


def get_vision_provider(settings: Any = None) -> VisionTranscriptProvider:
    """Return configured VisionTranscriptProvider based on effective_vlm_provider.

    멤버 컨텍스트는 프로바이더가 ``none``이면 ``ProviderNotConfigured``, 등급에서 ``vlm``을 끄면 ``ProviderNotAllowed``를 내는
    자리표시자를 받고, mock으로 대체되지 않는다(설계 §10.2).
    """
    from ...accounts.provider_policy import context_of

    s = settings or get_settings()
    ctx = context_of(s)
    if ctx is not None and not ctx.is_admin:
        from ..provider import member_policy_unavailable

        err = member_policy_unavailable(ctx, "vlm")
        if err is not None:
            return UnavailableVisionProvider(s, str(err), error=err)
        if getattr(s, "effective_vlm_provider", "") != "gemini":
            return UnavailableVisionProvider(s, s.provider_unavailable_reason("vlm") or "vlm disabled")
        from .gemini_vision import GeminiVisionProvider

        return GeminiVisionProvider(s)

    eff = getattr(s, "effective_vlm_provider", "mock")

    if eff == "unavailable":
        return UnavailableVisionProvider(s, s.provider_unavailable_reason("vlm") or "")

    if eff == "gemini":
        from .gemini_vision import GeminiVisionProvider

        return GeminiVisionProvider(s)

    return MockVisionProvider(s)
