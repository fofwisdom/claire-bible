"""계정별 텔레그램 봇 저장소 (auth.db v4 ``account_telegram_bots``).

- 계정마다 봇 하나를 둔다(``account_id`` 기본 키). 같은 봇(``bot_id``)을 두 계정에 등록할 수 없다(``UNIQUE``).
- 봇 토큰은 쓰기 전용 비밀이다. ``load_enabled_bots``만 평문을 돌려준다(봇 러너 전용). 그 밖의 함수와 API는 메타데이터만 다룬다.
- AAD는 ``telegram|account_id|key_id``이므로 암호문을 다른 행으로 옮기면 복호화가 실패한다.
- 허용 사용자 목록(텔레그램 user ID)은 비어 있을 수 없다. 비어 있으면 아무나 그 계정으로 적재할 수 있기 때문이다.
"""

from __future__ import annotations

import re
import secrets
import sqlite3
import time
from dataclasses import dataclass
from typing import TYPE_CHECKING, Final, Iterable, Literal

from claire.secret_scrub import register_secret

if TYPE_CHECKING:
    from claire.store.secrets import SecretService

BotStatus = Literal["pending", "running", "error", "disabled"]
BOT_STATUSES: Final[tuple[str, ...]] = ("pending", "running", "error", "disabled")
FINGERPRINT_PURPOSE: Final[str] = "telegram_bot_token_fingerprint"
MAX_ALLOWED_USERS: Final[int] = 20
LAST_ERROR_MAX_LENGTH: Final[int] = 300
# BotFather 토큰: "<봇 숫자 ID>:<35자 안팎의 비밀>".
_TOKEN_RE: Final[re.Pattern[str]] = re.compile(r"^(\d{3,20}):([A-Za-z0-9_-]{30,64})$")


class TelegramBotError(RuntimeError):
    """봇 저장·검증 실패. 메시지에 토큰 원문을 넣지 않는다."""


class TelegramBotConflict(TelegramBotError):
    """같은 봇이 이미 다른 계정 또는 인스턴스 봇(``TELEGRAM_BOT_TOKEN``)으로 등록되어 있다."""


@dataclass(frozen=True, slots=True)
class TelegramBotMeta:
    """API 응답 필드. 토큰의 어떤 문자도 담지 않는다."""

    account_id: int
    bot_id: int
    bot_username: str
    fingerprint: str
    allowed_user_ids: tuple[int, ...]
    enabled: bool
    status: str
    last_error: str | None
    status_at: float | None
    created_at: float
    updated_at: float

    def to_dict(self) -> dict[str, object]:
        return {
            "bot_id": self.bot_id,
            "bot_username": self.bot_username,
            "fingerprint": self.fingerprint,
            "allowed_user_ids": list(self.allowed_user_ids),
            "enabled": self.enabled,
            "status": self.status,
            "last_error": self.last_error,
            "status_at": self.status_at,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
        }


@dataclass(frozen=True, slots=True)
class TelegramBotProfile:
    """봇 러너가 실행할 봇 하나. ``token``은 평문이다."""

    account_id: int
    bot_id: int
    token: str
    allowed_user_ids: tuple[int, ...]
    fingerprint: str
    source: Literal["account", "instance"] = "account"

    def __repr__(self) -> str:  # 로그·예외에 원문이 찍히지 않게
        return (
            f"TelegramBotProfile(account_id={self.account_id!r}, bot_id={self.bot_id!r}, "
            f"source={self.source!r}, fingerprint={self.fingerprint!r})"
        )

    @property
    def signature(self) -> tuple[object, ...]:
        """설정이 바뀌었는지 판정하는 값. 러너는 값이 바뀌면 봇을 다시 시작한다."""
        return (self.account_id, self.bot_id, self.fingerprint, self.allowed_user_ids, self.source)


def parse_bot_token(value: object) -> tuple[int, str] | None:
    """토큰 형식 검사. 맞으면 ``(bot_id, token)``, 아니면 None. 앞뒤 공백만 걷는다."""
    if not isinstance(value, str):
        return None
    token = value.strip()
    match = _TOKEN_RE.match(token)
    if match is None:
        return None
    return int(match.group(1)), token


def bot_id_from_token(token: str) -> int | None:
    parsed = parse_bot_token(token)
    return None if parsed is None else parsed[0]


def parse_allowed_user_ids(value: object) -> tuple[int, ...] | None:
    """허용 사용자 목록. 정수 리스트 또는 쉼표·공백 구분 문자열을 받는다. 1개 이상 ``MAX_ALLOWED_USERS`` 이하가 아니면 None."""
    items: list[object]
    if isinstance(value, str):
        items = [part for part in re.split(r"[\s,]+", value.strip()) if part]
    elif isinstance(value, (list, tuple)):
        items = list(value)
    else:
        return None
    out: list[int] = []
    for item in items:
        if isinstance(item, bool):
            return None
        if isinstance(item, int):
            number = item
        elif isinstance(item, str) and item.strip().isdigit():
            number = int(item.strip())
        else:
            return None
        if number <= 0 or number >= 1 << 63:
            return None
        if number not in out:
            out.append(number)
    if not out or len(out) > MAX_ALLOWED_USERS:
        return None
    return tuple(out)


