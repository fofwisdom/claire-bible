"""Object storage settings of the server, the storage test, and the migration from local storage (FEDERATION_REMOTE_READING_DESIGN.md Sections 7.3, 9.1.1, 9.2, and 9.3; unit FP4-1).

- The admin settings are one JSON value in ``claire.db`` ``server_settings`` (key ``media_storage``). The secret key is encrypted with ``SecretService`` and the additional authenticated data ``media_storage:s3``. No function of this module gives the secret to a caller, except ``active_config`` and ``target_config`` (for the store of this process).
- The admin settings are the only source of the object storage configuration (/admin/storage).
- The result of the last test and of the last migration run are two more ``server_settings`` values. The console shows them.

Only this module reads and writes these ``server_settings`` keys (store boundary, DATA_STORE_LAYOUT_DESIGN.md Section 3 rule 6).
"""

from __future__ import annotations

import asyncio
import hashlib
import ipaddress
import json
import logging
import os
import secrets as _secrets
import sqlite3
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

from . import db as dbm
from .media import MediaStore, MediaStoreError, S3MediaStore, is_object_storage_key
from .secrets import SecretError, SecretService, get_secret_service

log = logging.getLogger(__name__)

SETTING_KEY = "media_storage"
TEST_RESULT_KEY = "media_storage_test"
MIGRATION_RESULT_KEY = "media_storage_migration"
SECRET_AAD = "media_storage:s3"
MODES = ("local", "s3")
DEFAULT_REGION = "us-east-1"
# Section 9.1.1 rule 5: the defaults of the console form (Garage).
GARAGE_REGION = "garage"
GARAGE_ENDPOINT_PLACEHOLDER = "https://garage.example:3900"
PROBE_PREFIX = "claire-probe"
_TEXT_MAX = {"endpoint": 2048, "bucket": 255, "region": 64, "access_key": 256, "secret_key": 1024, "public_url_base": 2048}


class StorageSettingsError(ValueError):
    """A storage setting is not valid. ``field`` is the name of the setting."""

    def __init__(self, field: str, message: str) -> None:
        super().__init__(message)
        self.field = field


@dataclass(frozen=True, slots=True)
class S3Config:
    """A complete object storage configuration."""

    endpoint: str
    bucket: str
    region: str
    access_key: str
    secret_key: str
    public_url_base: str


# --- Stored admin settings -------------------------------------------------


def _read_json(conn: sqlite3.Connection, key: str) -> dict[str, Any] | None:
    try:
        row = conn.execute("SELECT value FROM server_settings WHERE key=?", (key,)).fetchone()
    except sqlite3.OperationalError:
        return None
    if row is None:
        return None
    try:
        value = json.loads(str(row[0]))
    except ValueError as exc:
        raise RuntimeError(f"server_settings.{key} is not valid JSON") from exc
    if not isinstance(value, dict):
        raise RuntimeError(f"server_settings.{key} has an unexpected shape")
    return value


def _write_json(conn: sqlite3.Connection, key: str, value: dict[str, Any], *, updated_by: int | None, now: float | None = None) -> None:
    conn.execute(
        "INSERT INTO server_settings (key, value, updated_at, updated_by) VALUES (?, ?, ?, ?) "
        "ON CONFLICT(key) DO UPDATE SET value=excluded.value, updated_at=excluded.updated_at, updated_by=excluded.updated_by",
        (key, json.dumps(value, sort_keys=True), time.time() if now is None else now, updated_by),
    )
    conn.commit()


def _stored(conn: sqlite3.Connection) -> dict[str, Any]:
    stored = _read_json(conn, SETTING_KEY) or {}
    return {
        "mode": stored.get("mode") if stored.get("mode") in MODES else "local",
        "endpoint": str(stored.get("endpoint") or ""),
        "bucket": str(stored.get("bucket") or ""),
        "region": str(stored.get("region") or ""),
        "access_key": str(stored.get("access_key") or ""),
        "public_url_base": str(stored.get("public_url_base") or ""),
        "secret": stored.get("secret") if isinstance(stored.get("secret"), dict) else None,
    }


def _decrypt_secret(stored: dict[str, Any], secret_service: SecretService | None) -> str:
    enc = stored.get("secret")
    if not enc:
        return ""
    service = secret_service or get_secret_service()
    return service.decrypt(bytes.fromhex(str(enc["ciphertext"])), bytes.fromhex(str(enc["nonce"])), SECRET_AAD, int(enc.get("key_version", 1))).decode("utf-8")


