"""자동복구(recover) — 마이그레이션 + error inbox 게이팅 재적재 + 지수백오프 + 영구실패.

네트워크 없이 fetch 를 monkeypatch 한다(test_refresh 와 동일 패턴).
"""

from __future__ import annotations

import sqlite3

from claire.config import Settings
from claire.ontology.base import Document
from claire.store import db as dbm
from claire.ingest import service as svcmod
from claire.ingest import pipeline as pipemod
from claire.ingest.service import IngestService


def _patch_fetch(monkeypatch, fn):
    monkeypatch.setattr(svcmod, "default_fetch", fn)
    monkeypatch.setattr(pipemod, "default_fetch", fn)


def _mem(monkeypatch, tmp_path):
    monkeypatch.setenv("CLAIRE_DB_PATH", str(tmp_path / "r.db"))
    monkeypatch.setenv("CLAIRE_VAULT_PATH", str(tmp_path / "vault"))
    monkeypatch.setenv("CLAIRE_PROVIDER", "mock")
    return Settings()


def _boom(_payload):
    raise RuntimeError("429 rate limit")


# --- 마이그레이션 (기존 운영 DB 무손실 업그레이드) ---

def test_migration_adds_recovery_columns():
    conn = sqlite3.connect(":memory:"); conn.row_factory = sqlite3.Row
    # 재시도 컬럼이 없던 구버전 raw_inbox 를 흉내낸다.
    conn.execute("CREATE TABLE raw_inbox (id INTEGER PRIMARY KEY AUTOINCREMENT, "
                 "status TEXT, payload TEXT, error TEXT)")
    conn.execute("INSERT INTO raw_inbox(status, payload) VALUES ('error', 'u')")
    conn.commit()
    dbm.init_db(conn)  # 마이그레이션 적용
    cols = {r["name"] for r in conn.execute("PRAGMA table_info(raw_inbox)").fetchall()}
    assert {"attempts", "last_attempt", "next_retry_at"} <= cols
    # 기존 행 보존(데이터 무손실)
    assert conn.execute("SELECT COUNT(*) c FROM raw_inbox").fetchone()["c"] == 1


def test_migration_idempotent():
    conn = sqlite3.connect(":memory:"); conn.row_factory = sqlite3.Row
    dbm.init_db(conn)
    dbm.init_db(conn)  # 두 번 호출해도 ALTER 중복 에러 없음
    cols = {r["name"] for r in conn.execute("PRAGMA table_info(raw_inbox)").fetchall()}
    assert "attempts" in cols


# --- due 게이팅 ---

def test_due_gating():
    conn = sqlite3.connect(":memory:"); conn.row_factory = sqlite3.Row
    dbm.init_db(conn)
    now = 1000.0
    conn.execute("INSERT INTO raw_inbox(status,payload,attempts) VALUES ('error','a',0)")
    conn.execute("INSERT INTO raw_inbox(status,payload,attempts,next_retry_at) "
                 "VALUES ('error','b',1,?)", (now + 100,))  # 아직 예약시각 전
    conn.execute("INSERT INTO raw_inbox(status,payload,attempts) VALUES ('error','c',5)")  # 상한
    conn.execute("INSERT INTO raw_inbox(status,payload,attempts) VALUES ('done','d',0)")   # 대상 아님
    conn.commit()
    assert {r["payload"] for r in dbm.due_for_recovery(conn, max_attempts=5, now=now)} == {"a"}
    # 예약시각이 지나면 b 도 도래
    assert {r["payload"] for r in dbm.due_for_recovery(conn, max_attempts=5, now=now + 200)} == {"a", "b"}


# --- service.recover_failed ---

