"""``data/auth/auth.db``: 자격 비밀과 제어 평면 (설계 §13.3).

지식 DB(``claire.db``)와 분리한다. graph bundle·support bundle·내보내기에 비밀이 섞이지 않게 하고, 지식 DB 장애가 로그인으로 번지지 않게 하기 위해서다. 자체 ``meta.schema_version``을 1부터 둔다.

P1 범위: 스키마 전체 생성, 로컬 비밀번호(scrypt, §8.5), 로그인 실패 제한, 관리자 감사 기록. 초대·IdP·키 테이블은 만들기만 하고 다루지 않는다(P3–P5).
"""

from __future__ import annotations

import hashlib
import hmac
import json
import os
import secrets
import sqlite3
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal

AUTH_DB_SCHEMA_VERSION = 1
AUTH_DB_RELATIVE_PATH = Path("auth") / "auth.db"

# §8.5 scrypt 매개변수. n=2^15, r=8이면 128·n·r = 32MiB이므로 hashlib 기본 maxmem(32MiB)으로는
# 실패한다. ``SCRYPT_MAXMEM``을 함께 넘긴다.
SCRYPT_N = 1 << 15
SCRYPT_R = 8
SCRYPT_P = 1
SCRYPT_DKLEN = 64
SCRYPT_MAXMEM = 64 * 1024 * 1024
SALT_BYTES = 16

# 프로세스 안에서 동시에 도는 scrypt 계산 수의 상한. 계산 하나가 128·n·r = 32MiB를 쓰고, 라우트는
# ``asyncio.to_thread``(기본 스레드 풀, 최대 32개)로 부르므로 제한이 없으면 로그인 폭주가 1GiB 가까이 잡는다.
# 4면 최고 128MiB이고, 한 계산이 수십 ms라 정상 로그인은 거의 기다리지 않는다. 더미 계산도 같은 세마포어를 거친다
# (거치지 않으면 없는 계정의 응답 시간이 대기 시간만큼 달라진다).
SCRYPT_MAX_CONCURRENCY = 4
_SCRYPT_SLOTS = threading.BoundedSemaphore(SCRYPT_MAX_CONCURRENCY)

MIN_PASSWORD_LENGTH = 12

# 실패 제한(§8.5): (username, IP)와 (IP) 두 키로 지수 백오프. 고정 잠금은 없다.
THROTTLE_WINDOW_SECONDS = 15 * 60
THROTTLE_FREE_FAILURES = 5          # 창 안에서 이만큼은 지연 없이 허용
THROTTLE_MAX_DELAY_SECONDS = 15 * 60
# 무료 실패를 넘긴 첫 실패의 지연. 이후 실패마다 두 배(1, 2, 4, ... 초, 상한 THROTTLE_MAX_DELAY_SECONDS).
_THROTTLE_BASE_DELAY_SECONDS = 1.0

PasswordRejectReason = Literal["invalid_characters", "too_short", "contains_username"]

# 비밀번호에 쓸 수 있는 문자(소유자 결정 2026-11-02): 영문 대소문자, 숫자, 표준 특수문자(ASCII 0x21–0x7E).
# 공백·비ASCII·제어문자는 거부한다. 새로 설정할 때만 적용하고, 저장된 해시 검증(verify_password)에는 적용하지 않는다.
PASSWORD_ALLOWED_MIN_CODEPOINT = 0x21
PASSWORD_ALLOWED_MAX_CODEPOINT = 0x7E


class PasswordRejected(ValueError):
    def __init__(self, reason: PasswordRejectReason) -> None:
        super().__init__(reason)
        self.reason: PasswordRejectReason = reason


@dataclass(frozen=True, slots=True)
class ThrottleDecision:
    allowed: bool
    # 허용되지 않을 때 다시 시도할 수 있는 시각(epoch 초). 허용이면 None.
    retry_after: float | None


def auth_db_path(data_dir: Path | str) -> Path:
    """``<data_dir>/auth/auth.db``."""
    return Path(data_dir) / AUTH_DB_RELATIVE_PATH


