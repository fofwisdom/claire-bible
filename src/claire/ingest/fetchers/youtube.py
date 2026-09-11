"""YouTube fetcher — 자막(transcript) + 메타데이터 + 챕터 + 무자막 시 STT 폴백."""

from __future__ import annotations

import json
import logging
import re
import tempfile
from pathlib import Path
from typing import Any

import httpx

from ...config import Settings, find_ffmpeg_executable, get_settings
from ...extract.provider import emit_progress
from ...extract.table_budget import slice_document_text
from ...extract.transcript.factory import get_transcript_provider
from ...ontology.base import Document
from ..normalize import content_hash
from .base import FetchError
from .captions import CaptionAcquisition, acquire_caption, format_chapters
from .video import parse_ytdlp_extractor_args

logger = logging.getLogger(__name__)

_ID_RES = [
    re.compile(r"[?&]v=([\w-]{11})"),
    re.compile(r"youtu\.be/([\w-]{11})"),
    re.compile(r"/shorts/([\w-]{11})"),
    re.compile(r"/live/([\w-]{11})"),
    re.compile(r"/embed/([\w-]{11})"),
]


def video_id(url: str) -> str | None:
    for r in _ID_RES:
        m = r.search(url)
        if m:
            return m.group(1)
    return None


def fetch_video_title(vid: str) -> str | None:
    """oEmbed(API 키 불필요)로 실제 영상 제목을 가져온다. 실패하면 None(호출측이 폴백)."""
    details = fetch_video_details(vid)
    return details.get("title") or None


def fetch_video_details(vid: str) -> dict[str, str | list[str]]:
    """YouTube 웹 페이지(ytInitialPlayerResponse) 또는 oEmbed에서 제목, 채널명, 설명문, 태그 추출."""
    import json
    import httpx

    info: dict[str, str | list[str]] = {
        "title": "",
        "author": "",
        "description": "",
        "keywords": [],
    }

    # 1. YouTube 웹 페이지 파싱 (상세 설명, 채널명, 키워드 확보)
    try:
        resp = httpx.get(
            f"https://www.youtube.com/watch?v={vid}",
            headers={
                "User-Agent": (
                    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
                ),
                "Accept-Language": "ko,en-US;q=0.9,en;q=0.8",
            },
            timeout=8,
        )
        if resp.status_code == 200:
            m = re.search(r"ytInitialPlayerResponse\s*=\s*({.+?});", resp.text)
            if m:
                data = json.loads(m.group(1))
                vd = data.get("videoDetails", {})
                if vd.get("title"):
                    info["title"] = str(vd["title"]).strip()
                if vd.get("author"):
                    info["author"] = str(vd["author"]).strip()
                if vd.get("shortDescription"):
                    info["description"] = str(vd["shortDescription"]).strip()
                if vd.get("keywords") and isinstance(vd["keywords"], list):
                    info["keywords"] = vd["keywords"]
    except Exception:  # noqa: BLE001
        pass

    # 2. oEmbed 폴백 (제목/작성자가 아직 없는 경우)
    if not info["title"] or not info["author"]:
        try:
            resp = httpx.get(
                "https://www.youtube.com/oembed",
                params={"url": f"https://www.youtube.com/watch?v={vid}", "format": "json"},
                timeout=8,
            )
            if resp.status_code == 200:
                data = resp.json() or {}
                if not info["title"] and data.get("title"):
                    info["title"] = str(data["title"]).strip()
                if not info["author"] and data.get("author_name"):
                    info["author"] = str(data["author_name"]).strip()
        except Exception:  # noqa: BLE001
            pass

    return info


