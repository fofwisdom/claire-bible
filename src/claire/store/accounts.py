"""계정 저장소와 자격증명의 계정 귀속 (설계 §8.2–§8.4, §13.1).

``accounts``, ``username_tombstones``, ``instance_settings`` 테이블과 ``auth_sessions``·``oauth_tokens``의 ``account_id``·``authenticated_at`` 열을 다룬다. 스키마와 v15 이관은 `store.db`에 있다.

``AccountStore``는 호출자가 연 연결을 감싼다. 연결의 수명은 호출자가 정하고, 변경 메서드는 끝에서 커밋한다(`store.db`의 기존 함수와 같은 관례).
"""

from __future__ import annotations

import hashlib
import secrets
import sqlite3
import time
from dataclasses import dataclass
from typing import Literal

from ..accounts.username_policy import UsernameRejected
from . import db as dbm

__all__ = [
    "ADMIN_ACCOUNT_ID",
    "Account",
    "AccountNotFound",
    "AccountRole",
    "AccountStateError",
    "AccountStatus",
    "AccountStore",
    "CredentialBinding",
    "INSTANCE_MODES",
    "InstanceMode",
    "UsernameRejected",
    "create_account_session",
    "delete_session",
    "mark_session_authenticated",
    "revoke_other_sessions",
    "validate_oauth_binding",
    "validate_session_binding",
]

AccountRole = Literal["admin", "member"]
AccountStatus = Literal["setup_required", "active", "suspended", "deleting", "purged"]
# 인스턴스 모드(§4). ``instance_settings.mode``에 둔다. 행이 없으면 싱글 모드다(P2 이전 설치).
InstanceMode = Literal["single", "network"]
INSTANCE_MODES: frozenset[str] = frozenset({"single", "network"})

# v15 이관이 만드는 관리자 행. v15 이전 자격증명과 테마는 모두 이 계정의 것이다(§8.3, §8.4).
# 값(1)의 정의는 v15 이관과 자격증명 쓰기 함수가 있는 `store.db`에 하나만 둔다.
ADMIN_ACCOUNT_ID = dbm.ADMIN_ACCOUNT_ID

# 세션 자격증명 범위. create_account_session은 이 밖의 값을 거부한다(C1).
SESSION_SCOPES: frozenset[str] = frozenset({"owner", "collaborator", "readonly"})

_ACCOUNT_COLUMNS = (
    "id, public_id, username, role, status, display_name, profile_public, "
    "created_at, activated_at, delete_after, updated_at"
)

# 자격증명이 통하는 계정 상태. 그 밖의 상태(suspended 등)는 자격증명을 거부한다(§8.2).
CREDENTIAL_ACCEPTING_STATUSES: frozenset[str] = frozenset({"setup_required", "active"})


class AccountNotFound(LookupError):
    """계정이 없다."""


class AccountStateError(RuntimeError):
    """현재 상태에서 허용되지 않는 전이다. 예: ``active`` 계정에 ``complete_setup``."""


@dataclass(frozen=True, slots=True)
class Account:
    id: int
    public_id: int
    username: str | None
    role: AccountRole
    status: AccountStatus
    display_name: str
    profile_public: bool
    created_at: float
    activated_at: float | None
    delete_after: float | None
    updated_at: float


@dataclass(frozen=True, slots=True)
class CredentialBinding:
    """세션·OAuth 토큰을 검증한 결과. 토큰 값은 담지 않는다."""

    scope: str
    account_id: int
    # 마지막으로 사람이 인증한 시각(로그인, /setup, 부트스트랩 링크 교환). 단계 인증(§8.5)에 쓴다. OAuth는 None.
    authenticated_at: float | None
    # 감사용 식별자. 세션은 ``nonce``의 SHA-256 앞 16자 같은 비가역 값이며 토큰 값이 아니다.
    credential_id: str


def _row_to_account(row: sqlite3.Row | tuple) -> Account:
    (
        account_id, public_id, username, role, status, display_name, profile_public,
        created_at, activated_at, delete_after, updated_at,
    ) = tuple(row)
    return Account(
        id=int(account_id),
        public_id=int(public_id),
        username=None if username is None else str(username),
        role=role,
        status=status,
        display_name=str(display_name),
        profile_public=bool(profile_public),
        created_at=float(created_at),
        activated_at=None if activated_at is None else float(activated_at),
        delete_after=None if delete_after is None else float(delete_after),
        updated_at=float(updated_at),
    )


