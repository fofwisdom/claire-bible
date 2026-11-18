"""Federation readiness checks GF1 to GF17 (Fedify sidecar design Section 12, KNOWLEDGE_FEDERATION_DESIGN.md Section 14.1, units FP1-6 and KF6).

The admin can set the server setting ``federation`` to ``on`` only when all applicable required checks pass (GF1 to GF7 and GF8 to GF13). ``POST /api/admin/federation`` and ``claire server check --federation`` use ``check_federation_readiness``.

The checks do not change data. GF4 and GF12 do not make a key file. GF14 and GF15 read actor documents through the public origin, which can make the keys of those actors on a first read. The HTTP checks run at the same time with a 20-second limit and trust the system trust store.
"""

from __future__ import annotations

import json
import math
import os
import sqlite3
import ssl
import stat
import time
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor, wait
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Literal

import httpx

from .. import auto_contribution, contributions, ids, theme_judgement
from ..api import federation_routes
from ..api.federation_internal import SERVICE_KEY_BYTES, service_key_path
from ..api.security import WebRuntimeConfig
from ..knowledge_federation import actors as kf_actors
from ..knowledge_federation import vocabulary
from ..store import accounts as accounts_store
from ..store import actor_keys, auth_db
from ..store import db as dbm
from ..store.secrets import SecretError, SecretService

__all__ = [
    "FederationReadinessItem",
    "FederationReadinessReport",
    "GF10_INBOX_BODY_BYTES",
    "HEARTBEAT_MAX_AGE_SECONDS",
    "HttpResponse",
    "LOOP_MAX_AGE_SECONDS",
    "METADATA_FETCH_TIMEOUT_SECONDS",
    "METADATA_MAX_BYTES",
    "METADATA_PATH",
    "MetadataFetch",
    "PublicRequest",
    "READINESS_RESPONSE_MAX_BYTES",
    "REQUEST_CHECKS_TIMEOUT_SECONDS",
    "ResponseTooLargeError",
    "check_federation_readiness",
    "fetch_metadata",
    "heartbeat_is_recent",
    "master_key_path",
    "request_public_origin",
]

# The sidecar sends a heartbeat about each 30 seconds. The limit permits three lost heartbeats.
HEARTBEAT_MAX_AGE_SECONDS = 120.0
# The longest step of a sidecar knowledge loop is 240 seconds. The check permits 300 seconds.
LOOP_MAX_AGE_SECONDS = 300.0
METADATA_PATH = "/.well-known/oauth-authorization-server"
METADATA_FETCH_TIMEOUT_SECONDS = 5.0
METADATA_MAX_BYTES = 65536
READINESS_RESPONSE_MAX_BYTES = 256 * 1024
REQUEST_CHECKS_TIMEOUT_SECONDS = 20.0
SERVICE_KEY_MODE = 0o600
GF10_INBOX_BODY_BYTES = federation_routes.INBOX_BODY_LIMIT_BYTES - 8192
_SPECIAL_USE_SUFFIXES = ("test", "example", "invalid", "localhost", "local")
_KNOWLEDGE_LOOPS = ("knowledge_reads", "knowledge_exchange")

MetadataFetch = Callable[[str], tuple[int, bytes]]
PublicRequest = Callable[..., "HttpResponse"]


class ResponseTooLargeError(ValueError):
    """The response body was larger than the readiness limit."""


@dataclass(frozen=True, slots=True)
class HttpResponse:
    status: int
    headers: dict[str, str]
    body: bytes


@dataclass(frozen=True, slots=True)
class FederationReadinessItem:
    id: str
    name: str
    group: Literal["account", "knowledge"]
    level: Literal["required", "warning"]
    applies: bool
    passed: bool
    code: str
    detail: str
    values: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "name": self.name,
            "group": self.group,
            "level": self.level,
            "applies": self.applies,
            "passed": self.passed,
            "code": self.code,
            "detail": self.detail,
            "values": dict(self.values),
        }


@dataclass(frozen=True, slots=True)
class FederationReadinessReport:
    items: tuple[FederationReadinessItem, ...]
    passed: bool

    def to_dict(self) -> dict[str, Any]:
        return {
            "items": [item.to_dict() for item in self.items],
            "passed": self.passed,
        }

    def __getitem__(self, key: str) -> Any:
        return self.to_dict()[key]


def _system_ssl_context() -> ssl.SSLContext:
    cafile = os.environ.get("SSL_CERT_FILE") or None
    return ssl.create_default_context(cafile=cafile)


def fetch_metadata(url: str) -> tuple[int, bytes]:
    """Default fetch for GF7: one GET, a short timeout, no redirects, no environment proxy settings, system trust store."""
    response = httpx.get(url, timeout=METADATA_FETCH_TIMEOUT_SECONDS, follow_redirects=False, trust_env=False, verify=_system_ssl_context(), headers={"Accept": "application/json"})
    return response.status_code, response.content


def request_public_origin(method: str, url: str, *, headers: dict[str, str] | None = None, body: bytes | None = None) -> HttpResponse:
    """Send one HTTP request to the public origin: short timeout, no redirects, no environment proxy settings, system trust store, bounded response."""
    with httpx.Client(timeout=METADATA_FETCH_TIMEOUT_SECONDS, follow_redirects=False, trust_env=False, verify=_system_ssl_context()) as client:
        with client.stream(method, url, headers=headers, content=body) as response:
            declared = response.headers.get("content-length")
            if declared is not None and declared.strip().isdigit() and int(declared.strip()) > READINESS_RESPONSE_MAX_BYTES:
                raise ResponseTooLargeError("response too large")
            chunks: list[bytes] = []
            total = 0
            for chunk in response.iter_bytes():
                total += len(chunk)
                if total > READINESS_RESPONSE_MAX_BYTES:
                    raise ResponseTooLargeError("response too large")
                chunks.append(chunk)
            return HttpResponse(status=response.status_code, headers={k.lower(): v for k, v in response.headers.items()}, body=b"".join(chunks))


def master_key_path(data_dir: Path | str) -> Path:
    return Path(data_dir) / "auth" / "master.key"


def _heartbeat_age(heartbeat: dict[str, Any] | None, now: float) -> float | None:
    """Return the age of the heartbeat in seconds, or ``None`` when there is no valid time in the record."""
    if heartbeat is None:
        return None
    received_at = heartbeat.get("received_at")
    if isinstance(received_at, bool) or not isinstance(received_at, (int, float)):
        return None
    return now - float(received_at)


