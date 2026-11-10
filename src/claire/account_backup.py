"""계정 백업과 복원 (유저 단위 기능).

- 백업 생성·목록·다운로드·삭제와 자동 백업 주기는 계정 소유자가 WebUI(``/settings/backups``)에서 한다.
- 복원은 운영자(관리자) 전용이다(``/admin/backups``). 복원은 지식 DB를 바꾸는 파괴적 작업이므로 먼저 내부 checkpoint를 만든다.
- 인스턴스 전체 백업·복원(``./cb-manuscript backup``·``restore``)은 그대로 운영자 도구다. 이 모듈은 그것을 대신하지 않는다.

백업 파일(``data/account_backups/<account_id>/<backup_id>.tar.zst``) 내용:

- ``manifest.json``: 형식 버전, 계정 공개 ID, 지식 DB 스키마 버전·계보, 테마 목록과 행 수, 멤버 파일의 SHA-256.
- ``themes/<theme_id>.db``: 테마 하나의 SQLite 스냅샷. ``themes`` 행과 ``theme_id`` 열이 있는 모든 테이블의 그 테마 행,
  그리고 그 테마 엔티티의 ``entities_fts`` 행을 담는다. 모든 테마를 한 읽기 트랜잭션에서 읽어 서로 일관된다.

담지 않는 것: auth.db(비밀번호·키·봇 토큰), ``master.key``, 다른 계정의 행, ``data/raw``·``data/images`` 파일(문서 본문은 DB에 있다).
"""

from __future__ import annotations

import fcntl
import hashlib
import io
import json
import logging
import os
import re
import secrets
import shutil
import sqlite3
import tarfile
import tempfile
import time
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterator

import zstandard as zstd

from . import ids
from .config import Settings, get_settings
from .store import db as dbm

logger = logging.getLogger(__name__)

ACCOUNT_BACKUP_FORMAT_VERSION = 1
ACCOUNT_BACKUPS_DIRNAME = "account_backups"
BACKUP_SCHEDULES: tuple[str, ...] = ("off", "daily", "weekly")
SCHEDULE_INTERVAL_SECONDS: dict[str, float] = {"daily": 86400.0, "weekly": 7 * 86400.0}
DEFAULT_KEEP = 3
MIN_KEEP = 1
MAX_KEEP = 10
# 수동 백업 연타 방지. 같은 계정은 이 간격 안에 새 수동 백업을 만들 수 없다.
MANUAL_BACKUP_COOLDOWN_SECONDS = 60.0
_BACKUP_ID_RE = re.compile(r"^ab_\d{8}_\d{6}_[0-9a-f]{8}$")
# 테마 행이 아닌데 theme_id 열이 있는 표 가운데 백업하지 않는 표. 없음(모두 계정 데이터다).
_SKIP_THEME_TABLES: frozenset[str] = frozenset()


class AccountBackupError(RuntimeError):
    """백업·복원 실패. 메시지는 사용자에게 보여 줘도 된다(비밀·경로 없음)."""


class AccountBackupConflict(AccountBackupError):
    """복원 대상과 충돌한다(다른 계정 소유 테마·행, 번호 중복 등). 아무것도 바꾸지 않았다."""


@dataclass(frozen=True, slots=True)
class BackupPolicy:
    schedule: str = "off"
    keep: int = DEFAULT_KEEP
    last_run_at: float | None = None
    last_status: str | None = None
    last_error: str | None = None
    updated_at: float | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "schedule": self.schedule,
            "keep": self.keep,
            "last_run_at": _iso(self.last_run_at),
            "last_status": self.last_status,
            "last_error": self.last_error,
            "next_run_at": _iso(self.next_run_at()),
        }

    def next_run_at(self) -> float | None:
        interval = SCHEDULE_INTERVAL_SECONDS.get(self.schedule)
        if interval is None:
            return None
        if self.last_run_at is None:
            return self.updated_at or 0.0
        return self.last_run_at + interval


def _iso(ts: float | None) -> str | None:
    if ts is None:
        return None
    return datetime.fromtimestamp(float(ts), timezone.utc).isoformat()


# --- 경로·등록부 ------------------------------------------------------------------


def backups_root(data_dir: Path | str) -> Path:
    path = Path(data_dir) / ACCOUNT_BACKUPS_DIRNAME
    path.mkdir(parents=True, mode=0o700, exist_ok=True)
    return path


