"""Object storage settings of the instance: ``/api/admin/storage``, ``/api/admin/storage/test``, and the body of the console section ``/admin/storage`` (FEDERATION_REMOTE_READING_DESIGN.md Sections 9.1.1 and 9.2, unit FP4-1).

- ``GET /api/admin/storage``: the settings without the secret key, the state of the last test, and the result of the last migration run.
- ``PUT /api/admin/storage``: a partial update. Refused with 409 ``env_locked`` when the environment variables ``CLAIRE_MEDIA_S3_*`` set the store. A change takes effect at the next start of the ``api`` process.
- ``POST /api/admin/storage/test``: write, read with a byte range, head, and delete one probe object in the bucket of the environment or of the stored fields.

All routes are for the admin. A change and the test need the same origin and a recent authentication. ``api.security.ROUTE_POLICY`` has the access rules.
"""

from __future__ import annotations

import asyncio
import time
from html import escape
from typing import Any

from starlette.requests import Request
from starlette.responses import JSONResponse, Response
from starlette.routing import Route

from ...i18n import t
from ...store import auth_db
from ...store import media_storage
from ...store.media import MediaStoreError
from ...store.secrets import SecretError
from ..principal import request_principal
from ..security import read_json_body
from .admin import is_admin
from .common import AccountsContext, json_error, not_found, open_auth_db, open_db, require_recent_auth, same_origin

ADMIN_STORAGE_API_PATH = "/api/admin/storage"
ADMIN_STORAGE_TEST_API_PATH = "/api/admin/storage/test"
AUDIT_UPDATE = "storage.update"
AUDIT_TEST = "storage.test"

_NO_STORE = {"Cache-Control": "no-store"}


def _allow_private_http(ctx: AccountsContext) -> bool:
    return bool(getattr(ctx.settings, "allow_private_networks", False))


def _view(ctx: AccountsContext) -> dict[str, Any]:
    conn = open_db(ctx)
    try:
        return media_storage.settings_view(conn)
    finally:
        conn.close()


def _format_ts(value: Any) -> str:
    try:
        return time.strftime("%Y-%m-%d %H:%M:%S UTC", time.gmtime(float(value)))
    except (TypeError, ValueError):
        return "-"


def _field(name: str, label_key: str, value: str, *, input_type: str = "text", placeholder: str = "", disabled: bool = False, hint_key: str | None = None) -> str:
    hint = f'<small style="font-weight:400;color:var(--muted)">{escape(t(hint_key))}</small>' if hint_key else ""
    return (
        f'<label style="display:flex;flex-direction:column;gap:4px;font-size:12px;font-weight:600">{escape(t(label_key))}'
        f'<input type="{input_type}" name="{name}" class="pref-input" value="{escape(value)}" placeholder="{escape(placeholder)}" data-storage-initial="{escape(value)}" autocomplete="off" spellcheck="false"{" disabled" if disabled else ""}/>'
        f"{hint}</label>"
    )


def _test_state_html(last_test: dict[str, Any] | None) -> str:
    if not last_test:
        return f'<span data-storage-test-state="none">{escape(t("console.storage.test.none"))}</span>'
    when = _format_ts(last_test.get("tested_at"))
    if last_test.get("ok"):
        return f'<span class="badge badge-enabled" data-storage-test-state="ok">{escape(t("console.storage.test.ok"))}</span> {escape(when)}'
    text = t("console.storage.test.failed", operation=str(last_test.get("failed_operation") or "-"), detail=str(last_test.get("detail") or ""))
    return f'<span class="badge badge-disabled" data-storage-test-state="failed">{escape(text)}</span> {escape(when)}'


def _migration_html(last: dict[str, Any] | None) -> str:
    if not last:
        return f'<p class="hint" data-storage-migration="none">{escape(t("console.storage.migration.none"))}</p>'
    rows = (
        ("console.storage.migration.finished_at", _format_ts(last.get("finished_at"))),
        ("console.storage.migration.total", last.get("total", 0)),
        ("console.storage.migration.uploaded", last.get("uploaded", 0)),
        ("console.storage.migration.already_stored", last.get("already_stored", 0)),
        ("console.storage.migration.errors", last.get("errors", 0)),
        ("console.storage.migration.deleted_sources", last.get("deleted_sources", 0)),
    )
    return '<dl data-storage-migration="present">' + "".join(f"<dt>{escape(t(key))}</dt><dd>{escape(str(value))}</dd>" for key, value in rows) + "</dl>"


