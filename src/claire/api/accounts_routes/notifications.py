"""Notifications and Web Push of the account (PWA_NOTIFICATION_DESIGN.md PN4 and PN5).

| Path | Methods | Function |
| :--- | :--- | :--- |
| ``/api/me/notifications`` | GET | The last notifications with rendered text, and the count that is not read |
| ``/api/me/notifications/read`` | POST | Mark notifications as read |
| ``/api/me/push`` | GET | The public key, the readiness, the devices, the kinds with their switches |
| ``/api/me/push/subscriptions`` | POST, DELETE | Add or renew the subscription of this browser; remove a subscription |
| ``/api/me/push/kinds/{kind}`` | PUT | Turn Web Push on or off for one kind |
| ``/api/me/push/test`` | POST | Make a test notification |

``settings_notifications_body`` makes the body of ``/settings/notifications``; ``src/bible/static/js/console_notifications.js`` controls it.
"""

from __future__ import annotations

import asyncio
import hashlib
import ipaddress
from typing import Any
from urllib.parse import urlsplit

from starlette.requests import Request
from starlette.responses import JSONResponse, Response
from starlette.routing import Route

from ... import notifications
from ...i18n import LocaleCode, t
from ...i18n.context import get_locale
from ...notifications import sender, webpush
from ...store import accounts as accounts_store
from ...store import notifications as store
from ...store.secrets import get_secret_service
from ..principal import Principal, request_principal
from ..security import read_json_body
from .common import AccountsContext, escape, format_ts, json_error, not_found, open_auth_db, open_client_db
from .console_ui import console_actions, console_card, console_notice, console_pill, console_result, console_table, console_toggle_row

ME_NOTIFICATIONS_PATH = "/api/me/notifications"
ME_NOTIFICATIONS_READ_PATH = "/api/me/notifications/read"
ME_PUSH_PATH = "/api/me/push"
ME_PUSH_SUBSCRIPTIONS_PATH = "/api/me/push/subscriptions"
ME_PUSH_KIND_PATH = "/api/me/push/kinds/{kind}"
ME_PUSH_TEST_PATH = "/api/me/push/test"
SETTINGS_PATH = "/settings/notifications"

NO_STORE = {"Cache-Control": "no-store"}
LIST_LIMIT_DEFAULT = 20
LIST_LIMIT_MAX = 50
_ENDPOINT_MAX = 2048


def _owner(request: Request) -> tuple[Principal, Response | None]:
    principal = request_principal(request)
    if principal.account_id is None or principal.scope != "owner":
        return principal, not_found()
    return principal, None


def _endpoint_ref(endpoint: str) -> str:
    """A short digest of an endpoint. The browser compares it with the digest of its own endpoint to find its row; an API answer never has the endpoint itself."""
    return hashlib.sha256(endpoint.encode("utf-8")).hexdigest()[:16]


def _valid_endpoint(value: Any) -> str | None:
    """An endpoint is an ``https`` URL with a host name and without credentials (PN3 rule 6). The address check is at the send."""
    if not isinstance(value, str) or not value or len(value) > _ENDPOINT_MAX or any(ch.isspace() for ch in value):
        return None
    try:
        parts = urlsplit(value)
        host = parts.hostname
    except ValueError:
        return None
    if parts.scheme != "https" or not host or parts.username is not None or parts.password is not None:
        return None
    try:
        ipaddress.ip_address(host)
    except ValueError:
        return value
    return None


def device_label(user_agent: str) -> str:
    """A short name of the browser and the system from the ``User-Agent`` text, for the device list. Text that is not known gives an empty label."""
    ua = user_agent or ""
    browser = next((name for mark, name in (("Edg/", "Edge"), ("OPR/", "Opera"), ("SamsungBrowser/", "Samsung Internet"), ("Firefox/", "Firefox"), ("FxiOS/", "Firefox"), ("CriOS/", "Chrome"), ("Chrome/", "Chrome"), ("Safari/", "Safari")) if mark in ua), "")
    system = next((name for mark, name in (("iPhone", "iPhone"), ("iPad", "iPad"), ("Android", "Android"), ("Windows", "Windows"), ("Mac OS X", "macOS"), ("CrOS", "ChromeOS"), ("Linux", "Linux")) if mark in ua), "")
    return " · ".join(part for part in (browser, system) if part)


def _item(notification: store.Notification, locale: LocaleCode) -> dict[str, Any]:
    title, body = notifications.render(notification, locale)
    spec = notifications.KINDS.get(notification.kind)
    return {
        "id": notification.id,
        "kind": notification.kind,
        "channel": spec.channel if spec else None,
        "class": spec.klass if spec else None,
        "title": title,
        "body": body,
        "url": notification.target_path,
        "count": notification.count,
        "created_at": notification.created_at,
        "updated_at": notification.updated_at,
        "read": notification.read_at is not None,
    }


