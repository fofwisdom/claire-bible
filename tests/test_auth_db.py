"""auth.db: 스키마, scrypt 비밀번호, 로그인 실패 제한, 관리자 감사 기록 (설계 §8.5, §13.3).

scrypt를 실제 매개변수(n=2^15)로 돌리는 테스트는 ``test_real_params_*`` 몇 개로 줄이고, 나머지는 ``fast_scrypt``로 n을 낮춘다.
"""

from __future__ import annotations

import json
import os
import sqlite3
import stat

import pytest

from claire.store import auth_db as adb

T0 = 1_800_000_000.0
TABLES_13_3 = {
    "meta",
    "account_passwords",
    "account_identities",
    "account_provider_keys",
    "invitations",
    "invitation_redemptions",
    "oidc_states",
    "login_throttle",
    "account_usage",
    "admin_audit",
}


@pytest.fixture
def conn(tmp_path):
    c = adb.connect_auth_db(adb.auth_db_path(tmp_path))
    yield c
    c.close()


@pytest.fixture
def fast_scrypt(monkeypatch):
    monkeypatch.setattr(adb, "SCRYPT_N", 1 << 4)


def _schema_snapshot(c: sqlite3.Connection) -> list[tuple]:
    return c.execute(
        "SELECT type, name, sql FROM sqlite_master WHERE name NOT LIKE 'sqlite_%' ORDER BY name"
    ).fetchall()


# --- 스키마 ---------------------------------------------------------------------


def test_path_is_data_auth_auth_db(tmp_path):
    assert adb.auth_db_path(tmp_path) == tmp_path / "auth" / "auth.db"
    assert adb.auth_db_path(str(tmp_path)) == tmp_path / "auth" / "auth.db"


def test_schema_creates_all_tables_and_is_idempotent(conn):
    names = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    assert TABLES_13_3 <= names
    before = [tuple(r) for r in _schema_snapshot(conn)]
    adb.init_auth_db(conn)
    adb.init_auth_db(conn)
    assert [tuple(r) for r in _schema_snapshot(conn)] == before
    rows = conn.execute("SELECT value FROM meta WHERE key='schema_version'").fetchall()
    assert [r[0] for r in rows] == [str(adb.AUTH_DB_SCHEMA_VERSION)] == ["5"]


def test_reconnect_keeps_data(tmp_path, fast_scrypt):
    path = adb.auth_db_path(tmp_path)
    c = adb.connect_auth_db(path)
    adb.set_password(c, 1, "a-long-enough-secret", username="alice")
    c.close()
    c = adb.connect_auth_db(path)
    assert adb.has_password(c, 1)
    c.close()


def test_newer_schema_version_is_refused(tmp_path):
    path = adb.auth_db_path(tmp_path)
    c = adb.connect_auth_db(path)
    c.execute(
        "UPDATE meta SET value=? WHERE key='schema_version'", (str(adb.AUTH_DB_SCHEMA_VERSION + 1),)
    )
    c.commit()
    c.close()
    with pytest.raises(RuntimeError, match="newer"):
        adb.connect_auth_db(path)
    raw = sqlite3.connect(path)
    with pytest.raises(RuntimeError, match="newer"):
        adb.init_auth_db(raw)
    raw.close()


def test_invalid_schema_version_is_refused(tmp_path):
    raw = sqlite3.connect(":memory:")
    raw.execute("CREATE TABLE meta (key TEXT PRIMARY KEY, value TEXT)")
    raw.execute("INSERT INTO meta VALUES ('schema_version', 'abc')")
    with pytest.raises(RuntimeError, match="invalid"):
        adb.init_auth_db(raw)


@pytest.mark.skipif(os.name != "posix", reason="POSIX 권한")
def test_file_permissions(tmp_path):
    old = os.umask(0o022)
    try:
        path = adb.auth_db_path(tmp_path)
        adb.connect_auth_db(path).close()
    finally:
        os.umask(old)
    assert stat.S_IMODE(path.parent.stat().st_mode) == 0o700
    assert stat.S_IMODE(path.stat().st_mode) == 0o600


