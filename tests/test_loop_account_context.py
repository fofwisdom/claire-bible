"""루프 및 서비스 풀의 계정 컨텍스트, 공용 파일 필터 검증.

1. Interface & File I/O: active_jobs 저장 시 account_id 메타데이터 보존 및 계정별 필터링.
2. Interface: IngestService & IngestServicePool의 account_id 인식 및 theme.is_default 일원화.
3. Interface & File I/O: _theme_services_for_cycle에서 suspended, deleting, purged 계정 테마 필터링.
4. E2E: 백그라운드 recover-loop cycle에서 활성 계정 테마만 처리되고 정지 계정 테마는 건너뛰어짐 검증.
"""

from __future__ import annotations

import json
import sqlite3
import time
from pathlib import Path
from types import SimpleNamespace

import pytest

from claire import cli
from claire.config import Settings
from claire.extract.provider import MockProvider
from claire.ingest.active import (
    active_job,
    get_active_jobs,
    get_active_jobs_for_account,
    get_primary_active_job,
)
from claire.ingest.service import IngestService, IngestServicePool
from claire.store import db as dbm
from claire.store.theme import ThemeInfo, ThemeManager, get_theme_manager


def _init_test_db(db_path: Path) -> sqlite3.Connection:
    conn = dbm.connect(db_path)
    dbm.init_db(conn)
    return conn


def _insert_account(
    conn: sqlite3.Connection,
    account_id: int,
    username: str,
    status: str = "active",
    role: str = "member",
) -> None:
    now = time.time()
    conn.execute(
        "INSERT OR REPLACE INTO accounts (id, public_id, username, role, status, display_name, profile_public, created_at, updated_at) "
        "VALUES (?, ?, ?, ?, ?, ?, 0, ?, ?)",
        (account_id, (1 << 57) + account_id, username, role, status, username, now, now),
    )
    conn.commit()


def _save_themes_registry(data_dir: Path, db_path: Path, themes: list[ThemeInfo]) -> None:
    registry_path = data_dir / "themes.json"
    data = {
        "version": 1,
        "next_seq": max((t.seq for t in themes), default=0) + 1,
        "default_theme_id": 0,
        "themes": [t.to_dict() for t in themes],
    }
    registry_path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")

    conn = dbm.connect(db_path)
    for t in themes:
        conn.execute(
            "INSERT OR REPLACE INTO themes (id, seq, label, description, icon, db_path, is_default, is_public, is_collaborator_accessible, default_focus, created_at, updated_at, account_id, number) "
            "VALUES (?, ?, ?, '', '📁', ?, ?, 1, 1, ?, ?, ?, ?, ?)",
            (
                t.id,
                t.seq,
                t.label,
                t.db_path,
                1 if t.is_default else 0,
                t.default_focus,
                t.created_at,
                t.updated_at,
                t.account_id,
                t.number,
            ),
        )
    conn.commit()
    conn.close()


def test_active_jobs_account_metadata_and_filtering(tmp_path: Path):
    """File I/O 및 Interface 테스트: active_jobs 저장 시 account_id를 보존하고 계정별로 필터링한다."""
    import threading

    data_dir = tmp_path / "data"
    data_dir.mkdir(parents=True)

    assert get_active_jobs_for_account(1, data_dir) == []
    assert get_active_jobs_for_account(2, data_dir) == []

    barrier_start = threading.Barrier(3)
    barrier_done = threading.Barrier(3)
    job_ids: dict[int, str] = {}

    def run_worker1():
        with active_job(
            data_dir,
            kind="ingest",
            payload="https://example.com/acc1_job",
            theme_id=1,
            theme_label="Account 1 Theme",
            account_id=1,
        ) as handle1:
            job_ids[1] = handle1.job_id
            barrier_start.wait()
            barrier_done.wait()

    def run_worker2():
        with active_job(
            data_dir,
            kind="reingest",
            payload="https://example.com/acc2_job",
            theme_id=2,
            theme_label="Account 2 Theme",
            account_id=2,
        ) as handle2:
            job_ids[2] = handle2.job_id
            barrier_start.wait()
            barrier_done.wait()

    t1 = threading.Thread(target=run_worker1)
    t2 = threading.Thread(target=run_worker2)
    t1.start()
    t2.start()

    # 두 스레드가 모두 작업 컨텍스트에 진입할 때까지 대기
    barrier_start.wait()

    try:
        # File I/O 검증: 디스크의 작업 JSON 파일에 account_id 메타데이터가 올바르게 기록되었는지 확인
        job_file1 = data_dir / "active_jobs" / f"{job_ids[1]}.json"
        assert job_file1.exists()
        saved_data1 = json.loads(job_file1.read_text(encoding="utf-8"))
        assert saved_data1.get("account_id") == 1
        assert saved_data1.get("theme_id") == 1

        job_file2 = data_dir / "active_jobs" / f"{job_ids[2]}.json"
        assert job_file2.exists()
        saved_data2 = json.loads(job_file2.read_text(encoding="utf-8"))
        assert saved_data2.get("account_id") == 2
        assert saved_data2.get("theme_id") == 2

        # Interface 검증: get_active_jobs_for_account 계정별 분리 확인
        jobs_acc1 = get_active_jobs_for_account(1, data_dir)
        assert len(jobs_acc1) == 1
        assert jobs_acc1[0]["job_id"] == job_ids[1]
        assert jobs_acc1[0]["account_id"] == 1

        jobs_acc2 = get_active_jobs_for_account(2, data_dir)
        assert len(jobs_acc2) == 1
        assert jobs_acc2[0]["job_id"] == job_ids[2]
        assert jobs_acc2[0]["account_id"] == 2

        # 전체 활성 작업에는 2개 모두 존재
        all_jobs = get_active_jobs(data_dir)
        assert len(all_jobs) == 2

        # 존재하지 않는 계정 조회 시 빈 목록 반환
        assert get_active_jobs_for_account(999, data_dir) == []

        # get_primary_active_job 계정 필터링 검증
        primary1 = get_primary_active_job(data_dir, account_id=1)
        assert primary1 is not None and primary1["job_id"] == job_ids[1]

        primary2 = get_primary_active_job(data_dir, account_id=2)
        assert primary2 is not None and primary2["job_id"] == job_ids[2]

        primary_none = get_primary_active_job(data_dir, account_id=999)
        assert primary_none is None
    finally:
        barrier_done.wait()
        t1.join()
        t2.join()


