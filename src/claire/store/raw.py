"""[재적재 Layer 2] fetched artifact 원본 보관.

fetcher 가 가져온 원본(HTML/transcript/PDF 추출텍스트)을 gzip 으로 파일 저장한다.
나중에 추출 알고리즘(prompt/모델)을 바꿔도 *재fetch 없이* raw_text 부터 재생할 수 있게
한다. 용량 주의(사용자 요구) → gzip 압축, 텍스트 위주. 임의 prune 은 하지 않는다
(데이터 삭제 금지 원칙). 용량은 doctor/stats 로 모니터만 한다.

레이아웃:
  data/raw/artifacts/<doc_id>.txt.gz   # fetcher 가 추출한 원본 텍스트(=documents.raw_text 미니멀 사본)
  data/raw/files/<inbox_id>_<name>     # 텔레그램으로 받은 원본 파일(pdf 등) 그대로
  data/images/<doc_id>_<i>.<ext>       # 본문 이미지 후보 로컬 보존(원본 사이트/링크 삭제 대비)
"""

from __future__ import annotations

import gzip
import shutil
from pathlib import Path


def _artifacts_dir(data_dir: Path) -> Path:
    d = data_dir / "raw" / "artifacts"
    d.mkdir(parents=True, exist_ok=True)
    return d


def _files_dir(data_dir: Path) -> Path:
    d = data_dir / "raw" / "files"
    d.mkdir(parents=True, exist_ok=True)
    return d


def save_artifact(data_dir: Path, doc_id: str, text: str) -> str:
    """추출 원본 텍스트를 gzip 으로 저장. 저장 경로(상대 문자열) 반환."""
    path = _artifacts_dir(data_dir) / f"{doc_id}.txt.gz"
    with gzip.open(path, "wt", encoding="utf-8") as f:
        f.write(text or "")
    return str(path)


def load_artifact(data_dir: Path, doc_id: str) -> str | None:
    path = _artifacts_dir(data_dir) / f"{doc_id}.txt.gz"
    if not path.exists():
        return None
    with gzip.open(path, "rt", encoding="utf-8") as f:
        return f.read()


def save_raw_file(data_dir: Path, inbox_id: int, src_path: Path, name: str) -> str:
    """텔레그램 등에서 받은 원본 파일(pdf 등)을 그대로 보관. 보관 경로 반환."""
    safe = "".join(c for c in name if c.isalnum() or c in "._-") or "file"
    dest = _files_dir(data_dir) / f"{inbox_id}_{safe}"
    shutil.copyfile(src_path, dest)
    return str(dest)


def _images_dir(data_dir: Path) -> Path:
    d = data_dir / "images"
    d.mkdir(parents=True, exist_ok=True)
    return d


_EXT_BY_CTYPE = {
    "image/jpeg": ".jpg", "image/jpg": ".jpg", "image/png": ".png",
    "image/webp": ".webp", "image/gif": ".gif", "image/svg+xml": ".svg",
}
_MAX_IMAGE_BYTES = 8 * 1024 * 1024  # 8MB — 비정상적으로 큰 파일(오탐/공격성 URL) 방어
# 이미지 CDN(위키미디어 등)이 기본 httpx UA 를 403 으로 막는 경우가 있어(실측: upload.
# wikimedia.org) web.py 의 정적 fetcher 와 같은 브라우저 UA 를 준다.
_UA = (
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0 Safari/537.36"
)


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
                                  headers={"User-Agent": _UA}) as client:
                    resp = client.get(url)
                ctype = resp.headers.get("content-type", "").split(";")[0].strip().lower()
                if (resp.status_code < 400 and ctype.startswith("image/")
                        and len(resp.content) <= _MAX_IMAGE_BYTES):
                    ext = _EXT_BY_CTYPE.get(ctype) or (Path(url.split("?", 1)[0]).suffix or ".jpg")
                    path = _images_dir(data_dir) / f"{doc_id}_{i}{ext}"
                    path.write_bytes(resp.content)
                    im["local"] = f"images/{doc_id}_{i}{ext}"
            except Exception:  # noqa: BLE001
                pass  # 실패 시 원본 url 만 남음(렌더링측이 폴백)
        out.append(im)
    return out


def raw_disk_usage(data_dir: Path) -> dict[str, int]:
    """raw 보관 용량(bytes) — 모니터링용."""
    out = {"artifacts": 0, "files": 0, "images": 0}
    a = data_dir / "raw" / "artifacts"
    f = data_dir / "raw" / "files"
    im = data_dir / "images"
    if a.exists():
        out["artifacts"] = sum(p.stat().st_size for p in a.glob("*") if p.is_file())
    if f.exists():
        out["files"] = sum(p.stat().st_size for p in f.glob("*") if p.is_file())
    if im.exists():
        out["images"] = sum(p.stat().st_size for p in im.glob("*") if p.is_file())
    return out
