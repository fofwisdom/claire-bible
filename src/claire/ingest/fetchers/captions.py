"""비디오 플랫폼이 제공하는 CC 자막의 선택·다운로드·검증.

CC는 원격 음성에서 새로 생성하는 STT 결과가 아니라 발행자가 제공한 원문이다.
따라서 선호 언어의 유효한 CC를 먼저 보존하고, CC가 없을 때만 호출자가 STT로
폴백할 수 있도록 획득 상태와 출처를 명시적으로 반환한다.
"""

from __future__ import annotations

import hashlib
import logging
import re
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

logger = logging.getLogger(__name__)

MAX_CAPTION_BYTES = 8 * 1024 * 1024
_SAFE_EXT_RE = re.compile(r"^[a-z0-9]{1,8}$")
_VTT_TIMING_RE = re.compile(
    r"(?m)^(?:\d{2}:)?\d{2}:\d{2}[.,]\d{3}\s+-->\s+"
    r"(?:\d{2}:)?\d{2}:\d{2}[.,]\d{3}(?:[ \t]+.*)?$"
)


@dataclass(frozen=True)
class CaptionCandidate:
    """선호도 정렬을 마친 단일 yt-dlp 자막 트랙."""

    language: str
    source: str  # manual_caption | automatic_caption
    format: str
    track: dict[str, Any]


@dataclass(frozen=True)
class CaptionAcquisition:
    """CC 획득 결과. URL 자체는 만료 토큰 노출 방지를 위해 보존하지 않는다."""

    status: str = "absent"  # available | absent | download_failed | invalid
    text: str = ""
    language: str | None = None
    source: str | None = None
    format: str | None = None
    content_hash: str | None = None
    error: str | None = None


def normalize_language_tag(value: object) -> str:
    """BCP 47 비교용 정규화. 원래 표기는 CaptionCandidate에 그대로 보존한다."""
    return str(value or "").strip().replace("_", "-").lower()


def _language_match_rank(preferred: str, available: str) -> int | None:
    pref = normalize_language_tag(preferred)
    actual = normalize_language_tag(available)
    if not pref or not actual:
        return None
    if pref == actual:
        return 0
    if pref.split("-", 1)[0] == actual.split("-", 1)[0]:
        return 1
    return None


def _track_rank(track: dict[str, Any]) -> tuple[int, int, str]:
    """인라인 > 직접 HTTPS VTT > 직접 HTTP VTT > 조각형/기타 순서."""
    if track.get("data") is not None:
        return (0, 0, "")

    url = str(track.get("url") or "")
    parts = urlsplit(url)
    ext = str(track.get("ext") or "").strip().lower()
    protocol = str(track.get("protocol") or "").strip().lower()
    direct_vtt = ext == "vtt" and "m3u8" not in protocol and parts.path.lower().endswith(".vtt")
    https_rank = 0 if parts.scheme.lower() == "https" else 1
    if direct_vtt:
        return (1, https_rank, url)
    return (2, https_rank, url)


def select_caption_candidates(
    info: dict[str, Any], target_languages: list[str]
) -> list[CaptionCandidate]:
    """언어 우선순위·정확도·수동/자동·전송 형식 순으로 CC 후보를 정렬한다."""
    ranked: list[tuple[tuple[Any, ...], CaptionCandidate]] = []
    collections = (
        ("manual_caption", info.get("subtitles") or {}, 0),
        ("automatic_caption", info.get("automatic_captions") or {}, 1),
    )

    for source, tracks_by_language, source_rank in collections:
        if not isinstance(tracks_by_language, dict):
            continue
        for language, tracks in tracks_by_language.items():
            matches = [
                (preference_index, match_rank)
                for preference_index, preferred in enumerate(target_languages)
                if (match_rank := _language_match_rank(preferred, str(language))) is not None
            ]
            if not matches or not isinstance(tracks, list):
                continue
            preference_index, match_rank = min(matches)
            for track in tracks:
                if not isinstance(track, dict):
                    continue
                if track.get("data") is None and not str(track.get("url") or "").strip():
                    continue
                fmt = str(track.get("ext") or "vtt").strip().lower() or "vtt"
                candidate = CaptionCandidate(
                    language=str(language),
                    source=source,
                    format=fmt,
                    track=dict(track),
                )
                ranked.append(
                    (
                        (
                            preference_index,
                            match_rank,
                            source_rank,
                            *_track_rank(track),
                        ),
                        candidate,
                    )
                )

    ranked.sort(key=lambda item: item[0])

    # 동일 URL의 HTTP/HTTPS 변형과 같은 중복은 선호도가 높은 첫 후보만 유지한다.
    candidates: list[CaptionCandidate] = []
    seen: set[tuple[str, str, str]] = set()
    for _, candidate in ranked:
        data = candidate.track.get("data")
        if data is not None:
            identity = (
                normalize_language_tag(candidate.language),
                candidate.source,
                "data:" + hashlib.sha256(str(data).encode("utf-8", "ignore")).hexdigest(),
            )
        else:
            parts = urlsplit(str(candidate.track.get("url") or ""))
            identity = (
                normalize_language_tag(candidate.language),
                candidate.source,
                f"{parts.netloc.lower()}{parts.path}?{parts.query}",
            )
        if identity in seen:
            continue
        seen.add(identity)
        candidates.append(candidate)
    return candidates