def connect_auth_db(path: Path | str) -> sqlite3.Connection:
    """auth.db를 열고(없으면 디렉터리·파일 생성) 스키마를 맞춘다. WAL, foreign_keys, busy_timeout은 `store.db.connect`와 같다.

    ``meta.schema_version``이 ``AUTH_DB_SCHEMA_VERSION``보다 크면 ``RuntimeError``(다운그레이드 거부).
    """
    p = Path(path)
    _ensure_private_dir(p.parent)
    _ensure_private_file(p)
    conn = sqlite3.connect(str(p), timeout=5.0)
    try:
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL;")
        conn.execute("PRAGMA foreign_keys=ON;")
        conn.execute("PRAGMA busy_timeout=5000;")
        init_auth_db(conn)
    except BaseException:
        conn.close()
        raise
    return conn


def init_auth_db(conn: sqlite3.Connection) -> None:
    """§13.3의 테이블을 멱등으로 만든다(A3)."""
    version = _stored_schema_version(conn)
    if version is not None and version > AUTH_DB_SCHEMA_VERSION:
        raise RuntimeError(
            "auth.db schema is newer than this code: "
            f"actual={version}, expected<={AUTH_DB_SCHEMA_VERSION}"
        )
    if version is not None and version < 1:
        raise RuntimeError(f"invalid auth.db schema_version: {version}")
    with conn:
        conn.executescript(_SCHEMA_SQL)
        conn.execute(
            "INSERT OR IGNORE INTO meta(key, value) VALUES ('schema_version', ?)",
            (str(AUTH_DB_SCHEMA_VERSION),),
        )


# --- 로컬 비밀번호 ----------------------------------------------------------------


def check_password_policy(password: str, *, username: str | None) -> None:
    """검사 순서: 허용 밖 문자(ASCII 0x21–0x7E 밖: 공백·비ASCII·제어문자)가 있으면 ``invalid_characters``, 12자 미만이면 ``too_short``, USERNAME을 대소문자 무시로 포함하면 ``contains_username``."""
    if any(
        not (PASSWORD_ALLOWED_MIN_CODEPOINT <= ord(ch) <= PASSWORD_ALLOWED_MAX_CODEPOINT)
        for ch in password
    ):
        raise PasswordRejected("invalid_characters")
    if len(password) < MIN_PASSWORD_LENGTH:
        raise PasswordRejected("too_short")
    if username and username.casefold() in password.casefold():
        raise PasswordRejected("contains_username")


def set_password(
    conn: sqlite3.Connection,
    account_id: int,
    password: str,
    *,
    username: str | None,
    now: float | None = None,
) -> None:
    """정책을 검사하고 새 솔트로 해시해 ``account_passwords``에 넣거나 바꾼다. ``params``에 n·r·p·dklen을 JSON으로 저장한다."""
    check_password_policy(password, username=username)
    params = _current_params()
    salt = os.urandom(SALT_BYTES)
    digest = _scrypt(password, salt, params)
    ts = time.time() if now is None else now
    with conn:
        conn.execute(
            """
            INSERT INTO account_passwords(account_id, algo, params, salt, hash, updated_at)
            VALUES (?, ?, ?, ?, ?, ?)
            ON CONFLICT(account_id) DO UPDATE SET
                algo=excluded.algo, params=excluded.params, salt=excluded.salt,
                hash=excluded.hash, updated_at=excluded.updated_at
            """,
            (account_id, _ALGO, json.dumps(params, sort_keys=True), salt, digest, ts),
        )


def has_password(conn: sqlite3.Connection, account_id: int) -> bool:
    row = conn.execute(
        "SELECT 1 FROM account_passwords WHERE account_id=?", (account_id,)
    ).fetchone()
    return row is not None


def verify_password(conn: sqlite3.Connection, account_id: int | None, password: str) -> bool:
    """저장된 매개변수로 다시 계산해 ``hmac.compare_digest``로 비교한다.

    ``account_id``가 None이거나 행이 없어도 같은 비용의 더미 계산을 한 뒤 False를 낸다. 계정 존재 여부가 응답 시간으로 드러나지 않게 하기 위해서다(§8.5).
    """
    row = None
    if account_id is not None:
        row = conn.execute(
            "SELECT algo, params, salt, hash FROM account_passwords WHERE account_id=?",
            (account_id,),
        ).fetchone()
    if row is None:
        # 계정 유무가 응답 시간으로 드러나지 않게 같은 비용으로 계산하고 버린다.
        hmac.compare_digest(_scrypt(password, _DUMMY_SALT, _DUMMY_PARAMS), _DUMMY_HASH)
        return False
    algo, params_json, salt, stored = row[0], row[1], row[2], row[3]
    if algo != _ALGO:
        raise RuntimeError(f"unsupported password algo in auth.db: {algo!r}")
    params = _parse_params(params_json)
    return hmac.compare_digest(_scrypt(password, bytes(salt), params), bytes(stored))


