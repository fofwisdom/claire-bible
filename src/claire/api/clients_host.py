"""Isolated delivery host and bundle builder for ``agent_client`` plugins (design §§4.1-4.3, §9.2)."""

from __future__ import annotations

import asyncio
import gzip
import hashlib
import hmac
import io
import ipaddress
import json
import mimetypes
import sqlite3
import tarfile
import time
import zipfile
from pathlib import Path
from typing import TYPE_CHECKING, Any
from urllib.parse import parse_qs

from starlette.requests import Request
from starlette.responses import PlainTextResponse, Response
from starlette.routing import Route
from starlette.types import ASGIApp, Receive, Scope, Send

from ..plugin_host import PluginFileError
from ..plugin_host.discovery import PluginStatus
from ..plugin_host.files import check_no_executable_files
from ..plugin_host.manifest import CLIENT_ID_RE
from ..plugin_host.service import get_agent_client, snapshot as plugin_snapshot
from ..store import accounts as accounts_store
from ..store import db as dbm
from ..store.secrets import SecretService, get_secret_service
from .prototype_host import registrable_domain

if TYPE_CHECKING:
    from .host_router import HostRoles
    from .security import WebRuntimeConfig

__all__ = [
    "AGENT_CLIENT_DELIVERY_MODES",
    "AGENT_CLIENT_SETTING_KEY",
    "BUNDLE_TOKEN_TTL",
    "DEFAULT_AGENT_CLIENT_DELIVERY_MODE",
    "DEV_CLIENTS_PORT",
    "MARKETPLACE_CLIENT_ID",
    "MARKETPLACE_PATHS",
    "build_bundle_tar_gz",
    "build_bundle_zip",
    "build_manifest_bytes",
    "build_marketplace_bytes",
    "bundle_query_url",
    "clients_app_routes",
    "clients_authority",
    "clients_security_headers",
    "create_bundle_token",
    "create_clients_app",
    "delivery_summary",
    "read_delivery_mode",
    "read_delivery_mode_from_settings",
    "verify_bundle_token",
    "write_delivery_mode",
]

DEV_CLIENTS_PORT = 8770
_CLIENTS_LABEL = "clients"

AGENT_CLIENT_DELIVERY_MODES: tuple[str, str] = ("uri_path", "fqdn")
DEFAULT_AGENT_CLIENT_DELIVERY_MODE = "uri_path"
AGENT_CLIENT_SETTING_KEY = "agent_client_delivery_mode"
BUNDLE_TOKEN_TTL = 60
MARKETPLACE_CLIENT_ID = "_marketplace"
MARKETPLACE_PATHS: tuple[str, str] = (".claude-plugin/marketplace.json", "marketplace.json")

_BUNDLE_TOKEN_PURPOSE = "client_bundle_token"
_CLIENT_CSP = "default-src 'none'; sandbox"
_READONLY_PLUS_SCOPES = frozenset({"owner", "write", "readonly"})
_EXTRA_MIME_TYPES = {
    ".json": "application/json; charset=utf-8",
    ".md": "text/markdown; charset=utf-8",
    ".toml": "application/toml; charset=utf-8",
    ".txt": "text/plain; charset=utf-8",
    ".yaml": "application/yaml; charset=utf-8",
    ".yml": "application/yaml; charset=utf-8",
}


def clients_security_headers() -> dict[str, str]:
    """Return strict response headers for all ``agent_client`` delivery endpoints."""
    return {
        "Cache-Control": "no-store",
        "X-Content-Type-Options": "nosniff",
        "Content-Security-Policy": _CLIENT_CSP,
        "Referrer-Policy": "no-referrer",
        "Cross-Origin-Resource-Policy": "same-origin",
        "X-Frame-Options": "DENY",
        "X-Robots-Tag": "noindex, nofollow",
    }


def clients_authority(app_authority: str, environment: str, *, site_domain: str | None = None) -> str | None:
    """Return the fixed authority for the ``clients`` host role, or None if unavailable."""
    app = str(app_authority or "").strip().lower()
    if environment == "development":
        ip, sep, _port = app.rpartition(":")
        try:
            address = ipaddress.IPv4Address(ip if sep else app)
        except ValueError:
            return None
        if address.is_unspecified:
            return None
        return f"{address}:{DEV_CLIENTS_PORT}"
    if environment != "production":
        return None
    candidate = str(site_domain or "").strip().lower()
    domain = registrable_domain(candidate) if candidate else None
    if domain is None:
        domain = registrable_domain(app)
    return None if domain is None else f"{_CLIENTS_LABEL}.{domain}"


