"""Schema v16: 테마 번호, 공개 epoch, 임베딩 열, 레거시 호스트, 공개 디렉터리 카운터 (설계 §7.2, §13.2, §18.6).

tmp_path와 메모리 DB만 쓴다. 저장소 data/에는 쓰지 않는다(§18.2-6).
"""

from __future__ import annotations

import shutil
import sqlite3
import time
from pathlib import Path

import pytest

from claire import ids
from claire.store import db as dbm


def _memory_database() -> sqlite3.Connection:
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    dbm.init_db(conn)
    return conn


def _salt(conn: sqlite3.Connection) -> bytes:
    return bytes.fromhex(conn.execute("SELECT value FROM instance_settings WHERE key='id_salt'").fetchone()["value"])


def _expected_number(conn: sqlite3.Connection, theme_id: int, created_at: float, *, probe: int = 0) -> int:
    return ids.legacy_timestamp_id(
        "themes",
        salt=_salt(conn),
        unix_ms=max(int(created_at * 1000), dbm.ADMIN_LEGACY_PUBLIC_ID_UNIX_MS) + probe,
        row_id=theme_id,
    )


# 2030-01-01T00:00:00Z. 하한(ADMIN_LEGACY_PUBLIC_ID_UNIX_MS)보다 뒤라 그대로 쓰인다.
LATER_CREATED_AT = 1_893_456_000.0