def _is_recent(age: float | None) -> bool:
    # A heartbeat time that is later than the clock is not valid (fail closed).
    return age is not None and 0.0 <= age <= HEARTBEAT_MAX_AGE_SECONDS


def heartbeat_is_recent(heartbeat: dict[str, Any] | None, now: float | None = None) -> bool:
    """Return ``True`` when the sidecar sent a heartbeat in the last ``HEARTBEAT_MAX_AGE_SECONDS`` seconds."""
    return _is_recent(_heartbeat_age(heartbeat, time.time() if now is None else now))


def _major(version: Any) -> str | None:
    if not isinstance(version, str) or not version:
        return None
    return version.split(".")[0]


def _media_type(headers: dict[str, str]) -> str:
    return headers.get("content-type", "").split(";", 1)[0].strip().lower()


def _stored_rsa_pem(data_dir: Path, *, account_id: int | None = None, kb_number: int | None = None) -> str | None:
    auth_path = auth_db.auth_db_path(data_dir)
    if not auth_path.is_file():
        return None
    conn = dbm.connect_existing(auth_path, readonly=True)
    try:
        if kb_number is not None:
            jwks = actor_keys.load_kb_public_jwks(conn, kb_number)
        else:
            assert account_id is not None
            jwks = actor_keys.load_public_jwks(conn, account_id)
    except (sqlite3.Error, ValueError):
        return None
    finally:
        conn.close()
    rsa_jwk = jwks.get("rsa2048")
    if rsa_jwk is None:
        return None
    try:
        return actor_keys.public_key_pem(rsa_jwk)
    except (KeyError, TypeError, ValueError):
        return None


def _check_mode(conn: sqlite3.Connection) -> FederationReadinessItem:
    """GF1: the server mode permits federation. Network mode does. Single mode does when the first setup is complete."""
    name = "Server mode permits federation"
    store = accounts_store.AccountStore(conn)
    mode = store.server_mode()
    if mode == "network":
        return FederationReadinessItem(id="GF1", name=name, group="account", level="required", applies=True, passed=True, code="mode_network", detail="The server mode is network.")
    admin = conn.execute("SELECT status, username FROM accounts WHERE role='admin' ORDER BY id LIMIT 1").fetchone()
    passed = admin is not None and admin[0] == "active" and bool(admin[1])
    if passed:
        return FederationReadinessItem(id="GF1", name=name, group="account", level="required", applies=True, passed=True, code="mode_single_ready", detail=f"The server mode is {mode} and the first setup is complete.", values={"mode": mode})
    return FederationReadinessItem(id="GF1", name=name, group="account", level="required", applies=True, passed=False, code="mode_single_setup_required", detail=f"The server mode is {mode} and the first setup is not complete: the admin account must be active and have a USERNAME.", values={"mode": mode})


def _check_heartbeat(heartbeat: dict[str, Any] | None, age: float | None) -> FederationReadinessItem:
    name = "Sidecar heartbeat and contract version"
    if heartbeat is None:
        return FederationReadinessItem(id="GF2", name=name, group="account", level="required", applies=True, passed=False, code="no_heartbeat", detail="There is no sidecar heartbeat record.")
    if not _is_recent(age):
        limit = int(HEARTBEAT_MAX_AGE_SECONDS)
        return FederationReadinessItem(id="GF2", name=name, group="account", level="required", applies=True, passed=False, code="heartbeat_stale", detail=f"The sidecar heartbeat is not recent (limit: {limit} seconds).", values={"limit": limit})
    expected = _major(federation_routes.CONTRACT_VERSION)
    actual = _major(heartbeat.get("contract_version"))
    if actual != expected:
        return FederationReadinessItem(id="GF2", name=name, group="account", level="required", applies=True, passed=False, code="contract_version_mismatch", detail=f"The contract major version of the sidecar ({actual}) is not that of Claire ({expected}).", values={"actual": str(actual), "expected": str(expected)})
    return FederationReadinessItem(id="GF2", name=name, group="account", level="required", applies=True, passed=True, code="heartbeat_ok", detail=f"The heartbeat is recent and the contract major version is {expected}.", values={"expected": str(expected)})


def _check_service_key(data_dir: Path) -> FederationReadinessItem:
    name = "Service key file"
    path = service_key_path(data_dir)
    try:
        info = os.stat(path)
    except OSError:
        return FederationReadinessItem(id="GF3", name=name, group="account", level="required", applies=True, passed=False, code="service_key_missing", detail="The service key file does not exist. Run 'claire migrate' to make it.")
    if not stat.S_ISREG(info.st_mode):
        return FederationReadinessItem(id="GF3", name=name, group="account", level="required", applies=True, passed=False, code="service_key_not_file", detail="The service key path is not a regular file.")
    mode = stat.S_IMODE(info.st_mode)
    if mode != SERVICE_KEY_MODE:
        mode_text = f"{mode:04o}"
        return FederationReadinessItem(id="GF3", name=name, group="account", level="required", applies=True, passed=False, code="service_key_mode", detail=f"The mode of the service key file is {mode_text}. The mode must be 0600.", values={"mode": mode_text})
    if info.st_size != SERVICE_KEY_BYTES:
        return FederationReadinessItem(id="GF3", name=name, group="account", level="required", applies=True, passed=False, code="service_key_size", detail=f"The service key file must contain {SERVICE_KEY_BYTES} bytes.", values={"bytes": SERVICE_KEY_BYTES})
    return FederationReadinessItem(id="GF3", name=name, group="account", level="required", applies=True, passed=True, code="service_key_ok", detail="The service key file exists with mode 0600.")


def _check_master_key(data_dir: Path) -> FederationReadinessItem:
    name = "Master key"
    path = master_key_path(data_dir)
    # ``SecretService`` makes a new key file when it finds no key. This check must not do that.
    if not os.environ.get("CLAIRE_SECRET_KEY") and not path.is_file():
        return FederationReadinessItem(id="GF4", name=name, group="account", level="required", applies=True, passed=False, code="master_key_missing", detail="There is no master key: CLAIRE_SECRET_KEY is not set and the master key file does not exist.")
    try:
        SecretService(key_path=path)
    except (SecretError, OSError) as exc:
        err = type(exc).__name__
        return FederationReadinessItem(id="GF4", name=name, group="account", level="required", applies=True, passed=False, code="master_key_unreadable", detail=f"SecretService cannot load the master key ({err}).", values={"error": err})
    return FederationReadinessItem(id="GF4", name=name, group="account", level="required", applies=True, passed=True, code="master_key_ok", detail="SecretService can load the master key.")


