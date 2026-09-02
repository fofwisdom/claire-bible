import os
import tempfile
import time
from pathlib import Path
from unittest.mock import MagicMock, patch

from claire.config import get_settings
from claire.ingest.fetchers.video import fetch_video
from claire.store.video_cache import (
    compute_video_cache_keys,
    delete_cached_video_file,
    extract_video_id_from_url,
    get_cached_video_file,
    prune_expired_video_cache,
    save_video_file_to_cache,
)


def test_video_cache_id_extraction():
    url_bc = "https://www.vmware.com/explore/video/6403821842112"
    assert extract_video_id_from_url(url_bc) == "brightcove_6403821842112"

    url_bc_player = "https://players.brightcove.net/6164421911001/default_default/index.html?videoId=6403821842112"
    assert extract_video_id_from_url(url_bc_player) == "brightcove_6403821842112"

    url_yt = "https://www.youtube.com/watch?v=dQw4w9WgXcQ"
    assert extract_video_id_from_url(url_yt) == "youtube_dQw4w9WgXcQ"


def test_video_cache_save_retrieve_and_delete():
    with tempfile.TemporaryDirectory() as td:
        data_dir = Path(td)
        url = "https://www.vmware.com/explore/video/6403821842112"

        # 임시 미디어 파일 생성
        dummy_file = data_dir / "sample_audio.mp4"
        dummy_file.write_bytes(b"dummy audio binary data")

        # 1. 저장
        cached_path = save_video_file_to_cache(data_dir, url, dummy_file)
        assert cached_path is not None
        assert cached_path.is_file()
        assert cached_path.read_bytes() == b"dummy audio binary data"

        # 2. 조회
        retrieved = get_cached_video_file(data_dir, url, max_age_sec=259200)
        assert retrieved is not None
        assert retrieved == cached_path

        # 3. 다른 파라미터 형태의 URL로도 동일 캐시 조회 (Brightcove ID 일치)
        alt_url = "https://players.brightcove.net/6164421911001/default_default/index.html?videoId=6403821842112"
        retrieved_alt = get_cached_video_file(data_dir, alt_url, max_age_sec=259200)
        assert retrieved_alt == cached_path

        # 4. 삭제
        assert delete_cached_video_file(data_dir, url) is True
        assert get_cached_video_file(data_dir, url) is None


def test_video_cache_expiration():
    with tempfile.TemporaryDirectory() as td:
        data_dir = Path(td)
        url = "https://example.com/video/123"

        dummy_file = data_dir / "test.mp4"
        dummy_file.write_bytes(b"data")

        cached_path = save_video_file_to_cache(data_dir, url, dummy_file)
        assert cached_path is not None

        # 과거 시간으로 mtime 조작 (사흘 초과: 4일 전)
        four_days_ago = time.time() - (4 * 24 * 3600)
        os.utime(cached_path, (four_days_ago, four_days_ago))

        # 조회 시 자동 만료되어 삭제되고 None 반환
        assert get_cached_video_file(data_dir, url, max_age_sec=259200) is None
        assert not cached_path.exists()


def test_fetch_video_failure_caching_and_reingest_reuse():
    """STT 전사 실패 시 비디오 캐시 저장, 재적재 시 원격 다운로드 없이 캐시 사용 확인."""
    with tempfile.TemporaryDirectory() as td:
        data_dir = Path(td)
        s = get_settings().model_copy(
            update={
                "db_path": str(data_dir / "claire.db"),
                "enable_video_transcription": True,
            }
        )

        url = "https://www.vmware.com/explore/video/6403821842112"

        # 1차 시도: 다운로드는 성공하지만 STT가 실패하는 상황 시뮬레이션
        class FailingSTT:
            def transcribe(self, *args, **kwargs):
                raise RuntimeError("API quota exceeded or network timeout")

        with (
            patch("claire.ingest.fetchers.video.get_settings", return_value=s),
            patch("claire.ingest.fetchers.video.get_transcript_provider", return_value=FailingSTT()),
            patch("claire.ingest.fetchers.video.find_ffmpeg_executable", return_value="/bin/ffmpeg"),
            patch("yt_dlp.YoutubeDL") as mock_ydl_cls,
        ):
            def mock_download(urls):
                opts = mock_ydl_cls.call_args[0][0] if mock_ydl_cls.call_args else {}
                outtmpl = Path(opts.get("outtmpl", ""))
                if outtmpl.parent.is_dir():
                    f = outtmpl.parent / "audio.mp4"
                    f.write_bytes(b"simulated audio 100mb")

            mock_ydl_instance = MagicMock()
            mock_ydl_instance.download.side_effect = mock_download
            mock_ydl_instance.extract_info.return_value = {
                "title": "Test Video",
                "duration": 120.0,
            }
            mock_ydl_cls.return_value.__enter__.return_value = mock_ydl_instance

            doc1 = fetch_video(url, settings=s)
            assert doc1.meta.get("has_transcript") is False
            assert "RuntimeError" in (doc1.meta.get("stt_error") or "")
            assert doc1.meta.get("video_cached") is True

        # 실패 후 캐시 디렉터리에 파일이 사흘 보존용으로 저장되었는지 검증
        cached_file = get_cached_video_file(data_dir, url)
        assert cached_file is not None
        assert cached_file.is_file()
        assert cached_file.read_bytes() == b"simulated audio 100mb"

        # 2차 시도: 재적재 실행 (이번에는 STT 성공)
        # yt_dlp가 다운로드를 시도하면 에러가 발생하도록 설정하여 캐시가 사용되었음을 보장
        from claire.extract.transcript.base import TranscriptResult, TranscriptSegment

        class SuccessSTT:
            def transcribe(self, audio_path, *args, **kwargs):
                assert Path(audio_path).read_bytes() == b"simulated audio 100mb"
                return TranscriptResult(
                    provider="gemini",
                    language="en",
                    duration_sec=120.0,
                    segments=[TranscriptSegment(start_sec=0.0, end_sec=5.0, text="Hello world")],
                    full_text="Hello world",
                )

        with (
            patch("claire.ingest.fetchers.video.get_settings", return_value=s),
            patch("claire.ingest.fetchers.video.get_transcript_provider", return_value=SuccessSTT()),
            patch("claire.ingest.fetchers.video.find_ffmpeg_executable", return_value="/bin/ffmpeg"),
            patch("yt_dlp.YoutubeDL") as mock_ydl_cls,
        ):
            mock_ydl_instance = MagicMock()
            mock_ydl_instance.download.side_effect = AssertionError("yt-dlp download should NOT be called when cache is present!")
            mock_ydl_instance.extract_info.return_value = {
                "title": "Test Video",
                "duration": 120.0,
            }
            mock_ydl_cls.return_value.__enter__.return_value = mock_ydl_instance

            doc2 = fetch_video(url, settings=s)
            assert doc2.meta.get("has_transcript") is True
            assert doc2.meta.get("video_cache_used") is True
            assert "Hello world" in doc2.raw_text

        # 3. 전사 및 적재 성공 후 캐시가 정상 정리되었는지 검증
        assert get_cached_video_file(data_dir, url) is None