def read_delivery_mode(conn: sqlite3.Connection) -> str:
    """Read ``agent_client_delivery_mode`` from ``server_settings`` or return default."""
    raw = accounts_store.AccountStore(conn).get_setting(AGENT_CLIENT_SETTING_KEY)
    if raw is None or raw == "":
        return DEFAULT_AGENT_CLIENT_DELIVERY_MODE
    if raw not in AGENT_CLIENT_DELIVERY_MODES:
        raise ValueError(f"Invalid {AGENT_CLIENT_SETTING_KEY}: {raw!r}")
    return raw


def _db_file_from_settings(settings: Any) -> Path | None:
    raw_db = getattr(settings, "db_file", None)
    if raw_db:
        return Path(raw_db)
    raw_data = getattr(settings, "data_dir", None)
    if raw_data:
        return Path(raw_data) / "claire.db"
    return None


def read_delivery_mode_from_settings(settings: Any) -> str:
    """Read ``agent_client_delivery_mode`` read-only from settings database."""
    db_file = _db_file_from_settings(settings)
    if db_file is None or not db_file.is_file():
        return DEFAULT_AGENT_CLIENT_DELIVERY_MODE
    conn = dbm.connect_existing(db_file, readonly=True)
    try:
        return read_delivery_mode(conn)
    finally:
        conn.close()


def write_delivery_mode(
    conn: sqlite3.Connection,
    mode: str,
    *,
    updated_by: int | None = None,
    roles: HostRoles | None = None,
) -> str:
    """Validate and persist ``agent_client_delivery_mode`` in ``server_settings``."""
    if mode not in AGENT_CLIENT_DELIVERY_MODES:
        raise ValueError(f"{AGENT_CLIENT_SETTING_KEY} must be one of {AGENT_CLIENT_DELIVERY_MODES}")
    if mode == "fqdn" and roles is not None and roles.clients is None:
        raise ValueError("clients host is unavailable in this environment")
    accounts_store.AccountStore(conn).set_setting(AGENT_CLIENT_SETTING_KEY, mode, updated_by=updated_by)
    return mode


def _resolve_secret_service(settings: Any = None, secret_service: SecretService | None = None) -> SecretService:
    if secret_service is not None:
        return secret_service
    configured = getattr(settings, "secret_service", None)
    if configured is not None:
        return configured
    data_dir = getattr(settings, "data_dir", None)
    if not data_dir:
        db_file = getattr(settings, "db_file", None)
        if db_file:
            data_dir = Path(db_file).parent
    if data_dir:
        return get_secret_service(key_path=Path(data_dir) / "auth" / "master.key")
    return get_secret_service()


def _bundle_token_message(account_id: int, client_id: str, target_file: str, exp: int) -> bytes:
    payload = json.dumps(
        ["client_bundle_v1", int(account_id), client_id, target_file, int(exp)],
        separators=(",", ":"),
        ensure_ascii=False,
    )
    return payload.encode("utf-8")


def _is_valid_token_client_id(client_id: str) -> bool:
    return client_id == MARKETPLACE_CLIENT_ID or bool(CLIENT_ID_RE.fullmatch(client_id))


def create_bundle_token(
    account_id: int,
    client_id: str,
    target_file: str,
    *,
    expires_in: int = BUNDLE_TOKEN_TTL,
    secret_service: SecretService | None = None,
    settings: Any = None,
    now: float | None = None,
) -> str:
    """Mint a 60s HMAC-signed token bound to ``(client_id, target_file, account_id)``."""
    if not _is_valid_token_client_id(client_id):
        raise ValueError(f"Invalid client_id: {client_id!r}")
    if not target_file:
        raise ValueError("target_file must be non-empty")
    current_time = int(now if now is not None else time.time())
    exp = current_time + int(expires_in)
    svc = _resolve_secret_service(settings, secret_service)
    key = svc.derive_hmac_key(_BUNDLE_TOKEN_PURPOSE)
    sig = hmac.new(key, _bundle_token_message(account_id, client_id, target_file, exp), hashlib.sha256).hexdigest()
    return f"{int(account_id)}.{exp}.{sig}"


