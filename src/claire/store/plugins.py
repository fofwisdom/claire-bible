"""Records of plugins: one ``claire.db`` ``server_settings`` row, key ``plugins`` (INGEST_ADAPTER_PLUGIN_DESIGN.md §8.2).

Value (JSON)::

    {"initialized_at": 1763500000.0,
     "records": {"claire.youtube": {"enabled": true, "approved_digest": "sha256:…", "approved_revision": 1,
                                    "changed_at": 1763500000.0, "changed_by": 1}}}

Only this module reads and writes the key (store boundary R2). An absent row means that the plugin store has not started on this server yet (§11 item 1).
"""

from __future__ import annotations

import json
import sqlite3
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

SETTING_KEY = "plugins"


class PluginRecordError(RuntimeError):
    """The stored row is not valid JSON of the expected form. A defect of the data."""


@dataclass(frozen=True)
class PluginRecord:
    enabled: bool
    approved_digest: str | None
    approved_revision: int | None
    changed_at: float
    changed_by: int | None

    def to_dict(self) -> dict[str, Any]:
        return {
            "enabled": self.enabled,
            "approved_digest": self.approved_digest,
            "approved_revision": self.approved_revision,
            "changed_at": self.changed_at,
            "changed_by": self.changed_by,
        }


@dataclass(frozen=True)
class PluginRecords:
    initialized_at: float
    records: dict[str, PluginRecord]


def _parse(raw: str) -> PluginRecords:
    try:
        data = json.loads(raw)
        records = {
            str(plugin_id): PluginRecord(
                enabled=bool(item["enabled"]),
                approved_digest=item.get("approved_digest"),
                approved_revision=item.get("approved_revision"),
                changed_at=float(item["changed_at"]),
                changed_by=item.get("changed_by"),
            )
            for plugin_id, item in data["records"].items()
        }
        return PluginRecords(initialized_at=float(data["initialized_at"]), records=records)
    except (ValueError, KeyError, TypeError, AttributeError) as exc:
        raise PluginRecordError(f"server_settings.{SETTING_KEY} is not valid: {exc}") from exc


def _dump(records: PluginRecords) -> str:
    return json.dumps(
        {"initialized_at": records.initialized_at, "records": {k: v.to_dict() for k, v in sorted(records.records.items())}},
        ensure_ascii=False,
        sort_keys=True,
    )


def read_records(conn: sqlite3.Connection) -> PluginRecords | None:
    row = conn.execute("SELECT value FROM server_settings WHERE key=?", (SETTING_KEY,)).fetchone()
    return None if row is None else _parse(str(row[0]))


def read_records_file(db_file: Path) -> PluginRecords | None:
    """Read from the file without a change to it. ``None`` when the file, the table, or the row is absent."""
    if not Path(db_file).is_file():
        return None
    from .db import connect_existing

    conn = connect_existing(db_file, readonly=True)
    try:
        return read_records(conn)
    except sqlite3.OperationalError:
        return None
    finally:
        conn.close()


def _write(conn: sqlite3.Connection, records: PluginRecords, *, updated_by: int | None, now: float) -> None:
    conn.execute(
        "INSERT INTO server_settings (key, value, updated_at, updated_by) VALUES (?, ?, ?, ?) "
        "ON CONFLICT(key) DO UPDATE SET value=excluded.value, updated_at=excluded.updated_at, updated_by=excluded.updated_by",
        (SETTING_KEY, _dump(records), now, updated_by),
    )


def initialize(conn: sqlite3.Connection, project_plugins: list[tuple[str, int, str]], *, now: float | None = None) -> int | None:
    """First start of the plugin store (O7, §11 item 1).

    When the row is absent, write it with an ``enabled`` record for each project plugin ``(id, revision, digest)`` and return the count. When the row exists, change nothing and return ``None``. One transaction.
    """
    ts = time.time() if now is None else now
    conn.execute("BEGIN IMMEDIATE")
    try:
        if read_records(conn) is not None:
            conn.execute("COMMIT")
            return None
        records = {
            plugin_id: PluginRecord(enabled=True, approved_digest=digest, approved_revision=revision, changed_at=ts, changed_by=None)
            for plugin_id, revision, digest in project_plugins
        }
        _write(conn, PluginRecords(initialized_at=ts, records=records), updated_by=None, now=ts)
        conn.execute("COMMIT")
        return len(records)
    except BaseException:
        conn.execute("ROLLBACK")
        raise


def switch(
    conn: sqlite3.Connection,
    plugin_id: str,
    *,
    enabled: bool,
    digest: str | None,
    revision: int | None,
    replace: tuple[str, ...] = (),
    changed_by: int | None,
    now: float | None = None,
) -> PluginRecords:
    """Switch one plugin on or off, and switch the plugins of ``replace`` off, in one transaction (§10.2).

    The caller checks the states and the overlap before the call. The row must exist (the plugin store has started).
    """
    ts = time.time() if now is None else now
    conn.execute("BEGIN IMMEDIATE")
    try:
        current = read_records(conn)
        if current is None:
            raise PluginRecordError("the plugin store has not started on this server")
        records = dict(current.records)
        for other in replace:
            old = records.get(other)
            records[other] = PluginRecord(
                enabled=False,
                approved_digest=old.approved_digest if old else None,
                approved_revision=old.approved_revision if old else None,
                changed_at=ts,
                changed_by=changed_by,
            )
        old = records.get(plugin_id)
        records[plugin_id] = PluginRecord(
            enabled=enabled,
            approved_digest=digest if enabled else (old.approved_digest if old else None),
            approved_revision=revision if enabled else (old.approved_revision if old else None),
            changed_at=ts,
            changed_by=changed_by,
        )
        result = PluginRecords(initialized_at=current.initialized_at, records=records)
        _write(conn, result, updated_by=changed_by, now=ts)
        conn.execute("COMMIT")
        return result
    except BaseException:
        conn.execute("ROLLBACK")
        raise


def delete_record(conn: sqlite3.Connection, plugin_id: str, *, changed_by: int | None, now: float | None = None) -> bool:
    """Remove the record of one plugin. Return ``False`` when it has no record."""
    ts = time.time() if now is None else now
    conn.execute("BEGIN IMMEDIATE")
    try:
        current = read_records(conn)
        if current is None or plugin_id not in current.records:
            conn.execute("COMMIT")
            return False
        records = {k: v for k, v in current.records.items() if k != plugin_id}
        _write(conn, PluginRecords(initialized_at=current.initialized_at, records=records), updated_by=changed_by, now=ts)
        conn.execute("COMMIT")
        return True
    except BaseException:
        conn.execute("ROLLBACK")
        raise