def _encode_allowed(ids: Iterable[int]) -> str:
    return ",".join(str(int(i)) for i in ids)


def _decode_allowed(raw: str | None) -> tuple[int, ...]:
    if not raw:
        return ()
    return tuple(int(part) for part in raw.split(",") if part.strip().isdigit())


def token_aad(account_id: int, key_id: str) -> bytes:
    return f"telegram|{account_id}|{key_id}".encode("utf-8")


def _service(secret_service: "SecretService | None") -> "SecretService":
    if secret_service is not None:
        return secret_service
    from claire.store.secrets import get_secret_service

    return get_secret_service()


def token_fingerprint(token: str, *, secret_service: "SecretService | None" = None) -> str:
    return _service(secret_service).compute_fingerprint(token, purpose=FINGERPRINT_PURPOSE)[:8]


_META_COLUMNS: Final[str] = (
    "account_id, bot_id, bot_username, fingerprint, allowed_user_ids, enabled, status, last_error, "
    "status_at, created_at, updated_at"
)


def _meta_from_row(row: sqlite3.Row | tuple) -> TelegramBotMeta:
    (account_id, bot_id, bot_username, fingerprint, allowed, enabled, status, last_error, status_at, created_at, updated_at) = tuple(row)
    return TelegramBotMeta(
        account_id=int(account_id),
        bot_id=int(bot_id),
        bot_username=str(bot_username or ""),
        fingerprint=str(fingerprint),
        allowed_user_ids=_decode_allowed(allowed),
        enabled=bool(enabled),
        status=str(status),
        last_error=None if last_error is None else str(last_error),
        status_at=None if status_at is None else float(status_at),
        created_at=float(created_at),
        updated_at=float(updated_at),
    )


def get_meta(conn: sqlite3.Connection, *, account_id: int) -> TelegramBotMeta | None:
    row = conn.execute(
        f"SELECT {_META_COLUMNS} FROM account_telegram_bots WHERE account_id=?", (account_id,)
    ).fetchone()
    return None if row is None else _meta_from_row(row)


def list_meta(conn: sqlite3.Connection) -> list[TelegramBotMeta]:
    rows = conn.execute(f"SELECT {_META_COLUMNS} FROM account_telegram_bots ORDER BY account_id").fetchall()
    return [_meta_from_row(r) for r in rows]


def put_bot(
    conn: sqlite3.Connection,
    *,
    account_id: int,
    token: str,
    bot_username: str,
    allowed_user_ids: Iterable[int],
    reserved_bot_ids: Iterable[int] = (),
    secret_service: "SecretService | None" = None,
    now: float | None = None,
) -> TelegramBotMeta:
    """등록·교체. 교체하면 새 ``key_id``와 nonce를 쓰고 상태를 ``pending``으로 되돌린다.

    ``reserved_bot_ids``(인스턴스 봇 ID)와 같거나 다른 계정이 이미 등록한 봇이면 ``TelegramBotConflict``.
    """
    parsed = parse_bot_token(token)
    if parsed is None:
        raise TelegramBotError("invalid bot token format")
    bot_id, token = parsed
    register_secret(token)
    allowed = tuple(int(i) for i in allowed_user_ids)
    if not allowed:
        raise TelegramBotError("allowed_user_ids must not be empty")
    if bot_id in set(int(i) for i in reserved_bot_ids):
        raise TelegramBotConflict("bot is already used as the instance bot")
    sec = _service(secret_service)
    ts = time.time() if now is None else now
    key_id = secrets.token_hex(16)
    encrypted = sec.encrypt(token, token_aad(account_id, key_id))
    fingerprint = sec.compute_fingerprint(token, purpose=FINGERPRINT_PURPOSE)[:8]
    with conn:
        other = conn.execute(
            "SELECT account_id FROM account_telegram_bots WHERE bot_id=? AND account_id<>?", (bot_id, account_id)
        ).fetchone()
        if other is not None:
            raise TelegramBotConflict("bot is already registered by another account")
        previous = conn.execute(
            "SELECT created_at, enabled FROM account_telegram_bots WHERE account_id=?", (account_id,)
        ).fetchone()
        created_at = float(previous[0]) if previous is not None else ts
        conn.execute("DELETE FROM account_telegram_bots WHERE account_id=?", (account_id,))
        conn.execute(
            "INSERT INTO account_telegram_bots (account_id, key_id, ciphertext, nonce, key_version, fingerprint, bot_id, "
            "bot_username, allowed_user_ids, enabled, status, last_error, status_at, created_at, updated_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 1, 'pending', NULL, ?, ?, ?)",
            (
                account_id, key_id, encrypted.ciphertext, encrypted.nonce, encrypted.key_version, fingerprint, bot_id,
                str(bot_username or "")[:64], _encode_allowed(allowed), ts, created_at, ts,
            ),
        )
    meta = get_meta(conn, account_id=account_id)
    assert meta is not None
    return meta