def verify_bundle_token(
    token: str,
    *,
    client_id: str,
    target_file: str,
    secret_service: SecretService | None = None,
    settings: Any = None,
    now: float | None = None,
) -> int | None:
    """Verify a signed bundle token against ``(client_id, target_file)`` and expiry; return ``account_id`` or None."""
    if not token or not isinstance(token, str):
        return None
    if not _is_valid_token_client_id(client_id) or not target_file:
        return None
    parts = token.split(".")
    if len(parts) != 3:
        return None
    account_str, exp_str, sig = parts
    if not account_str.isdigit() or not exp_str.isdigit() or not sig:
        return None
    account_id = int(account_str)
    exp = int(exp_str)
    if account_id < 0:
        return None
    current_time = int(now if now is not None else time.time())
    if current_time > exp:
        return None
    svc = _resolve_secret_service(settings, secret_service)
    key = svc.derive_hmac_key(_BUNDLE_TOKEN_PURPOSE)
    expected_sig = hmac.new(key, _bundle_token_message(account_id, client_id, target_file, exp), hashlib.sha256).hexdigest()
    if not hmac.compare_digest(sig, expected_sig):
        return None
    return account_id


def bundle_query_url(base_url: str, token: str) -> str:
    """Append a single ``t`` query parameter to ``base_url``."""
    sep = "&" if "?" in base_url else "?"
    return f"{base_url}{sep}t={token}"


def _read_verified_bundle_files(status: PluginStatus) -> dict[str, bytes]:
    manifest = status.manifest
    folder = status.path
    if manifest is None or folder is None:
        raise PluginFileError("agent_client plugin has no folder or manifest")
    check_no_executable_files(folder, manifest.files)
    contents: dict[str, bytes] = {}
    for rel in sorted(manifest.files):
        raw = (folder / rel).read_bytes()
        expected = status.files_sha256.get(rel)
        actual = hashlib.sha256(raw).hexdigest()
        if expected is None or not hmac.compare_digest(actual, expected):
            raise PluginFileError(f"{rel}: digest mismatch against approved snapshot")
        contents[rel] = raw
    return contents


def build_manifest_bytes(status: PluginStatus, *, delivery_mode: str = DEFAULT_AGENT_CLIENT_DELIVERY_MODE) -> bytes:
    """Build deterministic JSON bytes for ``manifest.json`` with verified file SHA-256 digests."""
    manifest = status.manifest
    if manifest is None or not manifest.client_id:
        raise PluginFileError("agent_client plugin has no manifest")
    contents = _read_verified_bundle_files(status)
    payload = {
        "id": manifest.id,
        "client_id": manifest.client_id,
        "revision": manifest.revision,
        "core_commit": manifest.core_commit,
        "api": manifest.api,
        "license": manifest.license,
        "maturity": manifest.maturity,
        "required_scopes": list(manifest.required_scopes),
        "delivery_mode": delivery_mode,
        "digest": status.digest,
        "files": [
            {
                "path": rel,
                "sha256": hashlib.sha256(contents[rel]).hexdigest(),
                "size": len(contents[rel]),
            }
            for rel in sorted(contents)
        ],
    }
    return json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True).encode("utf-8")


def build_bundle_tar_gz(status: PluginStatus) -> bytes:
    """Build a deterministic gzip-compressed tar archive for an enabled ``agent_client`` plugin."""
    manifest = status.manifest
    if manifest is None:
        raise PluginFileError("agent_client plugin has no manifest")
    contents = _read_verified_bundle_files(status)
    buf = io.BytesIO()
    with gzip.GzipFile(fileobj=buf, mode="wb", compresslevel=9, mtime=0) as gz:
        with tarfile.open(fileobj=gz, mode="w") as tf:
            for rel in sorted(contents):
                data = contents[rel]
                info = tarfile.TarInfo(name=rel)
                info.size = len(data)
                info.mtime = 0
                info.mode = 0o644
                info.uid = 0
                info.gid = 0
                info.uname = ""
                info.gname = ""
                tf.addfile(info, io.BytesIO(data))
    return buf.getvalue()


def build_bundle_zip(status: PluginStatus) -> bytes:
    """Build a deterministic ZIP archive for an enabled ``agent_client`` plugin."""
    manifest = status.manifest
    if manifest is None:
        raise PluginFileError("agent_client plugin has no manifest")
    contents = _read_verified_bundle_files(status)
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, mode="w", compression=zipfile.ZIP_DEFLATED, compresslevel=9) as zf:
        for rel in sorted(contents):
            info = zipfile.ZipInfo(filename=rel, date_time=(1980, 1, 1, 0, 0, 0))
            info.compress_type = zipfile.ZIP_DEFLATED
            info.create_system = 3
            info.external_attr = 0o644 << 16
            zf.writestr(info, contents[rel])
    return buf.getvalue()


