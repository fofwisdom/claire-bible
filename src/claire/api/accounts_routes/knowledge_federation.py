"""Admin routes of the federation of shared knowledge (KNOWLEDGE_FEDERATION_DESIGN.md Sections 6.2, 6.5, 13, and 14; unit KF1).

The routes are for the admin only. Other principals get the same 404 as for the other admin routes. The console section ``/admin/federation`` (``console_admin.js``) reads them.

- ``GET /api/admin/federation/known-servers``: the known servers, with the switches of Section 14.
- ``POST /api/admin/federation/known-servers``: the admin adds a domain (Section 6.2 rule 3).
- ``DELETE /api/admin/federation/known-servers``: the admin removes a known server and what this server keeps of it (``remove_known_server``).
- ``GET /api/admin/federation/journal``: the inbox journal, newest first.
- ``GET /api/admin/federation/journal/{id}``: one row with the activity as it came.
- ``POST /api/admin/federation/journal/{id}/replay``: process a failed row again (Section 13 rule 3).

A change requires the same origin and a recent authentication, and writes an ``admin_audit`` row (KS7). The switches ``federation`` and ``kb_auto_contribution`` have their own routes (``POST /api/admin/federation``, ``PUT /api/admin/auto-contribution``); the domain blocks have ``/api/admin/federation/domain-blocks``.
"""

from __future__ import annotations

import asyncio
import time
from typing import Any

from starlette.requests import Request
from starlette.responses import JSONResponse, Response
from starlette.routing import Route

from ...collaboration import auto_contribution
from ...federation.knowledge import handlers as kf_handlers  # noqa: F401 - the import installs the handlers of unit KF3 in the journal
from ...federation.knowledge import actors, journal, known_servers, outbox, remote_documents, remote_kbs
from ...store import auth_db
from ...store import federation as federation_store
from ..principal import Principal, request_principal
from ..security import read_json_body
from .admin import is_admin
from .common import AccountsContext, json_error, not_found, open_auth_db, open_db, require_recent_auth, same_origin

ADMIN_KNOWN_SERVERS_API_PATH = "/api/admin/federation/known-servers"
ADMIN_JOURNAL_API_PATH = "/api/admin/federation/journal"
AUDIT_KNOWN_SERVER_ADD = "federation.known_server_add"
AUDIT_KNOWN_SERVER_REMOVE = "federation.known_server_remove"
AUDIT_JOURNAL_REPLAY = "federation.journal_replay"
JOURNAL_PAGE_DEFAULT = 50
JOURNAL_PAGE_MAX = 200

_NO_STORE = {"Cache-Control": "no-store"}


def _positive_int(raw: str | None) -> int | None:
    """Return the value of a decimal number without a sign and without a leading zero, or ``None``."""
    if raw is None or not raw.isascii() or not raw.isdigit() or raw[0] == "0" or len(raw) > 18:
        return None
    return int(raw)


def known_server_view(conn: Any, server: known_servers.KnownServer) -> dict[str, Any]:
    """One known server for the admin: ``known_servers.server_view`` and the data of the store of remote knowledge bases (unit KF2).

    - ``kbs``: the records of the shared knowledge bases of the server, in the order of the label.
    - ``kb_count``: the number of these records after a complete read of the ``/kb`` collection; before that read, the number that the read of the server gave (it can be ``None``).
    - ``kb_read``: the state of the reads of the ``/kb`` collection, or ``None`` when no read started.
    """
    view = known_servers.server_view(conn, server)
    records = remote_kbs.list_of_domain(conn, server.domain)
    state = remote_kbs.read_state(conn, server.domain)
    view["kbs"] = [remote_kbs.view(kb) for kb in records]
    if state is not None and state.last_read_at is not None:
        view["kb_count"] = len(records)
    view["kb_read"] = None if state is None else {"last_read_at": state.last_read_at, "stored": state.stored_count, "refused": state.refused_count, "refused_code": state.refused_code, "error_count": state.error_count, "last_error": state.last_error, "last_error_at": state.last_error_at}
    return view