# --- 로그인 실패 제한 -------------------------------------------------------------


def begin_login_attempt(
    conn: sqlite3.Connection, *, username: str, ip: str, now: float | None = None
) -> ThrottleDecision:
    """비밀번호를 검증하기 **전에** 부른다. 한 트랜잭션(``BEGIN IMMEDIATE``) 안에서 판정과 기록을 함께 한다.

    - 두 키 중 하나라도 ``retry_after``가 미래면 아무것도 기록하지 않고 거부를 반환한다(거부한 시도는 세지 않는다).
    - 허용이면 이 시도를 실패로 미리 센다. 검증이 실패하면 더 할 일이 없고, 성공하면 `settle_login_success`를 부른다.

    확인과 기록 사이에 틈이 있으면(확인 → scrypt 수십 ms → 실패 기록) 동시 요청이 모두 확인을 통과해 무료 실패 수를 넘겨 검증에 닿는다. 그래서 둘을 한 쓰기 트랜잭션으로 묶는다.
    """
    ts = time.time() if now is None else now
    if conn.in_transaction:
        raise RuntimeError("begin_login_attempt needs its own transaction")
    conn.execute("BEGIN IMMEDIATE")
    try:
        decision = check_login_throttle(conn, username=username, ip=ip, now=ts)
        if decision.allowed:
            _increment_failures(conn, username=username, ip=ip, ts=ts)
        conn.commit()
    except BaseException:
        conn.rollback()
        raise
    return decision


def settle_login_success(conn: sqlite3.Connection, *, username: str, ip: str) -> None:
    """`begin_login_attempt`로 미리 센 시도가 성공했을 때 부른다.

    ``(username, IP)`` 키는 지운다. ``(IP)`` 키에서는 미리 센 이 시도 하나만 되돌린다. 성공은 실패가 아니므로 같은 IP의 정상 로그인이 백오프를 쌓지 않게 하고, 그 IP의 다른 실패는 그대로 남긴다. 되돌린 뒤 실패 수가 무료 범위면 ``retry_after``를 지우고, 아니면 그대로 둔다(보수적).
    """
    user_ip_key, ip_key = _throttle_keys(username, ip)
    with conn:
        if not conn.in_transaction:
            conn.execute("BEGIN IMMEDIATE")
        conn.execute("DELETE FROM login_throttle WHERE key=?", (user_ip_key,))
        row = conn.execute(
            "SELECT failures FROM login_throttle WHERE key=?", (ip_key,)
        ).fetchone()
        if row is not None:
            failures = max(0, int(row[0]) - 1)
            if _throttle_delay(failures) > 0:
                conn.execute(
                    "UPDATE login_throttle SET failures=? WHERE key=?", (failures, ip_key)
                )
            else:
                conn.execute(
                    "UPDATE login_throttle SET failures=?, retry_after=NULL WHERE key=?",
                    (failures, ip_key),
                )


def check_login_throttle(
    conn: sqlite3.Connection, *, username: str, ip: str, now: float | None = None
) -> ThrottleDecision:
    """두 키 중 하나라도 ``retry_after``가 미래면 거부한다. 계정이 없는 이름도 똑같이 센다. 읽기만 한다.

    라우트는 이 함수와 `record_login_failure`를 따로 부르지 않고 `begin_login_attempt`를 쓴다(둘 사이 틈이 TOCTOU다).
    ``login_throttle.key``에는 USERNAME 원문을 넣지 않는다(해시한 값을 쓴다).
    """
    ts = time.time() if now is None else now
    retry: float | None = None
    for key in _throttle_keys(username, ip):
        row = conn.execute(
            "SELECT retry_after FROM login_throttle WHERE key=?", (key,)
        ).fetchone()
        if row is not None and row[0] is not None and row[0] > ts:
            retry = row[0] if retry is None else max(retry, row[0])
    return ThrottleDecision(allowed=retry is None, retry_after=retry)