def test_ingest_service_and_pool_account_context_and_default_theme(tmp_path: Path):
    """Interface 테스트: IngestService 및 IngestServicePool의 account_id 인식과 theme.is_default 일원화 검증."""
    data_dir = tmp_path / "data"
    data_dir.mkdir(parents=True)
    db_file = data_dir / "claire.db"

    conn = _init_test_db(db_file)
    _insert_account(conn, 1, "adminuser", status="active", role="admin")
    _insert_account(conn, 2, "memberuser", status="active", role="member")
    conn.close()

    now = time.time()
    themes = [
        ThemeInfo(id=0, seq=0, label="기본 지식베이스", db_path=str(db_file), is_default=True, account_id=1, number=1 << 56, created_at=now, updated_at=now),
        ThemeInfo(id=1, seq=1, label="멤버 기본 테마", db_path=str(db_file), is_default=True, account_id=2, default_focus="Member Focus", number=(1 << 56) + 1, created_at=now, updated_at=now),
        ThemeInfo(id=2, seq=2, label="멤버 서브 테마", db_path=str(db_file), is_default=False, account_id=2, default_focus="Sub Focus", number=(1 << 56) + 2, created_at=now, updated_at=now),
    ]
    _save_themes_registry(data_dir, db_file, themes)

    settings = Settings(
        CLAIRE_DATA_DIR=str(data_dir),
        CLAIRE_DB_PATH=str(db_file),
        CLAIRE_PROVIDER="mock",
        CLAIRE_MULTI_THEME=True,
    )
    tm = ThemeManager(settings)

    # 1. IngestService의 account_id 인식 검증
    svc = IngestService(settings, theme_id=1, account_id=2)
    assert svc.account_id == 2
    assert svc.theme_id == 1

    # 2. theme.is_default 일원화: 기본 테마(is_default=True)는 theme_id > 0이어도 default_focus가 비워짐(None) 검증
    assert svc.get_effective_default_focus() is None

    # 비기본 테마(is_default=False)는 default_focus가 정상 유지됨
    svc_sub = IngestService(settings, theme_id=2, account_id=2)
    assert svc_sub.get_effective_default_focus() == "Sub Focus"

    # 3. IngestServicePool의 계정별 기본 테마 조회 검증
    pool = IngestServicePool(settings, theme_manager=tm, account_id=2)

    # theme_ref=None 일 때 pool의 기본 account_id(2)의 기본 테마(id=1) 서비스가 조회되어야 함
    default_svc_member = pool.get_service(None)
    assert default_svc_member.theme_id == 1
    assert default_svc_member.account_id == 2

    # 명시적으로 account_id=1을 전달하면 관리자의 기본 테마(id=0) 서비스가 조회되어야 함
    default_svc_admin = pool.get_service(None, account_id=1)
    assert default_svc_admin.theme_id == 0
    assert default_svc_admin.account_id == 1


