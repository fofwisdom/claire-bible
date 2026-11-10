"""``data/auth/auth.db``: 자격 비밀과 제어 평면 (설계 §13.3).

지식 DB(``claire.db``)와 분리한다. graph bundle·support bundle·내보내기에 비밀이 섞이지 않게 하고, 지식 DB 장애가 로그인으로 번지지 않게 하기 위해서다. 자체 ``meta.schema_version``을 1부터 둔다(v2: ``account_usage`` 일 적재 열, P4).

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
from typing import Any, Callable, Literal

from .. import ids
from ..accounts.invites import (
    InvitationInfo,
    InviteError,
    InviteInactiveError,
    InviteNotFoundError,
    InviteValidationError,
    generate_invite_code,
    mask_invite_code,
    normalize_invite_code,
    validate_invite_code_syntax,
    validate_invite_params,
)
from .secrets import SecretDecryptionError, SecretService, get_secret_service

# v3 (P5 L5-b): ``oidc_states``에 계정 연결용 ``account_id``를 더한다(§12-7, §13.3).
# v4 (유저 단위 기능): ``account_telegram_bots``(계정별 텔레그램 봇)와 ``account_backup_policies``(계정 백업 주기)를 더한다.
AUTH_DB_SCHEMA_VERSION = 4
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
        prune_stale_redemptions(conn)
        prune_stale_oidc_states(conn)
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
        # executescript는 열린 트랜잭션을 먼저 커밋한다. 아래 이관과 버전 기록은 한 트랜잭션으로 묶는다.
        if not conn.in_transaction:
            conn.execute("BEGIN IMMEDIATE")
        _migrate_v2_account_usage_daily(conn)
        _migrate_v3_oidc_states_account_id(conn)
        conn.execute(
            "INSERT OR IGNORE INTO meta(key, value) VALUES ('schema_version', ?)",
            (str(AUTH_DB_SCHEMA_VERSION),),
        )
        conn.execute(
            "UPDATE meta SET value=? WHERE key='schema_version' AND CAST(value AS INTEGER) < ?",
            (str(AUTH_DB_SCHEMA_VERSION), AUTH_DB_SCHEMA_VERSION),
        )


# v2에서 ``account_usage``에 더한 열. ``CREATE TABLE IF NOT EXISTS``는 v1 테이블에 열을 더하지 못하므로 따로 넣는다.
_ACCOUNT_USAGE_V2_COLUMNS: tuple[tuple[str, str], ...] = (
    ("daily_day", "TEXT NOT NULL DEFAULT ''"),
    ("daily_ingests", "INTEGER NOT NULL DEFAULT 0"),
)


def _migrate_v2_account_usage_daily(conn: sqlite3.Connection) -> None:
    """v1 ``account_usage``에 v2 열을 더한다. 열이 이미 있으면 아무것도 하지 않는다(멱등, A3)."""
    existing = {str(r[1]) for r in conn.execute("PRAGMA table_info(account_usage)")}
    for name, decl in _ACCOUNT_USAGE_V2_COLUMNS:
        if name not in existing:
            conn.execute(f"ALTER TABLE account_usage ADD COLUMN {name} {decl}")


_OIDC_STATES_V3_COLUMNS: tuple[tuple[str, str], ...] = (
    ("account_id", "INTEGER"),
)


def _migrate_v3_oidc_states_account_id(conn: sqlite3.Connection) -> None:
    """v2 ``oidc_states``에 v3 계정 연결용 ``account_id`` 열을 더한다(멱등, A3)."""
    existing = {str(r[1]) for r in conn.execute("PRAGMA table_info(oidc_states)")}
    for name, decl in _OIDC_STATES_V3_COLUMNS:
        if name not in existing:
            conn.execute(f"ALTER TABLE oidc_states ADD COLUMN {name} {decl}")


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
    account_id INTEGER,
    expires_at REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_oidc_states_expires ON oidc_states(expires_at);
CREATE TABLE IF NOT EXISTS login_throttle (
    key TEXT PRIMARY KEY, failures INTEGER NOT NULL,
    window_start REAL NOT NULL, retry_after REAL
);
CREATE TABLE IF NOT EXISTS account_usage (
    account_id INTEGER NOT NULL, period TEXT NOT NULL,
    tokens_in INTEGER NOT NULL DEFAULT 0, tokens_out INTEGER NOT NULL DEFAULT 0,
    ingests INTEGER NOT NULL DEFAULT 0,
    daily_day TEXT NOT NULL DEFAULT '',
    daily_ingests INTEGER NOT NULL DEFAULT 0,
    PRIMARY KEY (account_id, period)
);
CREATE TABLE IF NOT EXISTS account_telegram_bots (
    account_id INTEGER PRIMARY KEY,
    key_id TEXT NOT NULL,
    ciphertext BLOB NOT NULL, nonce BLOB NOT NULL, key_version INTEGER NOT NULL,
    fingerprint TEXT NOT NULL,
    bot_id INTEGER NOT NULL UNIQUE,
    bot_username TEXT NOT NULL DEFAULT '',
    allowed_user_ids TEXT NOT NULL DEFAULT '',
    enabled INTEGER NOT NULL DEFAULT 1 CHECK (enabled IN (0, 1)),
    status TEXT NOT NULL DEFAULT 'pending',
    last_error TEXT,
    status_at REAL,
    created_at REAL NOT NULL,
    updated_at REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS account_backup_policies (
    account_id INTEGER PRIMARY KEY,
    schedule TEXT NOT NULL DEFAULT 'off' CHECK (schedule IN ('off', 'daily', 'weekly')),
    keep INTEGER NOT NULL DEFAULT 3 CHECK (keep BETWEEN 1 AND 10),
    last_run_at REAL,
    last_status TEXT,
    last_error TEXT,
    updated_at REAL NOT NULL
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


# --- 초대 (설계 §8.8, §13.3, §15 S12) ----------------------------------------------

INVITATION_HMAC_PURPOSE = "invitation_code"
INVITATION_RATE_LIMIT_WINDOW_SECONDS = 60.0
INVITATION_RATE_LIMIT_MAX_FAILURES = 10


def _invite_throttle_key(ip: str) -> str:
    return "invite_fail_ip:" + hashlib.sha256(b"claire-invite-throttle-v1\0" + ip.encode("utf-8")).hexdigest()


def check_invite_throttle(conn: sqlite3.Connection, *, ip: str, now: float | None = None) -> ThrottleDecision:
    """존재하지 않는 코드 조회 실패가 분당 10회를 넘으면 차단/지연 결정을 반환한다."""
    ts = time.time() if now is None else now
    key = _invite_throttle_key(ip)
    row = conn.execute("SELECT retry_after FROM login_throttle WHERE key=?", (key,)).fetchone()
    if row is not None and row[0] is not None and row[0] > ts:
        return ThrottleDecision(allowed=False, retry_after=float(row[0]))
    return ThrottleDecision(allowed=True, retry_after=None)


def record_invite_failure(conn: sqlite3.Connection, *, ip: str, now: float | None = None) -> ThrottleDecision:
    """틀린(존재하지 않거나 문법 오류인) 코드 조회 시 실패를 기록한다."""
    ts = time.time() if now is None else now
    key = _invite_throttle_key(ip)
    with conn:
        row = conn.execute("SELECT failures, window_start, retry_after FROM login_throttle WHERE key=?", (key,)).fetchone()
        if row is None or (ts - float(row[1])) >= INVITATION_RATE_LIMIT_WINDOW_SECONDS:
            failures, window_start = 1, ts
        else:
            failures, window_start = int(row[0]) + 1, float(row[1])
        retry_after = (window_start + INVITATION_RATE_LIMIT_WINDOW_SECONDS) if failures > INVITATION_RATE_LIMIT_MAX_FAILURES else None
        conn.execute(
            """
            INSERT INTO login_throttle (key, failures, window_start, retry_after)
            VALUES (?, ?, ?, ?)
            ON CONFLICT(key) DO UPDATE SET
                failures=excluded.failures, window_start=excluded.window_start,
                retry_after=excluded.retry_after
            """,
            (key, failures, window_start, retry_after),
        )
    return ThrottleDecision(allowed=retry_after is None, retry_after=retry_after)


def create_invitation(
    conn: sqlite3.Connection,
    *,
    created_by: int,
    max_uses: int = 1,
    expires_in: float | None = None,
    expires_at: float | None = None,
    reserved_username: str | None = None,
    note: str = "",
    code: str | None = None,
    id_salt: bytes | None = None,
    secret_service: SecretService | None = None,
    now: float | None = None,
) -> InvitationInfo:
    """초대를 생성하고 HMAC 색인 및 AES-256-GCM 암호화 사본을 저장한다."""
    ts = time.time() if now is None else now
    if expires_at is None:
        dur = float(expires_in) if expires_in is not None else float(7 * 86400)
        expires_at_val = ts + dur
    else:
        dur = float(expires_at) - ts
        expires_at_val = float(expires_at)

    norm_reserved = reserved_username.strip().lower() if reserved_username else None
    validate_invite_params(max_uses, dur, norm_reserved)

    code_val = code if code is not None else generate_invite_code()
    norm_code = normalize_invite_code(code_val)
    if not validate_invite_code_syntax(norm_code):
        raise InviteValidationError(f"Invalid invite code syntax: {code_val!r}")

    sec = secret_service or get_secret_service()
    code_hmac = sec.hmac_index(norm_code, purpose=INVITATION_HMAC_PURPOSE)

    salt = id_salt if id_salt is not None else sec.derive_hmac_key("invitation_id_salt")
    unix_ms = int(ts * 1000)
    public_id = ids.timestamp_id("invitations", salt=salt, unix_ms=unix_ms)

    enc = sec.encrypt(norm_code, aad=f"invitation|{public_id}")

    with conn:
        cur = conn.execute(
            """
            INSERT INTO invitations (
                public_id, code_hmac, code_ciphertext, code_nonce, key_version,
                created_by, created_at, expires_at, max_uses, uses_count,
                reserved_username, note
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 0, ?, ?)
            """,
            (
                public_id,
                code_hmac,
                enc.ciphertext,
                enc.nonce,
                enc.key_version,
                created_by,
                ts,
                expires_at_val,
                max_uses,
                norm_reserved,
                note.strip(),
            ),
        )
        row_id = int(cur.lastrowid)

    return InvitationInfo(
        id=row_id,
        public_id=public_id,
        code_masked=mask_invite_code(norm_code),
        created_by=created_by,
        created_at=ts,
        expires_at=expires_at_val,
        max_uses=max_uses,
        uses_count=0,
        reserved_username=norm_reserved,
        note=note.strip(),
        revoked_at=None,
        revoked_by=None,
        code=norm_code,
    )


def get_invitation_by_code(
    conn: sqlite3.Connection,
    code: str,
    *,
    check_active: bool = True,
    secret_service: SecretService | None = None,
    now: float | None = None,
) -> InvitationInfo:
    """정규화 코드의 HMAC 색인으로 조회 후 복호화하여 반환한다. 없으면 404, 비활성은 410 상태 예외를 낸다."""
    norm_code = normalize_invite_code(code)
    if not validate_invite_code_syntax(norm_code):
        raise InviteNotFoundError(f"Invalid invite code syntax: {code!r}")

    sec = secret_service or get_secret_service()
    code_hmac = sec.hmac_index(norm_code, purpose=INVITATION_HMAC_PURPOSE)

    row = conn.execute(
        """
        SELECT id, public_id, code_hmac, code_ciphertext, code_nonce, key_version,
               created_by, created_at, expires_at, max_uses, uses_count,
               reserved_username, note, revoked_at, revoked_by
        FROM invitations WHERE code_hmac=?
        """,
        (code_hmac,),
    ).fetchone()
    if row is None:
        raise InviteNotFoundError(f"Invitation with code {norm_code} not found")

    try:
        raw_code = sec.decrypt(
            row["code_ciphertext"],
            row["code_nonce"],
            aad=f"invitation|{row['public_id']}",
            key_version=row["key_version"],
        )
    except SecretDecryptionError:
        raw_code = sec.decrypt(
            row["code_ciphertext"],
            row["code_nonce"],
            aad=f"invitation|{row['id']}",
            key_version=row["key_version"],
        )
    decrypted_code = raw_code.decode("utf-8")

    info = InvitationInfo(
        id=row["id"],
        public_id=row["public_id"],
        code_masked=mask_invite_code(decrypted_code),
        created_by=row["created_by"],
        created_at=row["created_at"],
        expires_at=row["expires_at"],
        max_uses=row["max_uses"],
        uses_count=row["uses_count"],
        reserved_username=row["reserved_username"],
        note=row["note"] or "",
        revoked_at=row["revoked_at"],
        revoked_by=row["revoked_by"],
        code=decrypted_code,
    )

    if check_active:
        ts = time.time() if now is None else now
        if info.revoked_at is not None:
            raise InviteInactiveError("revoked")
        if info.uses_count >= info.max_uses:
            raise InviteInactiveError("exhausted")
        if info.expires_at <= ts:
            raise InviteInactiveError("expired")

    return info


def get_invitation_by_id(
    conn: sqlite3.Connection,
    identifier: int,
    *,
    secret_service: SecretService | None = None,
) -> InvitationInfo | None:
    """id 또는 public_id로 초대를 조회해 복호화하여 반환한다."""
    sec = secret_service or get_secret_service()
    row = conn.execute(
        """
        SELECT id, public_id, code_hmac, code_ciphertext, code_nonce, key_version,
               created_by, created_at, expires_at, max_uses, uses_count,
               reserved_username, note, revoked_at, revoked_by
        FROM invitations WHERE id=? OR public_id=?
        """,
        (identifier, identifier),
    ).fetchone()
    if row is None:
        return None

    try:
        raw_code = sec.decrypt(
            row["code_ciphertext"],
            row["code_nonce"],
            aad=f"invitation|{row['public_id']}",
            key_version=row["key_version"],
        )
    except SecretDecryptionError:
        raw_code = sec.decrypt(
            row["code_ciphertext"],
            row["code_nonce"],
            aad=f"invitation|{row['id']}",
            key_version=row["key_version"],
        )
    decrypted_code = raw_code.decode("utf-8")

    return InvitationInfo(
        id=row["id"],
        public_id=row["public_id"],
        code_masked=mask_invite_code(decrypted_code),
        created_by=row["created_by"],
        created_at=row["created_at"],
        expires_at=row["expires_at"],
        max_uses=row["max_uses"],
        uses_count=row["uses_count"],
        reserved_username=row["reserved_username"],
        note=row["note"] or "",
        revoked_at=row["revoked_at"],
        revoked_by=row["revoked_by"],
        code=decrypted_code,
    )


def revoke_invitation(
    conn: sqlite3.Connection,
    invitation_id: int,
    *,
    revoked_by: int,
    now: float | None = None,
) -> bool:
    """초대를 비활성화한다. 이미 비활성화되었거나 없으면 False."""
    ts = time.time() if now is None else now
    with conn:
        cur = conn.execute(
            """
            UPDATE invitations
            SET revoked_at=?, revoked_by=?
            WHERE (id=? OR public_id=?) AND revoked_at IS NULL
            """,
            (ts, revoked_by, invitation_id, invitation_id),
        )
    return cur.rowcount > 0


def redeem_invitation_atomic(
    conn: sqlite3.Connection,
    invitation_id: int,
    *,
    method: str = "local",
    account_callback: Callable[[], int],
    now: float | None = None,
) -> int:
    """초대를 조건부 원자적 UPDATE로 차감하고 콜백을 실행한다. 실패 시 uses_count를 복원하고 'failed'로 기록한다."""
    ts = time.time() if now is None else now
    if conn.in_transaction:
        raise RuntimeError("redeem_invitation_atomic needs its own transaction boundary")

    conn.execute("BEGIN IMMEDIATE")
    try:
        cur = conn.execute(
            """
            UPDATE invitations
            SET uses_count = uses_count + 1
            WHERE (id=? OR public_id=?)
              AND revoked_at IS NULL
              AND expires_at > ?
              AND uses_count < max_uses
            """,
            (invitation_id, invitation_id, ts),
        )
        if cur.rowcount != 1:
            row = conn.execute(
                "SELECT id, revoked_at, expires_at, uses_count, max_uses FROM invitations WHERE id=? OR public_id=?",
                (invitation_id, invitation_id),
            ).fetchone()
            conn.rollback()
            if row is None:
                raise InviteNotFoundError(f"Invitation {invitation_id} not found")
            if row["revoked_at"] is not None:
                raise InviteInactiveError("revoked")
            if row["uses_count"] >= row["max_uses"]:
                raise InviteInactiveError("exhausted")
            if row["expires_at"] <= ts:
                raise InviteInactiveError("expired")
            raise InviteInactiveError("exhausted")

        cur_redemption = conn.execute(
            """
            INSERT INTO invitation_redemptions (invitation_id, method, status, started_at)
            VALUES ((SELECT id FROM invitations WHERE id=? OR public_id=?), ?, 'pending', ?)
            """,
            (invitation_id, invitation_id, method, ts),
        )
        redemption_id = int(cur_redemption.lastrowid)
        conn.commit()
    except BaseException:
        if conn.in_transaction:
            conn.rollback()
        raise

    try:
        account_id = account_callback()
    except BaseException:
        with conn:
            conn.execute(
                "UPDATE invitations SET uses_count = max(0, uses_count - 1) WHERE id=? OR public_id=?",
                (invitation_id, invitation_id),
            )
            conn.execute(
                "UPDATE invitation_redemptions SET status='failed', finished_at=? WHERE id=?",
                (time.time() if now is None else now, redemption_id),
            )
        raise

    with conn:
        conn.execute(
            "UPDATE invitation_redemptions SET status='completed', account_id=?, finished_at=? WHERE id=?",
            (account_id, time.time() if now is None else now, redemption_id),
        )
    return account_id


def list_invitations(
    conn: sqlite3.Connection,
    query: Any = None,
    *,
    max_id: int | None = None,
    min_id: int | None = None,
    since_id: int | None = None,
    limit: int = 20,
    secret_service: SecretService | None = None,
) -> tuple[list[dict[str, Any]], int]:
    """초대 컬렉션 커서 기반 목록과 전체 개수를 반환한다."""
    q_max_id = getattr(query, "max_id", max_id) if query is not None else max_id
    q_min_id = getattr(query, "min_id", min_id) if query is not None else min_id
    q_since_id = getattr(query, "since_id", since_id) if query is not None else since_id
    q_limit = getattr(query, "limit", limit) if query is not None else limit

    sec = secret_service or get_secret_service()
    total_row = conn.execute("SELECT COUNT(*) FROM invitations").fetchone()
    total_count = int(total_row[0]) if total_row is not None else 0

    if q_max_id is not None:
        rows = conn.execute(
            "SELECT * FROM invitations WHERE public_id < ? ORDER BY public_id DESC LIMIT ?",
            (q_max_id, q_limit),
        ).fetchall()
    elif q_min_id is not None:
        rows = conn.execute(
            "SELECT * FROM invitations WHERE public_id > ? ORDER BY public_id ASC LIMIT ?",
            (q_min_id, q_limit),
        ).fetchall()
        rows = list(reversed(rows))
    elif q_since_id is not None:
        rows = conn.execute(
            "SELECT * FROM invitations WHERE public_id > ? ORDER BY public_id DESC LIMIT ?",
            (q_since_id, q_limit),
        ).fetchall()
    else:
        rows = conn.execute(
            "SELECT * FROM invitations ORDER BY public_id DESC LIMIT ?",
            (q_limit,),
        ).fetchall()

    items: list[dict[str, Any]] = []
    now_ts = time.time()
    for row in rows:
        try:
            raw_code = sec.decrypt(
                row["code_ciphertext"],
                row["code_nonce"],
                aad=f"invitation|{row['public_id']}",
                key_version=row["key_version"],
            )
        except SecretDecryptionError:
            raw_code = sec.decrypt(
                row["code_ciphertext"],
                row["code_nonce"],
                aad=f"invitation|{row['id']}",
                key_version=row["key_version"],
            )
        code_str = raw_code.decode("utf-8")

        if row["revoked_at"] is not None:
            status = "revoked"
        elif row["uses_count"] >= row["max_uses"]:
            status = "exhausted"
        elif row["expires_at"] <= now_ts:
            status = "expired"
        else:
            status = "active"

        redemptions = conn.execute(
            "SELECT account_id FROM invitation_redemptions WHERE invitation_id=? AND status='completed'",
            (row["id"],),
        ).fetchall()
        account_ids = [int(r[0]) for r in redemptions if r[0] is not None]

        items.append({
            "id": row["id"],
            "public_id": ids.format_public_id(row["public_id"]),
            "code": code_str,
            "code_masked": mask_invite_code(code_str),
            "status": status,
            "uses_count": row["uses_count"],
            "max_uses": row["max_uses"],
            "created_by": row["created_by"],
            "created_at": row["created_at"],
            "expires_at": row["expires_at"],
            "revoked_at": row["revoked_at"],
            "revoked_by": row["revoked_by"],
            "reserved_username": row["reserved_username"],
            "note": row["note"] or "",
            "redeemed_account_ids": account_ids,
        })

    return items, total_count


def prune_stale_redemptions(
    conn: sqlite3.Connection,
    *,
    now: float | None = None,
    stale_seconds: float = 600.0,
) -> int:
    """10분 이상 pending 상태인 redemptions를 failed로 정리하고 uses_count를 복원한다."""
    ts = time.time() if now is None else now
    cutoff = ts - stale_seconds
    rows = conn.execute(
        "SELECT id, invitation_id FROM invitation_redemptions WHERE status='pending' AND started_at <= ?",
        (cutoff,),
    ).fetchall()
    if not rows:
        return 0
    with conn:
        for r in rows:
            redemption_id, inv_id = int(r[0]), int(r[1])
            conn.execute("UPDATE invitations SET uses_count = max(0, uses_count - 1) WHERE id=?", (inv_id,))
            conn.execute("UPDATE invitation_redemptions SET status='failed', finished_at=? WHERE id=?", (ts, redemption_id))
    return len(rows)


# --- 외부 IdP 및 OIDC 상태 (설계 §12, §13.3, P5 L5-b) --------------------------------

OidcPurpose = Literal["login", "link", "signup"]


@dataclass(frozen=True, slots=True)
class OidcStateRecord:
    key: str
    provider_id: str
    state: str
    nonce: str
    code_verifier: str
    purpose: OidcPurpose
    invitation_id: int | None
    account_id: int | None
    expires_at: float


@dataclass(frozen=True, slots=True)
class AccountIdentityRecord:
    iss: str
    sub: str
    account_id: int
    provider_id: str
    linked_at: float
    last_login_at: float | None


class OidcStateError(Exception):
    """Base error for OIDC state operations."""


class OidcStateNotFoundError(OidcStateError):
    """State record does not exist or expired."""


class OidcStateMismatchError(OidcStateError):
    """State parameter does not match recorded state."""


class IdentityAlreadyLinkedError(Exception):
    """(iss, sub) identity is already linked to an account."""


class IdentityUnlinkForbiddenError(Exception):
    """Cannot unlink the only remaining authentication credential."""


def save_oidc_state(
    conn: sqlite3.Connection,
    *,
    key: str,
    provider_id: str,
    state: str,
    nonce: str,
    code_verifier: str,
    purpose: OidcPurpose,
    invitation_id: int | None = None,
    account_id: int | None = None,
    expires_at: float,
) -> None:
    """단기 OIDC 교환 상태(10분 수명)를 저장한다."""
    with conn:
        conn.execute(
            """
            INSERT INTO oidc_states (
                key, provider_id, state, nonce, code_verifier,
                purpose, invitation_id, account_id, expires_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (key, provider_id, state, nonce, code_verifier, purpose, invitation_id, account_id, expires_at),
        )


