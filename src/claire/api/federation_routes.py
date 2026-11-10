"""Route table of the internal federation API (Fedify sidecar design Section 6.2, unit FP1-3).

Paths are under ``federation_internal.INTERNAL_PREFIX``. ``SignedRequestMiddleware`` checks each request before a handler runs. The handlers do not read a ``Principal`` credential.

Phase FP1 has these endpoints: ``config``, ``heartbeat``, ``actors/by-username``, ``actors/{public_id}``, and ``actors/{public_id}/keys``. The other endpoints of Section 6.2 come in phases FP2 and FP3, each with the schema that it reads (FK10).

Actor rule (FK4): an account is an actor only when the instance setting ``federation`` is ``on``, the mode is ``network``, the account status is ``active``, and ``profile_public`` is 1. All other states give the same 404. An actor record has no field from ``auth.db``.

Request and response models are sealed (``extra="forbid"``, ``strict=True``). The JSON fixtures in ``tests/fixtures/federation_contract/`` are the contract that the Python tests and the sidecar tests share.
"""

from __future__ import annotations

import importlib.metadata
import json
import os
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError
from starlette.requests import Request
from starlette.responses import JSONResponse, Response
from starlette.routing import BaseRoute, Route

from ..store import accounts as accounts_store
from ..store import actor_keys
from ..store import auth_db
from ..store import db as dbm
from ..store.secrets import get_secret_service

__all__ = [
    "CONTRACT_VERSION",
    "HEARTBEAT_FILE",
    "ActorKeysResponse",
    "ActorRecord",
    "ConfigResponse",
    "HeartbeatRequest",
    "HeartbeatResponse",
    "federation_internal_routes",
    "read_heartbeat",
    "software_version",
]

# ``major.minor``. Claire refuses a sidecar that has a different major version.
CONTRACT_VERSION = "1.0"
SOFTWARE_NAME = "claire-bible"
HEARTBEAT_FILE = Path("run") / "federation_heartbeat.json"
INSTANCE_ACTOR_ID = "instance"

_NO_STORE = {"Cache-Control": "no-store"}


class _Sealed(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)


class SoftwareInfo(_Sealed):
    name: str
    version: str


class ConfigResponse(_Sealed):
    contract_version: str
    canonical_origin: str
    handle_host: str
    mode: Literal["single", "network"]
    federation: Literal["off", "on"]
    software: SoftwareInfo
    trusted_proxies: list[str]


class SelfCheck(_Sealed):
    """Result of the public self-check of the sidecar (design Section 12, GF6). ``ok`` is ``None`` before the first check."""

    ok: bool | None
    checked_at: float | None


class HeartbeatRequest(_Sealed):
    contract_version: str = Field(pattern=r"^[0-9]{1,4}\.[0-9]{1,4}$")
    sidecar_version: str = Field(min_length=1, max_length=64)
    queue_depth: int = Field(ge=0)
    self_check: SelfCheck


class HeartbeatResponse(_Sealed):
    contract_version: str
    federation: Literal["off", "on"]


class ActorRecord(_Sealed):
    public_id: str
    username: str
    display_name: str
    activated_at: float | None
    updated_at: float
    profile_url: str


class ActorKey(_Sealed):
    alg: Literal["rsa2048", "ed25519"]
    public_jwk: dict[str, str]
    private_jwk: dict[str, str]


class ActorKeysResponse(_Sealed):
    public_id: str
    keys: list[ActorKey]


def software_version() -> str:
    try:
        return importlib.metadata.version(SOFTWARE_NAME)
    except importlib.metadata.PackageNotFoundError:
        return "unknown"


