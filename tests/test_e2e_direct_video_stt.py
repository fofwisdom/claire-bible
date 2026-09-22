"""E2E integration test for direct video URL classification, STT transcription, and ingestion pipeline."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from claire.config import Settings
from claire.extract.transcript.base import TranscriptResult, TranscriptSegment
from claire.ingest.fetchers.video import VideoFetcher, is_video_or_audio_url, resolve_media_title
from claire.ingest.router import classify, fetch
from claire.ingest.service import IngestService
from claire.ontology.base import Document
from claire.telegram_bot import classify_input


MINIO_VIDEO_URL = (
    "https://orb.etevers.tech/minio/api/v1/buckets/asset/objects/download"
    "?prefix=files/snsPost/c25871c7-6c0a-4930-a5a9-f8eff59b5008/a938aae0-943a-4f50-bdb8-ffbf6688aee3.mp4"
)


def test_e2e_direct_video_url_classification():
    """1. MinIO 직접 다운로드 URL이 video로 정확히 분류되는지 검증."""
    assert is_video_or_audio_url(MINIO_VIDEO_URL) is True
    assert VideoFetcher.can_handle(MINIO_VIDEO_URL) is True
    assert classify(MINIO_VIDEO_URL) == "video"
    assert classify_input(MINIO_VIDEO_URL) == "video"

    # 공유 텍스트 형태
    shared_text = f"세미나 녹화본입니다: {MINIO_VIDEO_URL}"
    assert classify(shared_text) == "video"
    assert classify_input(shared_text) == "video"


def test_e2e_direct_video_title_resolution():
    """2. generic 제목('download') 대신 쿼리 파라미터 내 실제 파일명 줄기가 채택되는지 검증."""
    title = resolve_media_title(MINIO_VIDEO_URL, raw_title="download")
    assert title == "a938aae0-943a-4f50-bdb8-ffbf6688aee3"

    # S3 및 CDN URL 형태
    s3_url = "https://s3.amazonaws.com/media/download?key=conference/vcf_9_keynote.mp4"
    assert resolve_media_title(s3_url, raw_title="video") == "vcf_9_keynote"


def test_e2e_ingest_service_pipeline_with_stt(tmp_path, monkeypatch):
    """3. IngestService를 통한 실제 적재 파이프라인 E2E 검증 (STT 전사 및 지식 적재)."""
    db_file = tmp_path / "claire_test.db"
    vault_dir = tmp_path / "vault"
    vault_dir.mkdir()

    settings = Settings(
        db_path=str(db_file),
        vault_path=str(vault_dir),
        provider="mock",
        enable_video_transcription=True,
        ffmpeg_bin="/bin/true",
    )

    # Mock TranscriptProvider
    mock_stt = MagicMock()
    mock_stt.transcribe.return_value = TranscriptResult(
        full_text="안녕하세요. 이번 세션에서는 클라우드 인프라 현대화 및 가상화 최적화에 대해 논의하겠습니다.",
        segments=[
            TranscriptSegment(
                start_sec=0.0,
                end_sec=5.0,
                text="안녕하세요. 이번 세션에서는 클라우드 인프라 현대화 및 가상화 최적화에 대해 논의하겠습니다.",
            )
        ],
        duration_sec=5.0,
        provider="mock_gemini",
        model="gemini-3.5-transcribe",
    )

    # Mock yt-dlp to avoid network download during unit test
    class MockYDL:
        def __init__(self, options):
            self.options = options

        def __enter__(self):
            return self

        def __exit__(self, *args):
            pass

        def extract_info(self, url, download=False):
            return {
                "title": "download",  # yt-dlp returns "download"
                "duration": 5.0,
            }

        def download(self, urls):
            # Create dummy audio file in tmp
            outtmpl = self.options.get("outtmpl", "")
            if "%(ext)s" in outtmpl:
                target = Path(outtmpl.replace("%(ext)s", "mp3"))
                target.write_bytes(b"dummy audio data")

    import sys
    from types import SimpleNamespace
    monkeypatch.setitem(sys.modules, "yt_dlp", SimpleNamespace(YoutubeDL=MockYDL))
    monkeypatch.setattr("claire.ingest.fetchers.video.get_transcript_provider", lambda _s: mock_stt)
    monkeypatch.setattr("claire.ingest.fetchers.video.find_ffmpeg_executable", lambda _b: "/bin/true")

    service = IngestService(settings)
    report = service.ingest(MINIO_VIDEO_URL, source="test")

    # IngestReport assertions
    assert report.error is None
    assert report.source_type == "video"
    assert report.has_transcript is True
    assert report.title == "a938aae0-943a-4f50-bdb8-ffbf6688aee3"
    assert report.document_id is not None
    assert "클라우드 인프라 현대화" in (report.summary or "")


def test_e2e_web_fetcher_second_line_of_defense_delegation(monkeypatch):
    """4. 제2방어선: URL에 확장자가 없으나 Content-Type: video/mp4를 반환하는 경우 fetch_video로 위임."""
    ambiguous_url = "https://example.com/api/v1/download-stream?session_id=98765"

    fake_doc = Document(
        url=ambiguous_url,
        canonical_url=ambiguous_url,
        title="Stream Recording",
        raw_text="[영상 자막]\n위임된 영상 전사 내용",
        source_type="video",
        content_hash="mock_hash",
    )

    from claire.ingest.fetchers.http import MediaResponseDetected
    import claire.ingest.fetchers.web as webmod
    import claire.ingest.fetchers.video as videomod

    # Mock SafeHttpClient.get to trigger MediaResponseDetected
    def mock_get(self, url, **kwargs):
        raise MediaResponseDetected(
            "Media response detected (video/mp4)",
            url=url,
            content_type="video/mp4",
        )

    monkeypatch.setattr("claire.ingest.fetchers.http.SafeHttpClient.get", mock_get)
    mock_fetch_video = MagicMock(return_value=fake_doc)
    monkeypatch.setattr(videomod, "fetch_video", mock_fetch_video)

    # Calling fetch() on ambiguous URL -> classified as web -> fetch_web() -> catches MediaResponseDetected -> delegates to fetch_video
    doc = fetch(ambiguous_url)
    assert doc.source_type == "video"
    assert doc.title == "Stream Recording"
    mock_fetch_video.assert_called_once_with(ambiguous_url, full_content=False)
