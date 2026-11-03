"""서버 등급 저장과 예산 집행 (설계 §10.3, P4 계약 스텁).

등급은 ``claire.db`` ``instance_settings``의 ``server_tier`` 키에 ``ServerTier.to_dict()`` JSON으로 둔다.
값이 없으면 기본 ``ServerTier()``(``provider='none'``, D6)이다. 사용량은 ``store.usage_ledger``(auth.db)에 둔다.

- 관리자 컨텍스트(``is_admin`` 또는 ``account_id`` None = 호스트 운영자)는 어떤 한도도 받지 않는다.
- 자원 한도(``max_themes``·``max_documents``·``storage_mb``·``daily_ingests``)는 BYOK 멤버에게도 적용한다.
- 월 토큰 상한은 서버 등급 컨텍스트(``source='tier'``)에만 적용한다.
- 한도 값 0은 "허용 없음"이다(fail-closed).

레인 소유: L4-c.
"""

from __future__ import annotations

import json
import logging
import os
import sqlite3
import threading
import time
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

from claire.accounts.provider_policy import (
    SERVER_TIER_SETTING_KEY,
    ProviderBudgetExceeded,
    ProviderContext,
    ServerTier,
    UsageHooks,
)
from claire.store import usage_ledger

log = logging.getLogger(__name__)

MIB = 1024 * 1024


# --- 저장 ----------------------------------------------------------------------------


def parse_server_tier(raw: str) -> ServerTier:
    """저장된 JSON 문자열을 검증하며 읽는다. JSON이 아니거나 ``ServerTier.from_dict``가 거부하면 ``ValueError``."""
    try:
        data = json.loads(raw)
    except (TypeError, ValueError) as exc:
        raise ValueError("server tier is not valid JSON") from exc
    return ServerTier.from_dict(data)


def read_stored_server_tier(conn: sqlite3.Connection) -> str | None:
    """``instance_settings.server_tier`` 원문. 없으면 None."""
    row = conn.execute(
        "SELECT value FROM instance_settings WHERE key=?", (SERVER_TIER_SETTING_KEY,)
    ).fetchone()
    return None if row is None else str(row[0])


def load_server_tier(conn: sqlite3.Connection) -> ServerTier:
    """``claire.db`` 연결에서 읽는다. 값이 없거나 깨졌으면 기본 등급(깨진 값은 로그만 남기고 ``none``으로 fail-closed)."""
    raw = read_stored_server_tier(conn)
    if raw is None:
        return ServerTier()
    try:
        return parse_server_tier(raw)
    except ValueError as exc:
        # 원문은 로그에 넣지 않는다(관리자가 잘못 넣은 값에 비밀이 섞였을 수 있다).
        log.warning("instance_settings.server_tier is invalid; falling back to provider=none: %s", exc)
        return ServerTier()


def save_server_tier(conn: sqlite3.Connection, tier: ServerTier, *, updated_by: int) -> None:
    """검증한 뒤(``to_dict`` → ``from_dict`` 왕복) JSON으로 저장하고 커밋한다."""
    data = ServerTier.from_dict(tier.to_dict()).to_dict()
    conn.execute(
        "INSERT INTO instance_settings (key, value, updated_at, updated_by) VALUES (?, ?, ?, ?) "
        "ON CONFLICT(key) DO UPDATE SET value=excluded.value, updated_at=excluded.updated_at, "
        "updated_by=excluded.updated_by",
        (SERVER_TIER_SETTING_KEY, json.dumps(data, sort_keys=True), time.time(), updated_by),
    )
    conn.commit()


# --- 공용 ----------------------------------------------------------------------------


def _is_admin(ctx: ProviderContext) -> bool:
    return ctx.is_admin or ctx.account_id is None


def _member_id(ctx: ProviderContext) -> int:
    assert ctx.account_id is not None
    return int(ctx.account_id)


def _open_ledger(auth_db_path: Path | str) -> sqlite3.Connection:
    """기동 때 준비한(v2로 이관한) auth.db를 연다. 없으면 만들지 않고 실패한다(C1)."""
    uri = Path(auth_db_path).resolve().as_uri() + "?mode=rw"
    conn = sqlite3.connect(uri, uri=True, timeout=5.0)
    conn.execute("PRAGMA busy_timeout=5000;")
    return conn


# --- 원장 집행 -------------------------------------------------------------------------


