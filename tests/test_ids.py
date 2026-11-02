"""시간 정렬 64비트 번호 생성기 (설계 §7.2, §18.6)."""

from __future__ import annotations

import sqlite3

import pytest

from claire import ids

SALT = bytes(range(32))
# 2026-11-02T00:00:00Z
UNIX_MS = 1_793_577_600_000


def _table(conn: sqlite3.Connection) -> None:
    conn.execute("CREATE TABLE items (public_id INTEGER UNIQUE NOT NULL, label TEXT UNIQUE)")


def test_generate_id_salt_is_32_random_bytes() -> None:
    a = ids.generate_id_salt()
    b = ids.generate_id_salt()
    assert len(a) == ids.ID_SALT_BYTES == 32
    assert a != b


def test_number_layout_and_determinism() -> None:
    n = ids.timestamp_id("themes", salt=SALT, unix_ms=UNIX_MS)
    assert n == ids.timestamp_id("themes", salt=SALT, unix_ms=UNIX_MS)
    assert n >> 16 == UNIX_MS
    # 테이블과 salt가 하위 비트를 바꾼다.
    assert ids.timestamp_id("accounts", salt=SALT, unix_ms=UNIX_MS) >> 16 == UNIX_MS
    other_salt = ids.timestamp_id("themes", salt=b"\x01" * 32, unix_ms=UNIX_MS)
    assert other_salt >> 16 == UNIX_MS
    # seq는 하위 16비트에만 더해지고 0xFFFF에서 감싼다.
    low = n & 0xFFFF
    assert ids.timestamp_id("themes", salt=SALT, unix_ms=UNIX_MS, seq=1) & 0xFFFF == (low + 1) & 0xFFFF
    assert ids.timestamp_id("themes", salt=SALT, unix_ms=UNIX_MS, seq=0x10000) == n


def test_monotonic_across_milliseconds() -> None:
    numbers = [ids.timestamp_id("themes", salt=SALT, unix_ms=UNIX_MS + i) for i in range(500)]
    assert numbers == sorted(numbers)
    assert len(set(numbers)) == len(numbers)


def test_current_numbers_exceed_two_to_56() -> None:
    n = ids.timestamp_id("themes", salt=SALT, unix_ms=UNIX_MS)
    assert n > ids.MIN_CURRENT_NUMBER == 1 << 56
    conn = sqlite3.connect(":memory:")
    _table(conn)
    live = ids.insert_with_timestamp_id(
        conn,
        table="items",
        column="public_id",
        salt=SALT,
        insert=lambda num: conn.execute("INSERT INTO items (public_id) VALUES (?)", (num,)),
    )
    assert live > ids.MIN_CURRENT_NUMBER
    # SQLite 부호 있는 64비트 안에 들어간다.
    assert conn.execute("SELECT public_id FROM items").fetchone()[0] == live


def test_legacy_number_is_deterministic_and_per_row() -> None:
    a = ids.legacy_timestamp_id("themes", salt=SALT, unix_ms=UNIX_MS, row_id=7)
    assert a == ids.legacy_timestamp_id("themes", salt=SALT, unix_ms=UNIX_MS, row_id=7)
    assert a >> 16 == UNIX_MS
    b = ids.legacy_timestamp_id("themes", salt=SALT, unix_ms=UNIX_MS, row_id=8)
    assert a != b
    # 하위 비트는 행 번호에서 오고, 같은 밀리초의 timestamp_id와 다른 입력(HMAC 메시지)을 쓴다.
    import hashlib
    import hmac

    expected_low = int.from_bytes(hmac.new(SALT, b"themes:7", hashlib.sha256).digest()[:2], "big")
    assert a & 0xFFFF == expected_low


def test_same_millisecond_collision_retries_with_next_seq() -> None:
    conn = sqlite3.connect(":memory:")
    _table(conn)
    first = ids.timestamp_id("items", salt=SALT, unix_ms=UNIX_MS, seq=0)
    second = ids.timestamp_id("items", salt=SALT, unix_ms=UNIX_MS, seq=1)
    conn.execute("INSERT INTO items (public_id) VALUES (?)", (first,))
    tried: list[int] = []

    def insert(num: int) -> None:
        tried.append(num)
        conn.execute("INSERT INTO items (public_id) VALUES (?)", (num,))

    got = ids.insert_with_timestamp_id(
        conn, table="items", column="public_id", salt=SALT, insert=insert, now_ms=lambda: UNIX_MS
    )
    assert got == second
    assert tried == [first, second]


def test_eight_collisions_raise() -> None:
    conn = sqlite3.connect(":memory:")
    _table(conn)
    for seq in range(ids.MAX_INSERT_ATTEMPTS):
        conn.execute(
            "INSERT INTO items (public_id) VALUES (?)",
            (ids.timestamp_id("items", salt=SALT, unix_ms=UNIX_MS, seq=seq),),
        )
    calls = []

    def insert(num: int) -> None:
        calls.append(num)
        conn.execute("INSERT INTO items (public_id) VALUES (?)", (num,))

    with pytest.raises(ids.TimestampIdExhausted):
        ids.insert_with_timestamp_id(
            conn, table="items", column="public_id", salt=SALT, insert=insert, now_ms=lambda: UNIX_MS
        )
    assert len(calls) == ids.MAX_INSERT_ATTEMPTS == 8


def test_other_constraint_violation_is_not_retried() -> None:
    conn = sqlite3.connect(":memory:")
    _table(conn)
    conn.execute("INSERT INTO items (public_id, label) VALUES (1, 'dup')")
    calls = []

    def insert(num: int) -> None:
        calls.append(num)
        conn.execute("INSERT INTO items (public_id, label) VALUES (?, 'dup')", (num,))

    with pytest.raises(sqlite3.IntegrityError, match="items.label"):
        ids.insert_with_timestamp_id(conn, table="items", column="public_id", salt=SALT, insert=insert)
    assert len(calls) == 1


def test_format_and_parse_public_id() -> None:
    n = ids.timestamp_id("themes", salt=SALT, unix_ms=UNIX_MS)
    s = ids.format_public_id(n)
    assert s == str(n)
    assert ids.parse_public_id(s) == n
    assert ids.parse_public_id(n) == n
    assert ids.parse_public_id(5) == 5


@pytest.mark.parametrize(
    "raw",
    ["", "0", "-1", "+1", " 1", "1 ", "01", "1.0", "1e3", "١٢", str(1 << 63), 0, -5, 1 << 63, True, 1.5, None],
)
def test_parse_public_id_rejects(raw) -> None:
    with pytest.raises(ValueError):
        ids.parse_public_id(raw)


def test_format_public_id_rejects_out_of_range() -> None:
    with pytest.raises(ValueError):
        ids.format_public_id(0)
    with pytest.raises(ValueError):
        ids.format_public_id(1 << 63)
    with pytest.raises(TypeError):
        ids.format_public_id(True)
