"""비디오 전사 환경변수 및 설정 단위 테스트."""

import pytest
from claire.config import Settings, find_ffmpeg_executable


def test_config_video_defaults(monkeypatch):
    monkeypatch.delenv("CLAIRE_ENABLE_VIDEO_TRANSCRIPTION", raising=False)
    monkeypatch.delenv("CLAIRE_STT_PROVIDER", raising=False)
    s = Settings()
    assert s.enable_video_transcription is True
    assert s.stt_provider == "antigravity"
    assert s.stt_language == "ko"
    assert s.ffmpeg_bin == "ffmpeg"
    assert s.ytdlp_extractor_args == "generic:impersonate"


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
