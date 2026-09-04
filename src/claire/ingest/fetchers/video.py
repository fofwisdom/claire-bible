"""비디오 페처 — 메타데이터와 발행자 CC를 수집하고 필요할 때만 STT를 수행한다."""

from __future__ import annotations

import logging
import re
import tempfile
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

from ...config import find_ffmpeg_executable, get_settings
from ...extract.table_budget import slice_document_text
from ...extract.transcript.factory import get_transcript_provider
from ...ontology.base import Document
from ..normalize import canonicalize_url, content_hash
from .base import FetchError
from .captions import CaptionAcquisition, acquire_caption, is_valid_speech_vtt

logger = logging.getLogger(__name__)

# VMware Explore 및 Brightcove 패턴 정규식
_VMWARE_EXPLORE_RE = re.compile(r"vmware\.com/explore/video/(\d+)", re.IGNORECASE)
_BRIGHTCOVE_ACCOUNT_RE = re.compile(r"data-account=[\"'](\d+)[\"']", re.IGNORECASE)
_BRIGHTCOVE_VIDEO_RE = re.compile(r"data-video-id=[\"'](\d+)[\"']", re.IGNORECASE)
_HTML5_VIDEO_SRC_RE = re.compile(
    r"<video[^>]+src=[\"']([^\"']+)[\"']|<source[^>]+src=[\"']([^\"']+)[\"']",
    re.IGNORECASE,
)
_OG_VIDEO_RE = re.compile(
    r"<meta[^>]+property=[\"']og:video[\"'][^>]+content=[\"']([^\"']+)[\"']",
    re.IGNORECASE,
)


def resolve_video_target_url(url: str, html_text: str = "") -> str:
    """웹페이지 URL 또는 HTML 내용에서 yt-dlp가 직접 해석 가능한 표준 비디오/임베드 URL로 변환."""
    # 1. VMware Explore 비디오 페이지 -> Brightcove 공식 임베드 URL
    m_vmware = _VMWARE_EXPLORE_RE.search(url)
    if m_vmware:
        video_id = m_vmware.group(1)
        account_id = "6164421911001"  # Broadcom / VMware Explore 기본 Account ID
        if html_text:
            acc_match = _BRIGHTCOVE_ACCOUNT_RE.search(html_text)
            if acc_match:
                account_id = acc_match.group(1)
        return f"https://players.brightcove.net/{account_id}/default_default/index.html?videoId={video_id}"

    # 2. 일반 HTML 내 Brightcove 임베드 태그 탐색
    if html_text:
        acc_m = _BRIGHTCOVE_ACCOUNT_RE.search(html_text)
        vid_m = _BRIGHTCOVE_VIDEO_RE.search(html_text)
        if acc_m and vid_m:
            return f"https://players.brightcove.net/{acc_m.group(1)}/default_default/index.html?videoId={vid_m.group(1)}"

        # 3. HTML5 <video> 또는 og:video 추출
        v_src = _HTML5_VIDEO_SRC_RE.search(html_text)
        if v_src:
            src = v_src.group(1) or v_src.group(2)
            if src and src.startswith(("http://", "https://")):
                return src

        og_src = _OG_VIDEO_RE.search(html_text)
        if og_src:
            src = og_src.group(1)
            if src and src.startswith(("http://", "https://")):
                return src

    return url


def parse_ytdlp_extractor_args(args_str: str) -> dict[str, dict[str, list[str]]]:
    """'generic:impersonate,youtube:player_client=android' 등의 문자열을 yt-dlp extractor_args 딕셔너리로 변환."""
    if not args_str or not args_str.strip():
        return {}
    try:
        import yt_dlp.options

        parser = yt_dlp.options.create_parser()
        opts = parser.parse_args(["--extractor-args", args_str.strip()])[0]
        return opts.extractor_args or {}
    except Exception as e:
        logger.warning("Failed to parse extractor_args '%s': %s", args_str, e)
        return {"generic": {"impersonate": [""]}}