@pytest.mark.skipif(os.name != "posix", reason="POSIX 권한")
def test_existing_loose_permissions_are_tightened(tmp_path):
    path = adb.auth_db_path(tmp_path)
    path.parent.mkdir()
    os.chmod(path.parent, 0o755)
    path.touch()
    os.chmod(path, 0o644)
    adb.connect_auth_db(path).close()
    assert stat.S_IMODE(path.parent.stat().st_mode) == 0o700
    assert stat.S_IMODE(path.stat().st_mode) == 0o600


# --- 비밀번호 -------------------------------------------------------------------


def test_real_params_roundtrip_and_stored_params(conn):
    adb.set_password(conn, 7, "correct-horse-battery", username="alice", now=T0)
    row = conn.execute(
        "SELECT algo, params, salt, hash, updated_at FROM account_passwords WHERE account_id=7"
    ).fetchone()
    assert row["algo"] == "scrypt"
    assert json.loads(row["params"]) == {"n": 1 << 15, "r": 8, "p": 1, "dklen": 64}
    assert len(row["salt"]) == adb.SALT_BYTES == 16
    assert len(row["hash"]) == 64
    assert row["updated_at"] == T0
    assert adb.verify_password(conn, 7, "correct-horse-battery") is True
    assert adb.verify_password(conn, 7, "correct-horse-batterY") is False


def test_real_params_missing_account_does_dummy_work(conn, monkeypatch):
    calls = []
    real = adb._scrypt

    def spy(password, salt, params):
        calls.append(dict(params))
        return real(password, salt, params)

    monkeypatch.setattr(adb, "_scrypt", spy)
    assert adb.verify_password(conn, 999, "whatever-password") is False
    assert adb.verify_password(conn, None, "whatever-password") is False
    assert calls == [{"n": 1 << 15, "r": 8, "p": 1, "dklen": 64}] * 2


def test_wrong_password_and_fresh_salt(conn, fast_scrypt):
    adb.set_password(conn, 1, "a-long-enough-secret", username="alice")
    adb.set_password(conn, 2, "a-long-enough-secret", username="bob")
    r1, r2 = (
        conn.execute("SELECT salt, hash FROM account_passwords WHERE account_id=?", (i,)).fetchone()
        for i in (1, 2)
    )
    assert r1["salt"] != r2["salt"] and r1["hash"] != r2["hash"]
    assert adb.verify_password(conn, 1, "a-long-enough-secret") is True
    assert adb.verify_password(conn, 1, "a-long-enough-secreT") is False
    assert adb.verify_password(conn, 1, "") is False


def test_set_password_replaces_with_new_salt(conn, fast_scrypt):
    adb.set_password(conn, 1, "first-long-password", username="alice")
    salt1 = conn.execute("SELECT salt FROM account_passwords WHERE account_id=1").fetchone()[0]
    adb.set_password(conn, 1, "second-long-password", username="alice")
    salt2 = conn.execute("SELECT salt FROM account_passwords WHERE account_id=1").fetchone()[0]
    assert salt1 != salt2
    assert conn.execute("SELECT COUNT(*) FROM account_passwords").fetchone()[0] == 1
    assert adb.verify_password(conn, 1, "first-long-password") is False
    assert adb.verify_password(conn, 1, "second-long-password") is True


def test_verify_uses_stored_params_not_current(conn, monkeypatch):
    monkeypatch.setattr(adb, "SCRYPT_N", 1 << 4)
    adb.set_password(conn, 1, "a-long-enough-secret", username="alice")
    monkeypatch.setattr(adb, "SCRYPT_N", 1 << 15)  # 비용을 올린 뒤에도 옛 해시를 검증한다
    seen = []
    real = adb._scrypt
    monkeypatch.setattr(adb, "_scrypt", lambda pw, s, p: (seen.append(p["n"]), real(pw, s, p))[1])
    assert adb.verify_password(conn, 1, "a-long-enough-secret") is True
    assert seen == [1 << 4]


def test_has_password(conn, fast_scrypt):
    assert adb.has_password(conn, 1) is False
    adb.set_password(conn, 1, "a-long-enough-secret", username="alice")
    assert adb.has_password(conn, 1) is True