def read_heartbeat(data_dir: Path | str) -> dict[str, Any] | None:
    """Return the last heartbeat record, or ``None`` when there is no valid record. The readiness check GF2 reads this."""
    try:
        record = json.loads((Path(data_dir) / HEARTBEAT_FILE).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return record if isinstance(record, dict) else None


def _json(model: BaseModel, status: int = 200) -> JSONResponse:
    return JSONResponse(model.model_dump(mode="json"), status_code=status, headers=_NO_STORE)


def _error(status: int, code: str) -> JSONResponse:
    return JSONResponse({"error": code}, status_code=status, headers=_NO_STORE)


def _not_found() -> JSONResponse:
    return _error(404, "not_found")


def federation_internal_routes(settings: Any, *, now: Callable[[], float] = time.time) -> list[BaseRoute]:
    """Make the route table for ``create_internal_federation_app``."""
    from .security import WebRuntimeConfig

    data_dir = Path(settings.data_dir)

    def _runtime() -> WebRuntimeConfig:
        return WebRuntimeConfig.from_settings(settings)

    def _open_store() -> accounts_store.AccountStore:
        return accounts_store.AccountStore(dbm.connect_existing(settings.db_file, readonly=True))

    def _federation_active(store: accounts_store.AccountStore) -> bool:
        return store.federation_state() == "on" and store.instance_mode() == "network"

    def _is_actor(account: accounts_store.Account | None) -> bool:
        return account is not None and account.status == "active" and account.profile_public and bool(account.username)

    def _record(account: accounts_store.Account) -> ActorRecord:
        return ActorRecord(
            public_id=str(account.public_id),
            username=str(account.username),
            display_name=account.display_name,
            activated_at=account.activated_at,
            updated_at=account.updated_at,
            profile_url=f"{_runtime().public_origin}/@{account.username}/",
        )

    def _find_actor(store: accounts_store.AccountStore, *, username: str | None = None, public_id: str | None = None) -> accounts_store.Account | None:
        if not _federation_active(store):
            return None
        if username is not None:
            account = store.find_by_username(username)
        else:
            # A public ID is a positive decimal number without a sign, a space, or a leading zero.
            if not (public_id and public_id.isascii() and public_id.isdigit() and public_id[0] != "0" and len(public_id) <= 19):
                return None
            account = store.find_by_public_id(int(public_id))
        return account if _is_actor(account) else None

    def config(_request: Request) -> Response:
        runtime = _runtime()
        store = _open_store()
        try:
            mode, federation = store.instance_mode(), store.federation_state()
        finally:
            store.conn.close()
        return _json(
            ConfigResponse(
                contract_version=CONTRACT_VERSION,
                canonical_origin=runtime.public_origin,
                handle_host=runtime.expected_authority,
                mode=mode,
                federation=federation,
                software=SoftwareInfo(name=SOFTWARE_NAME, version=software_version()),
                trusted_proxies=[str(network) for network in runtime.trusted_proxies],
            )
        )

    async def heartbeat(request: Request) -> Response:
        try:
            body = HeartbeatRequest.model_validate_json(await request.body())
        except ValidationError:
            return _error(400, "invalid_request")
        if body.contract_version.split(".")[0] != CONTRACT_VERSION.split(".")[0]:
            return _error(409, "contract_version_mismatch")
        record = {"received_at": now(), **body.model_dump(mode="json")}
        path = data_dir / HEARTBEAT_FILE
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary = path.with_name(path.name + ".tmp")
        temporary.write_text(json.dumps(record), encoding="utf-8")
        os.replace(temporary, path)
        store = _open_store()
        try:
            federation = store.federation_state()
        finally:
            store.conn.close()
        return _json(HeartbeatResponse(contract_version=CONTRACT_VERSION, federation=federation))

    def actor_by_username(request: Request) -> Response:
        store = _open_store()
        try:
            account = _find_actor(store, username=request.path_params["username"].lower())
        finally:
            store.conn.close()
        return _not_found() if account is None else _json(_record(account))

    def actor(request: Request) -> Response:
        store = _open_store()
        try:
            account = _find_actor(store, public_id=request.path_params["public_id"])
        finally:
            store.conn.close()
        return _not_found() if account is None else _json(_record(account))

    def keys(request: Request) -> Response:
        raw_id = request.path_params["public_id"]
        store = _open_store()
        try:
            if raw_id == INSTANCE_ACTOR_ID:
                key_owner = actor_keys.INSTANCE_ACTOR_ACCOUNT_ID if _federation_active(store) else None
            else:
                account = _find_actor(store, public_id=raw_id)
                key_owner = None if account is None else account.id
        finally:
            store.conn.close()
        if key_owner is None:
            return _not_found()
        # The first request for an actor generates its keys (design Section 7 item 1). A later request returns the same keys.
        secret_service = get_secret_service(data_dir / "auth" / "master.key")
        auth = auth_db.connect_auth_db(auth_db.auth_db_path(data_dir))
        try:
            actor_keys.ensure_actor_keys(auth, key_owner, secret_service, now=now())
            pairs = actor_keys.load_actor_keys(auth, key_owner, secret_service)
        finally:
            auth.close()
        return _json(ActorKeysResponse(public_id=raw_id, keys=[ActorKey(alg=p.alg, public_jwk=p.public_jwk, private_jwk=p.private_jwk) for p in pairs]))

    return [
        Route("/config", config, methods=["GET"]),
        Route("/heartbeat", heartbeat, methods=["POST"]),
        Route("/actors/by-username/{username}", actor_by_username, methods=["GET"]),
        Route("/actors/{public_id}", actor, methods=["GET"]),
        Route("/actors/{public_id}/keys", keys, methods=["GET"]),
    ]
