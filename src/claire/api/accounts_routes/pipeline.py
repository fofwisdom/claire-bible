"""The page ``/support/pipeline``: the tabs, SVG flow graph, server API flow, agent client flow, auxiliary AI uses, video/media rules, fixed values, and administrator records.

Each account sees the AI of its own jobs (``accounts.provider_view.describe_account_providers``). The administrator sees the AI of the server jobs (``describe_server_providers``), the Jev shadow evaluation, the pair review pre-label model, the model lineage of recent documents of all accounts, and the removed fallbacks. A member page has no document title of another account, no ``/admin/`` link, no ``data-jev-state``, no ``records`` tab, and no ``gemini-decision-1`` row.
"""

from __future__ import annotations

from html import escape
from typing import Any, Final

from ...accounts.provider_policy import is_gemini_fixed_effort_model, split_gemini_model_and_effort
from ...i18n import interpolate, t
from .console_ui import console_card, console_flow, console_kv, console_notice, console_pill, console_table, console_tabs

STT_ORDER_ID: Final = "pipeline-stt-order"
_RECENT_LIMIT: Final = 10
_BYOK_VERIFY_MODEL: Final = "gemini-3.1-flash-lite"


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


def _svg_clip(text: str, max_len: int = 26) -> str:
    clean = str(text or "-").strip() or "-"
    if len(clean) <= max_len:
        return clean
    return clean[: max_len - 1] + "…"


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


def _models(rows: list[tuple[str, str]]) -> str:
    return console_kv([{"label": label, "value_html": value_html} for label, value_html in rows], attrs={"data-flow-models": True})


def _slot_label(slot: str) -> str:
    return t(f"console.settings.providers.slot.{slot}")


def _same_spec(a: dict[str, Any] | None, b: dict[str, Any] | None) -> bool:
    return bool(a and b and (a.get("provider"), a.get("model"), a.get("effort")) == (b.get("provider"), b.get("model"), b.get("effort")))


def _flow_intro(view: dict[str, Any], *, admin_viewer: bool) -> str:
    source = str(view.get("source") or "server")
    if admin_viewer:
        return escape(_k("flow_hint_admin"))
    return escape(_k("flow_hint_member", source=t(f"console.settings.providers.source.{source}")))


def _svg_node(
    x: int,
    y: int,
    w: int,
    h: int,
    *,
    kind: str,
    title: str,
    lines: list[tuple[str, str]],
) -> str:
    """Build one SVG node rectangle with title, subtitle, and model lines."""
    text_parts: list[str] = [f'<text class="pg-node-title" x="{x + 10}" y="{y + 19}">{escape(_svg_clip(title, 28))}</text>']
    cur_y = y + 35
    for cls, line in lines:
        text_parts.append(f'<text class="{cls}" x="{x + 10}" y="{cur_y}">{escape(_svg_clip(line, 30))}</text>')
        cur_y += 15
    return f'<g><rect class="pg-node pg-node-{escape(kind)}" x="{x}" y="{y}" width="{w}" height="{h}" rx="8" />{"".join(text_parts)}</g>'