def test_recover_success_is_idempotent(monkeypatch, tmp_path):
    s = _mem(monkeypatch, tmp_path)
    svc = IngestService(s)
    # 1) fetch 실패 → error 기록
    _patch_fetch(monkeypatch, _boom)
    rep = svc.ingest("https://x/1", source="test")
    assert rep.error is not None

    conn = dbm.connect(s.db_file); dbm.init_db(conn)
    assert dbm.inbox_status_counts(conn).get("error") == 1
    n_before = conn.execute("SELECT COUNT(*) c FROM raw_inbox").fetchone()["c"]
    conn.close()

    # 2) fetch 가 이제 성공(쿼터 회복 가정) → recover
    good = Document(url="https://x/1", title="T", raw_text="body " * 50,
                    source_type="web", content_hash="h")
    _patch_fetch(monkeypatch, lambda p: good)
    results = svc.recover_failed(max_attempts=5, base_delay=300.0)
    assert len(results) == 1 and results[0]["status"] == "done"

    conn = dbm.connect(s.db_file); dbm.init_db(conn)
    # 같은 inbox 행 재사용 → 행 수 불변(멱등, 폭증 안 함)
    assert conn.execute("SELECT COUNT(*) c FROM raw_inbox").fetchone()["c"] == n_before
    assert dbm.inbox_status_counts(conn).get("done") == 1
    assert dbm.inbox_status_counts(conn).get("error", 0) == 0
    conn.close()


def test_recover_extract_failure_actually_extracts(monkeypatch, tmp_path):
    """핵심 케이스: 429가 extract 단계에서 터진 행은 문서가 이미 적재돼 있다.

    recover 가 full re-ingest 하면 dedup→duplicate 로 빠져 추출이 영영 안 되는 버그를
    방어. document_id 가 있으면 extract 만 재실행해 엔티티가 *실제로* 생성돼야 한다.
    (status=='done' 만으로는 불충분 — 엔티티/관계 생성을 직접 확인)
    """
    s = _mem(monkeypatch, tmp_path)
    svc = IngestService(s)
    good = Document(url="https://github.com/owner/repo", title="repo",
                    raw_text="A tool about agents and graphs. " * 20,
                    source_type="web", content_hash="h3")
    _patch_fetch(monkeypatch, lambda p: good)

    # provider.extract 가 첫 호출에 429, 그 다음부터 정상.
    real_extract = svc.provider.extract
    calls = {"n": 0}

    def flaky(doc, ctx=None):
        calls["n"] += 1
        if calls["n"] == 1:
            raise RuntimeError("429 quota")
        return real_extract(doc, ctx)

    monkeypatch.setattr(svc.provider, "extract", flaky)

    rep = svc.ingest("https://github.com/owner/repo", source="test")
    assert rep.error is not None  # extract 단계 실패

    conn = dbm.connect(s.db_file); dbm.init_db(conn)
    assert dbm.counts(conn)["documents"] == 1            # 문서는 이미 적재됨
    ent_before = dbm.counts(conn)["entities"]
    row = conn.execute("SELECT * FROM raw_inbox").fetchone()
    assert row["status"] == "error" and row["document_id"] is not None
    conn.close()

    # recover → extract 이제 성공해야 하고, 엔티티가 진짜 생겨야 한다.
    results = svc.recover_failed(max_attempts=5, base_delay=0.0)
    assert len(results) == 1 and results[0]["status"] == "done"

    conn = dbm.connect(s.db_file); dbm.init_db(conn)
    assert dbm.counts(conn)["entities"] > ent_before     # ← 진짜 복구의 증거
    assert dbm.counts(conn)["documents"] == 1            # 문서 안 늘어남
    assert dbm.inbox_status_counts(conn).get("done") == 1
    conn.close()


def test_recover_backoff_then_permanent_failure(monkeypatch, tmp_path):
    s = _mem(monkeypatch, tmp_path)
    svc = IngestService(s)
    _patch_fetch(monkeypatch, _boom)
    rep = svc.ingest("https://x/2", source="test")
    inbox_id = rep.inbox_id

    # base_delay=0 → next_retry_at 이 즉시 도래하므로 게이팅 우회하고 연속 시도 가능.
    statuses = []
    for _ in range(3):
        res = svc.recover_failed(max_attempts=3, base_delay=0.0)
        statuses.append(res[0]["status"] if res else "none")
    assert statuses == ["error", "error", "failed"]

    conn = dbm.connect(s.db_file); dbm.init_db(conn)
    row = conn.execute("SELECT * FROM raw_inbox WHERE id=?", (inbox_id,)).fetchone()
    assert row["status"] == "failed" and row["attempts"] == 3
    # failed 는 더 이상 자동복구 대상 아님(무한재시도 차단)
    assert dbm.due_for_recovery(conn, max_attempts=3) == []
    conn.close()