def record_login_failure(
    conn: sqlite3.Connection, *, username: str, ip: str, now: float | None = None
) -> ThrottleDecision:
    """두 키의 실패 수를 올리고 갱신된 결정을 반환한다. 창이 지난 키는 새로 센다."""
    ts = time.time() if now is None else now
    with conn:
        if not conn.in_transaction:
            # 두 프로세스가 같은 키를 동시에 올릴 때 읽기-쓰기 사이에 끼어들지 못하게 한다.
            conn.execute("BEGIN IMMEDIATE")
        return _increment_failures(conn, username=username, ip=ip, ts=ts)


def _increment_failures(
    conn: sqlite3.Connection, *, username: str, ip: str, ts: float
) -> ThrottleDecision:
    """열린 쓰기 트랜잭션 안에서 두 키의 실패 수를 하나씩 올린다."""
    retry: float | None = None
    for key in _throttle_keys(username, ip):
        row = conn.execute(
            "SELECT failures, window_start, retry_after FROM login_throttle WHERE key=?",
            (key,),
        ).fetchone()
        if row is None or _window_expired(row[1], row[2], ts):
            failures, window_start = 1, ts
        else:
            failures, window_start = int(row[0]) + 1, float(row[1])
        delay = _throttle_delay(failures)
        key_retry = ts + delay if delay > 0 else None
        conn.execute(
            """
            INSERT INTO login_throttle(key, failures, window_start, retry_after)
            VALUES (?, ?, ?, ?)
            ON CONFLICT(key) DO UPDATE SET
                failures=excluded.failures, window_start=excluded.window_start,
                retry_after=excluded.retry_after
            """,
            (key, failures, window_start, key_retry),
        )
        if key_retry is not None:
            retry = key_retry if retry is None else max(retry, key_retry)
    return ThrottleDecision(allowed=retry is None, retry_after=retry)


def prune_login_throttle(conn: sqlite3.Connection, *, now: float | None = None) -> int:
    """창과 ``retry_after``가 모두 지난 행을 지우고 지운 행 수를 반환한다.

    판정은 ``_window_expired``와 같다. 그런 행은 다음 실패 때 어차피 새로 세므로 지워도 백오프가 줄지 않는다. ``retry_after``만 지난 행을 지우면 공격자가 창마다 무료 실패를 다시 얻는다.
    """
    ts = time.time() if now is None else now
    with conn:
        cur = conn.execute(
            """
            DELETE FROM login_throttle
            WHERE ? - window_start >= ?
              AND (retry_after IS NULL OR ? - retry_after >= ?)
            """,
            (ts, THROTTLE_WINDOW_SECONDS, ts, THROTTLE_WINDOW_SECONDS),
        )
    return int(cur.rowcount)


def record_login_success(conn: sqlite3.Connection, *, username: str, ip: str) -> None:
    """(username, IP) 키만 지운다. (IP) 키는 남긴다(한 IP의 여러 이름 대입을 계속 센다)."""
    user_ip_key, _ip_key = _throttle_keys(username, ip)
    with conn:
        conn.execute("DELETE FROM login_throttle WHERE key=?", (user_ip_key,))


# --- 관리자 감사 기록 -------------------------------------------------------------


def record_admin_audit(
    conn: sqlite3.Connection,
    *,
    actor_account_id: int,
    action: str,
    target_account_id: int | None = None,
    detail: dict[str, Any] | None = None,
    now: float | None = None,
) -> int:
    """행을 추가하고 id를 반환한다. ``detail``에 ``username`` 키가 있으면 ``ValueError``(K4: 감사 기록은 account_id로만)."""
    detail_json: str | None = None
    if detail is not None:
        _reject_username_keys(detail)
        detail_json = json.dumps(detail, ensure_ascii=False, sort_keys=True)
    ts = time.time() if now is None else now
    with conn:
        cur = conn.execute(
            """
            INSERT INTO admin_audit(at, actor_account_id, action, target_account_id, detail_json)
            VALUES (?, ?, ?, ?, ?)
            """,
            (ts, actor_account_id, action, target_account_id, detail_json),
        )
    return int(cur.lastrowid)


