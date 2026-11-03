"""시간 정렬 64비트 번호 생성기 (설계 §7.2).

``number = (unix_ms << 16) | low16`` 이고, ``low16 = (HMAC-SHA256(id_salt, "<table>:<unix_ms>")[0:2] + seq) & 0xFFFF`` 이다. 테마 번호, ``accounts.public_id``, ``invitations.public_id``가 모두 이 규칙을 쓴다. JSON에서는 10진 문자열로 주고받는다(§7.1 c).

이 모듈은 DB 테이블을 알지 않는다. ``id_salt``는 호출자가 ``instance_settings``에서 읽어 넘긴다(`store.accounts.AccountStore.id_salt`).
"""

from __future__ import annotations

import hashlib
import hmac
import secrets
import sqlite3
import time
from collections.abc import Callable

ID_SALT_BYTES = 32
# 같은 밀리초 충돌 시 seq를 올려 다시 넣는 최대 횟수. 넘으면 실패한다(C1).
MAX_INSERT_ATTEMPTS = 8
# 현재 시각으로 만든 번호는 이 값보다 크다. 내부 ID(< 2^31)와 범위가 겹치지 않는다(§7.3).
MIN_CURRENT_NUMBER = 1 << 56
# 내부 테마 ID(``themes.id``)의 상한(배타). 이보다 작은 음이 아닌 정수는 내부 ID로 해석한다(§7.3).
INTERNAL_ID_LIMIT = 1 << 31
# SQLite INTEGER(부호 있는 64비트)에 들어가는 가장 큰 번호.
MAX_NUMBER = (1 << 63) - 1
_LOW_BITS = 16
_LOW_MASK = (1 << _LOW_BITS) - 1
# 상위 비트(밀리초)가 이 값을 넘으면 번호가 부호 있는 64비트를 넘는다.
_MAX_UNIX_MS = MAX_NUMBER >> _LOW_BITS


class TimestampIdExhausted(RuntimeError):
    """같은 밀리초에서 ``MAX_INSERT_ATTEMPTS``번 모두 UNIQUE 충돌이 났다."""


def generate_id_salt() -> bytes:
    """v15 이관이 ``instance_settings.id_salt``에 저장할 32바이트 무작위 값."""
    return secrets.token_bytes(ID_SALT_BYTES)


def _check_inputs(table: str, salt: bytes, unix_ms: int) -> None:
    if not table:
        raise ValueError("table is required")
    if not isinstance(salt, (bytes, bytearray)) or not salt:
        raise ValueError("salt must be non-empty bytes")
    if isinstance(unix_ms, bool) or not isinstance(unix_ms, int):
        raise TypeError("unix_ms must be int")
    if not 0 <= unix_ms <= _MAX_UNIX_MS:
        raise ValueError(f"unix_ms out of range: {unix_ms}")


def _hmac_low16(salt: bytes, message: str) -> int:
    digest = hmac.new(bytes(salt), message.encode("utf-8"), hashlib.sha256).digest()
    return int.from_bytes(digest[0:2], "big")


def timestamp_id(table: str, *, salt: bytes, unix_ms: int, seq: int = 0) -> int:
    """``table``과 밀리초, 시퀀스로 번호 하나를 계산한다. 같은 입력이면 같은 값이다."""
    _check_inputs(table, salt, unix_ms)
    if isinstance(seq, bool) or not isinstance(seq, int) or seq < 0:
        raise ValueError(f"seq must be a non-negative int: {seq!r}")
    low16 = (_hmac_low16(salt, f"{table}:{unix_ms}") + seq) & _LOW_MASK
    return (unix_ms << _LOW_BITS) | low16


def legacy_timestamp_id(table: str, *, salt: bytes, unix_ms: int, row_id: int) -> int:
    """이관용 결정론적 번호. 하위 16비트는 ``HMAC(salt, "<table>:<row_id>")[0:2]``다(A3)."""
    _check_inputs(table, salt, unix_ms)
    if isinstance(row_id, bool) or not isinstance(row_id, int):
        raise TypeError("row_id must be int")
    return (unix_ms << _LOW_BITS) | _hmac_low16(salt, f"{table}:{row_id}")


