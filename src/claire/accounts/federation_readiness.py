"""Federation readiness checks GF1 to GF7 (Fedify sidecar design Section 12, unit FP1-6).

The admin can set the instance setting ``federation`` to ``on`` only when all checks pass. ``POST /api/admin/federation`` and ``claire instance check --federation`` use the same function, ``check_federation_readiness``.

The checks do not change data. GF4 does not make a master key file. GF7 is the only check that sends a network request: one GET to the public origin of this instance.
"""

from __future__ import annotations

import json
import os
import sqlite3
import stat
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any

import httpx

from ..api import federation_routes
from ..api.federation_internal import SERVICE_KEY_BYTES, service_key_path
from ..api.security import WebRuntimeConfig
from ..store import accounts as accounts_store
from ..store.secrets import SecretError, SecretService
from .readiness import ReadinessItem, ReadinessReport

__all__ = [
    "HEARTBEAT_MAX_AGE_SECONDS",
    "heartbeat_is_recent",
    "METADATA_FETCH_TIMEOUT_SECONDS",
    "METADATA_MAX_BYTES",
    "METADATA_PATH",
    "MetadataFetch",
    "check_federation_readiness",
    "fetch_metadata",
    "master_key_path",
]

# The sidecar sends a heartbeat about each 30 seconds. The limit permits three lost heartbeats.
HEARTBEAT_MAX_AGE_SECONDS = 120.0
METADATA_PATH = "/.well-known/oauth-authorization-server"
METADATA_FETCH_TIMEOUT_SECONDS = 5.0
METADATA_MAX_BYTES = 65536
SERVICE_KEY_MODE = 0o600

# A function that gets a URL and returns the HTTP status and the response body. It must not follow redirects.
MetadataFetch = Callable[[str], tuple[int, bytes]]


def fetch_metadata(url: str) -> tuple[int, bytes]:
    """Default fetch for GF7: one GET, a short timeout, no redirects, no environment proxy settings."""
    response = httpx.get(url, timeout=METADATA_FETCH_TIMEOUT_SECONDS, follow_redirects=False, trust_env=False, headers={"Accept": "application/json"})
    return response.status_code, response.content


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


def _check_mode(conn: sqlite3.Connection) -> ReadinessItem:
    mode = accounts_store.AccountStore(conn).instance_mode()
    passed = mode == "network"
    return ReadinessItem(id="GF1", name="Instance mode is network", passed=passed, detail="The instance mode is network." if passed else f"The instance mode is {mode}. Federation operates in network mode only.")


def _check_heartbeat(heartbeat: dict[str, Any] | None, age: float | None) -> ReadinessItem:
    name = "Sidecar heartbeat and contract version"
    if heartbeat is None:
        return ReadinessItem(id="GF2", name=name, passed=False, detail="There is no sidecar heartbeat record.")
    if not _is_recent(age):
        return ReadinessItem(id="GF2", name=name, passed=False, detail=f"The sidecar heartbeat is not recent (limit: {int(HEARTBEAT_MAX_AGE_SECONDS)} seconds).")
    expected = _major(federation_routes.CONTRACT_VERSION)
    actual = _major(heartbeat.get("contract_version"))
    if actual != expected:
        return ReadinessItem(id="GF2", name=name, passed=False, detail=f"The contract major version of the sidecar ({actual}) is not that of Claire ({expected}).")
    return ReadinessItem(id="GF2", name=name, passed=True, detail=f"The heartbeat is recent and the contract major version is {expected}.")


def _check_service_key(data_dir: Path) -> ReadinessItem:
    name = "Service key file"
    path = service_key_path(data_dir)
    try:
        info = os.stat(path)
    except OSError:
        return ReadinessItem(id="GF3", name=name, passed=False, detail="The service key file does not exist. Run 'claire migrate' to make it.")
    if not stat.S_ISREG(info.st_mode):
        return ReadinessItem(id="GF3", name=name, passed=False, detail="The service key path is not a regular file.")
    mode = stat.S_IMODE(info.st_mode)
    if mode != SERVICE_KEY_MODE:
        return ReadinessItem(id="GF3", name=name, passed=False, detail=f"The mode of the service key file is {mode:04o}. The mode must be 0600.")
    if info.st_size != SERVICE_KEY_BYTES:
        return ReadinessItem(id="GF3", name=name, passed=False, detail=f"The service key file must contain {SERVICE_KEY_BYTES} bytes.")
    return ReadinessItem(id="GF3", name=name, passed=True, detail="The service key file exists with mode 0600.")


