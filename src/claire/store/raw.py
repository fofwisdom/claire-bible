"""[재적재 Layer 2] fetched artifact 원본 보관.

fetcher 가 가져온 원본(HTML/transcript/PDF 추출텍스트)을 zstandard(.txt.zst)로 파일 저장한다.
레거시 gzip(.txt.gz)과의 투명한 Dual-Read 하위 호환성을 보장한다.
나중에 추출 알고리즘(prompt/모델)을 바꿔도 *재fetch 없이* raw_text 부터 재생할 수 있게
한다. 용량 주의(사용자 요구) → zstd 압축, 텍스트 위주. 임의 prune 은 하지 않는다
(데이터 삭제 금지 원칙). 용량은 doctor/stats 로 모니터만 한다.

레이아웃:
  data/raw/artifacts/<doc_id>.txt.zst  # zstandard 압축 추출 원본 (신규 정본)
  data/raw/artifacts/<doc_id>.txt.gz   # gzip 압축 추출 원본 (레거시 하위 호환)
  data/raw/files/<inbox_id>_<name>     # 텔레그램으로 받은 원본 파일(pdf 등) 그대로
  data/images/<doc_id>_<i>.<ext>       # 본문 이미지 후보 로컬 보존(원본 사이트/링크 삭제 대비)
"""

from __future__ import annotations

import gzip
import hashlib
import io
import logging
import os
import shutil
import tempfile
import threading
from pathlib import Path
from typing import Any

import zstandard

from ..ingest.fetchers.http_policy import BROWSER_USER_AGENT
from ..ontology.base import Document, SourceAttachment

logger = logging.getLogger(__name__)

_tls = threading.local()


def _get_compressor(level: int = 3, write_checksum: bool = True) -> zstandard.ZstdCompressor:
    """스레드 로컬 zstd 압축기 인스턴스를 반환한다.

    C-API 컨텍스트(ZSTD_CCtx) 재사용을 통해 반복 생성에 따른 힙 할당 및 GC 오버헤드를 제거하며,
    스레드 로컬 격리(threading.local)를 통해 libzstd C-API 다중 스레드 동시 접근 크래시(SIGSEGV)를 방어한다.
    """
    compressors = getattr(_tls, "compressors", None)
    if compressors is None:
        compressors = {}
        _tls.compressors = compressors
    key = (level, write_checksum)
    c = compressors.get(key)
    if c is None:
        c = zstandard.ZstdCompressor(level=level, write_checksum=write_checksum)
        compressors[key] = c
    return c


def _get_decompressor() -> zstandard.ZstdDecompressor:
    """스레드 로컬 zstd 압축 해제기 인스턴스를 반환한다.

    C-API 컨텍스트(ZSTD_DCtx) 재사용을 통해 반복 생성 오버헤드를 제거하고
    스레드 안전성(thread-safety)을 보장한다.
    """
    d = getattr(_tls, "decompressor", None)
    if d is None:
        d = zstandard.ZstdDecompressor()
        _tls.decompressor = d
    return d


def _sync_dir(dir_path: Path) -> None:
    """디렉토리 메타데이터를 디스크에 강제 동기화(fsync)한다."""
    try:
        dir_fd = os.open(dir_path, os.O_RDONLY)
        try:
            os.fsync(dir_fd)
        finally:
            os.close(dir_fd)
    except OSError:
        pass


def _artifacts_dir(data_dir: Path) -> Path:
    d = data_dir / "raw" / "artifacts"
    d.mkdir(parents=True, exist_ok=True)
    return d


def _files_dir(data_dir: Path) -> Path:
    d = data_dir / "raw" / "files"
    d.mkdir(parents=True, exist_ok=True)
    return d


def artifact_paths(data_dir: Path, doc_id: str) -> list[Path]:
    """doc_id에 대해 존재하는 artifact 파일 목록(.txt.zst 및 .txt.gz)을 반환한다."""
    artifacts = _artifacts_dir(data_dir)
    candidates = [
        artifacts / f"{doc_id}.txt.zst",
        artifacts / f"{doc_id}.txt.gz",
    ]
    return [p for p in candidates if p.exists()]


