"""Unit tests for YouTube fetcher, URL parsing, transcript list, and metadata fallback."""

from __future__ import annotations

import pytest

from claire.config import get_settings
from claire.ingest.fetchers.base import FetchError
from claire.ingest.fetchers.captions import CaptionAcquisition, format_chapters
from claire.ingest.fetchers.youtube import (
    fetch_transcript,
    fetch_video_details,
    fetch_youtube,
    video_id,
)


@pytest.fixture(autouse=True)
def _mock_ytdlp_and_stt_defaults(monkeypatch):
    monkeypatch.setattr(
        "claire.ingest.fetchers.youtube.extract_youtube_ytdlp",
        lambda _url, _langs, _settings: ({}, CaptionAcquisition(status="absent")),
    )
    monkeypatch.setattr(
        "claire.ingest.fetchers.youtube.transcribe_youtube_audio",
        lambda _url, _canonical_url, **_kw: ("", [], 0.0, None, False, False),
    )


def test_video_id_patterns():
    assert video_id("https://www.youtube.com/watch?v=ti9FHqP1i-w") == "ti9FHqP1i-w"
    assert video_id("https://youtu.be/ti9FHqP1i-w?si=xyz123") == "ti9FHqP1i-w"
    assert video_id("https://www.youtube.com/shorts/ti9FHqP1i-w") == "ti9FHqP1i-w"
    assert video_id("https://www.youtube.com/live/ti9FHqP1i-w") == "ti9FHqP1i-w"
    assert video_id("https://www.youtube.com/embed/ti9FHqP1i-w") == "ti9FHqP1i-w"
    assert video_id("https://m.youtube.com/watch?v=ti9FHqP1i-w&t=42s") == "ti9FHqP1i-w"
    assert video_id("https://example.com/not-youtube") is None