def list_admin_audit(conn: sqlite3.Connection, *, limit: int = 100) -> list[dict[str, Any]]:
    """최근 것부터."""
    if limit < 1:
        raise ValueError(f"limit must be >= 1: {limit}")
    rows = conn.execute(
        """
        SELECT id, at, actor_account_id, action, target_account_id, detail_json
        FROM admin_audit ORDER BY at DESC, id DESC LIMIT ?
        """,
        (limit,),
    ).fetchall()
    return [
        {
            "id": r[0],
            "at": r[1],
            "actor_account_id": r[2],
            "action": r[3],
            "target_account_id": r[4],
            "detail": json.loads(r[5]) if r[5] is not None else None,
        }
        for r in rows
    ]


# --- 내부 ----------------------------------------------------------------------

_ALGO = "scrypt"

_SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS meta (
    key TEXT PRIMARY KEY,
    value TEXT
);
CREATE TABLE IF NOT EXISTS account_passwords (
    account_id INTEGER PRIMARY KEY,
    algo TEXT NOT NULL, params TEXT NOT NULL,
    salt BLOB NOT NULL, hash BLOB NOT NULL,
    updated_at REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS account_identities (
    iss TEXT NOT NULL, sub TEXT NOT NULL,
    account_id INTEGER NOT NULL, provider_id TEXT NOT NULL,
    linked_at REAL NOT NULL, last_login_at REAL,
    PRIMARY KEY (iss, sub)
);
CREATE TABLE IF NOT EXISTS account_provider_keys (
    id TEXT PRIMARY KEY, account_id INTEGER NOT NULL, provider TEXT NOT NULL,
    ciphertext BLOB NOT NULL, nonce BLOB NOT NULL, key_version INTEGER NOT NULL,
    fingerprint TEXT NOT NULL, status TEXT NOT NULL,
    created_at REAL NOT NULL, last_verified_at REAL, last_used_at REAL,
    UNIQUE (account_id, provider)
);
CREATE TABLE IF NOT EXISTS invitations (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    public_id INTEGER UNIQUE NOT NULL,
    code_hmac TEXT UNIQUE NOT NULL,
    code_ciphertext BLOB NOT NULL, code_nonce BLOB NOT NULL, key_version INTEGER NOT NULL,
    created_by INTEGER NOT NULL,
    created_at REAL NOT NULL,
    expires_at REAL NOT NULL,
    max_uses INTEGER NOT NULL CHECK (max_uses BETWEEN 1 AND 5),
    uses_count INTEGER NOT NULL DEFAULT 0,
    reserved_username TEXT,
    note TEXT NOT NULL DEFAULT '',
    revoked_at REAL, revoked_by INTEGER,
    CHECK (uses_count <= max_uses),
    CHECK (reserved_username IS NULL OR max_uses = 1)
);
CREATE TABLE IF NOT EXISTS invitation_redemptions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    invitation_id INTEGER NOT NULL REFERENCES invitations(id),
    account_id INTEGER,
    method TEXT NOT NULL,
    status TEXT NOT NULL,
    started_at REAL NOT NULL, finished_at REAL
);
CREATE INDEX IF NOT EXISTS idx_redemptions_invitation
    ON invitation_redemptions(invitation_id, status);