def test_theme_services_for_cycle_skips_suspended_and_deleting_accounts(tmp_path: Path):
    """Interface & File I/O 테스트: _theme_services_for_cycle에서 suspended, deleting, purged 계정 테마를 건너뛴다."""
    data_dir = tmp_path / "data"
    data_dir.mkdir(parents=True)
    db_file = data_dir / "claire.db"

    conn = _init_test_db(db_file)
    _insert_account(conn, 1, "adminuser", status="active", role="admin")
    _insert_account(conn, 2, "user_suspended", status="suspended", role="member")
    _insert_account(conn, 3, "user_deleting", status="deleting", role="member")
    _insert_account(conn, 4, "user_purged", status="purged", role="member")
    _insert_account(conn, 5, "user_active", status="active", role="member")
    conn.close()

    now = time.time()
    themes = [
        ThemeInfo(id=0, seq=0, label="관리자 기본", db_path=str(db_file), is_default=True, account_id=1, number=1 << 56, created_at=now, updated_at=now),
        ThemeInfo(id=10, seq=1, label="관리자 테마10", db_path=str(db_file), is_default=False, account_id=1, number=(1 << 56) + 10, created_at=now, updated_at=now),
        ThemeInfo(id=20, seq=2, label="정지 계정 테마", db_path=str(db_file), is_default=False, account_id=2, number=(1 << 56) + 20, created_at=now, updated_at=now),
        ThemeInfo(id=30, seq=3, label="삭제중 계정 테마", db_path=str(db_file), is_default=False, account_id=3, number=(1 << 56) + 30, created_at=now, updated_at=now),
        ThemeInfo(id=40, seq=4, label="소각 계정 테마", db_path=str(db_file), is_default=False, account_id=4, number=(1 << 56) + 40, created_at=now, updated_at=now),
        ThemeInfo(id=50, seq=5, label="활성 멤버 테마", db_path=str(db_file), is_default=False, account_id=5, number=(1 << 56) + 50, created_at=now, updated_at=now),
    ]
    _save_themes_registry(data_dir, db_file, themes)

    settings = Settings(
        CLAIRE_DATA_DIR=str(data_dir),
        CLAIRE_DB_PATH=str(db_file),
        CLAIRE_PROVIDER="mock",
        CLAIRE_MULTI_THEME=True,
    )

    state = cli._ThemeLoopState()
    factory = lambda ts, **kwargs: SimpleNamespace(settings=ts, kwargs=kwargs)

    # 1. 1차 cycle 실행: active 상태인 계정 1과 5의 테마만 포함되어야 함
    services = cli._theme_services_for_cycle(settings, state, factory)
    included_ids = [theme.id for theme, _ in services]
    assert included_ids == [0, 10, 50]
    assert 20 not in included_ids  # suspended 계정
    assert 30 not in included_ids  # deleting 계정
    assert 40 not in included_ids  # purged 계정

    # 2. 동적 계정 상태 전이 검증: 활성 멤버 계정(id=5)이 suspended로 전이된 경우
    conn = dbm.connect(db_file)
    conn.execute("UPDATE accounts SET status='suspended' WHERE id=5")
    conn.commit()
    conn.close()

    # 다음 cycle 실행 시 테마 50이 서비스 풀에서 제거되고 결과에서 제외되어야 함
    next_services = cli._theme_services_for_cycle(settings, state, factory)
    next_included_ids = [theme.id for theme, _ in next_services]
    assert set(next_included_ids) == {0, 10}
    assert 50 not in next_included_ids
    assert 50 not in state.services