def account_backup_dir(data_dir: Path | str, account_id: int) -> Path:
    path = backups_root(data_dir) / str(int(account_id))
    path.mkdir(parents=True, mode=0o700, exist_ok=True)
    return path


def is_valid_backup_id(value: object) -> bool:
    return isinstance(value, str) and _BACKUP_ID_RE.match(value) is not None


def _archive_path(data_dir: Path | str, account_id: int, backup_id: str) -> Path:
    if not is_valid_backup_id(backup_id):
        raise AccountBackupError("invalid backup id")
    return account_backup_dir(data_dir, account_id) / f"{backup_id}.tar.zst"


def _record_path(data_dir: Path | str, account_id: int, backup_id: str) -> Path:
    if not is_valid_backup_id(backup_id):
        raise AccountBackupError("invalid backup id")
    return account_backup_dir(data_dir, account_id) / f"{backup_id}.json"


def _write_json_atomic(path: Path, value: dict[str, Any]) -> None:
    fd, tmp = tempfile.mkstemp(prefix=".tmp-", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as stream:
            json.dump(value, stream, ensure_ascii=False, separators=(",", ":"))
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())
        os.chmod(tmp, 0o600)
        os.replace(tmp, path)
    except BaseException:
        Path(tmp).unlink(missing_ok=True)
        raise


def _public_record(record: dict[str, Any]) -> dict[str, Any]:
    return {
        "backup_id": record["backup_id"],
        "filename": f"{record['backup_id']}.tar.zst",
        "reason": record.get("reason"),
        "created_at": _iso(record.get("created_at")),
        "size_bytes": record.get("size_bytes"),
        "sha256": record.get("sha256"),
        "themes": record.get("themes", []),
        "schema_version": record.get("schema_version"),
    }


def _read_record(path: Path) -> dict[str, Any] | None:
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    if not isinstance(raw, dict) or not is_valid_backup_id(raw.get("backup_id")):
        return None
    if raw.get("format_version") != ACCOUNT_BACKUP_FORMAT_VERSION:
        return None
    return raw


def _list_records(data_dir: Path | str, account_id: int) -> list[dict[str, Any]]:
    folder = backups_root(data_dir) / str(int(account_id))
    if not folder.is_dir():
        return []
    out = []
    for path in folder.glob("ab_*.json"):
        record = _read_record(path)
        if record is None:
            continue
        if not (folder / f"{record['backup_id']}.tar.zst").is_file():
            continue
        out.append(record)
    out.sort(key=lambda r: float(r.get("created_at") or 0.0), reverse=True)
    return out


def list_account_backups(settings: Settings, *, account_id: int) -> list[dict[str, Any]]:
    """계정의 백업 목록(최신 순). 파일 경로는 싣지 않는다."""
    return [_public_record(r) for r in _list_records(settings.data_dir, account_id)]


def find_account_backup(settings: Settings, *, account_id: int, backup_id: str) -> dict[str, Any] | None:
    """백업 하나. 있으면 공개 필드와 ``filepath``를, 없으면 None."""
    if not is_valid_backup_id(backup_id):
        return None
    record = _read_record(_record_path(settings.data_dir, account_id, backup_id))
    archive = _archive_path(settings.data_dir, account_id, backup_id)
    if record is None or not archive.is_file():
        return None
    return {**_public_record(record), "filepath": str(archive)}


def delete_account_backup(settings: Settings, *, account_id: int, backup_id: str) -> bool:
    if not is_valid_backup_id(backup_id):
        return False
    archive = _archive_path(settings.data_dir, account_id, backup_id)
    record = _record_path(settings.data_dir, account_id, backup_id)
    existed = archive.is_file() or record.is_file()
    archive.unlink(missing_ok=True)
    record.unlink(missing_ok=True)
    return existed


def delete_all_account_backups(data_dir: Path | str, account_id: int) -> int:
    """계정 삭제(정보 초기화) 때 부른다. 지운 백업 수."""
    folder = backups_root(data_dir) / str(int(account_id))
    if not folder.is_dir():
        return 0
    count = len(list(folder.glob("ab_*.tar.zst")))
    shutil.rmtree(folder, ignore_errors=True)
    return count


def prune_account_backups(settings: Settings, *, account_id: int, keep: int) -> int:
    """최신 ``keep``개만 남긴다. 지운 수."""
    removed = 0
    for record in _list_records(settings.data_dir, account_id)[max(MIN_KEEP, int(keep)):]:
        if delete_account_backup(settings, account_id=account_id, backup_id=record["backup_id"]):
            removed += 1
    return removed