def fetch_transcript(vid: str, *, preferred_languages: list[str] | None = None) -> str:
    """youtube-transcript-api 기반 자막 추출 (수동/자동 자막, 선호 언어 우선, 전체 언어 탐색)."""
    if preferred_languages is None:
        settings = get_settings()
        target_langs = settings.effective_preferred_languages
    else:
        target_langs = [
            lang.strip().lower() for lang in preferred_languages if lang.strip().lower() != "en"
        ] + ["en"]

    try:
        from youtube_transcript_api import YouTubeTranscriptApi

        api = YouTubeTranscriptApi()

        # 1. list() 지원 시 (1.x 이상 권장)
        if hasattr(api, "list"):
            try:
                tl = api.list(vid)
                transcript_obj = None
                # 선호 언어 + en 우선 검색
                try:
                    transcript_obj = tl.find_transcript(target_langs)
                except Exception:  # noqa: BLE001
                    pass

                # 수동 자막 전체 중 첫 번째
                if not transcript_obj and getattr(tl, "_manually_created_transcripts", None):
                    transcript_obj = next(iter(tl._manually_created_transcripts.values()), None)
                # 자동 생성 자막 전체 중 첫 번째
                if not transcript_obj and getattr(tl, "_generated_transcripts", None):
                    transcript_obj = next(iter(tl._generated_transcripts.values()), None)

                if transcript_obj:
                    snippets = transcript_obj.fetch()
                    parts = []
                    for snip in snippets:
                        txt = getattr(snip, "text", None)
                        if txt is None and isinstance(snip, dict):
                            txt = snip.get("text", "")
                        if txt:
                            parts.append(txt)
                    if parts:
                        return " ".join(parts).strip()
            except Exception:  # noqa: BLE001
                pass

        # 2. fetch() 폴백 (0.x 또는 list 실패 방어)
        try:
            try:
                fetched = api.fetch(vid, languages=target_langs)
            except Exception:  # noqa: BLE001
                fetched = api.fetch(vid)
            parts = []
            for snip in fetched:
                txt = getattr(snip, "text", None)
                if txt is None and isinstance(snip, dict):
                    txt = snip.get("text", "")
                if txt:
                    parts.append(txt)
            if parts:
                return " ".join(parts).strip()
        except Exception:  # noqa: BLE001
            pass

    except Exception:  # noqa: BLE001
        pass

    return ""


def extract_youtube_ytdlp(
    url: str,
    target_langs: list[str],
    settings: Settings,
) -> tuple[dict[str, Any], CaptionAcquisition]:
    """yt-dlp로 YouTube 상세 메타데이터 및 CC 자막 추출 시도."""
    try:
        import yt_dlp
    except ImportError:
        return {}, CaptionAcquisition(status="absent", error="yt-dlp not installed")

    ext_args = parse_ytdlp_extractor_args(
        getattr(settings, "ytdlp_extractor_args", "generic:impersonate")
    )
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

    info: dict[str, Any] = {}
    caption = CaptionAcquisition()
    try:
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            info = ydl.extract_info(url, download=False) or {}
            if info:
                caption = acquire_caption(info, target_langs, ydl)
    except Exception as e:
        logger.warning("yt-dlp metadata extraction failed for %s: %s", url, e)
    return info, caption