def _admin_config(conn: sqlite3.Connection, secret_service: SecretService | None) -> S3Config | None:
    """The configuration of the stored fields, or ``None`` when a necessary field is empty. The mode is not part of the check."""
    stored = _stored(conn)
    if not (stored["endpoint"] and stored["bucket"] and stored["access_key"] and stored["secret"]):
        return None
    return S3Config(endpoint=stored["endpoint"], bucket=stored["bucket"], region=stored["region"] or DEFAULT_REGION, access_key=stored["access_key"], secret_key=_decrypt_secret(stored, secret_service), public_url_base=stored["public_url_base"])


def _is_private_host(host: str) -> bool:
    if host.lower() == "localhost" or host.lower().endswith(".localhost"):
        return True
    try:
        address = ipaddress.ip_address(host)
    except ValueError:
        return False
    return address.is_private or address.is_loopback


def _check_url(field: str, value: str, *, allow_private_http: bool) -> str:
    try:
        parts = urlsplit(value)
        host = parts.hostname
        _port = parts.port
    except ValueError as exc:
        raise StorageSettingsError(field, f"{field} is not a URL") from exc
    scheme = parts.scheme.lower()
    if scheme not in ("http", "https") or not host or parts.query or parts.fragment or parts.username or parts.password:
        raise StorageSettingsError(field, f"{field} must be an http or https URL without a query, a fragment, or a credential")
    if scheme == "http" and not (allow_private_http and _is_private_host(host)):
        raise StorageSettingsError(field, f"{field} must use https (http is possible only for a private address in development)")
    return value.rstrip("/")


def validate_update(body: Any, *, allow_private_http: bool = False) -> dict[str, Any]:
    """Check a partial update of the admin settings. Unknown keys and wrong values raise ``StorageSettingsError``. An empty string clears a field; the secret key is cleared only by the key ``clear_secret_key``."""
    if not isinstance(body, dict) or not body:
        raise StorageSettingsError("body", "the body must be a JSON object with one setting or more")
    checked: dict[str, Any] = {}
    for key, value in body.items():
        if key == "mode":
            if value not in MODES:
                raise StorageSettingsError(key, "mode must be local or s3")
            checked[key] = value
            continue
        if key == "clear_secret_key":
            if not isinstance(value, bool):
                raise StorageSettingsError(key, "clear_secret_key must be true or false")
            checked[key] = value
            continue
        if key not in _TEXT_MAX:
            raise StorageSettingsError(str(key), f"unknown setting: {key}")
        if not isinstance(value, str) or len(value) > _TEXT_MAX[key] or any(ord(ch) < 32 or ord(ch) == 127 for ch in value):
            raise StorageSettingsError(key, f"{key} must be a string of at most {_TEXT_MAX[key]} characters without control characters")
        text = value if key == "secret_key" else value.strip()
        if key in ("endpoint", "public_url_base") and text:
            text = _check_url(key, text, allow_private_http=allow_private_http)
        if key == "bucket" and text and not (is_object_storage_key(text) and "/" not in text):
            raise StorageSettingsError(key, "bucket has characters that are not possible in a bucket name")
        if key == "secret_key" and not text:
            raise StorageSettingsError(key, "secret_key must not be empty (use clear_secret_key to remove it)")
        checked[key] = text
    if "secret_key" in checked and checked.get("clear_secret_key"):
        raise StorageSettingsError("clear_secret_key", "secret_key and clear_secret_key cannot be in one update")
    return checked


def set_admin_settings(conn: sqlite3.Connection, update: dict[str, Any], *, secret_service: SecretService | None = None, updated_by: int | None, now: float | None = None) -> None:
    """Apply a checked update and commit. The mode ``s3`` needs all necessary fields; else ``StorageSettingsError``."""
    stored = _stored(conn)
    for key in ("mode", "endpoint", "bucket", "region", "access_key", "public_url_base"):
        if key in update:
            stored[key] = update[key]
    if update.get("clear_secret_key"):
        stored["secret"] = None
    if "secret_key" in update:
        enc = (secret_service or get_secret_service()).encrypt(update["secret_key"], SECRET_AAD)
        stored["secret"] = {"ciphertext": enc.ciphertext.hex(), "nonce": enc.nonce.hex(), "key_version": enc.key_version}
    if stored["mode"] == "s3":
        for key in ("endpoint", "bucket", "access_key"):
            if not stored[key]:
                raise StorageSettingsError(key, f"the mode s3 needs {key}")
        if not stored["secret"]:
            raise StorageSettingsError("secret_key", "the mode s3 needs secret_key")
    _write_json(conn, SETTING_KEY, stored, updated_by=updated_by, now=now)


