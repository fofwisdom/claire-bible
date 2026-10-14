"""Unit tests for Naver Clip / Shorts fetcher and STT adapter."""

from __future__ import annotations

import json
from io import BytesIO
from unittest.mock import MagicMock, patch
import urllib.request

import pytest

from claire.ingest.fetchers.base import FetchError
from claire.ingest.fetchers.naver_clip import (
    NaverClipClient,
    NaverClipFetcher,
    fetch_naver_clip,
    is_naver_clip_url,
)
from claire.ontology.base import Document


def test_is_naver_clip_url():
    assert is_naver_clip_url("https://naver.me/5eDc8abm") is True
    assert is_naver_clip_url("https://clip.naver.com/shorts/A08CD689352F2A92D6BF04CC676647DAE467") is True
    assert is_naver_clip_url("https://m.naver.com/shorts/?seedMediaId=A08CD689352F2A92D6BF04CC676647DAE467") is True
    assert is_naver_clip_url("https://tv.naver.com/v/12345") is True
    assert is_naver_clip_url("https://example.com/article") is False
    assert is_naver_clip_url("") is False


def test_resolve_media_id_from_query_and_path():
    client = NaverClipClient()
    # Query parameter seedMediaId
    url_qs = "https://m.naver.com/shorts/?seedMediaId=A08CD689352F2A92D6BF04CC676647DAE467&serviceType=CLIP"
    assert client.resolve_media_id(url_qs) == "A08CD689352F2A92D6BF04CC676647DAE467"

    # Path parameter /shorts/<hex>
    url_path = "https://clip.naver.com/shorts/A08CD689352F2A92D6BF04CC676647DAE467"
    assert client.resolve_media_id(url_path) == "A08CD689352F2A92D6BF04CC676647DAE467"


def test_resolve_media_id_from_redirect_response(monkeypatch):
    client = NaverClipClient()
    fake_html = """
    <html>
      <head>
        <meta property="og:url" content="https://m.naver.com/shorts/?seedMediaId=A08CD689352F2A92D6BF04CC676647DAE467" />
      </head>
      <body>Redirecting...</body>
    </html>
    """

    class FakeResponse:
        def __init__(self):
            self.url = "https://link.naver.com/bridge?url=https%3A%2F%2Fm.naver.com%2Fshorts%2F%3FseedMediaId%3DA08CD689352F2A92D6BF04CC676647DAE467"

        def read(self, _size=65536):
            return fake_html.encode("utf-8")

        def __enter__(self):
            return self

        def __exit__(self, *args):
            return None

    monkeypatch.setattr(urllib.request, "urlopen", lambda req, timeout=20.0: FakeResponse())
    media_id = client.resolve_media_id("https://naver.me/5eDc8abm")
    assert media_id == "A08CD689352F2A92D6BF04CC676647DAE467"


def test_get_card_parses_creatorhub_api(monkeypatch):
    client = NaverClipClient()
    mock_payload = {
        "header": {"code": 0, "message": "success"},
        "body": {
            "card": {
                "content": {
                    "title": "NAVER Tech Meetup",
                    "description": "NAVER Developers presentation on distributed systems.",
                    "profile": {"nickname": "NAVER D2"},
                    "videoQuality": {"duration": 3900.0},
                    "vod": {
                        "media": [
                            {"type": "AUDIO", "url": "https://cdn.pstatic.net/audio/clip_sample.m4a"},
                            {"type": "VIDEO", "url": "https://cdn.pstatic.net/video/clip_sample.mp4"},
                        ]
                    },
                }
            }
        },
    }

    class FakeApiResponse:
        def read(self):
            return json.dumps(mock_payload).encode("utf-8")

        def __enter__(self):
            return self

        def __exit__(self, *args):
            return None

    monkeypatch.setattr(urllib.request, "urlopen", lambda req, timeout=20.0: FakeApiResponse())
    card = client.get_card("A08CD689352F2A92D6BF04CC676647DAE467")
    assert card["header"]["code"] == 0

    audio_url, video_url, hls_url = client.extract_streams(card)
    assert audio_url == "https://cdn.pstatic.net/audio/clip_sample.m4a"
    assert video_url == "https://cdn.pstatic.net/video/clip_sample.mp4"
    assert hls_url is None