def _graph_card(settings: Any, view: dict[str, Any], *, admin_viewer: bool) -> str:
    """Build the top SVG overview diagram card inside Tab 1."""
    slots = {str(row["slot"]): row for row in view["slots"]}
    embed_model = _svg_clip(str(view.get("embed_model") or "-"), 24)
    stt_label = _svg_clip(_svg_media_label(view["stt"]), 24)
    vlm_label = _svg_clip(_svg_media_label(view["vlm"], strip_effort=True), 24)
    classify_label = _svg_clip(_svg_slot_label(slots.get("classify")), 24)
    extract_label = _svg_clip(_svg_slot_label(slots.get("extract_graph")), 22)
    summary_label = _svg_clip(_svg_slot_label(slots.get("summarize")), 26)
    detail_label = _svg_clip(_svg_slot_label(slots.get("render_detail")), 22)
    judge_label = _svg_clip(_svg_slot_label(slots.get("entity_judge")), 22)
    rel_label = _svg_clip(_svg_slot_label(slots.get("relation_judge")), 22)
    has_text_ai = all(row.get("available", True) for row in (slots.get("extract_graph"), slots.get("summarize")) if row)
    same_summary = has_text_ai and _same_spec(slots.get("extract_graph"), slots.get("summarize"))
    auto = f"{float(getattr(settings, 'sim_tier_auto_merge', 0.93)):.2f}"
    jev_model = str(getattr(settings, "jev_model", "") or "jev-latest")
    jev_state = _k("on" if view.get("jev_enabled") else "off")

    svg_parts: list[str] = [
        '<defs><marker id="pg-arrow" viewBox="0 0 10 10" refX="8" refY="5" markerWidth="6" markerHeight="6" orient="auto-start-reverse"><path d="M 0 1.5 L 9 5 L 0 8.5 z" fill="var(--muted)" /></marker></defs>',
        # Top entry lane
        '<rect class="pg-lane" x="10" y="8" width="960" height="110" rx="10" />',
        f'<text class="pg-lane-label" x="24" y="25">{escape(_k("graph.lane.entry"))}</text>',
        _svg_node(24, 34, 164, 72, kind="rule", title=_k("graph.node.fetch_title"), lines=[("pg-node-sub", _k("graph.node.fetch_sub"))]),
        '<path class="pg-edge" d="M 188 70 L 212 70" marker-end="url(#pg-arrow)" />',
        _svg_node(214, 34, 234, 72, kind="ai", title=_k("graph.node.media_title"), lines=[("pg-node-sub", _k("graph.node.media_sub")), ("pg-node-model", f"STT: {stt_label}"), ("pg-node-model", f"VLM: {vlm_label}")]),
        '<path class="pg-edge" d="M 448 70 L 472 70" marker-end="url(#pg-arrow)" />',
        _svg_node(474, 34, 224, 72, kind="ai", title=_k("graph.node.classify_title"), lines=[("pg-node-sub", _k("graph.node.classify_sub")), ("pg-node-model", classify_label)]),
        '<path class="pg-edge" d="M 698 70 L 722 70" marker-end="url(#pg-arrow)" />',
        _svg_node(724, 34, 232, 72, kind="rule", title=_k("graph.node.dedup_title"), lines=[("pg-node-sub", _k("graph.node.dedup_sub"))]),
        # Connectors from entry lane to API lane and agent client lane
        '<path class="pg-edge" d="M 840 106 L 840 126 L 140 126 L 140 162" marker-end="url(#pg-arrow)" />',
        '<path class="pg-edge pg-edge-dashed" d="M 38 106 L 38 444" marker-end="url(#pg-arrow)" />',
        # Upper main lane: server API path
        '<rect class="pg-lane" x="54" y="136" width="916" height="266" rx="10" />',
        f'<text class="pg-lane-label" x="68" y="154">{escape(_k("graph.lane.api"))}</text>',
        _svg_node(68, 164, 144, 74, kind="ai", title=_k("graph.node.extract_title"), lines=[("pg-node-sub", _k("graph.node.extract_sub")), ("pg-node-model", extract_label)]),
        _svg_node(236, 164, 212, 62, kind="ai", title=_k("graph.node.summary_title"), lines=[("pg-node-sub", _k("graph.node.summary_sub")), ("pg-node-model", summary_label)]),
        '<path class="pg-edge' + (" pg-edge-dashed" if same_summary else "") + '" d="M 212 190 L 234 190" marker-end="url(#pg-arrow)" />',
        '<path class="pg-edge pg-edge-dashed" d="M 212 222 L 224 222 L 224 238 L 234 268" marker-end="url(#pg-arrow)" />',
        f'<text class="pg-edge-label" x="236" y="241">{escape(_k("graph.edge.bypass"))}</text>',
        '<path class="pg-edge" d="M 342 226 L 342 246" marker-end="url(#pg-arrow)" />',
        _svg_node(68, 284, 144, 102, kind="ai", title=_k("graph.node.detail_title"), lines=[("pg-node-sub", _k("graph.node.detail_sub")), ("pg-node-model", detail_label), ("pg-node-sub", _k("graph.node.detail_note"))]),
        '<path class="pg-edge pg-edge-dashed" d="M 140 238 L 140 282" marker-end="url(#pg-arrow)" />',
        _svg_node(
            236,
            248,
            212,
            140,
            kind="ai",
            title=_k("graph.node.resolve_title"),
            lines=[
                ("pg-node-sub", _k("graph.node.resolve_sub1")),
                ("pg-node-sub", _k("graph.node.resolve_sub2")),
                ("pg-node-model", f"auto ≥ {auto}"),
                ("pg-node-model", f"embed: {embed_model}"),
                ("pg-node-model", f"judge: {judge_label}"),
            ],
        ),
        '<path class="pg-edge" d="M 212 204 L 224 204 L 224 282 L 234 282" marker-end="url(#pg-arrow)" />',
    ]

    if admin_viewer:
        svg_parts.append(_svg_node(472, 164, 174, 62, kind="shadow", title=_k("graph.node.jev_title"), lines=[("pg-node-sub", _k("graph.node.jev_sub")), ("pg-node-model", f"{jev_model} · {jev_state}")]))
        svg_parts.append('<path class="pg-edge pg-edge-dashed" d="M 448 268 L 460 268 L 460 195 L 470 195" marker-end="url(#pg-arrow)" />')

    svg_parts.extend([
        _svg_node(472, 242, 174, 44, kind="rule", title=_k("graph.node.record_title"), lines=[("pg-node-sub", _k("graph.node.record_sub"))]),
        _svg_node(472, 294, 174, 44, kind="rule", title=_k("graph.node.relations_title"), lines=[("pg-node-sub", _k("graph.node.relations_sub"))]),
        _svg_node(472, 346, 174, 48, kind="ai", title=_k("graph.node.links_title"), lines=[("pg-node-model", f"relational → {rel_label}")]),
        '<path class="pg-edge" d="M 448 292 L 470 264" marker-end="url(#pg-arrow)" />',
        '<path class="pg-edge" d="M 448 316 L 470 316" marker-end="url(#pg-arrow)" />',
        '<path class="pg-edge" d="M 448 368 L 470 368" marker-end="url(#pg-arrow)" />',
        _svg_node(670, 268, 130, 76, kind="rule", title=_k("graph.node.lineage_title"), lines=[("pg-node-sub", _k("graph.node.lineage_sub"))]),
        '<path class="pg-edge" d="M 646 264 L 668 292" marker-end="url(#pg-arrow)" />',
        '<path class="pg-edge" d="M 646 316 L 668 306" marker-end="url(#pg-arrow)" />',
        '<path class="pg-edge" d="M 646 368 L 668 320" marker-end="url(#pg-arrow)" />',
        _svg_node(824, 224, 132, 66, kind="ai", title=_k("graph.node.watch_title"), lines=[("pg-node-sub", _k("graph.node.watch_sub")), ("pg-node-model", classify_label)]),
        _svg_node(824, 312, 132, 66, kind="rule", title=_k("graph.node.expand_title"), lines=[("pg-node-sub", _k("graph.node.expand_sub"))]),
        '<path class="pg-edge" d="M 800 296 L 822 257" marker-end="url(#pg-arrow)" />',
        '<path class="pg-edge" d="M 800 316 L 822 345" marker-end="url(#pg-arrow)" />',
        # Lower parallel lane: agent client path
        '<rect class="pg-lane" x="10" y="414" width="960" height="136" rx="10" />',
        f'<text class="pg-lane-label" x="24" y="432">{escape(_k("graph.lane.client"))}</text>',
        _svg_node(24, 446, 162, 88, kind="rule", title=_k("graph.node.stage_title"), lines=[("pg-node-sub", _k("graph.node.stage_sub"))]),
        '<path class="pg-edge" d="M 186 490 L 206 490" marker-end="url(#pg-arrow)" />',
        _svg_node(208, 446, 176, 88, kind="client", title=_k("graph.node.mcp_title"), lines=[("pg-node-sub", _k("graph.node.mcp_sub"))]),
        '<path class="pg-edge" d="M 384 490 L 404 490" marker-end="url(#pg-arrow)" />',
        _svg_node(406, 446, 194, 88, kind="ai", title=_k("graph.node.validate_title"), lines=[("pg-node-sub", _k("graph.node.validate_sub")), ("pg-node-model", embed_model)]),
        '<path class="pg-edge" d="M 600 490 L 620 490" marker-end="url(#pg-arrow)" />',
        _svg_node(622, 446, 186, 88, kind="rule", title=_k("graph.node.cresolve_title"), lines=[("pg-node-sub", _k("graph.node.cresolve_sub"))]),
        '<path class="pg-edge" d="M 808 490 L 828 490" marker-end="url(#pg-arrow)" />',
        _svg_node(830, 446, 126, 88, kind="client", title=_k("graph.node.commit_title"), lines=[("pg-node-sub", _k("graph.node.commit_sub"))]),
    ])

    svg_html = (
        '<div class="pipeline-graph-wrap">'
        '<svg id="pipeline-lineage-svg" class="pipeline-graph-svg" viewBox="0 0 980 560" role="img" aria-labelledby="pipeline-graph-title pipeline-graph-desc">'
        f'<title id="pipeline-graph-title">{escape(_k("graph.title"))}</title>'
        f'<desc id="pipeline-graph-desc">{escape(_k("graph.aria_desc"))}</desc>'
        f'{"".join(svg_parts)}'
        "</svg>"
        "</div>"
    )
    legend_items = [
        console_pill(_k("graph.legend.ai"), tone="accent"),
        console_pill(_k("graph.legend.rule"), tone="neutral"),
        console_pill(_k("graph.legend.client"), tone="ok"),
    ]
    if admin_viewer:
        legend_items.append(console_pill(_k("graph.legend.shadow"), tone="warn"))
    legend_items.append(console_pill(_k("graph.legend.dashed"), tone="prop"))
    legend_html = f'<div class="pipeline-graph-legend">{"".join(legend_items)}</div>'
    return console_card(escape(_k("graph.title")), svg_html + legend_html, id="pipeline-graph", icon="fork", hint_html=_flow_intro(view, admin_viewer=admin_viewer), split=False)


