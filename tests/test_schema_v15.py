"""Schema v15: 계정, 테마·자격증명 귀속 (설계 §8.2–§8.4, §13.1, §18.6)."""

from __future__ import annotations

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


def _make_v14_database(path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """현재 코드에서 v15 블록만 빼고 초기화한 v14 DB."""
    with monkeypatch.context() as m:
        m.setattr(dbm, "SCHEMA_VERSION", 14)
        m.setattr(dbm, "_migrate_v15_accounts", lambda conn: None)
        m.setattr(dbm, "_migrate_v16_themes", lambda conn: None)
        conn = dbm.connect(path)
        try:
            dbm.init_db(conn)
            assert dbm.stored_schema_version(conn) == 14
            assert "account_id" not in dbm._column_names(conn, "themes")
            now = time.time()
            conn.execute(
                "INSERT INTO themes (id, seq, label, is_default, created_at, updated_at) VALUES (1, 1, 'one', 0, ?, ?)",
                (now, now),
            )
            conn.execute(
                "INSERT INTO auth_sessions (nonce, session_token, approved, created_at, expires_at, scope) "
                "VALUES ('n-legacy', 'legacy-session-token-aaaaaaaaaaaaaaaaaaaaaa', 1, ?, ?, 'owner')",
                (now, now + 3600),
            )
            conn.execute(
                "INSERT INTO oauth_codes (code, client_id, redirect_uri, code_challenge, expires_at) "
                "VALUES ('c1', 'cli', 'https://x/cb', 'ch', ?)",
                (now + 300,),
            )
            conn.execute(
                "INSERT INTO oauth_tokens (token, refresh_token, client_id, created_at, expires_at) "
                "VALUES ('legacy-oauth-access-token-xx', 'r1', 'cli', ?, ?)",
                (now, now + 3600),
            )
            conn.execute(
                "INSERT INTO oauth_auth_requests (nonce, client_id, redirect_uri, code_challenge, created_at, expires_at) "
                "VALUES ('a1', 'cli', 'https://x/cb', 'ch', ?, ?)",
                (now, now + 600),
            )
            conn.commit()
        finally:
            conn.close()


def _dump(conn: sqlite3.Connection) -> list[str]:
    return list(conn.iterdump())


def test_fresh_database_declares_v15_objects() -> None:
    conn = _memory_database()
    assert dbm.require_current_schema(conn) == dbm.SCHEMA_VERSION
    names = {r["name"] for r in conn.execute("SELECT name FROM sqlite_master")}
    assert {
        "accounts",
        "username_tombstones",
        "instance_settings",
        "idx_accounts_single_admin",
        "accounts_username_immutable",
        "idx_themes_account_default",
        "users",  # v14 테이블은 남긴다
    } <= names
    assert "account_id" in dbm._column_names(conn, "themes")
    assert {"account_id", "authenticated_at"} <= dbm._column_names(conn, "auth_sessions")
    for tbl in ("oauth_codes", "oauth_tokens", "oauth_auth_requests"):
        assert "account_id" in dbm._column_names(conn, tbl)


def test_admin_row_and_salt() -> None:
    conn = _memory_database()
    rows = conn.execute("SELECT * FROM accounts").fetchall()
    assert len(rows) == 1
    admin = rows[0]
    assert admin["id"] == dbm.ADMIN_ACCOUNT_ID == 1
    assert admin["role"] == "admin"
    assert admin["status"] == "setup_required"
    assert admin["username"] is None
    salt_hex = conn.execute("SELECT value FROM instance_settings WHERE key='id_salt'").fetchone()["value"]
    salt = bytes.fromhex(salt_hex)
    assert len(salt) == 32
    assert admin["public_id"] == ids.legacy_timestamp_id(
        "accounts", salt=salt, unix_ms=dbm.ADMIN_LEGACY_PUBLIC_ID_UNIX_MS, row_id=1
    )
    assert admin["public_id"] > ids.MIN_CURRENT_NUMBER
    # 기본 테마 0은 관리자 것이다.
    assert conn.execute("SELECT account_id FROM themes WHERE id=0").fetchone()["account_id"] == 1


def test_migration_is_idempotent(tmp_path: Path) -> None:
    path = tmp_path / "claire.db"
    conn = dbm.connect(path)
    try:
        dbm.init_db(conn)
        first = _dump(conn)
        dbm.init_db(conn)
        dbm.init_db(conn)
        assert _dump(conn) == first
    finally:
        conn.close()


def test_existing_salt_is_kept() -> None:
    conn = _memory_database()
    before = conn.execute("SELECT value FROM instance_settings WHERE key='id_salt'").fetchone()["value"]
    dbm.init_db(conn)
    after = conn.execute("SELECT value FROM instance_settings WHERE key='id_salt'").fetchone()["value"]
    assert before == after


def test_v14_database_upgrades_and_binds_rows_to_admin(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    path = tmp_path / "claire.db"
    _make_v14_database(path, monkeypatch)

    conn = dbm.connect(path)
    try:
        dbm.init_db(conn)
        assert dbm.require_current_schema(conn) == dbm.SCHEMA_VERSION
        themes = conn.execute("SELECT id, account_id FROM themes ORDER BY id").fetchall()
        assert [(r["id"], r["account_id"]) for r in themes] == [(0, 1), (1, 1)]
        session = conn.execute("SELECT account_id, authenticated_at FROM auth_sessions").fetchone()
        assert session["account_id"] == 1
        # 이관은 사람 인증 시각을 만들어 내지 않는다.
        assert session["authenticated_at"] is None
        for tbl in ("oauth_codes", "oauth_tokens", "oauth_auth_requests"):
            assert {r["account_id"] for r in conn.execute(f"SELECT account_id FROM {tbl}")} == {1}
        # v14 users 테이블은 그대로 남는다.
        assert conn.execute("SELECT 1 FROM sqlite_master WHERE name='users'").fetchone() is not None
        # 기존 세션은 그대로 유효하다(E1).
        assert dbm.validate_session(conn, "legacy-session-token-aaaaaaaaaaaaaaaaaaaaaa")
        first = _dump(conn)
        dbm.init_db(conn)
        assert _dump(conn) == first
    finally:
        conn.close()


def test_multiple_default_themes_fail_migration_without_changes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = tmp_path / "claire.db"
    _make_v14_database(path, monkeypatch)
    conn = dbm.connect(path)
    try:
        conn.execute("UPDATE themes SET is_default=1 WHERE id=1")
        conn.commit()
        with pytest.raises(RuntimeError, match=r"more than one default theme.*themes\.id=\[0,1\]"):
            dbm.init_db(conn)
        conn.rollback()
        assert dbm.stored_schema_version(conn) == 14
        assert conn.execute("SELECT 1 FROM sqlite_master WHERE name='accounts'").fetchone() is None
    finally:
        conn.close()


def test_username_is_immutable_once_set() -> None:
    conn = _memory_database()
    conn.execute("UPDATE accounts SET username='alice' WHERE id=1")
    conn.execute("UPDATE accounts SET username='alice' WHERE id=1")  # 같은 값은 허용
    with pytest.raises(sqlite3.IntegrityError, match="username is immutable"):
        conn.execute("UPDATE accounts SET username='bob' WHERE id=1")
    with pytest.raises(sqlite3.IntegrityError, match="username is immutable"):
        conn.execute("UPDATE accounts SET username=NULL WHERE id=1")


def test_only_one_admin() -> None:
    conn = _memory_database()
    now = time.time()
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute(
            "INSERT INTO accounts (public_id, role, status, created_at, updated_at) VALUES (?, 'admin', 'active', ?, ?)",
            (1 << 57, now, now),
        )
    conn.execute(
        "INSERT INTO accounts (public_id, role, status, created_at, updated_at) VALUES (?, 'member', 'active', ?, ?)",
        (1 << 57, now, now),
    )
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute(
            "INSERT INTO accounts (public_id, role, status, created_at, updated_at) VALUES (?, 'owner', 'active', ?, ?)",
            ((1 << 57) + 1, now, now),
        )


def test_one_default_theme_per_account() -> None:
    conn = _memory_database()
    now = time.time()
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute(
            "INSERT INTO themes (id, seq, label, is_default, created_at, updated_at, account_id) "
            "VALUES (5, 5, 'x', 1, ?, ?, 1)",
            (now, now),
        )


def test_admin_row_with_wrong_role_fails_migration() -> None:
    conn = _memory_database()
    conn.execute("UPDATE accounts SET role='member' WHERE id=1")
    conn.commit()
    with pytest.raises(RuntimeError, match="must be the admin account"):
        dbm.init_db(conn)


def test_new_credentials_record_admin_account() -> None:
    conn = _memory_database()
    before = time.time()
    tok = dbm.create_session(conn, scope="owner")
    row = conn.execute("SELECT account_id, authenticated_at FROM auth_sessions WHERE session_token=?", (tok,)).fetchone()
    assert row["account_id"] == 1
    assert row["authenticated_at"] >= before

    conn.execute("UPDATE auth_sessions SET authenticated_at=NULL")
    exchanged = dbm.exchange_session_token(conn, tok)
    assert exchanged is not None
    row = conn.execute(
        "SELECT account_id, authenticated_at FROM auth_sessions WHERE session_token=?", (exchanged[1],)
    ).fetchone()
    assert row["account_id"] == 1
    assert row["authenticated_at"] is not None

    nonce = dbm.create_auth_nonce(conn)
    assert conn.execute("SELECT account_id FROM auth_sessions WHERE nonce=?", (nonce,)).fetchone()[0] == 1
    approved = dbm.approve_auth_nonce(conn, nonce)
    assert approved
    assert conn.execute("SELECT authenticated_at FROM auth_sessions WHERE nonce=?", (nonce,)).fetchone()[0]

    req = dbm.create_oauth_auth_request(conn, "cli", "https://x/cb", "ch")
    code = dbm.approve_oauth_auth_request(conn, req)
    assert code
    assert conn.execute("SELECT account_id FROM oauth_auth_requests WHERE nonce=?", (req,)).fetchone()[0] == 1
    consumed = dbm.consume_oauth_code(conn, code, "cli", "https://x/cb")
    assert consumed is not None and consumed["account_id"] == 1

    access, refresh = dbm.create_oauth_token(conn, "cli")
    assert conn.execute("SELECT account_id FROM oauth_tokens WHERE token=?", (access,)).fetchone()[0] == 1
    rotated = dbm.refresh_oauth_token(conn, refresh)
    assert rotated is not None
    assert conn.execute("SELECT account_id FROM oauth_tokens WHERE token=?", (rotated[0],)).fetchone()[0] == 1


def test_create_session_only_replaces_same_account_same_scope() -> None:
    conn = _memory_database()
    now = time.time()
    conn.execute(
        "INSERT INTO accounts (id, public_id, role, status, created_at, updated_at) VALUES (2, ?, 'member', 'active', ?, ?)",
        (1 << 57, now, now),
    )
    other = "member-session-token-bbbbbbbbbbbbbbbbbbbbbb"
    conn.execute(
        "INSERT INTO auth_sessions (nonce, session_token, approved, created_at, expires_at, scope, account_id) "
        "VALUES (?, ?, 1, ?, ?, 'owner', 2)",
        (other, other, now, now + 3600),
    )
    conn.commit()
    first = dbm.create_session(conn)
    second = dbm.create_session(conn)
    assert not dbm.validate_session(conn, first)
    assert dbm.validate_session(conn, second)
    assert dbm.validate_session(conn, other)


def test_theme_registry_account_id(tmp_path: Path) -> None:
    import json

    from claire.config import Settings
    from claire.store.theme import ThemeInfo, ThemeManager

    # §13.1: themes.json 항목에 account_id가 없으면 v15 이전 관리자 테마(1)다.
    assert ThemeInfo.from_dict({"id": 3, "label": "x"}).account_id == 1
    assert ThemeInfo.from_dict({"id": 3, "label": "x", "account_id": 2}).account_id == 2
    with pytest.raises(ValueError):
        ThemeInfo.from_dict({"id": 3, "label": "x", "account_id": None})

    data_dir = tmp_path / "data"
    data_dir.mkdir()
    # v15 이전 형식의 레지스트리: account_id가 없다.
    (data_dir / "themes.json").write_text(
        json.dumps(
            {
                "version": 1,
                "next_seq": 2,
                "default_theme_id": 0,
                "themes": [
                    {"id": 0, "seq": 0, "label": "기본", "is_default": True},
                    {"id": 1, "seq": 1, "label": "legacy"},
                ],
            }
        ),
        encoding="utf-8",
    )
    settings = Settings(
        CLAIRE_DB_PATH=str(data_dir / "claire.db"),
        CLAIRE_VAULT_PATH=str(tmp_path / "vault"),
        CLAIRE_PROVIDER="mock",
        CLAIRE_MULTI_THEME=True,
    )
    manager = ThemeManager(base_settings=settings)
    assert {t.id: t.account_id for t in manager.list_themes(include_private=True)} == {0: 1, 1: 1}
    manager.define_theme("new one")
    written = json.loads((data_dir / "themes.json").read_text(encoding="utf-8"))
    assert all(entry["account_id"] == 1 for entry in written["themes"])
    conn = dbm.connect(data_dir / "claire.db")
    try:
        assert {r["account_id"] for r in conn.execute("SELECT account_id FROM themes")} == {1}
    finally:
        conn.close()
