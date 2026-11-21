"""STT 프로바이더 계약 및 파싱 단위 테스트."""

import pytest
from pathlib import Path
from claire.extract.transcript import (
    TranscriptProvider,
    TranscriptResult,
    TranscriptSegment,
    get_transcript_provider,
)
from claire.extract.transcript.mock_stt import MockTranscriptProvider


def test_transcript_segment_formatting():
    seg1 = TranscriptSegment(start_sec=65.0, end_sec=70.0, text="테스트")
    assert seg1.format_timestamp() == "01:05"

    seg2 = TranscriptSegment(start_sec=3665.0, end_sec=3670.0, text="긴 영상")
    assert seg2.format_timestamp() == "01:01:05"


def test_mock_transcript_provider(tmp_path):
    dummy_file = tmp_path / "test.mp3"
    dummy_file.write_bytes(b"dummy audio content")

    prov = MockTranscriptProvider()
    res = prov.transcribe(dummy_file, language="ko", timestamps=True)

    assert isinstance(res, TranscriptResult)
    assert len(res.segments) > 0
    assert "[00:00]" in res.full_text
    assert res.provider == "mock"


def test_factory_fallback(monkeypatch):
    from claire.core.config import Settings
    monkeypatch.setenv("CLAIRE_ENABLE_VIDEO_TRANSCRIPTION", "0")
    s = Settings()
    prov = get_transcript_provider(s)
    assert prov.name == "mock"
