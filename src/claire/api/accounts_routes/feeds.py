"""Feed routes of the account (FEDERATION_REMOTE_READING_DESIGN.md Section 1.3 rule 7, units FP4-F1 and FP4-F2).

For the owner of the account only, in single mode (the single account) and in network mode (each account). Other principals get 404. The same-origin rule for a cookie request is in ``AuthenticationMiddleware``, as for the other account routes.

| Path | Method | Function |
|------|--------|----------|
| ``/api/feeds`` | GET | List the feeds with their folders and their item counts |
| ``/api/feeds`` | POST | Subscribe to a feed (``url``, optional ``title`` and ``folder``) |
| ``/api/feeds`` | DELETE | Unsubscribe (``feed_id``). The items of the feed are deleted; linked documents stay |
| ``/api/feeds/poll`` | POST | Poll now: all feeds of the account (at most 50 for each call) or one feed (``feed_id``) |
| ``/api/feeds/opml`` | GET | OPML export |
| ``/api/feeds/opml`` | POST | OPML import (``opml``: the document as text) |
| ``/api/feeds/items`` | GET | One page of items (filters ``feed_id``, ``folder``, ``unread``, ``starred``; cursor ``before_id``) |
| ``/api/feeds/items`` | PATCH | Mark items read, unread, starred, or not starred (``item_ids``), or mark all items of one feed (``feed_id``) |
| ``/api/feeds/items/{item_id}`` | GET | One item with the render URL of its body (unit FP4-F3) |
| ``/api/feeds/discover`` | POST | Find the feeds of an address (``url``): the feed itself, or the ``<link rel="alternate">`` feeds of a page (unit FP4-F3) |
| ``/render/feed/{item_id}`` | GET | The body of one item on the app origin, only for an instance without a ``usercontent`` host (unit FP4-F3) |

``POST /api/feeds/items/{item_id}/ingest`` is in ``server``, because it uses the ingest service of a theme.

Feed HTML is not trusted. The item lists have plain text only (``text``); the body renders through ``usercontent.remote_item_response`` (the sanitizer and the sandbox policy of remote posts) and never in the DOM of the app origin.

The account of each query is the account of the caller. An ID of a different account gives the same result as an unknown ID.
"""

from __future__ import annotations

import asyncio
import time
from typing import Any

from starlette.requests import Request
from starlette.responses import JSONResponse, Response
from starlette.routing import Route

from ...feeds import discover as discover_mod
from ...feeds import opml as opml_mod
from ...feeds import poller
from ...feeds.fetch import FeedFetcher
from ...store import feeds as feeds_store
from .. import usercontent
from ..principal import request_principal
from ..security import read_json_body
from .common import AccountsContext, json_error, not_found, open_client_db

FEEDS_PATH = "/api/feeds"
FEEDS_POLL_PATH = "/api/feeds/poll"
FEEDS_OPML_PATH = "/api/feeds/opml"
FEEDS_ITEMS_PATH = "/api/feeds/items"
FEEDS_DISCOVER_PATH = "/api/feeds/discover"
FEED_RENDER_PATH = "/render/feed"
ITEM_PAGE_DEFAULT = 50

_NO_STORE = {"Cache-Control": "no-store"}


def _settings(ctx: AccountsContext) -> Any:
    if ctx.settings is not None:
        return ctx.settings
    from ...config import get_settings

    return get_settings()


def _owner_account_id(request: Request) -> int | None:
    principal = request_principal(request)
    return principal.account_id if principal.scope == "owner" and principal.account_id is not None else None


def _positive_int(value: Any) -> int | None:
    """A JSON integer or a decimal string above 0, else ``None``."""
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value if value > 0 else None
    if isinstance(value, str) and value.isdigit() and len(value) <= 18:
        number = int(value)
        return number if number > 0 else None
    return None


