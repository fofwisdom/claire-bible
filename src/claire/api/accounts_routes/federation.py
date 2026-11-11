"""Admin API for the instance setting ``federation`` (Fedify sidecar design Sections 8.3 item 6 and 12, unit FP1-6).

- ``GET /api/admin/federation``: the switch state, the instance mode, the readiness checks GF1 to GF7, and the last sidecar heartbeat.
- ``POST /api/admin/federation``: set the switch. The change to ``on`` requires that all readiness checks pass. The change to ``off`` has no readiness condition.

The two routes are for the admin only. Other principals get the same 404 as for the other admin routes. The console screen ``/admin/federation`` is unit FP2-4.
"""

from __future__ import annotations

import asyncio
from typing import Any

from starlette.requests import Request
from starlette.responses import JSONResponse, Response
from starlette.routing import Route

from ...accounts import federation_readiness
from ...store import accounts as accounts_store
from ...store import auth_db
from .. import federation_routes
from ..principal import request_principal
from ..security import read_json_body
from .admin import is_admin
from .common import AccountsContext, json_error, not_found, open_auth_db, open_db, require_recent_auth, same_origin

ADMIN_FEDERATION_API_PATH = "/api/admin/federation"
AUDIT_ACTION = "federation.update"

_NO_STORE = {"Cache-Control": "no-store"}
_HEARTBEAT_KEYS = ("received_at", "contract_version", "sidecar_version", "queue_depth", "self_check")


def _heartbeat_view(data_dir: Any) -> dict[str, Any] | None:
    """Return the known fields of the last heartbeat record, or ``None`` when there is no record."""
    record = federation_routes.read_heartbeat(data_dir)
    if record is None:
        return None
    return {key: record.get(key) for key in _HEARTBEAT_KEYS}


def routes(ctx: AccountsContext) -> list[Route]:
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

    return [
        Route(ADMIN_FEDERATION_API_PATH, admin_federation_get, methods=["GET"]),
        Route(ADMIN_FEDERATION_API_PATH, admin_federation_post, methods=["POST"]),
    ]