def _plugin_en_description(status: PluginStatus) -> str:
    manifest = status.manifest
    if manifest is None:
        return ""
    texts = getattr(manifest, "text", None) or {}
    en = texts.get("en")
    if en is not None and getattr(en, "description", ""):
        return str(en.description)
    for entry in texts.values():
        desc = getattr(entry, "description", "")
        if desc:
            return str(desc)
    return manifest.id


def build_marketplace_bytes(
    statuses: list[PluginStatus],
    *,
    base_url: str,
    delivery_mode: str = DEFAULT_AGENT_CLIENT_DELIVERY_MODE,
    account_id: int | None = None,
    settings: Any = None,
    secret_service: SecretService | None = None,
    now: float | None = None,
) -> bytes:
    """Build deterministic Claude-compatible ``marketplace.json`` listing all enabled ``agent_client`` plugins."""
    ordered = sorted(
        (s for s in statuses if s.manifest is not None and s.manifest.client_id and s.path is not None),
        key=lambda s: (str(s.manifest.client_id), str(s.manifest.id)),
    )
    plugins: list[dict[str, Any]] = []
    clean_base = base_url.rstrip("/")
    for status in ordered:
        manifest = status.manifest
        assert manifest is not None and manifest.client_id is not None
        cid = manifest.client_id
        client_base = f"{clean_base}/{cid}"
        zip_bytes = build_bundle_zip(status)
        zip_sha256 = hashlib.sha256(zip_bytes).hexdigest()
        zip_url = _client_url(client_base, "bundle.zip", account_id=account_id, client_id=cid, settings=settings, secret_service=secret_service, now=now)
        manifest_url = _client_url(client_base, "manifest.json", account_id=account_id, client_id=cid, settings=settings, secret_service=secret_service, now=now)
        tar_gz_url = _client_url(client_base, "bundle.tar.gz", account_id=account_id, client_id=cid, settings=settings, secret_service=secret_service, now=now)
        plugins.append(
            {
                "name": manifest.id.replace(".", "-").replace("_", "-"),
                "description": _plugin_en_description(status),
                "version": f"{manifest.revision}.0.0",
                "author": {"name": "Claire Bible"},
                "category": "productivity",
                "license": manifest.license,
                "source": {
                    "source": "archive",
                    "url": zip_url,
                    "sha256": zip_sha256,
                },
                "claire": {
                    "plugin_id": manifest.id,
                    "client_id": cid,
                    "revision": manifest.revision,
                    "required_scopes": list(manifest.required_scopes),
                    "digest": status.digest,
                    "manifest_url": manifest_url,
                    "tar_gz_url": tar_gz_url,
                },
            }
        )
    payload = {
        "$schema": "https://anthropic.com/claude-code/marketplace.schema.json",
        "name": "claire-bible",
        "description": "Claire Bible agent client plugins for OAuth 2.1 MCP connection, staged source extraction, and knowledge graph queries.",
        "owner": {"name": "Claire Bible"},
        "metadata": {
            "delivery_mode": delivery_mode,
            "plugin_count": len(plugins),
        },
        "plugins": plugins,
    }
    return json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True).encode("utf-8")


def _media_type_for(relpath: str) -> str:
    suffix = Path(relpath).suffix.lower()
    if suffix in _EXTRA_MIME_TYPES:
        return _EXTRA_MIME_TYPES[suffix]
    guessed, _encoding = mimetypes.guess_type(relpath)
    if guessed:
        if guessed.startswith("text/") or guessed in {"application/javascript", "image/svg+xml"}:
            return f"{guessed}; charset=utf-8"
        return guessed
    return "application/octet-stream"


def _has_unsafe_raw_path(raw_path: bytes) -> bool:
    if not raw_path:
        return False
    lower = raw_path.lower()
    return (
        b".." in lower
        or b"%2e" in lower
        or b"\\" in lower
        or b"%5c" in lower
        or b"///" in lower
        or b"//" in lower
        or b"\x00" in lower
    )


