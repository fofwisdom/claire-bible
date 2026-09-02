"""STT 프로바이더 계약 및 파싱 단위 테스트."""

import pytest
from pathlib import Path
from claire.extract.transcript import (
    TranscriptProvider,
    TranscriptResult,
    TranscriptSegment,
    get_transcript_provider,
)
from claire.extract.transcript.antigravity_stt import parse_raw_transcript_lines
from claire.extract.transcript.mock_stt import MockTranscriptProvider


def test_transcript_segment_formatting():
    seg1 = TranscriptSegment(start_sec=65.0, end_sec=70.0, text="테스트")
    assert seg1.format_timestamp() == "01:05"

    seg2 = TranscriptSegment(start_sec=3665.0, end_sec=3670.0, text="긴 영상")
    assert seg2.format_timestamp() == "01:01:05"


def test_parse_raw_transcript_lines():
    raw = """
    [00:00] VMware Cloud Foundation 소개를 시작합니다.
    [01:15] Private AI Cloud 아키텍처의 핵심 원리.
    [01:05:30] 마지막 정리 및 Q&A 세션입니다.
    """
    segments = parse_raw_transcript_lines(raw)
    assert len(segments) == 3
    assert segments[0].start_sec == 0.0
    assert "VMware Cloud Foundation" in segments[0].text
    assert segments[1].start_sec == 75.0
    assert "Private AI Cloud" in segments[1].text
    assert segments[2].start_sec == 3930.0


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
    from claire.config import Settings
    monkeypatch.setenv("CLAIRE_ENABLE_VIDEO_TRANSCRIPTION", "0")
    s = Settings()
    prov = get_transcript_provider(s)
    assert prov.name == "mock"