def _flag(value: str | None) -> bool | None:
    """Query flag ``1``/``0``. Missing gives ``None``. Raises ``ValueError`` for other values."""
    if value is None:
        return None
    if value in ("1", "0"):
        return value == "1"
    raise ValueError(value)


def _feed_view(feed: dict[str, Any]) -> dict[str, Any]:
    view = {key: feed[key] for key in ("id", "url", "site_url", "title", "folder", "format", "websub_hub", "last_polled_at", "last_success_at", "next_poll_at", "last_status", "last_error", "failure_count", "created_at")}
    for key in ("items", "unread"):
        if key in feed:
            view[key] = feed[key]
    return view


def _item_view(item: dict[str, Any], *, with_content: bool, timeline: dict[str, int] | None = None) -> dict[str, Any]:
    keys = ["id", "feed_id", "feed_title", "feed_folder", "url", "title", "author", "summary", "categories", "published_at", "modified_at", "fetched_at", "unread", "starred", "linked_theme_id", "linked_doc_id"]
    if with_content:
        keys.append("content")
    view = {key: item.get(key) for key in keys}
    # Plain text for a list: the tags are removed on the server (design Section 8 rule 1). A client puts no feed HTML into its page.
    view["text"] = usercontent.remote_item_text(item.get("summary") or item.get("content") or "", 400)
    view["linked"] = None if item.get("linked_doc_id") is None else {"theme_id": item.get("linked_theme_id"), "document_id": item.get("linked_doc_id")}
    # Merge by URL (design Section 1.3 rule 7): the timeline item of the account with the address of this item.
    match = (timeline or {}).get(item.get("url") or "")
    view["timeline_item_id"] = None if match is None else str(match)
    return view


def _item_id(request: Request) -> int | None:
    raw = request.path_params["item_id"]
    return int(raw) if raw.isascii() and raw.isdigit() and raw[0] != "0" and len(raw) <= 18 else None


