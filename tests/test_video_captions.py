"""CC 우선 비디오 적재의 언어 선택·다운로드·STT 차단 회귀 테스트."""

from __future__ import annotations

import sys
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from claire.config import Settings
from claire.ingest.fetchers.base import FetchError
from claire.ingest.fetchers.captions import (
    acquire_caption,
    normalize_language_tag,
    select_caption_candidates,
)
from claire.ingest.fetchers.video import fetch_video


VALID_VTT = """WEBVTT

00:03.160 --> 00:07.140
Welcome to the VMware Cloud Foundation session.

00:08.120 --> 00:11.420
This caption is provided by the publisher.
"""


@pytest.fixture(autouse=True)
def _presentation_absent(monkeypatch):
    monkeypatch.setattr(
        "claire.ingest.fetchers.video.discover_presentations",
        lambda _url: SimpleNamespace(status="absent", candidates=[], error=None),
    )


def test_language_normalization_and_direct_https_manual_caption_selection():
    info = {
        "subtitles": {
            "en-US": [
                {
                    "url": "http://cdn.example/captions/rendition.m3u8?token=same",
                    "ext": "vtt",
                    "protocol": "m3u8_native",
                },
                {
                    "url": "http://cdn.example/captions/text.vtt?token=same",
                    "ext": "vtt",
                },
                {
                    "url": "https://cdn.example/captions/text.vtt?token=same",
                    "ext": "vtt",
                },
            ]
        },
        "automatic_captions": {
            "en-US": [{"data": VALID_VTT, "ext": "vtt"}],
        },
    }

    candidates = select_caption_candidates(info, ["ko", "en"])

    assert normalize_language_tag("EN_us") == "en-us"
    assert candidates[0].language == "en-US"
    assert candidates[0].source == "manual_caption"
    assert candidates[0].track["url"].startswith("https://")
    assert sum("text.vtt" in str(c.track.get("url")) for c in candidates) == 1


def test_preferred_language_precedes_caption_generation_kind():
    info = {
        "subtitles": {"en-US": [{"data": VALID_VTT, "ext": "vtt"}]},
        "automatic_captions": {
            "ko-KR": [{"data": VALID_VTT.replace("Welcome", "환영합니다"), "ext": "vtt"}]
        },
    }

    candidates = select_caption_candidates(info, ["ko", "en"])

    assert candidates[0].language == "ko-KR"
    assert candidates[0].source == "automatic_caption"


def test_inline_caption_is_used_without_downloader():
    ydl = Mock()
    info = {"subtitles": {"en-US": [{"data": VALID_VTT, "ext": "vtt"}]}}

    result = acquire_caption(info, ["en"], ydl)

    assert result.status == "available"
    assert result.source == "manual_caption"
    assert result.language == "en-US"
    assert result.content_hash
    ydl.dl.assert_not_called()


def test_url_caption_uses_ytdlp_downloader_and_parent_headers():
    captured: dict = {}

    class FakeYDL:
        def dl(self, filename, track, subtitle=False):
            captured.update({"track": track, "subtitle": subtitle})
            Path(filename).write_text(VALID_VTT, encoding="utf-8")

    info = {
        "http_headers": {"Referer": "https://video.example/"},
        "subtitles": {
            "en-US": [
                {
                    "url": "https://cdn.example/text.vtt?fastly_token=secret",
                    "ext": "vtt",
                }
            ]
        },
    }

    result = acquire_caption(info, ["en"], FakeYDL())

    assert result.status == "available"
    assert result.text.startswith("WEBVTT")
    assert captured["subtitle"] is True
    assert captured["track"]["http_headers"] == info["http_headers"]


def test_caption_download_failure_exposes_only_error_type():
    class FailingYDL:
        def dl(self, *_args, **_kwargs):
            raise RuntimeError("https://cdn.example/text.vtt?fastly_token=do-not-store")

    info = {
        "subtitles": {
            "en-US": [
                {
                    "url": "https://cdn.example/text.vtt?fastly_token=do-not-store",
                    "ext": "vtt",
                }
            ]
        }
    }

    result = acquire_caption(info, ["en"], FailingYDL())

    assert result.status == "download_failed"
    assert result.error == "RuntimeError"
    assert "fastly_token" not in (result.error or "")