def insert_with_timestamp_id(
    conn: sqlite3.Connection,
    *,
    table: str,
    column: str,
    salt: bytes,
    insert: Callable[[int], None],
    now_ms: Callable[[], int] | None = None,
) -> int:
    """번호를 만들어 ``insert(number)``를 호출하고, 그 번호를 반환한다.

    ``insert``가 ``table.column``의 UNIQUE 제약으로 ``sqlite3.IntegrityError``를 내면 seq를 올려 다시 시도한다. 다른 제약 위반은 그대로 올린다. ``MAX_INSERT_ATTEMPTS``번 모두 실패하면 ``TimestampIdExhausted``. 트랜잭션 경계는 호출자가 정한다.
    """
    # conn은 계약상 받기만 한다. 트랜잭션 경계와 실제 INSERT는 호출자(insert) 몫이다.
    clock = now_ms or (lambda: time.time_ns() // 1_000_000)
    unix_ms = clock()
    collision = f"UNIQUE constraint failed: {table}.{column}"
    for seq in range(MAX_INSERT_ATTEMPTS):
        number = timestamp_id(table, salt=salt, unix_ms=unix_ms, seq=seq)
        try:
            insert(number)
        except sqlite3.IntegrityError as exc:
            if str(exc) != collision:
                raise
            continue
        return number
    raise TimestampIdExhausted(
        f"{table}.{column}: {MAX_INSERT_ATTEMPTS} collisions at unix_ms={unix_ms}"
    )


def format_public_id(number: int) -> str:
    """JSON·경로 표기용 10진 문자열."""
    if isinstance(number, bool) or not isinstance(number, int):
        raise TypeError("number must be int")
    if not 1 <= number <= MAX_NUMBER:
        raise ValueError(f"number out of range: {number}")
    return str(number)


def parse_public_id(raw: str | int) -> int:
    """10진 문자열 또는 정수를 번호로 바꾼다. 부호·공백·선행 0·범위 밖이면 ``ValueError``."""
    if isinstance(raw, bool):
        raise ValueError("bool is not a number")
    if isinstance(raw, int):
        value = raw
    elif isinstance(raw, str):
        # str.isdigit()은 유니코드 숫자도 받으므로 ASCII로 직접 검사한다.
        if not raw or not all("0" <= ch <= "9" for ch in raw) or raw[0] == "0":
            raise ValueError(f"invalid number: {raw!r}")
        if len(raw) > len(str(MAX_NUMBER)):
            raise ValueError(f"number out of range: {raw!r}")
        value = int(raw)
    else:
        raise ValueError(f"invalid number type: {type(raw).__name__}")
    if not 1 <= value <= MAX_NUMBER:
        raise ValueError(f"number out of range: {raw!r}")
    return value


def parse_internal_id_or_number(raw: str | int) -> tuple[bool, int]:
    """호환 입력을 ``(내부 ID 여부, 값)``으로 바꾼다(§7.3).

    ``0 <= 값 < INTERNAL_ID_LIMIT``이면 내부 ID다. ``parse_public_id``는 ``"0"``을 거부하므로 그보다 먼저 판정한다(Rev. 2.3).
    그 밖의 값은 ``parse_public_id``로 검증한 번호다. 문자열은 ASCII 10진 정규형(부호·공백·선행 0 없음, ``"0"``만 예외)이어야 한다.
    숫자가 아니거나 범위 밖이면 ``ValueError``.
    """
    if isinstance(raw, bool):
        raise ValueError("bool is not a number")
    if isinstance(raw, int):
        value = raw
    elif isinstance(raw, str):
        if not raw or not all("0" <= ch <= "9" for ch in raw) or (raw[0] == "0" and raw != "0"):
            raise ValueError(f"invalid number: {raw!r}")
        if len(raw) > len(str(MAX_NUMBER)):
            raise ValueError(f"number out of range: {raw!r}")
        value = int(raw)
    else:
        raise ValueError(f"invalid number type: {type(raw).__name__}")
    if 0 <= value < INTERNAL_ID_LIMIT:
        return True, value
    return False, parse_public_id(value)
