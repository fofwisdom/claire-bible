"""What applies now: the AI that each feature uses, for the server (``/admin/providers``) and for one account (``/account/providers``).

The view uses the same resolvers as the jobs (``settings_for_context``, ``extract.provider._resolve_slot_spec``, the STT and VLM fields of ``Settings``), thus the screen shows what a job does. The account view reads the key metadata and the model preferences, never the key: a page view makes no key decryption (a placeholder stands for the key in the display context).
"""

from __future__ import annotations

from dataclasses import replace
from pathlib import Path
from typing import Any, Final

from .provider_policy import (
    ADMIN_ONLY_FEATURES,
    FEATURE_SLOTS,
    MEMBER_KEY_PROVIDERS,
    ProviderContext,
    ServerTier,
    admin_context,
    byok_context,
    settings_for_context,
    tier_context,
)

# The text slots of the routing, in the order of the screen. ``embed`` has its own row.
LLM_SLOTS: Final[tuple[str, ...]] = tuple(slot for slot in FEATURE_SLOTS if slot != "embed")
# The only STT model (``extract/transcript/gemini_stt.py``). STT has no provider or model choice.
STT_MODEL: Final = "gemini-3.5-transcribe"
# Stands for the key in a display context. It is never sent to a provider.
_DISPLAY_KEY: Final = "display-only"
_USABLE_ACCOUNT_STATUSES: Final = frozenset({"active", "setup_required"})


def _slot_rows(settings: Any) -> list[dict[str, Any]]:
    from ..extract.provider import _resolve_slot_spec

    routes = getattr(settings, "feature_routing", None) or {}
    rows: list[dict[str, Any]] = []
    for slot in LLM_SLOTS:
        provider, model, effort = _resolve_slot_spec(settings, slot)
        entry = routes.get(slot) if isinstance(routes, dict) else None
        overridden = bool(isinstance(entry, dict) and (str(entry.get("model") or "").strip() or str(entry.get("provider") or "inherit").strip().lower() not in ("", "inherit")))
        rows.append({"slot": slot, "provider": provider, "model": model, "effort": effort, "overridden": overridden})
    return rows


def _media_row(settings: Any, kind: str) -> dict[str, Any]:
    """STT (``kind="stt"``) or VLM: on or off, the provider that a job uses, and the reason when it cannot work."""
    enabled = bool(getattr(settings, "enable_video_transcription" if kind == "stt" else "enable_vlm_transcription", False))
    reason = settings.provider_unavailable_reason(kind) if enabled else None
    provider = settings.effective_stt_provider if kind == "stt" else settings.effective_vlm_provider
    if kind == "stt":
        model = str(getattr(settings, "stt_model", "") or "").strip() or STT_MODEL
    else:
        model = str(getattr(settings, "vlm_model", "") or "").strip() or str(getattr(settings, "gemini_model", "") or "")
    return {"enabled": enabled, "provider": provider, "model": model, "ready": enabled and reason is None, "problem": reason}


def media_language_order(settings: Any) -> dict[str, Any]:
    """The language order of a video (``ingest/fetchers/video.py``) for the preferred languages of ``settings``: publisher captions, then automatic captions, each in the order of ``caption_languages``; STT only when no caption is usable, with the hints ``stt_hints`` (``stt_language`` ``auto``: the preferred languages; one tag: that tag). VLM writes in ``vlm_language``."""
    from ..extract.transcript.gemini_stt import AUTO_LANGUAGE, stt_language_codes

    languages = list(settings.effective_preferred_languages)
    stt_language = str(getattr(settings, "stt_language", "") or "").strip() or AUTO_LANGUAGE
    return {
        "caption_languages": languages,
        "stt_language": stt_language,
        "stt_hints": stt_language_codes(stt_language, languages),
        "vlm_language": str(settings.effective_vlm_language),
    }