def remove_artifact(data_dir: Path, doc_id: str) -> list[Path]:
    """doc_id에 대응하는 모든 artifact(.txt.zst 및 .txt.gz)를 안전하게 삭제하고 삭제된 경로 목록을 반환한다."""
    paths = artifact_paths(data_dir, doc_id)
    removed: list[Path] = []
    for p in paths:
        p.unlink(missing_ok=True)
        removed.append(p)
    return removed


def save_artifact(data_dir: Path, doc_id: str, text: str, level: int = 3) -> str:
    """추출 원본 텍스트를 zstandard로 원자적으로 저장. 저장 경로(문자열) 반환.

    원자적 저장 절차:
      1. 대상 디렉토리에 .tmp- 접두어로 임시 파일 생성
      2. _get_compressor(level=level)로 압축 바이트 작성 (스레드 로컬 C-API 컨텍스트 재사용)
      3. stream.flush() 및 os.fsync() 호출 (데이터 블록 디스크 플러시)
      4. os.replace()로 <doc_id>.txt.zst에 원자적 치환
      5. 부모 디렉토리 fsync 방어적 호출 (_sync_dir)
      6. 신규 zst 저장이 안전하게 완료된 후 기존 동일 doc_id의 .txt.gz가 존재하면 안전하게 삭제
      7. 예외 발생 시 임시 파일 정리 및 예외 전파 (기존 .gz 무손실 보존)
    """
    artifacts = _artifacts_dir(data_dir)
    destination = artifacts / f"{doc_id}.txt.zst"
    compressor = _get_compressor(level=level)
    payload = (text or "").encode("utf-8")
    compressed = compressor.compress(payload)

    fd, temp_name = tempfile.mkstemp(
        prefix=f".tmp-{doc_id}-",
        suffix=".zst",
        dir=destination.parent,
    )
    temp_path = Path(temp_name)
    try:
        with os.fdopen(fd, "wb") as stream:
            stream.write(compressed)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temp_path, destination)
        _sync_dir(destination.parent)

        # 성공 후 동일 doc_id의 .txt.gz가 존재하면 안전하게 삭제
        legacy_gz = artifacts / f"{doc_id}.txt.gz"
        legacy_gz.unlink(missing_ok=True)
    except Exception:
        temp_path.unlink(missing_ok=True)
        raise

    return str(destination)


def load_artifact(data_dir: Path, doc_id: str) -> str | None:
    """추출 원본 텍스트를 Dual-Read 프로토콜로 로드한다.

    1순위: <doc_id>.txt.zst 확인 -> _get_decompressor().decompress(...) 성공 시 반환
    손상/zstandard.ZstdError/EOFError/OSError/UnicodeDecodeError 발생 시 경고 로깅 후 2순위로 투명 폴백
    2순위: <doc_id>.txt.gz 확인 -> gzip.open(..., "rt", encoding="utf-8")로 읽어 반환
    둘 다 없으면 None 반환
    """
    artifacts = _artifacts_dir(data_dir)
    zst_path = artifacts / f"{doc_id}.txt.zst"
    gz_path = artifacts / f"{doc_id}.txt.gz"

    if zst_path.exists():
        try:
            decompressor = _get_decompressor()
            raw_bytes = zst_path.read_bytes()
            try:
                decompressed = decompressor.decompress(raw_bytes)
            except zstandard.ZstdError:
                decompressed = decompressor.stream_reader(io.BytesIO(raw_bytes)).read()
            return decompressed.decode("utf-8")
        except (zstandard.ZstdError, EOFError, OSError, ValueError, UnicodeDecodeError) as e:
            logger.warning(
                "Failed to decompress zstd artifact for doc_id=%s (%s: %s). Falling back to legacy .gz",
                doc_id,
                type(e).__name__,
                e,
            )

    if gz_path.exists():
        try:
            with gzip.open(gz_path, "rt", encoding="utf-8") as f:
                return f.read()
        except Exception as e:
            logger.warning(
                "Failed to decompress legacy gzip artifact for doc_id=%s (%s: %s)",
                doc_id,
                type(e).__name__,
                e,
            )
            return None

    return None


