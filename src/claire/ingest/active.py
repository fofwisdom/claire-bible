"""글로벌 활성 적재(Active Ingest) 및 멀티 슬롯 작업(Active Jobs) 상태 관리자.

웹, 텔레그램 봇, CLI 등 모든 인제스트 진입점에서 실행되는
적재, 재적재, 재생성 파이프라인의 실시간 진행 상태와 Heatmap Matrix를
공유 볼륨(/app/data 또는 data/)의 active_jobs/<job_id>.json 및
active_ingest.json을 통해 프로세스 간(Cross-process) 안전하게 공유한다.
"""

from __future__ import annotations

import json
import logging
import re
import secrets
import threading
import time
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Iterator

logger = logging.getLogger(__name__)

_STATE_FILENAME = "active_ingest.json"
_JOBS_DIRNAME = "active_jobs"
_MAX_EXPENSIVE_JOBS = 4  # server.py 동시 고가 작업 최대 슬롯 수
_STALE_TIMEOUT_SEC = 600.0  # 10분 이상 갱신 없는 작업은 크래시로 간주
_COMPLETED_RETENTION_SEC = 30.0  # 완료 후 30초간 클라이언트에 완료 상태 유지

_local = threading.local()


class JobHandle:
    """활성 작업에 대한 갱신 핸들."""

    def __init__(
        self,
        data_dir: Path | str | None,
        state: dict[str, Any],
        is_reentrant: bool = False,
    ) -> None:
        self.data_dir = data_dir
        self.state = state
        self.is_reentrant = is_reentrant

    @property
    def job_id(self) -> str:
        return str(self.state.get("job_id") or "")

    def update(
        self,
        *,
        msg: str | None = None,
        stage: str | None = None,
        heatmap_matrix: dict[str, Any] | None = None,
        title: str | None = None,
        doc_id: str | None = None,
        result: dict[str, Any] | None = None,
    ) -> None:
        if msg is not None:
            self.state["msg"] = msg
        if stage is not None:
            self.state["stage"] = stage
        if heatmap_matrix is not None:
            self.state["heatmap_matrix"] = heatmap_matrix
        if title is not None:
            self.state["title"] = title
        if doc_id is not None:
            self.state["doc_id"] = doc_id
        if result is not None:
            self.state["result"] = result
        self.state["updated_at"] = time.time()
        _save_job(self.data_dir, self.state)
        _sync_active_ingest(self.data_dir)

    def to_dict(self) -> dict[str, Any]:
        return dict(self.state)


def _get_state_file(data_dir: Path | str | None) -> Path:
    p = Path(data_dir) if data_dir else Path("data")
    p.mkdir(parents=True, exist_ok=True)
    return p / _STATE_FILENAME


def _get_jobs_dir(data_dir: Path | str | None) -> Path:
    p = Path(data_dir) if data_dir else Path("data")
    jobs_dir = p / _JOBS_DIRNAME
    jobs_dir.mkdir(parents=True, exist_ok=True)
    return jobs_dir


def _job_file_path(data_dir: Path | str | None, job_id: str) -> Path:
    jobs_dir = _get_jobs_dir(data_dir)
    return jobs_dir / f"{job_id}.json"


def _save_job(data_dir: Path | str | None, data: dict[str, Any]) -> None:
    job_id = data.get("job_id")
    if not job_id:
        return
    f = _job_file_path(data_dir, job_id)
    _atomic_write(f, data)