def test_downloaded_invalid_caption_remains_an_stt_fallback():
    class PartiallyFailingYDL:
        def dl(self, *_args, **_kwargs):
            raise TimeoutError("signed URL must not escape")

    info = {
        "subtitles": {
            "en-US": [
                {"data": "WEBVTT\n\nthumbnail.jpg#xywh=0,0,160,90", "ext": "vtt"},
                {"url": "https://cdn.example/caption.vtt?token=secret", "ext": "vtt"},
            ]
        }
    }

    result = acquire_caption(info, ["en"], PartiallyFailingYDL())

    assert result.status == "invalid"
    assert result.error is None


def test_fetch_video_uses_url_caption_and_never_enters_stt(monkeypatch):
    class FakeYDL:
        def __init__(self, _options):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return None

        def extract_info(self, _url, download=False):
            assert download is False
            return {
                "title": "Captioned session",
                "duration": 2475.0,
                "subtitles": {
                    "en-US": [
                        {
                            "url": "https://cdn.example/text.vtt?fastly_token=secret",
                            "ext": "vtt",
                        }
                    ]
                },
            }

        def dl(self, filename, _track, subtitle=False):
            assert subtitle is True
            Path(filename).write_text(VALID_VTT, encoding="utf-8")

    monkeypatch.setitem(sys.modules, "yt_dlp", SimpleNamespace(YoutubeDL=FakeYDL))
    stt_provider = Mock(side_effect=AssertionError("STT must not run when CC is available"))
    ffmpeg_lookup = Mock(side_effect=AssertionError("ffmpeg must not be inspected when CC is available"))
    monkeypatch.setattr("claire.ingest.fetchers.video.get_transcript_provider", stt_provider)
    monkeypatch.setattr("claire.ingest.fetchers.video.find_ffmpeg_executable", ffmpeg_lookup)

    settings = Settings(
        _env_file=None,
        enable_video_transcription=True,
        ytdlp_extractor_args="",
        preferred_languages="ko",
    )
    doc = fetch_video(
        "https://www.vmware.com/explore/video/6403820644112",
        settings=settings,
    )

    assert doc.meta["has_transcript"] is True
    assert doc.meta["is_stt"] is False
    assert doc.meta["transcript_source"] == "manual_caption"
    assert doc.meta["caption_status"] == "available"
    assert doc.meta["caption_language"] == "en-US"
    assert "fastly_token" not in str(doc.meta)
    assert VALID_VTT.strip() in doc.raw_text
    stt_provider.assert_not_called()
    ffmpeg_lookup.assert_not_called()


def test_fetch_video_does_not_hide_advertised_caption_download_failure(monkeypatch):
    class FailingYDL:
        def __init__(self, _options):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return None

        def extract_info(self, _url, download=False):
            return {
                "title": "Captioned session",
                "subtitles": {
                    "en-US": [
                        {
                            "url": "https://cdn.example/text.vtt?fastly_token=secret",
                            "ext": "vtt",
                        }
                    ]
                },
            }

        def dl(self, *_args, **_kwargs):
            raise TimeoutError("https://cdn.example/text.vtt?fastly_token=secret")

    monkeypatch.setitem(sys.modules, "yt_dlp", SimpleNamespace(YoutubeDL=FailingYDL))
    stt_provider = Mock(side_effect=AssertionError("caption failure must remain recoverable"))
    monkeypatch.setattr("claire.ingest.fetchers.video.get_transcript_provider", stt_provider)
    settings = Settings(
        _env_file=None,
        enable_video_transcription=True,
        ytdlp_extractor_args="",
        preferred_languages="en",
    )

    with pytest.raises(FetchError) as exc_info:
        fetch_video("https://www.vmware.com/explore/video/6403820644112", settings=settings)

    assert "TimeoutError" in str(exc_info.value)
    assert "fastly_token" not in str(exc_info.value)
    stt_provider.assert_not_called()