def _credential_id(value: str) -> str:
    """감사용 비가역 식별자: SHA-256 hex 앞 16자. 토큰 값 자체는 남기지 않는다."""
    return hashlib.sha256(value.encode("utf-8")).hexdigest()[:16]


class AccountStore:
    def __init__(self, conn: sqlite3.Connection) -> None:
        self.conn = conn

    # --- 조회 -------------------------------------------------------------
    def get(self, account_id: int) -> Account:
        """없으면 ``AccountNotFound``."""
        row = self.conn.execute(
            f"SELECT {_ACCOUNT_COLUMNS} FROM accounts WHERE id=?", (account_id,)
        ).fetchone()
        if row is None:
            raise AccountNotFound(account_id)
        return _row_to_account(row)

    def find_by_username(self, username: str) -> Account | None:
        """정규화한 이름으로 정확히 일치하는 계정. 부분 일치는 하지 않는다."""
        row = self.conn.execute(
            f"SELECT {_ACCOUNT_COLUMNS} FROM accounts WHERE username=?", (username,)
        ).fetchone()
        return None if row is None else _row_to_account(row)

    def admin(self) -> Account:
        """관리자 계정(``role='admin'``). 없으면 ``AccountNotFound``(이관이 안 된 DB)."""
        row = self.conn.execute(
            f"SELECT {_ACCOUNT_COLUMNS} FROM accounts WHERE role='admin'"
        ).fetchone()
        if row is None:
            raise AccountNotFound("admin")
        return _row_to_account(row)

    def list_accounts(self) -> list[Account]:
        """``id`` 오름차순."""
        rows = self.conn.execute(f"SELECT {_ACCOUNT_COLUMNS} FROM accounts ORDER BY id").fetchall()
        return [_row_to_account(r) for r in rows]

    def is_username_retired(self, username: str) -> bool:
        """``username_tombstones``에 있고 ``released_at``이 NULL이면 True."""
        row = self.conn.execute(
            "SELECT 1 FROM username_tombstones WHERE username=? AND released_at IS NULL", (username,)
        ).fetchone()
        return row is not None

    # --- 인스턴스 설정 ------------------------------------------------------
    def get_setting(self, key: str) -> str | None:
        row = self.conn.execute("SELECT value FROM instance_settings WHERE key=?", (key,)).fetchone()
        return None if row is None else str(row[0])

    def set_setting(self, key: str, value: str, *, updated_by: int | None) -> None:
        self.conn.execute(
            "INSERT INTO instance_settings (key, value, updated_at, updated_by) VALUES (?, ?, ?, ?) "
            "ON CONFLICT(key) DO UPDATE SET value=excluded.value, updated_at=excluded.updated_at, "
            "updated_by=excluded.updated_by",
            (key, value, time.time(), updated_by),
        )
        self.conn.commit()

    def instance_mode(self) -> InstanceMode:
        """``instance_settings.mode``. 행이 없으면 ``"single"``. 알 수 없는 값이면 ``RuntimeError``(C1)."""
        raw = self.get_setting("mode")
        if raw is None:
            return "single"
        if raw not in INSTANCE_MODES:
            raise RuntimeError(f"instance_settings.mode has an unknown value: {raw!r}")
        return raw  # type: ignore[return-value]  # INSTANCE_MODES가 걸렀다

    def set_instance_mode(self, mode: InstanceMode, *, updated_by: int) -> None:
        """모드를 기록한다. 전환 조건(§4.2 준비 검사·멤버 없음)은 호출자(``accounts_routes``)가 먼저 판정한다."""
        raise NotImplementedError  # P2 L2-f

    def id_salt(self) -> bytes:
        """``instance_settings.id_salt``(hex). 없으면 ``RuntimeError``(이관 누락, C1)."""
        raw = self.get_setting("id_salt")
        if raw is None:
            raise RuntimeError("instance_settings.id_salt is missing (schema v15 migration not applied)")
        try:
            salt = bytes.fromhex(raw)
        except ValueError as exc:
            raise RuntimeError("instance_settings.id_salt is not valid hex") from exc
        if not salt:
            raise RuntimeError("instance_settings.id_salt is empty")
        return salt

    # --- 변경 -------------------------------------------------------------
    def complete_setup(
        self,
        account_id: int,
        *,
        username: str,
        display_name: str,
        now: float | None = None,
    ) -> Account:
        """최초 관리자 설정(§8.4-5). ``status='setup_required'``인 관리자만 ``active``로 바꾼다.

        ``username``은 `accounts.username_policy.validate_username`을 이미 통과한 값이어야 한다. 퇴역 이름이면 ``UsernameRejected("retired")``, 다른 계정이 쓰면 ``UsernameRejected("taken")``. 상태가 맞지 않으면 ``AccountStateError``. 한 번의 조건부 UPDATE로 처리해 동시 요청 중 하나만 성공한다.
        """
        when = time.time() if now is None else now
        current = self.get(account_id)
        self._require_setup_pending(current)
        if self.is_username_retired(username):
            raise UsernameRejected("retired")
        taken = self.find_by_username(username)
        if taken is not None and taken.id != account_id:
            raise UsernameRejected("taken")
        try:
            # 상태·역할·이름 미설정·퇴역 아님을 모두 WHERE에 넣는다. 동시 요청은 SQLite 쓰기 직렬화로 하나만 rowcount=1을 얻는다.
            cur = self.conn.execute(
                "UPDATE accounts SET username=?, display_name=?, status='active', activated_at=?, updated_at=? "
                "WHERE id=? AND role='admin' AND status='setup_required' AND username IS NULL "
                "AND NOT EXISTS (SELECT 1 FROM username_tombstones WHERE username=? AND released_at IS NULL)",
                (username, display_name, when, when, account_id, username),
            )
        except sqlite3.IntegrityError as exc:
            self.conn.rollback()
            if str(exc) == "UNIQUE constraint failed: accounts.username":
                raise UsernameRejected("taken") from exc
            raise
        if cur.rowcount != 1:
            self.conn.rollback()
            # 검사와 UPDATE 사이에 다른 요청이 먼저 바꿨다. 무엇이 바뀌었는지 다시 읽어 사유를 정한다.
            self._require_setup_pending(self.get(account_id))
            if self.is_username_retired(username):
                raise UsernameRejected("retired")
            raise AccountStateError(f"account {account_id}: setup could not be completed")
        self.conn.commit()
        return self.get(account_id)

    @staticmethod
    def _require_setup_pending(account: Account) -> None:
        if account.role != "admin" or account.status != "setup_required" or account.username is not None:
            raise AccountStateError(
                f"account {account.id}: complete_setup needs an admin in setup_required "
                f"(role={account.role}, status={account.status})"
            )

    def update_display_name(self, account_id: int, display_name: str, *, now: float | None = None) -> Account:
        when = time.time() if now is None else now
        cur = self.conn.execute(
            "UPDATE accounts SET display_name=?, updated_at=? WHERE id=?",
            (display_name, when, account_id),
        )
        if cur.rowcount != 1:
            self.conn.rollback()
            raise AccountNotFound(account_id)
        self.conn.commit()
        return self.get(account_id)


