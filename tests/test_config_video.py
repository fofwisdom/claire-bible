"""비디오 전사 환경변수 및 설정 단위 테스트."""

import pytest
from claire.config import Settings, find_ffmpeg_executable


def test_config_video_defaults(monkeypatch):
    monkeypatch.delenv("CLAIRE_ENABLE_VIDEO_TRANSCRIPTION", raising=False)
    monkeypatch.delenv("CLAIRE_STT_PROVIDER", raising=False)
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    s = Settings(gemini_api_key="")
    assert s.enable_video_transcription is True
    assert s.stt_provider == "gemini"
    assert s.effective_stt_provider == "mock"  # No GEMINI_API_KEY -> mock
    assert s.stt_language == "ko"
    assert s.ffmpeg_bin == "ffmpeg"
    assert s.ytdlp_extractor_args == "generic:impersonate"


def test_config_stt_provider_antigravity_no_hijack(monkeypatch, tmp_path):
    """CLAIRE_STT_PROVIDER=antigravity 설정 시 GEMINI_API_KEY가 있어도 gemini로 하이잭되지 않고 mock으로 처리되어야 함."""
    monkeypatch.setenv("CLAIRE_ENABLE_VIDEO_TRANSCRIPTION", "1")
    monkeypatch.setenv("CLAIRE_STT_PROVIDER", "antigravity")
    monkeypatch.setenv("GEMINI_API_KEY", "test-api-key")

    s = Settings()
    assert s.stt_provider == "antigravity"
    # 하이잭 없이 mock으로 안전 폴백
    assert s.effective_stt_provider == "mock"

    from claire.extract.transcript.factory import get_transcript_provider
    from claire.extract.transcript.mock_stt import MockTranscriptProvider
    from claire.extract.transcript.antigravity_stt import AntigravityTranscriptProvider

    provider = get_transcript_provider(s)
    assert isinstance(provider, MockTranscriptProvider)

    # AntigravityTranscriptProvider 직접 호출 시 NotImplementedError 발생 확인
    agy_provider = AntigravityTranscriptProvider(s)
    fake_audio = tmp_path / "sample.mp3"
    fake_audio.write_bytes(b"dummy")
    with pytest.raises(NotImplementedError) as excinfo:
        agy_provider.transcribe(fake_audio)
    assert "does not support audio transcription" in str(excinfo.value)


def test_config_video_toggle(monkeypatch):
    monkeypatch.setenv("CLAIRE_ENABLE_VIDEO_TRANSCRIPTION", "0")
    s = Settings()
    assert s.enable_video_transcription is False
    assert s.effective_stt_provider == "mock"

    monkeypatch.setenv("CLAIRE_ENABLE_VIDEO_TRANSCRIPTION", "1")
    s2 = Settings()
    assert s2.enable_video_transcription is True


def test_config_video_invalid_toggle(monkeypatch):
    monkeypatch.setenv("CLAIRE_ENABLE_VIDEO_TRANSCRIPTION", "invalid_val")
    with pytest.raises(ValueError):
        Settings()


def test_find_ffmpeg_executable():
    # find_ffmpeg_executable should not raise error and return string or None
    res = find_ffmpeg_executable("non_existent_binary_xyz_123")
    assert res is None
