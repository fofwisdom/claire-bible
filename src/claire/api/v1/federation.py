"""Account federation and feed subscription endpoints for `/api/v1/*`."""

from __future__ import annotations

import asyncio
import time
from typing import Any

from starlette.requests import Request
from starlette.responses import JSONResponse

from ...core import ids
from ...ingest.extraction_mode import parse_extraction_mode
from ...ingest.report_json import report_to_dict
from ...store import (
    accounts as accounts_store,
    client_db,
    db as dbm,
    federation as federation_store,
    feeds as feeds_store,
)
from .. import usercontent
from ..accounts_routes.console_federation_panels import actor_state
from ..security import WebRuntimeConfig
from ._context import (
    V1Context,
    enforce_v1_auth_policy,
    kb_number_str,
    public_origin,
    read_optional_v1_json_dict,
    read_v1_json,
    read_v1_json_dict,
    refuse_server_api_for_agent_client,
    resolve_personal_kb_context,
)
from .errors import V1Error, not_found_error, v1_endpoint
from .pagination import decode_number_cursor, encode_number_cursor, paginated_payload, parse_limit
from .schemas import (
    CreateFeedRequest,
    DeleteFeedRequest,
    DeleteFeedResponse,
    FederationFollowerCount,
    FederationObjectSummary,
    FederationPublishRequest,
    FederationPublishResponse,
    FederationRetractRequest,
    FederationRetractResponse,
    FederationStatusResponse,
    FederationTaskSummary,
    FeedDetailResponse,
    FeedItemDocumentRef,
    FeedItemIngestRequest,
    FeedItemIngestResponse,
    FeedItemListResponse,
    FeedItemSummary,
    FeedListResponse,
    FeedSummary,
    IngestResponse,
    UpdateFeedItemsRequest,
    UpdateFeedItemsResponse,
)


def _positive_int(raw: Any) -> int | None:
    if isinstance(raw, bool):
        return None
    if isinstance(raw, int):
        return raw if raw > 0 else None
    if isinstance(raw, str) and raw.isascii() and raw.isdigit() and raw[0] != "0" and len(raw) <= 18:
        val = int(raw)
        return val if val > 0 else None
    return None


def _parse_flag(raw: str | None, name: str) -> bool | None:
    if raw is None or raw == "":
        return None
    low = raw.strip().lower()
    if low in ("1", "true"):
        return True
    if low in ("0", "false"):
        return False
    raise V1Error(400, f"invalid_{name}", f"{name} must be true/false or 1/0")


def _feed_summary(feed: dict[str, Any]) -> FeedSummary:
    return FeedSummary(
        id=int(feed["id"]),
        url=str(feed["url"]),
        site_url=feed.get("site_url"),
        title=str(feed.get("title") or ""),
        folder=str(feed.get("folder") or ""),
        format=feed.get("format"),
        websub_hub=feed.get("websub_hub"),
        last_polled_at=float(feed["last_polled_at"]) if feed.get("last_polled_at") is not None else None,
        last_success_at=float(feed["last_success_at"]) if feed.get("last_success_at") is not None else None,
        next_poll_at=float(feed["next_poll_at"]) if feed.get("next_poll_at") is not None else None,
        last_status=feed.get("last_status"),
        last_error=feed.get("last_error"),
        failure_count=int(feed.get("failure_count") or 0),
        created_at=float(feed["created_at"]),
        items=int(feed["items"]) if feed.get("items") is not None else None,
        unread=int(feed["unread"]) if feed.get("unread") is not None else None,
    )