def test_e2e_recover_cycle_skips_suspended_account_themes(tmp_path: Path, monkeypatch):
    """End-to-End (E2E) 테스트: recover-loop 단일 사이클에서 활성 계정 테마만 복구 처리되고 정지 계정 테마는 건너뜀."""
    monkeypatch.delenv("CLAIRE_DB_PATH", raising=False)
    monkeypatch.delenv("CLAIRE_DATA_DIR", raising=False)

    data_dir = tmp_path / "data"
    data_dir.mkdir(parents=True)
    db_file = data_dir / "claire.db"

    conn = _init_test_db(db_file)
    _insert_account(conn, 1, "active_owner", status="active", role="admin")
    _insert_account(conn, 2, "suspended_owner", status="suspended", role="member")

    now = time.time()
    themes = [
        ThemeInfo(id=0, seq=0, label="관리자 기본", db_path=str(db_file), is_default=True, account_id=1, number=1 << 56, created_at=now, updated_at=now),
        ThemeInfo(id=1, seq=1, label="활성 테마", db_path=str(db_file), is_default=False, account_id=1, number=(1 << 56) + 1, created_at=now, updated_at=now),
        ThemeInfo(id=2, seq=2, label="정지 테마", db_path=str(db_file), is_default=False, account_id=2, number=(1 << 56) + 2, created_at=now, updated_at=now),
    ]
    _save_themes_registry(data_dir, db_file, themes)

    # 테마 1과 테마 2 각각에 재적재 도래 error inbox 항목 등록 (네트워크 호출 없는 text kind)
    inbox1_id = dbm.log_inbox(
        conn,
        source="cli",
        payload="활성 계정 복구 테스트 본문 내용입니다.",
        kind="text",
        theme_id=1,
    )
    dbm.update_inbox(conn, inbox1_id, status="error", error="temporary fail", theme_id=1)
    conn.execute("UPDATE raw_inbox SET next_retry_at = ? WHERE id = ?", (now - 100, inbox1_id))

    inbox2_id = dbm.log_inbox(
        conn,
        source="cli",
        payload="정지 계정 복구 테스트 본문 내용입니다.",
        kind="text",
        theme_id=2,
    )
    dbm.update_inbox(conn, inbox2_id, status="error", error="temporary fail", theme_id=2)
    conn.execute("UPDATE raw_inbox SET next_retry_at = ? WHERE id = ?", (now - 100, inbox2_id))
    conn.commit()
    conn.close()

    settings = Settings(
        CLAIRE_DATA_DIR=str(data_dir),
        CLAIRE_DB_PATH=str(db_file),
        CLAIRE_PROVIDER="mock",
        CLAIRE_MULTI_THEME=True,
    )

    args = SimpleNamespace(batch=5, max_attempts=5, base_delay=1.0)
    state = cli._ThemeLoopState()

    # _run_recover_cycle 실행
    cycle = cli._run_recover_cycle(settings, args, state, IngestService)

    # 테마 1의 inbox 항목만 복구되어 결과에 포함되어야 함
    assert len(cycle["results"]) >= 1
    processed_theme_ids = {r.get("_theme_id") for r in cycle["results"]}
    assert 1 in processed_theme_ids
    assert 2 not in processed_theme_ids

    # DB 상태 확인: 테마 1은 완료(done 또는 duplicate) 상태로 전이, 테마 2는 error 상태 그대로 유지
    verify_conn = dbm.connect_existing(db_file, readonly=True)
    row1 = dbm.get_inbox(verify_conn, inbox1_id, theme_id=1)
    row2 = dbm.get_inbox(verify_conn, inbox2_id, theme_id=2)
    verify_conn.close()

    assert row1["status"] in ("done", "duplicate")
    assert row2["status"] == "error"


def test_refresh_and_expand_cycles_skip_suspended_accounts(tmp_path: Path):
    """Interface 테스트: refresh 및 expand 루프 사이클에서도 suspended 계정 테마는 건너뛴다."""
    data_dir = tmp_path / "data"
    data_dir.mkdir(parents=True)
    db_file = data_dir / "claire.db"

    conn = _init_test_db(db_file)
    _insert_account(conn, 1, "active_owner", status="active", role="admin")
    _insert_account(conn, 2, "suspended_owner", status="suspended", role="member")
    conn.close()

    now = time.time()
    themes = [
        ThemeInfo(id=0, seq=0, label="관리자 기본", db_path=str(db_file), is_default=True, account_id=1, number=1 << 56, created_at=now, updated_at=now),
        ThemeInfo(id=1, seq=1, label="활성 테마", db_path=str(db_file), is_default=False, account_id=1, number=(1 << 56) + 1, created_at=now, updated_at=now),
        ThemeInfo(id=2, seq=2, label="정지 테마", db_path=str(db_file), is_default=False, account_id=2, number=(1 << 56) + 2, created_at=now, updated_at=now),
    ]
    _save_themes_registry(data_dir, db_file, themes)

    settings = Settings(
        CLAIRE_DATA_DIR=str(data_dir),
        CLAIRE_DB_PATH=str(db_file),
        CLAIRE_PROVIDER="mock",
        CLAIRE_MULTI_THEME=True,
    )

    visited_refresh: list[int] = []
    visited_expand: list[int] = []

    class MockWorkerService:
        def __init__(self, theme_settings, theme_id=0, account_id=None):
            self.theme_id = theme_id
            self.account_id = account_id

        def enqueue_due_watch(self, **_kwargs):
            return 0

        def run_refresh_queue(self, **_kwargs):
            visited_refresh.append(self.theme_id)
            return []

        def run_expand_queue(self, **_kwargs):
            visited_expand.append(self.theme_id)
            return []

    args = SimpleNamespace(batch=5)
    state_refresh = cli._ThemeLoopState()
    state_expand = cli._ThemeLoopState()

    cli._run_refresh_cycle(settings, args, state_refresh, MockWorkerService)
    assert 2 not in visited_refresh
    assert set(visited_refresh).issubset({0, 1})

    cli._run_expand_cycle(settings, args, state_expand, MockWorkerService)
    assert 2 not in visited_expand
    assert set(visited_expand).issubset({0, 1})

