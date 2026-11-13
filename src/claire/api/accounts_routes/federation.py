"""Federation routes of the public listener (Fedify sidecar design Sections 8.3 item 6, 9.2, 11, and 12, units FP1-6, FP2-4, and FP3-1).

Admin routes. Other principals get the same 404 as for the other admin routes.

- ``GET /api/admin/federation``: the switch state, the instance mode, the readiness checks GF1 to GF7, and the last sidecar heartbeat.
- ``POST /api/admin/federation``: set the switch. The change to ``on`` requires that all readiness checks pass. The change to ``off`` has no readiness condition.
- ``GET``, ``POST``, ``DELETE /api/admin/federation/domain-blocks``: list, add, and remove a blocked domain. A change requires the same origin and recent authentication, and writes an ``admin_audit`` row.
- ``GET /admin/federation``: the console section.

Account routes, for the owner of the account, in network mode only.

- ``GET /api/federation/status``: handle, actor ID, actor state, and follower count of the account of the caller.
- ``GET /api/federation/followers``: one cursor page of the followers of the account of the caller.
- ``DELETE /api/federation/followers``: remove one follower of the account of the caller. The route deletes the row only. ``Reject(Follow)`` needs the outbox of phase FP3.

Publishing routes, for the owner or a collaborator of the account, in network mode only (design Sections 8.3 item 4, 9.1 to 9.3).

- ``POST /api/federation/publish``: publish one document of a public theme, or publish it again. The object row and the outbox task are in one transaction.
- ``POST /api/federation/retract``: delete one published object. The tombstone, the row removal, and the ``delete`` task are in one transaction.

The account of each task is the account of the caller, and the theme must be of that account. No route has a default account, and the admin cannot publish for a member (K5).

The console section ``/settings/federation`` is in ``console``.
"""

from __future__ import annotations

import asyncio
import time
import unicodedata
from typing import Any

from starlette.exceptions import HTTPException
from starlette.requests import Request
from starlette.responses import JSONResponse, Response
from starlette.routing import Route

from ... import ids
from ...accounts import federation_readiness
from ...store import accounts as accounts_store
from ...store import auth_db
from ...store import db as dbm
from ...store import federation as federation_store
from ...store import federation_client as federation_client_store
from .. import federation_routes
from ..collections import parse_collection_query
from ..principal import Principal, request_principal
from ..security import read_json_body
from .admin import is_admin
from .common import AccountsContext, json_error, not_found, open_auth_db, open_client_db, open_db, require_recent_auth, same_origin
from .console import ADMIN_SPEC_BY_SLUG, _render_console_section
from .console_federation_panels import account_federation_status, actor_state, admin_federation_body, domain_block_view

ADMIN_FEDERATION_API_PATH = "/api/admin/federation"
ADMIN_FEDERATION_PATH = "/admin/federation"
ADMIN_DOMAIN_BLOCKS_API_PATH = "/api/admin/federation/domain-blocks"
FEDERATION_STATUS_API_PATH = "/api/federation/status"
FEDERATION_FOLLOWERS_API_PATH = "/api/federation/followers"
FEDERATION_PUBLISH_API_PATH = "/api/federation/publish"
FEDERATION_RETRACT_API_PATH = "/api/federation/retract"
# FP4-2 (FEDERATION_REMOTE_READING_DESIGN.md Section 10): the reading settings of the instance and the retention of an account.
ADMIN_FEDERATION_READING_API_PATH = "/api/admin/federation/reading"
FEDERATION_SETTINGS_API_PATH = "/api/federation/settings"
READING_AUDIT_ACTION = "federation.reading.update"
DOCUMENT_ID_MAX_LENGTH = 200
AUDIT_ACTION = "federation.update"
AUDIT_DOMAIN_BLOCK_ADD = "federation.domain_block_add"
AUDIT_DOMAIN_BLOCK_REMOVE = "federation.domain_block_remove"
DOMAIN_BLOCK_NOTE_MAX_LENGTH = 200
ACTOR_IRI_MAX_LENGTH = 2048

_NO_STORE = {"Cache-Control": "no-store"}
_HEARTBEAT_KEYS = ("received_at", "contract_version", "sidecar_version", "queue_depth", "self_check")


