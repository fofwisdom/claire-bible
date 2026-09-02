"""음성 전사(STT) 프로바이더 인터페이스 및 모델 정의."""

from __future__ import annotations

from pathlib import Path
from typing import Protocol

from pydantic import BaseModel, Field


class TranscriptSegment(BaseModel):
    """타임스탬프 구간별 전사 자막 세그먼트."""

    start_sec: float = 0.0
    end_sec: float = 0.0
    text: str = ""

    def format_timestamp(self) -> str:
        """[MM:SS] 또는 [HH:MM:SS] 포맷 타임스탬프 문자열."""
        total_sec = int(self.start_sec)
        hours = total_sec // 3600
        mins = (total_sec % 3600) // 60
        secs = total_sec % 60
        if hours > 0:
            return f"{hours:02d}:{mins:02d}:{secs:02d}"
        return f"{mins:02d}:{secs:02d}"


class TranscriptResult(BaseModel):
    """오디오 전사 최종 결과."""

    full_text: str = Field(
        default="",
        description="전체 전사 텍스트 (타임스탬프 또는 순수 문장 형태)",
    )
    segments: list[TranscriptSegment] = Field(
        default_factory=list,
        description="타임스탬프 구간별 자막 목록",
    )
    language: str = "ko"
    duration_sec: float = 0.0
    provider: str = ""
    model: str = ""


class TranscriptProvider(Protocol):
    """STT/전사 프로바이더 공통 프로토콜."""

    name: str

    def transcribe(
        self,
        audio_path: str | Path,
        *,
        language: str | None = None,
        timestamps: bool = True,
    ) -> TranscriptResult:
        """오디오 파일을 입력받아 텍스트 및 타임스탬프 자막 세그먼트를 반환한다."""
        ...