def transcribe_youtube_audio(
    url: str,
    canonical_url: str,
    *,
    target_langs: list[str],
    settings: Settings,
    duration_sec: float = 0.0,
) -> tuple[str, list[dict], float, str | None, bool, bool]:
    """무자막 유튜브 영상을 위한 오디오 추출 및 Gemini STT 전사 (3일 캐시 연동).

    Returns:
        (transcript_text, segments_data, duration_sec, stt_error_msg, cached_used, cached_saved)
    """
    from ...store.video_cache import (
        delete_cached_video_file,
        get_cached_video_file,
        save_video_file_to_cache,
    )

    effective_data_dir = getattr(settings, "data_dir", None)
    cached_file: Path | None = None
    cached_used = False
    cached_saved = False
    stt_error_msg: str | None = None
    transcript_text = ""
    segments_data: list[dict] = []

    if effective_data_dir:
        cached_file = get_cached_video_file(
            effective_data_dir,
            url,
            canonical_url=canonical_url,
            max_age_sec=getattr(settings, "video_cache_ttl_sec", 259200),
        )
        if cached_file:
            logger.info("Using cached video media file: %s for %s", cached_file, url)
            cached_used = True

    ffmpeg_exec = find_ffmpeg_executable(settings.ffmpeg_bin)
    if not ffmpeg_exec:
        stt_error_msg = f"ffmpeg binary not found ({settings.ffmpeg_bin})"
        logger.info("ffmpeg binary not found (%s), skipping YouTube audio extraction", settings.ffmpeg_bin)
        return transcript_text, segments_data, duration_sec, stt_error_msg, cached_used, cached_saved

    # 1. 캐시된 오디오가 있으면 즉시 STT 시도
    if cached_file:
        try:
            stt_provider = get_transcript_provider(settings)
            lang_code = settings.stt_language or (target_langs[0] if target_langs else "ko")
            stt_result = stt_provider.transcribe(cached_file, language=lang_code, timestamps=True)
            transcript_text = stt_result.full_text
            segments_data = [s.model_dump() for s in stt_result.segments]
            if not transcript_text:
                stt_error_msg = "Cached STT returned empty transcript"
                if effective_data_dir:
                    delete_cached_video_file(effective_data_dir, url, canonical_url=canonical_url)
                cached_file = None
            else:
                if effective_data_dir:
                    delete_cached_video_file(effective_data_dir, url, canonical_url=canonical_url)
                return transcript_text, segments_data, duration_sec, None, cached_used, cached_saved
        except Exception as e:
            stt_error_msg = f"{type(e).__name__}: {e}"
            logger.warning("Cached audio STT transcription failed for %s: %s", url, e)
            if effective_data_dir:
                delete_cached_video_file(effective_data_dir, url, canonical_url=canonical_url)
            cached_file = None

    # 2. 캐시가 없거나 캐시 STT 실패 시 yt-dlp 다운로드
    try:
        import yt_dlp
    except ImportError:
        stt_error_msg = "yt-dlp is not installed"
        return transcript_text, segments_data, duration_sec, stt_error_msg, cached_used, cached_saved

    ext_args = parse_ytdlp_extractor_args(
        getattr(settings, "ytdlp_extractor_args", "generic:impersonate")
    )

    with tempfile.TemporaryDirectory(prefix="claire_yt_audio_") as tmp_dir:
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
            try:
                with yt_dlp.YoutubeDL(audio_opts) as ydl:
                    ydl.download([url])
            except Exception as dl_err:
                logger.debug("Primary audio format download failed for %s, trying fallback: %s", url, dl_err)
                fallback_opts = dict(audio_opts)
                fallback_opts["format"] = "ba/b[height<=360]/b"
                with yt_dlp.YoutubeDL(fallback_opts) as ydl:
                    ydl.download([url])

            audio_candidates = [
                p for p in Path(tmp_dir).glob("audio.*")
                if p.is_file() and not p.name.endswith((".part", ".ytdl"))
            ]
            if audio_candidates:
                downloaded_file = audio_candidates[0]
                stt_provider = get_transcript_provider(settings)
                lang_code = settings.stt_language or (target_langs[0] if target_langs else "ko")
                stt_result = stt_provider.transcribe(downloaded_file, language=lang_code, timestamps=True)
                transcript_text = stt_result.full_text
                segments_data = [s.model_dump() for s in stt_result.segments]
                if not duration_sec and stt_result.duration_sec:
                    duration_sec = stt_result.duration_sec
                if not transcript_text:
                    stt_error_msg = "STT provider returned empty transcript"
                else:
                    if effective_data_dir:
                        delete_cached_video_file(effective_data_dir, url, canonical_url=canonical_url)
        except Exception as e:
            stt_error_msg = f"{type(e).__name__}: {e}"
            logger.warning("YouTube audio extraction & STT failed for %s: %s", url, e)
        finally:
            if not transcript_text and downloaded_file and downloaded_file.is_file() and effective_data_dir:
                saved_path = save_video_file_to_cache(
                    effective_data_dir,
                    url,
                    downloaded_file,
                    canonical_url=canonical_url,
                )
                if saved_path:
                    cached_saved = True
                    logger.info("Saved downloaded YouTube audio media to 3-day cache: %s", saved_path)

    return transcript_text, segments_data, duration_sec, stt_error_msg, cached_used, cached_saved