def test_unknown_algo_fails_loudly(conn, fast_scrypt):
    adb.set_password(conn, 1, "a-long-enough-secret", username="alice")
    conn.execute("UPDATE account_passwords SET algo='md5'")
    with pytest.raises(RuntimeError, match="algo"):
        adb.verify_password(conn, 1, "a-long-enough-secret")


def test_policy_too_short():
    with pytest.raises(adb.PasswordRejected) as e:
        adb.check_password_policy("x" * 11, username="alice")
    assert e.value.reason == "too_short"
    adb.check_password_policy("x" * 12, username="alice")


ASCII_PUNCTUATION = "!\"#$%&'()*+,-./:;<=>?@[\\]^_`{|}~"


def test_policy_rejects_characters_outside_printable_ascii():
    # 비ASCII는 길이와 관계없이 먼저 거부한다(검사 순서: invalid_characters → too_short → contains_username).
    for password in (
        "가" * 12,
        "가",
        "long enough password",     # 공백
        "long\tenough\tpassword",   # 탭
        "Ｐassword-long-1",          # 전각 문자
        "password-long-1\x7f",      # DEL(0x7F)
        "password-long-1\x00",
        "password-long-1\n",
        "alice\u00a0password-1",     # NBSP
    ):
        with pytest.raises(adb.PasswordRejected) as e:
            adb.check_password_policy(password, username="alice")
        assert e.value.reason == "invalid_characters", password


def test_policy_accepts_each_ascii_punctuation():
    assert len(ASCII_PUNCTUATION) == 32
    assert set(ASCII_PUNCTUATION) == {
        chr(c) for c in range(0x21, 0x7F) if not chr(c).isalnum()
    }
    for ch in ASCII_PUNCTUATION:
        adb.check_password_policy(f"Abcdef123456{ch}", username="alice")
    adb.check_password_policy("AZaz09" + ASCII_PUNCTUATION, username="alice")


def test_policy_reason_order():
    with pytest.raises(adb.PasswordRejected) as e:
        adb.check_password_policy("al ce", username="alice")  # 공백 + 짧음
    assert e.value.reason == "invalid_characters"
    with pytest.raises(adb.PasswordRejected) as e:
        adb.check_password_policy("alice1", username="alice")  # 짧음 + 이름 포함
    assert e.value.reason == "too_short"


def test_verify_password_does_not_apply_character_policy(conn, fast_scrypt):
    # 정책 이전에 저장된 해시(공백 포함 비밀번호)도 그대로 검증된다.
    adb.set_password(conn, 1, "legacy-placeholder-1", username="alice")
    salt = b"s" * adb.SALT_BYTES
    params = adb._current_params()
    conn.execute(
        "UPDATE account_passwords SET salt=?, hash=? WHERE account_id=1",
        (salt, adb._scrypt("legacy pass with spaces", salt, params)),
    )
    conn.commit()
    assert adb.verify_password(conn, 1, "legacy pass with spaces") is True


def test_policy_contains_username_case_insensitive():
    with pytest.raises(adb.PasswordRejected) as e:
        adb.check_password_policy("my-ALICE-password", username="alice")
    assert e.value.reason == "contains_username"
    adb.check_password_policy("my-alic-password", username="alice")
    adb.check_password_policy("my-alice-password", username=None)


def test_set_password_enforces_policy(conn, fast_scrypt):
    with pytest.raises(adb.PasswordRejected) as e:
        adb.set_password(conn, 1, "short", username="alice")
    assert e.value.reason == "too_short"
    with pytest.raises(adb.PasswordRejected) as e:
        adb.set_password(conn, 1, "xxAlice-long-enough", username="alice")
    assert e.value.reason == "contains_username"
    assert adb.has_password(conn, 1) is False


# --- 로그인 실패 제한 -----------------------------------------------------------


def _fail(c, n, *, username="alice", ip="203.0.113.5", now=T0):
    d = None
    for _ in range(n):
        d = adb.record_login_failure(c, username=username, ip=ip, now=now)
    return d


