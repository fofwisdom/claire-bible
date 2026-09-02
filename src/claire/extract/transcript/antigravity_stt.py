"""Antigravity CLI 및 Gemini 멀티모달 기반 STT 프로바이더."""

from __future__ import annotations

import json
import logging
import re
import subprocess
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
    """Antigravity CLI (agy) 및 Gemini 멀티모달 오디오 전사 프로바이더."""

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

        target_lang = language or self.language or "ko"

        # 1. Gemini API 키가 직접 설정되어 있는 경우 google-genai SDK 우선 시도
        api_key = getattr(self.settings, "gemini_api_key", None)
        if api_key:
            try:
                return self._transcribe_via_gemini_sdk(p, target_lang, timestamps)
            except Exception as e:
                logger.warning("Gemini SDK audio transcription failed: %s, trying agy CLI", e)

        # 2. Antigravity CLI (agy) 서브프로세스 호출
        return self._transcribe_via_agy_cli(p, target_lang, timestamps)

    def _transcribe_via_gemini_sdk(
        self,
        audio_file: Path,
        language: str,
        timestamps: bool,
    ) -> TranscriptResult:
        from google import genai
        from google.genai import types

        client = genai.Client(api_key=self.settings.gemini_api_key)
        uploaded = client.files.upload(file=str(audio_file))

        prompt = (
            f"You are an expert audio transcription system. Transcribe the entire speech in the provided audio file "
            f"accurately in its spoken language. Preserve technical terms, acronyms, and proper nouns correctly. "
            f"Format the output as chronological lines with timestamps, e.g. '[MM:SS] Transcribed sentence...'. "
            f"Do not omit any sections."
        )

        try:
            model_name = self.model or "gemini-2.5-flash"
            resp = client.models.generate_content(
                model=model_name,
                contents=[uploaded, prompt],
            )
            raw_text = (resp.text or "").strip()
        finally:
            try:
                client.files.delete(name=uploaded.name)
            except Exception:  # noqa: BLE001
                pass

        segments = parse_raw_transcript_lines(raw_text)
        max_duration = max((s.end_sec for s in segments), default=0.0)

        return TranscriptResult(
            full_text=raw_text,
            segments=segments,
            language=language,
            duration_sec=max_duration,
            provider="antigravity",
            model=model_name,
        )

    def _transcribe_via_agy_cli(
        self,
        audio_file: Path,
        language: str,
        timestamps: bool,
    ) -> TranscriptResult:
        from ...config import find_agy_executable

        raw_bin = getattr(self.settings, "agy_bin", "agy")
        agy_bin = find_agy_executable(raw_bin) or raw_bin

        prompt = (
            f"Transcribe the spoken audio from the file '{audio_file}' completely with timestamps in '[MM:SS] text' format. "
            f"Preserve all technical terminology accurately. Return plain text lines."
        )

        cmd = [
            agy_bin,
            "-p",
            prompt,
            "--output-format",
            "text",
            "--disable-slash-commands",
            "--dangerously-skip-permissions",
        ]
        if self.model:
            cmd.extend(["--model", self.model])

        proc = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=self.timeout,
            check=False,
        )

        if proc.returncode != 0:
            err = (proc.stderr or proc.stdout or "").strip()
            raise RuntimeError(f"agy audio transcription failed (code {proc.returncode}): {err[:300]}")

        raw_text = proc.stdout.strip()
        segments = parse_raw_transcript_lines(raw_text)
        max_duration = max((s.end_sec for s in segments), default=0.0)

        return TranscriptResult(
            full_text=raw_text,
            segments=segments,
            language=language,
            duration_sec=max_duration,
            provider="antigravity",
            model=self.model,
        )