def describe_server_providers(settings: Any) -> dict[str, Any]:
    """The AI of the server jobs and of the administrator account without a personal key (``providers.json``)."""
    configured = str(getattr(settings, "provider", "") or "").strip().lower()
    effective = str(settings.effective_provider)
    key_set = bool(getattr(settings, "gemini_api_key", ""))
    problems: list[str] = []
    if configured == "gemini" and not key_set:
        problems.append("no_api_key")
    if effective == "mock":
        problems.append("mock_provider")
    stt = _media_row(settings, "stt")
    stt["language"] = str(getattr(settings, "stt_language", "") or "")
    stt["order"] = media_language_order(settings)
    if stt["enabled"] and not stt["ready"]:
        problems.append("stt_not_ready")
    vlm = _media_row(settings, "vlm")
    return {
        "main_provider": configured,
        "effective_provider": effective,
        "api_key_set": key_set,
        "model": str(getattr(settings, "gemini_model", "") or ""),
        "slots": _slot_rows(settings),
        "embed_model": str(getattr(settings, "gemini_embed_model", "") or ""),
        "stt": stt,
        "vlm": vlm,
        "jev_enabled": bool(getattr(settings, "enable_jev", False)),
        "is_production": bool(getattr(settings, "is_production", False)),
        "problems": problems,
    }


def _account_row(db_path: Path, account_id: int) -> tuple[str, str, ServerTier, tuple[str, ...]]:
    from ..store.db import connect_existing
    from . import languages as _languages
    from . import server_tier as _server_tier

    conn = connect_existing(db_path, readonly=True)
    try:
        row = conn.execute("SELECT role, status FROM accounts WHERE id=?", (int(account_id),)).fetchone()
        if row is None:
            raise LookupError(f"account {account_id} not found")
        role, status = str(row[0]), str(row[1])
        tier = ServerTier() if role == "admin" else _server_tier.load_server_tier(conn)
        return role, status, tier, _languages.account_languages(conn, int(account_id))
    finally:
        conn.close()


def _key_state(auth_db_path: Path, account_id: int) -> tuple[Any | None, dict[str, Any]]:
    """The metadata of the registered key (or None) and its model preferences, without a decryption."""
    from ..store import auth_db as _auth_db
    from ..store import provider_keys as _provider_keys

    if not auth_db_path.is_file():
        return None, {}
    conn = _auth_db.connect_auth_db(auth_db_path)
    try:
        metas = [m for m in _provider_keys.get_key_meta(conn, account_id=int(account_id)) if m.provider in MEMBER_KEY_PROVIDERS]
        meta = metas[0] if metas else None
        prefs = _provider_keys.get_key_model_prefs(conn, account_id=int(account_id), provider=meta.provider) if meta else {}
        return meta, prefs
    finally:
        conn.close()


