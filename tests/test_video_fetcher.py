"""비디오 페처 및 라우팅 단위 테스트."""

import sys
from pathlib import Path
from types import SimpleNamespace

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
    # VMware Explore pages go to the project plugin claire.vmware_explore (its own tests).
    assert classify("https://players.brightcove.net/6164421911001/default_default/index.html?videoId=6403821753112") == "video"
    assert classify("https://vimeo.com/12345678") == "video"
    assert classify("https://example.com/stream/presentation.mp4") == "video"
    assert classify("https://example.com/stream/manifest.m3u8") == "video"


def test_resolve_video_target_url():
    # The VMware Explore rule is in the project plugin claire.vmware_explore (brightcove_player_url).
    assert resolve_video_target_url("https://www.vmware.com/explore/video/6403821753112") == "https://www.vmware.com/explore/video/6403821753112"

    html_snippet = '<div data-account="12345" data-video-id="67890"></div>'
    resolved_html = resolve_video_target_url("https://example.com/page", html_snippet)
    assert "players.brightcove.net/12345/default_default/index.html?videoId=67890" in resolved_html

    html_video = '<video src="https://cdn.example.com/sample.mp4"></video>'
    assert resolve_video_target_url("https://example.com", html_video) == "https://cdn.example.com/sample.mp4"


def test_fetch_video_disabled_stt(monkeypatch):
    class MetadataOnlyYDL:
        def __init__(self, _options):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return None

        def extract_info(self, _url, download=False):
            assert download is False
            return {"title": "Video without captions", "duration": 120.0}

    monkeypatch.setitem(
        sys.modules, "yt_dlp", SimpleNamespace(YoutubeDL=MetadataOnlyYDL)
    )
    monkeypatch.setenv("CLAIRE_ENABLE_VIDEO_TRANSCRIPTION", "0")
    monkeypatch.setenv("CLAIRE_YTDLP_EXTRACTOR_ARGS", "")
    from claire.core.config import get_settings
    get_settings.cache_clear()

    doc = fetch_video("https://www.vmware.com/explore/video/6403821753112")
    assert isinstance(doc, Document)
    assert doc.source_type == "video"
    assert "CLOB1244LV" in doc.title or "6403821753112" in doc.url
    assert "비디오 음성 전사 기능이 비활성화되어 있습니다" in doc.raw_text
    assert doc.meta["has_transcript"] is False
    assert doc.meta.get("is_stt") is False


def test_fetch_video_with_mock_stt(monkeypatch):
    class CaptionlessYDL:
        def __init__(self, options):
            self.options = options

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return None

        def extract_info(self, _url, download=False):
            assert download is False
            return {"title": "Video without captions", "duration": 120.0}

        def download(self, _urls):
            outtmpl = Path(self.options["outtmpl"])
            (outtmpl.parent / "audio.mp4").write_bytes(b"synthetic audio fixture")

    monkeypatch.setitem(sys.modules, "yt_dlp", SimpleNamespace(YoutubeDL=CaptionlessYDL))
    monkeypatch.setattr(
        "claire.ingest.fetchers.video.find_ffmpeg_executable",
        lambda _configured: "/bin/ffmpeg",
    )
    monkeypatch.setenv("CLAIRE_ENABLE_VIDEO_TRANSCRIPTION", "1")
    monkeypatch.setenv("CLAIRE_ENVIRONMENT", "development")
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    monkeypatch.setenv("CLAIRE_YTDLP_EXTRACTOR_ARGS", "")
    from claire.core.config import get_settings
    get_settings.cache_clear()

    doc = fetch_video("https://www.vmware.com/explore/video/6403821753112")
    assert isinstance(doc, Document)
    assert doc.source_type == "video"
    assert doc.title != ""
    assert doc.author is None
    assert "발표자/채널" not in doc.raw_text
    assert doc.meta["duration_sec"] > 0
    assert doc.meta.get("is_stt") is True
    assert doc.meta.get("transcript_source") == "stt"