def _check_https(runtime: WebRuntimeConfig | None, config_error: str) -> FederationReadinessItem:
    name = "Canonical origin uses https in production"
    if runtime is None:
        return FederationReadinessItem(id="GF5", name=name, group="account", level="required", applies=True, passed=False, code="runtime_invalid_detail", detail=f"The runtime configuration is not valid: {config_error}.", values={"error": config_error})
    if runtime.environment != "production":
        return FederationReadinessItem(id="GF5", name=name, group="account", level="required", applies=False, passed=True, code="production_only", detail=f"The environment is {runtime.environment}. This check applies to production only.", values={"environment": runtime.environment})
    passed = runtime.public_origin.lower().startswith("https://")
    if passed:
        return FederationReadinessItem(id="GF5", name=name, group="account", level="required", applies=True, passed=True, code="https_ok", detail="The canonical origin uses https.")
    return FederationReadinessItem(id="GF5", name=name, group="account", level="required", applies=True, passed=False, code="https_required", detail="The canonical origin must use https in production.")


def _check_self_check(heartbeat: dict[str, Any] | None, age: float | None) -> FederationReadinessItem:
    name = "Sidecar public self-check"
    if heartbeat is None or not _is_recent(age):
        return FederationReadinessItem(id="GF6", name=name, group="account", level="required", applies=True, passed=False, code="no_recent_heartbeat", detail="There is no recent sidecar heartbeat. The result of the public self-check is not known.")
    self_check = heartbeat.get("self_check")
    ok = self_check.get("ok") if isinstance(self_check, dict) else None
    if ok is True:
        return FederationReadinessItem(id="GF6", name=name, group="account", level="required", applies=True, passed=True, code="webfinger_self_check_ok", detail="The sidecar got its own WebFinger response through the public origin.")
    if ok is False:
        return FederationReadinessItem(id="GF6", name=name, group="account", level="required", applies=True, passed=False, code="webfinger_self_check_failed", detail="The public self-check of the sidecar failed. Examine the reverse proxy rules for the federation paths.")
    return FederationReadinessItem(id="GF6", name=name, group="account", level="required", applies=True, passed=False, code="webfinger_self_check_pending", detail="The sidecar did not do the public self-check yet.")


def _check_metadata(runtime: WebRuntimeConfig | None, fetch: MetadataFetch) -> FederationReadinessItem:
    name = "OAuth metadata through the public origin"
    if runtime is None:
        return FederationReadinessItem(id="GF7", name=name, group="account", level="required", applies=True, passed=False, code="runtime_invalid", detail="The runtime configuration is not valid.")
    issuer = runtime.public_origin.rstrip("/")
    try:
        status, body = fetch(issuer + METADATA_PATH)
    except Exception as exc:  # noqa: BLE001  # A failed request is a failed check, not an error of the caller.
        err = type(exc).__name__
        return FederationReadinessItem(id="GF7", name=name, group="account", level="required", applies=True, passed=False, code="request_failed", detail=f"The request to the public origin failed ({err}).", values={"error": err})
    if status != 200:
        return FederationReadinessItem(id="GF7", name=name, group="account", level="required", applies=True, passed=False, code="oauth_unexpected_status", detail=f"The public origin returned HTTP status {status} for the OAuth metadata.", values={"status": status})
    if len(body) > METADATA_MAX_BYTES:
        return FederationReadinessItem(id="GF7", name=name, group="account", level="required", applies=True, passed=False, code="oauth_too_large", detail="The OAuth metadata response is too large.")
    try:
        document = json.loads(body)
    except (ValueError, TypeError):
        document = None
    if not isinstance(document, dict) or document.get("issuer") != issuer:
        return FederationReadinessItem(id="GF7", name=name, group="account", level="required", applies=True, passed=False, code="oauth_metadata_mismatch", detail="The response is not the OAuth metadata of this server. Examine the reverse proxy rules.")
    return FederationReadinessItem(id="GF7", name=name, group="account", level="required", applies=True, passed=True, code="oauth_metadata_ok", detail="The public origin returns the OAuth metadata of this server.")


def _check_knowledge_paths(heartbeat: dict[str, Any] | None, age: float | None) -> FederationReadinessItem:
    name = "Knowledge federation paths reach this sidecar"
    if heartbeat is None or not _is_recent(age):
        return FederationReadinessItem(id="GF8", name=name, group="knowledge", level="required", applies=True, passed=False, code="knowledge_no_recent_heartbeat", detail="There is no recent sidecar heartbeat. The result of the knowledge self-check is not known.")
    ksc = heartbeat.get("knowledge_self_check")
    failed = ksc.get("failed_paths") if isinstance(ksc, dict) else None
    if not isinstance(ksc, dict) or ksc.get("checked_at") is None or not isinstance(failed, list) or not all(isinstance(p, str) for p in failed):
        return FederationReadinessItem(id="GF8", name=name, group="knowledge", level="required", applies=True, passed=False, code="knowledge_self_check_pending", detail="The sidecar did not do the knowledge self-check yet.")
    if not failed:
        return FederationReadinessItem(id="GF8", name=name, group="knowledge", level="required", applies=True, passed=True, code="paths_ok", detail="All knowledge federation paths reach this sidecar.")
    paths = ", ".join(failed)
    return FederationReadinessItem(id="GF8", name=name, group="knowledge", level="required", applies=True, passed=False, code="paths_not_routed", detail=f"The public origin does not send these paths to this sidecar: {paths}. Examine the reverse proxy rules: exact /.well-known/nodeinfo, prefix /nodeinfo/, prefix /ap/.", values={"paths": paths})


_GF9_DETAILS = {
    "not_routed": ("signed_not_routed", "The signed self-check request did not reach this sidecar. Examine the reverse proxy rules for the prefix /ap/."),
    "host_changed": ("signed_host_changed", "The reverse proxy changed the Host header. It must send the Host of the request without a change, because the signature includes it."),
    "signature_invalid": ("signed_signature_invalid", "The signature of the self-check request did not verify after the reverse proxy. The proxy must send the body and the headers Date, Digest, Content-Digest, Signature, and Signature-Input without a change."),
    "tls_error": ("signed_tls_error", "The sidecar cannot verify the TLS certificate of the public origin. Give the sidecar the certificate authority of the origin."),
    "dns_error": ("signed_dns_error", "The sidecar cannot resolve the name of the public origin."),
}