def list_state(ctx: AccountsContext, account_id: int, *, locale: LocaleCode, limit: int, before_id: int | None) -> dict[str, Any]:
    conn = open_client_db(ctx)
    try:
        rows = store.list_notifications(conn, account_id, limit=limit, before_id=before_id)
        return {"items": [_item(n, locale) for n in rows], "unread": store.unread_count(conn, account_id)}
    finally:
        conn.close()


def _mark_read(ctx: AccountsContext, account_id: int, ids: list[int] | None) -> int:
    conn = open_client_db(ctx)
    try:
        store.mark_read(conn, account_id, ids=ids)
        conn.commit()
        return store.unread_count(conn, account_id)
    finally:
        conn.close()


def _public_key(ctx: AccountsContext) -> str:
    auth = open_auth_db(ctx)
    try:
        return webpush.vapid_public_key(store.ensure_vapid_private_key(auth, get_secret_service()))
    finally:
        auth.close()


def push_state(ctx: AccountsContext, account_id: int, *, session_ref: str | None) -> dict[str, Any]:
    """The Web Push state of the account. ``ready`` is false when the server cannot send (no canonical origin, PN3 rule 8)."""
    ready = sender.vapid_subject(ctx.settings) is not None
    conn = open_client_db(ctx)
    try:
        subs = store.list_subscriptions(conn, account_id)
        off = store.push_off_kinds(conn, account_id)
    finally:
        conn.close()
    return {
        "ready": ready,
        "reason": None if ready else "no_public_origin",
        "public_key": _public_key(ctx),
        "max_subscriptions": store.MAX_SUBSCRIPTIONS,
        "subscriptions": [
            {"id": s.id, "ref": _endpoint_ref(s.endpoint), "label": s.label, "created_at": s.created_at, "last_success_at": s.last_success_at, "failure_count": s.failure_count, "this_session": session_ref is not None and s.session_ref == session_ref}
            for s in subs
        ],
        "kinds": [{"kind": k.name, "channel": k.channel, "class": k.klass, "push": k.name not in off} for k in notifications.KINDS.values() if k.channel in notifications.CHANNELS],
    }


def _subscribe(ctx: AccountsContext, account_id: int, *, endpoint: str, p256dh: str, auth: str, label: str, session_ref: str) -> dict[str, Any] | None:
    conn = open_client_db(ctx)
    try:
        try:
            sub = store.upsert_subscription(conn, account_id=account_id, endpoint=endpoint, p256dh=p256dh, auth=auth, label=label, session_ref=session_ref)
        except store.SubscriptionLimit:
            return None
        conn.commit()
        return {"id": sub.id, "ref": _endpoint_ref(sub.endpoint), "label": sub.label, "created_at": sub.created_at}
    finally:
        conn.close()


def _unsubscribe(ctx: AccountsContext, account_id: int, *, endpoint: str | None, subscription_id: int | None) -> bool:
    conn = open_client_db(ctx)
    try:
        if endpoint is not None:
            deleted = store.delete_subscription_by_endpoint(conn, account_id, endpoint)
        else:
            assert subscription_id is not None
            deleted = store.delete_subscription(conn, account_id, subscription_id)
        conn.commit()
        return deleted
    finally:
        conn.close()


def _set_kind(ctx: AccountsContext, account_id: int, kind: str, push: bool) -> None:
    conn = open_client_db(ctx)
    try:
        store.set_push_off(conn, account_id, kind, not push)
        conn.commit()
    finally:
        conn.close()


