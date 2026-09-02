"""[비디오 미디어 캐시] 처리/적재 실패 시 비디오/오디오 미디어 사흘(3일) 보관 및 재사용.

다운로드된 대용량 비디오/오디오 스트림의 처리(STT/적재)가 실패할 경우,
data/cache/video/ 에 최대 사흘(259,200초)간 로컬 캐시로 저장한다.
이후 재적재(video-reprocess/ingest) 시 원격 재다운로드 없이 캐시된 미디어를 즉시 재사용한다.
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import re
import shutil
import time
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

# 기본 캐시 보존 기간: 사흘 (3일 = 72시간 = 259,200초)
DEFAULT_VIDEO_CACHE_TTL_SEC: int = 3 * 24 * 3600  # 259,200s


def get_video_cache_dir(data_dir: Path) -> Path:
    """비디오 캐시 디렉터리 경로 반환 및 생성 (data/cache/video)."""
    d = data_dir / "cache" / "video"
    d.mkdir(parents=True, exist_ok=True)
    return d


def extract_video_id_from_url(url: str | None) -> str | None:
    """URL에서 비디오 고유 식별자(Brightcove ID, YouTube ID 등) 추출."""
    if not url:
        return None
    # Brightcove videoId=...
    m = re.search(r"videoId=(\d+)", url)
    if m:
        return f"brightcove_{m.group(1)}"
    # VMware Explore /video/<id>
    m = re.search(r"/explore/video/(\d+)", url)
    if m:
        return f"brightcove_{m.group(1)}"
    # YouTube v=... or youtu.be/...
    m = re.search(r"(?:v=|\/)([0-9A-Za-z_-]{11})(?:[&?]|$)", url)
    if m:
        return f"youtube_{m.group(1)}"
    # Vimeo /<id>
    m = re.search(r"vimeo\.com\/(\d+)", url)
    if m:
        return f"vimeo_{m.group(1)}"
    return None


def compute_video_cache_keys(url: str, canonical_url: str | None = None) -> list[str]:
    """URL, 정규화 URL, 비디오 ID로부터 후보 캐시 키 목록 반환."""
    keys: list[str] = []

    # 1. 비디오 ID 기반 고유 키 (가장 안정적)
    vid = extract_video_id_from_url(url) or (extract_video_id_from_url(canonical_url) if canonical_url else None)
    if vid:
        keys.append(vid)

    # 2. canonical_url SHA256 해시
    if canonical_url:
        h_canon = hashlib.sha256(canonical_url.strip().encode("utf-8")).hexdigest()[:32]
        if h_canon not in keys:
            keys.append(h_canon)

    # 3. 원본 URL SHA256 해시
    if url:
        h_url = hashlib.sha256(url.strip().encode("utf-8")).hexdigest()[:32]
        if h_url not in keys:
            keys.append(h_url)

    return keys


def get_cached_video_file(
    data_dir: Path,
    url: str,
    *,
    canonical_url: str | None = None,
    max_age_sec: float = DEFAULT_VIDEO_CACHE_TTL_SEC,
) -> Path | None:
    """해당 URL에 대해 사흘 이내에 캐시된 유효한 비디오/오디오 미디어 파일 탐색.

    만료된 캐시는 자동 삭제하고 None 반환. 유효 파일 발견 시 Path 반환.
    """
    cache_dir = get_video_cache_dir(data_dir)
    keys = compute_video_cache_keys(url, canonical_url)
    now = time.time()

    for key in keys:
        # 캐시 디렉터리 내 key.* 패턴 파일 탐색 (메타데이터 .json 및 임시파일 제외)
        for cand in cache_dir.glob(f"{key}.*"):
            if cand.name.endswith(".meta.json") or cand.name.endswith((".part", ".ytdl", ".tmp")):
                continue
            if not cand.is_file():
                continue

            try:
                stat = cand.stat()
                age = now - stat.st_mtime
                if age > max_age_sec:
                    logger.info(
                        "Expired video cache found for %s (age %.1fh > %.1fh), removing: %s",
                        url,
                        age / 3600,
                        max_age_sec / 3600,
                        cand,
                    )
                    cand.unlink(missing_ok=True)
                    meta_path = cache_dir / f"{key}.meta.json"
                    meta_path.unlink(missing_ok=True)
                    continue

                if stat.st_size > 0:
                    logger.info(
                        "Found valid cached video file (%s, %.1fMB, age %.1fh) for %s",
                        cand.name,
                        stat.st_size / (1024 * 1024),
                        age / 3600,
                        url,
                    )
                    return cand
            except OSError:
                continue

    return None


def save_video_file_to_cache(
    data_dir: Path,
    url: str,
    source_path: Path,
    *,
    canonical_url: str | None = None,
) -> Path | None:
    """다운로드된 미디어 파일을 사흘(3일) 보존용 캐시로 안전하게 복사."""
    if not source_path.is_file() or source_path.stat().st_size == 0:
        return None

    cache_dir = get_video_cache_dir(data_dir)
    keys = compute_video_cache_keys(url, canonical_url)
    if not keys:
        return None

    primary_key = keys[0]
    ext = source_path.suffix.lower() or ".mp4"
    target_path = cache_dir / f"{primary_key}{ext}"
    meta_path = cache_dir / f"{primary_key}.meta.json"
    temp_target = cache_dir / f"{primary_key}.tmp_{os.getpid()}{ext}"

    try:
        shutil.copyfile(source_path, temp_target)
        os.replace(temp_target, target_path)

        meta = {
            "url": url,
            "canonical_url": canonical_url or url,
            "cached_at": time.time(),
            "size_bytes": target_path.stat().st_size,
            "filename": target_path.name,
            "ttl_days": 3,
        }
        meta_path.write_text(json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8")
        logger.info(
            "Saved video cache (%s, %.1fMB) for %s (preserved for 3 days)",
            target_path.name,
            target_path.stat().st_size / (1024 * 1024),
            url,
        )
        return target_path
    except Exception as e:
        logger.warning("Failed to save video cache for %s: %s", url, e)
        if temp_target.exists():
            temp_target.unlink(missing_ok=True)
        return None


def delete_cached_video_file(
    data_dir: Path,
    url: str,
    *,
    canonical_url: str | None = None,
) -> bool:
    """해당 URL의 비디오 캐시 파일 및 메타데이터 삭제 (적재 성공 시 정리)."""
    cache_dir = get_video_cache_dir(data_dir)
    keys = compute_video_cache_keys(url, canonical_url)
    deleted = False

    for key in keys:
        for p in list(cache_dir.glob(f"{key}.*")):
            try:
                p.unlink(missing_ok=True)
                deleted = True
            except OSError:
                pass
    return deleted


def prune_expired_video_cache(
    data_dir: Path,
    max_age_sec: float = DEFAULT_VIDEO_CACHE_TTL_SEC,
) -> int:
    """사흘(3일) 이상 경과한 만료 비디오 캐시 일괄 삭제."""
    cache_dir = get_video_cache_dir(data_dir)
    if not cache_dir.exists():
        return 0

    now = time.time()
    pruned = 0

    for cand in list(cache_dir.glob("*")):
        if not cand.is_file():
            continue
        try:
            if now - cand.stat().st_mtime > max_age_sec:
                cand.unlink(missing_ok=True)
                pruned += 1
        except OSError:
            pass

    return pruned