CREATE TABLE IF NOT EXISTS oidc_states (
    key TEXT PRIMARY KEY, provider_id TEXT NOT NULL,
    state TEXT NOT NULL, nonce TEXT NOT NULL, code_verifier TEXT NOT NULL,
    purpose TEXT NOT NULL,
    invitation_id INTEGER,
    expires_at REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS login_throttle (
    key TEXT PRIMARY KEY, failures INTEGER NOT NULL,
    window_start REAL NOT NULL, retry_after REAL
);
CREATE TABLE IF NOT EXISTS account_usage (
    account_id INTEGER NOT NULL, period TEXT NOT NULL,
    tokens_in INTEGER NOT NULL DEFAULT 0, tokens_out INTEGER NOT NULL DEFAULT 0,
    ingests INTEGER NOT NULL DEFAULT 0,
    PRIMARY KEY (account_id, period)
);
CREATE TABLE IF NOT EXISTS admin_audit (
    id INTEGER PRIMARY KEY AUTOINCREMENT, at REAL NOT NULL,
    actor_account_id INTEGER NOT NULL, action TEXT NOT NULL,
    target_account_id INTEGER, detail_json TEXT
);
"""


def _ensure_private_dir(d: Path) -> None:
    d.mkdir(mode=0o700, parents=True, exist_ok=True)
    if os.name == "posix":
        os.chmod(d, 0o700)


def _ensure_private_file(p: Path) -> None:
    # sqlite3.connect가 umask대로 파일을 만들기 전에 0600으로 먼저 만든다. WAL·SHM 파일은 SQLite가 본 파일 권한을 따른다.
    try:
        fd = os.open(p, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
    except FileExistsError:
        pass
    else:
        os.close(fd)
    if os.name == "posix":
        os.chmod(p, 0o600)


def _stored_schema_version(conn: sqlite3.Connection) -> int | None:
    has_meta = conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name='meta'"
    ).fetchone()
    if has_meta is None:
        return None
    row = conn.execute("SELECT value FROM meta WHERE key='schema_version'").fetchone()
    if row is None:
        return None
    try:
        return int(row[0])
    except (TypeError, ValueError) as exc:
        raise RuntimeError(f"invalid auth.db schema_version: {row[0]!r}") from exc


def _current_params() -> dict[str, int]:
    return {"n": SCRYPT_N, "r": SCRYPT_R, "p": SCRYPT_P, "dklen": SCRYPT_DKLEN}


def _parse_params(raw: str) -> dict[str, int]:
    try:
        params = json.loads(raw)
        parsed = {k: params[k] for k in ("n", "r", "p", "dklen")}
    except (ValueError, TypeError, KeyError) as exc:
        raise RuntimeError(f"invalid scrypt params in auth.db: {raw!r}") from exc
    if not all(type(v) is int and v > 0 for v in parsed.values()):
        raise RuntimeError(f"invalid scrypt params in auth.db: {raw!r}")
    return parsed


def _scrypt(password: str, salt: bytes, params: dict[str, int]) -> bytes:
    with _SCRYPT_SLOTS:
        return hashlib.scrypt(
            password.encode("utf-8"),
            salt=salt,
            n=params["n"],
            r=params["r"],
            p=params["p"],
            dklen=params["dklen"],
            maxmem=SCRYPT_MAXMEM,
        )


# 없는 계정 검증용. 맞힐 수 없는 무작위 비밀번호로 모듈 로드 시 한 번 만든다.
_DUMMY_PARAMS = _current_params()
_DUMMY_SALT = os.urandom(SALT_BYTES)
_DUMMY_HASH = _scrypt(secrets.token_urlsafe(32), _DUMMY_SALT, _DUMMY_PARAMS)


def _throttle_keys(username: str, ip: str) -> tuple[str, str]:
    """``(username, IP)``·``(IP)`` 키. USERNAME 원문이 남지 않게 SHA-256으로 줄인다."""
    pair = json.dumps([username.lower(), ip], ensure_ascii=False).encode("utf-8")
    user_ip = "user_ip:" + hashlib.sha256(b"claire-login-throttle-v1\0" + pair).hexdigest()
    ip_only = "ip:" + hashlib.sha256(
        b"claire-login-throttle-v1\0" + ip.encode("utf-8")
    ).hexdigest()
    return user_ip, ip_only


def _window_expired(window_start: float, retry_after: float | None, now: float) -> bool:
    """창이 지났는지. 지연이 붙은 키는 대기가 끝난 뒤로도 창 길이만큼 조용해야 새로 센다.

    ``window_start``만 보면 누적 대기(1+2+...+512초)가 창(15분)보다 길어 상한에 닿기 전에 매번 초기화되고, 공격자가 창마다 무료 실패를 다시 얻는다.
    """
    if now - window_start < THROTTLE_WINDOW_SECONDS:
        return False
    return retry_after is None or now - retry_after >= THROTTLE_WINDOW_SECONDS


def _throttle_delay(failures: int) -> float:
    over = failures - THROTTLE_FREE_FAILURES
    if over <= 0:
        return 0.0
    exponent = min(over - 1, 62)
    return float(min(THROTTLE_MAX_DELAY_SECONDS, _THROTTLE_BASE_DELAY_SECONDS * (2**exponent)))


def _reject_username_keys(value: Any) -> None:
    if isinstance(value, dict):
        for k, v in value.items():
            if isinstance(k, str) and k.lower() == "username":
                raise ValueError("admin audit detail must not contain 'username' (K4)")
            _reject_username_keys(v)
    elif isinstance(value, (list, tuple)):
        for v in value:
            _reject_username_keys(v)
