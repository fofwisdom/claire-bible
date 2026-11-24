"""Shared knowledge base collaboration endpoints (`contributions` and `members`) for `/api/v1/*`."""

from __future__ import annotations

import asyncio
import json
import sqlite3
from typing import Any

from starlette.requests import Request
from starlette.responses import JSONResponse

from ...collaboration import contributions, reputation, theme_judgement
from ...core import ids
from ...notifications import events as notification_events
from ...store import accounts as accounts_store, db as dbm, kb_collab, themes as themes_store
from ...store.knowledge_base import KnowledgeBaseInfo, KnowledgeBaseNotFound
from ...federation.knowledge import offers as kf_offers
from ..namespace import kb_base_path, request_namespace
from ._context import (
    V1Context,
    enforce_v1_auth_policy,
    kb_decide,
    kb_number_str,
    public_origin,
    read_v1_json,
    require_v1_step_up,
    resolve_kb,
)
from .errors import V1Error, not_found_error, v1_endpoint
from .pagination import decode_number_cursor, encode_number_cursor, paginated_payload, parse_limit
from .schemas import (
    AccountRefSummary,
    ContributionDetailResponse,
    ContributionListResponse,
    ContributionOfferSummary,
    ContributionOriginRemovedInfo,
    ContributionRemoteInfo,
    ContributionReputationSummary,
    ContributionRevisionSummary,
    ContributionSummary,
    CreateContributionRequest,
    JoinMemberResponse,
    LeaveMemberResponse,
    MemberListResponse,
    MemberSummary,
    RemoveContributionRequest,
    RemoveContributionResponse,
    ReviewContributionRequest,
    UpdateMemberRequest,
    UpdateMemberResponse,
    WithdrawContributionResponse,
)

_MAX_REASON_LENGTH = 500


def _resolve_shared_kb(ctx: V1Context, request: Request, *, capability: str) -> KnowledgeBaseInfo:
    kb = resolve_kb(ctx, request, capability=capability)
    if not kb.is_shared:
        raise not_found_error("Shared knowledge base not found")
    return kb


def _contribution_number(request: Request) -> int:
    try:
        return ids.parse_public_id(str(request.path_params.get("number") or ""))
    except ValueError as exc:
        raise not_found_error("Contribution not found") from exc


def _document_title(conn: sqlite3.Connection, document_id: Any) -> str | None:
    if not document_id:
        return None
    found = conn.execute("SELECT title FROM documents WHERE id=?", (str(document_id),)).fetchone()
    return None if found is None else str(found[0]) if found[0] is not None else None


