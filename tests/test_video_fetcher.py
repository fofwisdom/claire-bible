"""비디오 페처 및 라우팅 단위 테스트."""

import pytest
from claire.ingest.router import classify
from claire.ingest.fetchers.video import (
    resolve_video_target_url,
    fetch_video,
    parse_ytdlp_extractor_args,
)
from claire.ontology.base import Document


def test_parse_ytdlp_extractor_args():
    parsed = parse_ytdlp_extractor_args("generic:impersonate")
    assert "generic" in parsed
    assert "impersonate" in parsed["generic"]

    empty = parse_ytdlp_extractor_args("")
    assert empty == {}


def test_classify_video_urls():
    assert classify("https://www.vmware.com/explore/video/6403821753112") == "video"
    assert classify("https://players.brightcove.net/6164421911001/default_default/index.html?videoId=6403821753112") == "video"
    assert classify("https://vimeo.com/12345678") == "video"
    assert classify("https://example.com/stream/presentation.mp4") == "video"
    assert classify("https://example.com/stream/manifest.m3u8") == "video"


def test_resolve_video_target_url():
    vm_url = "https://www.vmware.com/explore/video/6403821753112"
    resolved = resolve_video_target_url(vm_url)
    assert "players.brightcove.net/6164421911001/default_default/index.html?videoId=6403821753112" in resolved

    html_snippet = '<div data-account="12345" data-video-id="67890"></div>'
    resolved_html = resolve_video_target_url("https://example.com/page", html_snippet)
    assert "players.brightcove.net/12345/default_default/index.html?videoId=67890" in resolved_html

    html_video = '<video src="https://cdn.example.com/sample.mp4"></video>'
    assert resolve_video_target_url("https://example.com", html_video) == "https://cdn.example.com/sample.mp4"


def test_fetch_video_disabled_stt(monkeypatch):
    monkeypatch.setenv("CLAIRE_ENABLE_VIDEO_TRANSCRIPTION", "0")
    from claire.config import get_settings
    get_settings.cache_clear()

    doc = fetch_video("https://www.vmware.com/explore/video/6403821753112")
    assert isinstance(doc, Document)
    assert doc.source_type == "video"
    assert "CLOB1244LV" in doc.title or "6403821753112" in doc.url
    assert "비디오 음성 전사 기능이 비활성화되어 있습니다" in doc.raw_text
    assert doc.meta["has_transcript"] is False


def test_fetch_video_with_mock_stt(monkeypatch, tmp_path):
    monkeypatch.setenv("CLAIRE_ENABLE_VIDEO_TRANSCRIPTION", "1")
    monkeypatch.setenv("CLAIRE_STT_PROVIDER", "mock")
    from claire.config import get_settings
    get_settings.cache_clear()

    doc = fetch_video("https://www.vmware.com/explore/video/6403821753112")
    assert isinstance(doc, Document)
    assert doc.source_type == "video"
    assert doc.title != ""
    assert doc.meta["duration_sec"] > 0
