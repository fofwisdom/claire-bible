"""The page ``/support/pipeline``: the steps of one document from the input to the knowledge graph, the AI of each step, the language order of a video, and the fixed values (the model lineage diagram, corrected, for each account).

Each account sees the AI of its own jobs (``accounts.provider_view.describe_account_providers``). The administrator sees the AI of the server jobs (``describe_server_providers``), the Jev shadow evaluation, the model lineage of recent documents of all accounts, and the removed fallbacks. A member page has no document title of another account and no ``/admin/`` link.

The order of the steps is the order of ``ingest/pipeline.extract_resolve_store`` (``docs/implementation/EXTRACTION.md`` §1); the order of a video is the order of ``ingest/fetchers/video.fetch_video`` (``INGESTION.md`` §9).
"""

from __future__ import annotations

from html import escape
from typing import Any, Final

from ...i18n import interpolate, t
from .console_ui import console_card, console_flow, console_kv, console_notice, console_pill, console_table

STT_ORDER_ID: Final = "pipeline-stt-order"
# The step of a flow in an ingest with the agent client (``ingest/client_extraction.py``): ``client`` = the agent client does it, ``api_only`` = only an ingest with the API does it, None = the server does it in the two ingests.
_CLIENT_ROLE: Final[dict[str, str | None]] = {
    "input": None,
    "check": None,
    "effort": "api_only",
    "extract": "client",
    "summary": "client",
    "detail": "client",
    "resolve": None,
    "record": None,
    "relations": None,
    "links": "api_only",
    "lineage": "api_only",
}
_RECENT_LIMIT: Final = 10


def _k(key: str, **params: Any) -> str:
    return t(f"console.support.pipeline.{key}", **params)


def _code(text: str) -> str:
    return f'<code class="pref-mono">{escape(text)}</code>'


def _model_html(row: dict[str, Any] | None) -> str:
    """The model of a slot row of the provider view, or the reason why the slot does not work for the account."""
    if row is None:
        return "-"
    if row.get("available") is False:
        return console_pill(t(f"console.settings.providers.reason.{row['reason']}"), tone="neutral")
    model = str(row.get("model") or "")
    effort = str(row.get("effort") or "")
    if effort and not model.endswith(f"-{effort}"):
        model = f"{model} ({effort})" if model else effort
    return _code(model or "-")


def _media_html(media: dict[str, Any]) -> str:
    """STT or VLM: the model, or the reason why it does not work (server view: off or not ready; account view: the reason of the account)."""
    if "available" in media:
        return _code(str(media.get("model") or "-")) if media["available"] else console_pill(t(f"console.settings.providers.reason.{media['reason']}"), tone="neutral")
    if not media.get("enabled"):
        return console_pill(_k("off"), tone="neutral")
    if not media.get("ready"):
        return console_pill(t("console.settings.providers.reason.not_ready"), tone="warn")
    return _code(str(media.get("model") or "-"))


def _models(rows: list[tuple[str, str]]) -> str:
    return console_kv([{"label": label, "value_html": value_html} for label, value_html in rows], attrs={"data-flow-models": True})


def _slot_label(slot: str) -> str:
    return t(f"console.settings.providers.slot.{slot}")


def _same_spec(a: dict[str, Any] | None, b: dict[str, Any] | None) -> bool:
    return bool(a and b and (a.get("provider"), a.get("model"), a.get("effort")) == (b.get("provider"), b.get("model"), b.get("effort")))