def check_ingest_allowed(ctx: ProviderContext, *, auth_db_path: Path | str, now: float | None = None) -> None:
    """적재 1건을 예약한다. 관리자는 통과. 멤버(BYOK 포함)는 ``tier.daily_ingests``를 넘으면 ``ProviderBudgetExceeded``."""
    if _is_admin(ctx):
        return
    conn = _open_ledger(auth_db_path)
    try:
        ok = usage_ledger.try_reserve_ingest(
            conn, account_id=_member_id(ctx), daily_limit=ctx.tier.daily_ingests, now=now
        )
    finally:
        conn.close()
    if not ok:
        raise ProviderBudgetExceeded(
            f"daily ingest limit reached ({ctx.tier.daily_ingests} per UTC day)",
            remedy="UTC 자정이 지나면 다시 적재할 수 있습니다. 한도를 늘리려면 관리자에게 요청하세요.",
        )


def check_tokens_available(ctx: ProviderContext, *, auth_db_path: Path | str, now: float | None = None) -> None:
    """서버 등급(``source='tier'``) 컨텍스트에서 이번 달 토큰이 남지 않았으면 ``ProviderBudgetExceeded``. 관리자·BYOK는 통과."""
    if _is_admin(ctx) or ctx.source != "tier":
        return
    conn = _open_ledger(auth_db_path)
    try:
        remaining = usage_ledger.tokens_remaining(
            conn, account_id=_member_id(ctx), monthly_limit=ctx.tier.monthly_tokens, now=now
        )
    finally:
        conn.close()
    if remaining <= 0:
        raise ProviderBudgetExceeded(
            f"monthly token budget exhausted ({ctx.tier.monthly_tokens} per UTC month)",
            remedy="다음 UTC 달까지 기다리거나 내 계정에서 자기 API 키를 등록하세요.",
        )


def record_usage(
    ctx: ProviderContext,
    *,
    auth_db_path: Path | str,
    tokens_in: int,
    tokens_out: int,
    now: float | None = None,
) -> None:
    """멤버 컨텍스트의 토큰을 원장에 더한다. 관리자 컨텍스트(``account_id`` None 포함)는 기록하지 않는다."""
    if _is_admin(ctx):
        return
    if max(0, int(tokens_in or 0)) == 0 and max(0, int(tokens_out or 0)) == 0:
        return
    conn = _open_ledger(auth_db_path)
    try:
        usage_ledger.record_tokens(
            conn, account_id=_member_id(ctx), tokens_in=tokens_in, tokens_out=tokens_out, now=now
        )
    finally:
        conn.close()


class _LedgerHooks:
    """``UsageHooks`` 구현. 호출마다 auth.db를 짧게 연다."""

    __slots__ = ("_ctx", "_path")

    def __init__(self, ctx: ProviderContext, auth_db_path: Path | str) -> None:
        self._ctx = ctx
        self._path = Path(auth_db_path)

    def before_call(self) -> None:
        check_tokens_available(self._ctx, auth_db_path=self._path)

    def after_call(self, tokens_in: int, tokens_out: int) -> None:
        record_usage(self._ctx, auth_db_path=self._path, tokens_in=tokens_in, tokens_out=tokens_out)

    def __repr__(self) -> str:  # api_key가 드러나지 않게 컨텍스트 repr 대신 요약만
        return f"_LedgerHooks(account_id={self._ctx.account_id}, source={self._ctx.source})"


def usage_hooks_for(ctx: ProviderContext, *, auth_db_path: Path | str) -> "UsageHooks | None":
    """``check_tokens_available``·``record_usage``로 만든 ``UsageHooks``. 관리자 컨텍스트는 None."""
    if _is_admin(ctx):
        return None
    return _LedgerHooks(ctx, auth_db_path)


# --- 자원 한도 (claire.db) ------------------------------------------------------------


def account_theme_count(conn: sqlite3.Connection, account_id: int) -> int:
    return int(conn.execute("SELECT COUNT(*) FROM themes WHERE account_id=?", (account_id,)).fetchone()[0])


def account_document_count(conn: sqlite3.Connection, account_id: int) -> int:
    row = conn.execute(
        "SELECT COUNT(*) FROM documents WHERE theme_id IN (SELECT id FROM themes WHERE account_id=?)",
        (account_id,),
    ).fetchone()
    return int(row[0])