def admin_storage_body(ctx: AccountsContext) -> tuple[str, dict[str, Any]]:
    """Return the body and the bootstrap data of ``/admin/storage``. ``console_admin.js`` sends the changes."""
    view = _view(ctx)
    locked = bool(view["env_locked"])
    mode_options = "".join(f'<option value="{mode}"{" selected" if view["mode"] == mode else ""}>{escape(t(f"console.storage.mode.{mode}"))}</option>' for mode in media_storage.MODES)
    secret_placeholder = t("console.storage.secret_key.set") if view["secret_key_set"] else ""
    env_html = ""
    if locked:
        env = view.get("env") or {}
        env_html = f'<p class="hint" data-storage-env-locked>{escape(t("console.storage.env_locked", endpoint=str(env.get("endpoint", "")), bucket=str(env.get("bucket", ""))))}</p>'
    disabled = " disabled" if locked else ""
    messages = " ".join(f'data-msg-{name}="{escape(t(f"console.storage.msg.{name}"))}"' for name in ("saved", "nochange", "failed", "env_locked", "testing", "test_ok", "test_failed", "not_configured", "reauth", "network"))
    body = (
        f'<section class="card" id="admin-storage-state" aria-label="{escape(t("console.storage.state.title"))}">'
        f'<h2><span data-icon="database"></span> {escape(t("console.storage.state.title"))}</h2>'
        "<dl>"
        f'<dt>{escape(t("console.storage.state.active"))}</dt><dd><span class="badge badge-enabled" data-storage-field="effective_mode">{escape(t("console.storage.mode." + str(view["effective_mode"])))}</span></dd>'
        f'<dt>{escape(t("console.storage.state.last_test"))}</dt><dd id="admin-storage-test-state">{_test_state_html(view["last_test"])}</dd>'
        "</dl>"
        f'<p class="hint">{escape(t("console.storage.state.hint"))}</p>'
        f"{env_html}"
        "</section>"
        f'<section class="card" id="admin-storage-settings" aria-label="{escape(t("console.storage.form.title"))}">'
        f'<h2><span data-icon="settings"></span> {escape(t("console.storage.form.title"))}</h2>'
        f'<p class="hint">{escape(t("console.storage.form.hint"))}</p>'
        f'<form id="admin-storage-form" {messages}>'
        '<div style="display:grid;grid-template-columns:repeat(auto-fill,minmax(260px,1fr));gap:12px 16px;margin-bottom:12px;align-items:start">'
        f'<label style="display:flex;flex-direction:column;gap:4px;font-size:12px;font-weight:600">{escape(t("console.storage.field.mode"))}<select name="mode" class="pref-input" data-storage-initial="{escape(str(view["mode"]))}"{disabled}>{mode_options}</select></label>'
        f"{_field('endpoint', 'console.storage.field.endpoint', view['endpoint'], input_type='url', placeholder=media_storage.GARAGE_ENDPOINT_PLACEHOLDER, disabled=locked, hint_key='console.storage.field.endpoint_hint')}"
        f"{_field('bucket', 'console.storage.field.bucket', view['bucket'], disabled=locked, hint_key='console.storage.field.bucket_hint')}"
        f"{_field('region', 'console.storage.field.region', view['region'], placeholder=media_storage.GARAGE_REGION, disabled=locked, hint_key='console.storage.field.region_hint')}"
        f"{_field('access_key', 'console.storage.field.access_key', view['access_key'], disabled=locked)}"
        f"{_field('secret_key', 'console.storage.field.secret_key', '', input_type='password', placeholder=secret_placeholder, disabled=locked, hint_key='console.storage.field.secret_key_hint')}"
        f"{_field('public_url_base', 'console.storage.field.public_url_base', view['public_url_base'], input_type='url', disabled=locked, hint_key='console.storage.field.public_url_base_hint')}"
        "</div>"
        '<div style="display:flex;gap:10px;flex-wrap:wrap">'
        f'<button type="submit" class="pref-btn pref-btn-accent"{disabled}>{escape(t("common.action.save"))}</button>'
        f'<button type="button" class="pref-btn" id="admin-storage-test">{escape(t("console.storage.test.run"))}</button>'
        "</div>"
        "</form>"
        '<p class="result" id="admin-storage-result" role="status" aria-live="polite"></p>'
        "</section>"
        f'<section class="card" id="admin-storage-migration" aria-label="{escape(t("console.storage.migration.title"))}">'
        f'<h2><span data-icon="upload"></span> {escape(t("console.storage.migration.title"))}</h2>'
        f'<p class="hint">{escape(t("console.storage.migration.hint"))}</p>'
        f"{_migration_html(view['last_migration'])}"
        "</section>"
    )
    return body, {"env_locked": locked, "effective_mode": view["effective_mode"], "mode": view["mode"]}