@contextmanager
def _account_lock(data_dir: Path | str, account_id: int) -> Iterator[None]:
    """같은 계정의 백업·복원을 한 번에 하나만 한다(프로세스 사이 포함)."""
    lock_path = account_backup_dir(data_dir, account_id) / ".lock"
    with open(lock_path, "a+") as handle:
        try:
            fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise AccountBackupError("another backup or restore of this account is running") from None
        try:
            yield
        finally:
            fcntl.flock(handle.fileno(), fcntl.LOCK_UN)


# --- 스키마 헬퍼 ---------------------------------------------------------------------


def _theme_tables(conn: sqlite3.Connection, schema: str = "main") -> list[str]:
    """``theme_id`` 열이 있는 일반 테이블(``themes`` 제외)."""
    names = [
        str(r[0])
        for r in conn.execute(
            f"SELECT name FROM {schema}.sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%' ORDER BY name"
        )
    ]
    out = []
    for name in names:
        if name in ("themes", "entities_fts") or name.startswith("entities_fts_") or name in _SKIP_THEME_TABLES:
            continue
        cols = {str(r[1]) for r in conn.execute(f'PRAGMA {schema}.table_info("{name}")')}
        if "theme_id" in cols:
            out.append(name)
    return out


def _columns(conn: sqlite3.Connection, table: str, schema: str = "main") -> list[str]:
    return [str(r[1]) for r in conn.execute(f'PRAGMA {schema}.table_info("{table}")')]


def _pk_columns(conn: sqlite3.Connection, table: str, schema: str = "main") -> list[str]:
    rows = sorted((int(r[5]), str(r[1])) for r in conn.execute(f'PRAGMA {schema}.table_info("{table}")') if int(r[5]) > 0)
    return [name for _, name in rows]


def _quote(name: str) -> str:
    return '"' + name.replace('"', '""') + '"'


def _table_sql(conn: sqlite3.Connection, table: str) -> str:
    row = conn.execute("SELECT sql FROM sqlite_master WHERE type='table' AND name=?", (table,)).fetchone()
    if row is None or not row[0]:
        raise AccountBackupError(f"table {table} has no schema")
    return str(row[0])


# --- 생성 ------------------------------------------------------------------------


def _sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as stream:
        while chunk := stream.read(1 << 16):
            h.update(chunk)
    return h.hexdigest()


def _add_bytes(tar: tarfile.TarFile, arcname: str, data: bytes) -> None:
    info = tarfile.TarInfo(name=arcname)
    info.size = len(data)
    info.mtime = int(time.time())
    info.mode = 0o600
    tar.addfile(info, io.BytesIO(data))


def _snapshot_theme(src: sqlite3.Connection, theme_id: int, dest_path: Path) -> dict[str, int]:
    """열린 읽기 트랜잭션 안에서 테마 하나를 ``dest_path``로 복사한다. 표별 행 수를 돌려준다."""
    dest = sqlite3.connect(str(dest_path))
    counts: dict[str, int] = {}
    try:
        dest.execute(_table_sql(src, "themes"))
        cols = _columns(src, "themes")
        row = src.execute(f"SELECT {', '.join(_quote(c) for c in cols)} FROM themes WHERE id=?", (theme_id,)).fetchone()
        if row is None:
            raise AccountBackupError("theme disappeared during backup")
        dest.execute(f"INSERT INTO themes ({', '.join(_quote(c) for c in cols)}) VALUES ({', '.join('?' for _ in cols)})", tuple(row))
        for table in _theme_tables(src):
            dest.execute(_table_sql(src, table))
            tcols = _columns(src, table)
            col_sql = ", ".join(_quote(c) for c in tcols)
            rows = src.execute(f"SELECT {col_sql} FROM {_quote(table)} WHERE theme_id=?", (theme_id,))
            n = 0
            for batch in iter(lambda: rows.fetchmany(500), []):
                dest.executemany(f"INSERT INTO {_quote(table)} ({col_sql}) VALUES ({', '.join('?' for _ in tcols)})", [tuple(r) for r in batch])
                n += len(batch)
            counts[table] = n
        has_fts = src.execute("SELECT 1 FROM sqlite_master WHERE name='entities_fts'").fetchone()
        if has_fts:
            dest.execute("CREATE TABLE entities_fts (entity_id TEXT, name TEXT, body TEXT)")
            fts_rows = src.execute(
                "SELECT entity_id, name, body FROM entities_fts WHERE entity_id IN (SELECT id FROM entities WHERE theme_id=?)",
                (theme_id,),
            ).fetchall()
            dest.executemany("INSERT INTO entities_fts VALUES (?, ?, ?)", [tuple(r) for r in fts_rows])
            counts["entities_fts"] = len(fts_rows)
        dest.commit()
    finally:
        dest.close()
    return counts