def _feed_item_summary(
    ctx: V1Context,
    item: dict[str, Any],
    *,
    with_content: bool,
    timeline: dict[str, int],
) -> FeedItemSummary:
    linked_doc_id = item.get("linked_doc_id")
    linked_kb_id = item.get("linked_kb_id")
    linked: FeedItemDocumentRef | None = None
    if linked_doc_id is not None:
        kb_num: str | None = None
        if linked_kb_id is not None:
            try:
                kb_num = kb_number_str(ctx.theme_mgr.get_kb(int(linked_kb_id)))
            except Exception:  # noqa: BLE001
                kb_num = None
        linked = FeedItemDocumentRef(kb_number=kb_num, document_id=str(linked_doc_id))
    match = timeline.get(item.get("url") or "")
    cats = item.get("categories")
    return FeedItemSummary(
        id=int(item["id"]),
        feed_id=int(item["feed_id"]),
        feed_title=item.get("feed_title"),
        feed_folder=item.get("feed_folder"),
        url=item.get("url"),
        title=item.get("title"),
        author=item.get("author"),
        summary=item.get("summary"),
        categories=[str(c) for c in cats] if isinstance(cats, list) else [],
        published_at=float(item["published_at"]) if item.get("published_at") is not None else None,
        modified_at=float(item["modified_at"]) if item.get("modified_at") is not None else None,
        fetched_at=float(item["fetched_at"]) if item.get("fetched_at") is not None else None,
        unread=bool(item.get("unread")),
        starred=bool(item.get("starred")),
        text=usercontent.remote_item_text(item.get("summary") or item.get("content") or "", 400),
        content=item.get("content") if with_content else None,
        linked=linked,
        timeline_item_id=str(match) if match is not None else None,
    )


def _authority(ctx: V1Context, origin: str) -> str:
    try:
        return WebRuntimeConfig.from_settings(ctx.settings).expected_authority
    except Exception:
        return origin.replace("https://", "").replace("http://", "").split("/", 1)[0]