def routes(ctx: AccountsContext) -> list[Route]:
    def _run(func: Any) -> Any:
        """Run ``func(conn)`` with a database connection in a worker thread."""

        def _call() -> Any:
            conn = open_client_db(ctx)
            try:
                return func(conn)
            finally:
                conn.close()

        return asyncio.to_thread(_call)

    async def feeds_get(request: Request) -> Response:
        account_id = _owner_account_id(request)
        if account_id is None:
            return not_found()
        feeds = await _run(lambda conn: feeds_store.list_feeds(conn, account_id))
        folders = sorted({feed["folder"] for feed in feeds if feed["folder"]})
        return JSONResponse({"feeds": [_feed_view(f) for f in feeds], "folders": folders, "limit": feeds_store.FEED_LIMIT_PER_ACCOUNT}, headers=_NO_STORE)

    async def feeds_post(request: Request) -> Response:
        account_id = _owner_account_id(request)
        if account_id is None:
            return not_found()
        body = await read_json_body(request)
        if "url" not in body or not set(body) <= {"url", "title", "folder"}:
            return json_error(400, "invalid_request", fields=["url", "title", "folder"])
        if not isinstance(body.get("title", ""), str) or not isinstance(body.get("folder", ""), str):
            return json_error(400, "invalid_request", fields=["url", "title", "folder"])
        try:
            url = feeds_store.normalize_feed_url(body["url"])
        except ValueError:
            return json_error(400, "invalid_url")

        def _execute(conn: Any) -> tuple[dict[str, Any], bool] | None:
            try:
                feed, created = feeds_store.add_feed(conn, account_id, url, title=body.get("title", ""), folder=body.get("folder", ""), now=time.time())
            except feeds_store.FeedLimitReached:
                return None
            conn.commit()
            return feed, created

        result = await _run(_execute)
        if result is None:
            return json_error(409, "feed_limit", limit=feeds_store.FEED_LIMIT_PER_ACCOUNT)
        feed, created = result
        return JSONResponse({"feed": _feed_view(feed), "created": created}, status_code=201 if created else 200, headers=_NO_STORE)

    async def feeds_delete(request: Request) -> Response:
        account_id = _owner_account_id(request)
        if account_id is None:
            return not_found()
        body = await read_json_body(request)
        feed_id = _positive_int(body.get("feed_id"))
        if set(body) != {"feed_id"} or feed_id is None:
            return json_error(400, "invalid_request", fields=["feed_id"])

        def _execute(conn: Any) -> int | None:
            removed = feeds_store.remove_feed(conn, account_id, feed_id)
            conn.commit()
            return removed

        items = await _run(_execute)
        if items is None:
            return json_error(404, "feed_not_found")
        return JSONResponse({"removed": True, "feed_id": feed_id, "items": items}, headers=_NO_STORE)

    async def feeds_poll(request: Request) -> Response:
        account_id = _owner_account_id(request)
        if account_id is None:
            return not_found()
        body = await read_json_body(request)
        if not set(body) <= {"feed_id"}:
            return json_error(400, "invalid_request", fields=["feed_id"])
        feed_id = None
        if "feed_id" in body:
            feed_id = _positive_int(body["feed_id"])
            if feed_id is None:
                return json_error(400, "invalid_request", fields=["feed_id"])
        fetcher = FeedFetcher.from_settings(_settings(ctx))
        result = await asyncio.to_thread(poller.poll_account, ctx.client_db_file, account_id, fetcher=fetcher, feed_id=feed_id)
        if not result["found"]:
            return json_error(404, "feed_not_found")
        result.pop("found")
        return JSONResponse(result, headers=_NO_STORE)

    async def opml_get(request: Request) -> Response:
        account_id = _owner_account_id(request)
        if account_id is None:
            return not_found()
        feeds = await _run(lambda conn: feeds_store.list_feeds(conn, account_id))
        data = opml_mod.export_opml(feeds)
        return Response(data, media_type="text/x-opml; charset=utf-8", headers={**_NO_STORE, "Content-Disposition": 'attachment; filename="claire-feeds.opml"'})

    async def opml_post(request: Request) -> Response:
        account_id = _owner_account_id(request)
        if account_id is None:
            return not_found()
        body = await read_json_body(request)
        if set(body) != {"opml"} or not isinstance(body["opml"], str):
            return json_error(400, "invalid_request", fields=["opml"])

        def _execute(conn: Any) -> dict[str, Any]:
            report = opml_mod.import_opml(conn, account_id, body["opml"], now=time.time())
            conn.commit()
            return report

        try:
            report = await _run(_execute)
        except opml_mod.OpmlError as exc:
            return json_error(400, "invalid_opml", reason=exc.code)
        return JSONResponse(report, headers=_NO_STORE)

    async def items_get(request: Request) -> Response:
        account_id = _owner_account_id(request)
        if account_id is None:
            return not_found()
        query = request.query_params
        if not set(query) <= {"feed_id", "folder", "unread", "starred", "before_id", "limit", "content"}:
            return json_error(400, "invalid_request", detail="unknown query parameter")
        try:
            unread, starred, with_content = _flag(query.get("unread")), _flag(query.get("starred")), bool(_flag(query.get("content")))
        except ValueError:
            return json_error(400, "invalid_request", detail="unread, starred, and content must be 0 or 1")
        numbers: dict[str, int | None] = {}
        for key in ("feed_id", "before_id", "limit"):
            raw = query.get(key)
            numbers[key] = None if raw is None else _positive_int(raw)
            if raw is not None and numbers[key] is None:
                return json_error(400, "invalid_request", detail=f"{key} must be a positive integer")
        limit = numbers["limit"] or ITEM_PAGE_DEFAULT
        if limit > feeds_store.ITEM_PAGE_MAX:
            return json_error(400, "invalid_request", detail=f"limit must be at most {feeds_store.ITEM_PAGE_MAX}")
        folder = query.get("folder")

        def _fetch(conn: Any) -> tuple[list[dict[str, Any]], dict[str, int]]:
            rows = feeds_store.list_items(conn, account_id, limit=limit, before_id=numbers["before_id"], feed_id=numbers["feed_id"], folder=folder, unread=unread, starred=starred)
            return rows, feeds_store.timeline_matches(conn, account_id, [row["url"] for row in rows])

        items, timeline = await _run(_fetch)
        next_before = items[-1]["id"] if len(items) == limit else None
        return JSONResponse({"items": [_item_view(i, with_content=with_content, timeline=timeline) for i in items], "next_before_id": next_before}, headers=_NO_STORE)

    async def items_patch(request: Request) -> Response:
        account_id = _owner_account_id(request)
        if account_id is None:
            return not_found()
        body = await read_json_body(request)
        fields = ["item_ids", "feed_id", "unread", "starred"]
        if not set(body) <= set(fields) or ("item_ids" in body) == ("feed_id" in body):
            return json_error(400, "invalid_request", fields=fields)
        unread, starred = body.get("unread"), body.get("starred")
        if (unread is not None and not isinstance(unread, bool)) or (starred is not None and not isinstance(starred, bool)) or (unread is None and starred is None):
            return json_error(400, "invalid_request", fields=fields)
        if "feed_id" in body:
            feed_id = _positive_int(body["feed_id"])
            if feed_id is None or starred is not None:
                return json_error(400, "invalid_request", detail="feed_id takes unread only")

            def _feed(conn: Any) -> int | None:
                if feeds_store.get_feed(conn, account_id, feed_id) is None:
                    return None
                changed = feeds_store.mark_feed_items(conn, account_id, feed_id, unread=bool(unread))
                feeds_store.mark_timeline_of_feed(conn, account_id, feed_id, unread=bool(unread))
                conn.commit()
                return changed

            changed = await _run(_feed)
            if changed is None:
                return json_error(404, "feed_not_found")
            return JSONResponse({"changed": changed}, headers=_NO_STORE)
        raw_ids = body["item_ids"]
        ids = [_positive_int(v) for v in raw_ids] if isinstance(raw_ids, list) else []
        if not ids or len(ids) > feeds_store.MARK_MAX_ITEMS or any(i is None for i in ids):
            return json_error(400, "invalid_request", detail=f"item_ids must be 1 to {feeds_store.MARK_MAX_ITEMS} positive integers")

        def _items(conn: Any) -> int:
            item_ids = [i for i in ids if i is not None]
            changed = feeds_store.mark_items(conn, account_id, item_ids, unread=unread, starred=starred)
            # Merge by URL (unit FP4-F3): a timeline item with the address of a marked item gets the same state.
            feeds_store.mark_timeline_by_url(conn, account_id, feeds_store.item_urls(conn, account_id, item_ids), unread=unread, starred=starred)
            conn.commit()
            return changed

        return JSONResponse({"changed": await _run(_items)}, headers=_NO_STORE)

    # --- One item and its body (unit FP4-F3) ---

    def _secret_service() -> Any:
        return getattr(ctx.settings, "secret_service", None)

    def _role_origin(role: str) -> str | None:
        roles = ctx.config.host_roles
        return None if roles is None else roles.origin(role)

    def _render_url(account_id: int, item_id: int) -> str:
        """The address of the body of an item for a sandbox ``iframe``: the ``usercontent`` origin, or this origin when the instance has no such host (development, design Section 8 rule 5)."""
        token = usercontent.create_remote_token(account_id, item_id, secret_service=_secret_service(), kind=usercontent.FEED_TOKEN_KIND)
        return f"{_role_origin('usercontent') or ''}{FEED_RENDER_PATH}/{item_id}?t={token}"

    async def item_get(request: Request) -> Response:
        account_id = _owner_account_id(request)
        if account_id is None:
            return not_found()
        item_id = _item_id(request)
        if item_id is None:
            return json_error(404, "item_not_found")

        def _fetch(conn: Any) -> tuple[dict[str, Any], dict[str, int]] | None:
            item = feeds_store.get_item(conn, account_id, item_id)
            return None if item is None else (item, feeds_store.timeline_matches(conn, account_id, [item["url"]]))

        found = await _run(_fetch)
        if found is None:
            return json_error(404, "item_not_found")
        view = _item_view(found[0], with_content=False, timeline=found[1])
        view["has_body"] = bool(found[0].get("content") or found[0].get("summary"))
        view["render_url"] = _render_url(account_id, item_id)
        return JSONResponse({"item": view}, headers=_NO_STORE)

    async def feed_render_fallback(request: Request) -> Response:
        """The body of a feed item on the app origin (design Section 8 rule 5). With a ``usercontent`` host this path does not exist here: the body renders on that origin only."""
        account_id = _owner_account_id(request)
        item_id = _item_id(request)
        if account_id is None or item_id is None or _role_origin("usercontent") is not None:
            return not_found()
        item = await asyncio.to_thread(usercontent.load_feed_item, ctx.db_file, item_id)
        token = request.query_params.get("t", "")
        if item is None or item["account_id"] != account_id or not usercontent.verify_remote_token(token, account_id, item_id, secret_service=_secret_service(), kind=usercontent.FEED_TOKEN_KIND):
            return not_found()
        media_origin = _role_origin("media")
        media_url = await asyncio.to_thread(lambda: usercontent.remote_media_urls(ctx.db_file, account_id=account_id, content_html=item["content_html"], attachments=[], media_base=media_origin or "", secret_service=_secret_service()))
        return usercontent.remote_item_response(content_html=item["content_html"], attachments=[], language=None, frame_ancestors="'self'", extra_headers={"x-frame-options": "SAMEORIGIN"}, media_url=media_url, media_source=media_origin or "'self'")

    async def discover_post(request: Request) -> Response:
        account_id = _owner_account_id(request)
        if account_id is None:
            return not_found()
        body = await read_json_body(request)
        if set(body) != {"url"}:
            return json_error(400, "invalid_request", fields=["url"])
        try:
            url = feeds_store.normalize_feed_url(body["url"])
        except ValueError:
            return json_error(400, "invalid_url")
        fetcher = FeedFetcher.from_settings(_settings(ctx))
        result = await asyncio.to_thread(discover_mod.discover, fetcher, url, now=time.time())
        known = {feed["url"] for feed in await _run(lambda conn: feeds_store.list_feeds(conn, account_id))}
        for feed in result["feeds"]:
            try:
                feed["subscribed"] = feeds_store.normalize_feed_url(feed["url"]) in known
            except ValueError:
                feed["subscribed"] = False
        return JSONResponse({"url": url, **result}, headers=_NO_STORE)

    return [
        Route(FEEDS_PATH, feeds_get, methods=["GET", "HEAD"]),
        Route(FEEDS_PATH, feeds_post, methods=["POST"]),
        Route(FEEDS_PATH, feeds_delete, methods=["DELETE"]),
        Route(FEEDS_POLL_PATH, feeds_poll, methods=["POST"]),
        Route(FEEDS_OPML_PATH, opml_get, methods=["GET", "HEAD"]),
        Route(FEEDS_OPML_PATH, opml_post, methods=["POST"]),
        Route(FEEDS_ITEMS_PATH, items_get, methods=["GET", "HEAD"]),
        Route(FEEDS_ITEMS_PATH, items_patch, methods=["PATCH"]),
        Route(FEEDS_ITEMS_PATH + "/{item_id}", item_get, methods=["GET", "HEAD"]),
        Route(FEEDS_DISCOVER_PATH, discover_post, methods=["POST"]),
        Route(FEED_RENDER_PATH + "/{item_id}", feed_render_fallback, methods=["GET", "HEAD"]),
    ]