# --- 자격증명 귀속 (auth_sessions, oauth_tokens) -------------------------------


def create_account_session(
    conn: sqlite3.Connection,
    *,
    account_id: int,
    scope: str = "owner",
    replace_token: str | None = None,
    now: float | None = None,
) -> str:
    """사람이 방금 인증한 세션을 새로 발급한다(로그인, /setup 확정). ``authenticated_at=now``.

    ``replace_token``이 있으면 그 세션을 지운다(세션 고정 방지, §8.4-5). 기존 ``create_session``과 달리 같은 scope의 **다른 계정** 세션은 건드리지 않는다.
    """
    if scope not in SESSION_SCOPES:
        raise ValueError(f"unknown session scope: {scope!r}")
    if conn.execute("SELECT 1 FROM accounts WHERE id=?", (account_id,)).fetchone() is None:
        raise AccountNotFound(account_id)
    when = time.time() if now is None else now
    token = secrets.token_urlsafe(32)
    try:
        if replace_token:
            conn.execute("DELETE FROM auth_sessions WHERE session_token=?", (replace_token,))
        conn.execute(
            "INSERT INTO auth_sessions "
            "(nonce, session_token, approved, created_at, expires_at, scope, account_id, authenticated_at) "
            "VALUES (?, ?, 1, ?, ?, ?, ?, ?)",
            (token, token, when, when + dbm.SESSION_TTL, scope, account_id, when),
        )
    except BaseException:
        conn.rollback()
        raise
    conn.commit()
    return token


