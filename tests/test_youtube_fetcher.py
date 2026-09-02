"""Unit tests for YouTube fetcher, URL parsing, transcript list, and metadata fallback."""

from __future__ import annotations

import pytest

from claire.config import get_settings
from claire.ingest.fetchers.base import FetchError
from claire.ingest.fetchers.youtube import (
    fetch_transcript,
    fetch_video_details,
    fetch_youtube,
    video_id,
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