def _heartbeat_view(data_dir: Any) -> dict[str, Any] | None:
    """Return the known fields of the last heartbeat record, or ``None`` when there is no record."""
    record = federation_routes.read_heartbeat(data_dir)
    if record is None:
        return None
    return {key: record.get(key) for key in _HEARTBEAT_KEYS}


def _clean_note(value: Any) -> str | None:
    """Return the note without spaces at its ends, or ``None`` when the value is not a short string without control characters."""
    if not isinstance(value, str):
        return None
    note = value.strip()
    if len(note) > DOMAIN_BLOCK_NOTE_MAX_LENGTH or any(unicodedata.category(ch) in {"Cc", "Cf", "Zl", "Zp"} for ch in note):
        return None
    return note


def _is_account_owner(principal: Principal) -> bool:
    return principal.scope == "owner" and principal.account_id is not None


def routes(ctx: AccountsContext) -> list[Route]:
    def _admin_change_denied(request: Request, principal: Principal) -> Response | None:
        """Guards of an admin change: admin, same origin, recent authentication. The sequence is that of ``POST /api/admin/federation``."""
        if not is_admin(principal):
            return not_found()
        if not same_origin(request):
            return json_error(403, "forbidden")
        return require_recent_auth(principal)

    async def admin_federation_get(request: Request) -> Response:
        principal = request_principal(request)
        if not is_admin(principal):
            return not_found()

        def _fetch() -> dict[str, Any]:
            conn = open_db(ctx)
            try:
                store = accounts_store.AccountStore(conn)
                report = federation_readiness.check_federation_readiness(conn, ctx.settings)
                return {
                    "federation": store.federation_state(),
                    "mode": store.instance_mode(),
                    "readiness": report.to_dict(),
                    "heartbeat": _heartbeat_view(ctx.data_dir),
                }
            finally:
                conn.close()

        # The readiness checks read files and send one HTTP request. They must not stop the event loop.
        data = await asyncio.to_thread(_fetch)
        return JSONResponse(data, headers=_NO_STORE)

    async def admin_federation_post(request: Request) -> Response:
        principal = request_principal(request)
        if not is_admin(principal):
            return not_found()
        if not same_origin(request):
            return json_error(403, "forbidden")
        denied = require_recent_auth(principal)
        if denied is not None:
            return denied

        body = await read_json_body(request)
        target = body.get("federation")
        # Sealed body: one field, and its value is a known state.
        if set(body) != {"federation"} or not isinstance(target, str) or target not in accounts_store.FEDERATION_STATES:
            return json_error(400, "invalid_request", detail=f"The body must be an object with one field, federation, that is one of {sorted(accounts_store.FEDERATION_STATES)}")

        def _execute() -> tuple[int, dict[str, Any]]:
            conn = open_db(ctx)
            try:
                store = accounts_store.AccountStore(conn)
                if target == store.federation_state():
                    return 200, {"federation": target, "changed": False}
                if target == "on":
                    report = federation_readiness.check_federation_readiness(conn, ctx.settings)
                    if not report.passed:
                        return 400, {
                            "error": "readiness_failed",
                            "detail": "Federation readiness checks did not pass",
                            "failures": [item.to_dict() for item in report.items if not item.passed],
                        }
                auth_conn = open_auth_db(ctx)
                try:
                    store.set_federation_state(target, updated_by=principal.account_id)
                    auth_db.record_admin_audit(auth_conn, actor_account_id=principal.account_id, action=AUDIT_ACTION, detail={"federation": target})
                finally:
                    auth_conn.close()
                return 200, {"federation": target, "changed": True}
            finally:
                conn.close()

        status_code, data = await asyncio.to_thread(_execute)
        return JSONResponse(data, status_code=status_code, headers=_NO_STORE)

    # --- Reading settings (FEDERATION_REMOTE_READING_DESIGN.md Section 10, FP4-2) ---

    async def admin_reading_get(request: Request) -> Response:
        if not is_admin(request_principal(request)):
            return not_found()

        def _fetch() -> dict[str, Any]:
            conn = open_db(ctx)
            try:
                return {"settings": federation_client_store.get_reading_settings(conn), "defaults": dict(federation_client_store.READING_DEFAULTS)}
            finally:
                conn.close()

        return JSONResponse(await asyncio.to_thread(_fetch), headers=_NO_STORE)

    async def admin_reading_put(request: Request) -> Response:
        principal = request_principal(request)
        denied = _admin_change_denied(request, principal)
        if denied is not None:
            return denied
        body = await read_json_body(request)
        try:
            update = federation_client_store.validate_reading_update(body)
        except federation_client_store.ReadingSettingsError as exc:
            return json_error(400, "invalid_request", detail=str(exc))

        def _execute() -> dict[str, Any]:
            conn = open_db(ctx)
            try:
                values = federation_client_store.set_reading_settings(conn, update, updated_by=principal.account_id)
                auth_conn = open_auth_db(ctx)
                try:
                    auth_db.record_admin_audit(auth_conn, actor_account_id=principal.account_id, action=READING_AUDIT_ACTION, detail=update)
                finally:
                    auth_conn.close()
                return {"settings": values}
            finally:
                conn.close()

        return JSONResponse(await asyncio.to_thread(_execute), headers=_NO_STORE)

    def _account_settings_view(client: Any, account_id: int, instance_value: int) -> dict[str, Any]:
        return {
            "retention_days": federation_client_store.get_account_retention(client, account_id),
            "instance_retention_days": instance_value,
            "effective_retention_days": federation_client_store.effective_retention_days(client, account_id, instance_value=instance_value),
        }

    def _instance_retention(conn: Any) -> int:
        return int(federation_client_store.get_reading_settings(conn)["retention_days"])

    async def federation_settings_get(request: Request) -> Response:
        account_id = _owner_account_id(request)
        if account_id is None:
            return not_found()

        def _fetch() -> dict[str, Any] | None:
            conn = open_db(ctx)
            try:
                if not _network_mode(conn):
                    return None
                instance_value = _instance_retention(conn)
            finally:
                conn.close()
            client = open_client_db(ctx)
            try:
                return _account_settings_view(client, account_id, instance_value)
            finally:
                client.close()

        data = await asyncio.to_thread(_fetch)
        return not_found() if data is None else JSONResponse(data, headers=_NO_STORE)

    async def federation_settings_put(request: Request) -> Response:
        # Same-origin rule for a cookie request: ``AuthenticationMiddleware`` (as for ``PATCH /api/me``).
        account_id = _owner_account_id(request)
        if account_id is None:
            return not_found()
        body = await read_json_body(request)
        # Sealed body: one field. ``null`` returns to the instance value.
        if not isinstance(body, dict) or set(body) != {"retention_days"}:
            return json_error(400, "invalid_request", detail="The body must be an object with one field, retention_days (null or an integer)")
        value = body["retention_days"]

        def _execute() -> tuple[int, dict[str, Any]] | None:
            conn = open_db(ctx)
            try:
                if not _network_mode(conn):
                    return None
                instance_value = _instance_retention(conn)
            finally:
                conn.close()
            client = open_client_db(ctx)
            try:
                try:
                    federation_client_store.set_account_retention(client, account_id, value, instance_max=instance_value, now=time.time())
                except federation_client_store.ReadingSettingsError as exc:
                    return 400, {"error": "invalid_request", "detail": str(exc)}
                client.commit()
                return 200, _account_settings_view(client, account_id, instance_value)
            finally:
                client.close()

        result = await asyncio.to_thread(_execute)
        if result is None:
            return not_found()
        status_code, data = result
        return JSONResponse(data, status_code=status_code, headers=_NO_STORE)

    # --- Domain blocks (design Section 11 item 1) ---

    async def domain_blocks_get(request: Request) -> Response:
        principal = request_principal(request)
        if not is_admin(principal):
            return not_found()

        def _fetch() -> list[dict[str, Any]]:
            conn = open_db(ctx)
            try:
                return [domain_block_view(block) for block in federation_store.list_domain_blocks(conn)]
            finally:
                conn.close()

        return JSONResponse({"domain_blocks": await asyncio.to_thread(_fetch)}, headers=_NO_STORE)

    async def domain_blocks_post(request: Request) -> Response:
        principal = request_principal(request)
        denied = _admin_change_denied(request, principal)
        if denied is not None:
            return denied
        assert principal.account_id is not None
        admin_id = principal.account_id

        body = await read_json_body(request)
        if "domain" not in body or not set(body) <= {"domain", "note"}:
            return json_error(400, "invalid_request", fields=["domain", "note"])
        note = _clean_note(body.get("note", ""))
        if note is None:
            return json_error(400, "invalid_note")
        try:
            domain = federation_store.normalize_domain(body["domain"])
        except ValueError:
            return json_error(400, "invalid_domain")

        def _execute() -> tuple[dict[str, Any], bool]:
            conn = open_db(ctx)
            try:
                block, made = federation_store.add_domain_block(conn, domain, created_by=admin_id, note=note, now=time.time())
                conn.commit()
            finally:
                conn.close()
            if made:
                auth_conn = open_auth_db(ctx)
                try:
                    auth_db.record_admin_audit(auth_conn, actor_account_id=admin_id, action=AUDIT_DOMAIN_BLOCK_ADD, detail={"domain": block.domain})
                finally:
                    auth_conn.close()
            return domain_block_view(block), made

        view, made = await asyncio.to_thread(_execute)
        # An existing block is not changed: its note and its time stay.
        return JSONResponse({"domain_block": view, "created": made}, status_code=201 if made else 200, headers=_NO_STORE)

    async def domain_blocks_delete(request: Request) -> Response:
        principal = request_principal(request)
        denied = _admin_change_denied(request, principal)
        if denied is not None:
            return denied
        assert principal.account_id is not None
        admin_id = principal.account_id

        body = await read_json_body(request)
        if set(body) != {"domain"}:
            return json_error(400, "invalid_request", fields=["domain"])
        try:
            domain = federation_store.normalize_domain(body["domain"])
        except ValueError:
            return json_error(400, "invalid_domain")

        def _execute() -> bool:
            conn = open_db(ctx)
            try:
                removed = federation_store.remove_domain_block(conn, domain)
                conn.commit()
            finally:
                conn.close()
            if removed:
                auth_conn = open_auth_db(ctx)
                try:
                    auth_db.record_admin_audit(auth_conn, actor_account_id=admin_id, action=AUDIT_DOMAIN_BLOCK_REMOVE, detail={"domain": domain})
                finally:
                    auth_conn.close()
            return removed

        if not await asyncio.to_thread(_execute):
            return json_error(404, "domain_block_not_found")
        return JSONResponse({"removed": True, "domain": domain}, headers=_NO_STORE)

    # --- Console section /admin/federation ---

    async def admin_federation_page(request: Request) -> Response:
        principal = request_principal(request)
        if not is_admin(principal):
            return not_found()
        raw_theme = request.query_params.get("theme") if "theme" in request.query_params else None

        def _render() -> Response:
            body_html, extra = admin_federation_body(ctx)
            return _render_console_section(ctx, spec=ADMIN_SPEC_BY_SLUG["federation"], principal=principal, raw_theme=raw_theme, custom_body_html=body_html, extra_bootstrap={"admin_federation": extra})

        return await asyncio.to_thread(_render)

    # --- Account routes (design Sections 9.2 and 11 item 2) ---

    def _owner_account_id(request: Request) -> int | None:
        """Return the account of the caller, or ``None`` when the caller is not the owner of an account."""
        principal = request_principal(request)
        return principal.account_id if _is_account_owner(principal) else None

    def _network_mode(conn: Any) -> bool:
        # ``ROUTE_POLICY`` gives 404 in single mode before the handler runs. This check keeps the handler safe without the policy.
        return accounts_store.AccountStore(conn).instance_mode() == "network"

    async def federation_status_get(request: Request) -> Response:
        account_id = _owner_account_id(request)
        if account_id is None:
            return not_found()

        def _fetch() -> dict[str, Any] | None:
            conn = open_db(ctx)
            try:
                return account_federation_status(ctx, conn, account_id) if _network_mode(conn) else None
            finally:
                conn.close()

        data = await asyncio.to_thread(_fetch)
        return not_found() if data is None else JSONResponse(data, headers=_NO_STORE)

    async def federation_followers_get(request: Request) -> Response:
        account_id = _owner_account_id(request)
        if account_id is None:
            return not_found()
        try:
            query = parse_collection_query(request.query_params)
        except HTTPException as exc:
            return json_error(400, "invalid_request", detail=str(exc.detail))

        def _fetch() -> dict[str, Any] | None:
            conn = open_db(ctx)
            try:
                if not _network_mode(conn):
                    return None
                total = federation_store.count_followers(conn, account_id)
                rows = federation_store.list_followers(conn, account_id, limit=query.limit, max_id=query.max_id, min_id=query.min_id, since_id=query.since_id)
            finally:
                conn.close()
            return {
                "total": total,
                # Newest first. The inbox addresses are not in the result: the owner does not need them.
                "items": [{"id": str(row.public_id), "actor_iri": row.actor_iri, "created_at": row.created_at} for row in rows],
                # The ``max_id`` of the next page, or ``None`` when this page is not full.
                "next_max_id": str(rows[-1].public_id) if len(rows) == query.limit else None,
            }

        data = await asyncio.to_thread(_fetch)
        return not_found() if data is None else JSONResponse(data, headers=_NO_STORE)

    async def federation_followers_delete(request: Request) -> Response:
        # The same-origin rule for a cookie request is in ``AuthenticationMiddleware``, as for the other account routes (``PATCH /api/me``). The handler adds no second rule.
        account_id = _owner_account_id(request)
        if account_id is None:
            return not_found()

        body = await read_json_body(request)
        actor_iri = body.get("actor_iri")
        if set(body) != {"actor_iri"} or not isinstance(actor_iri, str) or not actor_iri or len(actor_iri) > ACTOR_IRI_MAX_LENGTH:
            return json_error(400, "invalid_request", fields=["actor_iri"])

        def _execute() -> tuple[bool, int] | None:
            conn = open_db(ctx)
            try:
                if not _network_mode(conn):
                    return None
                # The key has the account of the caller. Thus the route cannot delete a follower of a different account.
                removed = federation_store.remove_follower(conn, account_id, actor_iri)
                conn.commit()
                return removed, federation_store.count_followers(conn, account_id)
            finally:
                conn.close()

        result = await asyncio.to_thread(_execute)
        if result is None:
            return not_found()
        removed, total = result
        if not removed:
            return json_error(404, "follower_not_found")
        return JSONResponse({"removed": True, "followers": {"total": total}}, headers=_NO_STORE)

    # --- Publishing routes (design Sections 9.1 to 9.3, unit FP3-1) ---

    def _publisher(request: Request) -> Principal | None:
        """Return the principal when it can cause an outbound activity: owner or collaborator access with an account. A ``readonly`` credential cannot (design Section 8.3 item 4)."""
        principal = request_principal(request)
        return principal if principal.scope in ("owner", "collaborator") and principal.account_id is not None else None

    def _own_theme(conn: Any, principal: Principal, *, theme_id: int | None = None, number: int | None = None) -> Any:
        """Return the theme row when it is of the account of the caller and the caller can use it. All other conditions give ``None``, so that the response does not show that a theme of a different account exists."""
        if theme_id is not None:
            row = conn.execute("SELECT id, account_id, is_public, is_collaborator_accessible FROM themes WHERE id=?", (theme_id,)).fetchone()
        else:
            row = conn.execute("SELECT id, account_id, is_public, is_collaborator_accessible FROM themes WHERE number=?", (number,)).fetchone()
        if row is None or row["account_id"] != principal.account_id:
            return None
        if principal.scope == "collaborator" and not row["is_collaborator_accessible"]:
            return None
        return row

    def _object_view(obj: federation_store.FederationObject) -> dict[str, Any]:
        return {"id": str(obj.public_id), "url": f"{ctx.config.public_origin}/ap/objects/{obj.public_id}", "published_at": obj.published_at, "updated_at": obj.updated_at}

    async def federation_publish_post(request: Request) -> Response:
        principal = _publisher(request)
        if principal is None:
            return not_found()
        account_id = principal.account_id
        assert account_id is not None
        body = await read_json_body(request)
        document_id = body.get("document_id")
        if set(body) != {"theme", "document_id"} or not isinstance(document_id, str) or not document_id or len(document_id) > DOCUMENT_ID_MAX_LENGTH:
            return json_error(400, "invalid_request", fields=["theme", "document_id"])
        try:
            number = ids.parse_public_id(body["theme"]) if isinstance(body["theme"], str) else None
        except ValueError:
            number = None
        if number is None:
            return json_error(400, "invalid_request", fields=["theme", "document_id"])

        def _execute() -> tuple[int, dict[str, Any]] | None:
            conn = open_db(ctx)
            try:
                store = accounts_store.AccountStore(conn)
                if not _network_mode(conn):
                    return None
                theme = _own_theme(conn, principal, number=number)
                if theme is None:
                    return 404, {"error": "theme_not_found"}
                state = actor_state(store.get(account_id), federation=store.federation_state())
                if state != "active":
                    return 409, {"error": "not_an_actor", "actor_state": state}
                # Only a document of a public theme can be published (design Section 9.1).
                if not theme["is_public"]:
                    return 409, {"error": "theme_not_public"}
                document = dbm.get_document_row(conn, document_id, theme_id=int(theme["id"]))
                if document is None:
                    return 404, {"error": "document_not_found"}
                if document["hidden"]:
                    return 409, {"error": "document_not_public"}
                if not (document["detail"] or "").strip() and not (document["detail_html"] or "").strip():
                    return 409, {"error": "document_has_no_body"}
                # The public page of the document is its share page. This call commits, so it comes before the transaction of the object and the task.
                dbm.create_doc_share(conn, document_id, reuse_existing=True, theme_id=int(theme["id"]))
                try:
                    obj, task, created = federation_store.publish_document(conn, account_id=account_id, theme_id=int(theme["id"]), document_id=document_id, published_by_account_id=account_id, salt=store.id_salt(), now=time.time())
                    conn.commit()
                except BaseException:
                    conn.rollback()
                    raise
                return 202, {"object": _object_view(obj), "task": {"id": task.id, "kind": task.kind}, "created": created}
            finally:
                conn.close()

        result = await asyncio.to_thread(_execute)
        if result is None:
            return not_found()
        return JSONResponse(result[1], status_code=result[0], headers=_NO_STORE)

    async def federation_retract_post(request: Request) -> Response:
        principal = _publisher(request)
        if principal is None:
            return not_found()
        body = await read_json_body(request)
        try:
            object_id = ids.parse_public_id(body["object_id"]) if set(body) == {"object_id"} and isinstance(body["object_id"], str) else None
        except ValueError:
            object_id = None
        if object_id is None:
            return json_error(400, "invalid_request", fields=["object_id"])

        def _execute() -> tuple[int, dict[str, Any]] | None:
            conn = open_db(ctx)
            try:
                if not _network_mode(conn):
                    return None
                obj = federation_store.get_object(conn, object_id)
                # An object of a different account gives the same answer as no object.
                if obj is None or obj.account_id != principal.account_id or _own_theme(conn, principal, theme_id=obj.theme_id) is None:
                    return 404, {"error": "object_not_found"}
                try:
                    task = federation_store.retract_object(conn, obj, now=time.time())
                    conn.commit()
                except BaseException:
                    conn.rollback()
                    raise
                return 202, {"retracted": True, "object_id": str(obj.public_id), "task": {"id": task.id, "kind": task.kind}}
            finally:
                conn.close()

        result = await asyncio.to_thread(_execute)
        if result is None:
            return not_found()
        return JSONResponse(result[1], status_code=result[0], headers=_NO_STORE)

    return [
        Route(FEDERATION_PUBLISH_API_PATH, federation_publish_post, methods=["POST"]),
        Route(FEDERATION_RETRACT_API_PATH, federation_retract_post, methods=["POST"]),
        Route(ADMIN_FEDERATION_API_PATH, admin_federation_get, methods=["GET"]),
        Route(ADMIN_FEDERATION_API_PATH, admin_federation_post, methods=["POST"]),
        Route(ADMIN_FEDERATION_READING_API_PATH, admin_reading_get, methods=["GET"]),
        Route(ADMIN_FEDERATION_READING_API_PATH, admin_reading_put, methods=["PUT"]),
        Route(FEDERATION_SETTINGS_API_PATH, federation_settings_get, methods=["GET"]),
        Route(FEDERATION_SETTINGS_API_PATH, federation_settings_put, methods=["PUT"]),
        Route(ADMIN_DOMAIN_BLOCKS_API_PATH, domain_blocks_get, methods=["GET"]),
        Route(ADMIN_DOMAIN_BLOCKS_API_PATH, domain_blocks_post, methods=["POST"]),
        Route(ADMIN_DOMAIN_BLOCKS_API_PATH, domain_blocks_delete, methods=["DELETE"]),
        Route(ADMIN_FEDERATION_PATH, admin_federation_page, methods=["GET"]),
        Route(FEDERATION_STATUS_API_PATH, federation_status_get, methods=["GET"]),
        Route(FEDERATION_FOLLOWERS_API_PATH, federation_followers_get, methods=["GET"]),
        Route(FEDERATION_FOLLOWERS_API_PATH, federation_followers_delete, methods=["DELETE"]),
    ]