def _check_signed_self_check(heartbeat: dict[str, Any] | None, age: float | None) -> FederationReadinessItem:
    name = "Signed request through the public origin"
    if heartbeat is None or not _is_recent(age):
        return FederationReadinessItem(id="GF9", name=name, group="knowledge", level="required", applies=True, passed=False, code="knowledge_no_recent_heartbeat", detail="There is no recent sidecar heartbeat. The result of the knowledge self-check is not known.")
    ksc = heartbeat.get("knowledge_self_check")
    signed = ksc.get("signed") if isinstance(ksc, dict) else None
    result = signed.get("result") if isinstance(signed, dict) else None
    if result is None:
        return FederationReadinessItem(id="GF9", name=name, group="knowledge", level="required", applies=True, passed=False, code="knowledge_self_check_pending", detail="The sidecar did not do the knowledge self-check yet.")
    if result == "ok":
        return FederationReadinessItem(id="GF9", name=name, group="knowledge", level="required", applies=True, passed=True, code="signed_ok", detail="A signed request reaches this sidecar through the public origin without a change of its signature parts.")
    if result in _GF9_DETAILS:
        code, detail = _GF9_DETAILS[result]
        return FederationReadinessItem(id="GF9", name=name, group="knowledge", level="required", applies=True, passed=False, code=code, detail=detail)
    err = str(result)
    return FederationReadinessItem(id="GF9", name=name, group="knowledge", level="required", applies=True, passed=False, code="signed_request_failed", detail=f"The self-check request to the public origin failed ({err}).", values={"error": err})


def _check_inbox_body_limit(runtime: WebRuntimeConfig | None, requester: PublicRequest) -> FederationReadinessItem:
    name = "Inbox body of the largest size through the public origin"
    if runtime is None:
        return FederationReadinessItem(id="GF10", name=name, group="knowledge", level="required", applies=True, passed=False, code="runtime_invalid", detail="The runtime configuration is not valid.")
    origin = runtime.public_origin.rstrip("/")
    size = GF10_INBOX_BODY_BYTES
    prefix = b'{"summary":"'
    suffix = b'"}'
    payload = prefix + (b"x" * (size - len(prefix) - len(suffix))) + suffix
    try:
        resp = requester("POST", f"{origin}/ap/inbox", headers={"Content-Type": "application/activity+json"}, body=payload)
    except Exception as exc:  # noqa: BLE001
        err = type(exc).__name__
        return FederationReadinessItem(id="GF10", name=name, group="knowledge", level="required", applies=True, passed=False, code="request_failed", detail=f"The request to the public origin failed ({err}).", values={"error": err})
    if resp.status == 413:
        return FederationReadinessItem(id="GF10", name=name, group="knowledge", level="required", applies=True, passed=False, code="inbox_body_too_large", detail=f"The public origin refused an inbox body of {size} bytes (HTTP {resp.status}). The reverse proxy must accept a request body of {size} bytes or more on the paths below /ap/.", values={"bytes": size, "status": resp.status})
    if not resp.headers.get("x-claire-fedify-server"):
        return FederationReadinessItem(id="GF10", name=name, group="knowledge", level="required", applies=True, passed=False, code="inbox_not_sidecar", detail="The answer did not come from a sidecar (no header X-Claire-Fedify-Server). Examine the reverse proxy rules for the prefix /ap/.")
    return FederationReadinessItem(id="GF10", name=name, group="knowledge", level="required", applies=True, passed=True, code="inbox_body_ok", detail=f"The public origin accepts an inbox body of {size} bytes.", values={"bytes": size})


def _check_knowledge_loops(heartbeat: dict[str, Any] | None, current: float) -> FederationReadinessItem:
    name = "Knowledge federation loops of the sidecar make progress"
    loops = heartbeat.get("loops") if isinstance(heartbeat, dict) else None
    if not isinstance(loops, dict):
        return FederationReadinessItem(id="GF11", name=name, group="knowledge", level="required", applies=True, passed=False, code="loops_missing", detail="The sidecar heartbeat has no state of the knowledge federation loops.")
    for loop_name in _KNOWLEDGE_LOOPS:
        state = loops.get(loop_name)
        if not isinstance(state, dict):
            return FederationReadinessItem(id="GF11", name=name, group="knowledge", level="required", applies=True, passed=False, code="loops_missing", detail="The sidecar heartbeat has no state of the knowledge federation loops.")
    for loop_name in _KNOWLEDGE_LOOPS:
        state = loops[loop_name]
        progress_at = state.get("progress_at")
        raw_error = state.get("error")
        err_text = raw_error if isinstance(raw_error, str) and raw_error else "none"
        recent = not isinstance(progress_at, bool) and isinstance(progress_at, (int, float)) and 0.0 <= (current - float(progress_at)) <= LOOP_MAX_AGE_SECONDS
        if not recent:
            return FederationReadinessItem(id="GF11", name=name, group="knowledge", level="required", applies=True, passed=False, code="loop_stalled", detail=f"The loop {loop_name} of the sidecar made no progress in the last 300 seconds. Last error: {err_text}.", values={"loop": loop_name, "error": err_text})
    return FederationReadinessItem(id="GF11", name=name, group="knowledge", level="required", applies=True, passed=True, code="loops_ok", detail="The knowledge federation loops of the sidecar make progress.")