def get_active_jobs(data_dir: Path | str | None = None) -> list[dict[str, Any]]:
    """현재 활성화되어 있거나 최근 완료된 작업 목록 반환 (최대 _MAX_EXPENSIVE_JOBS개)."""
    p = Path(data_dir) if data_dir else Path("data")
    jobs_dir = p / _JOBS_DIRNAME
    jobs: list[dict[str, Any]] = []
    now = time.time()

    if jobs_dir.is_dir():
        for f in jobs_dir.glob("*.json"):
            try:
                text = f.read_text(encoding="utf-8")
                if not text.strip():
                    continue
                job = json.loads(text)
                if not isinstance(job, dict):
                    continue

                if job.get("active"):
                    updated_at = float(job.get("updated_at") or job.get("started_at") or 0)
                    if now - updated_at > _STALE_TIMEOUT_SEC:
                        try:
                            f.unlink(missing_ok=True)
                        except Exception:
                            pass
                        continue
                else:
                    completed_at = float(job.get("completed_at") or 0)
                    if now - completed_at > _COMPLETED_RETENTION_SEC:
                        try:
                            f.unlink(missing_ok=True)
                        except Exception:
                            pass
                        continue
                jobs.append(job)
            except Exception as e:
                logger.debug("Failed to read job file %s: %s", f, e)
                continue

    # active_ingest.json에 존재하는 단일 작업이 active_jobs 디렉터리에 없는 경우 동기화 (레거시 단일 슬롯 쓰기 호환)
    single = get_active_ingest(data_dir)
    if single and isinstance(single, dict):
        single_id = single.get("job_id")
        if not single_id:
            if single.get("active") and not any(j.get("active") for j in jobs):
                jobs.append(single)
            elif not single.get("active") and not jobs:
                jobs.append(single)

    # 활성 작업 우선 정렬, 최신 갱신 순
    jobs.sort(
        key=lambda j: (
            1 if j.get("active") else 0,
            float(j.get("updated_at") or j.get("started_at") or 0),
        ),
        reverse=True,
    )
    return jobs[:_MAX_EXPENSIVE_JOBS]


def get_primary_active_job(
    data_dir: Path | str | None = None,
    theme_id: int | None = None,
) -> dict[str, Any] | None:
    """요청된 테마의 우선 활성 작업 반환 (해당 테마 작업이 없으면 전체 중 최신 작업)."""
    jobs = get_active_jobs(data_dir)
    if not jobs:
        return get_active_ingest(data_dir)

    # 1. 요청 테마의 활성 작업
    if theme_id is not None:
        for j in jobs:
            if j.get("active") and j.get("theme_id") == theme_id:
                return j

    # 2. 전체 활성 작업 중 최신
    for j in jobs:
        if j.get("active"):
            return j

    # 3. 요청 테마의 완료 보존 작업
    if theme_id is not None:
        for j in jobs:
            if j.get("theme_id") == theme_id:
                return j

    # 4. 전체 완료 보존 작업 중 최신
    return jobs[0] if jobs else None


def _sync_active_ingest(data_dir: Path | str | None) -> None:
    """하위 호환성을 위해 primary 작업을 active_ingest.json에 동기화."""
    try:
        primary = get_primary_active_job(data_dir)
        state_file = _get_state_file(data_dir)
        if primary:
            _atomic_write(state_file, primary)
        elif state_file.exists():
            current = get_active_ingest(data_dir)
            if current and current.get("active"):
                _atomic_write(state_file, {"active": False, "completed_at": time.time()})
    except Exception as e:
        logger.debug("Failed to sync active ingest state: %s", e)


def get_active_ingest(data_dir: Path | str | None = None) -> dict[str, Any] | None:
    """현재 활성화되어 있거나 방금 완료된 단일 적재 상태 반환 (하위 호환성)."""
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