def _parse_target(prefix: str, path: str, raw_path: bytes) -> tuple[str, str] | None:
    """Return ``(client_id, target_file)`` when path matches a bundle endpoint, else None."""
    if _has_unsafe_raw_path(raw_path):
        return None
    if prefix:
        if not path.startswith(prefix):
            return None
        rest = path[len(prefix) :]
    else:
        rest = path
    if not rest.startswith("/") or len(rest) < 2:
        return None
    tail = rest[1:]
    if tail in MARKETPLACE_PATHS:
        return MARKETPLACE_CLIENT_ID, tail
    client_id, sep, sub = tail.partition("/")
    if not sep or not CLIENT_ID_RE.fullmatch(client_id):
        return None
    if sub in {"manifest.json", "bundle.tar.gz", "bundle.zip"}:
        return client_id, sub
    if sub.startswith("files/"):
        relpath = sub[len("files/") :]
        if not relpath or relpath.startswith("/"):
            return None
        parts = relpath.split("/")
        if any(part in {"", ".", ".."} for part in parts):
            return None
        return client_id, f"files/{relpath}"
    return None


def _account_is_active(conn: sqlite3.Connection, account_id: int) -> bool:
    try:
        account = accounts_store.AccountStore(conn).get(account_id)
    except accounts_store.AccountNotFound:
        return False
    return account.status in accounts_store.CREDENTIAL_ACCEPTING_STATUSES


def _validate_bearer_sync(
    config: WebRuntimeConfig,
    bearer: str,
    *,
    allow_session: bool,
    cross_origin: bool,
) -> tuple[bool, int, int | None]:
    """Validate Bearer token; return ``(allowed, denial_status, account_id)``."""
    from .security import _constant_equal

    if config.owner_token and _constant_equal(bearer, config.owner_token):
        return True, 200, None
    if config.readonly_token and _constant_equal(bearer, config.readonly_token):
        return True, 200, None

    db_file = _db_file_from_settings(config)
    if db_file is None or not Path(db_file).is_file():
        return False, 401, None

    data_dir = config.data_dir or Path(db_file).parent
    scope: str | None = None
    account_id: int | None = None

    try:
        from ..store.token_store import get_token_store

        entry = get_token_store(data_dir=data_dir).validate_api_token(bearer)
        if entry is not None:
            scope = str(entry["scope"])
            account_id = int(entry["account_id"])
    except Exception:
        entry = None

    conn = dbm.connect_existing(db_file, readonly=True)
    try:
        if account_id is None and len(bearer) >= 20:
            oauth_binding = accounts_store.validate_oauth_binding(conn, bearer)
            if oauth_binding is not None:
                scope = oauth_binding.scope
                account_id = oauth_binding.account_id

        if account_id is None and allow_session and not cross_origin and dbm.plausible_session_token(bearer):
            session_binding = accounts_store.validate_session_binding(conn, bearer)
            if session_binding is not None:
                scope = session_binding.scope
                account_id = session_binding.account_id

        if account_id is None or scope is None:
            return False, 401, None
        if not _account_is_active(conn, account_id):
            return False, 401, None
        if scope not in _READONLY_PLUS_SCOPES:
            return False, 403, None
        return True, 200, account_id
    finally:
        conn.close()


def _validate_session_sync(config: WebRuntimeConfig, token: str) -> tuple[bool, int, int | None]:
    if not dbm.plausible_session_token(token):
        return False, 401, None
    db_file = _db_file_from_settings(config)
    if db_file is None or not Path(db_file).is_file():
        return False, 401, None
    conn = dbm.connect_existing(db_file, readonly=True)
    try:
        binding = accounts_store.validate_session_binding(conn, token)
        if binding is None:
            return False, 401, None
        if not _account_is_active(conn, binding.account_id):
            return False, 401, None
        if binding.scope not in _READONLY_PLUS_SCOPES:
            return False, 403, None
        return True, 200, binding.account_id
    finally:
        conn.close()


def _validate_signed_token_sync(
    config: WebRuntimeConfig,
    token: str,
    *,
    client_id: str,
    target_file: str,
    settings: Any,
    secret_service: SecretService | None,
    now: float | None,
) -> int | None:
    account_id = verify_bundle_token(
        token,
        client_id=client_id,
        target_file=target_file,
        secret_service=secret_service,
        settings=settings,
        now=now,
    )
    if account_id is None:
        return None
    db_file = _db_file_from_settings(settings) or _db_file_from_settings(config)
    if db_file is None or not Path(db_file).is_file():
        return None
    conn = dbm.connect_existing(db_file, readonly=True)
    try:
        return account_id if _account_is_active(conn, account_id) else None
    finally:
        conn.close()


