"""``legacy_hosts`` 읽기·쓰기 계층 (설계 §5.3, §13.2, Rev. 2.3).

옛 테마 FQDN은 v16 이관이 ``legacy_hosts``로 옮긴다(테이블과 이관은 `store.db`). 이 모듈은

- ``SqliteLegacyHostDirectory``: ``HostRouter``가 요청마다 묻는 ``LegacyHostDirectory`` 구현. 행과 정책을 메모리에 두고, DB 파일(본 파일과 ``-wal``)의 변경이나 이 프로세스의 쓰기가 있으면 다시 읽는다.
- 일몰 시계 활성화·연장·무기한 유지 함수와 ``instance_settings.legacy_host_policy``(JSON) 읽기·쓰기. 화면과 API는 ``/api/admin/instance``(P2 L2-f)가 맡는다.

테이블이 아직 없으면(v16 이전 DB) 레거시 호스트가 없는 것으로 본다. 정책 JSON이 잘못됐으면 기본값으로 대신하지 않고 ``ValueError``다(C1).
"""

from __future__ import annotations

import json
import sqlite3
import threading
import time
from collections.abc import Callable, Iterable
from pathlib import Path
from typing import Any

from ..api.host_router import LegacyHost, LegacyHostPolicy
from . import db as dbm

__all__ = [
    "LEGACY_HOST_POLICY_KEY",
    "SECONDS_PER_DAY",
    "SqliteLegacyHostDirectory",
    "activate_sunset_clock",
    "extend_sunset",
    "keep_indefinitely",
    "list_legacy_hosts",
    "read_legacy_host_policy",
    "write_legacy_host_policy",
]

LEGACY_HOST_POLICY_KEY = "legacy_host_policy"
SECONDS_PER_DAY = 86400
_MAX_SUNSET_DAYS = 3650
_COLUMNS = "host, theme_id, created_at, activated_at, sunset_at"

# 이 프로세스의 쓰기 세대. 파일 시각이 같은 틱 안에서 바뀌어도 캐시가 곧바로 무효화되게 한다.
_generation = 0
_generation_lock = threading.Lock()


def _bump_generation() -> None:
    global _generation
    with _generation_lock:
        _generation += 1


def _normalize_host(host: str) -> str:
    return str(host).strip().lower()


def _row(row: Any) -> LegacyHost:
    return LegacyHost(
        host=str(row[0]),
        theme_id=int(row[1]),
        created_at=float(row[2]),
        activated_at=None if row[3] is None else float(row[3]),
        sunset_at=None if row[4] is None else float(row[4]),
    )


def _is_missing_table(exc: sqlite3.OperationalError) -> bool:
    return "no such table" in str(exc)


# --- 정책 ---------------------------------------------------------------------


def _validate_policy(policy: LegacyHostPolicy) -> LegacyHostPolicy:
    if policy.after_sunset_status not in (410, 421):
        raise ValueError("after_sunset_status must be 410 or 421")
    if (
        isinstance(policy.sunset_days, bool)
        or not isinstance(policy.sunset_days, int)
        or not 1 <= policy.sunset_days <= _MAX_SUNSET_DAYS
    ):
        raise ValueError(f"sunset_days must be an integer in 1..{_MAX_SUNSET_DAYS}")
    return policy


def _parse_policy(raw: str | None) -> LegacyHostPolicy:
    if raw is None:
        return LegacyHostPolicy()
    try:
        data = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise ValueError("instance_settings.legacy_host_policy is not valid JSON") from exc
    if not isinstance(data, dict) or set(data) - {"after_sunset_status", "sunset_days"}:
        raise ValueError("instance_settings.legacy_host_policy has an unexpected shape")
    default = LegacyHostPolicy()
    return _validate_policy(
        LegacyHostPolicy(
            after_sunset_status=data.get("after_sunset_status", default.after_sunset_status),
            sunset_days=data.get("sunset_days", default.sunset_days),
        )
    )