@contextmanager
def active_job(
    data_dir: Path | str | None,
    *,
    kind: str,
    payload: str,
    theme_id: int = 0,
    theme_label: str = "",
    doc_id: str | None = None,
    focus: str | None = None,
    title: str | None = None,
    source: str = "web",
) -> Iterator[JobHandle]:
    """멀티 슬롯 활성 작업 컨텍스트 관리자.

    동일 스레드 내 재진입(reentrant) 호출 시 바깥 작업의 job_id 및 테마 정보를 보호하며,
    내부 호출은 상태 파일의 덮어쓰기 없이 진행 상태만 공유 갱신한다.
    """
    current_job: JobHandle | None = getattr(_local, "current_job", None)
    if current_job is not None:
        _local.depth = getattr(_local, "depth", 1) + 1
        inner_handle = JobHandle(data_dir, current_job.state, is_reentrant=True)
        if doc_id and not current_job.state.get("doc_id"):
            current_job.state["doc_id"] = doc_id
        if title and (not current_job.state.get("title") or current_job.state["title"] == current_job.state.get("payload")):
            current_job.state["title"] = title
        try:
            yield inner_handle
        finally:
            _local.depth -= 1
        return

    _local.depth = 1
    job_id = f"j_{secrets.token_hex(4)}"
    clean_payload = re.sub(r"\s+", " ", (payload or "")).strip()
    short_title = title or (clean_payload[:80] + "…" if len(clean_payload) > 80 else clean_payload)
    now = time.time()
    state = {
        "active": True,
        "job_id": job_id,
        "kind": kind,
        "payload": clean_payload,
        "source": source,
        "theme_id": theme_id,
        "theme_label": theme_label,
        "doc_id": doc_id,
        "focus": focus or "",
        "title": short_title,
        "stage": "init",
        "stages": ["fetch", "extract", "resolve", "detail", "done"],
        "msg": "원문 분석 및 엔티티 대조 준비 중…",
        "heatmap_matrix": None,
        "started_at": now,
        "updated_at": now,
        "completed_at": None,
        "result": None,
    }
    handle = JobHandle(data_dir, state, is_reentrant=False)
    _local.current_job = handle
    _save_job(data_dir, state)
    _sync_active_ingest(data_dir)
    try:
        yield handle
        now = time.time()
        handle.state["active"] = False
        if not handle.state.get("completed_at"):
            handle.state["completed_at"] = now
        handle.state["updated_at"] = now
        if handle.state.get("stage") != "error" and handle.state.get("stage") != "done":
            handle.state["stage"] = "done"
            if not handle.state.get("msg"):
                handle.state["msg"] = "작업 완료"
        _save_job(data_dir, handle.state)
        _sync_active_ingest(data_dir)
    except Exception as exc:
        now = time.time()
        handle.state["active"] = False
        handle.state["completed_at"] = now
        handle.state["updated_at"] = now
        handle.state["stage"] = "error"
        handle.state["msg"] = f"작업 오류: {exc}"
        _save_job(data_dir, handle.state)
        _sync_active_ingest(data_dir)
        raise
    finally:
        _local.current_job = None
        _local.depth = 0


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
    """새 적재 작업 시작 시 호출 (하위 호환성 유지)."""
    current_job: JobHandle | None = getattr(_local, "current_job", None)
    if current_job is not None:
        if title:
            current_job.state["title"] = title
        if focus:
            current_job.state["focus"] = focus
        current_job.state["updated_at"] = time.time()
        _save_job(data_dir, current_job.state)
        _sync_active_ingest(data_dir)
        return

    f = _get_state_file(data_dir)
    now = time.time()
    clean_payload = re.sub(r"\s+", " ", (payload or "")).strip()
    short_title = title or (clean_payload[:80] + "…" if len(clean_payload) > 80 else clean_payload)
    job_id = f"j_{secrets.token_hex(4)}"
    state = {
        "active": True,
        "job_id": job_id,
        "kind": "ingest",
        "payload": clean_payload,
        "source": source,
        "theme_id": theme_id,
        "theme_label": theme_label,
        "focus": focus or "",
        "title": short_title,
        "stage": "init",
        "stages": ["fetch", "extract", "resolve", "detail", "done"],
        "msg": "원문 분석 및 엔티티 대조 준비 중…",
        "heatmap_matrix": None,
        "started_at": now,
        "updated_at": now,
        "completed_at": None,
        "result": None,
    }
    _save_job(data_dir, state)
    _atomic_write(f, state)


def update_active_ingest(
    data_dir: Path | str | None,
    *,
    msg: str | None = None,
    stage: str | None = None,
    heatmap_matrix: dict[str, Any] | None = None,
    title: str | None = None,
    doc_id: str | None = None,
    result: dict[str, Any] | None = None,
) -> None:
    """진행 중 메시지, 단계, 또는 매트릭스 갱신 (하위 호환성 유지)."""
    current_job: JobHandle | None = getattr(_local, "current_job", None)
    if current_job is not None:
        current_job.update(
            msg=msg,
            stage=stage,
            heatmap_matrix=heatmap_matrix,
            title=title,
            doc_id=doc_id,
            result=result,
        )
        return

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
    if doc_id is not None:
        current["doc_id"] = doc_id
    if result is not None:
        current["result"] = result
    current["updated_at"] = time.time()
    _atomic_write(f, current)
    if current.get("job_id"):
        _save_job(data_dir, current)