def migrate_artifacts(
    data_dir: Path,
    apply: bool = False,
    level: int = 3,
    stop_on_error: bool = False,
    batch_fsync_interval: int = 100,
) -> dict[str, Any]:
    """data/raw/artifacts/*.txt.gz를 탐색하여 zstandard(.txt.zst)로 마이그레이션한다.

    SHA-256 라운드트립 무결성 검증 기반의 안전 변환 로직을 수행한다.
    대량 마이그레이션 시 개별 파일 fsync로 데이터 영속성을 보장하되,
    디렉토리 메타데이터 fsync는 배치 단위(batch_fsync_interval)로 묶어 I/O 병목을 제거한다.
    통계 반환:
      {total, migrated, already_zst, errors, error_details, bytes_before, bytes_after, savings_pct, dry_run}
    """
    artifacts_dir = _artifacts_dir(data_dir)
    gz_files = sorted(p for p in artifacts_dir.glob("*.txt.gz") if not p.name.startswith(".tmp-"))
    zst_files = sorted(p for p in artifacts_dir.glob("*.txt.zst") if not p.name.startswith(".tmp-"))

    gz_doc_map = {p.name[:-7]: p for p in gz_files}
    zst_doc_map = {p.name[:-8]: p for p in zst_files}
    all_doc_ids = sorted(set(gz_doc_map.keys()) | set(zst_doc_map.keys()))

    total = len(all_doc_ids)
    migrated = 0
    already_zst = 0
    errors: list[dict[str, Any]] = []
    bytes_before = 0
    bytes_after = 0

    compressor = _get_compressor(level=level)
    decompressor = _get_decompressor()
    migrated_since_last_sync = 0

    try:
        for doc_id in all_doc_ids:
            gz_path = gz_doc_map.get(doc_id)
            zst_path = zst_doc_map.get(doc_id)

            # 1. 이미 .txt.zst만 존재하는 경우
            if zst_path is not None and gz_path is None:
                already_zst += 1
                continue

            # 2. .txt.zst와 .txt.gz가 둘 다 존재하는 경우
            if zst_path is not None and gz_path is not None:
                already_zst += 1
                if apply:
                    try:
                        raw_bytes = zst_path.read_bytes()
                        try:
                            _ = decompressor.decompress(raw_bytes)
                        except zstandard.ZstdError:
                            _ = decompressor.stream_reader(io.BytesIO(raw_bytes)).read()
                        gz_path.unlink(missing_ok=True)
                        migrated_since_last_sync += 1
                        if batch_fsync_interval > 0 and migrated_since_last_sync >= batch_fsync_interval:
                            _sync_dir(artifacts_dir)
                            migrated_since_last_sync = 0
                    except Exception as e:
                        errors.append({"doc_id": doc_id, "error": f"Existing zst verification failed: {e}"})
                        if stop_on_error:
                            break
                continue

            # 3. .txt.gz만 존재하는 경우 -> 마이그레이션 수행
            assert gz_path is not None
            gz_size = gz_path.stat().st_size
            dest_zst = artifacts_dir / f"{doc_id}.txt.zst"

            try:
                # A. gzip 읽기 및 원본 SHA-256 계산
                with gzip.open(gz_path, "rt", encoding="utf-8") as f:
                    content = f.read()
                raw_payload = content.encode("utf-8")
                orig_sha256 = hashlib.sha256(raw_payload).hexdigest()

                # B. zstd 압축 및 라운드트립 해시 검증
                compressed_bytes = compressor.compress(raw_payload)
                try:
                    roundtrip_bytes = decompressor.decompress(compressed_bytes)
                except zstandard.ZstdError:
                    roundtrip_bytes = decompressor.stream_reader(io.BytesIO(compressed_bytes)).read()
                roundtrip_sha256 = hashlib.sha256(roundtrip_bytes).hexdigest()

                if orig_sha256 != roundtrip_sha256:
                    raise ValueError(
                        f"SHA-256 mismatch during roundtrip: expected {orig_sha256}, got {roundtrip_sha256}"
                    )

                if apply:
                    # C. 원자적 저장 및 개별 파일 fsync
                    fd, temp_name = tempfile.mkstemp(
                        prefix=f".tmp-{doc_id}-",
                        suffix=".zst",
                        dir=artifacts_dir,
                    )
                    temp_path = Path(temp_name)
                    try:
                        with os.fdopen(fd, "wb") as stream:
                            stream.write(compressed_bytes)
                            stream.flush()
                            os.fsync(stream.fileno())
                        os.replace(temp_path, dest_zst)
                        # D. 무결성 확인 후 원본 .gz 안전하게 삭제
                        gz_path.unlink(missing_ok=True)
                        actual_zst_size = dest_zst.stat().st_size
                    except Exception:
                        temp_path.unlink(missing_ok=True)
                        raise
                    bytes_after += actual_zst_size
                    migrated_since_last_sync += 1
                    if batch_fsync_interval > 0 and migrated_since_last_sync >= batch_fsync_interval:
                        _sync_dir(artifacts_dir)
                        migrated_since_last_sync = 0
                else:
                    bytes_after += len(compressed_bytes)

                bytes_before += gz_size
                migrated += 1

            except Exception as e:
                errors.append({"doc_id": doc_id, "error": str(e)})
                if stop_on_error:
                    break
    finally:
        if apply and migrated_since_last_sync > 0:
            _sync_dir(artifacts_dir)

    savings_pct = (
        round((bytes_before - bytes_after) / bytes_before * 100, 2)
        if bytes_before > 0
        else 0.0
    )

    return {
        "total": total,
        "migrated": migrated,
        "already_zst": already_zst,
        "errors": len(errors),
        "error_details": errors,
        "bytes_before": bytes_before,
        "bytes_after": bytes_after,
        "savings_pct": savings_pct,
        "dry_run": not apply,
    }