def _check_actor_keys(data_dir: Path) -> FederationReadinessItem:
    name = "Actor keys can be decrypted"
    auth_path = auth_db.auth_db_path(data_dir)
    if not auth_path.is_file():
        return FederationReadinessItem(id="GF12", name=name, group="knowledge", level="required", applies=True, passed=True, code="actor_keys_empty", detail="There is no actor key yet.")
    try:
        auth_conn = dbm.connect_existing(auth_path, readonly=True)
    except (sqlite3.Error, OSError) as exc:
        err = type(exc).__name__
        return FederationReadinessItem(id="GF12", name=name, group="knowledge", level="required", applies=True, passed=False, code="actor_keys_unreadable", detail=f"The actor key store cannot be read ({err}).", values={"error": err})
    try:
        has_table = auth_conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='actor_keys'").fetchone()
        if has_table is None:
            return FederationReadinessItem(id="GF12", name=name, group="knowledge", level="required", applies=True, passed=True, code="actor_keys_empty", detail="There is no actor key yet.")
        rows = auth_conn.execute("SELECT account_id, alg, private_ciphertext, private_nonce, key_version FROM actor_keys ORDER BY account_id, alg").fetchall()
    except sqlite3.Error as exc:
        err = type(exc).__name__
        return FederationReadinessItem(id="GF12", name=name, group="knowledge", level="required", applies=True, passed=False, code="actor_keys_unreadable", detail=f"The actor key store cannot be read ({err}).", values={"error": err})
    finally:
        auth_conn.close()

    if not rows:
        return FederationReadinessItem(id="GF12", name=name, group="knowledge", level="required", applies=True, passed=True, code="actor_keys_empty", detail="There is no actor key yet.")

    key_file = master_key_path(data_dir)
    secret_service: SecretService | None = None
    if os.environ.get("CLAIRE_SECRET_KEY") or key_file.is_file():
        try:
            secret_service = SecretService(key_path=key_file)
        except (SecretError, OSError):
            secret_service = None

    failed_count = 0
    failed_actors: list[int] = []
    seen_actors: set[int] = set()
    for account_id, alg, ciphertext, nonce, key_version in rows:
        owner = int(account_id)
        ok = False
        if secret_service is not None:
            try:
                plaintext = secret_service.decrypt(bytes(ciphertext), bytes(nonce), f"actor_key|{owner}|{alg}", int(key_version))
                json.loads(plaintext)
                ok = True
            except (SecretError, TypeError, ValueError):
                ok = False
        if not ok:
            failed_count += 1
            if owner not in seen_actors:
                seen_actors.add(owner)
                failed_actors.append(owner)
    if failed_count > 0:
        actors_text = ", ".join(str(a) for a in failed_actors[:5])
        return FederationReadinessItem(id="GF12", name=name, group="knowledge", level="required", applies=True, passed=False, code="actor_keys_decrypt_failed", detail=f"{failed_count} actor keys cannot be decrypted with the master key (actors: {actors_text}). The master key is not the key that encrypted them.", values={"count": failed_count, "actors": actors_text})
    return FederationReadinessItem(id="GF12", name=name, group="knowledge", level="required", applies=True, passed=True, code="actor_keys_ok", detail=f"All {len(rows)} actor keys can be decrypted.", values={"count": len(rows)})


def _invalid_setting_item(key: str, value: Any) -> FederationReadinessItem:
    return FederationReadinessItem(
        id="GF13",
        name="Settings of the knowledge federation and the public name",
        group="knowledge",
        level="required",
        applies=True,
        passed=False,
        code="setting_invalid",
        detail=f"The server setting {key} has a value that is not valid: {value}.",
        values={"key": key, "value": str(value)},
    )


def _check_knowledge_settings(conn: sqlite3.Connection, runtime: WebRuntimeConfig | None) -> FederationReadinessItem:
    name = "Settings of the knowledge federation and the public name"
    for switch_key in ("federation", auto_contribution.SWITCH_KEY):
        row = conn.execute("SELECT value FROM server_settings WHERE key=?", (switch_key,)).fetchone()
        if row is not None and row[0] not in ("off", "on"):
            return _invalid_setting_item(switch_key, row[0])
    for key in contributions.LIMITS:
        row = conn.execute("SELECT value FROM server_settings WHERE key=?", (key,)).fetchone()
        if row is None:
            continue
        try:
            parsed = int(row[0])
        except (TypeError, ValueError):
            return _invalid_setting_item(key, row[0])
        if parsed < contributions._LIMIT_MINIMUM.get(key, 1):
            return _invalid_setting_item(key, row[0])
    for threshold_key in ("theme_match_threshold", "auto_match_threshold"):
        row = conn.execute("SELECT value FROM server_settings WHERE key=?", (threshold_key,)).fetchone()
        if row is None:
            continue
        try:
            value = float(row[0])
        except (TypeError, ValueError):
            return _invalid_setting_item(threshold_key, row[0])
        if not math.isfinite(value) or not 0.0 <= value <= 1.0:
            return _invalid_setting_item(threshold_key, row[0])
    row = conn.execute("SELECT value FROM server_settings WHERE key='auto_run_documents'").fetchone()
    if row is not None:
        try:
            value = float(row[0])
        except (TypeError, ValueError):
            return _invalid_setting_item("auto_run_documents", row[0])
        if not math.isfinite(value) or value != int(value) or int(value) < 1:
            return _invalid_setting_item("auto_run_documents", row[0])
    if runtime is None:
        return FederationReadinessItem(id="GF13", name=name, group="knowledge", level="required", applies=True, passed=False, code="runtime_invalid", detail="The runtime configuration is not valid.")
    if runtime.environment == "production":
        host = runtime.expected_authority.split(":", 1)[0].lower()
        for suffix in _SPECIAL_USE_SUFFIXES:
            if host == suffix or host.endswith(f".{suffix}"):
                return FederationReadinessItem(id="GF13", name=name, group="knowledge", level="required", applies=True, passed=False, code="special_use_host", detail=f"The host {host} of the public origin is below the special-use name {suffix}. Other servers cannot resolve it.", values={"host": host, "suffix": suffix})
    return FederationReadinessItem(id="GF13", name=name, group="knowledge", level="required", applies=True, passed=True, code="settings_ok", detail="The settings of the knowledge federation and the public name are valid.")