def _account_identity(conn: sqlite3.Connection, account_id: int) -> dict[str, Any]:
    from .store import accounts as accounts_store

    account = accounts_store.AccountStore(conn).get(account_id)
    return {
        "public_id": str(account.public_id),
        "username": account.username,
        "display_name": account.display_name,
        "role": account.role,
    }


def create_account_backup(
    settings: Settings | None = None,
    *,
    account_id: int,
    reason: str = "manual",
    now_epoch: float | None = None,
) -> dict[str, Any]:
    """계정 소유 테마 전부를 백업한다. 공개 레코드를 돌려준다.

    ``reason``은 ``manual`` 또는 ``scheduled``. 수동 백업은 ``MANUAL_BACKUP_COOLDOWN_SECONDS`` 간격 제한이 있다.
    생성 뒤 계정 정책의 ``keep``을 넘는 오래된 백업을 지운다.
    """
    if reason not in ("manual", "scheduled"):
        raise ValueError("invalid backup reason")
    s = settings or get_settings()
    now = time.time() if now_epoch is None else now_epoch
    with _account_lock(s.data_dir, account_id):
        existing = _list_records(s.data_dir, account_id)
        if reason == "manual" and existing and now - float(existing[0].get("created_at") or 0.0) < MANUAL_BACKUP_COOLDOWN_SECONDS:
            raise AccountBackupError("a backup was created less than a minute ago")
        stamp = datetime.fromtimestamp(now, timezone.utc).strftime("%Y%m%d_%H%M%S")
        backup_id = f"ab_{stamp}_{secrets.token_hex(4)}"
        archive = _archive_path(s.data_dir, account_id, backup_id)

        src = sqlite3.connect(Path(s.db_file).resolve().as_uri() + "?mode=ro", uri=True, timeout=10.0)
        src.row_factory = sqlite3.Row
        themes_meta: list[dict[str, Any]] = []
        files: dict[str, dict[str, Any]] = {}
        try:
            with tempfile.TemporaryDirectory(dir=archive.parent, prefix=".work-") as work_str:
                work = Path(work_str)
                # 모든 테마를 한 읽기 트랜잭션에서 읽는다(WAL 스냅샷).
                src.execute("BEGIN")
                try:
                    identity = _account_identity(src, account_id)
                    schema_version = dbm.stored_schema_version(src)
                    lineage_row = src.execute("SELECT value FROM meta WHERE key='schema_lineage'").fetchone()
                    theme_rows = src.execute(
                        "SELECT id, number, label FROM themes WHERE account_id=? ORDER BY id", (account_id,)
                    ).fetchall()
                    if not theme_rows:
                        raise AccountBackupError("this account owns no theme to back up")
                    for row in theme_rows:
                        theme_id = int(row["id"])
                        member = f"themes/{theme_id}.db"
                        path = work / f"{theme_id}.db"
                        counts = _snapshot_theme(src, theme_id, path)
                        files[member] = {"sha256": _sha256_file(path), "size": path.stat().st_size}
                        number = row["number"]
                        themes_meta.append(
                            {
                                "theme_id": theme_id,
                                "theme": ids.format_public_id(number) if number is not None else str(theme_id),
                                "label": str(row["label"]),
                                "counts": counts,
                            }
                        )
                finally:
                    src.rollback()
                manifest = {
                    "format_version": ACCOUNT_BACKUP_FORMAT_VERSION,
                    "backup_id": backup_id,
                    "reason": reason,
                    "created_at": _iso(now),
                    "created_at_epoch": now,
                    "account": identity,
                    "schema_version": schema_version,
                    "schema_lineage": None if lineage_row is None else str(lineage_row[0]),
                    "themes": themes_meta,
                    "files": files,
                }
                tmp_archive = archive.with_name(f".{archive.name}.tmp")
                try:
                    with open(tmp_archive, "wb") as raw_out:
                        with zstd.ZstdCompressor(level=10).stream_writer(raw_out) as compressed:
                            with tarfile.open(fileobj=compressed, mode="w|") as tar:
                                _add_bytes(tar, "manifest.json", json.dumps(manifest, ensure_ascii=False, indent=2).encode("utf-8"))
                                for member in files:
                                    tar.add(str(work / Path(member).name), arcname=member, recursive=False)
                    os.chmod(tmp_archive, 0o600)
                    os.replace(tmp_archive, archive)
                except BaseException:
                    tmp_archive.unlink(missing_ok=True)
                    raise
        finally:
            src.close()

        record = {
            "format_version": ACCOUNT_BACKUP_FORMAT_VERSION,
            "backup_id": backup_id,
            "account_id": int(account_id),
            "reason": reason,
            "created_at": now,
            "size_bytes": archive.stat().st_size,
            "sha256": _sha256_file(archive),
            "schema_version": manifest["schema_version"],
            "themes": [{"theme": t["theme"], "label": t["label"]} for t in themes_meta],
        }
        _write_json_atomic(_record_path(s.data_dir, account_id, backup_id), record)
        policy = load_policy(s, account_id=account_id)
        prune_account_backups(s, account_id=account_id, keep=policy.keep)
        return _public_record(record)


