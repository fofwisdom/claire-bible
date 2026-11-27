"""The page ``/support/pipeline`` and the shared Gemini Neon pipeline data serializer (``/about`` + ``/support/pipeline``).

Each account sees the AI of its own jobs (``accounts.provider_view.describe_account_providers``). The administrator sees the AI of the server jobs (``describe_server_providers``), the Jev shadow evaluation, the pair review pre-label model, the model lineage of recent documents of all accounts, and the removed fallbacks. Public ``/about`` sees generic slot labels only. A member or public page has no document title of another account, no ``/admin/`` link, no ``jev-latest``, no ``records`` tab, and no ``gemini-decision-1`` row.
"""

from __future__ import annotations

import json
from collections.abc import Sequence
from html import escape
from typing import Any, Final, Literal

from ...accounts.languages import ALWAYS_LANGUAGE, read_server_languages
from ...accounts.provider_policy import split_gemini_model_and_effort
from ...i18n import get_locale, interpolate, t
from ...i18n.catalog import load_catalog
from ...i18n.locales import DEFAULT_LOCALE, LocaleCode
from .console_ui import console_card, console_kv, console_notice, console_pill, console_table, console_tabs

ViewerRole = Literal["public", "member", "admin"]

STT_ORDER_ID: Final = "pipeline-stt-order"
_RECENT_LIMIT: Final = 10
_BYOK_VERIFY_MODEL: Final = "gemini-3.1-flash-lite"
_PIPELINE_SLOTS: Final[tuple[str, ...]] = (
    "classify",
    "extract_graph",
    "summarize",
    "render_detail",
    "entity_judge",
    "relation_judge",
    "research",
)


def _k(key: str, **params: Any) -> str:
    return t(f"console.support.pipeline.{key}", **params)


def _code(text: str) -> str:
    return f'<code class="pref-mono">{escape(text)}</code>'


def _clean_vlm_model(media: dict[str, Any] | None) -> str:
    """Return the real VLM API model identifier with any embedded effort suffix stripped."""
    raw = str((media or {}).get("model") or "").strip()
    if not raw:
        return "-"
    api_model, _ = split_gemini_model_and_effort(raw)
    return api_model or raw


def _media_html(media: dict[str, Any], *, strip_effort: bool = False) -> str:
    """STT or VLM: the model, or the reason why it does not work (server view: off or not ready; account view: the reason of the account)."""
    model_val = _clean_vlm_model(media) if strip_effort else str(media.get("model") or "-")
    if "available" in media:
        return _code(model_val) if media["available"] else console_pill(t(f"console.settings.providers.reason.{media['reason']}"), tone="neutral")
    if not media.get("enabled"):
        return console_pill(_k("off"), tone="neutral")
    if not media.get("ready"):
        return console_pill(t("console.settings.providers.reason.not_ready"), tone="warn")
    return _code(model_val)


def _svg_slot_label(row: dict[str, Any] | None) -> str:
    if row is None:
        return "-"
    if row.get("available") is False:
        return t(f"console.settings.providers.reason.{row['reason']}")
    model = str(row.get("model") or "")
    effort = str(row.get("effort") or "")
    if effort and not model.endswith(f"-{effort}"):
        model = f"{model} ({effort})" if model else effort
    return model or "-"


def _svg_media_label(media: dict[str, Any], *, strip_effort: bool = False) -> str:
    model_val = _clean_vlm_model(media) if strip_effort else str(media.get("model") or "-")
    if "available" in media:
        return model_val if media["available"] else t(f"console.settings.providers.reason.{media['reason']}")
    if not media.get("enabled"):
        return _k("off")
    if not media.get("ready"):
        return t("console.settings.providers.reason.not_ready")
    return model_val


def _localized_language_name(tag: str, *, locale: LocaleCode) -> str:
    """Map a BCP-47 tag (e.g. ``ko``, ``zh-hans``) to its localized display name."""
    cleaned = str(tag or "").strip().lower() or ALWAYS_LANGUAGE
    norm = cleaned.replace("-", "_")
    full_key = f"lang.name.{norm}"
    translated = t(full_key, locale=locale)
    if translated != full_key:
        return translated
    primary_sub = norm.split("_", 1)[0]
    sub_key = f"lang.name.{primary_sub}"
    sub_translated = t(sub_key, locale=locale)
    if sub_translated != sub_key:
        return sub_translated
    return cleaned.upper()


