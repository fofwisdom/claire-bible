"""Route table of the internal federation API (Fedify sidecar design Section 6.2, unit FP1-3).

Paths are under ``federation_internal.INTERNAL_PREFIX``. ``SignedRequestMiddleware`` checks each request before a handler runs. The handlers do not read a ``Principal`` credential.

Phase FP1 has these endpoints: ``config``, ``heartbeat``, ``actors/by-username``, ``actors/{public_id}``, and ``actors/{public_id}/keys``. Unit FP2-2 adds ``inbox-events`` (Section 6.3), ``actors/{public_id}/followers``, and ``domain-blocks``. The other endpoints of Section 6.2 come in phase FP3, each with the schema that it reads (FK10).

Actor rule (FK4): an account is an actor only when the instance setting ``federation`` is ``on``, the mode is ``network``, the account status is ``active``, and ``profile_public`` is 1. All other states give the same 404. An actor record has no field from ``auth.db``.

Request and response models are sealed (``extra="forbid"``, ``strict=True``). The JSON fixtures in ``tests/fixtures/federation_contract/`` are the contract that the Python tests and the sidecar tests share.
"""

from __future__ import annotations

import importlib.metadata
import json
import logging
import os
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any, Literal
from urllib.parse import urlsplit

from pydantic import BaseModel, ConfigDict, Field, ValidationError
from starlette.concurrency import run_in_threadpool
from starlette.exceptions import HTTPException
from starlette.requests import Request
from starlette.responses import JSONResponse, Response
from starlette.routing import BaseRoute, Route

from ..store import accounts as accounts_store
from ..store import actor_keys
from ..store import auth_db
from .. import ids
from ..store import db as dbm
from ..store import federation as federation_store
from ..store.secrets import get_secret_service
from .collections import parse_collection_query

__all__ = [
    "CONTRACT_VERSION",
    "HEARTBEAT_FILE",
    "INBOX_BODY_LIMIT_BYTES",
    "ActorKeysResponse",
    "ActorRecord",
    "ConfigResponse",
    "DomainBlocksResponse",
    "FollowersResponse",
    "HeartbeatRequest",
    "HeartbeatResponse",
    "InboxEventRequest",
    "InboxEventResponse",
    "federation_internal_routes",
    "read_heartbeat",
    "software_version",
]

# ``major.minor``. Claire refuses a sidecar that has a different major version.
CONTRACT_VERSION = "1.1"
SOFTWARE_NAME = "claire-bible"
HEARTBEAT_FILE = Path("run") / "federation_heartbeat.json"
INSTANCE_ACTOR_ID = "instance"
# The largest body of a public inbox request that the sidecar reads (design Section 11 item 4). The sidecar gets the value from ``config``.
INBOX_BODY_LIMIT_BYTES = 1024 * 1024
_MAX_IRI_LENGTH = 2048

_log = logging.getLogger("claire.federation.routes")

_NO_STORE = {"Cache-Control": "no-store"}


class _Sealed(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)


class SoftwareInfo(_Sealed):
    name: str
    version: str


class Limits(_Sealed):
    inbox_body_bytes: int


class ConfigResponse(_Sealed):
    contract_version: str
    canonical_origin: str
    handle_host: str
    mode: Literal["single", "network"]
    federation: Literal["off", "on"]
    software: SoftwareInfo
    trusted_proxies: list[str]
    # Number of federation actors. NodeInfo shows this number only (design Section 5.1 rule 6). It is 0 when federation is not active.
    federated_actors: int
    limits: Limits
    # Entity tag of the ``domain-blocks`` response. The sidecar reads the list again when this value changes.
    domain_blocks_etag: str


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


class InboxActor(_Sealed):
    iri: str = Field(min_length=1, max_length=_MAX_IRI_LENGTH)
    inbox: str = Field(min_length=1, max_length=_MAX_IRI_LENGTH)
    shared_inbox: str | None = Field(min_length=1, max_length=_MAX_IRI_LENGTH)


class InboxEventRequest(_Sealed):
    """One inbound activity that the sidecar verified (design Section 6.3).

    ``type`` is the type of the activity. ``object_iri`` is the ID of its object. For an ``Undo``, ``object_type`` is the type of the activity that is undone and ``object_iri`` is the object of that activity. ``recipient_public_id`` is the actor of the inbox that got the activity, or ``None`` for the shared inbox.
    """

    event_id: str = Field(min_length=1, max_length=_MAX_IRI_LENGTH)
    type: str = Field(min_length=1, max_length=64)
    object_type: str | None = Field(min_length=1, max_length=64)
    recipient_public_id: str | None = Field(pattern=r"^[1-9][0-9]{0,18}$")
    actor: InboxActor
    object_iri: str | None = Field(min_length=1, max_length=_MAX_IRI_LENGTH)
    verified_by: Literal["http-signature", "ld-signature", "object-integrity-proof"]