def _effort_desc_html(settings: Any, extract_slot: dict[str, Any] | None) -> str:
    extract_prov = str((extract_slot or {}).get("provider") or "").strip().lower()
    extract_model = str((extract_slot or {}).get("model") or "").strip()
    if extract_prov == "gemini" and is_gemini_fixed_effort_model(extract_model):
        _, fixed_eff = split_gemini_model_and_effort(extract_model)
        text = _k("step.effort.desc_fixed", model=extract_model, effort=fixed_eff or "-")
        return f'<p class="hint" data-effort-mode="fixed">{escape(text)}</p>'
    threshold = f"{int(getattr(settings, 'pdf_paper_threshold_chars', 15000)):,}"
    paper = str(getattr(settings, "pdf_paper_effort", "") or "-")
    default_eff = str(getattr(settings, "pdf_default_effort", "") or (extract_slot or {}).get("effort") or "-")
    text = _k("step.effort.desc_adaptive", threshold=threshold, paper=paper, default=default_eff)
    return f'<p class="hint" data-effort-mode="adaptive">{escape(text)}</p>'


def _classify_desc_html(settings: Any, classify_slot: dict[str, Any] | None) -> str:
    classify_prov = str((classify_slot or {}).get("provider") or "").strip().lower()
    classify_model = str((classify_slot or {}).get("model") or "").strip()
    if classify_prov == "gemini" and is_gemini_fixed_effort_model(classify_model):
        _, fixed_eff = split_gemini_model_and_effort(classify_model)
        text = _k("step.classify.desc_fixed", model=classify_model, effort=fixed_eff or "-")
        return f'<p class="hint" data-classify-effort="fixed">{escape(text)}</p>'
    eff = str(getattr(settings, "pdf_classifier_effort", "") or "-")
    text = _k("step.classify.desc_setting", effort=eff)
    return f'<p class="hint" data-classify-effort="setting">{escape(text)}</p>'