def save_raw_file(data_dir: Path, inbox_id: int, src_path: Path, name: str) -> str:
    """텔레그램 등에서 받은 원본 파일(pdf 등)을 그대로 보관. 보관 경로 반환."""
    safe = "".join(c for c in name if c.isalnum() or c in "._-") or "file"
    dest = _files_dir(data_dir) / f"{inbox_id}_{safe}"
    shutil.copyfile(src_path, dest)
    return str(dest)


def _safe_path_component(value: str, fallback: str) -> str:
    safe = "".join(c for c in value if c.isalnum() or c in "._-")
    return safe or fallback


def _attachment_path(data_dir: Path, doc_id: str, attachment: SourceAttachment) -> Path:
    safe_doc_id = _safe_path_component(doc_id, "document")
    safe_kind = "presentation" if attachment.kind == "presentation_pdf" else "attachment"
    digest = attachment.content_sha256.lower()
    if len(digest) != 64 or any(c not in "0123456789abcdef" for c in digest):
        raise ValueError("attachment content_sha256 must be a lowercase SHA-256 digest")
    suffix = ".pdf" if attachment.kind == "presentation_pdf" else ".bin"
    return data_dir / "raw" / "attachments" / safe_doc_id / safe_kind / f"{digest}{suffix}"


def save_attachment(data_dir: Path, doc_id: str, attachment: SourceAttachment) -> str:
    """검증된 첨부를 임시 파일+fsync+원자적 rename으로 저장하고 상대 경로를 반환한다."""
    actual_digest = hashlib.sha256(attachment.content).hexdigest()
    if actual_digest != attachment.content_sha256:
        raise ValueError("attachment content hash mismatch")
    if len(attachment.content) != attachment.byte_length:
        raise ValueError("attachment byte length mismatch")

    destination = _attachment_path(data_dir, doc_id, attachment)
    destination.parent.mkdir(parents=True, exist_ok=True)
    if destination.exists():
        existing_digest = hashlib.sha256(destination.read_bytes()).hexdigest()
        if existing_digest != actual_digest:
            raise ValueError("stored attachment hash mismatch")
        return str(destination.relative_to(data_dir))

    fd, temp_name = tempfile.mkstemp(
        prefix=".incoming-",
        suffix=destination.suffix,
        dir=destination.parent,
    )
    temp_path = Path(temp_name)
    try:
        with os.fdopen(fd, "wb") as stream:
            stream.write(attachment.content)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temp_path, destination)
        _sync_dir(destination.parent)
    except Exception:
        temp_path.unlink(missing_ok=True)
        raise
    return str(destination.relative_to(data_dir))