def _contribution_view(
    ctx: V1Context,
    conn: sqlite3.Connection,
    row: dict[str, Any],
    *,
    full: bool,
    review_help: bool = False,
    cache: dict[str, Any] | None = None,
) -> ContributionSummary:
    cache = {} if cache is None else cache
    title = _document_title(conn, row["document_id"])
    if title is None and full:
        title = _document_title(conn, row["source_document_id"])
    contributor: AccountRefSummary | None = None
    if row["contributor_account_id"] is not None:
        try:
            account = accounts_store.AccountStore(conn).get(int(row["contributor_account_id"]))
            contributor = AccountRefSummary(
                account=ids.format_public_id(account.public_id),
                display_name=account.display_name,
            )
        except accounts_store.AccountNotFound:
            contributor = None

    remote: ContributionRemoteInfo | None = None
    if row["remote_source_iri"]:
        remote = ContributionRemoteInfo(
            document=str(row["remote_source_iri"]),
            knowledge_base=str(row["remote_source_kb_iri"]) if row["remote_source_kb_iri"] else None,
            credit=str(row["remote_credit_iri"]) if row["remote_credit_iri"] else None,
        )

    origin_removed: ContributionOriginRemovedInfo | None = None
    reason: str | None = None
    revisions: list[ContributionRevisionSummary] | None = None
    offer: ContributionOfferSummary | None = None
    if full:
        if row["origin_removed_at"] is not None:
            origin_removed = ContributionOriginRemovedInfo(
                at=float(row["origin_removed_at"]),
                reason=str(row["origin_removed_reason"]) if row["origin_removed_reason"] is not None else None,
                waiting=bool(contributions.in_origin_review(row)),
            )
        reason = str(row["decision_reason"]) if row["decision_reason"] is not None else None
        revisions = [
            ContributionRevisionSummary(
                document_id=str(r["document_id"]),
                state=str(r["state"]),
                created_at=float(r["created_at"]),
            )
            for r in kb_collab.list_revisions(conn, int(row["public_id"]))
        ]
        raw_offer = kf_offers.inbound_of_contribution(conn, int(row["public_id"]))
        if raw_offer is not None:
            offer = ContributionOfferSummary(
                actor=str(row["remote_credit_iri"]) if row["remote_credit_iri"] else None,
                server=str(raw_offer["domain"]),
                with_grant=bool(raw_offer["capability_iri"]),
                grant_valid=bool(raw_offer["grant_valid"]),
            )

    rep_summary: ContributionReputationSummary | None = None
    if review_help and row["contributor_account_id"] is None and row["remote_credit_iri"] and not row["remote_source_kb_iri"]:
        reputations = cache.setdefault("remote_reputations", {})
        actor_iri = str(row["remote_credit_iri"])
        if actor_iri not in reputations:
            reputations[actor_iri] = reputation.compute_remote(conn, actor_iri)
        level, rate = reputations[actor_iri]
        rep_summary = ContributionReputationSummary(level=str(level), approval_rate=float(rate) if rate is not None else None)
    elif review_help and row["contributor_account_id"] is not None:
        reputations = cache.setdefault("reputations", {})
        contributor_id = int(row["contributor_account_id"])
        if contributor_id not in reputations:
            reputations[contributor_id] = reputation.compute(conn, contributor_id)
        rep_obj = reputations[contributor_id]
        rep_summary = ContributionReputationSummary(
            level=str(rep_obj.level),
            approval_rate=float(rep_obj.approval_rate) if rep_obj.approval_rate is not None else None,
        )

    review_help_data: dict[str, Any] | None = None
    if review_help and row["submission_item_id"] is not None and row["state"] == "pending":
        item = conn.execute(
            "SELECT theme_id, match_score, routed_by FROM kb_submission_items WHERE id=?",
            (int(row["submission_item_id"]),),
        ).fetchone()
        others: list[dict[str, Any]] = []
        for other in kb_collab.contributions_of_item(conn, int(row["submission_item_id"])):
            if int(other["kb_id"]) == int(row["kb_id"]):
                continue
            try:
                other_kb = ctx.theme_mgr.get_kb(int(other["kb_id"]))
                kb_ref = {"kb_number": kb_number_str(other_kb), "label": other_kb.label}
            except KnowledgeBaseNotFound:
                kb_ref = {"kb_number": None, "label": None}
            others.append({**kb_ref, "decision": contributions.decision_of(other), "reason": other["decision_reason"]})
        kb_score = None
        if "signatures" not in cache:
            cache["signatures"] = {int(sig["kb_id"]): sig for sig in themes_store.load_signatures(conn)}
        signature = cache["signatures"].get(int(row["kb_id"]))
        if signature is not None and row["document_id"]:
            if "matcher" not in cache:
                cache["matcher"] = theme_judgement.DocumentMatcher.load(conn)
            matcher = cache["matcher"]
            keys = (
                theme_judgement.document_keys(conn, int(row["source_kb_id"]), str(row["source_document_id"]))
                if row["source_kb_id"] is not None
                else set()
            )
            kb_keys = {(str(e[0]), str(e[1])) for e in json.loads(signature["entities"] or "[]")}
            kb_score = matcher.score(keys, kb_keys) if keys else None
        review_help_data = {
            "other_decisions": others,
            "theme_score": None if item is None else item["match_score"],
            "routed_by": None if item is None else item["routed_by"],
            "kb_score": kb_score,
        }

    return ContributionSummary(
        number=ids.format_public_id(int(row["public_id"])),
        document_id=str(row["document_id"]) if row["document_id"] is not None else None,
        title=title,
        origin=str(row["origin"]),
        state=str(row["state"]),
        contributor=contributor,
        approved=row["approved_at"] is not None,
        approved_at=float(row["approved_at"]) if row["approved_at"] is not None else None,
        updates_stopped=bool(row["updates_frozen"]),
        contributed_at=float(row["contributed_at"]),
        revised_at=float(row["revised_at"]) if row["revised_at"] is not None else None,
        remote=remote,
        origin_removed=origin_removed,
        reason=reason,
        revisions=revisions,
        offer=offer,
        contributor_reputation=rep_summary,
        review_help=review_help_data,
    )