def consume_oidc_state(
    conn: sqlite3.Connection,
    *,
    key: str,
    state: str,
    now: float | None = None,
) -> OidcStateRecord:
    """OIDC 상태를 원자적으로 조회하고 즉시 삭제하여 재생 공격을 방지한다."""
    ts = time.time() if now is None else now
    with conn:
        row = conn.execute(
            """
            SELECT key, provider_id, state, nonce, code_verifier,
                   purpose, invitation_id, account_id, expires_at
            FROM oidc_states WHERE key = ?
            """,
            (key,),
        ).fetchone()
        if row is None:
            raise OidcStateNotFoundError("State not found")
        if float(row["expires_at"]) <= ts:
            conn.execute("DELETE FROM oidc_states WHERE key = ?", (key,))
            conn.commit()
            raise OidcStateNotFoundError("State expired")
        if not hmac.compare_digest(str(row["state"]), str(state)):
            conn.execute("DELETE FROM oidc_states WHERE key = ?", (key,))
            conn.commit()
            raise OidcStateMismatchError("State mismatch")
        conn.execute("DELETE FROM oidc_states WHERE key = ?", (key,))

    return OidcStateRecord(
        key=str(row["key"]),
        provider_id=str(row["provider_id"]),
        state=str(row["state"]),
        nonce=str(row["nonce"]),
        code_verifier=str(row["code_verifier"]),
        purpose=str(row["purpose"]),  # type: ignore[assignment]
        invitation_id=int(row["invitation_id"]) if row["invitation_id"] is not None else None,
        account_id=int(row["account_id"]) if row["account_id"] is not None else None,
        expires_at=float(row["expires_at"]),
    )