def resolve_primary_language_info(
    settings: Any,
    *,
    viewer_lang_order: Sequence[str] | None = None,
    locale: LocaleCode | None = None,
) -> tuple[str, list[str], str]:
    """Resolve ``(primary_tag, lang_order, primary_lang_name)`` using server/viewer preferred languages."""
    active_locale = locale if locale is not None else get_locale()
    order: list[str] = [str(code).strip().lower() for code in (viewer_lang_order or ()) if str(code).strip()]
    if not order and settings is not None:
        db_file = getattr(settings, "db_file", None)
        if db_file is not None:
            order = [str(code).strip().lower() for code in read_server_languages(db_file) if str(code).strip()]
        if not order:
            order = [str(code).strip().lower() for code in getattr(settings, "effective_preferred_languages", ()) or () if str(code).strip()]
    if not order:
        order = [ALWAYS_LANGUAGE]
    primary_tag = order[0]
    return primary_tag, order, _localized_language_name(primary_tag, locale=active_locale)


def _neon_i18n_map(*, locale: LocaleCode | None = None) -> dict[str, str]:
    """Collect all ``pipeline.neon.*`` strings for the active locale so the shared JS bundle works without extra fetches."""
    active = locale if locale is not None else get_locale()
    merged = {**load_catalog(DEFAULT_LOCALE), **load_catalog(active)}
    return {key: value for key, value in sorted(merged.items()) if key.startswith("pipeline.neon.")}


def build_pipeline_view_data(
    *,
    viewer: ViewerRole,
    settings: Any = None,
    view: dict[str, Any] | None = None,
    lang_order: Sequence[str] | None = None,
    locale: LocaleCode | None = None,
) -> dict[str, Any]:
    """Serialize sanitized Gemini Neon pipeline configuration and i18n copy for ``public``, ``member``, or ``admin`` viewers."""
    if viewer not in ("public", "member", "admin"):
        raise ValueError(f"unsupported pipeline viewer role: {viewer!r}")
    active_locale = locale if locale is not None else get_locale()
    stt_order = ((view or {}).get("stt") or {}).get("order") or {}
    explicit_order = lang_order if lang_order is not None else stt_order.get("caption_languages")
    _, resolved_order, primary_lang_name = resolve_primary_language_info(settings, viewer_lang_order=explicit_order, locale=active_locale)
    auto_merge = round(float(getattr(settings, "sim_tier_auto_merge", 0.93) or 0.93), 2)
    borderline = round(float(getattr(settings, "sim_tier_borderline", 0.72) or 0.72), 2)
    client_llm = t("pipeline.neon.model_client_llm", locale=active_locale)

    if viewer == "public":
        source_label = t("pipeline.neon.source_public", locale=active_locale)
        models: dict[str, str] = {
            slot: t("pipeline.neon.model_slot", locale=active_locale, slot=t(f"console.settings.providers.slot.{slot}", locale=active_locale))
            for slot in _PIPELINE_SLOTS
        }
        models["embed"] = t("pipeline.neon.model_public_embed", locale=active_locale)
        models["stt"] = t("pipeline.neon.model_public_stt", locale=active_locale)
        models["vlm"] = t("pipeline.neon.model_public_vlm", locale=active_locale)
        models["client_llm"] = client_llm
        models["jev"] = ""
    else:
        slots = {str(row["slot"]): row for row in (view or {}).get("slots") or []}
        source_code = str((view or {}).get("source") or ("server" if viewer == "admin" else "tier"))
        if viewer == "admin":
            source_label = t("pipeline.neon.source_admin", locale=active_locale)
        else:
            source_label = t("pipeline.neon.source_member", locale=active_locale, source=t(f"console.settings.providers.source.{source_code}", locale=active_locale))
        models = {slot: _svg_slot_label(slots.get(slot)) for slot in _PIPELINE_SLOTS}
        models["embed"] = str((view or {}).get("embed_model") or "-")
        models["stt"] = _svg_media_label((view or {}).get("stt") or {})
        models["vlm"] = _svg_media_label((view or {}).get("vlm") or {}, strip_effort=True)
        models["client_llm"] = client_llm
        if viewer == "admin":
            jev_model = str(getattr(settings, "jev_model", "") or "jev-latest")
            jev_state = _k("on" if (view or {}).get("jev_enabled") else "off")
            models["jev"] = f"{jev_model} · {jev_state}"
        else:
            models["jev"] = ""

    return {
        "viewer": viewer,
        "sourceLabel": source_label,
        "primaryLangName": primary_lang_name,
        "langOrder": resolved_order,
        "autoMerge": auto_merge,
        "borderline": borderline,
        "showJev": viewer == "admin",
        "showSimulators": True,
        "models": models,
        "i18n": _neon_i18n_map(locale=active_locale),
    }