def _api_flow_card(settings: Any, view: dict[str, Any], *, admin_viewer: bool) -> str:
    """Build the detailed server API ingest flow card inside Tab 1."""
    from ...extract.resolver import MAX_JUDGE

    slots = {str(row["slot"]): row for row in view["slots"]}
    auto = f"{float(getattr(settings, 'sim_tier_auto_merge', 0.93)):.2f}"
    low = f"{float(getattr(settings, 'sim_tier_borderline', 0.72)):.2f}"
    embed_html = _code(str(view.get("embed_model") or "-"))
    summary_marks: list[str] = [console_pill(_k("mark.conditional"), tone="neutral")]
    has_text_ai = all(row.get("available", True) for row in (slots.get("extract_graph"), slots.get("summarize")) if row)
    if has_text_ai:
        runs = not _same_spec(slots.get("extract_graph"), slots.get("summarize"))
        summary_marks.append(console_pill(_k("summary_runs" if runs else "summary_skipped"), tone="ok" if runs else "neutral", attrs={"data-summary-state": "runs" if runs else "skipped"}))
    api_only_pill = console_pill(_k("mark.api_only"), tone="neutral", attrs={"data-client-role": "api_only"})
    gates = "".join(
        f'<li data-flow-gate="{n}">{text}</li>'
        for n, text in (
            (1, escape(_k("gate1"))),
            (2, escape(_k("gate2"))),
            (3, escape(_k("gate3", auto=auto))),
            (4, escape(_k("gate4", low=low, auto=auto, max=MAX_JUDGE)) + " " + api_only_pill),
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
            "kind": "ai",
            "body_html": f'<p class="hint">{escape(_k("step.input.desc"))} <a href="#{STT_ORDER_ID}">{escape(_k("step.input.stt_link"))}</a></p>'
            + _models([
                (t("console.settings.providers.feature.stt"), _media_html(view["stt"]) + " " + api_only_pill),
                (t("console.settings.providers.feature.vlm"), _media_html(view["vlm"], strip_effort=True) + " " + api_only_pill),
            ]),
        },
        {
            "key": "classify",
            "kind": "ai",
            "marks": [api_only_pill],
            "body_html": _classify_desc_html(settings, slots.get("classify")) + _models([(_slot_label("classify"), _model_html(slots.get("classify")))]),
        },
        {"key": "check", "kind": "rule", "ai": False},
        {
            "key": "effort",
            "kind": "rule",
            "ai": False,
            "marks": [api_only_pill],
            "body_html": _effort_desc_html(settings, slots.get("extract_graph")),
        },
        {"key": "extract", "kind": "ai", "models": [(_slot_label("extract_graph"), _model_html(slots.get("extract_graph")))]},
        {"key": "summary", "kind": "conditional", "models": [(_slot_label("summarize"), _model_html(slots.get("summarize")))], "marks": summary_marks},
        {
            "key": "detail",
            "kind": "branch",
            "marks": [console_pill(_k("mark.branch"), tone="neutral")],
            "models": [(_slot_label("render_detail"), _model_html(slots.get("render_detail")))],
        },
        {
            "key": "resolve",
            "kind": "ai",
            "body_html": resolve_body + _models([(t("console.settings.providers.feature.embed"), embed_html), (_slot_label("entity_judge"), _model_html(slots.get("entity_judge")))]),
            "side_html": resolve_side,
        },
        {"key": "record", "kind": "rule", "ai": False},
        {"key": "relations", "kind": "rule", "ai": False},
        {
            "key": "links",
            "kind": "ai",
            "desc_params": {"per_doc": int(getattr(settings, "max_relation_judges_per_doc", 0) or 0)},
            "models": [(_slot_label("relation_judge"), _model_html(slots.get("relation_judge")))],
            "marks": [api_only_pill] + ([] if links_on else [console_pill(_k("off"), tone="neutral", attrs={"data-links-state": "off"})]),
        },
        {"key": "lineage", "kind": "rule", "ai": False},
        {
            "key": "post",
            "kind": "ai",
            "marks": [api_only_pill],
            "models": [(_slot_label("classify"), _model_html(slots.get("classify")))],
        },
    ]
    flow_steps: list[dict[str, Any]] = []
    for step in steps:
        key = str(step["key"])
        marks: list[str] = list(step.get("marks", []))
        if step.get("ai") is False:
            marks.append(console_pill(_k("mark.no_ai"), tone="neutral"))
        body = step.get("body_html")
        if body is None:
            body = f'<p class="hint">{escape(_k(f"step.{key}.desc", **step.get("desc_params", {})))}</p>'
            if step.get("models"):
                body += _models(step["models"])
        flow_steps.append({"key": key, "title": _k(f"step.{key}.title"), "marks_html": " ".join(marks), "body_html": body, "side_html": step.get("side_html", ""), "kind": str(step.get("kind") or "ai")})

    legend = f'<p class="hint" data-flow-legend>{escape(_k("legend"))}</p>'
    body = legend + console_flow(flow_steps, aria_label=_k("flow_title"))
    return console_card(escape(_k("flow_title")), body, id="pipeline-api-flow", icon="list", hint_html=escape(_k("api_flow.hint")), split=False)