def read_legacy_host_policy(conn: sqlite3.Connection) -> LegacyHostPolicy:
    """``instance_settings.legacy_host_policy``. 행이 없으면 기본값(410, 30일)."""
    try:
        row = conn.execute(
            "SELECT value FROM instance_settings WHERE key=?", (LEGACY_HOST_POLICY_KEY,)
        ).fetchone()
    except sqlite3.OperationalError as exc:
        if _is_missing_table(exc):
            return LegacyHostPolicy()
        raise
    return _parse_policy(None if row is None else str(row[0]))


def write_legacy_host_policy(
    conn: sqlite3.Connection, policy: LegacyHostPolicy, *, updated_by: int | None
) -> None:
    _validate_policy(policy)
    value = json.dumps(
        {"after_sunset_status": policy.after_sunset_status, "sunset_days": policy.sunset_days},
        separators=(",", ":"),
        sort_keys=True,
    )
    conn.execute(
        "INSERT INTO instance_settings (key, value, updated_at, updated_by) VALUES (?, ?, ?, ?) "
        "ON CONFLICT(key) DO UPDATE SET value=excluded.value, updated_at=excluded.updated_at, "
        "updated_by=excluded.updated_by",
        (LEGACY_HOST_POLICY_KEY, value, time.time(), updated_by),
    )
    conn.commit()
    _bump_generation()


# --- 행 -----------------------------------------------------------------------


def list_legacy_hosts(conn: sqlite3.Connection, *, theme_id: int | None = None) -> list[LegacyHost]:
    """호스트 이름 순. 테이블이 없으면 빈 목록."""
    sql = f"SELECT {_COLUMNS} FROM legacy_hosts"
    params: tuple[Any, ...] = ()
    if theme_id is not None:
        sql += " WHERE theme_id=?"
        params = (int(theme_id),)
    try:
        rows = conn.execute(sql + " ORDER BY host", params).fetchall()
    except sqlite3.OperationalError as exc:
        if _is_missing_table(exc):
            return []
        raise
    return [_row(r) for r in rows]


def _get(conn: sqlite3.Connection, host: str) -> LegacyHost:
    row = conn.execute(f"SELECT {_COLUMNS} FROM legacy_hosts WHERE host=?", (host,)).fetchone()
    if row is None:
        raise KeyError(host)
    return _row(row)


def activate_sunset_clock(
    conn: sqlite3.Connection,
    *,
    hosts: Iterable[str] | None = None,
    days: int | None = None,
    now: float | None = None,
) -> int:
    """일몰 시계를 켠다(§5.3): ``activated_at=now``, ``sunset_at=now + days*86400``.

    아직 활성화하지 않은 행(``activated_at IS NULL``)만 바꾼다. ``hosts``가 None이면 그런 행 전부(네트워크 모드 전환), 주면 그 호스트만이다. ``days``가 None이면 정책의 ``sunset_days``. 바꾼 행 수를 돌려준다.
    """
    when = time.time() if now is None else float(now)
    span = read_legacy_host_policy(conn).sunset_days if days is None else days
    _validate_policy(LegacyHostPolicy(sunset_days=span))
    sunset = when + span * SECONDS_PER_DAY
    if hosts is None:
        cur = conn.execute(
            "UPDATE legacy_hosts SET activated_at=?, sunset_at=? WHERE activated_at IS NULL",
            (when, sunset),
        )
        changed = cur.rowcount
    else:
        changed = 0
        for host in {_normalize_host(h) for h in hosts}:
            cur = conn.execute(
                "UPDATE legacy_hosts SET activated_at=?, sunset_at=? WHERE host=? AND activated_at IS NULL",
                (when, sunset, host),
            )
            changed += cur.rowcount
    conn.commit()
    _bump_generation()
    return changed