async def _authorize_request(
    scope: Scope,
    *,
    config: WebRuntimeConfig,
    settings: Any,
    client_id: str,
    target_file: str,
    allow_session: bool,
    secret_service: SecretService | None,
    now: float | None,
) -> tuple[bool, int, int | None]:
    if config.anonymous_readonly:
        return True, 200, None

    from .security import _parse_bearer, _raw_headers, read_session_cookie

    state = scope.get("state") or {}
    cross_origin = state.get("_claire_origin_kind") == "cross"
    authorization_present, bearer = _parse_bearer(scope)
    if authorization_present:
        if bearer is None:
            return False, 401, None
        return await asyncio.to_thread(
            _validate_bearer_sync,
            config,
            bearer,
            allow_session=allow_session,
            cross_origin=cross_origin,
        )

    query = parse_qs((scope.get("query_string") or b"").decode("latin-1"), keep_blank_values=True)
    t_values = query.get("t")
    if t_values is not None:
        if len(t_values) != 1 or not t_values[0]:
            return False, 401, None
        signed_account_id = await asyncio.to_thread(
            _validate_signed_token_sync,
            config,
            t_values[0],
            client_id=client_id,
            target_file=target_file,
            settings=settings,
            secret_service=secret_service,
            now=now,
        )
        return (True, 200, signed_account_id) if signed_account_id is not None else (False, 401, None)

    if not allow_session or cross_origin:
        return False, 401, None

    cookie_present, cookie_token = read_session_cookie(scope)
    x_session_headers = _raw_headers(scope, b"x-session")
    if cookie_present and x_session_headers:
        return False, 401, None
    if len(x_session_headers) > 1:
        return False, 401, None
    session_token = cookie_token if cookie_present else (x_session_headers[0].decode("latin-1").strip() if x_session_headers else None)
    if not session_token:
        return False, 401, None
    return await asyncio.to_thread(_validate_session_sync, config, session_token)


def _security_plain_response(status_code: int, message: str, *, extra_headers: dict[str, str] | None = None) -> PlainTextResponse:
    headers = clients_security_headers()
    if extra_headers:
        headers.update(extra_headers)
    return PlainTextResponse(message, status_code=status_code, headers=headers)


def _resolve_delivery_base_url(scope: Scope, *, config: WebRuntimeConfig, expected_mode: str, prefix: str) -> str:
    roles = getattr(config, "host_roles", None)
    if roles is not None:
        if expected_mode == "fqdn":
            fqdn_origin = roles.origin("clients")
            if fqdn_origin:
                return fqdn_origin.rstrip("/")
        else:
            app_origin = roles.origin("app")
            if app_origin:
                return f"{app_origin.rstrip('/')}{prefix}"
    scheme = str(scope.get("scheme") or "http")
    host = ""
    for k, v in scope.get("headers") or ():
        if k.lower() == b"host":
            host = v.decode("latin-1").strip()
            break
    if not host:
        host = str(getattr(config, "expected_authority", "") or "127.0.0.1")
    return f"{scheme}://{host}{prefix}"