def _make_v15_database(path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """현재 코드에서 v16 블록만 빼고 초기화한 v15 DB. 레거시 테마 행을 넣는다."""
    with monkeypatch.context() as m:
        m.setattr(dbm, "SCHEMA_VERSION", 15)
        m.setattr(dbm, "_migrate_v16_themes", lambda conn: None)
        m.setattr(dbm, "_migrate_v17_federation", lambda conn: None)
        conn = dbm.connect(path)
        try:
            dbm.init_db(conn)
            assert dbm.stored_schema_version(conn) == 15
            assert "number" not in dbm._column_names(conn, "themes")
            rows = [
                # created_at=0인 레거시 행. FQDN은 대소문자·공백이 섞여 있다.
                (1, 1, "zero", 0.0, "  Old.Example.COM "),
                (2, 2, "later", LATER_CREATED_AT, "later.example.com"),
                (3, 3, "no-host", LATER_CREATED_AT, ""),
                (4, 4, "zero-too", 0.0, None),
            ]
            for tid, seq, label, created_at, fqdn in rows:
                conn.execute(
                    "INSERT INTO themes (id, seq, label, is_default, created_at, updated_at, fqdn, account_id) "
                    "VALUES (?, ?, ?, 0, ?, ?, ?, 1)",
                    (tid, seq, label, created_at, created_at, fqdn),
                )
            conn.commit()
        finally:
            conn.close()


def _numbers(conn: sqlite3.Connection) -> dict[int, int]:
    return {r["id"]: r["number"] for r in conn.execute("SELECT id, number FROM themes ORDER BY id")}


def test_fresh_database_declares_v16_objects() -> None:
    conn = _memory_database()
    assert dbm.require_current_schema(conn) == dbm.SCHEMA_VERSION
    assert {"number", "visibility_epoch", "embed_provider", "embed_model", "fqdn"} <= dbm._column_names(
        conn, "themes"
    )
    names = {r["name"] for r in conn.execute("SELECT name FROM sqlite_master")}
    assert {
        "legacy_hosts",
        "public_directory_counters",
        "idx_themes_number",
        "idx_themes_account_number",
    } <= names
    assert [r["name"] for r in conn.execute("PRAGMA table_info(legacy_hosts)")] == [
        "host",
        "theme_id",
        "created_at",
        "activated_at",
        "sunset_at",
    ]
    assert [r["name"] for r in conn.execute("PRAGMA table_info(public_directory_counters)")] == [
        "account_id",
        "public_themes",
        "public_documents",
        "updated_at",
    ]
    unique = {r["name"]: r["unique"] for r in conn.execute("PRAGMA index_list(themes)")}
    assert unique["idx_themes_number"] == 1
    assert unique["idx_themes_account_number"] == 0


def test_theme_zero_gets_number_on_fresh_database() -> None:
    conn = _memory_database()
    row = conn.execute("SELECT number, created_at, visibility_epoch, embed_provider, embed_model FROM themes WHERE id=0").fetchone()
    assert row["number"] >= ids.MIN_CURRENT_NUMBER
    assert row["number"] == _expected_number(conn, 0, row["created_at"])
    assert row["visibility_epoch"] == 1
    assert row["embed_provider"] is None and row["embed_model"] is None


def test_v15_upgrade_fills_numbers_with_floor(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    path = tmp_path / "claire.db"
    _make_v15_database(path, monkeypatch)
    conn = dbm.connect(path)
    try:
        dbm.init_db(conn)
        assert dbm.require_current_schema(conn) == dbm.SCHEMA_VERSION
        numbers = _numbers(conn)
        assert all(n is not None and n >= ids.MIN_CURRENT_NUMBER for n in numbers.values())
        # created_at=0이어도 하한(ADMIN_LEGACY_PUBLIC_ID_UNIX_MS) 덕분에 2^56 이상이다(Rev. 2.3).
        assert numbers[1] == _expected_number(conn, 1, 0.0)
        assert numbers[1] >> 16 == dbm.ADMIN_LEGACY_PUBLIC_ID_UNIX_MS
        assert numbers[4] == _expected_number(conn, 4, 0.0)
        assert numbers[2] == _expected_number(conn, 2, LATER_CREATED_AT)
        assert numbers[2] >> 16 == int(LATER_CREATED_AT * 1000)
        assert len(set(numbers.values())) == len(numbers)
        # 기존 행은 기본 epoch 1, 임베딩 미정.
        assert {r["visibility_epoch"] for r in conn.execute("SELECT visibility_epoch FROM themes")} == {1}
        # 기존 데이터는 그대로다.
        assert conn.execute("SELECT label, account_id FROM themes WHERE id=2").fetchone()[:] == ("later", 1)
    finally:
        conn.close()


def test_legacy_number_floor_helper() -> None:
    assert dbm.legacy_theme_number_unix_ms(None) == dbm.ADMIN_LEGACY_PUBLIC_ID_UNIX_MS
    assert dbm.legacy_theme_number_unix_ms(0) == dbm.ADMIN_LEGACY_PUBLIC_ID_UNIX_MS
    assert dbm.legacy_theme_number_unix_ms(1.5) == dbm.ADMIN_LEGACY_PUBLIC_ID_UNIX_MS
    assert dbm.legacy_theme_number_unix_ms(LATER_CREATED_AT) == int(LATER_CREATED_AT * 1000)
    salt = b"\x01" * 32
    lowest = ids.legacy_timestamp_id(
        "themes", salt=salt, unix_ms=dbm.legacy_theme_number_unix_ms(0), row_id=0
    )
    assert lowest >= ids.MIN_CURRENT_NUMBER
    assert lowest >= ids.INTERNAL_ID_LIMIT


def test_v15_upgrade_copies_fqdn_to_legacy_hosts(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    path = tmp_path / "claire.db"
    _make_v15_database(path, monkeypatch)
    conn = dbm.connect(path)
    try:
        before = time.time()
        dbm.init_db(conn)
        hosts = [dict(r) for r in conn.execute("SELECT * FROM legacy_hosts ORDER BY host")]
        assert [(h["host"], h["theme_id"]) for h in hosts] == [
            ("later.example.com", 2),
            ("old.example.com", 1),
        ]
        for h in hosts:
            assert h["created_at"] >= before
            # 일몰 시계는 네트워크 전환·관리자 활성화 때 시작한다(§5.3).
            assert h["activated_at"] is None
            assert h["sunset_at"] is None
        # fqdn 열은 지우지 않는다(A3).
        assert conn.execute("SELECT fqdn FROM themes WHERE id=1").fetchone()[0] == "  Old.Example.COM "
    finally:
        conn.close()


def test_v16_migration_is_idempotent(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    path = tmp_path / "claire.db"
    _make_v15_database(path, monkeypatch)
    conn = dbm.connect(path)
    try:
        dbm.init_db(conn)
        first = list(conn.iterdump())
        dbm.init_db(conn)
        dbm.init_db(conn)
        assert list(conn.iterdump()) == first
    finally:
        conn.close()


def test_v16_numbers_are_deterministic(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    path = tmp_path / "claire.db"
    _make_v15_database(path, monkeypatch)
    copy = tmp_path / "copy.db"
    shutil.copyfile(path, copy)

    conn = dbm.connect(path)
    other = dbm.connect(copy)
    try:
        dbm.init_db(conn)
        dbm.init_db(other)
        first = _numbers(conn)
        # 같은 v15 DB를 따로 이관해도 같은 번호다.
        assert _numbers(other) == first
        # 번호를 지우고 다시 채워도 같은 번호다.
        conn.execute("UPDATE themes SET number=NULL")
        assert dbm.backfill_theme_numbers(conn) == len(first)
        assert _numbers(conn) == first
    finally:
        conn.close()
        other.close()


def test_legacy_hosts_row_removed_later_is_not_resurrected(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = tmp_path / "claire.db"
    _make_v15_database(path, monkeypatch)
    conn = dbm.connect(path)
    try:
        dbm.init_db(conn)
        conn.execute("DELETE FROM legacy_hosts WHERE host='old.example.com'")
        conn.commit()
        dbm.init_db(conn)
        assert [r["host"] for r in conn.execute("SELECT host FROM legacy_hosts")] == ["later.example.com"]
    finally:
        conn.close()


def test_backfill_probes_next_millisecond_when_number_is_taken() -> None:
    conn = _memory_database()
    now = LATER_CREATED_AT
    conn.execute(
        "INSERT INTO themes (id, seq, label, created_at, updated_at, account_id) VALUES (7, 7, 'a', ?, ?, 1)",
        (now, now),
    )
    occupied = _expected_number(conn, 8, now)
    # 다른 행이 8번 테마의 결정론적 번호를 이미 쓰고 있다.
    conn.execute("UPDATE themes SET number=? WHERE id=7", (occupied,))
    conn.execute(
        "INSERT INTO themes (id, seq, label, created_at, updated_at, account_id) VALUES (8, 8, 'b', ?, ?, 1)",
        (now, now),
    )
    assert dbm.backfill_theme_numbers(conn) == 1
    assert _numbers(conn)[8] == _expected_number(conn, 8, now, probe=1)


def test_number_is_unique() -> None:
    conn = _memory_database()
    number = conn.execute("SELECT number FROM themes WHERE id=0").fetchone()[0]
    with pytest.raises(sqlite3.IntegrityError, match="themes.number"):
        conn.execute(
            "INSERT INTO themes (id, seq, label, created_at, updated_at, account_id, number) "
            "VALUES (9, 9, 'dup', 0, 0, 1, ?)",
            (number,),
        )


def test_backfill_requires_salt() -> None:
    conn = _memory_database()
    conn.execute(
        "INSERT INTO themes (id, seq, label, created_at, updated_at, account_id) VALUES (5, 5, 'x', 0, 0, 1)"
    )
    conn.execute("DELETE FROM instance_settings WHERE key='id_salt'")
    with pytest.raises(RuntimeError, match="id_salt is missing"):
        dbm.backfill_theme_numbers(conn)


def test_v15_database_with_newer_code_is_rejected_by_require_current_schema(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = tmp_path / "claire.db"
    _make_v15_database(path, monkeypatch)
    conn = dbm.connect_existing(path, readonly=True)
    try:
        with pytest.raises(RuntimeError, match="actual=15, expected=17"):
            dbm.require_current_schema(conn)
    finally:
        conn.close()