# --- 정책 (auth.db account_backup_policies) ---------------------------------------


def _auth_conn(settings: Settings) -> sqlite3.Connection:
    from .store import auth_db

    return auth_db.connect_auth_db(auth_db.auth_db_path(settings.data_dir))


def load_policy(settings: Settings, *, account_id: int) -> BackupPolicy:
    conn = _auth_conn(settings)
    try:
        row = conn.execute(
            "SELECT schedule, keep, last_run_at, last_status, last_error, updated_at FROM account_backup_policies WHERE account_id=?",
            (account_id,),
        ).fetchone()
    finally:
        conn.close()
    if row is None:
        return BackupPolicy()
    return BackupPolicy(
        schedule=str(row[0]),
        keep=int(row[1]),
        last_run_at=None if row[2] is None else float(row[2]),
        last_status=None if row[3] is None else str(row[3]),
        last_error=None if row[4] is None else str(row[4]),
        updated_at=None if row[5] is None else float(row[5]),
    )


def save_policy(settings: Settings, *, account_id: int, schedule: str, keep: int, now: float | None = None) -> BackupPolicy:
    if schedule not in BACKUP_SCHEDULES:
        raise ValueError("invalid schedule")
    if not isinstance(keep, int) or isinstance(keep, bool) or not (MIN_KEEP <= keep <= MAX_KEEP):
        raise ValueError("invalid keep")
    ts = time.time() if now is None else now
    conn = _auth_conn(settings)
    try:
        with conn:
            conn.execute(
                "INSERT INTO account_backup_policies (account_id, schedule, keep, updated_at) VALUES (?, ?, ?, ?) "
                "ON CONFLICT(account_id) DO UPDATE SET schedule=excluded.schedule, keep=excluded.keep, updated_at=excluded.updated_at",
                (account_id, schedule, keep, ts),
            )
    finally:
        conn.close()
    prune_account_backups(settings, account_id=account_id, keep=keep)
    return load_policy(settings, account_id=account_id)


def _record_run(settings: Settings, account_id: int, *, status: str, error: str | None, now: float) -> None:
    conn = _auth_conn(settings)
    try:
        with conn:
            conn.execute(
                "UPDATE account_backup_policies SET last_run_at=?, last_status=?, last_error=? WHERE account_id=?",
                (now, status, None if error is None else error[:300], account_id),
            )
    finally:
        conn.close()


def run_due_backups(settings: Settings | None = None, *, now_epoch: float | None = None) -> list[dict[str, Any]]:
    """자동 백업 주기가 된 계정을 백업한다. 결과 목록 ``[{account_id, status, backup_id|error}]``.

    활성(``active``) 계정만 한다. 한 계정의 실패는 다른 계정을 막지 않는다.
    """
    s = settings or get_settings()
    now = time.time() if now_epoch is None else now_epoch
    conn = _auth_conn(s)
    try:
        rows = conn.execute(
            "SELECT account_id, schedule, keep, last_run_at, updated_at FROM account_backup_policies WHERE schedule<>'off'"
        ).fetchall()
    finally:
        conn.close()
    if not rows:
        return []
    active = _active_account_ids(s)
    results = []
    for row in rows:
        account_id = int(row[0])
        policy = BackupPolicy(schedule=str(row[1]), keep=int(row[2]), last_run_at=row[3], updated_at=row[4])
        due = policy.next_run_at()
        if account_id not in active or due is None or due > now:
            continue
        try:
            record = create_account_backup(s, account_id=account_id, reason="scheduled", now_epoch=now)
        except AccountBackupError as exc:
            _record_run(s, account_id, status="error", error=str(exc), now=now)
            results.append({"account_id": account_id, "status": "error", "error": str(exc)})
            continue
        except Exception as exc:  # noqa: BLE001
            logger.exception("scheduled account backup failed for account %s", account_id)
            _record_run(s, account_id, status="error", error=type(exc).__name__, now=now)
            results.append({"account_id": account_id, "status": "error", "error": type(exc).__name__})
            continue
        _record_run(s, account_id, status="ok", error=None, now=now)
        results.append({"account_id": account_id, "status": "ok", "backup_id": record["backup_id"]})
    return results