def prune_stale_oidc_states(
    conn: sqlite3.Connection,
    *,
    now: float | None = None,
) -> int:
    """만료된 OIDC state 행을 정리한다."""
    ts = time.time() if now is None else now
    with conn:
        cur = conn.execute("DELETE FROM oidc_states WHERE expires_at <= ?", (ts,))
    return int(cur.rowcount)


def link_account_identity(
    conn: sqlite3.Connection,
    *,
    iss: str,
    sub: str,
    account_id: int,
    provider_id: str,
    now: float | None = None,
) -> None:
    """외부 신원(iss, sub)을 계정에 연결한다. 이미 연결되어 있으면 IdentityAlreadyLinkedError 발생."""
    ts = time.time() if now is None else now
    try:
        with conn:
            conn.execute(
                """
                INSERT INTO account_identities (iss, sub, account_id, provider_id, linked_at, last_login_at)
                VALUES (?, ?, ?, ?, ?, NULL)
                """,
                (iss, sub, account_id, provider_id, ts),
            )
    except sqlite3.IntegrityError as exc:
        raise IdentityAlreadyLinkedError(f"Identity ({iss}, {sub}) is already linked") from exc


def find_account_identity(
    conn: sqlite3.Connection,
    *,
    iss: str,
    sub: str,
) -> AccountIdentityRecord | None:
    """(iss, sub)로 연결된 계정 신원을 조회한다."""
    row = conn.execute(
        """
        SELECT iss, sub, account_id, provider_id, linked_at, last_login_at
        FROM account_identities WHERE iss = ? AND sub = ?
        """,
        (iss, sub),
    ).fetchone()
    if row is None:
        return None
    return AccountIdentityRecord(
        iss=str(row["iss"]),
        sub=str(row["sub"]),
        account_id=int(row["account_id"]),
        provider_id=str(row["provider_id"]),
        linked_at=float(row["linked_at"]),
        last_login_at=float(row["last_login_at"]) if row["last_login_at"] is not None else None,
    )