def fetch_youtube(
    url: str,
    *,
    full_content: bool = False,
    preferred_languages: list[str] | None = None,
    settings: Settings | None = None,
) -> Document:
    vid = video_id(url)
    if not vid:
        raise FetchError(f"no youtube video id in {url}")

    settings = settings or get_settings()
    target_langs = (
        preferred_languages
        if preferred_languages is not None
        else settings.effective_preferred_languages
    )
    canonical_url = f"https://youtube.com/watch?v={vid}"

    # 1. Tier 1: Fast-Path 시도 (youtube-transcript-api + 경량 메타데이터)
    details = fetch_video_details(vid)
    transcript = fetch_transcript(vid, preferred_languages=preferred_languages)

    title = str(details.get("title") or "").strip()
    author = str(details.get("author") or "").strip()
    description = str(details.get("description") or "").strip()
    keywords = details.get("keywords") or []

    chapters: list[dict[str, Any]] = []
    duration_sec: float = 0.0
    is_stt = False
    segments_data: list[dict[str, Any]] = []
    stt_error_msg: str | None = None
    cached_used = False
    cached_saved = False
    caption_status: str | None = "available" if transcript else None
    caption_language: str | None = target_langs[0] if (transcript and target_langs) else None
    transcript_source: str | None = "youtube_transcript_api" if transcript else None

    # 2. Tier 2: 1차 자막 부재 시 또는 제목 부재 시 yt-dlp 에스컬레이션 시도
    # (1차에서 자막과 제목이 확보된 경우 yt-dlp 호출을 생략하여 0.3초 초고속 Fast-path 유지)
    if not transcript or not title:
        emit_progress("YouTube 메타데이터 및 CC 확인 중 (yt-dlp)…")
        ytdlp_info, caption_acq = extract_youtube_ytdlp(url, target_langs, settings)
        if ytdlp_info:
            if not title and ytdlp_info.get("title"):
                title = str(ytdlp_info["title"]).strip()
            if not author and (ytdlp_info.get("uploader") or ytdlp_info.get("channel")):
                author = str(ytdlp_info.get("uploader") or ytdlp_info.get("channel")).strip()
            if not description and ytdlp_info.get("description"):
                description = str(ytdlp_info["description"]).strip()
            if not keywords and (ytdlp_info.get("tags") or ytdlp_info.get("categories")):
                keywords = ytdlp_info.get("tags") or ytdlp_info.get("categories") or []
            if ytdlp_info.get("duration"):
                try:
                    duration_sec = float(ytdlp_info["duration"])
                except (ValueError, TypeError):
                    duration_sec = 0.0
            if ytdlp_info.get("chapters"):
                chapters = ytdlp_info["chapters"]

        if not transcript and caption_acq.status == "available" and caption_acq.text:
            transcript = caption_acq.text
            caption_status = caption_acq.status
            caption_language = caption_acq.language
            transcript_source = caption_acq.source

    # 3. Tier 3: 1·2차 모두 자막이 없고, STT 활성화 시 오디오 추출 및 Gemini STT 수행
    if not transcript and settings.enable_video_transcription:
        emit_progress("YouTube 음성 추출 및 STT 전사 처리 중…")
        (
            stt_text,
            segments_data,
            stt_dur,
            stt_error_msg,
            cached_used,
            cached_saved,
        ) = transcribe_youtube_audio(
            url,
            canonical_url,
            target_langs=target_langs,
            settings=settings,
            duration_sec=duration_sec,
        )
        if stt_text:
            transcript = stt_text
            is_stt = True
            transcript_source = "stt"
            if stt_dur and not duration_sec:
                duration_sec = stt_dur

    title = title or f"YouTube {vid}"

    # 본문 텍스트 구성 (자막 + 설명문 결합, 또는 자막/설명문 단독)
    text_sections: list[str] = []
    if author:
        text_sections.append(f"채널: {author}")
    if duration_sec > 0:
        mins = int(duration_sec // 60)
        secs = int(duration_sec % 60)
        text_sections.append(f"재생 시간: {mins}분 {secs}초 ({duration_sec:.1f}초)")

    formatted_chapters = format_chapters(chapters)
    if formatted_chapters:
        text_sections.append(f"[영상 챕터]\n{formatted_chapters}")

    if transcript:
        header = "[영상 음성 전사 (STT)]" if is_stt else "[영상 자막]"
        text_sections.append(f"{header}\n{transcript}")
    elif stt_error_msg:
        cached_notice = " (다운로드된 영상 미디어는 사흘간 로컬 캐시로 안전하게 보관되며, 재적재 시 즉시 재사용됩니다.)" if cached_saved else ""
        text_sections.append(f"[영상 자막]\n(음성 전사 처리 중 오류가 발생했습니다: {stt_error_msg}{cached_notice})")

    if description:
        text_sections.append(f"[영상 설명]\n{description}")

    if keywords and isinstance(keywords, list):
        text_sections.append(f"[태그]\n{', '.join(str(k) for k in keywords if k)}")

    text = "\n\n".join(text_sections).strip()
    if not text:
        raise FetchError(f"empty transcript and details for {vid}")

    budget = 0 if full_content else settings.raw_char_budget
    raw_text, is_truncated, orig_chars, raw_chars = slice_document_text(
        text or "", budget, strategy=settings.slicing_strategy
    )
    return Document(
        url=url,
        canonical_url=canonical_url,
        title=title,
        author=author or None,
        raw_text=raw_text,
        source_type="youtube",
        content_hash=content_hash(text),
        partial=bool(not transcript or is_truncated),
        meta={
            "video_id": vid,
            "raw_truncated": is_truncated,
            "orig_chars": orig_chars,
            "raw_chars": raw_chars,
            "has_transcript": bool(transcript),
            "duration_sec": duration_sec,
            "chapters": [
                {
                    "title": ch.get("title"),
                    "start_time": ch.get("start_time"),
                    "end_time": ch.get("end_time"),
                }
                for ch in chapters
                if isinstance(ch, dict)
            ]
            if chapters
            else [],
            "is_stt": is_stt,
            "stt": is_stt,
            "stt_error": stt_error_msg,
            "transcript_segments": segments_data,
            "transcript_source": transcript_source,
            "video_cached": cached_saved or cached_used,
            "video_cache_used": cached_used,
            "caption_status": caption_status,
            "caption_language": caption_language,
        },
    )