def test_fetch_naver_clip_missing_transcript_raises_fetch_error(monkeypatch):
    client = NaverClipClient()
    monkeypatch.setattr(client, "resolve_media_id", lambda url: "A08CD689352F2A92D6BF04CC676647DAE467")

    thin_card = {
        "header": {"code": 0, "message": "success"},
        "body": {
            "card": {
                "content": {
                    "title": "Short Clip",
                    "description": "Short description without substantive text",
                    "profile": {"nickname": "Tester"},
                    "videoQuality": {"duration": 45.0},
                    "vod": {"media": []},
                }
            }
        },
    }
    monkeypatch.setattr(client, "get_card", lambda mid: thin_card)
    monkeypatch.setattr("claire.ingest.fetchers.naver_clip.NaverClipClient", lambda: client)

    # When STT is disabled or yields no transcript, thin clips must raise FetchError
    with pytest.raises(FetchError, match="video_transcript_missing"):
        fetch_naver_clip("https://naver.me/5eDc8abm")


def test_fetch_naver_clip_with_successful_stt(monkeypatch):
    client = NaverClipClient()
    monkeypatch.setattr(client, "resolve_media_id", lambda url: "A08CD689352F2A92D6BF04CC676647DAE467")

    card = {
        "header": {"code": 0, "message": "success"},
        "body": {
            "card": {
                "content": {
                    "title": "[기술밋업] 네이버 개발자가 들려주는 기술 이야기",
                    "description": "네이버 개발자 밋업 세션 발표입니다.",
                    "profile": {"nickname": "NAVER D2"},
                    "videoQuality": {"duration": 3900.0},
                    "vod": {
                        "media": [
                            {"type": "AUDIO", "url": "https://cdn.pstatic.net/audio/clip_sample.m4a"},
                        ]
                    },
                }
            }
        },
    }
    monkeypatch.setattr(client, "get_card", lambda mid: card)
    monkeypatch.setattr("claire.ingest.fetchers.naver_clip.NaverClipClient", lambda: client)

    class FakeStreamResponse:
        def __init__(self):
            self.data = b"M4A_SYNTHETIC_AUDIO_STREAM_DATA"
            self.read_called = False

        def read(self, _size=1024 * 1024):
            if not self.read_called:
                self.read_called = True
                return self.data
            return b""

        def __enter__(self):
            return self

        def __exit__(self, *args):
            return None

    monkeypatch.setattr(urllib.request, "urlopen", lambda req, timeout=60.0: FakeStreamResponse())

    class FakeSegment:
        def model_dump(self):
            return {"start_sec": 0.0, "end_sec": 10.0, "text": "네이버 기술 밋업에 오신 것을 환영합니다."}

    class FakeSTTResult:
        full_text = "네이버 기술 밋업에 오신 것을 환영합니다. 대규모 분산 아키텍처와 트래픽 제어 기법을 설명합니다."
        segments = [FakeSegment()]
        duration_sec = 3900.0

    class FakeSTTProvider:
        def transcribe(self, path, language="ko", timestamps=True):
            return FakeSTTResult()

    monkeypatch.setattr(
        "claire.ingest.fetchers.naver_clip.get_transcript_provider",
        lambda settings: FakeSTTProvider(),
    )

    doc = fetch_naver_clip("https://naver.me/5eDc8abm")
    assert isinstance(doc, Document)
    assert doc.source_type == "video"
    assert doc.title == "[기술밋업] 네이버 개발자가 들려주는 기술 이야기"
    assert "대규모 분산 아키텍처" in doc.raw_text
    assert doc.meta["is_stt"] is True
    assert doc.meta["has_transcript"] is True
    assert doc.meta["media_id"] == "A08CD689352F2A92D6BF04CC676647DAE467"
    assert doc.meta["fetch_via"] == "naver_clip"