def list_account_identities(
    conn: sqlite3.Connection,
    *,
    account_id: int,
) -> list[AccountIdentityRecord]:
    """계정에 연결된 모든 외부 신원 목록을 반환한다."""
    rows = conn.execute(
        """
        SELECT iss, sub, account_id, provider_id, linked_at, last_login_at
        FROM account_identities WHERE account_id = ?
        ORDER BY linked_at ASC
        """,
        (account_id,),
    ).fetchall()
    return [
        AccountIdentityRecord(
            iss=str(r["iss"]),
            sub=str(r["sub"]),
            account_id=int(r["account_id"]),
            provider_id=str(r["provider_id"]),
            linked_at=float(r["linked_at"]),
            last_login_at=float(r["last_login_at"]) if r["last_login_at"] is not None else None,
        )
        for r in rows
    ]


def record_identity_login(
    conn: sqlite3.Connection,
    *,
    iss: str,
    sub: str,
    now: float | None = None,
) -> None:
    """외부 신원의 마지막 로그인 시각을 기록한다."""
    ts = time.time() if now is None else now
    with conn:
        conn.execute(
            "UPDATE account_identities SET last_login_at = ? WHERE iss = ? AND sub = ?",
            (ts, iss, sub),
        )


def unlink_account_identity(
    conn: sqlite3.Connection,
    *,
    account_id: int,
    provider_id: str,
    iss: str,
    sub: str,
) -> bool:
    """신원 연결을 해제한다. 비밀번호도 없고 다른 신원도 없으면 IdentityUnlinkForbiddenError를 발생시킨다."""
    has_pw = has_password(conn, account_id)
    identities = list_account_identities(conn, account_id=account_id)
    if not has_pw and len(identities) <= 1:
        raise IdentityUnlinkForbiddenError("Cannot unlink the only authentication credential.")

    with conn:
        cur = conn.execute(
            """
            DELETE FROM account_identities
            WHERE account_id = ? AND provider_id = ? AND iss = ? AND sub = ?
            """,
            (account_id, provider_id, iss, sub),
        )
    return cur.rowcount > 0