def test_free_failures_then_blocked(conn):
    free = adb.THROTTLE_FREE_FAILURES
    for _ in range(free):
        d = adb.record_login_failure(conn, username="alice", ip="203.0.113.5", now=T0)
        assert d == adb.ThrottleDecision(allowed=True, retry_after=None)
    assert adb.check_login_throttle(conn, username="alice", ip="203.0.113.5", now=T0).allowed
    d = adb.record_login_failure(conn, username="alice", ip="203.0.113.5", now=T0)
    assert d.allowed is False and d.retry_after == T0 + 1.0
    assert adb.check_login_throttle(conn, username="alice", ip="203.0.113.5", now=T0) == d
    assert adb.check_login_throttle(conn, username="alice", ip="203.0.113.5", now=T0 + 1.0).allowed


def test_delay_grows_exponentially_and_caps(conn):
    _fail(conn, adb.THROTTLE_FREE_FAILURES)
    delays = []
    now = T0
    for _ in range(14):
        d = adb.record_login_failure(conn, username="alice", ip="203.0.113.5", now=now)
        delays.append(d.retry_after - now)
        now = d.retry_after  # 대기가 끝나자마자 다시 실패한다
    assert delays[:10] == [1.0, 2.0, 4.0, 8.0, 16.0, 32.0, 64.0, 128.0, 256.0, 512.0]
    assert delays[10:] == [float(adb.THROTTLE_MAX_DELAY_SECONDS)] * 4


def test_window_expiry_resets_count(conn):
    _fail(conn, adb.THROTTLE_FREE_FAILURES)
    later = T0 + adb.THROTTLE_WINDOW_SECONDS
    d = adb.record_login_failure(conn, username="alice", ip="203.0.113.5", now=later)
    assert d.allowed is True
    rows = conn.execute("SELECT failures, window_start FROM login_throttle").fetchall()
    assert [tuple(r) for r in rows] == [(1, later), (1, later)]


def test_blocked_key_needs_a_quiet_window_after_its_wait(conn):
    _fail(conn, adb.THROTTLE_FREE_FAILURES + 3)  # 대기 4초
    w = adb.THROTTLE_WINDOW_SECONDS
    # window_start에서 창이 지났어도, 대기가 끝난 뒤 창 길이만큼 조용하지 않았으면 이어서 센다.
    d2 = adb.record_login_failure(conn, username="alice", ip="203.0.113.5", now=T0 + w)
    assert d2.allowed is False and d2.retry_after == T0 + w + 8.0
    quiet = d2.retry_after + w
    d3 = adb.record_login_failure(conn, username="alice", ip="203.0.113.5", now=quiet)
    assert d3.allowed is True
    assert {r[0] for r in conn.execute("SELECT failures FROM login_throttle")} == {1}


def test_ip_key_accumulates_across_usernames(conn):
    free = adb.THROTTLE_FREE_FAILURES
    for i in range(free):
        assert adb.record_login_failure(conn, username=f"user{i}", ip="203.0.113.5", now=T0).allowed
    d = adb.record_login_failure(conn, username="fresh_name", ip="203.0.113.5", now=T0)
    assert d.allowed is False
    assert not adb.check_login_throttle(conn, username="another", ip="203.0.113.5", now=T0).allowed
    assert adb.check_login_throttle(conn, username="another", ip="198.51.100.7", now=T0).allowed


def test_success_clears_user_ip_key_only(conn):
    _fail(conn, adb.THROTTLE_FREE_FAILURES + 1)
    adb.record_login_success(conn, username="alice", ip="203.0.113.5")
    assert conn.execute("SELECT COUNT(*) FROM login_throttle").fetchone()[0] == 1
    # IP 키는 남아 있으므로 같은 IP에서는 여전히 기다려야 한다.
    assert not adb.check_login_throttle(conn, username="alice", ip="203.0.113.5", now=T0).allowed
    d = adb.record_login_failure(conn, username="bob", ip="203.0.113.5", now=T0 + 1.0)
    assert d.allowed is False and d.retry_after == T0 + 1.0 + 2.0


def test_unknown_username_is_counted_the_same(conn):
    # 계정 존재 여부를 이 모듈은 모른다. 어떤 이름이든 같은 규칙이다.
    d1 = _fail(conn, adb.THROTTLE_FREE_FAILURES + 1, username="nobody_here", ip="198.51.100.1")
    d2 = _fail(conn, adb.THROTTLE_FREE_FAILURES + 1, username="alice", ip="198.51.100.2")
    assert d1 == d2