def clear_active_ingest(
    data_dir: Path | str | None,
    *,
    result: dict[str, Any] | None = None,
    error: str | None = None,
) -> None:
    """적재 완료 또는 실패 시 호출 (하위 호환성 유지)."""
    current_job: JobHandle | None = getattr(_local, "current_job", None)
    if current_job is not None:
        if getattr(_local, "depth", 1) > 1:
            if result:
                current_job.state["result"] = result
            return
        now = time.time()
        current_job.state["active"] = False
        current_job.state["completed_at"] = now
        current_job.state["updated_at"] = now
        if error:
            current_job.state["msg"] = f"적재 오류: {error}"
            current_job.state["stage"] = "error"
        else:
            current_job.state["msg"] = "적재 및 대조 완료"
            current_job.state["stage"] = "done"
        if result:
            current_job.state["result"] = result
            if result.get("title"):
                current_job.state["title"] = result["title"]
            if result.get("heatmap_matrix"):
                current_job.state["heatmap_matrix"] = result["heatmap_matrix"]
        _save_job(data_dir, current_job.state)
        _sync_active_ingest(data_dir)
        return

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
    if current.get("job_id"):
        _save_job(data_dir, current)


def purge_active_matrix(data_dir: Path | str | None, doc_id: str | None = None) -> None:
    """활성 또는 최근 적재 상태에서 heatmap_matrix 를 물리적으로 소각."""
    f = _get_state_file(data_dir)
    current = get_active_ingest(data_dir)
    if current:
        matched = True
        if doc_id:
            res_doc_id = (current.get("result") or {}).get("document_id")
            mat_doc_id = (current.get("heatmap_matrix") or {}).get("document_id")
            if res_doc_id and res_doc_id != doc_id and mat_doc_id and mat_doc_id != doc_id:
                matched = False
        if matched:
            if "heatmap_matrix" in current:
                current["heatmap_matrix"] = None
            if current.get("result") and isinstance(current["result"], dict) and "heatmap_matrix" in current["result"]:
                current["result"]["heatmap_matrix"] = None
            current["updated_at"] = time.time()
            _atomic_write(f, current)

    # active_jobs 디렉터리 내 개별 작업 파일들도 함께 소각
    try:
        p = Path(data_dir) if data_dir else Path("data")
        jobs_dir = p / _JOBS_DIRNAME
        if jobs_dir.is_dir():
            for jf in jobs_dir.glob("*.json"):
                try:
                    txt = jf.read_text(encoding="utf-8")
                    job = json.loads(txt)
                    if not isinstance(job, dict):
                        continue
                    m = True
                    if doc_id:
                        res_did = (job.get("result") or {}).get("document_id")
                        mat_did = (job.get("heatmap_matrix") or {}).get("document_id")
                        if res_did and res_did != doc_id and mat_did and mat_did != doc_id:
                            m = False
                    if m:
                        changed = False
                        if "heatmap_matrix" in job:
                            job["heatmap_matrix"] = None
                            changed = True
                        if job.get("result") and isinstance(job["result"], dict) and "heatmap_matrix" in job["result"]:
                            job["result"]["heatmap_matrix"] = None
                            changed = True
                        if changed:
                            job["updated_at"] = time.time()
                            _atomic_write(jf, job)
                except Exception:
                    continue
    except Exception as e:
        logger.debug("Failed to purge active matrix in active_jobs: %s", e)


def _atomic_write(file_path: Path, data: dict[str, Any]) -> None:
    tmp = file_path.with_name(f"{file_path.name}.tmp.{secrets.token_hex(4)}")
    try:
        tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
        tmp.replace(file_path)
    except Exception as e:
        logger.debug("Failed to write active state to %s: %s", file_path, e)
        try:
            if tmp.exists():
                tmp.unlink()
        except Exception:
            pass
