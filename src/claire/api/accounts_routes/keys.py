"""``/api/me/keys``: 멤버 본인의 프로바이더 API 키 (설계 §11, P4 L4-b).

- 네트워크 모드 전용이다. 싱글 모드에서는 404(``ROUTE_POLICY``의 ``NETWORK_ONLY``와 같은 판정을 여기서도 한다).
- 관리자는 403 ``admin_uses_provider_settings``: 관리자는 ``providers.json``을 쓴다.
- 쓰기 전용 비밀이다(D8). 응답에는 §11.2 메타 필드만 싣고, 키 원문·일부 문자를 어디에도 싣지 않는다.
  요청 본문을 로그에 남기지 않으며, ``api_key`` 값은 읽자마자 ``register_secret``으로 등록해 로그 스크러버가 지우게 한다.
- 등록·교체(PUT)와 삭제(DELETE)는 단계 인증(`common.require_recent_auth`)을 요구한다.
- 등록은 저장 전에 ``provider_keys.verify_key``로 시험 호출한다. 거부면 저장하지 않고 422, 판정 불가(일시 장애)면 저장하지 않고 503.
- 재검증이 판정 불가면 상태를 바꾸지 않고 503 ``key_verification_unavailable``.
"""

from __future__ import annotations

import asyncio
import time
import unicodedata
from dataclasses import asdict
from typing import Any

from starlette.requests import Request
from starlette.responses import JSONResponse, Response
from starlette.routing import Route

from ...accounts.provider_policy import MEMBER_KEY_PROVIDERS
from ...secret_scrub import register_secret, scrub_text
from ...store import accounts as accounts_store
from ...store import auth_db
from ...store import provider_keys
from ..principal import Principal, request_principal
from ..security import read_json_body
from .common import (
    AccountsContext,
    escape,
    format_ts,
    json_error,
    not_found,
    open_auth_db,
    open_db,
    require_recent_auth,
)

ME_KEYS_PATH = "/api/me/keys"
ME_KEYS_VERIFY_PATH = "/api/me/keys/verify"
API_KEY_MAX_LENGTH = 512

STATUS_TEXT = {"active": "사용 중", "rejected": "거부됨", "unverified": "확인 전"}
PROVIDER_TEXT = {"gemini": "Gemini"}


def is_network_mode(ctx: AccountsContext) -> bool:
    conn = open_db(ctx)
    try:
        return accounts_store.AccountStore(conn).instance_mode() == "network"
    finally:
        conn.close()


def _is_member(principal: Principal) -> bool:
    return principal.account_id is not None and principal.instance_role == "member" and principal.scope == "owner"


def _meta_dict(meta: provider_keys.ProviderKeyMeta) -> dict[str, Any]:
    return asdict(meta)


def _clean_api_key(value: Any) -> str | None:
    """앞뒤 공백만 걷어 낸 키. 문자열이 아니거나 비었거나 길거나 공백·제어 문자가 섞이면 None."""
    if not isinstance(value, str):
        return None
    key = value.strip()
    if not key or len(key) > API_KEY_MAX_LENGTH:
        return None
    if any(ch.isspace() or unicodedata.category(ch).startswith("C") for ch in key):
        return None
    return key


# --- 동기 작업 (asyncio.to_thread) ------------------------------------------------


def list_meta(ctx: AccountsContext, account_id: int) -> list[dict[str, Any]]:
    auth = open_auth_db(ctx)
    try:
        return [_meta_dict(m) for m in provider_keys.get_key_meta(auth, account_id=account_id)]
    finally:
        auth.close()


def store_key(ctx: AccountsContext, account_id: int, provider: str, api_key: str) -> dict[str, Any]:
    auth = open_auth_db(ctx)
    try:
        meta = provider_keys.put_key(auth, account_id=account_id, provider=provider, api_key=api_key, status="active")
        auth_db.record_admin_audit(
            auth,
            actor_account_id=account_id,
            action="account.provider_key_put",
            target_account_id=account_id,
            detail={"provider": provider},
        )
        return _meta_dict(meta)
    finally:
        auth.close()


def reverify_key(ctx: AccountsContext, account_id: int, provider: str) -> dict[str, Any] | None:
    """저장된 키를 복호화해 다시 시험한다. 키가 없으면 None."""
    auth = open_auth_db(ctx)
    try:
        key = provider_keys.load_key(auth, account_id=account_id, provider=provider)
        if key is None:
            return None
        try:
            ok, message = provider_keys.verify_key(provider, key.api_key)
        except provider_keys.ProviderKeyVerifyUnavailable as exc:
            # 상태·last_verified_at을 바꾸지 않는다.
            return {"unavailable": True, "message": scrub_text(exc, extra=(key.api_key,))}
        message = scrub_text(message, extra=(key.api_key,))
        del key
        provider_keys.set_key_status(
            auth, account_id=account_id, provider=provider,
            status="active" if ok else "rejected", verified_at=time.time(),
        )
        meta = next(m for m in provider_keys.get_key_meta(auth, account_id=account_id) if m.provider == provider)
        return {"ok": ok, "message": message, "key": _meta_dict(meta)}
    finally:
        auth.close()