def test_fetch_youtube_fallback_when_transcript_fails(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("CLAIRE_RAW_CHAR_BUDGET", "500")
    get_settings.cache_clear()

    # Mock fetch_transcript to return empty (e.g. video without captions or blocked)
    monkeypatch.setattr("claire.ingest.fetchers.youtube.fetch_transcript", lambda vid, **kwargs: "")

    # Mock fetch_video_details
    monkeypatch.setattr(
        "claire.ingest.fetchers.youtube.fetch_video_details",
        lambda vid: {
            "title": "Fallback Video Title",
            "author": "Tech Channel",
            "description": "This is a detailed video description explaining the architecture.",
            "keywords": ["tech", "ai", "cloud"],
        },
    )

    doc = fetch_youtube("https://www.youtube.com/watch?v=ti9FHqP1i-w")
    assert doc.title == "Fallback Video Title"
    assert doc.author == "Tech Channel"
    assert doc.source_type == "youtube"
    assert doc.canonical_url == "https://youtube.com/watch?v=ti9FHqP1i-w"
    assert "[영상 설명]" in doc.raw_text
    assert "This is a detailed video description" in doc.raw_text
    assert "[태그]" in doc.raw_text
    assert "tech, ai, cloud" in doc.raw_text
    assert doc.meta["has_transcript"] is False


def test_fetch_youtube_combined_transcript_and_description(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("CLAIRE_RAW_CHAR_BUDGET", "1000")
    get_settings.cache_clear()

    monkeypatch.setattr(
        "claire.ingest.fetchers.youtube.fetch_transcript",
        lambda vid, **kwargs: "Spoken transcript content goes here.",
    )
    monkeypatch.setattr(
        "claire.ingest.fetchers.youtube.fetch_video_details",
        lambda vid: {
            "title": "Full Video Title",
            "author": "Presenter",
            "description": "Description with links.",
            "keywords": ["keyword1"],
        },
    )

    doc = fetch_youtube("https://www.youtube.com/watch?v=ti9FHqP1i-w")
    assert "[영상 자막]" in doc.raw_text
    assert "Spoken transcript content goes here." in doc.raw_text
    assert "[영상 설명]" in doc.raw_text
    assert "Description with links." in doc.raw_text
    assert doc.meta["has_transcript"] is True


def test_fetch_youtube_invalid_url():
    with pytest.raises(FetchError, match="no youtube video id"):
        fetch_youtube("https://example.com/random")


def test_fetch_youtube_empty_everything_raises(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr("claire.ingest.fetchers.youtube.fetch_transcript", lambda vid, **kw: "")
    monkeypatch.setattr(
        "claire.ingest.fetchers.youtube.fetch_video_details",
        lambda vid: {"title": "", "author": "", "description": "", "keywords": []},
    )

    with pytest.raises(FetchError, match="empty transcript and details"):
        fetch_youtube("https://www.youtube.com/watch?v=ti9FHqP1i-w")


def test_youtube_preferred_languages_config(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("CLAIRE_PREFERRED_LANGUAGES", "ja, es")
    get_settings.cache_clear()
    s = get_settings()
    assert s.effective_preferred_languages == ["ja", "es", "en"]
    assert s.effective_youtube_languages == ["ja", "es", "en"]

    monkeypatch.setenv("CLAIRE_PREFERRED_LANGUAGES", "")
    get_settings.cache_clear()
    s2 = get_settings()
    assert s2.effective_preferred_languages == ["en"]
    assert s2.effective_youtube_languages == ["en"]


def test_fetch_transcript_passes_configured_languages(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("CLAIRE_PREFERRED_LANGUAGES", "ja")
    get_settings.cache_clear()

    captured_langs = []

    class DummyTranscript:
        def fetch(self):
            return [{"text": "Japanese text"}]

    class DummyTranscriptList:
        def find_transcript(self, langs):
            captured_langs.append(list(langs))
            return DummyTranscript()

    class DummyApi:
        def list(self, vid):
            return DummyTranscriptList()

    import youtube_transcript_api
    monkeypatch.setattr(youtube_transcript_api, "YouTubeTranscriptApi", DummyApi)

    text = fetch_transcript("ti9FHqP1i-w")
    assert captured_langs == [["ja", "en"]]
    assert text == "Japanese text"


def test_format_chapters():
    assert format_chapters(None) == ""
    assert format_chapters([]) == ""
    chapters = [
        {"title": "Introduction", "start_time": 0.0, "end_time": 30.0},
        {"title": "Architecture Deep Dive", "start_time": 125.0, "end_time": 3600.0},
        {"title": "Q&A and Wrap Up", "start_time": 3665.0, "end_time": 4000.0},
    ]
    formatted = format_chapters(chapters)
    assert "- [00:00] Introduction" in formatted
    assert "- [02:05] Architecture Deep Dive" in formatted
    assert "- [01:01:05] Q&A and Wrap Up" in formatted


def test_fetch_youtube_ytdlp_recovers_captions_and_chapters(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr("claire.ingest.fetchers.youtube.fetch_transcript", lambda vid, **kw: "")
    monkeypatch.setattr(
        "claire.ingest.fetchers.youtube.fetch_video_details",
        lambda vid: {"title": "", "author": "", "description": "", "keywords": []},
    )

    fake_ytdlp_info = {
        "title": "yt-dlp Discovered Title",
        "uploader": "Advanced Channel",
        "description": "yt-dlp description",
        "tags": ["cloud", "k8s"],
        "duration": 360.0,
        "chapters": [
            {"title": "Start", "start_time": 0.0, "end_time": 60.0},
            {"title": "Main Content", "start_time": 60.0, "end_time": 360.0},
        ],
    }
    fake_caption = CaptionAcquisition(
        status="available",
        text="yt-dlp extracted WebVTT captions text.",
        language="en-us",
        source="manual_caption",
    )

    monkeypatch.setattr(
        "claire.ingest.fetchers.youtube.extract_youtube_ytdlp",
        lambda _url, _langs, _settings: (fake_ytdlp_info, fake_caption),
    )

    doc = fetch_youtube("https://www.youtube.com/watch?v=ti9FHqP1i-w")
    assert doc.title == "yt-dlp Discovered Title"
    assert doc.author == "Advanced Channel"
    assert doc.source_type == "youtube"
    assert doc.canonical_url == "https://youtube.com/watch?v=ti9FHqP1i-w"
    assert "[영상 챕터]" in doc.raw_text
    assert "- [00:00] Start" in doc.raw_text
    assert "- [01:00] Main Content" in doc.raw_text
    assert "[영상 자막]" in doc.raw_text
    assert "yt-dlp extracted WebVTT captions text." in doc.raw_text
    assert doc.meta["has_transcript"] is True
    assert doc.meta["is_stt"] is False
    assert doc.meta["transcript_source"] == "manual_caption"
    assert doc.meta["duration_sec"] == 360.0
    assert len(doc.meta["chapters"]) == 2


def test_fetch_youtube_stt_fallback(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("CLAIRE_ENABLE_VIDEO_TRANSCRIPTION", "1")
    get_settings.cache_clear()

    monkeypatch.setattr("claire.ingest.fetchers.youtube.fetch_transcript", lambda vid, **kw: "")
    monkeypatch.setattr(
        "claire.ingest.fetchers.youtube.fetch_video_details",
        lambda vid: {"title": "No Captions Video", "author": "Live Speaker", "description": "Desc", "keywords": []},
    )

    # yt-dlp has no captions either
    monkeypatch.setattr(
        "claire.ingest.fetchers.youtube.extract_youtube_ytdlp",
        lambda _url, _langs, _settings: (
            {
                "title": "No Captions Video",
                "uploader": "Live Speaker",
                "duration": 180.0,
                "chapters": [],
            },
            CaptionAcquisition(status="absent"),
        ),
    )

    # STT succeeds
    monkeypatch.setattr(
        "claire.ingest.fetchers.youtube.transcribe_youtube_audio",
        lambda _url, _canonical_url, **_kw: (
            "Spoken audio transcribed by Gemini STT.",
            [{"start_sec": 0.0, "end_sec": 10.0, "text": "Spoken audio"}],
            180.0,
            None,
            False,
            False,
        ),
    )

    doc = fetch_youtube("https://www.youtube.com/watch?v=ti9FHqP1i-w")
    assert doc.title == "No Captions Video"
    assert doc.source_type == "youtube"
    assert "[영상 음성 전사 (STT)]" in doc.raw_text
    assert "Spoken audio transcribed by Gemini STT." in doc.raw_text
    assert doc.meta["has_transcript"] is True
    assert doc.meta["is_stt"] is True
    assert doc.meta["transcript_source"] == "stt"
    assert len(doc.meta["transcript_segments"]) == 1


def test_fetch_youtube_stt_disabled_fallback(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("CLAIRE_ENABLE_VIDEO_TRANSCRIPTION", "0")
    get_settings.cache_clear()

    monkeypatch.setattr("claire.ingest.fetchers.youtube.fetch_transcript", lambda vid, **kw: "")
    monkeypatch.setattr(
        "claire.ingest.fetchers.youtube.fetch_video_details",
        lambda vid: {"title": "Mute Video", "author": "Silent Author", "description": "Only description", "keywords": []},
    )

    doc = fetch_youtube("https://www.youtube.com/watch?v=ti9FHqP1i-w")
    assert doc.title == "Mute Video"
    assert doc.source_type == "youtube"
    assert doc.meta["has_transcript"] is False
    assert doc.meta["is_stt"] is False
    assert "[영상 설명]" in doc.raw_text
    assert "Only description" in doc.raw_text