class InboxEventResponse(_Sealed):
    decision: Literal["accepted", "rejected", "ignored"]
    # ``accept``: the sidecar sends ``Accept(Follow)``.
    reply: Literal["accept"] | None
    # A code for the log and the telemetry of the sidecar. ``None`` for ``accepted``.
    reason: Literal["federation_off", "invalid_actor", "blocked_domain", "not_an_actor", "recipient_mismatch", "unsupported_type"] | None


class FollowerItem(_Sealed):
    id: str
    actor_iri: str
    inbox_iri: str
    shared_inbox_iri: str | None


class FollowersResponse(_Sealed):
    public_id: str
    total: int
    # Newest first.
    items: list[FollowerItem]
    # The ``max_id`` of the next page, or ``None`` when this page is not full.
    next_max_id: str | None


class DomainBlocksResponse(_Sealed):
    etag: str
    # Lowercase ASCII, in order. A block of a domain also applies to its subdomains.
    domains: list[str]


def _remote_host(iri: str, origin: str) -> str | None:
    """Return the normalized host of the address of a remote server, or ``None`` when the address is not permitted.

    The scheme must be ``https``. ``http`` is permitted only when the canonical origin of this instance uses ``http`` (development). An address with credentials, or on the origin of this instance, is not permitted.
    """
    try:
        parts = urlsplit(iri)
        credentials = parts.username is not None or parts.password is not None
    except ValueError:
        return None
    if parts.scheme != "https" and not (parts.scheme == "http" and origin.startswith("http://")):
        return None
    if credentials or f"{parts.scheme}://{parts.netloc}".lower() == origin.lower():
        return None
    return federation_store.iri_host(iri)


def _event_kind(event: InboxEventRequest) -> Literal["follow", "undo_follow", "delete_actor", "update_actor"] | None:
    """Return the v1 operation of an event, or ``None`` for a type outside the v1 set (design Section 6.3)."""
    if event.type == "Follow":
        return "follow"
    if event.type == "Undo" and event.object_type == "Follow":
        return "undo_follow"
    if event.type == "Delete" and event.object_iri == event.actor.iri:
        return "delete_actor"
    if event.type == "Update" and event.object_iri == event.actor.iri:
        return "update_actor"
    return None