def update_settings(
    conn: sqlite3.Connection,
    *,
    account_id: int,
    enabled: bool | None = None,
    allowed_user_ids: Iterable[int] | None = None,
    now: float | None = None,
) -> TelegramBotMeta | None:
    """켜기·끄기와 허용 사용자 목록 변경. 봇이 없으면 None."""
    ts = time.time() if now is None else now
    sets: list[str] = []
    params: list[object] = []
    if enabled is not None:
        sets.append("enabled=?")
        params.append(1 if enabled else 0)
        sets.append("status=?")
        params.append("pending" if enabled else "disabled")
        sets.append("status_at=?")
        params.append(ts)
        sets.append("last_error=NULL")
    if allowed_user_ids is not None:
        allowed = tuple(int(i) for i in allowed_user_ids)
        if not allowed:
            raise TelegramBotError("allowed_user_ids must not be empty")
        sets.append("allowed_user_ids=?")
        params.append(_encode_allowed(allowed))
    sets.append("updated_at=?")
    params.append(ts)
    with conn:
        cur = conn.execute(
            f"UPDATE account_telegram_bots SET {', '.join(sets)} WHERE account_id=?", (*params, account_id)
        )
    if not cur.rowcount:
        return None
    return get_meta(conn, account_id=account_id)


def set_status(
    conn: sqlite3.Connection,
    *,
    account_id: int,
    status: BotStatus,
    error: str | None = None,
    fingerprint: str | None = None,
    now: float | None = None,
) -> None:
    """러너가 실행 상태를 기록한다. ``fingerprint``를 주면 그 토큰일 때만 바꾼다(교체 경합 방지)."""
    if status not in BOT_STATUSES:
        raise ValueError("invalid bot status")
    ts = time.time() if now is None else now
    message = None if error is None else str(error)[:LAST_ERROR_MAX_LENGTH]
    with conn:
        if fingerprint is None:
            conn.execute(
                "UPDATE account_telegram_bots SET status=?, last_error=?, status_at=? WHERE account_id=?",
                (status, message, ts, account_id),
            )
        else:
            conn.execute(
                "UPDATE account_telegram_bots SET status=?, last_error=?, status_at=? WHERE account_id=? AND fingerprint=?",
                (status, message, ts, account_id, fingerprint),
            )


def delete_bot(conn: sqlite3.Connection, *, account_id: int) -> int:
    with conn:
        cur = conn.execute("DELETE FROM account_telegram_bots WHERE account_id=?", (account_id,))
    return int(cur.rowcount or 0)


def load_enabled_bots(
    conn: sqlite3.Connection,
    *,
    account_ids: Iterable[int] | None = None,
    secret_service: "SecretService | None" = None,
) -> tuple[list[TelegramBotProfile], dict[int, str]]:
    """켜진 봇을 복호화한다. ``(프로필 목록, {account_id: 오류 문구})``. 복호화 실패는 오류로 모으고 계속한다.

    ``account_ids``를 주면 그 계정만 읽는다(호출자가 활성 계정으로 거른다).
    """
    from claire.store.secrets import SecretDecryptionError

    sec = _service(secret_service)
    rows = conn.execute(
        "SELECT account_id, key_id, ciphertext, nonce, key_version, fingerprint, bot_id, allowed_user_ids "
        "FROM account_telegram_bots WHERE enabled=1 ORDER BY account_id"
    ).fetchall()
    wanted = None if account_ids is None else {int(i) for i in account_ids}
    profiles: list[TelegramBotProfile] = []
    errors: dict[int, str] = {}
    for row in rows:
        account_id, key_id, ciphertext, nonce, key_version, fingerprint, bot_id, allowed = tuple(row)
        account_id = int(account_id)
        if wanted is not None and account_id not in wanted:
            continue
        try:
            raw = sec.decrypt(bytes(ciphertext), bytes(nonce), token_aad(account_id, str(key_id)), int(key_version))
            token = raw.decode("utf-8")
        except (SecretDecryptionError, UnicodeDecodeError):
            errors[account_id] = "stored bot token could not be decrypted"
            continue
        register_secret(token)
        allowed_ids = _decode_allowed(allowed)
        if not allowed_ids:
            errors[account_id] = "allowed_user_ids is empty"
            continue
        profiles.append(
            TelegramBotProfile(
                account_id=account_id,
                bot_id=int(bot_id),
                token=token,
                allowed_user_ids=allowed_ids,
                fingerprint=str(fingerprint),
            )
        )
    return profiles, errors