def fetch_video(
    url: str,
    *,
    full_content: bool = False,
    preferred_languages: list[str] | None = None,
    settings: Settings | None = None,
) -> Document:
    """비디오 URL에서 메타데이터와 CC 또는 STT를 추출하여 Document를 생성한다."""
    settings = settings or get_settings()
    target_langs = (
        preferred_languages
        if preferred_languages is not None
        else settings.effective_preferred_languages
    )

    resolved_url = resolve_video_target_url(url)
    ext_args = parse_ytdlp_extractor_args(
        getattr(settings, "ytdlp_extractor_args", "generic:impersonate")
    )

    # 1. yt-dlp 라이브러리 사용 시도
    info: dict[str, Any] = {}
    has_ytdlp = False
    caption = CaptionAcquisition()
    try:
        import yt_dlp

        has_ytdlp = True
        ydl_opts = {
            "quiet": True,
            "noprogress": True,
            "no_warnings": True,
            "skip_download": True,
            "writesubtitles": True,
            "writeautomaticsub": True,
            "subtitleslangs": target_langs,
        }
        if ext_args:
            ydl_opts["extractor_args"] = ext_args

        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            try:
                info = ydl.extract_info(resolved_url, download=False) or {}
            except Exception as e:
                logger.warning("yt-dlp metadata extraction failed for %s: %s", resolved_url, e)
                # 원본 URL로 재시도
                if resolved_url != url:
                    try:
                        info = ydl.extract_info(url, download=False) or {}
                    except Exception:  # noqa: BLE001
                        pass
            if info:
                caption = acquire_caption(info, target_langs, ydl)
    except ImportError:
        logger.info("yt-dlp is not installed, falling back to minimal video fetch")

    if caption.status == "download_failed":
        raise FetchError(
            "Advertised caption download failed "
            f"({caption.error or 'unknown caption download error'})"
        )

    title = str(info.get("title") or "").strip()
    uploader = str(info.get("uploader") or info.get("channel") or "").strip()
    description = str(info.get("description") or "").strip()
    duration_val = info.get("duration")
    duration_sec = float(duration_val) if duration_val is not None else 0.0
    tags = info.get("tags") or info.get("categories") or []

    if not title:
        # URL 기반 기본 제목
        title = Path(urlsplit(url).path).stem or "Video"

    transcript_text = caption.text
    segments_data: list[dict] = []

    # 2. 유효한 CC가 없고 전사 활성화(CLAIRE_ENABLE_VIDEO_TRANSCRIPTION=1) 시 오디오 STT 실행
    stt_error_msg: str | None = None
    cached_used: bool = False
    cached_saved: bool = False
    is_stt: bool = False
    if not transcript_text and settings.enable_video_transcription:
        from ...store.video_cache import (
            delete_cached_video_file,
            get_cached_video_file,
            save_video_file_to_cache,
        )

        effective_data_dir = getattr(settings, "data_dir", None)
        cached_file: Path | None = None
        if effective_data_dir:
            cached_file = get_cached_video_file(
                effective_data_dir,
                url,
                canonical_url=resolved_url,
                max_age_sec=getattr(settings, "video_cache_ttl_sec", 259200),
            )
            if cached_file:
                logger.info("Using cached video media file: %s for %s", cached_file, url)
                cached_used = True

        ffmpeg_exec = find_ffmpeg_executable(settings.ffmpeg_bin)
        if not ffmpeg_exec:
            stt_error_msg = f"ffmpeg binary not found ({settings.ffmpeg_bin})"
            logger.info("ffmpeg binary not found (%s), skipping audio extraction", settings.ffmpeg_bin)
        else:
            if cached_file:
                # 1. 캐시된 미디어가 있으면 다운로드 없이 즉시 STT 실행 시도
                try:
                    stt_provider = get_transcript_provider(settings)
                    lang_code = settings.stt_language or (target_langs[0] if target_langs else "ko")
                    stt_result = stt_provider.transcribe(
                        cached_file, language=lang_code, timestamps=True
                    )
                    transcript_text = stt_result.full_text
                    segments_data = [s.model_dump() for s in stt_result.segments]
                    if not transcript_text:
                        stt_error_msg = "Cached STT returned empty transcript"
                        if effective_data_dir:
                            delete_cached_video_file(effective_data_dir, url, canonical_url=resolved_url)
                        cached_file = None
                    else:
                        is_stt = True
                        # 전사 성공 시 사용 완료된 캐시 정리
                        if effective_data_dir:
                            delete_cached_video_file(effective_data_dir, url, canonical_url=resolved_url)
                except Exception as e:
                    stt_error_msg = f"{type(e).__name__}: {e}"
                    logger.warning("Cached audio STT transcription failed for %s: %s", url, e)
                    # 손상되었거나 전사에 실패한 캐시 파일은 즉시 정리
                    if effective_data_dir:
                        delete_cached_video_file(effective_data_dir, url, canonical_url=resolved_url)
                    cached_file = None

            # 2. 캐시가 없거나 캐시 STT가 실패한 경우, yt-dlp로 원격 스트림 다운로드 진행
            if not transcript_text and has_ytdlp:
                with tempfile.TemporaryDirectory(prefix="claire_audio_") as tmp_dir:
                    tmp_out = Path(tmp_dir) / "audio.%(ext)s"
                    audio_opts = {
                        "format": "ba[protocol!*=dash]/ba/b[height<=360]/b[height<=480]/b",
                        "outtmpl": str(tmp_out),
                        "ffmpeg_location": ffmpeg_exec,
                        "quiet": True,
                        "no_warnings": True,
                        "retries": 5,
                        "fragment_retries": 10,
                    }
                    if ext_args:
                        audio_opts["extractor_args"] = ext_args
                    downloaded_file: Path | None = None
                    try:
                        import yt_dlp

                        try:
                            with yt_dlp.YoutubeDL(audio_opts) as ydl:
                                ydl.download([resolved_url])
                        except Exception as dl_err:
                            logger.debug("Primary audio format download failed, trying fallback: %s", dl_err)
                            fallback_opts = dict(audio_opts)
                            fallback_opts["format"] = "ba/b[height<=360]/b"
                            with yt_dlp.YoutubeDL(fallback_opts) as ydl:
                                ydl.download([resolved_url])

                        # 다운로드된 오디오 파일 탐색 (.mp3, .m4a, .mp4 등)
                        audio_candidates = [
                            p for p in Path(tmp_dir).glob("audio.*")
                            if p.is_file() and not p.name.endswith((".part", ".ytdl"))
                        ]
                        if audio_candidates:
                            downloaded_file = audio_candidates[0]
                            stt_provider = get_transcript_provider(settings)
                            lang_code = settings.stt_language or (target_langs[0] if target_langs else "ko")
                            stt_result = stt_provider.transcribe(
                                downloaded_file, language=lang_code, timestamps=True
                            )
                            transcript_text = stt_result.full_text
                            segments_data = [s.model_dump() for s in stt_result.segments]
                            if not duration_sec and stt_result.duration_sec:
                                duration_sec = stt_result.duration_sec
                            if not transcript_text:
                                stt_error_msg = "STT provider returned empty transcript"
                            else:
                                is_stt = True
                                # 적재 성공 시 기존 캐시가 있다면 정리
                                if effective_data_dir:
                                    delete_cached_video_file(effective_data_dir, url, canonical_url=resolved_url)
                    except Exception as e:
                        stt_error_msg = f"{type(e).__name__}: {e}"
                        logger.warning("Audio extraction & STT failed for %s: %s", url, e)
                    finally:
                        # 다운로드는 성공했으나 STT 또는 처리가 실패한 경우 사흘(3일)간 캐시 저장!
                        if not transcript_text and downloaded_file and downloaded_file.is_file() and effective_data_dir:
                            saved_path = save_video_file_to_cache(
                                effective_data_dir,
                                url,
                                downloaded_file,
                                canonical_url=resolved_url,
                            )
                            if saved_path:
                                cached_saved = True
                                logger.info("Saved downloaded video media to 3-day cache: %s", saved_path)

    transcript_source = caption.source if caption.status == "available" else None
    if is_stt:
        transcript_source = "stt"

    # 3. 텍스트 본문 결합 구성
    sections: list[str] = []
    if uploader:
        sections.append(f"발표자/채널: {uploader}")
    if duration_sec > 0:
        mins = int(duration_sec // 60)
        secs = int(duration_sec % 60)
        sections.append(f"재생 시간: {mins}분 {secs}초 ({duration_sec:.1f}초)")

    if transcript_text:
        header = "[영상 음성 전사 (STT)]" if is_stt else "[영상 자막]"
        sections.append(f"{header}\n{transcript_text}")
    elif not settings.enable_video_transcription:
        sections.append("[영상 자막]\n(비디오 음성 전사 기능이 비활성화되어 있습니다. CLAIRE_ENABLE_VIDEO_TRANSCRIPTION=1 설정 시 생성됩니다.)")
    elif stt_error_msg:
        cached_notice = " (다운로드된 영상 미디어는 사흘간 로컬 캐시로 안전하게 보관되며, 재적재 시 즉시 재사용됩니다.)" if cached_saved else ""
        sections.append(f"[영상 자막]\n(음성 전사 처리 중 오류가 발생했습니다: {stt_error_msg}{cached_notice})")
    else:
        sections.append("[영상 자막]\n(추출된 자막이 없거나 음성 추출이 지원되지 않는 스트림입니다.)")

    if description:
        sections.append(f"[영상 설명]\n{description}")

    if tags and isinstance(tags, list):
        sections.append(f"[태그]\n{', '.join(str(t) for t in tags if t)}")

    full_text_blob = "\n\n".join(sections).strip()
    if not full_text_blob:
        raise FetchError(f"Failed to extract video details for {url}")

    video_budget = getattr(settings, "video_max_extract_chars", 200000)
    budget = 0 if full_content else video_budget
    raw_text, is_truncated, orig_chars, raw_chars = slice_document_text(
        full_text_blob, budget, strategy=settings.slicing_strategy
    )

    canonical = canonicalize_url(info.get("webpage_url") or url)

    # STT 절단 여부 판정: 글자 수 상한 슬라이싱 또는 120초 이상의 전사 구간 누락
    stt_duration_gap = False
    if is_stt and duration_sec > 0 and segments_data:
        last_seg_end = float(segments_data[-1].get("end_sec", 0.0))
        if (duration_sec - last_seg_end) > 120.0:
            stt_duration_gap = True
    stt_truncated = bool(is_stt and (is_truncated or stt_duration_gap))

    return Document(
        url=url,
        canonical_url=canonical,
        title=title,
        author=uploader or None,
        raw_text=raw_text,
        source_type="video",
        content_hash=content_hash(full_text_blob),
        partial=bool(not transcript_text or is_truncated),
        meta={
            "duration_sec": duration_sec,
            "has_transcript": bool(transcript_text),
            "transcript_segments": segments_data,
            "resolved_stream_url": resolved_url if resolved_url != url else None,
            "raw_truncated": is_truncated,
            "orig_chars": orig_chars,
            "raw_chars": raw_chars,
            "stt_error": stt_error_msg,
            "video_cached": cached_saved or cached_used,
            "video_cache_used": cached_used,
            "is_stt": is_stt,
            "stt": is_stt,
            "stt_truncated": stt_truncated,
            "stt_orig_chars": orig_chars if is_stt else None,
            "stt_raw_chars": raw_chars if is_stt else None,
            "stt_duration_gap": stt_duration_gap,
            "transcript_source": transcript_source,
            "caption_status": caption.status,
            "caption_language": caption.language,
            "caption_format": caption.format,
            "caption_content_hash": caption.content_hash,
            "caption_error": caption.error,
        },
    )