def _client_flow_card(view: dict[str, Any]) -> str:
    """Build the 5-step summary card for agent client ingestion inside Tab 1."""
    embed_html = _code(str(view.get("embed_model") or "-"))
    items = "".join(f'<li data-client-step="{n}">{escape(_k(f"client_flow.step{n}"))}</li>' for n in range(1, 6))
    steps_html = f'<ol class="console-steps" data-client-flow-list>{items}</ol>'
    models_html = _models([(t("console.settings.providers.feature.embed"), embed_html)])
    omitted_html = console_notice(escape(_k("client_flow.omitted")), tone="info")
    return console_card(escape(_k("client_flow.title")), steps_html + models_html + omitted_html, id="pipeline-client-flow", icon="bot", hint_html=escape(_k("client_flow.hint")), split=False)


def _other_ai_card(view: dict[str, Any], *, admin_viewer: bool) -> str:
    """Build the table of AI calls outside the single-document ingest flow inside Tab 1."""
    slots = {str(row["slot"]): row for row in view["slots"]}
    embed_html = _code(str(view.get("embed_model") or "-"))
    rows: list[tuple[str, str, str]] = [
        (_k("other.search_embed"), f'{escape(t("console.settings.providers.feature.embed"))}: {embed_html}', _k("other.search_embed_desc")),
        (_k("other.search_synth"), f'{escape(_slot_label("research"))}: {_model_html(slots.get("research"))}', _k("other.search_synth_desc")),
        (_k("other.research"), f'{escape(_slot_label("research"))}: {_model_html(slots.get("research"))} {console_pill(_k("other.admin_only_tag"), tone="neutral")}', _k("other.research_desc")),
        (_k("other.auto_expand"), f'{escape(_slot_label("research"))}: {_model_html(slots.get("research"))}', _k("other.auto_expand_desc")),
        (_k("other.presentation"), f'{escape(_slot_label("summarize"))}: {_model_html(slots.get("summarize"))}', _k("other.presentation_desc")),
    ]
    if admin_viewer:
        rows.append((_k("other.pair_prelabel"), f'{escape(_k("other.fixed_slot"))}: {_code("gemini-decision-1")} {console_pill(_k("other.admin_only_tag"), tone="neutral")}', _k("other.pair_prelabel_desc")))
    rows.append((_k("other.byok_verify"), f'{escape(_k("other.fixed_slot"))}: {_code(_BYOK_VERIFY_MODEL)}', _k("other.byok_verify_desc")))
    tbody = "".join(f"<tr><td>{escape(feat)}</td><td>{slot_html}</td><td>{escape(desc)}</td></tr>" for feat, slot_html, desc in rows)
    table = console_table([_k("other.col_feature"), _k("other.col_slot"), _k("other.col_desc")], tbody, tbody_id="pipeline-other-rows", aria_label=_k("other.title"))
    return console_card(escape(_k("other.title")), table, id="pipeline-other", icon="pulse", hint_html=escape(_k("other.hint")), split=False)


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
    flow_tab = "".join([
        _graph_card(settings, view, admin_viewer=admin_viewer),
        _api_flow_card(settings, view, admin_viewer=admin_viewer),
        _client_flow_card(view),
        _other_ai_card(view, admin_viewer=admin_viewer),
    ])
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
