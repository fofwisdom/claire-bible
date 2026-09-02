"""Gemini STT 프로바이더 (gemini-3.5-transcribe) 및 청킹/예산 단위 테스트."""

import time
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
from claire.config import Settings, get_settings
from claire.extract.transcript.base import TranscriptResult, TranscriptSegment
from claire.extract.transcript.factory import get_transcript_provider
from claire.extract.transcript.gemini_stt import (
    GeminiTranscriptProvider,
    get_audio_duration_sec,
    parse_raw_transcript_lines,
    split_audio_into_chunks,
)
from claire.ingest.fetchers.video import fetch_video, is_valid_speech_vtt
from claire.ontology.base import Document
from google.genai import types


def test_parse_raw_transcript_lines_with_offset():
    raw = """[00:05] 안녕하세요 VMware Explore 세션입니다.
[01:10] VCF 9.1 최적화 아키텍처를 설명합니다.
[02:30] GPU 리소스 소비를 줄이는 방안입니다."""

    # 오프셋 0초
    segs = parse_raw_transcript_lines(raw, offset_sec=0.0)
    assert len(segs) == 3
    assert segs[0].start_sec == 5.0
    assert segs[0].format_timestamp() == "00:05"
    assert "VMware Explore" in segs[0].text
    assert segs[1].start_sec == 70.0
    assert segs[1].format_timestamp() == "01:10"
    assert segs[2].start_sec == 150.0

    # 오프셋 900초 (15분 청크 2번째 구간)
    segs_offset = parse_raw_transcript_lines(raw, offset_sec=900.0)
    assert len(segs_offset) == 3
    assert segs_offset[0].start_sec == 905.0
    assert segs_offset[0].format_timestamp() == "15:05"
    assert segs_offset[1].start_sec == 970.0
    assert segs_offset[1].format_timestamp() == "16:10"


def test_gemini_provider_routing(monkeypatch):
    monkeypatch.setenv("CLAIRE_ENABLE_VIDEO_TRANSCRIPTION", "1")
    monkeypatch.setenv("CLAIRE_STT_PROVIDER", "gemini")
    monkeypatch.setenv("CLAIRE_STT_MODEL", "gemini-3.5-transcribe")
    monkeypatch.setenv("GEMINI_API_KEY", "test-key-fake")

    s = Settings()
    assert s.effective_stt_provider == "gemini"
    assert s.stt_model == "gemini-3.5-transcribe"

    provider = get_transcript_provider(s)
    assert isinstance(provider, GeminiTranscriptProvider)
    assert provider.model == "gemini-3.5-transcribe"
    assert "VCF 9.1" in provider.custom_vocabulary


def test_gemini_provider_transcribe_mocked(tmp_path, monkeypatch):
    # 가상 오디오 파일 생성
    fake_audio = tmp_path / "test.mp3"
    fake_audio.write_bytes(b"\xFF\xFB\x90\x00" * 1000)

    s = Settings(
        enable_video_transcription=True,
        stt_provider="gemini",
        stt_model="gemini-3.5-transcribe",
        gemini_api_key="fake-key",
        video_chunk_duration_sec=900,
    )

    provider = GeminiTranscriptProvider(s)

    mock_client = MagicMock()
    mock_file = MagicMock()
    mock_file.name = "files/test_audio_123"
    mock_file.state = types.FileState.ACTIVE

    mock_client.files.upload.return_value = mock_file
    mock_client.files.get.return_value = mock_file

    mock_resp = MagicMock()
    mock_resp.output_text = "[00:10] Private AI Services and GPU consumption in VCF 9.1.\n[00:45] RoCE network configuration."
    mock_resp.text = mock_resp.output_text
    mock_resp.steps = []
    mock_client.interactions.create.return_value = mock_resp
    mock_client.models.generate_content.return_value = mock_resp

    with patch.object(provider, "_get_genai_client", return_value=mock_client), \
         patch("claire.extract.transcript.gemini_stt.get_audio_duration_sec", return_value=60.0):
        result = provider.transcribe(fake_audio, language="ko")

    assert isinstance(result, TranscriptResult)
    assert result.provider == "gemini"
    assert result.model == "gemini-3.5-transcribe"
    assert len(result.segments) == 2
    assert "Private AI Services" in result.full_text
    assert mock_client.files.upload.called
    mock_client.files.delete.assert_called_with(name="files/test_audio_123")


def test_is_valid_speech_vtt():
    assert is_valid_speech_vtt("") is False
    assert is_valid_speech_vtt("sprite.jpg#xywh=0,0,160,90") is False
    assert is_valid_speech_vtt("thumbnails.vtt#xywh=10,20,30,40") is False
    valid_text = "WEBVTT\n\n00:00:01.000 --> 00:00:04.000\nWelcome to VMware Cloud Foundation session."
    assert is_valid_speech_vtt(valid_text) is True


