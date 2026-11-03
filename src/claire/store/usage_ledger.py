"""계정별 UTC 사용량 원장 (설계 §10.3, §13.3 ``account_usage``, P4 계약 스텁).

월 단위 행(``period='YYYY-MM'``)에 토큰과 적재 건수를 더하고, 같은 행의 ``daily_day``·``daily_ingests``로 UTC 일 적재 건수를 센다.
날짜가 바뀌면 ``daily_ingests``를 0부터 다시 센다. 모든 갱신은 한 UPDATE 문으로 원자적으로 한다.

- 월이 바뀌면 새 ``period`` 행이 생기므로 ``daily_*``도 자연히 새로 시작한다.
- 연결은 auth.db(v2 이상) 연결이다. 함수는 각자 커밋한다(``with conn``).

레인 소유: L4-c.
"""

from __future__ import annotations

import sqlite3
import time
from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class UsageSnapshot:
    account_id: int
    period: str          # UTC 'YYYY-MM'
    tokens_in: int
    tokens_out: int
    ingests: int
    daily_day: str       # UTC 'YYYY-MM-DD'
    daily_ingests: int   # daily_day가 오늘이 아니면 0으로 읽는다

    @property
    def tokens_total(self) -> int:
        return self.tokens_in + self.tokens_out


def _ts(now: float | None) -> float:
    return time.time() if now is None else float(now)


def utc_period(now: float | None = None) -> str:
    return time.strftime("%Y-%m", time.gmtime(_ts(now)))


def utc_day(now: float | None = None) -> str:
    return time.strftime("%Y-%m-%d", time.gmtime(_ts(now)))


def get_usage(conn: sqlite3.Connection, *, account_id: int, now: float | None = None) -> UsageSnapshot:
    """이번 달(UTC) 행을 읽는다. 행이 없으면 0. ``daily_day``는 항상 오늘이고, 저장된 날이 오늘이 아니면 ``daily_ingests``는 0이다."""
    ts = _ts(now)
    period, day = utc_period(ts), utc_day(ts)
    row = conn.execute(
        "SELECT tokens_in, tokens_out, ingests, daily_day, daily_ingests FROM account_usage "
        "WHERE account_id=? AND period=?",
        (int(account_id), period),
    ).fetchone()
    if row is None:
        return UsageSnapshot(int(account_id), period, 0, 0, 0, day, 0)
    daily = int(row[4]) if str(row[3]) == day else 0
    return UsageSnapshot(int(account_id), period, int(row[0]), int(row[1]), int(row[2]), day, daily)


def list_usage(conn: sqlite3.Connection, *, now: float | None = None) -> list[UsageSnapshot]:
    """이번 달(UTC) 모든 계정의 사용량(관리자 화면 요약용). ``account_id`` 오름차순."""
    ts = _ts(now)
    period, day = utc_period(ts), utc_day(ts)
    rows = conn.execute(
        "SELECT account_id, tokens_in, tokens_out, ingests, daily_day, daily_ingests FROM account_usage "
        "WHERE period=? ORDER BY account_id",
        (period,),
    ).fetchall()
    return [
        UsageSnapshot(
            int(r[0]), period, int(r[1]), int(r[2]), int(r[3]), day, int(r[5]) if str(r[4]) == day else 0
        )
        for r in rows
    ]


def record_tokens(
    conn: sqlite3.Connection,
    *,
    account_id: int,
    tokens_in: int,
    tokens_out: int,
    now: float | None = None,
) -> None:
    """이번 달 행에 토큰을 더한다(음수는 0으로 본다). 한 UPSERT 문이다."""
    t_in = max(0, int(tokens_in or 0))
    t_out = max(0, int(tokens_out or 0))
    if t_in == 0 and t_out == 0:
        return
    with conn:
        conn.execute(
            """
            INSERT INTO account_usage(account_id, period, tokens_in, tokens_out)
            VALUES (?, ?, ?, ?)
            ON CONFLICT(account_id, period) DO UPDATE SET
                tokens_in = tokens_in + excluded.tokens_in,
                tokens_out = tokens_out + excluded.tokens_out
            """,
            (int(account_id), utc_period(now), t_in, t_out),
        )


def try_reserve_ingest(
    conn: sqlite3.Connection,
    *,
    account_id: int,
    daily_limit: int,
    now: float | None = None,
) -> bool:
    """오늘(UTC) 적재 1건을 원자적으로 예약한다. 상한에 닿았으면 False. 동시 호출에서 상한을 넘지 않는다.

    판정과 증가를 조건부 UPSERT 한 문으로 한다(``DO UPDATE ... WHERE``). 조건이 거짓이면 행이 바뀌지 않고 ``rowcount``가 0이다.
    SET의 식은 모두 갱신 전 값을 읽으므로 ``daily_day``를 바꾸는 것과 이전 날 비교가 서로 섞이지 않는다.
    """
    limit = int(daily_limit)
    if limit <= 0:
        return False
    ts = _ts(now)
    period, day = utc_period(ts), utc_day(ts)
    with conn:
        cur = conn.execute(
            """
            INSERT INTO account_usage(account_id, period, ingests, daily_day, daily_ingests)
            VALUES (?, ?, 1, ?, 1)
            ON CONFLICT(account_id, period) DO UPDATE SET
                ingests = ingests + 1,
                daily_ingests = CASE WHEN daily_day = excluded.daily_day THEN daily_ingests + 1 ELSE 1 END,
                daily_day = excluded.daily_day
            WHERE (CASE WHEN account_usage.daily_day = excluded.daily_day
                        THEN account_usage.daily_ingests ELSE 0 END) < ?
            """,
            (int(account_id), period, day, limit),
        )
        reserved = cur.rowcount == 1
    return reserved


def tokens_remaining(conn: sqlite3.Connection, *, account_id: int, monthly_limit: int, now: float | None = None) -> int:
    """이번 달 남은 토큰(0 이상)."""
    used = get_usage(conn, account_id=account_id, now=now).tokens_total
    return max(0, int(monthly_limit) - used)