async def _handle_delivery(
    scope: Scope,
    *,
    settings: Any,
    config: WebRuntimeConfig,
    expected_mode: str,
    prefix: str,
    allow_session: bool,
    secret_service: SecretService | None,
    now: float | None,
) -> Response:
    method = str(scope.get("method", "")).upper()
    if method not in {"GET", "HEAD"}:
        return _security_plain_response(404, "Not Found")

    try:
        active_mode = await asyncio.to_thread(read_delivery_mode_from_settings, settings)
    except ValueError:
        return _security_plain_response(404, "Not Found")
    if active_mode != expected_mode:
        return _security_plain_response(404, "Not Found")

    raw_path = bytes(scope.get("raw_path") or b"")
    parsed = _parse_target(prefix, str(scope.get("path", "")), raw_path)
    if parsed is None:
        return _security_plain_response(404, "Not Found")
    client_id, target_file = parsed

    allowed, denial_status, caller_account_id = await _authorize_request(
        scope,
        config=config,
        settings=settings,
        client_id=client_id,
        target_file=target_file,
        allow_session=allow_session,
        secret_service=secret_service,
        now=now,
    )
    if not allowed:
        if denial_status == 403:
            return _security_plain_response(403, "Forbidden")
        return _security_plain_response(401, "Unauthorized", extra_headers={"WWW-Authenticate": "Bearer"})

    base_url = _resolve_delivery_base_url(scope, config=config, expected_mode=expected_mode, prefix=prefix)
    mint_account = None if config.anonymous_readonly else caller_account_id

    def _build_payload() -> tuple[bytes, str] | None:
        if client_id == MARKETPLACE_CLIENT_ID:
            try:
                snap = plugin_snapshot(settings, fresh=True)
                return (
                    build_marketplace_bytes(
                        snap.agent_clients(),
                        base_url=base_url,
                        delivery_mode=active_mode,
                        account_id=mint_account,
                        settings=settings,
                        secret_service=secret_service,
                        now=now,
                    ),
                    "application/json; charset=utf-8",
                )
            except (PluginFileError, OSError):
                return None
        status = get_agent_client(client_id, settings=settings, fresh=True)
        if status is None or status.manifest is None or status.path is None:
            return None
        try:
            if target_file == "manifest.json":
                return build_manifest_bytes(status, delivery_mode=active_mode), "application/json; charset=utf-8"
            if target_file == "bundle.tar.gz":
                return build_bundle_tar_gz(status), "application/gzip"
            if target_file == "bundle.zip":
                return build_bundle_zip(status), "application/zip"
            relpath = target_file[len("files/") :]
            if relpath not in status.manifest.files:
                return None
            contents = _read_verified_bundle_files(status)
            data = contents.get(relpath)
            if data is None:
                return None
            return data, _media_type_for(relpath)
        except (PluginFileError, OSError):
            return None

    built = await asyncio.to_thread(_build_payload)
    if built is None:
        return _security_plain_response(404, "Not Found")
    payload, media_type = built
    headers = clients_security_headers()
    headers["Content-Length"] = str(len(payload))
    return Response(content=b"" if method == "HEAD" else payload, status_code=200, media_type=media_type, headers=headers)