def settings_view(conn: sqlite3.Connection) -> dict[str, Any]:
    """The settings for the API and the console. The secret key is never in the view: ``secret_key_set`` says whether one is stored (RS7)."""
    stored = _stored(conn)
    return {
        "mode": stored["mode"],
        "endpoint": stored["endpoint"],
        "bucket": stored["bucket"],
        "region": stored["region"],
        "access_key": stored["access_key"],
        "secret_key_set": bool(stored["secret"]),
        "public_url_base": stored["public_url_base"],
        "last_test": _read_json(conn, TEST_RESULT_KEY),
        "last_migration": _read_json(conn, MIGRATION_RESULT_KEY),
    }


# --- Configuration of a process --------------------------------------------


def secret_service_for(settings: Any) -> SecretService:
    """The secret service for the storage secret: the service of the settings, else the master key of the data directory (as the IdP secrets)."""
    service = getattr(settings, "secret_service", None) if settings is not None else None
    if service is not None:
        return service
    data_dir = getattr(settings, "data_dir", None) if settings is not None else None
    return get_secret_service(Path(data_dir) / "auth" / "master.key") if data_dir else get_secret_service()


def _open_claire_db(settings: Any) -> sqlite3.Connection | None:
    db_file = getattr(settings, "db_file", None) if settings is not None else None
    if not db_file or not Path(db_file).is_file():
        return None
    return dbm.connect_existing(Path(db_file), readonly=True)


def _config_from_db(settings: Any, *, need_mode_s3: bool) -> S3Config | None:
    conn = _open_claire_db(settings)
    if conn is None:
        return None
    try:
        if need_mode_s3 and _stored(conn)["mode"] != "s3":
            return None
        return _admin_config(conn, secret_service_for(settings))
    finally:
        conn.close()


def active_config(settings: Any) -> S3Config | None:
    """The object storage configuration that this process uses for its store, or ``None`` for local storage. A stored setting that cannot be read is logged and gives ``None``."""
    try:
        return _config_from_db(settings, need_mode_s3=True)
    except (sqlite3.Error, RuntimeError, SecretError, ValueError, KeyError) as exc:
        log.error("The object storage settings cannot be read; the media store is local storage: %s", exc)
        return None


def target_config(settings: Any) -> S3Config | None:
    """The object storage configuration for the test and the migration: the stored fields with any mode. ``None`` when the configuration is not complete."""
    return _config_from_db(settings, need_mode_s3=False)


def build_s3_store(config: S3Config, *, secret_service: SecretService | None = None) -> S3MediaStore:
    return S3MediaStore(endpoint_url=config.endpoint, bucket=config.bucket, access_key=config.access_key, secret_key=config.secret_key, region=config.region, secret_service=secret_service, public_url_base=config.public_url_base or None)


# --- Test of the object storage (Section 9.2 rule 2) ------------------------


async def probe_store(store: MediaStore, *, now: float | None = None) -> dict[str, Any]:
    """Write, read with a byte range, head, and delete one probe object. The result has the first operation that failed."""
    key = f"{PROBE_PREFIX}/{_secrets.token_hex(16)}.bin"
    data = _secrets.token_bytes(64)
    result: dict[str, Any] = {"ok": False, "failed_operation": None, "detail": "", "tested_at": time.time() if now is None else now}
    written = False

    async def _put() -> str | None:
        await store.put(key, data, "application/octet-stream")
        return None

    async def _get() -> str | None:
        obj = await store.get(key, range_header="bytes=8-23")
        if obj is None:
            return "the probe object was not found"
        return None if obj.body == data[8:24] else "the byte range did not have the expected bytes"

    async def _head() -> str | None:
        found = await store.head(key)
        if found is None:
            return "the probe object was not found"
        return None if found.content_length == len(data) else "the size of the probe object was not correct"

    async def _delete() -> str | None:
        await store.delete(key)
        return None if await store.head(key) is None else "the probe object was there after the delete"

    try:
        for name, step in (("put", _put), ("get", _get), ("head", _head), ("delete", _delete)):
            try:
                problem = await step()
            except (MediaStoreError, ValueError) as exc:
                problem = str(exc) or type(exc).__name__
            if name == "put" and problem is None:
                written = True
            if problem is not None:
                result.update(failed_operation=name, detail=problem[:300])
                return result
        written = False
        result["ok"] = True
        return result
    finally:
        if written:
            try:
                await store.delete(key)
            except (MediaStoreError, ValueError):
                pass