def _active_account_ids(settings: Settings) -> set[int]:
    conn = dbm.connect_existing(settings.db_file, readonly=True)
    try:
        return {int(r[0]) for r in conn.execute("SELECT id FROM accounts WHERE status='active'")}
    finally:
        conn.close()


# --- 복원 (운영자 전용) -----------------------------------------------------------------


def _safe_extract(archive: Path, dest: Path) -> dict[str, Any]:
    """manifest에 적힌 일반 파일만 꺼낸다. 경로 이탈·링크·여분 파일·해시 불일치는 거부한다."""
    allowed: dict[str, Path] = {}
    manifest: dict[str, Any] | None = None
    dctx = zstd.ZstdDecompressor()
    with open(archive, "rb") as raw_in, dctx.stream_reader(raw_in) as reader, tarfile.open(fileobj=reader, mode="r|") as tar:
        for member in tar:
            name = member.name
            if not member.isfile() or name.startswith("/") or ".." in Path(name).parts:
                raise AccountBackupError("backup archive contains an unsafe member")
            data = tar.extractfile(member)
            if data is None:
                raise AccountBackupError("backup archive member could not be read")
            if name == "manifest.json":
                manifest = json.loads(data.read().decode("utf-8"))
                continue
            if not re.fullmatch(r"themes/\d+\.db", name):
                raise AccountBackupError("backup archive contains an unexpected member")
            target = dest / Path(name).name
            with open(target, "wb") as out:
                shutil.copyfileobj(data, out)
            allowed[name] = target
    if not isinstance(manifest, dict) or manifest.get("format_version") != ACCOUNT_BACKUP_FORMAT_VERSION:
        raise AccountBackupError("backup manifest is missing or has an unsupported format")
    files = manifest.get("files")
    if not isinstance(files, dict) or set(files) != set(allowed):
        raise AccountBackupError("backup files do not match the manifest")
    for name, meta in files.items():
        if not isinstance(meta, dict) or meta.get("sha256") != _sha256_file(allowed[name]):
            raise AccountBackupError("backup file hash does not match the manifest")
    return manifest