def routes(ctx: AccountsContext) -> list[Route]:
    def _change_denied(request: Request) -> Response | None:
        principal = request_principal(request)
        if not is_admin(principal):
            return not_found()
        if not same_origin(request):
            return json_error(403, "forbidden")
        return require_recent_auth(principal)

    def _audit(principal: Any, action: str, detail: dict[str, Any]) -> None:
        auth_conn = open_auth_db(ctx)
        try:
            auth_db.record_admin_audit(auth_conn, actor_account_id=principal.account_id, action=action, detail=detail)
        finally:
            auth_conn.close()

    async def storage_get(request: Request) -> Response:
        if not is_admin(request_principal(request)):
            return not_found()
        return JSONResponse({"storage": await asyncio.to_thread(_view, ctx)}, headers=_NO_STORE)

    async def storage_put(request: Request) -> Response:
        denied = _change_denied(request)
        if denied is not None:
            return denied
        principal = request_principal(request)
        if media_storage.env_config() is not None:
            return json_error(409, "env_locked")
        body = await read_json_body(request)
        try:
            update = media_storage.validate_update(body, allow_private_http=_allow_private_http(ctx))
        except media_storage.StorageSettingsError as exc:
            return json_error(400, "invalid_request", field=exc.field, detail=str(exc))

        def _execute() -> tuple[int, dict[str, Any]]:
            conn = open_db(ctx)
            try:
                try:
                    media_storage.set_admin_settings(conn, update, secret_service=media_storage.secret_service_for(ctx.settings), updated_by=principal.account_id)
                except media_storage.StorageSettingsError as exc:
                    return 400, {"error": "invalid_request", "field": exc.field, "detail": str(exc)}
                # RS7: the audit record has the names of the changed settings, and never the secret key.
                _audit(principal, AUDIT_UPDATE, {"changed": sorted(update), **({"mode": update["mode"]} if "mode" in update else {})})
                return 200, {"storage": media_storage.settings_view(conn), "restart_required": True}
            finally:
                conn.close()

        status_code, data = await asyncio.to_thread(_execute)
        return JSONResponse(data, status_code=status_code, headers=_NO_STORE)

    async def storage_test_post(request: Request) -> Response:
        denied = _change_denied(request)
        if denied is not None:
            return denied
        principal = request_principal(request)
        try:
            config = await asyncio.to_thread(media_storage.target_config, ctx.settings)
        except (RuntimeError, SecretError, ValueError, KeyError):
            return json_error(409, "storage_settings_unreadable")
        if config is None:
            return json_error(409, "storage_not_configured")
        try:
            result = await media_storage.probe_store(media_storage.build_s3_store(config))
        except (MediaStoreError, ValueError) as exc:
            result = {"ok": False, "failed_operation": "put", "detail": str(exc)[:300], "tested_at": time.time()}
        result["source"] = config.source

        def _record() -> None:
            conn = open_db(ctx)
            try:
                media_storage.record_test_result(conn, result, updated_by=principal.account_id)
            finally:
                conn.close()
            _audit(principal, AUDIT_TEST, {"ok": bool(result["ok"]), "failed_operation": result["failed_operation"]})

        await asyncio.to_thread(_record)
        return JSONResponse({"test": result}, headers=_NO_STORE)

    return [
        Route(ADMIN_STORAGE_API_PATH, storage_get, methods=["GET"]),
        Route(ADMIN_STORAGE_API_PATH, storage_put, methods=["PUT"]),
        Route(ADMIN_STORAGE_TEST_API_PATH, storage_test_post, methods=["POST"]),
    ]