def test_video_character_budget_protection(monkeypatch):
    monkeypatch.setenv("CLAIRE_RAW_CHAR_BUDGET", "500")
    monkeypatch.setenv("CLAIRE_VIDEO_MAX_EXTRACT_CHARS", "10000")
    from claire.extract.table_budget import slice_document_text

    long_transcript = "가" * 5000  # 5,000자
    # 일반 웹 문서는 500자에서 잘림
    web_text, web_trunc, _, _ = slice_document_text(long_transcript, 500)
    assert web_trunc is True
    assert len(web_text) < 1000

    # 비디오 문서는 video_max_extract_chars(10,000자) 적용 시 원본 5,000자 100% 보존
    vid_text, vid_trunc, _, _ = slice_document_text(long_transcript, 10000)
    assert vid_trunc is False
    assert len(vid_text) == 5000


def test_parse_retry_delay():
    from claire.extract.transcript.gemini_stt import _parse_retry_delay

    err1 = Exception("429 RESOURCE_EXHAUSTED. Please retry in 29.191573879s.")
    assert abs(_parse_retry_delay(err1) - 29.191573879) < 0.01

    err2 = Exception("details: [{'@type': '...RetryInfo', 'retryDelay': '45s'}]")
    assert _parse_retry_delay(err2) == 45.0

    err3 = Exception("Unknown 429 error")
    assert _parse_retry_delay(err3, default_delay=30.0) == 30.0


def test_gemini_provider_429_retry_success(tmp_path):
    fake_audio = tmp_path / "test.mp3"
    fake_audio.write_bytes(b"dummy")

    s = Settings(
        enable_video_transcription=True,
        stt_provider="gemini",
        stt_model="gemini-3.5-transcribe",
        gemini_api_key="fake-key",
    )
    provider = GeminiTranscriptProvider(s)

    mock_client = MagicMock()
    mock_file = MagicMock()
    mock_file.name = "files/test_audio"
    mock_file.state = types.FileState.ACTIVE
    mock_client.files.upload.return_value = mock_file
    mock_client.files.get.return_value = mock_file

    mock_resp = MagicMock()
    mock_resp.output_text = "[00:01] Hello from retry"
    mock_resp.text = mock_resp.output_text
    mock_resp.steps = []

    # 1회차 429 오류, 2회차 성공
    err_429 = Exception("429 RESOURCE_EXHAUSTED. Please retry in 1.5s.")
    mock_client.interactions.create.side_effect = [err_429, mock_resp]
    mock_client.models.generate_content.side_effect = [err_429, mock_resp]

    with (
        patch.object(provider, "_get_genai_client", return_value=mock_client),
        patch("claire.extract.transcript.gemini_stt.get_audio_duration_sec", return_value=30.0),
        patch("time.sleep") as mock_sleep,
    ):
        res = provider.transcribe(fake_audio)
        assert res.full_text == "[00:01] Hello from retry"
        # 1.5s + 2.0s = 3.5s sleep 호출 확인
        mock_sleep.assert_called_with(3.5)


def test_gemini_provider_429_max_retries_exhausted_raises(tmp_path):
    fake_audio = tmp_path / "test.mp3"
    fake_audio.write_bytes(b"dummy")

    s = Settings(
        enable_video_transcription=True,
        stt_provider="gemini",
        stt_model="gemini-3.5-transcribe",
        gemini_api_key="fake-key",
    )
    provider = GeminiTranscriptProvider(s)

    mock_client = MagicMock()
    mock_file = MagicMock()
    mock_file.name = "files/test_audio"
    mock_file.state = types.FileState.ACTIVE
    mock_client.files.upload.return_value = mock_file
    mock_client.files.get.return_value = mock_file

    err_429 = Exception("429 RESOURCE_EXHAUSTED. Please retry in 1s.")
    mock_client.interactions.create.side_effect = err_429
    mock_client.models.generate_content.side_effect = err_429

    with (
        patch.object(provider, "_get_genai_client", return_value=mock_client),
        patch("claire.extract.transcript.gemini_stt.get_audio_duration_sec", return_value=30.0),
        patch("time.sleep"),
    ):
        with pytest.raises(Exception) as exc_info:
            provider.transcribe(fake_audio)
        assert "RESOURCE_EXHAUSTED" in str(exc_info.value)
        # 5회 재시도 모두 gemini-3.5-transcribe 모델로만 호출되었는지 확인
        assert mock_client.interactions.create.call_count == 5
        for call_args in mock_client.interactions.create.call_args_list:
            assert call_args[1]["model"] == "gemini-3.5-transcribe"