def _check_first_contact(runtime: WebRuntimeConfig | None, data_dir: Path, requester: PublicRequest) -> FederationReadinessItem:
    name = "First-contact documents through the public origin"
    if runtime is None:
        return FederationReadinessItem(id="GF14", name=name, group="knowledge", level="required", applies=True, passed=False, code="runtime_invalid", detail="The runtime configuration is not valid.")
    origin = runtime.public_origin.rstrip("/")

    def nodeinfo_fail(problem: str) -> FederationReadinessItem:
        return FederationReadinessItem(id="GF14", name=name, group="knowledge", level="required", applies=True, passed=False, code="nodeinfo_failed", detail=f"NodeInfo through the public origin: {problem}.", values={"problem": problem})

    def actor_fail(problem: str) -> FederationReadinessItem:
        return FederationReadinessItem(id="GF14", name=name, group="knowledge", level="required", applies=True, passed=False, code="server_actor_failed", detail=f"The server actor through the public origin: {problem}.", values={"problem": problem})

    try:
        disco = requester("GET", f"{origin}/.well-known/nodeinfo", headers={"Accept": "application/json"})
    except Exception as exc:  # noqa: BLE001
        return nodeinfo_fail(f"request failed ({type(exc).__name__})")
    if disco.status != 200:
        return nodeinfo_fail(f"HTTP status {disco.status} at /.well-known/nodeinfo")
    try:
        disco_doc = json.loads(disco.body)
    except (ValueError, TypeError):
        disco_doc = None
    expected_nodeinfo_href = f"{origin}/nodeinfo/2.1"
    links = disco_doc.get("links") if isinstance(disco_doc, dict) else None
    if not isinstance(links, list) or not any(isinstance(link, dict) and link.get("href") == expected_nodeinfo_href for link in links):
        return nodeinfo_fail("missing link to /nodeinfo/2.1")

    try:
        info = requester("GET", expected_nodeinfo_href, headers={"Accept": "application/json"})
    except Exception as exc:  # noqa: BLE001
        return nodeinfo_fail(f"request failed ({type(exc).__name__})")
    if info.status != 200:
        return nodeinfo_fail(f"HTTP status {info.status} at /nodeinfo/2.1")
    try:
        info_doc = json.loads(info.body)
    except (ValueError, TypeError):
        info_doc = None
    software = info_doc.get("software") if isinstance(info_doc, dict) else None
    protocols = info_doc.get("protocols") if isinstance(info_doc, dict) else None
    if not isinstance(software, dict) or software.get("name") != federation_routes.SOFTWARE_NAME or software.get("version") != federation_routes.software_version():
        return nodeinfo_fail("software name or version does not match")
    if not isinstance(protocols, list) or "activitypub" not in protocols:
        return nodeinfo_fail("activitypub is missing in protocols")

    try:
        actor_resp = requester("GET", f"{origin}/ap/actors/server", headers={"Accept": "application/activity+json"})
    except Exception as exc:  # noqa: BLE001
        return actor_fail(f"request failed ({type(exc).__name__})")
    if actor_resp.status != 200:
        return actor_fail(f"HTTP status {actor_resp.status}")
    try:
        actor_doc = json.loads(actor_resp.body)
    except (ValueError, TypeError):
        actor_doc = None
    if not isinstance(actor_doc, dict):
        return actor_fail("invalid JSON")
    actor_types = actor_doc.get("type")
    if isinstance(actor_types, str):
        has_app_type = actor_types == "Application"
    elif isinstance(actor_types, list):
        has_app_type = "Application" in actor_types
    else:
        has_app_type = False
    expected_id = f"{origin}/ap/actors/server"
    if not has_app_type:
        return actor_fail("type is not Application")
    if actor_doc.get("id") != expected_id:
        return actor_fail("id does not match the public origin")
    inbox = actor_doc.get("inbox")
    if not isinstance(inbox, str) or not inbox.startswith(f"{origin}/"):
        return actor_fail("inbox is not on the public origin")
    pub_key = actor_doc.get("publicKey")
    if not isinstance(pub_key, dict) or pub_key.get("owner") != expected_id or not isinstance(pub_key.get("publicKeyPem"), str) or not pub_key["publicKeyPem"].strip():
        return actor_fail("publicKey is missing or invalid")

    stored_pem = _stored_rsa_pem(data_dir, account_id=actor_keys.SERVER_ACTOR_ACCOUNT_ID)
    if stored_pem is None or pub_key["publicKeyPem"].strip() != stored_pem.strip():
        return FederationReadinessItem(id="GF14", name=name, group="knowledge", level="required", applies=True, passed=False, code="server_actor_key_mismatch", detail="The public key of the server actor through the public origin is not the stored key. Examine caches between the reverse proxy and the sidecar.")
    return FederationReadinessItem(id="GF14", name=name, group="knowledge", level="required", applies=True, passed=True, code="first_contact_ok", detail="NodeInfo and the server actor document are valid through the public origin.")


def _check_kb_actor(runtime: WebRuntimeConfig | None, data_dir: Path, kb_number: int, expected_auto: bool, requester: PublicRequest) -> FederationReadinessItem:
    name = "Knowledge base actor document through the public origin"
    if runtime is None:
        return FederationReadinessItem(id="GF15", name=name, group="knowledge", level="required", applies=True, passed=False, code="runtime_invalid", detail="The runtime configuration is not valid.")
    origin = runtime.public_origin.rstrip("/")
    num_text = str(kb_number)

    def fail(problem: str) -> FederationReadinessItem:
        return FederationReadinessItem(id="GF15", name=name, group="knowledge", level="required", applies=True, passed=False, code="kb_actor_failed", detail=f"The actor document of the knowledge base {num_text} through the public origin: {problem}.", values={"number": num_text, "problem": problem})

    try:
        resp = requester("GET", f"{origin}/ap/kb/{num_text}", headers={"Accept": "application/activity+json"})
    except Exception as exc:  # noqa: BLE001
        return fail(f"request failed ({type(exc).__name__})")
    if resp.status != 200:
        return fail(f"HTTP status {resp.status}")
    if _media_type(resp.headers) != "application/activity+json":
        return fail("media type is not application/activity+json")
    try:
        doc = json.loads(resp.body)
    except (ValueError, TypeError):
        doc = None
    if not isinstance(doc, dict):
        return fail("invalid JSON")
    raw_types = doc.get("type")
    types = [raw_types] if isinstance(raw_types, str) else list(raw_types) if isinstance(raw_types, list) else []
    if "Group" not in types or vocabulary.KNOWLEDGE_BASE not in types:
        return fail("type does not have Group and claire:KnowledgeBase")
    expected_id = f"{origin}/ap/kb/{num_text}"
    if doc.get("id") != expected_id:
        return fail("id does not match the public origin")
    if doc.get("inbox") != f"{expected_id}/inbox":
        return fail("inbox does not match")
    for field_name in ("followers", "outbox", vocabulary.DOCUMENTS):
        val = doc.get(field_name)
        if not isinstance(val, str) or not val.startswith(f"{origin}/"):
            return fail(f"{field_name} is not on the public origin")
    pub_key = doc.get("publicKey")
    if not isinstance(pub_key, dict) or pub_key.get("owner") != expected_id or not isinstance(pub_key.get("publicKeyPem"), str) or not pub_key["publicKeyPem"].strip():
        return fail("publicKey is missing or invalid")
    actual_auto = doc.get(vocabulary.ACCEPTS_AUTOMATIC)
    if not isinstance(actual_auto, bool) or actual_auto != expected_auto:
        actual_text = "true" if actual_auto is True else "false" if actual_auto is False else str(actual_auto)
        expected_text = "true" if expected_auto else "false"
        return FederationReadinessItem(id="GF15", name=name, group="knowledge", level="required", applies=True, passed=False, code="kb_actor_accepts_automatic_mismatch", detail=f"claire:acceptsAutomatic is {actual_text} in the document, but the switches give {expected_text}. A cache between the reverse proxy and the sidecar can give old documents.", values={"value": actual_text, "expected": expected_text})
    stored_pem = _stored_rsa_pem(data_dir, kb_number=kb_number)
    if stored_pem is None or pub_key["publicKeyPem"].strip() != stored_pem.strip():
        return fail("public key does not match the stored key")
    return FederationReadinessItem(id="GF15", name=name, group="knowledge", level="required", applies=True, passed=True, code="kb_actor_ok", detail=f"The actor document of the knowledge base {num_text} is valid through the public origin.", values={"number": num_text})