def _local_actor_public_id(origin: str, iri: str | None) -> str | None:
    """Return the public ID in an actor ID of this instance (``{origin}/ap/actors/{public_id}``), or ``None``."""
    prefix = f"{origin}/ap/actors/"
    if iri is None or not iri.startswith(prefix):
        return None
    raw = iri[len(prefix) :]
    try:
        ids.parse_public_id(raw)
    except ValueError:
        return None
    return raw


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

    def _domain_blocks(conn: Any) -> tuple[str, list[str]]:
        domains = [block.domain for block in federation_store.list_domain_blocks(conn)]
        return federation_store.domain_blocks_etag(domains), domains

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
            blocks_etag, _domains = _domain_blocks(store.conn)
            federated_actors = 0
            if _federation_active(store):
                federated_actors = int(store.conn.execute("SELECT COUNT(*) FROM accounts WHERE status='active' AND profile_public=1 AND username IS NOT NULL").fetchone()[0])
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
                federated_actors=federated_actors,
                limits=Limits(inbox_body_bytes=INBOX_BODY_LIMIT_BYTES),
                domain_blocks_etag=blocks_etag,
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

    def _decide(store: accounts_store.AccountStore, origin: str, event: InboxEventRequest) -> InboxEventResponse:
        """Apply one event and return the decision (design Section 6.3). The caller commits."""

        def rejected(reason: Any) -> InboxEventResponse:
            return InboxEventResponse(decision="rejected", reply=None, reason=reason)

        conn = store.conn
        if not _federation_active(store):
            return rejected("federation_off")
        hosts = [_remote_host(address, origin) for address in (event.actor.iri, event.actor.inbox, event.actor.shared_inbox) if address is not None]
        if any(host is None for host in hosts):
            return rejected("invalid_actor")
        # Claire makes the final check of the domain blocks (design Section 11 item 1).
        if any(federation_store.blocking_domain(conn, host) is not None for host in hosts if host is not None):
            return rejected("blocked_domain")
        kind = _event_kind(event)
        if kind is None:
            return InboxEventResponse(decision="ignored", reply=None, reason="unsupported_type")
        if kind == "delete_actor":
            federation_store.remove_followers_of_actor(conn, event.actor.iri)
            return InboxEventResponse(decision="accepted", reply=None, reason=None)
        if kind == "update_actor":
            federation_store.update_follower_inboxes(conn, event.actor.iri, inbox_iri=event.actor.inbox, shared_inbox_iri=event.actor.shared_inbox, now=now())
            return InboxEventResponse(decision="accepted", reply=None, reason=None)

        target = _local_actor_public_id(origin, event.object_iri)
        if target is None:
            return rejected("not_an_actor")
        if event.recipient_public_id is not None and event.recipient_public_id != target:
            return rejected("recipient_mismatch")
        if kind == "follow":
            account = _find_actor(store, public_id=target)
            if account is None:
                return rejected("not_an_actor")
            # The follower key is (account_id, actor_iri). A repeated ``Follow`` keeps the row and gives the same decision.
            federation_store.upsert_follower(conn, account_id=account.id, actor_iri=event.actor.iri, inbox_iri=event.actor.inbox, shared_inbox_iri=event.actor.shared_inbox, salt=store.id_salt(), now=now())
            return InboxEventResponse(decision="accepted", reply="accept", reason=None)
        # ``Undo(Follow)``: a paused account also loses the follower. Pause keeps followers, but it must not keep one that left.
        account = store.find_by_public_id(int(target))
        if account is None:
            return rejected("not_an_actor")
        federation_store.remove_follower(conn, account.id, event.actor.iri)
        return InboxEventResponse(decision="accepted", reply=None, reason=None)

    async def inbox_event(request: Request) -> Response:
        try:
            event = InboxEventRequest.model_validate_json(await request.body())
        except ValidationError:
            return _error(400, "invalid_request")
        origin = _runtime().public_origin

        def _apply() -> InboxEventResponse:
            store = accounts_store.AccountStore(dbm.connect_existing(settings.db_file))
            try:
                decision = _decide(store, origin, event)
                store.conn.commit()
                return decision
            finally:
                store.conn.close()

        decision = await run_in_threadpool(_apply)
        _log.info("inbox event %s: %s%s", event.type, decision.decision, "" if decision.reason is None else f" ({decision.reason})")
        return _json(decision)

    def followers(request: Request) -> Response:
        try:
            query = parse_collection_query(request.query_params)
        except HTTPException:
            return _error(400, "invalid_request")
        store = _open_store()
        try:
            account = _find_actor(store, public_id=request.path_params["public_id"])
            if account is None:
                return _not_found()
            total = federation_store.count_followers(store.conn, account.id)
            rows = federation_store.list_followers(store.conn, account.id, limit=query.limit, max_id=query.max_id, min_id=query.min_id, since_id=query.since_id)
        finally:
            store.conn.close()
        return _json(
            FollowersResponse(
                public_id=str(account.public_id),
                total=total,
                items=[FollowerItem(id=str(row.public_id), actor_iri=row.actor_iri, inbox_iri=row.inbox_iri, shared_inbox_iri=row.shared_inbox_iri) for row in rows],
                next_max_id=str(rows[-1].public_id) if len(rows) == query.limit else None,
            )
        )

    def domain_blocks(request: Request) -> Response:
        store = _open_store()
        try:
            etag, domains = _domain_blocks(store.conn)
        finally:
            store.conn.close()
        headers = {**_NO_STORE, "ETag": etag}
        if request.headers.get("if-none-match") == etag:
            return Response(status_code=304, headers=headers)
        return JSONResponse(DomainBlocksResponse(etag=etag, domains=domains).model_dump(mode="json"), headers=headers)

    return [
        Route("/config", config, methods=["GET"]),
        Route("/heartbeat", heartbeat, methods=["POST"]),
        Route("/actors/by-username/{username}", actor_by_username, methods=["GET"]),
        Route("/actors/{public_id}", actor, methods=["GET"]),
        Route("/actors/{public_id}/keys", keys, methods=["GET"]),
        Route("/actors/{public_id}/followers", followers, methods=["GET"]),
        Route("/inbox-events", inbox_event, methods=["POST"]),
        Route("/domain-blocks", domain_blocks, methods=["GET"]),
    ]