def record_test_result(conn: sqlite3.Connection, result: dict[str, Any], *, updated_by: int | None) -> None:
    _write_json(conn, TEST_RESULT_KEY, result, updated_by=updated_by)


# --- Migration from local storage (Section 9.3) ----------------------------


def local_media_files(data_dir: Path) -> list[tuple[str, Path]]:
    """The local files in the scope of the migration, as ``(object key, path)``: the files of ``data/media`` with their relative path, and the files of ``data/images`` with the key ``images/{name}`` (RT4)."""
    found: list[tuple[str, Path]] = []
    media_dir = Path(data_dir) / "media"
    if media_dir.is_dir():
        for path in sorted(media_dir.rglob("*")):
            if path.is_file() and ".tmp." not in path.name:
                found.append((path.relative_to(media_dir).as_posix(), path))
    images_dir = Path(data_dir) / "images"
    if images_dir.is_dir():
        for path in sorted(images_dir.iterdir()):
            if path.is_file():
                found.append((f"images/{path.name}", path))
    return found


async def migrate_to_object_storage(data_dir: Path, store: MediaStore, *, apply: bool = False, delete_source: bool = False, now: float | None = None) -> dict[str, Any]:
    """Copy the local media into the object store. Without ``apply`` the run changes nothing and only counts.

    For each file: when the bucket has the key with the same SHA-256, skip it; else upload, read back, and compare the SHA-256. The local files are deleted only with ``delete_source`` and only when all files passed. The run can be done again.
    """
    import mimetypes

    files = local_media_files(data_dir)
    result: dict[str, Any] = {"applied": bool(apply), "delete_source": bool(apply and delete_source), "total": len(files), "uploaded": 0, "already_stored": 0, "to_upload": 0, "invalid_keys": 0, "errors": 0, "deleted_sources": 0, "bytes": 0, "error_details": [], "finished_at": 0.0}

    def _error(key: str, message: str) -> None:
        result["errors"] += 1
        if len(result["error_details"]) < 20:
            result["error_details"].append({"key": key, "error": message[:300]})

    passed: list[Path] = []
    for key, path in files:
        if not is_object_storage_key(key):
            result["invalid_keys"] += 1
            _error(key, "the key has characters that are not possible in an object storage key")
            continue
        try:
            data = await asyncio.to_thread(path.read_bytes)
            digest = hashlib.sha256(data).hexdigest()
            existing = await store.get(key)
            if existing is not None and hashlib.sha256(existing.body).hexdigest() == digest:
                result["already_stored"] += 1
                passed.append(path)
                continue
            if not apply:
                result["to_upload"] += 1
                continue
            await store.put(key, data, mimetypes.guess_type(path.name)[0] or "application/octet-stream")
            stored = await store.get(key)
            if stored is None or hashlib.sha256(stored.body).hexdigest() != digest:
                _error(key, "the object that was read back does not have the hash of the local file")
                continue
            result["uploaded"] += 1
            result["bytes"] += len(data)
            passed.append(path)
        except (MediaStoreError, OSError, ValueError) as exc:
            _error(key, str(exc) or type(exc).__name__)
    if apply and delete_source and result["errors"] == 0 and len(passed) == len(files):
        for path in passed:
            try:
                await asyncio.to_thread(path.unlink)
                result["deleted_sources"] += 1
            except OSError as exc:
                _error(path.name, f"the local file was not deleted: {exc}")
    result["finished_at"] = time.time() if now is None else now
    return result


def record_migration_result(conn: sqlite3.Connection, result: dict[str, Any]) -> None:
    _write_json(conn, MIGRATION_RESULT_KEY, result, updated_by=None)