def create_clients_app(
    settings: Any,
    *,
    config: WebRuntimeConfig | None = None,
    secret_service: SecretService | None = None,
    now: float | None = None,
) -> ASGIApp:
    """Return the isolated ASGI sub-application for the ``clients`` host role."""
    from .security import WebRuntimeConfig

    runtime_cfg = config or WebRuntimeConfig.from_settings(settings)

    async def app(scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            return
        response = await _handle_delivery(
            scope,
            settings=settings,
            config=runtime_cfg,
            expected_mode="fqdn",
            prefix="",
            allow_session=False,
            secret_service=secret_service,
            now=now,
        )
        await response(scope, receive, send)

    return app


def clients_app_routes(
    settings: Any,
    *,
    config: WebRuntimeConfig | None = None,
    secret_service: SecretService | None = None,
    now: float | None = None,
) -> list[Route]:
    """Return app-host ``/clients/{client_id}/...`` routes active only in ``uri_path`` mode."""
    from .security import WebRuntimeConfig

    runtime_cfg = config or WebRuntimeConfig.from_settings(settings)

    async def serve_client_bundle(request: Request) -> Response:
        return await _handle_delivery(
            request.scope,
            settings=settings,
            config=runtime_cfg,
            expected_mode="uri_path",
            prefix="/clients",
            allow_session=True,
            secret_service=secret_service,
            now=now,
        )

    return [
        Route("/clients/.claude-plugin/marketplace.json", serve_client_bundle, methods=["GET", "HEAD"]),
        Route("/clients/marketplace.json", serve_client_bundle, methods=["GET", "HEAD"]),
        Route("/clients/{client_id}/manifest.json", serve_client_bundle, methods=["GET", "HEAD"]),
        Route("/clients/{client_id}/bundle.tar.gz", serve_client_bundle, methods=["GET", "HEAD"]),
        Route("/clients/{client_id}/bundle.zip", serve_client_bundle, methods=["GET", "HEAD"]),
        Route("/clients/{client_id}/files/{relpath:path}", serve_client_bundle, methods=["GET", "HEAD"]),
    ]


def _client_url(base_url: str, rel: str, *, account_id: int | None, client_id: str, settings: Any, secret_service: SecretService | None, now: float | None) -> str:
    url = f"{base_url.rstrip('/')}/{rel}"
    if account_id is None:
        return url
    token = create_bundle_token(
        account_id,
        client_id,
        rel,
        secret_service=secret_service,
        settings=settings,
        now=now,
    )
    return bundle_query_url(url, token)


def delivery_summary(
    conn: sqlite3.Connection,
    settings: Any,
    roles: HostRoles | None,
    *,
    account_id: int | None = None,
    anonymous_readonly: bool | None = None,
    secret_service: SecretService | None = None,
    now: float | None = None,
) -> dict[str, Any]:
    """Build a read-only delivery configuration and resolved bundle URL summary."""
    mode = read_delivery_mode(conn)
    app_origin = roles.origin("app") if roles is not None else None
    fqdn_origin = roles.origin("clients") if roles is not None else None
    fqdn_authority = roles.clients if roles is not None else None
    uri_base_url = f"{app_origin}/clients" if app_origin else "/clients"
    fqdn_base_url = fqdn_origin
    active_base_url = fqdn_base_url if mode == "fqdn" else uri_base_url
    uri_marketplace_url = f"{uri_base_url.rstrip('/')}/.claude-plugin/marketplace.json"
    fqdn_marketplace_url = f"{fqdn_base_url.rstrip('/')}/.claude-plugin/marketplace.json" if fqdn_base_url else None

    if anonymous_readonly is None:
        anonymous_readonly = bool(getattr(settings, "anonymous_readonly", False))
    mint_account_id = None if (anonymous_readonly or account_id is None) else int(account_id)
    marketplace_url = (
        _client_url(
            active_base_url,
            ".claude-plugin/marketplace.json",
            account_id=mint_account_id,
            client_id=MARKETPLACE_CLIENT_ID,
            settings=settings,
            secret_service=secret_service,
            now=now,
        )
        if active_base_url
        else None
    )

    clients_payload: list[dict[str, Any]] = []
    try:
        snap = plugin_snapshot(settings, fresh=True)
        agent_statuses = sorted(
            (s for s in snap.agent_clients() if s.manifest is not None and s.manifest.client_id),
            key=lambda s: (str(s.manifest.client_id), str(s.manifest.id)),
        )
    except Exception:
        agent_statuses = []

    for status in agent_statuses:
        manifest = status.manifest
        if manifest is None or not manifest.client_id:
            continue
        cid = manifest.client_id
        client_base = f"{active_base_url.rstrip('/')}/{cid}" if active_base_url else None
        manifest_url = (
            _client_url(
                client_base,
                "manifest.json",
                account_id=mint_account_id,
                client_id=cid,
                settings=settings,
                secret_service=secret_service,
                now=now,
            )
            if client_base
            else None
        )
        bundle_url = (
            _client_url(
                client_base,
                "bundle.tar.gz",
                account_id=mint_account_id,
                client_id=cid,
                settings=settings,
                secret_service=secret_service,
                now=now,
            )
            if client_base
            else None
        )
        bundle_zip_url = (
            _client_url(
                client_base,
                "bundle.zip",
                account_id=mint_account_id,
                client_id=cid,
                settings=settings,
                secret_service=secret_service,
                now=now,
            )
            if client_base
            else None
        )
        file_entries = []
        for rel in sorted(manifest.files):
            target_rel = f"files/{rel}"
            file_url = (
                _client_url(
                    client_base,
                    target_rel,
                    account_id=mint_account_id,
                    client_id=cid,
                    settings=settings,
                    secret_service=secret_service,
                    now=now,
                )
                if client_base
                else None
            )
            file_entries.append(
                {
                    "path": rel,
                    "sha256": status.files_sha256.get(rel, ""),
                    "url": file_url,
                }
            )
        clients_payload.append(
            {
                "plugin_id": manifest.id,
                "client_id": cid,
                "revision": manifest.revision,
                "license": manifest.license,
                "required_scopes": list(manifest.required_scopes),
                "digest": status.digest,
                "manifest_url": manifest_url,
                "bundle_url": bundle_url,
                "bundle_zip_url": bundle_zip_url,
                "files": file_entries,
            }
        )

    return {
        "mode": mode,
        "fqdn_available": fqdn_origin is not None,
        "clients_authority": fqdn_authority,
        "fqdn_origin": fqdn_origin,
        "uri_path_base_url": uri_base_url,
        "active_base_url": active_base_url,
        "uri_path_marketplace_url": uri_marketplace_url,
        "fqdn_marketplace_url": fqdn_marketplace_url,
        "marketplace_url": marketplace_url,
        "signed_token_ttl_seconds": BUNDLE_TOKEN_TTL,
        "requires_auth": not anonymous_readonly,
        "clients": clients_payload,
    }