def render_pipeline_mount_html(data: dict[str, Any], *, json_id: str = "claire-pipeline-data") -> str:
    """Render the shared Gemini Neon mount element and its escaped inline JSON payload."""
    payload = json.dumps(data, ensure_ascii=False, sort_keys=True).replace("<", "\\u003c").replace(">", "\\u003e").replace("&", "\\u0026")
    safe_id = escape(json_id)
    return (
        f'<div class="claire-pipeline-mount" data-pipeline-mount data-pipeline-json-id="{safe_id}"></div>'
        f'<script id="{safe_id}" type="application/json">{payload}</script>'
    )


def _neon_hint_html(view: dict[str, Any], *, admin_viewer: bool) -> str:
    if admin_viewer:
        return escape(_k("neon_hint_admin"))
    source = str(view.get("source") or "server")
    return escape(_k("neon_hint_member", source=t(f"console.settings.providers.source.{source}")))


def _graph_card(settings: Any, view: dict[str, Any], *, admin_viewer: bool) -> str:
    """Build the top-level ``#pipeline-graph`` console card containing the shared Gemini Neon mount."""
    data = build_pipeline_view_data(
        viewer="admin" if admin_viewer else "member",
        settings=settings,
        view=view,
    )
    return console_card(
        escape(_k("neon_title")),
        render_pipeline_mount_html(data),
        id="pipeline-graph",
        icon="fork",
        hint_html=_neon_hint_html(view, admin_viewer=admin_viewer),
        split=False,
    )


def _stt_order_card(view: dict[str, Any], *, admin_viewer: bool) -> str:
    stt = view["stt"]
    order = stt.get("order") or {}
    languages = [str(code) for code in order.get("caption_languages") or []]
    chain = " → ".join(_code(code) for code in languages) or "-"
    if str(order.get("stt_language") or "auto").lower() == "auto":
        language_text = interpolate(escape(_k("stt.hints")), {"hints": ", ".join(_code(str(code)) for code in order.get("stt_hints") or []) or "-"})
    else:
        language_text = interpolate(escape(_k("stt.fixed_language")), {"language": _code(str(order.get("stt_language") or "-"))})
    stt_state = _media_html(stt)
    items = [
        ("manual", interpolate(escape(_k("stt.manual")), {"languages": chain})),
        ("automatic", interpolate(escape(_k("stt.automatic")), {"languages": chain})),
        ("stt", interpolate(escape(_k("stt.transcribe")), {"model": _code(str(stt.get("model") or "-"))}) + f" {language_text} {stt_state}"),
        ("none", escape(_k("stt.nothing"))),
    ]
    steps_html = '<ol class="console-steps" data-stt-order-list>' + "".join(f'<li data-stt-order="{key}">{html}</li>' for key, html in items) + "</ol>"
    rules = [_k("stt.rule_other_language"), _k("stt.rule_region"), _k("stt.rule_no_speech"), _k("stt.rule_download_failed"), _k("stt.rule_vlm", language=str(order.get("vlm_language") or "-"))]
    rules_html = '<ul class="console-steps">' + "".join(f"<li>{escape(rule)}</li>" for rule in rules) + "</ul>"
    if admin_viewer:
        where = interpolate(escape(_k("stt.where_admin")), {"server": f'<a href="/admin/server#server-languages">{escape(_k("stt.where_admin_server"))}</a>', "page": f'<a href="/admin/providers#prov-stt-form">{escape(_k("stt.where_admin_page"))}</a>'})
    else:
        where = interpolate(escape(_k("stt.where_member")), {"profile": f'<a href="/account/profile#settings-languages">{escape(_k("stt.where_member_profile"))}</a>'})
    body = steps_html + f'<h3>{escape(_k("stt.rules_title"))}</h3>' + rules_html + f'<p class="hint" data-stt-where>{where}</p>'
    return console_card(escape(_k("stt.title")), body, id=STT_ORDER_ID, icon="captions", hint_html=escape(_k("stt.hint")), split=False)