def _member_view(row: dict[str, Any], *, mode: str) -> MemberSummary:
    return MemberSummary(
        number=ids.format_public_id(int(row["membership_public_id"])),
        account=ids.format_public_id(int(row["account_public_id"])),
        display_name=str(row["display_name"]),
        role=str(row["role"]),
        state=str(row["state"]),
        requested_at=float(row["requested_at"]),
        decided_at=float(row["decided_at"]) if row["decided_at"] is not None else None,
        username=str(row["username"]) if (mode == "network" and row["username"]) else None,
    )


def _member_row(conn: sqlite3.Connection, kb_id: int, account_id: int) -> dict[str, Any] | None:
    rows = [
        r
        for r in kb_collab.members_with_accounts(conn, kb_id, states=kb_collab.MEMBERSHIP_STATES)
        if r["account_id"] == account_id
    ]
    return rows[0] if rows else None


def _run_merge_jobs_of(ctx: V1Context, kb: KnowledgeBaseInfo) -> None:
    conn = dbm.connect_existing(ctx.settings.db_file)
    try:
        contributions.run_pending_merge_jobs(
            conn,
            lambda kb_id: ctx.service_pool.get_service(kb_id).provider,
            lambda kb_id: ctx.theme_mgr.get_settings_for_kb(kb_id, ctx.settings),
            kb_id=kb.id,
        )
    finally:
        conn.close()


def _contribution_v1_error(exc: contributions.ContributionError, *, kb: KnowledgeBaseInfo, origin: str) -> V1Error:
    details: dict[str, Any] | None = None
    if "document" in exc.extra and exc.extra.get("visible") and kb.number is not None:
        details = {
            "document_id": str(exc.extra["document"]),
            "url": f"{origin}{kb_base_path(kb.number)}?doc={exc.extra['document']}",
        }
    return V1Error(exc.status, exc.code, exc.code, details=details)


def _server_mode(request: Request) -> str:
    try:
        return request_namespace(request).mode
    except Exception:
        return "single"