def remove_known_server(conn: Any, domain: str, *, now: float) -> dict[str, int] | None:
    """Remove one known server and what this server keeps of it (design Sections 6.2 and 6.3). Return the counts of what was deleted, or None when the domain is not a known server. The caller commits.

    | Deleted | Stays |
    | :--- | :--- |
    | The row of the known server; the records of its shared knowledge bases with their theme memberships, the lists of their documents, and the state of the reads (``knowledge_bases``, ``listed_documents``) | The copies of its documents in the shared knowledge bases of this server, with their origin (design Section 10.1) |
    | Its server actors as followers of the knowledge base actors of this server (``followers``): no activity goes to that server until it follows again | The follows of this server: ``outbox.sync`` ends the follow of a record that is gone, and keeps the follow of an actor that this server holds copies from |
    | The tasks for that server that wait: ``refused`` with ``server_removed`` (``outbox_tasks``) | The rows of the inbox journal until their retention ends; a ``failed`` row keeps its payload for a replay |
    | The payload of each ``processed`` and ``refused`` row of the inbox journal of that server (``journal_payloads``) | A domain block: the admin removes it in its own list |

    The server becomes known again at its next contact (design Section 6.2).
    """
    if not known_servers.remove_server(conn, domain):
        return None
    return {
        "knowledge_bases": remote_kbs.remove_domain(conn, domain),
        "listed_documents": remote_documents.remove_listings_of_domain(conn, domain),
        "followers": actors.remove_followers_of_domain(conn, domain),
        "outbox_tasks": outbox.refuse_tasks_of_domain(conn, domain, error_code="server_removed", now=now),
        "journal_payloads": journal.drop_payloads_of_domain(conn, domain),
    }