def _vlm_card(settings: Any, view: dict[str, Any]) -> str:
    """Build the VLM transcription details card inside Tab 2."""
    vlm = view["vlm"]
    order = (view.get("stt") or {}).get("order") or {}
    vlm_lang = str(order.get("vlm_language") or "-")
    max_images = int(getattr(settings, "vlm_max_images", 20) or 20)
    kv = console_kv(
        [
            {"label": _k("vlm.status_label"), "value_html": _media_html(vlm, strip_effort=True)},
            {"label": _k("vlm.escalation_label"), "value": _k("vlm.escalation_value", max_images=max_images)},
            {"label": _k("vlm.language_label"), "value_html": _code(vlm_lang)},
            {"label": _k("vlm.staging_label"), "value": _k("vlm.staging_value")},
        ],
        attrs={"data-vlm-info": True},
    )
    return console_card(escape(_k("vlm.title")), kv, id="pipeline-vlm", icon="image", hint_html=escape(_k("vlm.hint")), split=False)


def _fixed_card(view: dict[str, Any], *, admin_viewer: bool) -> str:
    from ...accounts.provider_view import STT_MODEL

    clean_vlm = _clean_vlm_model(view.get("vlm"))
    rows: list[tuple[str, str]] = [
        (_k("fixed.embed_input"), escape(_k("fixed.embed_input_value"))),
        (_k("fixed.embed_store"), _code("RETRIEVAL_DOCUMENT") + " · " + escape(_k("fixed.embed_store_value"))),
        (_k("fixed.embed_query"), _code("RETRIEVAL_QUERY")),
        (_k("fixed.embed_size"), _code("3072")),
        (_k("fixed.embed_model"), interpolate(escape(_k("fixed.embed_model_value")), {"model": _code(str(view.get("embed_model") or "-"))})),
        (_k("fixed.stt_model"), _code(STT_MODEL) + " · " + escape(_k("fixed.stt_model_value"))),
        (_k("fixed.vlm_model"), interpolate(escape(_k("fixed.vlm_model_value")), {"model": _code(clean_vlm)})),
        (_k("fixed.byok_model"), _code(_BYOK_VERIFY_MODEL) + " · " + escape(_k("fixed.byok_model_value"))),
    ]
    if admin_viewer:
        rows.append((_k("fixed.pair_prelabel"), _code("gemini-decision-1") + " · " + escape(_k("fixed.pair_prelabel_value"))))
        rows.append((_k("fixed.jev_input"), escape(_k("fixed.jev_input_value"))))
    body = console_table([_k("col.item"), _k("col.value")], "".join(f"<tr><td>{escape(label)}</td><td>{value}</td></tr>" for label, value in rows), tbody_id="pipeline-fixed-rows", aria_label=_k("fixed.title"))
    return console_card(escape(_k("fixed.title")), body, id="pipeline-fixed", icon="lock", hint_html=escape(_k("fixed.hint")), split=False)


