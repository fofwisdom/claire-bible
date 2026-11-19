"""``/api/me/keys``: 계정 본인의 프로바이더 API 키 (설계 §11, P4 L4-b).

- 멤버는 네트워크 모드에서만 쓴다. 싱글 모드의 비관리자는 404.
- 서버 관리자는 두 모드에서 개인 키를 쓴다(owner decision 2026-11-19). 관리자 계정의 작업은 개인 키가 있으면 그 키만 쓰고, 없으면 ``providers.json``을 쓴다. 서버 작업은 언제나 ``providers.json``을 쓴다.
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

from ...accounts.provider_policy import (
    ADMIN_ONLY_FEATURES,
    EFFORT_ORDER,
    FEATURE_SLOTS,
    MEMBER_KEY_PROVIDERS,
    split_gemini_model_and_effort,
)
from ...i18n import t
from ...secret_scrub import register_secret, scrub_text
from ...store import accounts as accounts_store
from ...store import auth_db
from ...store import provider_keys
from ...store.secrets import BYOK_SEAL_COOKIE, generate_user_seal, register_volatile_user_seal
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

# The text of a key status is the catalog key ``console.settings.keys.status.<status>``.
KEY_STATUSES = ("active", "rejected", "unverified")
PROVIDER_TEXT = {"gemini": "Google AI Studio"}


def is_network_mode(ctx: AccountsContext) -> bool:
    conn = open_db(ctx)
    try:
        return accounts_store.AccountStore(conn).server_mode() == "network"
    finally:
        conn.close()


def _is_member(principal: Principal) -> bool:
    return principal.account_id is not None and principal.server_role == "member" and principal.scope == "owner"


def _is_owner_admin(principal: Principal) -> bool:
    return principal.account_id is not None and principal.server_role == "admin" and principal.scope == "owner"


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


def _admin_defaults(ctx: AccountsContext) -> dict[str, dict[str, str]]:
    """The effective model and effort of the server providers for each feature slot: the defaults of a key without model preferences."""
    from ...provider_manager import ProviderManager

    data_dir = ctx.data_dir or ctx.db_file.parent
    cfg: dict[str, Any] = {}
    try:
        pm = ProviderManager(data_dir=data_dir, auto_migrate=False)
        cfg = pm.get_config()
    except Exception:
        cfg = {}
    providers = cfg.get("providers", {}) if isinstance(cfg, dict) else {}
    active = str(cfg.get("active_provider") or getattr(ctx.settings, "provider", "mock") or "mock").strip().lower()
    raw_routing = cfg.get("feature_routing") if isinstance(cfg.get("feature_routing"), dict) else getattr(ctx.settings, "feature_routing", {}) or {}

    def _prov_defaults(pname: str, slot: str) -> tuple[str, str]:
        pdata = providers.get(pname, {}) if isinstance(providers, dict) else {}
        if pname == "gemini":
            if slot == "embed":
                return str(pdata.get("embed_model") or getattr(ctx.settings, "gemini_embed_model", "gemini-embedding-001") or "gemini-embedding-001"), ""
            gem_m = str(pdata.get("model") or getattr(ctx.settings, "gemini_model", "") or "")
            _, gem_embedded_eff = split_gemini_model_and_effort(gem_m)
            return gem_m, gem_embedded_eff or str(pdata.get("effort") or getattr(ctx.settings, "gemini_effort", "medium") or "medium")
        if pname == "antigravity":
            return str(pdata.get("model") or getattr(ctx.settings, "agy_model", "") or ""), str(pdata.get("effort") or getattr(ctx.settings, "agy_effort", "medium") or "medium")
        if pname == "codex":
            return str(pdata.get("model") or getattr(ctx.settings, "codex_model", "") or "codex-default"), str(pdata.get("effort") or getattr(ctx.settings, "codex_effort", "medium") or "medium")
        return ("mock", "") if pname == "mock" else ("", "")

    defaults: dict[str, dict[str, str]] = {}
    for slot in FEATURE_SLOTS:
        slot_cfg = raw_routing.get(slot, {}) if isinstance(raw_routing, dict) else {}
        slot_prov = str(slot_cfg.get("provider") or "inherit").strip().lower() or "inherit"
        eff_prov = active if slot_prov == "inherit" else slot_prov
        def_m, def_e = _prov_defaults(eff_prov, slot)
        model = str(slot_cfg.get("model") or def_m or "").strip()
        effort = str(slot_cfg.get("effort") or def_e or "").strip().lower()
        if eff_prov == "gemini" and slot != "embed":
            _, slot_embedded_eff = split_gemini_model_and_effort(model)
            if slot_embedded_eff:
                effort = slot_embedded_eff
        defaults[slot] = {"provider": eff_prov, "model": model, "effort": effort}
    return defaults


def _validate_byok_prefs(payload: dict[str, Any]) -> tuple[dict[str, Any], dict[str, Any] | None]:
    """Validate the optional model preferences of a key: a known model and a known effort. There is no administrator baseline (owner decision 2026-11-19)."""
    from ...provider_manager import DEFAULT_GEMINI_EMBED_MODELS, list_gemini_base_models, list_gemini_models

    allowed_gen = set(list_gemini_models()) | set(list_gemini_base_models())
    allowed_embed = set(DEFAULT_GEMINI_EMBED_MODELS)
    prefs: dict[str, Any] = {}

    if "model" in payload or "effort" in payload:
        req_model = str(payload.get("model") or "").strip()
        req_effort = str(payload.get("effort") or "").strip().lower()
        if req_model and req_model not in allowed_gen:
            return {}, {"code": "unsupported_model", "slot": "extract_graph", "model": req_model}
        if req_effort and req_effort not in EFFORT_ORDER:
            return {}, {"code": "unsupported_effort", "slot": "extract_graph", "effort": req_effort}
        _, embedded_effort = split_gemini_model_and_effort(req_model) if req_model else ("", "")
        if embedded_effort and req_effort and req_effort != embedded_effort:
            return {}, {"code": "unsupported_effort", "slot": "extract_graph", "effort": req_effort}
        eff_override = embedded_effort or req_effort
        if req_model:
            prefs["model"] = req_model
        if eff_override:
            prefs["effort"] = eff_override

    if "feature_models" in payload:
        raw_fm = payload.get("feature_models")
        if not isinstance(raw_fm, dict):
            return {}, {"code": "invalid_request", "fields": ["feature_models"]}
        fm_out: dict[str, dict[str, str]] = {}
        for slot, cfg in raw_fm.items():
            if slot not in FEATURE_SLOTS or slot in ADMIN_ONLY_FEATURES:
                return {}, {"code": "unsupported_feature", "slot": str(slot)}
            if not isinstance(cfg, dict):
                return {}, {"code": "invalid_request", "fields": [f"feature_models.{slot}"]}
            m = str(cfg.get("model") or "").strip()
            e = str(cfg.get("effort") or "").strip().lower()
            allowed = allowed_embed if slot == "embed" else allowed_gen
            if m and m not in allowed:
                return {}, {"code": "unsupported_model", "slot": slot, "model": m}
            if e and e not in EFFORT_ORDER:
                return {}, {"code": "unsupported_effort", "slot": slot, "effort": e}
            embedded_slot_eff = ""
            if m and slot != "embed":
                _, embedded_slot_eff = split_gemini_model_and_effort(m)
                if embedded_slot_eff and e and e != embedded_slot_eff:
                    return {}, {"code": "unsupported_effort", "slot": slot, "effort": e}
            if not m and not e:
                continue
            fm_out[slot] = {"provider": "gemini", "model": m, "effort": "" if slot == "embed" else (embedded_slot_eff or e)}
        if fm_out:
            prefs["feature_models"] = fm_out

    return prefs, None


def list_meta(ctx: AccountsContext, account_id: int) -> list[dict[str, Any]]:
    auth = open_auth_db(ctx)
    try:
        return [_meta_dict(m) for m in provider_keys.get_key_meta(auth, account_id=account_id)]
    finally:
        auth.close()


def get_member_prefs(ctx: AccountsContext, account_id: int, provider: str = "gemini") -> dict[str, Any]:
    auth = open_auth_db(ctx)
    try:
        return provider_keys.get_key_model_prefs(auth, account_id=account_id, provider=provider)
    finally:
        auth.close()


def store_key(
    ctx: AccountsContext,
    account_id: int,
    provider: str,
    api_key: str,
    *,
    user_seal: str | None = None,
    model_prefs: dict[str, Any] | None = None,
) -> dict[str, Any]:
    auth = open_auth_db(ctx)
    try:
        meta = provider_keys.put_key(
            auth,
            account_id=account_id,
            provider=provider,
            api_key=api_key,
            status="active",
            user_seal=user_seal,
            model_prefs=model_prefs,
        )
        auth_db.record_admin_audit(
            auth,
            actor_account_id=account_id,
            action="account.provider_key_put",
            target_account_id=account_id,
            detail={"provider": provider, "has_model_prefs": bool(model_prefs)},
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


# --- 키 메타 목록 마크업 (/settings/keys) --------------------------------------------------------------


def _meta_rows(items: list[dict[str, Any]]) -> str:
    if not items:
        return f'<p class="hint" data-provider-key-empty>{escape(t("console.settings.keys.meta_empty"))}</p>'
    parts = []
    for item in items:
        rows = [
            (t("console.settings.keys.provider"), PROVIDER_TEXT.get(item["provider"], item["provider"])),
            ("fingerprint", item["fingerprint"]),
            (t("console.common.status"), t(f"console.settings.keys.status.{item['status']}") if item["status"] in KEY_STATUSES else item["status"]),
            (t("console.settings.keys.field.created"), format_ts(item["created_at"])),
            (t("console.settings.keys.field.last_verified"), format_ts(item["last_verified_at"])),
            (t("console.settings.keys.field.last_used"), format_ts(item["last_used_at"])),
        ]
        body = "".join(f"<dt>{escape(k)}</dt><dd>{escape(v)}</dd>" for k, v in rows)
        parts.append(f'<dl data-provider-key="{escape(item["provider"])}">{body}</dl>')
    return "".join(parts)


# --- 라우트 ------------------------------------------------------------------------


def routes(ctx: AccountsContext) -> list[Route]:
    async def _gate(request: Request) -> tuple[Principal, Response | None]:
        principal = request_principal(request)
        if _is_owner_admin(principal):
            return principal, None
        if not _is_member(principal) or not await asyncio.to_thread(is_network_mode, ctx):
            return principal, not_found()
        return principal, None

    async def keys_get(request: Request) -> Response:
        principal, denied = await _gate(request)
        if denied is not None:
            return denied
        assert principal.account_id is not None
        items = await asyncio.to_thread(list_meta, ctx, principal.account_id)
        payload: dict[str, Any] = {"keys": items}
        if str(request.query_params.get("include_policy") or "").lower() in ("1", "true", "yes"):
            from ...provider_manager import DEFAULT_GEMINI_EMBED_MODELS, list_gemini_models

            defaults = await asyncio.to_thread(_admin_defaults, ctx)
            prefs = await asyncio.to_thread(get_member_prefs, ctx, principal.account_id)
            payload["model_policy"] = {
                "admin_defaults": defaults,
                "allowed_models": list_gemini_models(),
                "allowed_embed_models": list(DEFAULT_GEMINI_EMBED_MODELS),
                "effort_order": list(EFFORT_ORDER),
                "model_prefs": prefs,
                "split_key_enabled": True,
                "has_cookie_seal": bool(request.cookies.get(BYOK_SEAL_COOKIE)),
            }
        return JSONResponse(payload, headers={"Cache-Control": "no-store"})

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
        allowed_keys = {"provider", "model", "effort", "feature_models"}
        if "provider" not in body or (set(body) - allowed_keys) or raw_key is None:
            return json_error(400, "invalid_request", fields=["provider", "api_key"])
        provider = body["provider"]
        if not isinstance(provider, str) or provider not in MEMBER_KEY_PROVIDERS:
            return json_error(400, "unsupported_provider", providers=list(MEMBER_KEY_PROVIDERS))
        prefs, pref_err = _validate_byok_prefs(body)
        if pref_err is not None:
            err_code = str(pref_err.pop("code", "invalid_request"))
            return json_error(400, err_code, **pref_err)
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
        seal = request.cookies.get(BYOK_SEAL_COOKIE) or generate_user_seal()
        meta = await asyncio.to_thread(
            store_key,
            ctx,
            principal.account_id,
            provider,
            api_key,
            user_seal=seal,
            model_prefs=prefs,
        )
        res_body: dict[str, Any] = {"key": meta}
        if prefs:
            res_body["model_prefs"] = prefs
        resp = JSONResponse(res_body, headers={"Cache-Control": "no-store"})
        resp.set_cookie(
            BYOK_SEAL_COOKIE,
            seal,
            httponly=True,
            samesite="lax",
            secure=bool(ctx.config.secure_cookie),
            path="/",
        )
        return resp

    async def keys_verify(request: Request) -> Response:
        principal, denied = await _gate(request)
        if denied is not None:
            return denied
        assert principal.account_id is not None
        seal_cookie = request.cookies.get(BYOK_SEAL_COOKIE)
        if seal_cookie:
            register_volatile_user_seal(principal.account_id, seal_cookie)
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