def restore_account_backup(
    settings: Settings | None = None,
    *,
    account_id: int,
    backup_id: str,
    actor_account_id: int,
    now_epoch: float | None = None,
) -> dict[str, Any]:
    """운영자 전용. 계정 백업의 테마들을 그 계정에 되돌린다.

    - 백업에 있는 테마만 바꾼다. 백업 뒤에 만든 테마는 그대로 둔다.
    - 테마 행이 다른 계정 소유이거나, 테마 밖 행과 기본 키가 겹치면 ``AccountBackupConflict``로 아무것도 바꾸지 않는다.
    - 바꾸기 전에 지식 DB 전체 checkpoint(``data/checkpoints/``)를 만든다.
    - 백업 스키마가 현재 스키마보다 새것이거나 계보가 다르면 거부한다.
    """
    s = settings or get_settings()
    now = time.time() if now_epoch is None else now_epoch
    found = find_account_backup(s, account_id=account_id, backup_id=backup_id)
    if found is None:
        raise AccountBackupError("backup not found")
    archive = Path(found["filepath"])
    with _account_lock(s.data_dir, account_id), tempfile.TemporaryDirectory(dir=archive.parent, prefix=".restore-") as work_str:
        work = Path(work_str)
        manifest = _safe_extract(archive, work)
        if manifest.get("schema_lineage") not in (None, dbm.SCHEMA_LINEAGE):
            raise AccountBackupError("backup comes from a different schema lineage")
        backup_version = manifest.get("schema_version")
        if isinstance(backup_version, int) and backup_version > dbm.SCHEMA_VERSION:
            raise AccountBackupError("backup schema is newer than this server")

        checkpoint_dir = Path(s.data_dir) / "checkpoints"
        stamp = datetime.fromtimestamp(now, timezone.utc).strftime("%Y%m%d_%H%M%S")
        checkpoint = dbm.checkpoint_database(s.db_file, checkpoint_dir / f"account-restore-{int(account_id)}-{stamp}.db")

        conn = sqlite3.connect(str(s.db_file), timeout=30.0, isolation_level=None)
        restored: list[dict[str, Any]] = []
        try:
            conn.execute("PRAGMA busy_timeout=30000")
            conn.execute("PRAGMA foreign_keys=OFF")
            account_row = conn.execute("SELECT status FROM accounts WHERE id=?", (account_id,)).fetchone()
            if account_row is None or str(account_row[0]) == "purged":
                raise AccountBackupError("account does not exist")
            # SQLite는 트랜잭션 안에서 ATTACH·DETACH를 할 수 없다. 스냅샷을 먼저 붙이고 끝난 뒤 뗀다.
            theme_ids = [int(t["theme_id"]) for t in manifest.get("themes", [])]
            attached: list[str] = []
            try:
                for index, theme_id in enumerate(theme_ids):
                    alias = f"bk{index}"
                    conn.execute(f"ATTACH DATABASE ? AS {alias}", (f"{(work / f'{theme_id}.db').resolve().as_uri()}?mode=ro",))
                    attached.append(alias)
                conn.execute("BEGIN IMMEDIATE")
                try:
                    for alias, theme_id in zip(attached, theme_ids):
                        restored.append(_restore_theme(conn, alias, theme_id=theme_id, account_id=account_id))
                    violations = conn.execute("PRAGMA foreign_key_check").fetchall()
                    if violations:
                        raise AccountBackupConflict(f"restore would break {len(violations)} foreign key references")
                    conn.execute("COMMIT")
                except BaseException:
                    conn.execute("ROLLBACK")
                    raise
            finally:
                for alias in attached:
                    conn.execute(f"DETACH DATABASE {alias}")
        finally:
            try:
                conn.execute("PRAGMA foreign_keys=ON")
            finally:
                conn.close()

    try:
        from .store.theme import get_theme_manager

        get_theme_manager(s).reload()
    except Exception:  # noqa: BLE001
        logger.warning("theme manager reload after account restore failed")
    _audit_restore(s, actor_account_id=actor_account_id, account_id=account_id, backup_id=backup_id, themes=restored)
    return {
        "backup_id": backup_id,
        "restored_themes": restored,
        "checkpoint": checkpoint.name,
    }