def save_document_attachments(data_dir: Path, doc: Document) -> list[str]:
    """문서의 필수 첨부를 모두 저장하고 presentation 메타데이터에 경로를 반영한다."""
    if not doc.attachments:
        return []
    saved: list[str] = []
    created: list[Path] = []
    try:
        for attachment in doc.attachments:
            destination = _attachment_path(data_dir, doc.id, attachment)
            existed = destination.exists()
            rel_path = save_attachment(data_dir, doc.id, attachment)
            saved.append(rel_path)
            if not existed:
                created.append(destination)
    except Exception:
        for path in created:
            path.unlink(missing_ok=True)
        raise

    by_hash = {
        attachment.content_sha256: saved[index]
        for index, attachment in enumerate(doc.attachments)
    }
    items = list((doc.meta or {}).get("presentation_pdfs") or [])
    for item in items:
        digest = item.get("content_sha256")
        if digest in by_hash:
            item["artifact_path"] = by_hash[digest]
    if items:
        doc.meta["presentation_pdfs"] = items
        doc.meta["presentation_pdf"] = items[0]
    return saved


def _images_dir(data_dir: Path) -> Path:
    d = data_dir / "images"
    d.mkdir(parents=True, exist_ok=True)
    return d


_EXT_BY_CTYPE = {
    "image/jpeg": ".jpg", "image/jpg": ".jpg", "image/png": ".png",
    "image/webp": ".webp", "image/gif": ".gif",
}
_MAX_IMAGE_BYTES = 8 * 1024 * 1024  # 8MB — 비정상적으로 큰 파일(오탐/공격성 URL) 방어
def download_images(data_dir: Path, doc_id: str, images: list[dict]) -> list[dict]:
    """본문 이미지 후보를 로컬로 내려받아 보존(사용자 요구 — 원본 사이트/링크가 나중에
    사라지면 외부링크뿐인 이미지는 다 깨진다).

    images 는 fetcher 가 수집한 [{url, alt, caption}, ...]. 성공한 항목엔 "local"
    (data_dir 기준 상대경로, `images/<doc_id>_<i>.<ext>`)을 추가해 반환한다. 개별 이미지
    다운로드 실패(네트워크·403·404·비이미지 응답·용량초과)는 그 이미지만 원본 url 유지 —
    한 장이 실패해도 나머지·적재 자체를 막지 않는다."""
    import httpx

    out = []
    for i, im in enumerate(images):
        im = dict(im)
        url = im.get("url") or ""
        if url:
            try:
                with httpx.Client(follow_redirects=True, timeout=10,
                                  headers={"User-Agent": BROWSER_USER_AGENT}) as client:
                    resp = client.get(url)
                ctype = resp.headers.get("content-type", "").split(";")[0].strip().lower()
                if (resp.status_code < 400 and ctype in _EXT_BY_CTYPE
                        and len(resp.content) <= _MAX_IMAGE_BYTES):
                    ext = _EXT_BY_CTYPE[ctype]
                    path = _images_dir(data_dir) / f"{doc_id}_{i}{ext}"
                    path.write_bytes(resp.content)
                    im["local"] = f"images/{doc_id}_{i}{ext}"
            except Exception:  # noqa: BLE001
                pass  # 실패 시 원본 url 만 남음(렌더링측이 폴백)
        out.append(im)
    return out


def raw_disk_usage(data_dir: Path) -> dict[str, int]:
    """raw 보관 용량(bytes) — 모니터링용."""
    out = {"artifacts": 0, "files": 0, "attachments": 0, "images": 0}
    a = data_dir / "raw" / "artifacts"
    f = data_dir / "raw" / "files"
    att = data_dir / "raw" / "attachments"
    im = data_dir / "images"
    if a.exists():
        out["artifacts"] = sum(p.stat().st_size for p in a.glob("*") if p.is_file())
    if f.exists():
        out["files"] = sum(p.stat().st_size for p in f.glob("*") if p.is_file())
    if att.exists():
        out["attachments"] = sum(
            p.stat().st_size for p in att.rglob("*") if p.is_file()
        )
    if im.exists():
        out["images"] = sum(p.stat().st_size for p in im.glob("*") if p.is_file())
    return out
