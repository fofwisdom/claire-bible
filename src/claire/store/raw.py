"""[재적재 Layer 2] fetched artifact 원본 보관.

fetcher 가 가져온 원본(HTML/transcript/PDF 추출텍스트)을 gzip 으로 파일 저장한다.
나중에 추출 알고리즘(prompt/모델)을 바꿔도 *재fetch 없이* raw_text 부터 재생할 수 있게
한다. 용량 주의(사용자 요구) → gzip 압축, 텍스트 위주. 임의 prune 은 하지 않는다
(데이터 삭제 금지 원칙). 용량은 doctor/stats 로 모니터만 한다.

레이아웃:
  data/raw/artifacts/<doc_id>.txt.gz   # fetcher 가 추출한 원본 텍스트(=documents.raw_text 미니멀 사본)
  data/raw/files/<inbox_id>_<name>     # 텔레그램으로 받은 원본 파일(pdf 등) 그대로
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


def raw_disk_usage(data_dir: Path) -> dict[str, int]:
    """raw 보관 용량(bytes) — 모니터링용."""
    out = {"artifacts": 0, "files": 0}
    a = data_dir / "raw" / "artifacts"
    f = data_dir / "raw" / "files"
    if a.exists():
        out["artifacts"] = sum(p.stat().st_size for p in a.glob("*") if p.is_file())
    if f.exists():
        out["files"] = sum(p.stat().st_size for p in f.glob("*") if p.is_file())
    return out
