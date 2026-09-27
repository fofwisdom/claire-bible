"""글로벌 활성 적재(Active Ingest) 상태 관리자.

웹, 텔레그램 봇, CLI 등 모든 인제스트 진입점에서 실행되는
적재 파이프라인의 실시간 진행 상태와 Heatmap Matrix를
공유 볼륨(/app/data 또는 data/)의 active_ingest.json을 통해
프로세스 간(Cross-process) 안전하게 공유한다.
"""

from __future__ import annotations

import json
import logging
import os
import re
import time
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

_STATE_FILENAME = "active_ingest.json"
_STALE_TIMEOUT_SEC = 600.0  # 10분 이상 갱신 없는 작업은 크래시로 간주
_COMPLETED_RETENTION_SEC = 30.0  # 완료 후 30초간 클라이언트에 완료 상태 유지


def _get_state_file(data_dir: Path | str | None) -> Path:
    p = Path(data_dir) if data_dir else Path("data")
    p.mkdir(parents=True, exist_ok=True)
    return p / _STATE_FILENAME


def get_active_ingest(data_dir: Path | str | None = None) -> dict[str, Any] | None:
    """현재 활성화되어 있거나 방금 완료된 적재 상태 반환."""
    try:
        f = _get_state_file(data_dir)
        if not f.exists():
            return None
        text = f.read_text(encoding="utf-8")
        if not text.strip():
            return None
        data = json.loads(text)
        now = time.time()

        if data.get("active"):
            updated_at = float(data.get("updated_at") or data.get("started_at") or 0)
            if now - updated_at > _STALE_TIMEOUT_SEC:
                return None
            return data

        # 완료된 상태인 경우 30초 이내면 전달
        completed_at = float(data.get("completed_at") or 0)
        if now - completed_at < _COMPLETED_RETENTION_SEC:
            return data
        return None
    except Exception as e:
        logger.debug("Failed to read active ingest state: %s", e)
        return None


def set_active_ingest(
    data_dir: Path | str | None,
    *,
    payload: str,
    source: str = "web",
    theme_id: int = 0,
    theme_label: str = "",
    focus: str | None = None,
    title: str | None = None,
) -> None:
    """새 적재 작업 시작 시 호출."""
    f = _get_state_file(data_dir)
    now = time.time()
    clean_payload = re.sub(r"\s+", " ", (payload or "")).strip()
    short_title = title or (clean_payload[:80] + "…" if len(clean_payload) > 80 else clean_payload)
    state = {
        "active": True,
        "payload": clean_payload,
        "source": source,
        "theme_id": theme_id,
        "theme_label": theme_label,
        "focus": focus or "",
        "title": short_title,
        "stage": "init",
        "msg": "원문 분석 및 엔티티 대조 준비 중…",
        "heatmap_matrix": None,
        "started_at": now,
        "updated_at": now,
        "completed_at": None,
        "result": None,
    }
    _atomic_write(f, state)


def update_active_ingest(
    data_dir: Path | str | None,
    *,
    msg: str | None = None,
    stage: str | None = None,
    heatmap_matrix: dict[str, Any] | None = None,
    title: str | None = None,
) -> None:
    """진행 중 메시지, 단계, 또는 매트릭스 갱신."""
    f = _get_state_file(data_dir)
    current = get_active_ingest(data_dir) or {}
    if not current.get("active"):
        return
    if msg is not None:
        current["msg"] = msg
    if stage is not None:
        current["stage"] = stage
    if heatmap_matrix is not None:
        current["heatmap_matrix"] = heatmap_matrix
    if title is not None:
        current["title"] = title
    current["updated_at"] = time.time()
    _atomic_write(f, current)


def clear_active_ingest(
    data_dir: Path | str | None,
    *,
    result: dict[str, Any] | None = None,
    error: str | None = None,
) -> None:
    """적재 완료 또는 실패 시 호출."""
    f = _get_state_file(data_dir)
    current = get_active_ingest(data_dir) or {}
    now = time.time()
    current["active"] = False
    current["completed_at"] = now
    current["updated_at"] = now
    if error:
        current["msg"] = f"적재 오류: {error}"
        current["stage"] = "error"
    else:
        current["msg"] = "적재 및 대조 완료"
        current["stage"] = "done"
    if result:
        current["result"] = result
        if result.get("title"):
            current["title"] = result["title"]
        if result.get("heatmap_matrix"):
            current["heatmap_matrix"] = result["heatmap_matrix"]
    _atomic_write(f, current)


def _atomic_write(file_path: Path, data: dict[str, Any]) -> None:
    tmp = file_path.with_suffix(".tmp")
    try:
        tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
        tmp.replace(file_path)
    except Exception as e:
        logger.debug("Failed to write active ingest state to %s: %s", file_path, e)
        try:
            if tmp.exists():
                tmp.unlink()
        except Exception:
            pass
