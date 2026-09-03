"""Antigravity CLI 및 Gemini 멀티모달 기반 STT 프로바이더."""

from __future__ import annotations

import logging
import re
from pathlib import Path
from typing import Any

from .base import TranscriptProvider, TranscriptResult, TranscriptSegment

logger = logging.getLogger(__name__)

_TS_LINE_RE = re.compile(
    r"^\[?(\d{1,2}:)?(\d{1,2}):(\d{2})\]?\s*(.*)$"
)


def _parse_timestamp_to_sec(ts_match: re.Match) -> tuple[float, str]:
    """[HH:MM:SS] 또는 [MM:SS] 매칭을 초 단위 부동소수점과 텍스트로 변환."""
    hours_str, mins_str, secs_str, text = ts_match.groups()
    hours = int(hours_str.rstrip(":")) if hours_str else 0
    mins = int(mins_str)
    secs = int(secs_str)
    sec_val = float(hours * 3600 + mins * 60 + secs)
    return sec_val, text.strip()


def parse_raw_transcript_lines(raw_text: str) -> list[TranscriptSegment]:
    """타임스탬프가 포함된 텍스트 줄을 파싱하여 TranscriptSegment 목록 생성."""
    segments: list[TranscriptSegment] = []
    lines = [line.strip() for line in raw_text.splitlines() if line.strip()]

    for i, line in enumerate(lines):
        m = _TS_LINE_RE.match(line)
        if m:
            sec_val, text = _parse_timestamp_to_sec(m)
            segments.append(
                TranscriptSegment(
                    start_sec=sec_val,
                    end_sec=sec_val + 5.0,  # 임시 구간 길이
                    text=text or line,
                )
            )
        else:
            # 타임스탬프가 없는 일반 문장
            segments.append(
                TranscriptSegment(
                    start_sec=float(i * 5),
                    end_sec=float((i + 1) * 5),
                    text=line,
                )
            )

    # 구간 종료 시각을 다음 구간 시작 시각으로 보정
    for idx in range(len(segments) - 1):
        if segments[idx + 1].start_sec > segments[idx].start_sec:
            segments[idx].end_sec = segments[idx + 1].start_sec

    return segments


class AntigravityTranscriptProvider(TranscriptProvider):
    """Antigravity CLI 기반 오디오 전사 프로바이더 (STT 미지원 스텁).
    
    Antigravity CLI(agy)는 오디오 바이너리 스트리밍 및 전사 인터페이스를 제공하지 않으므로
    STT 구현이 불가능합니다. 현재 음성 전사는 Google AI Studio의 Gemini('gemini')만 지원됩니다.
    """

    name = "antigravity"

    def __init__(self, settings: Any = None):
        self.settings = settings
        self.model = getattr(settings, "stt_model", "") or getattr(
            settings, "agy_model", "gemini-3.7-flash"
        )
        self.language = getattr(settings, "stt_language", "ko")
        self.timeout = float(getattr(settings, "agy_timeout", 180.0))

    def transcribe(
        self,
        audio_path: str | Path,
        *,
        language: str | None = None,
        timestamps: bool = True,
    ) -> TranscriptResult:
        p = Path(audio_path).resolve()
        if not p.is_file():
            raise FileNotFoundError(f"Audio file not found: {p}")

        raise NotImplementedError(
            "Antigravity CLI does not support audio transcription (STT). "
            "Currently, Google AI Studio Gemini ('gemini') is the only supported production STT provider. "
            "Please configure CLAIRE_STT_PROVIDER=gemini with a valid GEMINI_API_KEY."
        )