def routes(ctx: AccountsContext) -> list[Route]:
    async def notifications_get(request: Request) -> Response:
        principal, denied = _owner(request)
        if denied is not None:
            return denied
        assert principal.account_id is not None
        try:
            limit = int(request.query_params.get("limit", LIST_LIMIT_DEFAULT))
            before_raw = request.query_params.get("before")
            before_id = int(before_raw) if before_raw is not None else None
        except ValueError:
            return json_error(400, "invalid_request", fields=["limit", "before"])
        if not 1 <= limit <= LIST_LIMIT_MAX or (before_id is not None and before_id < 1):
            return json_error(400, "invalid_request", fields=["limit", "before"])
        state = await asyncio.to_thread(list_state, ctx, principal.account_id, locale=get_locale(), limit=limit, before_id=before_id)
        return JSONResponse(state, headers=NO_STORE)

    async def notifications_read(request: Request) -> Response:
        principal, denied = _owner(request)
        if denied is not None:
            return denied
        assert principal.account_id is not None
        body = await read_json_body(request)
        ids: list[int] | None
        if body.get("all") is True and set(body) == {"all"}:
            ids = None
        else:
            raw = body.get("ids")
            if set(body) != {"ids"} or not isinstance(raw, list) or not raw or len(raw) > LIST_LIMIT_MAX or any(isinstance(i, bool) or not isinstance(i, int) or i < 1 for i in raw):
                return json_error(400, "invalid_request", fields=["ids", "all"])
            ids = [int(i) for i in raw]
        unread = await asyncio.to_thread(_mark_read, ctx, principal.account_id, ids)
        return JSONResponse({"ok": True, "unread": unread}, headers=NO_STORE)

    async def push_get(request: Request) -> Response:
        principal, denied = _owner(request)
        if denied is not None:
            return denied
        assert principal.account_id is not None
        session_ref = principal.credential_id if principal.channel == "cookie" else None
        state = await asyncio.to_thread(push_state, ctx, principal.account_id, session_ref=session_ref)
        return JSONResponse(state, headers=NO_STORE)

    async def subscription_post(request: Request) -> Response:
        principal, denied = _owner(request)
        if denied is not None:
            return denied
        assert principal.account_id is not None
        # A subscription is bound to the browser session that made it (PN3 rule 7); another credential has no such session.
        if principal.channel != "cookie" or not principal.credential_id:
            return json_error(403, "session_required")
        body = await read_json_body(request)
        keys = body.get("keys")
        endpoint = _valid_endpoint(body.get("endpoint"))
        if endpoint is None or not isinstance(keys, dict) or not isinstance(keys.get("p256dh"), str) or not isinstance(keys.get("auth"), str):
            return json_error(400, "invalid_subscription")
        try:
            webpush.check_subscription_keys(keys["p256dh"], keys["auth"])
        except webpush.WebPushError:
            return json_error(400, "invalid_subscription")
        saved = await asyncio.to_thread(_subscribe, ctx, principal.account_id, endpoint=endpoint, p256dh=keys["p256dh"], auth=keys["auth"], label=device_label(request.headers.get("user-agent", "")), session_ref=principal.credential_id)
        if saved is None:
            return json_error(409, "subscription_limit", max=store.MAX_SUBSCRIPTIONS)
        return JSONResponse({"subscription": saved}, status_code=201, headers=NO_STORE)

    async def subscription_delete(request: Request) -> Response:
        principal, denied = _owner(request)
        if denied is not None:
            return denied
        assert principal.account_id is not None
        body = await read_json_body(request)
        endpoint = body.get("endpoint")
        sub_id = body.get("id")
        if set(body) == {"endpoint"} and isinstance(endpoint, str) and endpoint:
            deleted = await asyncio.to_thread(_unsubscribe, ctx, principal.account_id, endpoint=endpoint, subscription_id=None)
        elif set(body) == {"id"} and isinstance(sub_id, int) and not isinstance(sub_id, bool):
            deleted = await asyncio.to_thread(_unsubscribe, ctx, principal.account_id, endpoint=None, subscription_id=sub_id)
        else:
            return json_error(400, "invalid_request", fields=["endpoint", "id"])
        return JSONResponse({"ok": True, "deleted": deleted}, headers=NO_STORE)

    async def kind_put(request: Request) -> Response:
        principal, denied = _owner(request)
        if denied is not None:
            return denied
        assert principal.account_id is not None
        kind = request.path_params["kind"]
        spec = notifications.KINDS.get(kind)
        if spec is None or spec.channel not in notifications.CHANNELS:
            return json_error(404, "kind_not_found")
        body = await read_json_body(request)
        if set(body) != {"push"} or not isinstance(body["push"], bool):
            return json_error(400, "invalid_request", fields=["push"])
        await asyncio.to_thread(_set_kind, ctx, principal.account_id, kind, body["push"])
        return JSONResponse({"kind": kind, "push": body["push"]}, headers=NO_STORE)

    async def test_post(request: Request) -> Response:
        principal, denied = _owner(request)
        if denied is not None:
            return denied
        assert principal.account_id is not None
        made = await asyncio.to_thread(notifications.emit, ctx.db_file, kind="test", account_ids=[principal.account_id], target=SETTINGS_PATH)
        if not made:
            return json_error(500, "notification_failed")
        return JSONResponse({"ok": True, "id": made[0]}, status_code=201, headers=NO_STORE)

    return [
        Route(ME_NOTIFICATIONS_PATH, notifications_get, methods=["GET", "HEAD"]),
        Route(ME_NOTIFICATIONS_READ_PATH, notifications_read, methods=["POST"]),
        Route(ME_PUSH_PATH, push_get, methods=["GET", "HEAD"]),
        Route(ME_PUSH_SUBSCRIPTIONS_PATH, subscription_post, methods=["POST"]),
        Route(ME_PUSH_SUBSCRIPTIONS_PATH, subscription_delete, methods=["DELETE"]),
        Route(ME_PUSH_KIND_PATH, kind_put, methods=["PUT"]),
        Route(ME_PUSH_TEST_PATH, test_post, methods=["POST"]),
    ]


