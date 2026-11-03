"""Public directory indexing and querying for network mode."""

from __future__ import annotations

import sqlite3
import time
from dataclasses import dataclass
from typing import Protocol

from .. import ids


@dataclass(frozen=True, slots=True)
class PublicThemeSummary:
    id: int
    number: int | None
    label: str
    is_default: bool
    documents: int


@dataclass(frozen=True, slots=True)
class PublicAccountSummary:
    account_id: int
    public_id: int
    username: str
    display_name: str
    public_themes: int
    public_documents: int
    created_at: float
    themes: tuple[PublicThemeSummary, ...] = ()


class PublicIndexStore(Protocol):
    """Protocol for public directory counters and index queries."""

    def list_public_accounts(
        self,
        *,
        limit: int = 50,
        max_id: int | None = None,
        min_id: int | None = None,
        since_id: int | None = None,
        include_themes: bool = True,
    ) -> list[PublicAccountSummary]:
        ...

    def count_public_accounts(self) -> int:
        ...

    def refresh_counters_for_account(self, account_id: int) -> tuple[int, int]:
        ...

    def refresh_all_counters(self) -> None:
        ...


class SqlitePublicIndexStore:
    def __init__(self, conn: sqlite3.Connection) -> None:
        self._conn = conn

    def list_public_accounts(
        self,
        *,
        limit: int = 50,
        max_id: int | None = None,
        min_id: int | None = None,
        since_id: int | None = None,
        include_themes: bool = True,
    ) -> list[PublicAccountSummary]:
        query = """
            SELECT a.id, a.public_id, a.username, a.display_name,
                   COALESCE(c.public_themes, (
                       SELECT COUNT(DISTINCT t.id) FROM themes t WHERE t.account_id = a.id AND t.is_public = 1
                   )) as public_themes,
                   COALESCE(c.public_documents, (
                       SELECT COUNT(DISTINCT d.id) FROM themes t
                       JOIN documents d ON t.id = d.theme_id
                       WHERE t.account_id = a.id AND t.is_public = 1 AND COALESCE(d.hidden, 0) = 0
                   )) as public_documents,
                   a.created_at
            FROM accounts a
            LEFT JOIN public_directory_counters c ON a.id = c.account_id
            WHERE a.profile_public = 1 AND a.status = 'active'
        """
        params: list[object] = []
        if max_id is not None:
            query += " AND a.public_id < ?"
            params.append(max_id)
        if min_id is not None:
            query += " AND a.public_id > ?"
            params.append(min_id)
        if since_id is not None:
            query += " AND a.public_id > ?"
            params.append(since_id)

        order = "ASC" if min_id is not None else "DESC"
        query += f" ORDER BY a.public_id {order} LIMIT ?"
        eff_limit = min(max(limit, 1), 80)
        params.append(eff_limit)

        cursor = self._conn.execute(query, params)
        rows = list(cursor.fetchall())
        if min_id is not None:
            rows.reverse()

        account_themes: dict[int, list[PublicThemeSummary]] = {}
        if include_themes and rows:
            acc_ids = [r[0] for r in rows]
            placeholders = ",".join("?" for _ in acc_ids)
            theme_query = f"""
                SELECT t.id, t.number, t.label, t.is_default, t.account_id,
                       COUNT(DISTINCT CASE WHEN d.id IS NOT NULL AND COALESCE(d.hidden, 0) = 0 THEN d.id END) as docs_count
                FROM themes t
                LEFT JOIN documents d ON t.id = d.theme_id
                WHERE t.account_id IN ({placeholders}) AND t.is_public = 1
                GROUP BY t.id
                ORDER BY t.is_default DESC, t.id ASC
            """
            t_cursor = self._conn.execute(theme_query, acc_ids)
            for tr in t_cursor.fetchall():
                t_aid = tr[4]
                if t_aid not in account_themes:
                    account_themes[t_aid] = []
                account_themes[t_aid].append(
                    PublicThemeSummary(
                        id=tr[0],
                        number=tr[1],
                        label=tr[2],
                        is_default=bool(tr[3]),
                        documents=int(tr[5]),
                    )
                )

        return [
            PublicAccountSummary(
                account_id=r[0],
                public_id=r[1],
                username=r[2],
                display_name=r[3],
                public_themes=r[4],
                public_documents=r[5],
                created_at=r[6],
                themes=tuple(account_themes.get(r[0], ())),
            )
            for r in rows
        ]

    def count_public_accounts(self) -> int:
        cur = self._conn.execute(
            "SELECT COUNT(*) FROM accounts WHERE profile_public = 1 AND status = 'active'"
        )
        row = cur.fetchone()
        return int(row[0]) if row and row[0] is not None else 0

    def refresh_counters_for_account(self, account_id: int) -> tuple[int, int]:
        now = time.time()
        cur = self._conn.execute(
            """
            SELECT COUNT(DISTINCT t.id) as themes_count,
                   COUNT(DISTINCT CASE WHEN d.id IS NOT NULL AND COALESCE(d.hidden, 0) = 0 THEN d.id END) as docs_count
            FROM themes t
            LEFT JOIN documents d ON t.id = d.theme_id
            WHERE t.account_id = ? AND t.is_public = 1
            """,
            (account_id,),
        )
        row = cur.fetchone()
        themes_cnt = int(row[0]) if row and row[0] is not None else 0
        docs_cnt = int(row[1]) if row and row[1] is not None else 0

        self._conn.execute(
            """
            INSERT INTO public_directory_counters (account_id, public_themes, public_documents, updated_at)
            VALUES (?, ?, ?, ?)
            ON CONFLICT(account_id) DO UPDATE SET
                public_themes = excluded.public_themes,
                public_documents = excluded.public_documents,
                updated_at = excluded.updated_at
            """,
            (account_id, themes_cnt, docs_cnt, now),
        )
        try:
            self._conn.commit()
        except Exception:
            pass
        return (themes_cnt, docs_cnt)

    def refresh_all_counters(self) -> None:
        cur = self._conn.execute("SELECT id FROM accounts WHERE profile_public = 1 AND status = 'active'")
        acc_ids = [r[0] for r in cur.fetchall()]
        for aid in acc_ids:
            self.refresh_counters_for_account(aid)