def test_throttle_key_does_not_contain_username(conn):
    _fail(conn, 1, username="secretname", ip="203.0.113.5")
    _fail(conn, 1, username="SecretName", ip="203.0.113.5")
    keys = [r[0] for r in conn.execute("SELECT key FROM login_throttle")]
    assert len(keys) == 2  # 대소문자만 다른 이름은 같은 (username, IP) 키
    for k in keys:
        assert "secretname" not in k.lower()
    dump = "\n".join(conn.iterdump()).lower()
    assert "secretname" not in dump


# --- 관리자 감사 기록 -----------------------------------------------------------


def test_audit_roundtrip_newest_first(conn):
    a = adb.record_admin_audit(conn, actor_account_id=1, action="account.suspend",
                               target_account_id=2, detail={"reason": "spam"}, now=T0)
    b = adb.record_admin_audit(conn, actor_account_id=1, action="mode.switch", now=T0 + 5)
    rows = adb.list_admin_audit(conn)
    assert [r["id"] for r in rows] == [b, a]
    assert rows[1] == {
        "id": a, "at": T0, "actor_account_id": 1, "action": "account.suspend",
        "target_account_id": 2, "detail": {"reason": "spam"},
    }
    assert rows[0]["detail"] is None and rows[0]["target_account_id"] is None
    raw = conn.execute("SELECT detail_json FROM admin_audit WHERE id=?", (a,)).fetchone()[0]
    assert json.loads(raw) == {"reason": "spam"}
    assert [r["id"] for r in adb.list_admin_audit(conn, limit=1)] == [b]
    with pytest.raises(ValueError):
        adb.list_admin_audit(conn, limit=0)


@pytest.mark.parametrize(
    "detail",
    [{"username": "alice"}, {"Username": "alice"}, {"nested": {"username": "alice"}},
     {"items": [{"username": "alice"}]}],
)
def test_audit_rejects_username_in_detail(conn, detail):
    with pytest.raises(ValueError, match="username"):
        adb.record_admin_audit(conn, actor_account_id=1, action="x", detail=detail)
    assert conn.execute("SELECT COUNT(*) FROM admin_audit").fetchone()[0] == 0


# --- 원자적 시도 기록 (begin_login_attempt) -----------------------------------------


def test_begin_attempt_counts_before_verification_and_rejects_without_counting(conn):
    free = adb.THROTTLE_FREE_FAILURES
    for _ in range(free + 1):
        assert adb.begin_login_attempt(conn, username="alice", ip="203.0.113.5", now=T0).allowed
    # 무료 범위를 넘긴 시도까지 미리 셌으므로 같은 순간의 다음 시도는 거부한다.
    d = adb.begin_login_attempt(conn, username="alice", ip="203.0.113.5", now=T0)
    assert d == adb.ThrottleDecision(allowed=False, retry_after=T0 + 1.0)
    # 거부한 시도는 세지 않는다.
    assert {r[0] for r in conn.execute("SELECT failures FROM login_throttle")} == {free + 1}
    assert not conn.in_transaction