def _check_unsigned_collections(runtime: WebRuntimeConfig | None, kb_number: int | None, requester: PublicRequest) -> FederationReadinessItem:
    name = "Collections refuse an unsigned read"
    if runtime is None:
        return FederationReadinessItem(id="GF16", name=name, group="knowledge", level="required", applies=True, passed=False, code="runtime_invalid", detail="The runtime configuration is not valid.")
    origin = runtime.public_origin.rstrip("/")
    paths = ["/ap/kb", "/ap/themes"]
    if kb_number is not None:
        paths.append(f"/ap/kb/{kb_number}/documents")
    for path in paths:
        try:
            resp = requester("GET", f"{origin}{path}", headers={"Accept": "application/activity+json"})
        except Exception as exc:  # noqa: BLE001
            err = type(exc).__name__
            return FederationReadinessItem(id="GF16", name=name, group="knowledge", level="required", applies=True, passed=False, code="request_failed", detail=f"The request to the public origin failed ({err}).", values={"error": err})
        if resp.status != 404:
            return FederationReadinessItem(id="GF16", name=name, group="knowledge", level="required", applies=True, passed=False, code="collection_not_refused", detail=f"The public origin gave HTTP {resp.status} for an unsigned read of {path}. A collection of shared knowledge must give 404 to a request without a signature (KS1). Examine caches in front of the sidecar.", values={"status": resp.status, "path": path})
    return FederationReadinessItem(id="GF16", name=name, group="knowledge", level="required", applies=True, passed=True, code="collections_refused_unsigned", detail="Collections refuse an unsigned read with HTTP 404.")


def _check_ns_documents(runtime: WebRuntimeConfig | None, requester: PublicRequest) -> FederationReadinessItem:
    name = "JSON-LD context and vocabulary page through the public origin"
    if runtime is None:
        return FederationReadinessItem(id="GF17", name=name, group="knowledge", level="warning", applies=True, passed=False, code="runtime_invalid", detail="The runtime configuration is not valid.")
    origin = runtime.public_origin.rstrip("/")

    def fail_context(problem: str) -> FederationReadinessItem:
        return FederationReadinessItem(id="GF17", name=name, group="knowledge", level="warning", applies=True, passed=False, code="ns_context_failed", detail=f"GET /ns/v1 through the public origin: {problem}.", values={"problem": problem})

    def fail_vocab(problem: str) -> FederationReadinessItem:
        return FederationReadinessItem(id="GF17", name=name, group="knowledge", level="warning", applies=True, passed=False, code="ns_vocabulary_failed", detail=f"GET /ns through the public origin: {problem}.", values={"problem": problem})

    try:
        ctx_resp = requester("GET", f"{origin}/ns/v1", headers={"Accept": "application/ld+json, application/json"})
    except Exception as exc:  # noqa: BLE001
        return fail_context(f"request failed ({type(exc).__name__})")
    if 300 <= ctx_resp.status < 400:
        return fail_context(f"unexpected redirect (HTTP {ctx_resp.status})")
    if ctx_resp.status != 200:
        return fail_context(f"HTTP status {ctx_resp.status}")
    media_type = _media_type(ctx_resp.headers)
    if media_type != vocabulary.CONTEXT_MEDIA_TYPE:
        return fail_context(f"media type is {media_type or 'missing'}")
    if ctx_resp.headers.get("access-control-allow-origin") != "*":
        return fail_context("missing header Access-Control-Allow-Origin: *")
    try:
        ctx_doc = json.loads(ctx_resp.body)
    except (ValueError, TypeError):
        ctx_doc = None
    if ctx_doc != vocabulary.context_document():
        return fail_context("body is not the context document of this version")

    try:
        vocab_resp = requester("GET", f"{origin}/ns", headers={"Accept": "text/html"})
    except Exception as exc:  # noqa: BLE001
        return fail_vocab(f"request failed ({type(exc).__name__})")
    if 300 <= vocab_resp.status < 400:
        return fail_vocab(f"unexpected redirect (HTTP {vocab_resp.status})")
    if vocab_resp.status != 200:
        return fail_vocab(f"HTTP status {vocab_resp.status}")
    vocab_type = _media_type(vocab_resp.headers)
    if vocab_type != "text/html":
        return fail_vocab(f"media type is {vocab_type or 'missing'}")
    return FederationReadinessItem(id="GF17", name=name, group="knowledge", level="warning", applies=True, passed=True, code="ns_ok", detail="The JSON-LD context and vocabulary page are served through the public origin.")


def _not_applicable_federation_off(item_id: str, name: str) -> FederationReadinessItem:
    return FederationReadinessItem(
        id=item_id,
        name=name,
        group="knowledge",
        level="required",
        applies=False,
        passed=True,
        code="federation_off",
        detail="The switch federation is off. The sidecar gives 404 for each federation path while it is off.",
    )


def _lowest_shared_kb_number(conn: sqlite3.Connection) -> int | None:
    row = conn.execute(
        "SELECT number FROM knowledge_bases WHERE is_personal=0 AND account_id IS NULL AND is_public=1 AND knowledge_type IN ('real', 'fictional') AND number IS NOT NULL ORDER BY number ASC LIMIT 1"
    ).fetchone()
    return None if row is None else int(row[0])