def validate_session_binding(
    conn: sqlite3.Connection,
    token: str,
    *,
    scopes: tuple[str, ...] = ("owner", "collaborator", "readonly"),
) -> CredentialBinding | None:
    """`store.db.validate_session_scope`와 같은 검증·슬라이딩 만료에 계정 귀속을 더한다.

    ``account_id``가 NULL인 행은 거부한다(v15 이관이 모두 채우므로 NULL은 결함이다, C1). 계정 상태는 여기서 보지 않는다. 호출자(`api.security`)가 ``AccountStore.get``으로 확인한다.
    """
    if not dbm.plausible_session_token(token) or not scopes:
        return None
    ph = ",".join("?" for _ in scopes)
    row = conn.execute(
        f"SELECT nonce, expires_at, scope, account_id, authenticated_at FROM auth_sessions "
        f"WHERE session_token=? AND approved=1 AND scope IN ({ph})",
        (token, *scopes),
    ).fetchone()
    now = time.time()
    if row is None or row["expires_at"] is None or row["expires_at"] < now:
        return None
    if row["account_id"] is None:
        return None
    ttl = dbm.SESSION_TTL
    if row["expires_at"] < now + (ttl / 2):
        conn.execute("UPDATE auth_sessions SET expires_at=? WHERE session_token=?", (now + ttl, token))
        conn.commit()
    authenticated_at = row["authenticated_at"]
    return CredentialBinding(
        scope=str(row["scope"]),
        account_id=int(row["account_id"]),
        authenticated_at=None if authenticated_at is None else float(authenticated_at),
        # nonce는 세션 행의 고정 키다. 교환·회전으로 session_token이 바뀌어도 같은 식별자가 남는다.
        credential_id=_credential_id(str(row["nonce"])),
    )


def validate_oauth_binding(conn: sqlite3.Connection, token: str) -> CredentialBinding | None:
    """`store.db.validate_oauth_token`에 계정 귀속을 더한다. ``authenticated_at``은 None."""
    if not token or len(token) < 20:
        return None
    row = conn.execute(
        "SELECT scope, account_id FROM oauth_tokens WHERE token=? AND expires_at>=?",
        (token, time.time()),
    ).fetchone()
    if row is None or row["account_id"] is None:
        return None
    return CredentialBinding(
        scope=str(row["scope"]),
        account_id=int(row["account_id"]),
        authenticated_at=None,
        credential_id=_credential_id(token),
    )


def mark_session_authenticated(conn: sqlite3.Connection, token: str, *, now: float | None = None) -> bool:
    """단계 인증(§8.5)을 다시 통과한 세션의 ``authenticated_at``을 갱신한다. 세션이 없으면 False."""
    if not dbm.plausible_session_token(token):
        return False
    when = time.time() if now is None else now
    cur = conn.execute(
        "UPDATE auth_sessions SET authenticated_at=? "
        "WHERE session_token=? AND approved=1 AND expires_at>=? AND account_id IS NOT NULL",
        (when, token, when),
    )
    conn.commit()
    return cur.rowcount == 1


def delete_session(conn: sqlite3.Connection, token: str) -> int:
    """세션 행 하나를 지운다(로그아웃). 형식이 맞지 않는 값은 DB에 묻지 않는다."""
    if not dbm.plausible_session_token(token):
        return 0
    cur = conn.execute("DELETE FROM auth_sessions WHERE session_token=?", (token,))
    conn.commit()
    return int(cur.rowcount)


def revoke_other_sessions(conn: sqlite3.Connection, account_id: int, *, keep_token: str | None) -> int:
    """그 계정의 승인된 세션(부트스트랩 링크 포함) 중 ``keep_token``을 뺀 나머지를 지운다. OAuth·API 토큰은 건드리지 않는다."""
    cur = conn.execute(
        "DELETE FROM auth_sessions WHERE account_id=? AND approved=1 AND session_token IS NOT ?",
        (account_id, keep_token),
    )
    conn.commit()
    return int(cur.rowcount)