def build_federation_handlers(ctx: V1Context) -> dict[str, Any]:
    """Build Starlette handlers for v1 federation and feeds endpoints."""

    @v1_endpoint
    async def federation_status(request: Request) -> JSONResponse:
        principal = enforce_v1_auth_policy(request)
        if principal.account_id is None or principal.scope != "owner":
            raise not_found_error("Federation status not found")
        account_id = principal.account_id
        origin = public_origin(ctx, request)
        authority = _authority(ctx, origin)

        def _fetch() -> FederationStatusResponse:
            conn = dbm.connect_existing(ctx.settings.db_file, readonly=True)
            try:
                store = accounts_store.AccountStore(conn)
                account = store.get(account_id)
                state = actor_state(account)
                return FederationStatusResponse(
                    handle=f"@{account.username}@{authority}" if account.username else None,
                    actor_id=f"{origin}/ap/actors/{account.public_id}" if account.username else None,
                    is_actor=(state == "active"),
                    actor_state=state,
                    discoverable=bool(account.profile_public),
                    followers=FederationFollowerCount(total=federation_store.count_followers(conn, account.id)),
                    pending_tasks=federation_store.count_pending_tasks(conn, account.id),
                )
            finally:
                conn.close()

        payload = await asyncio.to_thread(_fetch)
        return JSONResponse(payload.model_dump())

    @v1_endpoint
    async def federation_publish(request: Request) -> JSONResponse:
        principal = enforce_v1_auth_policy(request)
        if principal.account_id is None or principal.scope not in ("owner", "write"):
            raise not_found_error("Not found")
        account_id = principal.account_id
        body = await read_v1_json(request, FederationPublishRequest)
        kb_ref = body.kb.strip()
        personal_number: int | None = None
        if kb_ref != "personal":
            try:
                personal_number = ids.parse_public_id(kb_ref)
            except ValueError as exc:
                raise V1Error(400, "invalid_kb", "kb must be 'personal' or an external knowledge base number") from exc
            if personal_number < ids.MIN_CURRENT_NUMBER:
                raise V1Error(400, "invalid_kb", "Internal integer kb_id is not accepted")
        origin = public_origin(ctx, request)

        def _execute() -> FederationPublishResponse:
            conn = dbm.connect_existing(ctx.settings.db_file)
            try:
                store = accounts_store.AccountStore(conn)
                if personal_number is None:
                    row = conn.execute(
                        "SELECT id, account_id, is_public, is_personal FROM knowledge_bases WHERE account_id=? AND is_personal=1",
                        (account_id,),
                    ).fetchone()
                else:
                    row = conn.execute(
                        "SELECT id, account_id, is_public, is_personal FROM knowledge_bases WHERE number=?",
                        (personal_number,),
                    ).fetchone()
                if row is None or row["account_id"] != account_id or not row["is_personal"]:
                    raise not_found_error("Knowledge base not found")
                account = store.get(account_id)
                state = actor_state(account)
                if state != "active":
                    raise V1Error(409, "not_an_actor", f"Account is not an active actor ({state})", details={"actor_state": state})
                if not row["is_public"]:
                    raise V1Error(409, "theme_not_public", "Only public personal knowledge bases can publish")
                document = dbm.get_document_row(conn, body.document_id, kb_id=int(row["id"]))
                if document is None:
                    raise not_found_error("Document not found")
                if document["hidden"]:
                    raise V1Error(409, "document_not_public", "Hidden documents cannot be published")
                if "extraction_status" in document.keys() and str(document["extraction_status"] or "extracted") != "extracted":
                    raise V1Error(409, "document_not_extracted", "Document must be extracted before publishing")
                if not (document["detail"] or "").strip() and not (document["detail_html"] or "").strip():
                    raise V1Error(409, "document_has_no_body", "Document has no body to publish")
                dbm.create_doc_share(conn, body.document_id, kb_id=int(row["id"]))
                obj, task, created = federation_store.publish_document(
                    conn,
                    account_id=account_id,
                    kb_id=int(row["id"]),
                    document_id=body.document_id,
                    published_by_account_id=account_id,
                    salt=store.id_salt(),
                    now=time.time(),
                )
                conn.commit()
                return FederationPublishResponse(
                    object=FederationObjectSummary(
                        id=str(obj.public_id),
                        url=f"{origin}/ap/objects/{obj.public_id}",
                        published_at=float(obj.published_at),
                        updated_at=float(obj.updated_at) if obj.updated_at is not None else None,
                    ),
                    task=FederationTaskSummary(id=int(task.id), kind=str(task.kind)),
                    created=bool(created),
                )
            finally:
                conn.close()

        payload = await asyncio.to_thread(_execute)
        return JSONResponse(payload.model_dump(exclude_none=True), status_code=202)

    @v1_endpoint
    async def federation_retract(request: Request) -> JSONResponse:
        principal = enforce_v1_auth_policy(request)
        if principal.account_id is None or principal.scope not in ("owner", "write"):
            raise not_found_error("Not found")
        account_id = principal.account_id
        body = await read_v1_json(request, FederationRetractRequest)
        try:
            object_id = ids.parse_public_id(body.object_id)
        except ValueError as exc:
            raise V1Error(400, "invalid_request", "Invalid object_id") from exc

        def _execute() -> FederationRetractResponse:
            conn = dbm.connect_existing(ctx.settings.db_file)
            try:
                obj = federation_store.get_object(conn, object_id)
                if obj is None or obj.account_id != account_id:
                    raise not_found_error("Object not found")
                kb_row = conn.execute(
                    "SELECT account_id, is_personal FROM knowledge_bases WHERE id=?",
                    (obj.kb_id,),
                ).fetchone()
                if kb_row is None or kb_row["account_id"] != account_id or not kb_row["is_personal"]:
                    raise not_found_error("Object not found")
                task = federation_store.retract_object(conn, obj, now=time.time())
                conn.commit()
                return FederationRetractResponse(
                    retracted=True,
                    object_id=str(obj.public_id),
                    task=FederationTaskSummary(id=int(task.id), kind=str(task.kind)),
                )
            finally:
                conn.close()

        payload = await asyncio.to_thread(_execute)
        return JSONResponse(payload.model_dump(), status_code=202)

    def _client_db_path() -> Any:
        return client_db.client_db_path(ctx.settings.db_file)

    @v1_endpoint
    async def list_feeds(request: Request) -> JSONResponse:
        principal = enforce_v1_auth_policy(request)
        if principal.account_id is None or principal.scope != "owner":
            raise not_found_error("Feeds not found")
        account_id = principal.account_id
        params = request.query_params
        try:
            limit = parse_limit(params.get("limit"), default=50, minimum=1, maximum=100)
        except ValueError as exc:
            raise V1Error(400, "invalid_limit", "limit must be an integer between 1 and 100") from exc
        raw_cursor = params.get("cursor")
        cursor_idx: int | None = None
        if raw_cursor:
            try:
                cursor_idx = decode_number_cursor(raw_cursor)
            except ValueError as exc:
                raise V1Error(400, "invalid_cursor", "Invalid pagination cursor") from exc

        def _fetch() -> FeedListResponse:
            conn = client_db.connect_client_existing(_client_db_path(), readonly=True)
            try:
                feeds = feeds_store.list_feeds(conn, account_id)
            finally:
                conn.close()
            folders = sorted({str(feed["folder"]) for feed in feeds if feed.get("folder")})
            all_items = [_feed_summary(f) for f in feeds]
            total = len(all_items)
            start = cursor_idx if cursor_idx is not None else 0
            page_items = all_items[start : start + limit]
            next_cursor = encode_number_cursor(start + limit) if (start + limit) < total else None
            return FeedListResponse.model_validate(
                {
                    **paginated_payload(page_items, next_cursor=next_cursor, total=total),
                    "folders": folders,
                    "limit_per_account": feeds_store.FEED_LIMIT_PER_ACCOUNT,
                }
            )

        payload = await asyncio.to_thread(_fetch)
        return JSONResponse(payload.model_dump(exclude_none=True))

    @v1_endpoint
    async def create_feed(request: Request) -> JSONResponse:
        principal = enforce_v1_auth_policy(request)
        if principal.account_id is None or principal.scope != "owner":
            raise not_found_error("Feeds not found")
        account_id = principal.account_id
        body = await read_v1_json(request, CreateFeedRequest)
        try:
            url = feeds_store.normalize_feed_url(body.url)
        except ValueError as exc:
            raise V1Error(400, "invalid_url", "Invalid feed URL") from exc

        def _execute() -> tuple[FeedDetailResponse, bool]:
            conn = client_db.connect_client_existing(_client_db_path())
            try:
                try:
                    feed, created = feeds_store.add_feed(
                        conn,
                        account_id,
                        url,
                        title=body.title,
                        folder=body.folder,
                        now=time.time(),
                    )
                except feeds_store.FeedLimitReached as exc:
                    raise V1Error(
                        409,
                        "feed_limit",
                        "Account feed limit reached",
                        details={"limit": feeds_store.FEED_LIMIT_PER_ACCOUNT},
                    ) from exc
                conn.commit()
                return FeedDetailResponse(feed=_feed_summary(feed), created=bool(created)), bool(created)
            finally:
                conn.close()

        payload, created = await asyncio.to_thread(_execute)
        return JSONResponse(payload.model_dump(exclude_none=True), status_code=201 if created else 200)

    @v1_endpoint
    async def delete_feed(request: Request) -> JSONResponse:
        principal = enforce_v1_auth_policy(request)
        if principal.account_id is None or principal.scope != "owner":
            raise not_found_error("Feeds not found")
        account_id = principal.account_id
        raw_body = await read_optional_v1_json_dict(request)
        if raw_body:
            req = DeleteFeedRequest.model_validate(raw_body)
            feed_id = req.feed_id
        else:
            parsed = _positive_int(request.query_params.get("feed_id"))
            if parsed is None:
                raise V1Error(400, "invalid_request", "feed_id is required")
            feed_id = parsed

        def _execute() -> DeleteFeedResponse:
            conn = client_db.connect_client_existing(_client_db_path())
            try:
                removed = feeds_store.remove_feed(conn, account_id, feed_id)
                conn.commit()
            finally:
                conn.close()
            if removed is None:
                raise not_found_error("Feed not found")
            return DeleteFeedResponse(removed=True, feed_id=feed_id, items_removed=int(removed))

        payload = await asyncio.to_thread(_execute)
        return JSONResponse(payload.model_dump())

    @v1_endpoint
    async def list_feed_items(request: Request) -> JSONResponse:
        principal = enforce_v1_auth_policy(request)
        if principal.account_id is None or principal.scope != "owner":
            raise not_found_error("Feed items not found")
        account_id = principal.account_id
        params = request.query_params
        unread = _parse_flag(params.get("unread"), "unread")
        starred = _parse_flag(params.get("starred"), "starred")
        with_content = bool(_parse_flag(params.get("content"), "content"))
        raw_feed_id = params.get("feed_id")
        feed_id = _positive_int(raw_feed_id) if raw_feed_id is not None else None
        if raw_feed_id is not None and feed_id is None:
            raise V1Error(400, "invalid_request", "feed_id must be a positive integer")
        try:
            limit = parse_limit(params.get("limit"), default=50, minimum=1, maximum=feeds_store.ITEM_PAGE_MAX)
        except ValueError as exc:
            raise V1Error(400, "invalid_limit", f"limit must be an integer between 1 and {feeds_store.ITEM_PAGE_MAX}") from exc
        raw_cursor = params.get("cursor")
        before_id: int | None = None
        if raw_cursor:
            try:
                before_id = decode_number_cursor(raw_cursor)
            except ValueError as exc:
                raise V1Error(400, "invalid_cursor", "Invalid pagination cursor") from exc
        folder = params.get("folder")

        def _fetch() -> FeedItemListResponse:
            conn = client_db.connect_client_existing(_client_db_path(), readonly=True)
            try:
                rows = feeds_store.list_items(
                    conn,
                    account_id,
                    limit=limit + 1,
                    before_id=before_id,
                    feed_id=feed_id,
                    folder=folder,
                    unread=unread,
                    starred=starred,
                )
                has_more = len(rows) > limit
                page_rows = rows[:limit]
                timeline = feeds_store.timeline_matches(conn, account_id, [row["url"] for row in page_rows])
            finally:
                conn.close()
            items = [_feed_item_summary(ctx, row, with_content=with_content, timeline=timeline) for row in page_rows]
            next_cursor = encode_number_cursor(int(page_rows[-1]["id"])) if (has_more and page_rows) else None
            return FeedItemListResponse(items=items, next_cursor=next_cursor)

        payload = await asyncio.to_thread(_fetch)
        return JSONResponse(payload.model_dump(exclude_none=True))

    @v1_endpoint
    async def update_feed_items(request: Request) -> JSONResponse:
        principal = enforce_v1_auth_policy(request)
        if principal.account_id is None or principal.scope != "owner":
            raise not_found_error("Feed items not found")
        account_id = principal.account_id
        body = await read_v1_json(request, UpdateFeedItemsRequest)
        if (body.item_ids is None) == (body.feed_id is None):
            raise V1Error(400, "invalid_request", "Specify either item_ids or feed_id")
        if body.unread is None and body.starred is None:
            raise V1Error(400, "invalid_request", "Specify unread and/or starred")
        if body.feed_id is not None:
            if body.feed_id <= 0 or body.starred is not None:
                raise V1Error(400, "invalid_request", "feed_id takes unread only and must be positive")
            target_feed_id = body.feed_id
            unread_val = bool(body.unread)

            def _mark_feed() -> UpdateFeedItemsResponse:
                conn = client_db.connect_client_existing(_client_db_path())
                try:
                    if feeds_store.get_feed(conn, account_id, target_feed_id) is None:
                        raise not_found_error("Feed not found")
                    changed = feeds_store.mark_feed_items(conn, account_id, target_feed_id, unread=unread_val)
                    feeds_store.mark_timeline_of_feed(conn, account_id, target_feed_id, unread=unread_val)
                    conn.commit()
                    return UpdateFeedItemsResponse(changed=int(changed))
                finally:
                    conn.close()

            payload = await asyncio.to_thread(_mark_feed)
            return JSONResponse(payload.model_dump())

        assert body.item_ids is not None
        if not body.item_ids or len(body.item_ids) > feeds_store.MARK_MAX_ITEMS or any(i <= 0 for i in body.item_ids):
            raise V1Error(
                400,
                "invalid_request",
                f"item_ids must contain 1 to {feeds_store.MARK_MAX_ITEMS} positive integers",
            )
        item_ids = [int(i) for i in body.item_ids]

        def _mark_items() -> UpdateFeedItemsResponse:
            conn = client_db.connect_client_existing(_client_db_path())
            try:
                changed = feeds_store.mark_items(conn, account_id, item_ids, unread=body.unread, starred=body.starred)
                feeds_store.mark_timeline_by_url(
                    conn,
                    account_id,
                    feeds_store.item_urls(conn, account_id, item_ids),
                    unread=body.unread,
                    starred=body.starred,
                )
                conn.commit()
                return UpdateFeedItemsResponse(changed=int(changed))
            finally:
                conn.close()

        payload = await asyncio.to_thread(_mark_items)
        return JSONResponse(payload.model_dump())

    @v1_endpoint
    async def ingest_feed_item(request: Request) -> JSONResponse:
        principal = enforce_v1_auth_policy(request)
        if principal.scope != "owner" or principal.account_id is None:
            raise not_found_error("Feed item not found")
        account_id = principal.account_id
        item_id = _positive_int(request.path_params.get("item_id"))
        if item_id is None:
            raise not_found_error("Feed item not found")
        raw_body = await read_v1_json_dict(request)
        if "extraction_mode" not in raw_body:
            raise V1Error(400, "invalid_extraction_mode", "extraction_mode is required ('provider' or 'client')")
        try:
            parse_extraction_mode(raw_body.get("extraction_mode"))
        except ValueError as exc:
            raise V1Error(400, "invalid_extraction_mode", "extraction_mode must be 'provider' or 'client'") from exc
        req = FeedItemIngestRequest.model_validate(raw_body)
        if req.extraction_mode == "provider":
            await refuse_server_api_for_agent_client(ctx, request)
        kb, _, kb_svc = resolve_personal_kb_context(ctx, request)

        def _load_item() -> Any:
            conn = client_db.connect_client_existing(_client_db_path(), readonly=True)
            try:
                return feeds_store.get_item(conn, account_id, item_id)
            finally:
                conn.close()

        item = await asyncio.to_thread(_load_item)
        if item is None:
            raise not_found_error("Feed item not found")
        if not item["url"]:
            raise V1Error(409, "item_without_url", "Feed item has no URL to ingest")

        report = await ctx.run_expensive(
            kb_svc.ingest,
            item["url"],
            source="feed",
            extraction_mode=req.extraction_mode,
        )
        rep_dict = report_to_dict(report)
        rep_dict["kb_number"] = kb_number_str(kb)
        ingest_resp = IngestResponse.model_validate(rep_dict)

        document_id = getattr(report, "document_id", None)
        linked: FeedItemDocumentRef | None = None
        if document_id and not getattr(report, "error", None):
            def _link() -> None:
                conn = client_db.connect_client_existing(_client_db_path())
                try:
                    feeds_store.link_item(conn, account_id, item["id"], kb_id=kb.id, doc_id=str(document_id))
                    feeds_store.link_by_url(conn, account_id, [item["url"]], kb_id=kb.id, doc_id=str(document_id), timeline=True)
                    conn.commit()
                finally:
                    conn.close()

            await asyncio.to_thread(_link)
            linked = FeedItemDocumentRef(kb_number=kb_number_str(kb), document_id=str(document_id))

        payload = FeedItemIngestResponse(report=ingest_resp, linked=linked)
        return JSONResponse(payload.model_dump())

    return {
        "federation_status": federation_status,
        "federation_publish": federation_publish,
        "federation_retract": federation_retract,
        "list_feeds": list_feeds,
        "create_feed": create_feed,
        "delete_feed": delete_feed,
        "list_feed_items": list_feed_items,
        "update_feed_items": update_feed_items,
        "ingest_feed_item": ingest_feed_item,
    }