def remove_keys(ctx: AccountsContext, account_id: int, provider: str | None) -> int:
    auth = open_auth_db(ctx)
    try:
        deleted = provider_keys.delete_key(auth, account_id=account_id, provider=provider)
        if deleted:
            auth_db.record_admin_audit(
                auth,
                actor_account_id=account_id,
                action="account.provider_key_delete",
                target_account_id=account_id,
                detail={"provider": provider, "deleted": deleted},
            )
        return deleted
    finally:
        auth.close()


# --- /me 화면의 키 절 --------------------------------------------------------------


def _meta_rows(items: list[dict[str, Any]]) -> str:
    if not items:
        return '<p class="hint" data-provider-key-empty>등록한 키가 없습니다.</p>'
    parts = []
    for item in items:
        rows = [
            ("프로바이더", PROVIDER_TEXT.get(item["provider"], item["provider"])),
            ("fingerprint", item["fingerprint"]),
            ("상태", STATUS_TEXT.get(item["status"], item["status"])),
            ("등록", format_ts(item["created_at"])),
            ("마지막 확인", format_ts(item["last_verified_at"])),
            ("마지막 사용", format_ts(item["last_used_at"])),
        ]
        body = "".join(f"<dt>{escape(k)}</dt><dd>{escape(v)}</dd>" for k, v in rows)
        parts.append(f'<dl data-provider-key="{escape(item["provider"])}">{body}</dl>')
    return "".join(parts)


def keys_section_html(ctx: AccountsContext, principal: Principal) -> str:
    """네트워크 모드 멤버에게만 보이는 키 절. 그 밖에는 빈 문자열. 값은 모두 `escape`를 거친다."""
    if not _is_member(principal) or principal.account_id is None or not is_network_mode(ctx):
        return ""
    items = list_meta(ctx, principal.account_id)
    options = "".join(
        f'<option value="{escape(p)}">{escape(PROVIDER_TEXT.get(p, p))}</option>' for p in MEMBER_KEY_PROVIDERS
    )
    has_key = "1" if items else "0"
    return (
        '<section class="card" aria-labelledby="keys-title" data-provider-keys>'
        '<h2 id="keys-title">내 API 키</h2>'
        '<p class="hint">LLM 작업에 쓸 본인 API 키입니다. 등록한 키는 암호화해 저장하고, 다시 보거나 복사할 수 없습니다. '
        "화면과 API에는 키에서 파생한 fingerprint만 나옵니다. 키가 거부되거나 할당량이 다 되면 작업은 실패하며, "
        "서버 등급이나 다른 키로 바꿔 쓰지 않습니다.</p>"
        '<p class="hint">한계: 이 보호는 애플리케이션 경로로 키를 읽는 일을 막습니다. 서버가 키로 API를 호출하려면 '
        "복호화할 수밖에 없으므로, 서버 호스트의 root 권한자(프로세스 메모리·마스터 키 파일 접근)는 막지 못합니다.</p>"
        f'<div data-provider-key-meta aria-live="polite">{_meta_rows(items)}</div>'
        '</section>'
        '<form class="card" data-accounts-form="provider-key-put" aria-labelledby="key-put-title">'
        '<h2 id="key-put-title">키 등록·교체</h2>'
        '<label for="key_provider">프로바이더</label>'
        f'<select id="key_provider" name="provider">{options}</select>'
        '<label for="key_api_key">API 키</label>'
        '<input id="key_api_key" name="api_key" type="password" required autocomplete="off" '
        f'spellcheck="false" maxlength="{API_KEY_MAX_LENGTH}">'
        '<p class="hint">먼저 본인 확인을 해야 합니다. 저장하기 전에 시험 호출로 키를 확인하고, 실패하면 저장하지 않습니다. '
        "이미 등록한 키가 있으면 새 키로 바꿉니다.</p>"
        '<button type="submit">확인 후 저장</button>'
        '<p class="result" data-accounts-result aria-live="polite"></p>'
        "</form>"
        f'<form class="card" data-accounts-form="provider-key-verify" data-has-key="{has_key}" aria-labelledby="key-verify-title">'
        '<h2 id="key-verify-title">키 다시 확인</h2>'
        f'<input type="hidden" name="provider" value="{escape(MEMBER_KEY_PROVIDERS[0])}">'
        '<p class="hint">저장한 키로 시험 호출을 다시 해서 상태를 갱신합니다.</p>'
        '<button type="submit">다시 확인</button>'
        '<p class="result" data-accounts-result aria-live="polite"></p>'
        "</form>"
        '<form class="card" data-accounts-form="provider-key-delete" aria-labelledby="key-delete-title">'
        '<h2 id="key-delete-title">키 삭제</h2>'
        '<p class="hint">먼저 본인 확인을 해야 합니다. 삭제하면 서버 등급이 허용하는 범위에서만 LLM 작업을 합니다.</p>'
        '<button type="submit">삭제</button>'
        '<p class="result" data-accounts-result aria-live="polite"></p>'
        "</form>"
    )