@dataclass(frozen=True, slots=True)
class IdpProviderMeta:
    id: str
    type: Literal["oidc", "github"]
    display_name: str
    client_id: str
    enabled: bool = True
    allow_signup: bool = True
    issuer: str = ""
    scopes: tuple[str, ...] = ("openid", "email", "profile")
    hosted_domain: str | None = None
    tenant_id: str | None = None
    secret_configured: bool = False
    client_secret: str | None = None


def load_idp_providers(data_dir: Path | str, *, include_secrets: bool = False) -> list[IdpProviderMeta]:
    """data/auth/idp_providers.json 을 읽고 환경변수 및 비밀 파일의 client secret을 결합한다."""
    path = Path(data_dir) / "auth" / "idp_providers.json"
    if not path.is_file():
        return []
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return []

    providers: list[IdpProviderMeta] = []
    for item in data.get("providers", []):
        pid = str(item.get("id", "")).strip()
        if not pid:
            continue
        env_key = f"CLAIRE_OIDC_{pid.upper().replace('-', '_')}_CLIENT_SECRET"
        secret = os.environ.get(env_key, "").strip() or None
        if not secret:
            secret_file = Path(f"/run/secrets/claire_oidc_{pid.lower().replace('-', '_')}_client_secret")
            if secret_file.is_file():
                try:
                    secret = secret_file.read_text(encoding="utf-8").strip() or None
                except Exception:
                    secret = None

        providers.append(
            IdpProviderMeta(
                id=pid,
                type=item.get("type", "oidc"),
                display_name=item.get("display_name", pid),
                client_id=item.get("client_id", ""),
                enabled=bool(item.get("enabled", True)),
                allow_signup=bool(item.get("allow_signup", True)),
                issuer=item.get("issuer", ""),
                scopes=tuple(item.get("scopes", ["openid", "email", "profile"])),
                hosted_domain=item.get("hosted_domain"),
                tenant_id=item.get("tenant_id"),
                secret_configured=bool(secret),
                client_secret=secret if include_secrets else None,
            )
        )
    return providers


def save_idp_providers(data_dir: Path | str, providers: list[IdpProviderMeta]) -> None:
    """비밀을 제외하고 공급자 설정을 원자적(0600)으로 저장한다."""
    target_path = Path(data_dir) / "auth" / "idp_providers.json"
    _ensure_private_dir(target_path.parent)
    payload = {
        "version": 1,
        "providers": [
            {
                "id": p.id,
                "type": p.type,
                "display_name": p.display_name,
                "client_id": p.client_id,
                "enabled": p.enabled,
                "allow_signup": p.allow_signup,
                "issuer": p.issuer,
                "scopes": list(p.scopes),
                "hosted_domain": p.hosted_domain,
                "tenant_id": p.tenant_id,
            }
            for p in providers
        ],
    }
    tmp_path = target_path.with_suffix(".tmp")
    tmp_fd = os.open(str(tmp_path), os.O_CREAT | os.O_WRONLY | os.O_TRUNC, 0o600)
    with os.fdopen(tmp_fd, "w", encoding="utf-8") as f:
        json.dump(payload, f, indent=2, ensure_ascii=False)
        f.flush()
        os.fsync(f.fileno())
    os.replace(tmp_path, target_path)
