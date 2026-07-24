"""health_report — liveness(ok) + degraded(주의 신호) 판정."""

from __future__ import annotations

from claire.config import Settings
from claire.health import health_report
from claire.store import db as dbm


def _settings(monkeypatch, tmp_path):
    monkeypatch.setenv("CLAIRE_DB_PATH", str(tmp_path / "h.db"))
    monkeypatch.setenv("CLAIRE_VAULT_PATH", str(tmp_path / "vault"))
    monkeypatch.setenv("CLAIRE_PROVIDER", "mock")
    return Settings()


def test_healthy_when_no_errors(monkeypatch, tmp_path):
    s = _settings(monkeypatch, tmp_path)
    conn = dbm.connect(s.db_file); dbm.init_db(conn); conn.close()
    rep = health_report(s, "mock")
    assert rep["ok"] is True
    assert rep["db"] == "ok"
    assert rep["schema_version"] == dbm.SCHEMA_VERSION
    assert rep["expected_schema_version"] == dbm.SCHEMA_VERSION
    assert rep["degraded"] is False
    assert "attention" not in rep
    assert rep["graph"] == {"documents": 0, "entities": 0, "relations": 0}
    assert "last_backup" not in rep
    assert "backup_count" not in rep


def test_degraded_when_error_or_failed(monkeypatch, tmp_path):
    s = _settings(monkeypatch, tmp_path)
    conn = dbm.connect(s.db_file); dbm.init_db(conn)
    conn.execute("INSERT INTO raw_inbox(status,payload) VALUES ('error','a')")
    conn.execute("INSERT INTO raw_inbox(status,payload) VALUES ('failed','b')")
    conn.execute("INSERT INTO raw_inbox(status,payload) VALUES ('done','c')")
    conn.commit(); conn.close()
    rep = health_report(s, "mock")
    assert rep["ok"] is True          # 살아있음
    assert rep["degraded"] is True    # 주의 필요
    assert rep["attention"] == {"error": 1, "failed": 1}
    # error 행은 recover 대상(due)으로 집계, failed 는 영구라 제외
    assert rep["recover_due"] == 1


def test_not_ok_when_db_unreadable(monkeypatch, tmp_path):
    s = _settings(monkeypatch, tmp_path)
    # db_path 를 디렉터리로 만들어 연결/쿼리가 실패하게 한다.
    (tmp_path / "h.db").mkdir()
    rep = health_report(s, "mock")
    assert rep["ok"] is False
    assert "error" in rep["db"]


def test_health_does_not_create_a_missing_database(monkeypatch, tmp_path):
    s = _settings(monkeypatch, tmp_path)
    assert not s.db_file.exists()

    rep = health_report(s, "mock")

    assert rep["ok"] is False
    assert not s.db_file.exists()


def test_health_rejects_stale_schema_without_migrating(monkeypatch, tmp_path):
    s = _settings(monkeypatch, tmp_path)
    conn = dbm.connect(s.db_file)
    dbm.init_db(conn)
    stale = dbm.SCHEMA_VERSION - 1
    conn.execute(
        "UPDATE meta SET value=? WHERE key='schema_version'", (str(stale),)
    )
    conn.commit()
    conn.close()

    rep = health_report(s, "mock")

    assert rep["ok"] is False
    assert "schema_version mismatch" in rep["db"]
    conn = dbm.connect(s.db_file)
    try:
        row = conn.execute(
            "SELECT value FROM meta WHERE key='schema_version'"
        ).fetchone()
        assert int(row["value"]) == stale
    finally:
        conn.close()