def _check_master_key(data_dir: Path) -> ReadinessItem:
    name = "Master key"
    path = master_key_path(data_dir)
    # ``SecretService`` makes a new key file when it finds no key. This check must not do that. Thus it stops here when the environment has no key and the file does not exist.
    if not os.environ.get("CLAIRE_SECRET_KEY") and not path.is_file():
        return ReadinessItem(id="GF4", name=name, passed=False, detail="There is no master key: CLAIRE_SECRET_KEY is not set and the master key file does not exist.")
    try:
        SecretService(key_path=path)
    except (SecretError, OSError) as exc:
        return ReadinessItem(id="GF4", name=name, passed=False, detail=f"SecretService cannot load the master key ({type(exc).__name__}).")
    return ReadinessItem(id="GF4", name=name, passed=True, detail="SecretService can load the master key.")


def _check_https(runtime: WebRuntimeConfig | None, config_error: str) -> ReadinessItem:
    name = "Canonical origin uses https in production"
    if runtime is None:
        # ``WebRuntimeConfig`` refuses a production origin that does not use https. That error is the result of this check.
        return ReadinessItem(id="GF5", name=name, passed=False, detail=f"The runtime configuration is not valid: {config_error}.")
    if runtime.environment != "production":
        return ReadinessItem(id="GF5", name=name, passed=True, detail=f"The environment is {runtime.environment}. This check applies to production only.")
    passed = runtime.public_origin.lower().startswith("https://")
    return ReadinessItem(id="GF5", name=name, passed=passed, detail="The canonical origin uses https." if passed else "The canonical origin must use https in production.")


def _check_self_check(heartbeat: dict[str, Any] | None, age: float | None) -> ReadinessItem:
    name = "Sidecar public self-check"
    if heartbeat is None or not _is_recent(age):
        return ReadinessItem(id="GF6", name=name, passed=False, detail="There is no recent sidecar heartbeat. The result of the public self-check is not known.")
    self_check = heartbeat.get("self_check")
    ok = self_check.get("ok") if isinstance(self_check, dict) else None
    if ok is True:
        return ReadinessItem(id="GF6", name=name, passed=True, detail="The sidecar got its own WebFinger response through the public origin.")
    if ok is False:
        return ReadinessItem(id="GF6", name=name, passed=False, detail="The public self-check of the sidecar failed. Examine the reverse proxy rules for the federation paths.")
    return ReadinessItem(id="GF6", name=name, passed=False, detail="The sidecar did not do the public self-check yet.")


def _check_metadata(runtime: WebRuntimeConfig | None, fetch: MetadataFetch) -> ReadinessItem:
    name = "OAuth metadata through the public origin"
    if runtime is None:
        return ReadinessItem(id="GF7", name=name, passed=False, detail="The runtime configuration is not valid.")
    issuer = runtime.public_origin.rstrip("/")
    try:
        status, body = fetch(issuer + METADATA_PATH)
    except Exception as exc:  # noqa: BLE001  # A failed request is a failed check, not an error of the caller.
        return ReadinessItem(id="GF7", name=name, passed=False, detail=f"The request to the public origin failed ({type(exc).__name__}).")
    if status != 200:
        return ReadinessItem(id="GF7", name=name, passed=False, detail=f"The public origin returned HTTP status {status} for the OAuth metadata.")
    if len(body) > METADATA_MAX_BYTES:
        return ReadinessItem(id="GF7", name=name, passed=False, detail="The OAuth metadata response is too large.")
    try:
        document = json.loads(body)
    except (ValueError, TypeError):
        document = None
    if not isinstance(document, dict) or document.get("issuer") != issuer:
        return ReadinessItem(id="GF7", name=name, passed=False, detail="The response is not the OAuth metadata of this instance. Examine the reverse proxy rules.")
    return ReadinessItem(id="GF7", name=name, passed=True, detail="The public origin returns the OAuth metadata of this instance.")


def check_federation_readiness(conn: sqlite3.Connection, settings: Any, *, now: float | None = None, fetch: MetadataFetch | None = None) -> ReadinessReport:
    """Do the checks GF1 to GF7 and return the report. ``report.passed`` is ``True`` only when all checks pass.

    ``conn`` is a connection to ``claire.db``. ``settings`` gives ``data_dir`` and the values that ``WebRuntimeConfig.from_settings`` reads. ``now`` and ``fetch`` are for tests: the default values are the system clock and ``fetch_metadata``.
    """
    current = time.time() if now is None else now
    data_dir = Path(getattr(settings, "data_dir"))
    runtime: WebRuntimeConfig | None = None
    config_error = ""
    try:
        runtime = WebRuntimeConfig.from_settings(settings)
    except Exception as exc:  # noqa: BLE001  # Settings that are not valid make GF5 and GF7 fail.
        config_error = str(exc) or type(exc).__name__
    heartbeat = federation_routes.read_heartbeat(data_dir)
    age = _heartbeat_age(heartbeat, current)
    items = (
        _check_mode(conn),
        _check_heartbeat(heartbeat, age),
        _check_service_key(data_dir),
        _check_master_key(data_dir),
        _check_https(runtime, config_error),
        _check_self_check(heartbeat, age),
        _check_metadata(runtime, fetch if fetch is not None else fetch_metadata),
    )
    return ReadinessReport(items=items, passed=all(item.passed for item in items))