def account_storage_bytes(conn: sqlite3.Connection, account_id: int) -> int:
    """계정 테마들의 저장 용량 근사(바이트).

    더하는 것: 문서의 ``raw_text``·``detail``·``detail_html`` UTF-8 바이트, ``raw_inbox.payload`` 바이트, ``raw_inbox.file_ref``가
    가리키는 원본 파일 크기(절대 경로이고 파일이 있을 때만, 같은 파일은 한 번). 엔티티·관계·임베딩·FTS 색인·스냅샷은 넣지 않는다.
    """
    themes_sql = "SELECT id FROM themes WHERE account_id=?"
    doc_bytes = conn.execute(
        f"""
        SELECT COALESCE(SUM(
            COALESCE(LENGTH(CAST(raw_text AS BLOB)), 0)
            + COALESCE(LENGTH(CAST(detail AS BLOB)), 0)
            + COALESCE(LENGTH(CAST(detail_html AS BLOB)), 0)
        ), 0)
        FROM documents WHERE theme_id IN ({themes_sql})
        """,
        (account_id,),
    ).fetchone()[0]
    inbox_bytes = conn.execute(
        f"SELECT COALESCE(SUM(COALESCE(LENGTH(CAST(payload AS BLOB)), 0)), 0) FROM raw_inbox "
        f"WHERE theme_id IN ({themes_sql})",
        (account_id,),
    ).fetchone()[0]
    file_bytes = 0
    refs = conn.execute(
        f"SELECT DISTINCT file_ref FROM raw_inbox WHERE theme_id IN ({themes_sql}) "
        "AND file_ref IS NOT NULL AND trim(file_ref) != ''",
        (account_id,),
    ).fetchall()
    for (ref,) in refs:
        if not os.path.isabs(str(ref)):
            continue
        try:
            file_bytes += os.stat(str(ref)).st_size
        except OSError:
            continue
    return int(doc_bytes) + int(inbox_bytes) + int(file_bytes)


def check_theme_quota(conn: sqlite3.Connection, ctx: ProviderContext) -> None:
    """멤버가 테마를 하나 더 만들면 ``tier.max_themes``를 넘는지. 넘으면 ``ProviderBudgetExceeded``. 자기 키가 있어도 적용한다."""
    if _is_admin(ctx):
        return
    if account_theme_count(conn, _member_id(ctx)) >= ctx.tier.max_themes:
        raise ProviderBudgetExceeded(
            f"theme limit reached ({ctx.tier.max_themes})",
            remedy="쓰지 않는 테마를 지우거나 관리자에게 한도 상향을 요청하세요.",
        )


def check_document_quota(conn: sqlite3.Connection, ctx: ProviderContext) -> None:
    """멤버 계정 전체 문서 수가 ``tier.max_documents``에 닿았으면 ``ProviderBudgetExceeded``. 저장 용량(``storage_mb``)도 여기서 본다."""
    if _is_admin(ctx):
        return
    account_id = _member_id(ctx)
    if account_document_count(conn, account_id) >= ctx.tier.max_documents:
        raise ProviderBudgetExceeded(
            f"document limit reached ({ctx.tier.max_documents})",
            remedy="문서를 정리하거나 관리자에게 한도 상향을 요청하세요.",
        )
    if account_storage_bytes(conn, account_id) >= ctx.tier.storage_mb * MIB:
        raise ProviderBudgetExceeded(
            f"storage limit reached ({ctx.tier.storage_mb} MB)",
            remedy="문서를 정리하거나 관리자에게 한도 상향을 요청하세요.",
        )


# --- BYOK 동시 작업 상한 -------------------------------------------------------------

_SLOT_LOCK = threading.Lock()
_ACTIVE_SLOTS: dict[int, int] = {}


@contextmanager
def job_slot(ctx: ProviderContext) -> Iterator[None]:
    """BYOK 동시 작업 상한(``byok_caps.max_concurrency``, 프로세스 안 계정별 세마포어). 자리가 없으면 기다리지 않고 ``ProviderBudgetExceeded``.

    관리자와 서버 등급 컨텍스트는 그대로 통과한다(서버 등급은 ``daily_ingests``로 묶인다).
    상한은 들어올 때의 ``ctx.tier`` 값으로 판정하므로 관리자가 등급을 바꾸면 다음 작업부터 반영된다.
    """
    if _is_admin(ctx) or ctx.source != "byok":
        yield
        return
    account_id = _member_id(ctx)
    cap = ctx.tier.byok_max_concurrency
    with _SLOT_LOCK:
        active = _ACTIVE_SLOTS.get(account_id, 0)
        if active >= cap:
            raise ProviderBudgetExceeded(
                f"concurrent job limit reached ({cap})",
                remedy="진행 중인 작업이 끝난 뒤 다시 시도하세요.",
            )
        _ACTIVE_SLOTS[account_id] = active + 1
    try:
        yield
    finally:
        with _SLOT_LOCK:
            left = _ACTIVE_SLOTS.get(account_id, 1) - 1
            if left <= 0:
                _ACTIVE_SLOTS.pop(account_id, None)
            else:
                _ACTIVE_SLOTS[account_id] = left