# --- /settings/notifications -------------------------------------------------------------


def settings_notifications_body(ctx: AccountsContext, *, account: accounts_store.Account, session_ref: str | None) -> str:
    state = push_state(ctx, account.id, session_ref=session_ref)
    not_ready = "" if state["ready"] else console_notice(escape(t("console.notify.not_ready")), tone="warn", attrs={"data-notify-not-ready": True})
    device_card = console_card(
        escape(t("console.notify.device_title")),
        not_ready
        + f'<p class="hint" data-notify-device-state>{escape(t("console.notify.state.checking"))}</p>'
        + console_actions(
            f'<button type="button" class="pref-btn pref-btn-accent" data-notify-action="enable" hidden>{escape(t("console.notify.enable"))}</button>'
            f'<button type="button" class="pref-btn" data-notify-action="disable" hidden>{escape(t("console.notify.disable"))}</button>'
            f'<button type="button" class="pref-btn" data-notify-action="test">{escape(t("console.notify.test"))}</button>'
        )
        + console_result(attrs={"data-notify-result": True}),
        icon="bell",
        hint_html=escape(t("console.notify.device_hint")),
        extra_class="card",
        aria_label=t("console.notify.device_title"),
        attrs={"data-notify-device": True, "data-notify-public-key": state["public_key"], "data-notify-ready": "1" if state["ready"] else "0"},
    )
    rows = "".join(_subscription_row(sub) for sub in state["subscriptions"])
    # The script makes a row from this template after a change of the list, thus the text of a row has one source.
    row_template = f'<template id="notify-device-row">{_subscription_row({"id": "", "ref": "", "label": "", "created_at": None, "last_success_at": None})}</template>'
    devices_card = console_card(
        escape(t("console.notify.devices_title")),
        console_table([t("console.notify.col.device"), t("console.notify.col.added"), t("console.notify.col.last_sent"), ""], rows, tbody_id="notify-devices-body", aria_label=t("console.notify.devices_title"))
        + row_template
        + f'<p class="hint" data-notify-devices-empty{"" if not state["subscriptions"] else " hidden"}>{escape(t("console.notify.devices_empty"))}</p>',
        icon="screen",
        hint_html=escape(t("console.notify.devices_hint", max=state["max_subscriptions"])),
        extra_class="card",
        aria_label=t("console.notify.devices_title"),
    )
    kind_cards = []
    for channel in notifications.CHANNELS:
        toggles = "".join(
            console_toggle_row(
                t(f"notify.{entry['kind']}.title"),
                f"notify-kind-{entry['kind']}",
                desc_html=escape(t(f"console.notify.kind.{entry['kind']}")) + " " + console_pill(t("console.notify.class." + entry["class"])),
                checked=bool(entry["push"]),
                attrs={"data-notify-kind": entry["kind"]},
            )
            for entry in state["kinds"]
            if entry["channel"] == channel
        )
        kind_cards.append(
            console_card(
                escape(t(f"console.notify.channel.{channel}")),
                toggles + console_result(attrs={"data-notify-kind-result": True}),
                icon={"platform": "users", "agent": "puzzle", "activitypub": "globe"}[channel],
                hint_html=escape(t(f"console.notify.channel.{channel}_hint")),
                extra_class="card",
                aria_label=t(f"console.notify.channel.{channel}"),
                attrs={"data-notify-channel": channel},
            )
        )
    return device_card + devices_card + "".join(kind_cards)


def _subscription_row(sub: dict[str, Any]) -> str:
    label = sub["label"] or t("console.notify.device_unknown")
    return (
        f'<tr data-notify-sub="{escape(sub["id"])}" data-notify-ref="{escape(sub["ref"])}" data-notify-unknown="{escape(t("console.notify.device_unknown"))}">'
        f'<td><span data-notify-sub-label>{escape(label)}</span> {console_pill(t("console.notify.this_device"), attrs={"data-notify-this": True, "hidden": True})}</td>'
        f"<td data-notify-sub-added>{escape(format_ts(sub['created_at']))}</td>"
        f"<td data-notify-sub-sent>{escape(format_ts(sub['last_success_at']))}</td>"
        f'<td><button type="button" class="pref-btn pref-btn-danger" data-notify-remove="{escape(sub["id"])}">{escape(t("console.notify.remove"))}</button></td>'
        "</tr>"
    )