def extend_sunset(
    conn: sqlite3.Connection, host: str, *, days: int, now: float | None = None
) -> LegacyHost:
    """일몰을 ``days``일 늦춘다. 기준은 지금 일몰 시각과 현재 중 늦은 쪽이다(이미 지났으면 지금부터).

    활성화하지 않았거나 무기한 유지 중인 호스트도 이 호출로 일몰 시계가 켜진다(``activated_at``이 없으면 ``now``). 없는 호스트면 ``KeyError``.
    """
    _validate_policy(LegacyHostPolicy(sunset_days=days))
    when = time.time() if now is None else float(now)
    name = _normalize_host(host)
    current = _get(conn, name)
    base = max(current.sunset_at if current.sunset_at is not None else when, when)
    conn.execute(
        "UPDATE legacy_hosts SET activated_at=COALESCE(activated_at, ?), sunset_at=? WHERE host=?",
        (when, base + days * SECONDS_PER_DAY, name),
    )
    conn.commit()
    _bump_generation()
    return _get(conn, name)


def keep_indefinitely(conn: sqlite3.Connection, host: str) -> LegacyHost:
    """무기한 308 유지(``sunset_at=NULL``). ``activated_at``은 기록으로 남긴다. 없는 호스트면 ``KeyError``."""
    name = _normalize_host(host)
    _get(conn, name)
    conn.execute("UPDATE legacy_hosts SET sunset_at=NULL WHERE host=?", (name,))
    conn.commit()
    _bump_generation()
    return _get(conn, name)


# --- HostRouter용 디렉터리 ------------------------------------------------------


class SqliteLegacyHostDirectory:
    """``LegacyHostDirectory``(api.host_router) 구현.

    ``theme_location``은 ``create_app``이 넘긴다(테마 번호와 모드는 테마 매니저·네임스페이스가 안다). 주지 않으면 항상 None(421)이다.
    """

    def __init__(
        self,
        db_file: str | Path,
        *,
        theme_location: Callable[[LegacyHost], str | None] | None = None,
    ) -> None:
        self.db_file = Path(db_file)
        self._theme_location = theme_location
        self._lock = threading.Lock()
        self._signature: tuple[Any, ...] | None = None
        self._hosts: dict[str, LegacyHost] = {}
        self._policy: LegacyHostPolicy | None = None
        self._policy_error: ValueError | None = None

    def _file_signature(self) -> tuple[Any, ...]:
        parts: list[Any] = [_generation]
        for path in (self.db_file, self.db_file.with_name(self.db_file.name + "-wal")):
            try:
                st = path.stat()
            except FileNotFoundError:
                parts.append(None)
            else:
                parts.append((st.st_mtime_ns, st.st_size, st.st_ino))
        return tuple(parts)

    def _refresh(self) -> None:
        signature = self._file_signature()
        if signature == self._signature:
            return
        hosts: dict[str, LegacyHost] = {}
        policy: LegacyHostPolicy | None = LegacyHostPolicy()
        policy_error: ValueError | None = None
        if self.db_file.is_file():
            conn = dbm.connect_existing(self.db_file, readonly=True)
            try:
                hosts = {row.host: row for row in list_legacy_hosts(conn)}
                try:
                    policy = read_legacy_host_policy(conn)
                except ValueError as exc:
                    policy, policy_error = None, exc
            finally:
                conn.close()
        self._hosts, self._policy, self._policy_error = hosts, policy, policy_error
        self._signature = signature

    def invalidate(self) -> None:
        with self._lock:
            self._signature = None

    def lookup(self, host: str) -> LegacyHost | None:
        with self._lock:
            self._refresh()
            return self._hosts.get(_normalize_host(host))

    def list_hosts_for_theme(self, theme_id: int) -> list[LegacyHost]:
        with self._lock:
            self._refresh()
            return [h for h in self._hosts.values() if h.theme_id == theme_id]

    def policy(self) -> LegacyHostPolicy:
        with self._lock:
            self._refresh()
            if self._policy is None:
                raise self._policy_error or ValueError("legacy host policy is unavailable")
            return self._policy

    def theme_location(self, legacy: LegacyHost) -> str | None:
        if self._theme_location is None:
            return None
        return self._theme_location(legacy)