def is_valid_speech_vtt(text: str) -> bool:
    """타임드 음성 큐가 있는 WebVTT인지 검증하고 썸네일 스프라이트를 거부한다."""
    value = (text or "").lstrip("\ufeff").strip()
    if not value or not value.startswith("WEBVTT"):
        return False
    if ".jpg#xywh=" in value or ".png#xywh=" in value or "xywh=" in value:
        return False
    if not _VTT_TIMING_RE.search(value):
        return False

    clean = re.sub(r"(?m)^WEBVTT.*$|^NOTE.*$|^X-TIMESTAMP-MAP=.*$", "", value)
    clean = _VTT_TIMING_RE.sub("", clean)
    clean = re.sub(r"<[^>]+>", "", clean)
    letters = sum(1 for char in clean if char.isalnum())
    return letters >= 20


def _read_caption(candidate: CaptionCandidate, ydl: Any, info: dict[str, Any]) -> str:
    data = candidate.track.get("data")
    if data is not None:
        text = str(data)
        if len(text.encode("utf-8")) > MAX_CAPTION_BYTES:
            raise ValueError("caption exceeds size limit")
        return text

    track = dict(candidate.track)
    parent_headers = info.get("http_headers")
    if "http_headers" not in track and isinstance(parent_headers, dict):
        track["http_headers"] = parent_headers
    known_size = track.get("filesize") or track.get("filesize_approx")
    if isinstance(known_size, (int, float)) and known_size > MAX_CAPTION_BYTES:
        raise ValueError("caption exceeds size limit")

    ext = candidate.format if _SAFE_EXT_RE.fullmatch(candidate.format) else "vtt"
    with tempfile.TemporaryDirectory(prefix="claire_caption_") as temp_dir:
        path = Path(temp_dir) / f"caption.{ext}"
        ydl.dl(str(path), track, subtitle=True)
        if not path.is_file():
            raise OSError("caption downloader produced no file")
        if path.stat().st_size > MAX_CAPTION_BYTES:
            raise ValueError("caption exceeds size limit")
        return path.read_text(encoding="utf-8-sig")


def acquire_caption(
    info: dict[str, Any], target_languages: list[str], ydl: Any
) -> CaptionAcquisition:
    """선호 CC를 획득한다. 광고된 트랙의 실패와 실제 부재를 구분한다."""
    candidates = select_caption_candidates(info, target_languages)
    if not candidates:
        return CaptionAcquisition(status="absent")

    failures: list[str] = []
    downloaded_invalid = False
    for candidate in candidates:
        try:
            text = _read_caption(candidate, ydl, info)
        except Exception as exc:  # noqa: BLE001
            failure = type(exc).__name__
            failures.append(failure)
            logger.warning(
                "Caption download failed (language=%s, source=%s, format=%s, error=%s)",
                candidate.language,
                candidate.source,
                candidate.format,
                failure,
            )
            continue

        if not is_valid_speech_vtt(text):
            downloaded_invalid = True
            logger.info(
                "Rejected non-speech caption track (language=%s, source=%s, format=%s)",
                candidate.language,
                candidate.source,
                candidate.format,
            )
            continue

        return CaptionAcquisition(
            status="available",
            text=text.strip(),
            language=candidate.language,
            source=candidate.source,
            format=candidate.format,
            content_hash=hashlib.sha256(text.encode("utf-8")).hexdigest(),
        )

    if failures and len(failures) == len(candidates):
        return CaptionAcquisition(
            status="download_failed",
            error=",".join(dict.fromkeys(failures)),
        )
    if downloaded_invalid:
        return CaptionAcquisition(status="invalid")
    return CaptionAcquisition(status="absent")