def _recent_card(lineage: dict[str, Any]) -> str:
    """The model lineage of recent documents of all accounts (the administrator only)."""
    docs = [d for d in lineage.get("recent_documents") or [] if isinstance(d, dict)][:_RECENT_LIMIT]
    if docs:
        rows: list[str] = []
        for doc in docs:
            info = doc.get("lineage") if isinstance(doc.get("lineage"), dict) else {}
            finals = info.get("final_output_models") if isinstance(info.get("final_output_models"), dict) else {}
            models = "<br>".join(f"{escape(str(stage))}: {_code(str((spec or {}).get('model') or '-'))}" for stage, spec in finals.items()) or "-"
            doc_jev = info.get("jev") if isinstance(info.get("jev"), dict) else {}
            jev_text = escape(_k("recent.jev_cell", evaluated=int(doc_jev.get("evaluated") or 0), status=str(doc_jev.get("status") or "-")) if doc_jev.get("enabled") else _k("off"))
            fallbacks = len(info.get("fallbacks_triggered") or [])
            rows.append(f'<tr data-recent-doc><td>{escape(str(doc.get("title") or doc.get("id") or "-"))}</td><td>{models}</td><td>{jev_text}</td><td>{fallbacks}</td></tr>')
        table = console_table([_k("col.document"), _k("col.models"), "Jev", _k("col.fallbacks")], "".join(rows), tbody_id="pipeline-recent-rows", aria_label=_k("recent.title"))
    else:
        table = f'<p class="hint">{escape(_k("recent.empty"))}</p>'
    return console_card(escape(_k("recent.title")), table, id="pipeline-recent", icon="clock", hint_html=escape(_k("recent.hint")), split=False)


def _fallback_card(lineage: dict[str, Any]) -> str:
    """The removed silent fallbacks (``core.provider_manager.REMOVED_FALLBACK_CATALOG``, the administrator only)."""
    rows = "".join(
        f'<tr data-fallback="{escape(str(fb.get("id") or ""))}"><td>{escape(str(fb.get("id") or ""))}</td><td>{_code(str(fb.get("location") or ""))}</td><td>{escape(str(fb.get("behavior") or ""))}</td><td>{console_pill(_k("fallback." + ("removed" if fb.get("status") == "REMOVED" else "out_of_scope")), tone="neutral")}</td></tr>'
        for fb in lineage.get("fallbacks") or []
        if isinstance(fb, dict)
    )
    table = console_table(["ID", _k("col.location"), _k("col.old_behavior"), _k("col.state")], rows, tbody_id="pipeline-fallback-rows", aria_label=_k("fallback.title"))
    body = f'<details class="pref-disclosure"><summary>{escape(_k("fallback.summary"))}</summary>{table}</details>'
    return console_card(escape(_k("fallback.title")), body, id="pipeline-fallbacks", icon="shield", hint_html=escape(_k("fallback.hint")), split=False)


def support_pipeline_body(*, settings: Any, view: dict[str, Any] | None, admin_viewer: bool, lineage: dict[str, Any] | None = None) -> str:
    """The body of ``/support/pipeline``. ``view`` is the server view for the administrator and the account view for a member; ``lineage`` (``ProviderManager.get_lineage_summary``) is read only for the administrator."""
    if settings is None or view is None:
        return console_notice(escape(_k("unavailable")), tone="warn")
    flow_tab = _graph_card(settings, view, admin_viewer=admin_viewer)
    media_tab = "".join([
        _stt_order_card(view, admin_viewer=admin_viewer),
        _vlm_card(settings, view),
        _fixed_card(view, admin_viewer=admin_viewer),
    ])
    tabs: list[dict[str, str]] = [
        {"id": "flow", "label": _k("tab.flow"), "icon": "fork", "html": flow_tab},
        {"id": "media", "label": _k("tab.media"), "icon": "captions", "html": media_tab},
    ]
    if admin_viewer and lineage is not None:
        records_tab = _recent_card(lineage) + _fallback_card(lineage)
        tabs.append({"id": "records", "label": _k("tab.records"), "icon": "clock", "html": records_tab})
    return f'<div data-toc-tabs="1">{console_tabs(tabs, id="pipeline-tabs", aria_label=_k("tabs_aria"))}</div>'