def describe_account_providers(
    settings: Any,
    *,
    account_id: int,
    db_path: Path | str | None = None,
    auth_db_path: Path | str | None = None,
    kb_embed_model: str | None = None,
) -> dict[str, Any]:
    """The AI that the jobs of one account use, where each value comes from, and the limits that apply to the account.

    ``source``: ``byok`` (the key of the account), ``key_problem`` (a registered key that is not active: the jobs fail and do not change to the server tier, C2), ``server`` (the administrator without a personal key: the server providers), ``tier`` (the server tier with the server key), ``none`` (no API), ``blocked`` (the account status stops all jobs).
    """
    from ..ingest.extraction_mode import available_extraction_modes
    from ..store import auth_db as _auth_db

    db_file = Path(db_path) if db_path is not None else Path(settings.db_file)
    auth_file = Path(auth_db_path) if auth_db_path is not None else _auth_db.auth_db_path(settings.data_dir)
    role, status, tier, account_langs = _account_row(db_file, account_id)
    server_admin = role == "admin"
    meta, prefs = _key_state(auth_file, account_id)
    key: dict[str, Any] | None = None
    if meta is not None:
        key = {"provider": meta.provider, "fingerprint": meta.fingerprint, "status": meta.status, "last_verified_at": meta.last_verified_at, "model": str(prefs.get("model") or "")}

    ctx: ProviderContext
    if status not in _USABLE_ACCOUNT_STATUSES:
        source = "blocked"
        ctx = tier_context(int(account_id), settings=settings, tier=ServerTier())
    elif meta is not None and meta.status == "active":
        source = "byok"
        ctx = byok_context(int(account_id), settings=settings, auth_db_path=auth_file, provider=meta.provider, fingerprint=meta.fingerprint, api_key=_DISPLAY_KEY, tier=tier, server_admin=server_admin)
    elif meta is not None:
        source = "key_problem"
        ctx = byok_context(int(account_id), settings=settings, auth_db_path=auth_file, provider=meta.provider, fingerprint=meta.fingerprint, api_key=_DISPLAY_KEY, tier=tier, server_admin=server_admin)
    elif server_admin:
        source = "server"
        ctx = replace(admin_context(settings), account_id=int(account_id))
    else:
        ctx = tier_context(int(account_id), settings=settings, tier=tier)
        source = "tier" if ctx.source == "tier" else "none"

    msettings = settings_for_context(settings, replace(ctx, languages=account_langs))
    has_api = source in ("byok", "key_problem", "server", "tier")
    slots: list[dict[str, Any]] = []
    for row in _slot_rows(msettings):
        if row["slot"] in ADMIN_ONLY_FEATURES and not (server_admin or ctx.is_admin):
            row = {**row, "available": False, "reason": "admin_only"}
        elif not has_api:
            row = {**row, "available": False, "reason": "no_api" if source == "none" else "blocked"}
        else:
            row = {**row, "available": True, "reason": None, "from": _slot_origin(source, row["slot"], prefs)}
        slots.append(row)

    def media(kind: str) -> dict[str, Any]:
        server_on = bool(getattr(settings, "enable_video_transcription" if kind == "stt" else "enable_vlm_transcription", False))
        row = _media_row(msettings, kind) if has_api else {"enabled": False, "provider": "unavailable", "model": "", "ready": False, "problem": None}
        if not server_on:
            reason = "server_off"
        elif not has_api:
            reason = "no_api" if source == "none" else "blocked"
        elif not ctx.feature_allowed(kind):
            reason = "tier_feature_off"
        elif not row["ready"]:
            reason = "not_ready"
        else:
            reason = None
        if kind == "stt" and server_on:
            row["model"] = str(getattr(settings, "stt_model", "") or "").strip() or STT_MODEL
        if kind == "stt":
            row["order"] = media_language_order(msettings)
        return {**row, "available": reason is None, "reason": reason}

    features = {name: (has_api and ctx.feature_allowed(name)) for name in ("expand", "presentation", "synthesize")}
    limits = _limits(source, tier, server_admin)
    return {
        "source": source,
        "role": role,
        "key": key,
        "slots": slots,
        "embed_model": (kb_embed_model or "").strip() or str(getattr(msettings, "gemini_embed_model", "") or ""),
        "stt": media("stt"),
        "vlm": media("vlm"),
        "features": features,
        "extraction_modes": available_extraction_modes(msettings, ctx=ctx, provider_error=(RuntimeError("key not active") if source == "key_problem" else None)),
        "tier": {"provider": tier.provider, "model": tier.model, "max_effort": tier.max_effort},
        "limits": limits,
    }


def _slot_origin(source: str, slot: str, prefs: dict[str, Any]) -> str:
    """Where the model of a slot comes from: ``key_slot`` (a model of the key for this feature), ``key_model`` (the model of the key), ``server_default`` (the server providers), ``tier`` (the server tier)."""
    if source in ("byok", "key_problem"):
        fm = prefs.get("feature_models") if isinstance(prefs.get("feature_models"), dict) else {}
        if isinstance(fm.get(slot), dict) and fm[slot]:
            return "key_slot"
        return "key_model" if prefs.get("model") else "server_default"
    return "tier" if source == "tier" else "server_default"


def _limits(source: str, tier: ServerTier, server_admin: bool) -> dict[str, Any]:
    """The server tier limits that apply to the account (``accounts.server_tier``). The administrator account has none; ``monthly_tokens`` applies only to the server tier; the concurrency cap only to a key."""
    if server_admin:
        return {"exempt": True}
    return {
        "exempt": False,
        "daily_ingests": tier.daily_ingests,
        "max_documents": tier.max_documents,
        "storage_mb": tier.storage_mb,
        "monthly_tokens": tier.monthly_tokens if source == "tier" else None,
        "max_concurrency": tier.byok_max_concurrency if source in ("byok", "key_problem") else None,
    }