def test_begin_attempt_is_atomic_across_connections(tmp_path):
    """동시 요청이 확인과 기록 사이에 끼어들지 못한다: 무료 범위 + 1개만 검증 단계에 닿는다."""
    import threading

    path = adb.auth_db_path(tmp_path)
    adb.connect_auth_db(path).close()
    attempts = 40
    barrier = threading.Barrier(attempts)
    allowed: list[bool] = []
    lock = threading.Lock()

    def worker() -> None:
        c = adb.connect_auth_db(path)
        try:
            barrier.wait()
            d = adb.begin_login_attempt(c, username="alice", ip="203.0.113.5", now=T0)
            with lock:
                allowed.append(d.allowed)
        finally:
            c.close()

    threads = [threading.Thread(target=worker) for _ in range(attempts)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert len(allowed) == attempts
    # 6번째(무료 5회 다음) 시도가 첫 지연을 만든다. 같은 순간의 나머지는 모두 거부된다.
    assert allowed.count(True) == adb.THROTTLE_FREE_FAILURES + 1


def test_settle_success_deletes_user_ip_key_and_refunds_only_this_attempt(conn):
    ip = "203.0.113.5"
    _fail(conn, 2, username="bob", ip=ip)  # 같은 IP의 다른 이름 실패 2회
    assert adb.begin_login_attempt(conn, username="alice", ip=ip, now=T0).allowed
    adb.settle_login_success(conn, username="alice", ip=ip)
    rows = {r[0]: r[1] for r in conn.execute("SELECT key, failures FROM login_throttle")}
    user_ip_alice, ip_key = adb._throttle_keys("alice", ip)
    assert user_ip_alice not in rows
    assert rows[ip_key] == 2  # bob의 실패 2회만 남는다


def test_settle_success_clears_delay_only_when_back_in_free_range(conn):
    ip = "203.0.113.5"
    _, ip_key = adb._throttle_keys("alice", ip)
    _fail(conn, adb.THROTTLE_FREE_FAILURES, username="bob", ip=ip)
    assert adb.begin_login_attempt(conn, username="alice", ip=ip, now=T0).allowed  # 6번째: 1초 지연
    adb.settle_login_success(conn, username="alice", ip=ip)
    row = conn.execute("SELECT failures, retry_after FROM login_throttle WHERE key=?", (ip_key,)).fetchone()
    assert tuple(row) == (adb.THROTTLE_FREE_FAILURES, None)

    _fail(conn, 2, username="bob", ip=ip)  # 7회: 지연 2초
    assert adb.begin_login_attempt(conn, username="alice", ip=ip, now=T0 + 10).allowed
    adb.settle_login_success(conn, username="alice", ip=ip)
    row = conn.execute("SELECT failures, retry_after FROM login_throttle WHERE key=?", (ip_key,)).fetchone()
    assert row[0] == adb.THROTTLE_FREE_FAILURES + 2 and row[1] is not None


def test_begin_attempt_refuses_an_open_transaction(conn):
    conn.execute("BEGIN")
    try:
        with pytest.raises(RuntimeError, match="own transaction"):
            adb.begin_login_attempt(conn, username="alice", ip="203.0.113.5", now=T0)
    finally:
        conn.rollback()


# --- scrypt 동시 계산 상한 -----------------------------------------------------------


def test_scrypt_concurrency_is_bounded_and_dummy_path_shares_it(conn, monkeypatch):
    import threading
    import time as _time

    active = 0
    peak = 0
    lock = threading.Lock()
    real = adb.hashlib.scrypt

    def slow_scrypt(*args, **kwargs):
        nonlocal active, peak
        with lock:
            active += 1
            peak = max(peak, active)
        try:
            _time.sleep(0.02)
            return real(*args, **{**kwargs, "n": 16})
        finally:
            with lock:
                active -= 1

    monkeypatch.setattr(adb.hashlib, "scrypt", slow_scrypt)
    db_path = conn.execute("PRAGMA database_list").fetchone()[2]

    def worker(i: int) -> None:
        c = adb.connect_auth_db(db_path)
        try:
            # 짝수는 없는 계정(더미 계산), 홀수는 행 없는 계정 id — 둘 다 같은 세마포어를 거친다.
            adb.verify_password(c, None if i % 2 == 0 else 99, "whatever-password")
        finally:
            c.close()

    threads = [threading.Thread(target=worker, args=(i,)) for i in range(16)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert 1 <= peak <= adb.SCRYPT_MAX_CONCURRENCY


# --- OIDC state 및 신원 연결 테스트 (P5 L5-b) ----------------------------------------


def test_oidc_state_save_and_consume(conn):
    adb.save_oidc_state(
        conn,
        key="key1",
        provider_id="google",
        state="state123",
        nonce="nonce456",
        code_verifier="verifier789",
        purpose="link",
        account_id=42,
        expires_at=T0 + 600,
    )

    # 1. State mismatch -> raises OidcStateMismatchError and removes state
    with pytest.raises(adb.OidcStateMismatchError):
        adb.consume_oidc_state(conn, key="key1", state="wrong_state", now=T0)

    # State was consumed/deleted on mismatch to prevent replay
    with pytest.raises(adb.OidcStateNotFoundError):
        adb.consume_oidc_state(conn, key="key1", state="state123", now=T0)

    # 2. Expiry check
    adb.save_oidc_state(
        conn,
        key="key2",
        provider_id="google",
        state="state999",
        nonce="nonce999",
        code_verifier="verifier999",
        purpose="login",
        expires_at=T0 + 10,
    )
    with pytest.raises(adb.OidcStateNotFoundError):
        adb.consume_oidc_state(conn, key="key2", state="state999", now=T0 + 20)

    # 3. Successful consume
    adb.save_oidc_state(
        conn,
        key="key3",
        provider_id="github",
        state="state_ok",
        nonce="nonce_ok",
        code_verifier="verifier_ok",
        purpose="signup",
        invitation_id=7,
        expires_at=T0 + 600,
    )
    rec = adb.consume_oidc_state(conn, key="key3", state="state_ok", now=T0)
    assert rec.key == "key3"
    assert rec.provider_id == "github"
    assert rec.purpose == "signup"
    assert rec.invitation_id == 7
    assert rec.account_id is None

    # Replay fails
    with pytest.raises(adb.OidcStateNotFoundError):
        adb.consume_oidc_state(conn, key="key3", state="state_ok", now=T0)


def test_account_identities_linking_and_unlinking(conn):
    # Link first identity
    adb.link_account_identity(conn, iss="https://accounts.google.com", sub="sub-100", account_id=1, provider_id="google", now=T0)

    # Duplicate link fails
    with pytest.raises(adb.IdentityAlreadyLinkedError):
        adb.link_account_identity(conn, iss="https://accounts.google.com", sub="sub-100", account_id=2, provider_id="google", now=T0)

    # Query identity
    found = adb.find_account_identity(conn, iss="https://accounts.google.com", sub="sub-100")
    assert found is not None
    assert found.account_id == 1
    assert found.provider_id == "google"
    assert found.last_login_at is None

    # Record login
    adb.record_identity_login(conn, iss="https://accounts.google.com", sub="sub-100", now=T0 + 100)
    found = adb.find_account_identity(conn, iss="https://accounts.google.com", sub="sub-100")
    assert found.last_login_at == T0 + 100

    # Unlink safety: cannot unlink if only credential (no password and only 1 identity)
    with pytest.raises(adb.IdentityUnlinkForbiddenError):
        adb.unlink_account_identity(conn, account_id=1, provider_id="google", iss="https://accounts.google.com", sub="sub-100")

    # Link a second identity
    adb.link_account_identity(conn, iss="https://github.com", sub="99999", account_id=1, provider_id="github", now=T0)
    identities = adb.list_account_identities(conn, account_id=1)
    assert len(identities) == 2

    # Now unlinking google succeeds because github remains
    ok = adb.unlink_account_identity(conn, account_id=1, provider_id="google", iss="https://accounts.google.com", sub="sub-100")
    assert ok is True
    assert adb.find_account_identity(conn, iss="https://accounts.google.com", sub="sub-100") is None


def test_idp_providers_load_and_save(tmp_path, monkeypatch):
    providers = [
        adb.IdpProviderMeta(
            id="google",
            type="oidc",
            display_name="Google Workspace",
            client_id="google-client-id-123",
            issuer="https://accounts.google.com",
            scopes=("openid", "email", "profile"),
            hosted_domain="example.com",
            tenant_id=None,
        )
    ]
    adb.save_idp_providers(tmp_path, providers)

    # Without secret env var: secret_configured is False
    loaded = adb.load_idp_providers(tmp_path, include_secrets=False)
    assert len(loaded) == 1
    assert loaded[0].id == "google"
    assert loaded[0].client_id == "google-client-id-123"
    assert loaded[0].secret_configured is False
    assert loaded[0].client_secret is None

    # With secret env var: secret_configured is True
    monkeypatch.setenv("CLAIRE_OIDC_GOOGLE_CLIENT_SECRET", "super-secret-key")
    loaded = adb.load_idp_providers(tmp_path, include_secrets=True)
    assert loaded[0].secret_configured is True
    assert loaded[0].client_secret == "super-secret-key"