def _flow_card(settings: Any, view: dict[str, Any], *, admin_viewer: bool) -> str:
    from ...extract.resolver import MAX_JUDGE

    slots = {str(row["slot"]): row for row in view["slots"]}
    auto = f"{float(getattr(settings, 'sim_tier_auto_merge', 0.93)):.2f}"
    low = f"{float(getattr(settings, 'sim_tier_borderline', 0.72)):.2f}"
    embed_html = _code(str(view.get("embed_model") or "-"))
    summary_marks: list[str] = []
    has_text_ai = all(row.get("available", True) for row in (slots.get("extract_graph"), slots.get("summarize")) if row)
    if has_text_ai:
        runs = not _same_spec(slots.get("extract_graph"), slots.get("summarize"))
        summary_marks.append(console_pill(_k("summary_runs" if runs else "summary_skipped"), tone="ok" if runs else "neutral", attrs={"data-summary-state": "runs" if runs else "skipped"}))
    gates = "".join(
        f'<li data-flow-gate="{n}">{text}</li>'
        for n, text in (
            (1, escape(_k("gate1"))),
            (2, escape(_k("gate2"))),
            (3, escape(_k("gate3", auto=auto))),
            (4, escape(_k("gate4", low=low, auto=auto, max=MAX_JUDGE)) + " " + console_pill(_k("mark.api_only"), tone="neutral")),
            (5, escape(_k("gate5"))),
        )
    )
    resolve_body = f'<p class="hint">{escape(_k("step.resolve.desc"))}</p><ol class="console-flow-gates">{gates}</ol>'
    resolve_side = ""
    if admin_viewer:
        jev_on = bool(view.get("jev_enabled"))
        resolve_side = escape(_k("jev_side")) + " " + console_pill(_k("on" if jev_on else "off"), tone="ok" if jev_on else "neutral", attrs={"data-jev-state": "on" if jev_on else "off"})
    links_on = bool(getattr(settings, "enable_relation_linking", False))
    steps: list[dict[str, Any]] = [
        {
            "key": "input",
            "body_html": f'<p class="hint">{escape(_k("step.input.desc"))} <a href="#{STT_ORDER_ID}">{escape(_k("step.input.stt_link"))}</a></p>'
            + _models([(t("console.settings.providers.feature.stt"), _media_html(view["stt"])), (t("console.settings.providers.feature.vlm"), _media_html(view["vlm"]))]),
        },
        {"key": "check", "ai": False},
        {
            "key": "effort",
            "desc_params": {
                "classifier": str(getattr(settings, "pdf_classifier_effort", "") or "-"),
                "threshold": f"{int(getattr(settings, 'pdf_paper_threshold_chars', 15000)):,}",
                "paper": str(getattr(settings, "pdf_paper_effort", "") or "-"),
            },
            "models": [(_slot_label("classify"), _model_html(slots.get("classify")))],
        },
        {"key": "extract", "models": [(_slot_label("extract_graph"), _model_html(slots.get("extract_graph")))]},
        {"key": "summary", "models": [(_slot_label("summarize"), _model_html(slots.get("summarize")))], "marks": summary_marks},
        {"key": "detail", "models": [(_slot_label("render_detail"), _model_html(slots.get("render_detail")))]},
        {
            "key": "resolve",
            "body_html": resolve_body + _models([(t("console.settings.providers.feature.embed"), embed_html), (_slot_label("entity_judge"), _model_html(slots.get("entity_judge")))]),
            "side_html": resolve_side,
        },
        {"key": "record", "ai": False},
        {"key": "relations", "ai": False},
        {
            "key": "links",
            "desc_params": {"per_doc": int(getattr(settings, "max_relation_judges_per_doc", 0) or 0)},
            "models": [(_slot_label("relation_judge"), _model_html(slots.get("relation_judge")))],
            "marks": [] if links_on else [console_pill(_k("off"), tone="neutral", attrs={"data-links-state": "off"})],
        },
        {"key": "lineage", "ai": False},
    ]
    flow_steps: list[dict[str, Any]] = []
    for step in steps:
        key = str(step["key"])
        marks: list[str] = list(step.get("marks", []))
        role = _CLIENT_ROLE[key]
        if role:
            marks.append(console_pill(_k(f"mark.{role}"), tone="neutral", attrs={"data-client-role": role}))
        if step.get("ai") is False:
            marks.append(console_pill(_k("mark.no_ai"), tone="neutral"))
        body = step.get("body_html")
        if body is None:
            body = f'<p class="hint">{escape(_k(f"step.{key}.desc", **step.get("desc_params", {})))}</p>'
            if step.get("models"):
                body += _models(step["models"])
        flow_steps.append({"key": key, "title": _k(f"step.{key}.title"), "marks_html": " ".join(marks), "body_html": body, "side_html": step.get("side_html", ""), "kind": "rule" if step.get("ai") is False else "ai"})

    source = str(view.get("source") or "server")
    if admin_viewer:
        intro = escape(_k("flow_hint_admin"))
    else:
        intro = escape(_k("flow_hint_member", source=t(f"console.settings.providers.source.{source}")))
    legend = f'<p class="hint" data-flow-legend>{escape(_k("legend"))}</p>'
    other = console_kv(
        [
            (_k("other.query"), _k("other.query_value")),
            (_slot_label("research"), _k("other.research_value")),
        ],
        attrs={"data-flow-other": True},
    )
    body = legend + console_flow(flow_steps, aria_label=_k("flow_title")) + f'<h3>{escape(_k("other.title"))}</h3>' + other
    return console_card(escape(_k("flow_title")), body, id="pipeline-flow", icon="fork", hint_html=intro, split=False)


def _stt_order_card(view: dict[str, Any], *, admin_viewer: bool) -> str:
    stt = view["stt"]
    order = stt.get("order") or {}
    languages = [str(code) for code in order.get("caption_languages") or []]
    chain = " → ".join(_code(code) for code in languages) or "-"
    if order.get("stt_detects_language"):
        language_text = escape(_k("stt.detect"))
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
        where = interpolate(escape(_k("stt.where_admin")), {"variable": _code("CLAIRE_PREFERRED_LANGUAGES"), "page": f'<a href="/admin/providers#prov-stt-form">{escape(_k("stt.where_admin_page"))}</a>'})
    else:
        where = escape(_k("stt.where_member"))
    body = steps_html + f'<h3>{escape(_k("stt.rules_title"))}</h3>' + rules_html + f'<p class="hint" data-stt-where>{where}</p>'
    return console_card(escape(_k("stt.title")), body, id=STT_ORDER_ID, icon="captions", hint_html=escape(_k("stt.hint")), split=False)


def _fixed_card(view: dict[str, Any], *, admin_viewer: bool) -> str:
    from ...accounts.provider_view import STT_MODEL

    rows: list[tuple[str, str]] = [
        (_k("fixed.embed_input"), escape(_k("fixed.embed_input_value"))),
        (_k("fixed.embed_store"), _code("RETRIEVAL_DOCUMENT") + " · " + escape(_k("fixed.embed_store_value"))),
        (_k("fixed.embed_query"), _code("RETRIEVAL_QUERY")),
        (_k("fixed.embed_size"), _code("3072")),
        (_k("fixed.embed_model"), interpolate(escape(_k("fixed.embed_model_value")), {"model": _code(str(view.get("embed_model") or "-"))})),
        (_k("fixed.stt_model"), _code(STT_MODEL) + " · " + escape(_k("fixed.stt_model_value"))),
    ]
    if admin_viewer:
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
    body = table
    return console_card(escape(_k("recent.title")), body, id="pipeline-recent", icon="clock", hint_html=escape(_k("recent.hint")), split=False)


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
    parts = [_flow_card(settings, view, admin_viewer=admin_viewer), _stt_order_card(view, admin_viewer=admin_viewer), _fixed_card(view, admin_viewer=admin_viewer)]
    if admin_viewer and lineage is not None:
        parts += [_recent_card(lineage), _fallback_card(lineage)]
    return "".join(parts)