# --- 라우트 ------------------------------------------------------------------------


def routes(ctx: AccountsContext) -> list[Route]:
    async def _gate(request: Request) -> tuple[Principal, Response | None]:
        principal = request_principal(request)
        if principal.account_id is None or not await asyncio.to_thread(is_network_mode, ctx):
            return principal, not_found()
        if principal.instance_role == "admin":
            return principal, json_error(403, "admin_uses_provider_settings")
        if not _is_member(principal):
            return principal, not_found()
        return principal, None

    async def keys_get(request: Request) -> Response:
        principal, denied = await _gate(request)
        if denied is not None:
            return denied
        assert principal.account_id is not None
        items = await asyncio.to_thread(list_meta, ctx, principal.account_id)
        return JSONResponse({"keys": items}, headers={"Cache-Control": "no-store"})

    async def keys_put(request: Request) -> Response:
        principal, denied = await _gate(request)
        if denied is not None:
            return denied
        assert principal.account_id is not None
        step_up = require_recent_auth(principal)
        if step_up is not None:
            return step_up
        body = await read_json_body(request)
        raw_key = body.get("api_key")
        if isinstance(raw_key, str):
            register_secret(raw_key)
            register_secret(raw_key.strip())
        body.pop("api_key", None)
        if set(body) != {"provider"} or raw_key is None:
            return json_error(400, "invalid_request", fields=["provider", "api_key"])
        provider = body["provider"]
        if not isinstance(provider, str) or provider not in MEMBER_KEY_PROVIDERS:
            return json_error(400, "unsupported_provider", providers=list(MEMBER_KEY_PROVIDERS))
        api_key = _clean_api_key(raw_key)
        del raw_key
        if api_key is None:
            return json_error(400, "invalid_api_key")
        try:
            ok, message = await asyncio.to_thread(provider_keys.verify_key, provider, api_key)
        except provider_keys.ProviderKeyVerifyUnavailable as exc:
            # 판정하지 못했으니 저장하지 않는다(거부로 보지도 않는다).
            return json_error(503, "key_verification_unavailable", message=scrub_text(exc, extra=(api_key,)))
        if not ok:
            return json_error(422, "key_verification_failed", message=scrub_text(message, extra=(api_key,)))
        meta = await asyncio.to_thread(store_key, ctx, principal.account_id, provider, api_key)
        return JSONResponse({"key": meta}, headers={"Cache-Control": "no-store"})

    async def keys_verify(request: Request) -> Response:
        principal, denied = await _gate(request)
        if denied is not None:
            return denied
        assert principal.account_id is not None
        body = await read_json_body(request)
        if set(body) != {"provider"}:
            return json_error(400, "invalid_request", fields=["provider"])
        provider = body["provider"]
        if not isinstance(provider, str) or provider not in MEMBER_KEY_PROVIDERS:
            return json_error(400, "unsupported_provider", providers=list(MEMBER_KEY_PROVIDERS))
        try:
            result = await asyncio.to_thread(reverify_key, ctx, principal.account_id, provider)
        except provider_keys.ProviderKeyError:
            return json_error(409, "key_unreadable")
        if result is None:
            return json_error(404, "key_not_found")
        if result.get("unavailable"):
            return json_error(503, "key_verification_unavailable", message=result["message"])
        return JSONResponse(result, headers={"Cache-Control": "no-store"})

    async def keys_delete(request: Request) -> Response:
        principal, denied = await _gate(request)
        if denied is not None:
            return denied
        assert principal.account_id is not None
        step_up = require_recent_auth(principal)
        if step_up is not None:
            return step_up
        if set(request.query_params) - {"provider"}:
            return json_error(400, "invalid_request", fields=["provider"])
        provider = request.query_params.get("provider")
        if provider is not None and provider not in MEMBER_KEY_PROVIDERS:
            return json_error(400, "unsupported_provider", providers=list(MEMBER_KEY_PROVIDERS))
        deleted = await asyncio.to_thread(remove_keys, ctx, principal.account_id, provider)
        return JSONResponse({"ok": True, "deleted": deleted})

    return [
        Route(ME_KEYS_PATH, keys_get, methods=["GET"]),
        Route(ME_KEYS_PATH, keys_put, methods=["PUT"]),
        Route(ME_KEYS_PATH, keys_delete, methods=["DELETE"]),
        Route(ME_KEYS_VERIFY_PATH, keys_verify, methods=["POST"]),
    ]