def routes(ctx: AccountsContext) -> list[Route]:
    def _admin_change_denied(request: Request, principal: Principal) -> Response | None:
        """Guards of an admin change: admin, same origin, recent authentication. The sequence is that of ``POST /api/admin/federation``."""
        if not is_admin(principal):
            return not_found()
        if not same_origin(request):
            return json_error(403, "forbidden")
        return require_recent_auth(principal)

    # --- Known servers (design Sections 6.2 and 6.5) ---

    async def known_servers_get(request: Request) -> Response:
        if not is_admin(request_principal(request)):
            return not_found()

        def _fetch() -> dict[str, Any]:
            conn = open_db(ctx)
            try:
                return {
                    "servers": [known_server_view(conn, server) for server in known_servers.list_servers(conn)],
                    "switches": {
                        "kb_auto_contribution": "on" if auto_contribution.switch_on(conn) else "off",
                        # Not a switch: a collection of shared knowledge always needs a signed request of a server actor (design Section 6.3).
                        "signed_get": "required",
                    },
                }
            finally:
                conn.close()

        return JSONResponse(await asyncio.to_thread(_fetch), headers=_NO_STORE)

    async def known_servers_post(request: Request) -> Response:
        principal = request_principal(request)
        denied = _admin_change_denied(request, principal)
        if denied is not None:
            return denied
        assert principal.account_id is not None
        admin_id = principal.account_id

        body = await read_json_body(request)
        # Sealed body: one field.
        if not isinstance(body, dict) or set(body) != {"domain"}:
            return json_error(400, "invalid_request", fields=["domain"])
        try:
            domain = federation_store.normalize_domain(body["domain"])
        except ValueError:
            return json_error(400, "invalid_domain")
        # This server is not a known server of itself.
        if domain == federation_store.iri_host(ctx.config.public_origin):
            return json_error(400, "own_domain")

        def _execute() -> tuple[dict[str, Any], bool]:
            conn = open_db(ctx)
            try:
                server, made = known_servers.note_server(conn, domain, source="admin", added_by=admin_id, now=time.time())
                conn.commit()
                view = known_server_view(conn, server)
            finally:
                conn.close()
            if made:
                auth_conn = open_auth_db(ctx)
                try:
                    auth_db.record_admin_audit(auth_conn, actor_account_id=admin_id, action=AUDIT_KNOWN_SERVER_ADD, detail={"domain": domain})
                finally:
                    auth_conn.close()
            return view, made

        view, made = await asyncio.to_thread(_execute)
        # A server that is known does not change: how it became known stays.
        return JSONResponse({"server": view, "created": made}, status_code=201 if made else 200, headers=_NO_STORE)

    async def known_servers_delete(request: Request) -> Response:
        principal = request_principal(request)
        denied = _admin_change_denied(request, principal)
        if denied is not None:
            return denied
        assert principal.account_id is not None
        admin_id = principal.account_id

        body = await read_json_body(request)
        # Sealed body: one field, as ``DELETE /api/admin/federation/domain-blocks``.
        if not isinstance(body, dict) or set(body) != {"domain"}:
            return json_error(400, "invalid_request", fields=["domain"])
        try:
            domain = federation_store.normalize_domain(body["domain"])
        except ValueError:
            return json_error(400, "invalid_domain")

        def _execute() -> dict[str, int] | None:
            conn = open_db(ctx)
            try:
                deleted = remove_known_server(conn, domain, now=time.time())
                conn.commit()
            finally:
                conn.close()
            if deleted is not None:
                auth_conn = open_auth_db(ctx)
                try:
                    auth_db.record_admin_audit(auth_conn, actor_account_id=admin_id, action=AUDIT_KNOWN_SERVER_REMOVE, detail={"domain": domain, **deleted})
                finally:
                    auth_conn.close()
            return deleted

        deleted = await asyncio.to_thread(_execute)
        if deleted is None:
            return json_error(404, "known_server_not_found")
        return JSONResponse({"removed": True, "domain": domain, "deleted": deleted}, headers=_NO_STORE)

    # --- Inbox journal (design Section 13) ---

    async def journal_get(request: Request) -> Response:
        if not is_admin(request_principal(request)):
            return not_found()
        params = request.query_params
        status = params.get("status")
        if status is not None and status not in journal.STATES:
            return json_error(400, "invalid_status", states=list(journal.STATES))
        limit = JOURNAL_PAGE_DEFAULT
        if "limit" in params:
            parsed = _positive_int(params.get("limit"))
            if parsed is None or parsed > JOURNAL_PAGE_MAX:
                return json_error(400, "invalid_limit")
            limit = parsed
        max_id: int | None = None
        if "max_id" in params:
            max_id = _positive_int(params.get("max_id"))
            if max_id is None:
                return json_error(400, "invalid_max_id")

        def _fetch() -> dict[str, Any]:
            conn = open_db(ctx)
            try:
                entries = journal.list_entries(conn, status=status, limit=limit, max_id=max_id)
            finally:
                conn.close()
            return {
                "entries": [journal.entry_view(entry) for entry in entries],
                # The cursor of the next page, or null when this page is not full.
                "next_max_id": entries[-1].id if len(entries) == limit else None,
            }

        return JSONResponse(await asyncio.to_thread(_fetch), headers=_NO_STORE)

    async def journal_entry_get(request: Request) -> Response:
        if not is_admin(request_principal(request)):
            return not_found()
        entry_id = _positive_int(request.path_params["entry_id"])
        if entry_id is None:
            return json_error(404, "journal_entry_not_found")

        def _fetch() -> dict[str, Any] | None:
            conn = open_db(ctx)
            try:
                entry = journal.get_entry(conn, entry_id)
            finally:
                conn.close()
            return None if entry is None else journal.entry_view(entry, with_payload=True)

        view = await asyncio.to_thread(_fetch)
        if view is None:
            return json_error(404, "journal_entry_not_found")
        return JSONResponse({"entry": view}, headers=_NO_STORE)

    async def journal_replay_post(request: Request) -> Response:
        principal = request_principal(request)
        denied = _admin_change_denied(request, principal)
        if denied is not None:
            return denied
        assert principal.account_id is not None
        admin_id = principal.account_id
        body = await read_json_body(request)
        # Sealed body: an empty object.
        if not isinstance(body, dict) or body:
            return json_error(400, "invalid_request", fields=[])
        entry_id = _positive_int(request.path_params["entry_id"])
        if entry_id is None:
            return json_error(404, "journal_entry_not_found")

        def _execute() -> tuple[int, dict[str, Any]]:
            conn = open_db(ctx)
            try:
                try:
                    entry = journal.replay(conn, entry_id, origin=ctx.config.public_origin, now=time.time())
                except journal.JournalError as exc:
                    if exc.code == "not_found":
                        return 404, {"error": "journal_entry_not_found"}
                    return 409, {"error": "journal_entry_not_replayable", "states": list(journal.REPLAY_STATES)}
            finally:
                conn.close()
            if entry.status == "processed":
                # A revision of a copy (unit KF3) is a merge job that waits after the journal committed, as after a delivery of the sidecar.
                from ...ingest.service import run_waiting_merge_jobs

                run_waiting_merge_jobs(ctx.settings)
            auth_conn = open_auth_db(ctx)
            try:
                auth_db.record_admin_audit(auth_conn, actor_account_id=admin_id, action=AUDIT_JOURNAL_REPLAY, detail={"id": entry.id, "status": entry.status, "error_code": entry.error_code})
            finally:
                auth_conn.close()
            return 200, {"entry": journal.entry_view(entry)}

        status_code, data = await asyncio.to_thread(_execute)
        return JSONResponse(data, status_code=status_code, headers=_NO_STORE)

    return [
        Route(ADMIN_KNOWN_SERVERS_API_PATH, known_servers_get, methods=["GET"]),
        Route(ADMIN_KNOWN_SERVERS_API_PATH, known_servers_post, methods=["POST"]),
        Route(ADMIN_KNOWN_SERVERS_API_PATH, known_servers_delete, methods=["DELETE"]),
        Route(ADMIN_JOURNAL_API_PATH, journal_get, methods=["GET"]),
        Route(ADMIN_JOURNAL_API_PATH + "/{entry_id}", journal_entry_get, methods=["GET"]),
        Route(ADMIN_JOURNAL_API_PATH + "/{entry_id}/replay", journal_replay_post, methods=["POST"]),
    ]