def test_fetch_video_uploader_channel_isolation(monkeypatch: pytest.MonkeyPatch):
    """비디오 uploader가 Document.author로 승격되지 않고 raw_text에 주입되지 않음을 검증."""
    import sys
    from types import SimpleNamespace

    class MockYDL:
        def __init__(self, options):
            self.options = options
        def __enter__(self):
            return self
        def __exit__(self, *_args):
            return None
        def extract_info(self, _url, download=False):
            return {
                "title": "Orbrium Webinar Session",
                "uploader": "Orbrium",
                "channel": "Orbrium",
                "duration": 60.0,
                "description": "Video description",
            }

    monkeypatch.setitem(sys.modules, "yt_dlp", SimpleNamespace(YoutubeDL=MockYDL))
    monkeypatch.setenv("CLAIRE_ENABLE_VIDEO_TRANSCRIPTION", "0")
    from claire.core.config import get_settings
    get_settings.cache_clear()

    doc = fetch_video("https://www.youtube.com/watch?v=mock12345")
    assert doc.title == "Orbrium Webinar Session"
    assert doc.author is None
    assert doc.meta.get("video_channel") == "Orbrium"
    assert "발표자/채널: Orbrium" not in doc.raw_text


def test_resolve_media_title():
    from claire.ingest.fetchers.video import resolve_media_title

    # 1. Generic title "download" with prefix param -> should resolve to filename stem
    url1 = "https://orb.etevers.tech/minio/api/v1/buckets/asset/objects/download?prefix=files/snsPost/c258/a938aae0-943a-4f50-bdb8-ffbf6688aee3.mp4"
    assert resolve_media_title(url1, "download") == "a938aae0-943a-4f50-bdb8-ffbf6688aee3"

    # 2. Key param in S3 presigned URL
    url2 = "https://s3.amazonaws.com/bucket/download?key=media/tech_conference_keynote.mp4"
    assert resolve_media_title(url2, "video") == "tech_conference_keynote"

    # 3. Meaningful title preserved
    assert resolve_media_title(url1, "Official Session Keynote") == "Official Session Keynote"

    # 4. Standard path stem
    url3 = "https://example.com/videos/product_demo.mp4"
    assert resolve_media_title(url3, "") == "product_demo"


def test_video_fetcher_can_handle_direct_links():
    from claire.ingest.fetchers.video import VideoFetcher

    minio_url = "https://orb.etevers.tech/minio/api/v1/buckets/asset/objects/download?prefix=files/snsPost/c258/a938aae0.mp4"
    assert VideoFetcher.can_handle(minio_url) is True

    s3_url = "https://s3.amazonaws.com/bucket/item?filename=lecture.mkv"
    assert VideoFetcher.can_handle(s3_url) is True

    non_video_url = "https://example.com/articles/index.html"
    assert VideoFetcher.can_handle(non_video_url) is False


def test_validate_extractable_content_blocks_stt_error_even_with_long_description():
    """STT 오류가 기록된 video/youtube 문서는 설명글이 500자 이상이어도 일반 모델 추출로 넘어가지 않아야 한다."""
    from claire.ingest.pipeline import validate_extractable_content

    long_desc = "발표 영상의 상세 소개글과 목차 설명입니다. " * 25
    assert len(long_desc) >= 500

    for source_type in ("video", "youtube"):
        doc = Document(
            url="https://example.com/video",
            canonical_url="https://example.com/video",
            title="Video with STT error",
            raw_text=f"재생 시간: 25분 0초\n\n[영상 자막]\n(음성 전사 처리 중 오류가 발생했습니다: RuntimeError: 404)\n\n[영상 설명]\n{long_desc}",
            source_type=source_type,
            content_hash="hash_err",
            meta={"has_transcript": False, "stt_error": "RuntimeError: 404 Not Found"},
        )
        ok, err = validate_extractable_content(doc)
        assert ok is False
        assert err is not None and "video_stt_failed" in err

    # STT 오류가 없는(예: STT 비활성화) YouTube 문서는 기존과 같이 설명글 기반 추출을 허용한다.
    yt_disabled = Document(
        url="https://youtube.com/watch?v=abc12345678",
        canonical_url="https://youtube.com/watch?v=abc12345678",
        title="YouTube STT disabled",
        raw_text=f"[영상 설명]\n{long_desc}",
        source_type="youtube",
        content_hash="hash_ok",
        meta={"has_transcript": False, "stt_error": None},
    )
    ok_yt, err_yt = validate_extractable_content(yt_disabled)
    assert ok_yt is True
    assert err_yt is None