def _safe_federation_state(conn: sqlite3.Connection) -> str:
    row = conn.execute("SELECT value FROM server_settings WHERE key='federation'").fetchone()
    if row is not None and row[0] in ("off", "on"):
        return str(row[0])
    return "off"


def _safe_auto_contribution_on(conn: sqlite3.Connection) -> bool:
    row = conn.execute("SELECT value FROM server_settings WHERE key=?", (auto_contribution.SWITCH_KEY,)).fetchone()
    return row is not None and row[0] == "on"


def _timeout_item(item_id: str, name: str, level: Literal["required", "warning"]) -> FederationReadinessItem:
    group: Literal["account", "knowledge"] = "account" if item_id == "GF7" else "knowledge"
    return FederationReadinessItem(id=item_id, name=name, group=group, level=level, applies=True, passed=False, code="check_timeout", detail="The check did not end in 20 seconds.")


def check_federation_readiness(
    conn: sqlite3.Connection,
    settings: Any,
    *,
    now: float | None = None,
    fetch: MetadataFetch | None = None,
    request: PublicRequest | None = None,
    request_timeout: float = REQUEST_CHECKS_TIMEOUT_SECONDS,
) -> FederationReadinessReport:
    """Do the checks GF1 to GF17 and return the report. ``report.passed`` is ``True`` when all required items pass.

    ``conn`` is a connection to ``claire.db``. ``settings`` gives ``data_dir`` and the values that ``WebRuntimeConfig.from_settings`` reads.
    ``now``, ``fetch``, ``request``, and ``request_timeout`` are for tests.
    """
    current = time.time() if now is None else now
    data_dir = Path(getattr(settings, "data_dir"))
    runtime: WebRuntimeConfig | None = None
    config_error = ""
    try:
        runtime = WebRuntimeConfig.from_settings(settings)
    except Exception as exc:  # noqa: BLE001  # Settings that are not valid make GF5, GF7, GF10, and GF13 to GF17 fail.
        config_error = str(exc) or type(exc).__name__

    heartbeat = federation_routes.read_heartbeat(data_dir)
    age = _heartbeat_age(heartbeat, current)
    federation_state = _safe_federation_state(conn)
    federation_on = federation_state == "on"
    expected_auto = federation_on and _safe_auto_contribution_on(conn)
    lowest_kb = _lowest_shared_kb_number(conn)

    # Local / file / DB / heartbeat checks.
    gf1 = _check_mode(conn)
    gf2 = _check_heartbeat(heartbeat, age)
    gf3 = _check_service_key(data_dir)
    gf4 = _check_master_key(data_dir)
    gf5 = _check_https(runtime, config_error)
    gf6 = _check_self_check(heartbeat, age)
    gf8 = _check_knowledge_paths(heartbeat, age)
    gf9 = _check_signed_self_check(heartbeat, age)
    gf11 = _check_knowledge_loops(heartbeat, current)
    gf12 = _check_actor_keys(data_dir)
    gf13 = _check_knowledge_settings(conn, runtime)

    # Request checks run concurrently with a shared deadline.
    active_fetch = fetch if fetch is not None else fetch_metadata
    active_request = request if request is not None else request_public_origin

    request_specs: dict[str, tuple[str, Literal["required", "warning"], Callable[[], FederationReadinessItem]]] = {
        "GF7": ("OAuth metadata through the public origin", "required", lambda: _check_metadata(runtime, active_fetch)),
        "GF10": ("Inbox body of the largest size through the public origin", "required", lambda: _check_inbox_body_limit(runtime, active_request)),
        "GF17": ("JSON-LD context and vocabulary page through the public origin", "warning", lambda: _check_ns_documents(runtime, active_request)),
    }
    if federation_on:
        request_specs["GF14"] = ("First-contact documents through the public origin", "required", lambda: _check_first_contact(runtime, data_dir, active_request))
        request_specs["GF16"] = ("Collections refuse an unsigned read", "required", lambda: _check_unsigned_collections(runtime, lowest_kb, active_request))
        if lowest_kb is not None:
            request_specs["GF15"] = ("Knowledge base actor document through the public origin", "required", lambda: _check_kb_actor(runtime, data_dir, lowest_kb, expected_auto, active_request))

    request_results: dict[str, FederationReadinessItem] = {}
    executor = ThreadPoolExecutor(max_workers=len(request_specs))
    try:
        futures = {item_id: executor.submit(fn) for item_id, (_name, _level, fn) in request_specs.items()}
        done, _not_done = wait(futures.values(), timeout=request_timeout)
        for item_id, future in futures.items():
            item_name, item_level, _fn = request_specs[item_id]
            if future in done:
                try:
                    request_results[item_id] = future.result()
                except Exception as exc:  # noqa: BLE001
                    err = type(exc).__name__
                    group: Literal["account", "knowledge"] = "account" if item_id == "GF7" else "knowledge"
                    request_results[item_id] = FederationReadinessItem(id=item_id, name=item_name, group=group, level=item_level, applies=True, passed=False, code="request_failed", detail=f"The request to the public origin failed ({err}).", values={"error": err})
            else:
                future.cancel()
                request_results[item_id] = _timeout_item(item_id, item_name, item_level)
    finally:
        executor.shutdown(wait=False, cancel_futures=True)

    gf7 = request_results["GF7"]
    gf10 = request_results["GF10"]
    gf14 = request_results["GF14"] if federation_on else _not_applicable_federation_off("GF14", "First-contact documents through the public origin")
    if not federation_on:
        gf15 = _not_applicable_federation_off("GF15", "Knowledge base actor document through the public origin")
    elif lowest_kb is None:
        gf15 = FederationReadinessItem(id="GF15", name="Knowledge base actor document through the public origin", group="knowledge", level="required", applies=False, passed=True, code="no_shared_kb", detail="There is no shared knowledge base.")
    else:
        gf15 = request_results["GF15"]
    gf16 = request_results["GF16"] if federation_on else _not_applicable_federation_off("GF16", "Collections refuse an unsigned read")
    gf17 = request_results["GF17"]

    items = (gf1, gf2, gf3, gf4, gf5, gf6, gf7, gf8, gf9, gf10, gf11, gf12, gf13, gf14, gf15, gf16, gf17)
    return FederationReadinessReport(items=items, passed=all(item.passed for item in items if item.level == "required"))