def build_collaboration_handlers(ctx: V1Context) -> dict[str, Any]:
    """Build Starlette handlers for shared KB collaboration v1 endpoints."""

    @v1_endpoint
    async def list_contributions(request: Request) -> JSONResponse:
        principal = enforce_v1_auth_policy(request)
        kb = _resolve_shared_kb(ctx, request, capability="read")
        is_reviewer = kb_decide(ctx, request, kb, "review") == "allow"
        params = request.query_params
        state = params.get("state")
        if state is not None and state not in kb_collab.CONTRIBUTION_STATES:
            raise V1Error(400, "invalid_state", "Invalid contribution state filter")
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

        def _list() -> ContributionListResponse:
            conn = dbm.connect_existing(ctx.settings.db_file, readonly=True)
            try:
                views: list[ContributionSummary] = []
                cache: dict[str, Any] = {}
                for row in reversed(kb_collab.list_contributions(conn, kb.id, state=state)):
                    own = principal.account_id is not None and row["contributor_account_id"] == principal.account_id
                    if row["state"] == "visible" or is_reviewer or own:
                        views.append(_contribution_view(ctx, conn, row, full=is_reviewer or own, review_help=is_reviewer, cache=cache))
                if is_reviewer and state == "pending":
                    views.sort(
                        key=lambda item: (
                            reputation.queue_rank(item.contributor_reputation.level if item.contributor_reputation else None),
                            item.contributed_at,
                        )
                    )
                total = len(views)
                start = cursor_idx if cursor_idx is not None else 0
                page_items = views[start : start + limit]
                next_cursor = encode_number_cursor(start + limit) if (start + limit) < total else None
                return ContributionListResponse.model_validate(
                    paginated_payload(page_items, next_cursor=next_cursor, total=total)
                )
            finally:
                conn.close()

        payload = await asyncio.to_thread(_list)
        return JSONResponse(payload.model_dump(exclude_none=True))

    @v1_endpoint
    async def create_contribution(request: Request) -> JSONResponse:
        principal = enforce_v1_auth_policy(request)
        kb = _resolve_shared_kb(ctx, request, capability="contribute_direct")
        if principal.account_id is None:
            raise V1Error(401, "authentication_required", "Authentication required")
        account_id = principal.account_id
        body = await read_v1_json(request, CreateContributionRequest)
        doc_id = body.document_id.strip()
        if not doc_id:
            raise V1Error(400, "document_required", "document_id is required")
        is_reviewer = kb_decide(ctx, request, kb, "review") == "allow"
        origin = public_origin(ctx, request)

        def _contribute() -> ContributionDetailResponse:
            try:
                personal = ctx.theme_mgr.personal_kb_for(account_id)
            except KnowledgeBaseNotFound as exc:
                raise not_found_error("Document not found") from exc
            conn = dbm.connect_existing(ctx.settings.db_file)
            try:
                try:
                    approved, pending = contributions.direct_mode(
                        conn,
                        kb_id=kb.id,
                        account_id=account_id,
                        is_reviewer=is_reviewer,
                        is_admin=principal.server_role == "admin",
                    )
                    row = contributions.contribute_direct(
                        conn,
                        kb_id=kb.id,
                        source_kb_id=personal.id,
                        source_document_id=doc_id,
                        contributor_account_id=account_id,
                        approved=approved,
                        pending=pending,
                    )
                except contributions.ContributionError as exc:
                    conn.rollback()
                    raise _contribution_v1_error(exc, kb=kb, origin=origin) from exc
                conn.commit()
                public_id = int(row["public_id"])
            finally:
                conn.close()
            _run_merge_jobs_of(ctx, kb)
            rconn = dbm.connect_existing(ctx.settings.db_file, readonly=True)
            try:
                latest = kb_collab.get_contribution(rconn, public_id) or row
                return ContributionDetailResponse(contribution=_contribution_view(ctx, rconn, latest, full=True))
            finally:
                rconn.close()

        payload = await asyncio.to_thread(_contribute)
        return JSONResponse(payload.model_dump(exclude_none=True), status_code=201)

    @v1_endpoint
    async def review_contribution(request: Request) -> JSONResponse:
        principal = enforce_v1_auth_policy(request)
        kb = _resolve_shared_kb(ctx, request, capability="review")
        if principal.account_id is None:
            raise V1Error(401, "authentication_required", "Authentication required")
        actor = principal.account_id
        number = _contribution_number(request)
        body = await read_v1_json(request, ReviewContributionRequest)

        def _review() -> ContributionDetailResponse:
            conn = dbm.connect_existing(ctx.settings.db_file)
            try:
                try:
                    if body.action == "approve":
                        contributions.approve(
                            conn,
                            kb_id=kb.id,
                            contribution=number,
                            account_id=actor,
                            is_admin=principal.server_role == "admin",
                        )
                    elif body.action == "reject":
                        contributions.reject(
                            conn,
                            ctx.settings.data_dir,
                            kb_id=kb.id,
                            contribution=number,
                            account_id=actor,
                            reason=body.reason,
                        )
                    elif body.action in ("hide", "unhide"):
                        contributions.set_hidden(
                            conn,
                            kb_id=kb.id,
                            contribution=number,
                            account_id=actor,
                            hidden=body.action == "hide",
                        )
                    else:
                        contributions.set_updates_frozen(
                            conn,
                            kb_id=kb.id,
                            contribution=number,
                            account_id=actor,
                            frozen=body.action == "stop_updates",
                        )
                except contributions.ContributionError as exc:
                    conn.rollback()
                    raise V1Error(exc.status, exc.code, exc.code) from exc
                conn.commit()
                if body.action in ("approve", "reject"):
                    decided = kb_collab.get_contribution(conn, number)
                    notification_events.kb_contribution_decided(
                        ctx.settings.db_file,
                        kb_id=kb.id,
                        contributor_account_id=None if decided is None else decided["contributor_account_id"],
                        decision="approved" if body.action == "approve" else "rejected",
                        actor_account_id=actor,
                    )
            finally:
                conn.close()
            if body.action == "approve":
                _run_merge_jobs_of(ctx, kb)
            rconn = dbm.connect_existing(ctx.settings.db_file, readonly=True)
            try:
                row = kb_collab.get_contribution(rconn, number)
                view = None if row is None else _contribution_view(ctx, rconn, row, full=True)
                return ContributionDetailResponse(contribution=view)
            finally:
                rconn.close()

        payload = await asyncio.to_thread(_review)
        return JSONResponse(payload.model_dump(exclude_none=True))

    @v1_endpoint
    async def withdraw_contribution(request: Request) -> JSONResponse:
        principal = enforce_v1_auth_policy(request)
        kb = _resolve_shared_kb(ctx, request, capability="read")
        if principal.account_id is None:
            raise not_found_error("Contribution not found")
        account_id = principal.account_id
        number = _contribution_number(request)

        def _withdraw() -> WithdrawContributionResponse:
            conn = dbm.connect_existing(ctx.settings.db_file)
            try:
                result = contributions.withdraw(
                    conn,
                    ctx.settings.data_dir,
                    kb_id=kb.id,
                    contribution=number,
                    account_id=account_id,
                )
            except contributions.ContributionError as exc:
                raise V1Error(exc.status, exc.code, exc.code) from exc
            finally:
                conn.close()
            return WithdrawContributionResponse(
                withdrawn=True,
                contribution_blocked=bool(result["contribution_blocked"]),
            )

        payload = await asyncio.to_thread(_withdraw)
        return JSONResponse(payload.model_dump())

    @v1_endpoint
    async def retry_contribution(request: Request) -> JSONResponse:
        principal = enforce_v1_auth_policy(request)
        kb = _resolve_shared_kb(ctx, request, capability="read")
        number = _contribution_number(request)
        is_reviewer = kb_decide(ctx, request, kb, "review") == "allow"
        if kb.state == "archived":
            raise V1Error(403, "kb_archived", "Archived knowledge base is read-only")
        can_contribute = kb_decide(ctx, request, kb, "contribute_direct") == "allow"

        def _retry() -> ContributionDetailResponse:
            conn = dbm.connect_existing(ctx.settings.db_file)
            try:
                row = kb_collab.get_contribution(conn, number)
                if row is None or int(row["kb_id"]) != kb.id or not (
                    is_reviewer or (principal.account_id is not None and row["contributor_account_id"] == principal.account_id)
                ):
                    raise not_found_error("Contribution not found")
                if not is_reviewer and not can_contribute:
                    raise V1Error(403, "kb_capability_required", "Insufficient knowledge base permissions")
                try:
                    contributions.retry_merge(conn, contribution=number)
                except contributions.ContributionError as exc:
                    conn.rollback()
                    raise V1Error(exc.status, exc.code, exc.code) from exc
                conn.commit()
            finally:
                conn.close()
            _run_merge_jobs_of(ctx, kb)
            rconn = dbm.connect_existing(ctx.settings.db_file, readonly=True)
            try:
                latest = kb_collab.get_contribution(rconn, number)
                view = None if latest is None else _contribution_view(ctx, rconn, latest, full=True)
                return ContributionDetailResponse(contribution=view)
            finally:
                rconn.close()

        payload = await asyncio.to_thread(_retry)
        return JSONResponse(payload.model_dump(exclude_none=True))

    @v1_endpoint
    async def remove_contribution(request: Request) -> JSONResponse:
        principal = enforce_v1_auth_policy(request)
        kb = _resolve_shared_kb(ctx, request, capability="remove")
        if principal.account_id is None:
            raise V1Error(401, "authentication_required", "Authentication required")
        actor_id = principal.account_id
        number = _contribution_number(request)
        body = await read_v1_json(request, RemoveContributionRequest)

        def _remove() -> RemoveContributionResponse:
            conn = dbm.connect_existing(ctx.settings.db_file)
            try:
                contributions.remove(
                    conn,
                    ctx.settings.data_dir,
                    kb_id=kb.id,
                    contribution=number,
                    account_id=actor_id,
                    reason=body.reason,
                )
                removed = kb_collab.get_contribution(conn, number)
                notification_events.kb_contribution_decided(
                    ctx.settings.db_file,
                    kb_id=kb.id,
                    contributor_account_id=None if removed is None else removed["contributor_account_id"],
                    decision="removed",
                    actor_account_id=actor_id,
                )
            except contributions.ContributionError as exc:
                raise V1Error(exc.status, exc.code, exc.code) from exc
            finally:
                conn.close()
            return RemoveContributionResponse(removed=True)

        payload = await asyncio.to_thread(_remove)
        return JSONResponse(payload.model_dump())

    @v1_endpoint
    async def list_members(request: Request) -> JSONResponse:
        enforce_v1_auth_policy(request)
        kb = _resolve_shared_kb(ctx, request, capability="read")
        sees_all = kb_decide(ctx, request, kb, "people") == "allow"
        mode = _server_mode(request)
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

        def _members() -> MemberListResponse:
            conn = dbm.connect_existing(ctx.settings.db_file, readonly=True)
            try:
                states = kb_collab.MEMBERSHIP_STATES if sees_all else ("active",)
                all_members = [_member_view(r, mode=mode) for r in kb_collab.members_with_accounts(conn, kb.id, states=states)]
                total = len(all_members)
                start = cursor_idx if cursor_idx is not None else 0
                page_items = all_members[start : start + limit]
                next_cursor = encode_number_cursor(start + limit) if (start + limit) < total else None
                return MemberListResponse.model_validate(
                    paginated_payload(page_items, next_cursor=next_cursor, total=total)
                )
            finally:
                conn.close()

        payload = await asyncio.to_thread(_members)
        return JSONResponse(payload.model_dump(exclude_none=True))

    @v1_endpoint
    async def join_member(request: Request) -> JSONResponse:
        principal = enforce_v1_auth_policy(request)
        kb = _resolve_shared_kb(ctx, request, capability="join")
        if principal.account_id is None:
            raise V1Error(401, "authentication_required", "Authentication required")
        if kb.state == "archived":
            raise V1Error(403, "kb_archived", "Archived knowledge base cannot accept new members")
        account_id = principal.account_id
        mode = _server_mode(request)

        def _join() -> tuple[JoinMemberResponse, bool]:
            conn = dbm.connect_existing(ctx.settings.db_file)
            try:
                current = kb_collab.get_membership(conn, kb.id, account_id)
                created = current is None
                if created:
                    state = "active" if kb.join_policy == "open" else "pending"
                    kb_collab.add_membership(conn, kb_id=kb.id, account_id=account_id, role="member", state=state)
                    conn.commit()
                    if kb.join_policy != "open":
                        notification_events.kb_join_request(ctx.settings.db_file, kb_id=kb.id, actor_account_id=account_id)
                row = _member_row(conn, kb.id, account_id)
                assert row is not None
                return JoinMemberResponse(created=created, membership=_member_view(row, mode=mode)), created
            finally:
                conn.close()

        payload, created = await asyncio.to_thread(_join)
        return JSONResponse(payload.model_dump(exclude_none=True), status_code=201 if created else 200)

    @v1_endpoint
    async def leave_member(request: Request) -> JSONResponse:
        principal = enforce_v1_auth_policy(request)
        kb = _resolve_shared_kb(ctx, request, capability="join")
        if principal.account_id is None:
            raise V1Error(401, "authentication_required", "Authentication required")
        account_id = principal.account_id

        def _leave() -> LeaveMemberResponse:
            conn = dbm.connect_existing(ctx.settings.db_file)
            try:
                current = kb_collab.get_membership(conn, kb.id, account_id)
                if current is None:
                    raise not_found_error("Membership not found")
                if current["role"] == "moderator" and current["state"] == "active" and kb_collab.active_moderators(conn, kb.id) == [account_id]:
                    raise V1Error(409, "last_moderator", "The last active moderator cannot leave")
                kb_collab.delete_membership(conn, kb.id, account_id)
                if current["state"] == "active" and current["role"] != "member":
                    target_pub = ids.format_public_id(accounts_store.AccountStore(conn).get(account_id).public_id)
                    kb_collab.log_moderation(
                        conn,
                        kb_id=kb.id,
                        actor_account_id=account_id,
                        action="leave",
                        target=target_pub,
                        reason=str(current["role"]),
                    )
                conn.commit()
                return LeaveMemberResponse(left=True)
            finally:
                conn.close()

        payload = await asyncio.to_thread(_leave)
        return JSONResponse(payload.model_dump())

    @v1_endpoint
    async def update_member(request: Request) -> JSONResponse:
        principal = enforce_v1_auth_policy(request)
        kb = _resolve_shared_kb(ctx, request, capability="people")
        if principal.account_id is None:
            raise V1Error(401, "authentication_required", "Authentication required")
        actor_id = principal.account_id
        body = await read_v1_json(request, UpdateMemberRequest)
        if (body.action is None) == (body.role is None):
            raise V1Error(400, "action_or_role_required", "Provide either action or role, not both")
        if len(body.reason) > _MAX_REASON_LENGTH:
            raise V1Error(400, "reason_invalid", f"reason must be at most {_MAX_REASON_LENGTH} characters")
        reason = body.reason.strip()
        if body.role is not None:
            require_v1_step_up(principal)
        raw_account = str(request.path_params.get("account") or "")
        mode = _server_mode(request)

        def _change() -> UpdateMemberResponse:
            conn = dbm.connect_existing(ctx.settings.db_file)
            try:
                try:
                    public_id = ids.parse_public_id(raw_account)
                except ValueError as exc:
                    raise not_found_error("Account not found") from exc
                target = accounts_store.AccountStore(conn).find_by_public_id(public_id)
                if target is None or target.status != "active":
                    raise not_found_error("Account not found")
                if target.id == actor_id:
                    raise V1Error(400, "self_not_allowed", "Cannot modify your own membership")
                current = kb_collab.get_membership(conn, kb.id, target.id)
                if current is not None and current["role"] == "moderator" and current["state"] == "active":
                    raise V1Error(403, "moderator_protected", "Moderators can only be changed by a server admin")
                log_action = body.action if body.action is not None else f"role_{body.role}"
                if body.action == "approve":
                    if current is None or current["state"] != "pending":
                        raise V1Error(409, "not_pending", "Membership is not pending approval")
                    kb_collab.update_membership(conn, kb_id=kb.id, account_id=target.id, role="member", state="active", decided_by_account_id=actor_id)
                elif body.action == "ban":
                    if current is None:
                        kb_collab.add_membership(conn, kb_id=kb.id, account_id=target.id, role="member", state="banned", decided_by_account_id=actor_id)
                    else:
                        kb_collab.update_membership(conn, kb_id=kb.id, account_id=target.id, role="member", state="banned", decided_by_account_id=actor_id)
                elif body.action == "remove":
                    if current is None:
                        raise not_found_error("Membership not found")
                    kb_collab.delete_membership(conn, kb.id, target.id)
                else:
                    if current is None or current["state"] != "active":
                        raise V1Error(409, "not_active_member", "Role changes require an active membership")
                    assert body.role is not None
                    kb_collab.update_membership(conn, kb_id=kb.id, account_id=target.id, role=body.role, decided_by_account_id=actor_id)
                kb_collab.log_moderation(
                    conn,
                    kb_id=kb.id,
                    actor_account_id=actor_id,
                    action=log_action,
                    target=ids.format_public_id(target.public_id),
                    reason=reason,
                )
                conn.commit()
                if body.action == "approve":
                    notification_events.kb_join_approved(ctx.settings.db_file, kb_id=kb.id, account_id=target.id, actor_account_id=actor_id)
                elif body.role == "collaborator" and (current is None or current["role"] != "collaborator"):
                    notification_events.kb_role_given(ctx.settings.db_file, kb_id=kb.id, account_ids=[target.id], role="collaborator", actor_account_id=actor_id)
                row = _member_row(conn, kb.id, target.id)
                return UpdateMemberResponse(member=None if row is None else _member_view(row, mode=mode))
            finally:
                conn.close()

        payload = await asyncio.to_thread(_change)
        return JSONResponse(payload.model_dump(exclude_none=True))

    return {
        "list_contributions": list_contributions,
        "create_contribution": create_contribution,
        "review_contribution": review_contribution,
        "withdraw_contribution": withdraw_contribution,
        "retry_contribution": retry_contribution,
        "remove_contribution": remove_contribution,
        "list_members": list_members,
        "join_member": join_member,
        "leave_member": leave_member,
        "update_member": update_member,
    }