def _restore_theme(conn: sqlite3.Connection, bk: str, *, theme_id: int, account_id: int) -> dict[str, Any]:
    """열린 쓰기 트랜잭션 안에서 테마 하나를 스냅샷 스키마 ``bk``에서 되돌린다."""
    backup_theme = conn.execute(f"SELECT id FROM {bk}.themes WHERE id=?", (theme_id,)).fetchone()
    if backup_theme is None:
        raise AccountBackupError("backup theme snapshot is incomplete")
    current = conn.execute("SELECT account_id FROM main.themes WHERE id=?", (theme_id,)).fetchone()
    if current is not None and current[0] is not None and int(current[0]) != int(account_id):
        raise AccountBackupConflict(f"theme {theme_id} now belongs to another account")

    main_tables = set(_theme_tables(conn, "main"))
    backup_tables = [t for t in _theme_tables(conn, bk) if t in main_tables]
    # 테마 밖 행과 기본 키가 겹치면 거부한다(문서·엔티티 ID는 전역 키다).
    for table in backup_tables:
        pk = _pk_columns(conn, table, "main")
        if not pk or "theme_id" in pk:
            continue
        on = " AND ".join(f"m.{_quote(c)} = b.{_quote(c)}" for c in pk)
        hit = conn.execute(
            f"SELECT COUNT(*) FROM main.{_quote(table)} m JOIN {bk}.{_quote(table)} b ON {on} WHERE m.theme_id <> ?",
            (theme_id,),
        ).fetchone()[0]
        if hit:
            raise AccountBackupConflict(f"{hit} rows of {table} collide with another theme")

    has_fts = conn.execute("SELECT 1 FROM main.sqlite_master WHERE name='entities_fts'").fetchone() is not None
    backup_has_fts = conn.execute(f"SELECT 1 FROM {bk}.sqlite_master WHERE name='entities_fts'").fetchone() is not None
    if has_fts:
        # 지금 테마 엔티티의 색인 행과, 엔티티 행 없이 남은(고아) 색인 행 가운데 백업이 되돌릴 엔티티의 행을 지운다.
        conn.execute("DELETE FROM main.entities_fts WHERE entity_id IN (SELECT id FROM main.entities WHERE theme_id=?)", (theme_id,))
        if backup_has_fts:
            conn.execute(f"DELETE FROM main.entities_fts WHERE entity_id IN (SELECT entity_id FROM {bk}.entities_fts)")
    for table in sorted(main_tables):
        conn.execute(f"DELETE FROM main.{_quote(table)} WHERE theme_id=?", (theme_id,))

    theme_cols = [c for c in _columns(conn, "themes", bk) if c in set(_columns(conn, "themes", "main"))]
    col_sql = ", ".join(_quote(c) for c in theme_cols)
    conn.execute(f"DELETE FROM main.themes WHERE id=?", (theme_id,))
    try:
        conn.execute(f"INSERT INTO main.themes ({col_sql}) SELECT {col_sql} FROM {bk}.themes WHERE id=?", (theme_id,))
    except sqlite3.IntegrityError as exc:
        raise AccountBackupConflict(f"theme {theme_id} conflicts with an existing theme ({exc})") from None
    conn.execute("UPDATE main.themes SET account_id=? WHERE id=?", (account_id, theme_id))
    # 기본 테마는 계정에 하나다. 다른 기본 테마가 이미 있으면 되돌린 테마를 기본에서 뺀다.
    other_default = conn.execute(
        "SELECT 1 FROM main.themes WHERE account_id=? AND is_default=1 AND id<>?", (account_id, theme_id)
    ).fetchone()
    if other_default is not None:
        conn.execute("UPDATE main.themes SET is_default=0 WHERE id=?", (theme_id,))

    counts: dict[str, int] = {}
    for table in backup_tables:
        cols = [c for c in _columns(conn, table, bk) if c in set(_columns(conn, table, "main"))]
        tcols = ", ".join(_quote(c) for c in cols)
        try:
            cur = conn.execute(f"INSERT INTO main.{_quote(table)} ({tcols}) SELECT {tcols} FROM {bk}.{_quote(table)}")
        except sqlite3.IntegrityError as exc:
            raise AccountBackupConflict(f"rows of {table} conflict with existing data ({exc})") from None
        counts[table] = int(cur.rowcount or 0)
    if has_fts and backup_has_fts:
        conn.execute(f"INSERT INTO main.entities_fts (entity_id, name, body) SELECT entity_id, name, body FROM {bk}.entities_fts")
    label = conn.execute("SELECT label, number FROM main.themes WHERE id=?", (theme_id,)).fetchone()
    return {
        "theme_id": theme_id,
        "theme": ids.format_public_id(label[1]) if label[1] is not None else str(theme_id),
        "label": str(label[0]),
        "counts": counts,
    }


def _audit_restore(settings: Settings, *, actor_account_id: int, account_id: int, backup_id: str, themes: list[dict[str, Any]]) -> None:
    from .store import auth_db

    conn = _auth_conn(settings)
    try:
        auth_db.record_admin_audit(
            conn,
            actor_account_id=actor_account_id,
            action="account.backup_restore",
            target_account_id=account_id,
            detail={"backup_id": backup_id, "themes": [t["theme"] for t in themes]},
        )
    finally:
        conn.close()


def list_backups_for_admin(settings: Settings) -> list[dict[str, Any]]:
    """운영자 복원 화면용. 계정별 백업 목록. 백업 내용은 읽지 않는다."""
    from .store import accounts as accounts_store

    root = backups_root(settings.data_dir)
    conn = dbm.connect_existing(settings.db_file, readonly=True)
    try:
        store = accounts_store.AccountStore(conn)
        out = []
        for folder in sorted(root.iterdir()):
            if not folder.is_dir() or not folder.name.isdigit():
                continue
            account_id = int(folder.name)
            records = _list_records(settings.data_dir, account_id)
            if not records:
                continue
            try:
                account = store.get(account_id)
                who = {"public_id": str(account.public_id), "username": account.username, "display_name": account.display_name, "status": account.status}
            except Exception:  # noqa: BLE001
                who = {"public_id": None, "username": None, "display_name": "", "status": "unknown"}
            out.append({"account_id": account_id, "account": who, "backups": [_public_record(r) for r in records]})
        return out
    finally:
        conn.close()
