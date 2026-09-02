"""테스트 및 개발용 결정론적 Mock STT 프로바이더."""

from __future__ import annotations

from pathlib import Path

from .base import TranscriptProvider, TranscriptResult, TranscriptSegment


class MockTranscriptProvider(TranscriptProvider):
    """결정론적 더미 전사 프로바이더 (API 키 및 외부 호출 불필요)."""

    name = "mock"

    def __init__(self, settings=None):
        self.settings = settings

    def transcribe(
        self,
        audio_path: str | Path,
        *,
        language: str | None = None,
        timestamps: bool = True,
    ) -> TranscriptResult:
        p = Path(audio_path)
        base_name = p.stem or "audio"
        lang = language or "ko"

        segments = [
            TranscriptSegment(
                start_sec=0.0,
                end_sec=5.0,
                text=f"[mock transcript] {base_name} 세션 도입부입니다.",
            ),
            TranscriptSegment(
                start_sec=5.0,
                end_sec=15.0,
                text="클라우드 인프라와 Private AI 아키텍처에 대한 핵심 발표를 진행합니다.",
            ),
            TranscriptSegment(
                start_sec=15.0,
                end_sec=30.0,
                text="자동화된 오케스트레이션과 엔터프라이즈 데이터 거버넌스가 결합됩니다.",
            ),
        ]

        if timestamps:
            full_text = "\n".join(
                f"[{s.format_timestamp()}] {s.text}" for s in segments
            )
        else:
            full_text = " ".join(s.text for s in segments)

        return TranscriptResult(
            full_text=full_text,
            segments=segments,
            language=lang,
            duration_sec=30.0,
            provider="mock",
            model="mock-stt-1",
        )
