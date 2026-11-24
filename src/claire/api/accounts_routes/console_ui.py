"""Typed HTML builder helpers for console sections (/account/*, /settings/*, /admin/*, /moderation/*)."""

from __future__ import annotations

import re
from collections.abc import Mapping, Sequence
from typing import Any

from .common import escape, icon_html

__all__ = [
    "console_actions",
    "console_card",
    "console_field",
    "console_flow",
    "console_form_card",
    "console_host",
    "console_kv",
    "console_language_list",
    "console_notice",
    "console_pill",
    "console_result",
    "console_stat_row",
    "console_table",
    "console_tabs",
    "console_toggle_row",
]

_SAFE_CARD_TAGS = frozenset({"section", "div", "form", "article"})
_ATTR_NAME_RE = re.compile(r"^[a-zA-Z_:][a-zA-Z0-9_.:-]*$")


def _join_classes(*parts: str) -> str:
    return " ".join(part.strip() for part in parts if part and part.strip())


def _attrs_html(attrs: Mapping[str, Any] | None) -> str:
    if not attrs:
        return ""
    parts: list[str] = []
    for name, value in attrs.items():
        if not _ATTR_NAME_RE.match(name):
            raise ValueError(f"Invalid HTML attribute name: {name!r}")
        if value is None or value is False:
            continue
        if value is True:
            parts.append(f" {name}")
        else:
            parts.append(f' {name}="{escape(value)}"')
    return "".join(parts)


def console_card(
    title_html: str,
    body_html: str,
    *,
    id: str = "",
    icon: str = "",
    badge_html: str = "",
    hint_html: str = "",
    actions_html: str = "",
    tag: str = "section",
    split: bool = True,
    aria_label: str = "",
    extra_class: str = "",
    attrs: Mapping[str, Any] | None = None,
) -> str:
    """Build a canonical console card element."""
    if tag not in _SAFE_CARD_TAGS:
        raise ValueError(f"Unsupported console card tag: {tag!r}")
    classes = _join_classes("console-subcard", "" if split else "no-split", extra_class)
    id_attr = f' id="{escape(id)}"' if id else ""
    aria_attr = f' aria-label="{escape(aria_label)}"' if aria_label else ""
    icon_part = f"{icon_html(icon)} " if icon else ""
    badge_part = f" {badge_html}" if badge_html else ""
    heading_html = f"<h2>{icon_part}{title_html}{badge_part}</h2>" if (title_html or icon or badge_html) else ""
    if actions_html:
        header_html = f'<div class="console-card-head">{heading_html}{actions_html}</div>'
    else:
        header_html = heading_html
    hint_block = f'<p class="hint">{hint_html}</p>' if hint_html else ""
    return f'<{tag} class="{escape(classes)}"{id_attr}{aria_attr}{_attrs_html(attrs)}>{header_html}{hint_block}{body_html}</{tag}>'


def console_form_card(
    title_html: str,
    body_html: str,
    *,
    id: str = "",
    icon: str = "",
    badge_html: str = "",
    hint_html: str = "",
    actions_html: str = "",
    split: bool = True,
    aria_label: str = "",
    extra_class: str = "",
    action: str = "",
    method: str = "",
    attrs: Mapping[str, Any] | None = None,
) -> str:
    """Build a console card that renders as a form element."""
    merged_attrs: dict[str, Any] = dict(attrs) if attrs else {}
    if action:
        merged_attrs["action"] = action
    if method:
        merged_attrs["method"] = method
    return console_card(
        title_html,
        body_html,
        id=id,
        icon=icon,
        badge_html=badge_html,
        hint_html=hint_html,
        actions_html=actions_html,
        tag="form",
        split=split,
        aria_label=aria_label,
        extra_class=extra_class,
        attrs=merged_attrs or None,
    )


def console_host(
    host_id: str,
    *,
    loading_text: str = "",
    panel_host: str = "",
    outer_id: str = "",
    extra_class: str = "",
    attrs: Mapping[str, Any] | None = None,
) -> str:
    """Build an unboxed console host container without card borders."""
    classes = _join_classes("console-host", extra_class)
    merged_attrs: dict[str, Any] = dict(attrs) if attrs else {}
    if panel_host:
        if panel_host.startswith("admin:"):
            panel_name = panel_host.split(":", 1)[1]
            merged_attrs.setdefault("data-admin-panel-host", panel_name)
            merged_attrs.setdefault("data-console-host", panel_name)
        elif panel_host.startswith("settings:"):
            panel_name = panel_host.split(":", 1)[1]
            merged_attrs.setdefault("data-settings-panel-host", panel_name)
            merged_attrs.setdefault("data-console-host", panel_name)
        else:
            if host_id.startswith("admin-"):
                merged_attrs.setdefault("data-admin-panel-host", panel_host)
            elif host_id.startswith("settings-"):
                merged_attrs.setdefault("data-settings-panel-host", panel_host)
            merged_attrs.setdefault("data-console-host", panel_host)
    else:
        merged_attrs.setdefault("data-console-host", host_id)
    inner = f'<p class="hint">{escape(loading_text)}</p>' if loading_text else ""
    host_html = f'<div class="{escape(classes)}" id="{escape(host_id)}"{_attrs_html(merged_attrs)}>{inner}</div>'
    if outer_id:
        return f'<div id="{escape(outer_id)}">{host_html}</div>'
    return host_html


def console_field(
    label_text: str,
    control_html: str,
    *,
    for_id: str = "",
    hint_html: str = "",
    extra_class: str = "",
    attrs: Mapping[str, Any] | None = None,
) -> str:
    """Build a labeled console form field."""
    classes = _join_classes("console-field", extra_class)
    for_attr = f' for="{escape(for_id)}"' if for_id else ""
    hint_block = f'<small class="console-field-hint">{hint_html}</small>' if hint_html else ""
    return f'<label class="{escape(classes)}"{for_attr}{_attrs_html(attrs)}>{escape(label_text)}{control_html}{hint_block}</label>'


def console_toggle_row(
    label_text: str,
    input_id: str,
    *,
    desc_html: str = "",
    checked: bool = False,
    disabled: bool = False,
    attrs: Mapping[str, Any] | None = None,
) -> str:
    """Build a preference row with a native checkbox switch control."""
    desc_block = f"<span>{desc_html}</span>" if desc_html else ""
    checked_attr = " checked" if checked else ""
    disabled_attr = " disabled" if disabled else ""
    return (
        '<div class="pref-row">'
        '<div class="pref-row-label">'
        f'<label for="{escape(input_id)}"><strong>{escape(label_text)}</strong></label>'
        f"{desc_block}"
        "</div>"
        f'<input id="{escape(input_id)}" type="checkbox" switch{checked_attr}{disabled_attr}{_attrs_html(attrs)}/>'
        "</div>"
    )


def console_table(
    headers: Sequence[str],
    tbody_html: str,
    *,
    tbody_id: str = "",
    aria_label: str = "",
    caption_html: str = "",
    extra_class: str = "",
) -> str:
    """Build a responsive console table inside a scroll wrapper."""
    classes = _join_classes("pref-table", extra_class)
    aria_attr = f' aria-label="{escape(aria_label)}"' if aria_label else ""
    caption_block = f"<caption>{caption_html}</caption>" if caption_html else ""
    thead_block = f'<thead><tr>{"".join(f"<th>{escape(h)}</th>" for h in headers)}</tr></thead>' if headers else ""
    tbody_id_attr = f' id="{escape(tbody_id)}"' if tbody_id else ""
    return f'<div class="pref-table-wrap"><table class="{escape(classes)}"{aria_attr}>{caption_block}{thead_block}<tbody{tbody_id_attr}>{tbody_html}</tbody></table></div>'


def console_notice(
    content_html: str,
    *,
    tone: str = "info",
    extra_class: str = "",
    attrs: Mapping[str, Any] | None = None,
) -> str:
    """Build a console notice banner."""
    classes = _join_classes("pref-notice", extra_class)
    tone_attr = f' data-tone="{escape(tone)}"' if tone else ""
    return f'<div class="{escape(classes)}"{tone_attr}{_attrs_html(attrs)}>{content_html}</div>'


def console_kv(
    rows: Sequence[tuple[str, str] | Mapping[str, Any]],
    *,
    extra_class: str = "",
    attrs: Mapping[str, Any] | None = None,
) -> str:
    """Build a key-value definition list. Plain text tuples escape both key and value; mappings also support label_html, value_html, dt_attrs, and attrs."""
    classes = _join_classes("console-kv", extra_class)
    items: list[str] = []
    for row in rows:
        if isinstance(row, Mapping):
            label_part = str(row["label_html"]) if "label_html" in row and row["label_html"] is not None else escape(row.get("label", ""))
            value_part = str(row["value_html"]) if "value_html" in row and row["value_html"] is not None else escape(row.get("value", ""))
            dt_attrs = _attrs_html(row.get("dt_attrs"))
            dd_attrs = _attrs_html(row.get("attrs"))
            items.append(f"<dt{dt_attrs}>{label_part}</dt><dd{dd_attrs}>{value_part}</dd>")
        else:
            label, value = row
            items.append(f"<dt>{escape(label)}</dt><dd>{escape(value)}</dd>")
    return f'<dl class="{escape(classes)}"{_attrs_html(attrs)}>{"".join(items)}</dl>'


def console_result(*, id: str = "", attrs: Mapping[str, Any] | None = None) -> str:
    """Build a polite live-region status element for form or action feedback."""
    id_attr = f' id="{escape(id)}"' if id else ""
    return f'<p class="result" role="status" aria-live="polite"{id_attr}{_attrs_html(attrs)}></p>'


def console_stat_row(
    items_html: str | Sequence[str],
    *,
    attrs: Mapping[str, Any] | None = None,
) -> str:
    """Build a KPI stat grid container."""
    inner = items_html if isinstance(items_html, str) else "".join(items_html)
    return f'<div class="pref-stat-row"{_attrs_html(attrs)}>{inner}</div>'


def console_actions(content_html: str, *, extra_class: str = "") -> str:
    """Build a wrapped row of console action controls."""
    classes = _join_classes("console-actions", extra_class)
    return f'<div class="{escape(classes)}">{content_html}</div>'


def console_pill(
    text: str,
    *,
    tone: str = "neutral",
    extra_class: str = "",
    attrs: Mapping[str, Any] | None = None,
) -> str:
    """Build a status pill badge."""
    classes = _join_classes("pref-pill", extra_class)
    tone_attr = f' data-tone="{escape(tone)}"' if tone else ""
    return f'<span class="{escape(classes)}"{tone_attr}{_attrs_html(attrs)}>{escape(text)}</span>'


def console_tabs(
    tabs: Sequence[Mapping[str, str]],
    *,
    id: str,
    aria_label: str,
) -> str:
    """Build a tab set (``.claire-tabs``) that ``ClaireUi.enhance`` binds. Each tab is a mapping with ``id``, ``label`` (plain text), ``icon``, and ``html`` (the panel body). The first tab is selected; the other panels are ``hidden="until-found"``."""
    if not tabs:
        raise ValueError("console_tabs requires one tab or more")
    buttons: list[str] = []
    panels: list[str] = []
    for idx, tab in enumerate(tabs):
        selected = idx == 0
        tab_id = escape(tab["id"])
        buttons.append(
            f'<button type="button" class="claire-tab" role="tab" data-tab="{tab_id}" aria-selected="{"true" if selected else "false"}"{" focusgroupstart" if selected else ""}>'
            f'{icon_html(tab.get("icon", ""))} <span>{escape(tab["label"])}</span></button>'
        )
        hidden = "" if selected else ' hidden="until-found"'
        panels.append(f'<div role="tabpanel" data-tabpanel="{tab_id}"{hidden}><div class="claire-panel-inner">{tab["html"]}</div></div>')
    return (
        f'<div class="claire-tabs" id="{escape(id)}">'
        f'<div class="claire-tablist-wrap"><div class="claire-tablist" role="tablist" focusgroup="tablist nomemory" aria-label="{escape(aria_label)}">'
        + "".join(buttons)
        + "</div></div>"
        + '<div class="claire-tabpanels">'
        + "".join(panels)
        + "</div></div>"
    )


def console_flow(
    steps: Sequence[Mapping[str, Any]],
    *,
    aria_label: str = "",
    attrs: Mapping[str, Any] | None = None,
) -> str:
    """Build a flow diagram (``.console-flow``): the steps of a process in order. Each step is a mapping with ``key``, ``title`` (plain text), ``body_html``, and the optional ``marks_html`` (pills after the title), ``side_html`` (a note beside the step), and ``kind`` (``ai`` or ``rule``: a step without an AI call)."""
    if not steps:
        raise ValueError("console_flow requires one step or more")
    items: list[str] = []
    for number, step in enumerate(steps, 1):
        kind = str(step.get("kind") or "ai")
        marks = f' <span class="console-flow-marks">{step["marks_html"]}</span>' if step.get("marks_html") else ""
        side = f'<aside class="console-flow-side">{step["side_html"]}</aside>' if step.get("side_html") else ""
        items.append(
            f'<li class="console-flow-step{" has-side" if side else ""}" data-flow-step="{escape(str(step["key"]))}" data-flow-kind="{escape(kind)}">'
            f'<div class="console-flow-main"><h3 class="console-flow-title"><span class="console-flow-num" aria-hidden="true">{number}</span>{escape(str(step["title"]))}{marks}</h3>{step.get("body_html", "")}</div>{side}</li>'
        )
    aria_attr = f' aria-label="{escape(aria_label)}"' if aria_label else ""
    # role="list": a list with list-style none keeps its list role in each screen reader.
    return f'<ol class="console-flow" role="list"{aria_attr}{_attrs_html(attrs)}>{"".join(items)}</ol>'


# Suggestions of the language field of a language list. Any language tag is valid; the browser shows the name of each code (Intl.DisplayNames, console_languages.js).
LANGUAGE_SUGGESTIONS: tuple[str, ...] = ("ko", "en", "ja", "zh", "zh-hant", "es", "fr", "de", "it", "pt", "ru", "vi", "th", "id", "ar", "hi")


def console_language_list(
    languages: Sequence[str],
    *,
    list_id: str,
    endpoint: str,
    method: str,
    field: str,
    labels: Mapping[str, str],
    fixed_language: str = "en",
    follow: bool = False,
    reload_on_save: bool = False,
    attrs: Mapping[str, Any] | None = None,
) -> str:
    """Build the form of an ordered language list (``console_languages.js`` binds it): the items with buttons up, down, and remove, a field to add a language tag, and the save button with its result. ``labels`` has plain text for ``position``, ``up``, ``down``, ``remove``, ``add``, ``input``, ``save``, and (with ``follow``) ``follow``: a button that sends ``null`` (follow the server default). ``fixed_language`` cannot be removed."""
    items: list[str] = []
    for tag in languages:
        tag_e = escape(tag)
        remove_disabled = " disabled" if tag == fixed_language else ""
        items.append(
            f'<li data-lang="{tag_e}"><span class="console-lang-pos" aria-hidden="true"></span>'
            f'<span class="console-lang-name" data-lang-name="{tag_e}">{tag_e}</span> <code class="pref-mono">{tag_e}</code>'
            f'<span class="console-lang-btns">'
            f'<button type="button" class="pref-btn pref-btn-sm" data-lang-up aria-label="{escape(labels["up"])}">↑</button>'
            f'<button type="button" class="pref-btn pref-btn-sm" data-lang-down aria-label="{escape(labels["down"])}">↓</button>'
            f'<button type="button" class="pref-btn pref-btn-sm" data-lang-remove aria-label="{escape(labels["remove"])}"{remove_disabled}>✕</button>'
            f"</span></li>"
        )
    suggestions = "".join(f'<option value="{escape(code)}"></option>' for code in LANGUAGE_SUGGESTIONS)
    follow_html = f'<button type="button" class="pref-btn" data-lang-follow>{escape(labels["follow"])}</button>' if follow else ""
    input_id = f"{list_id}-input"
    return (
        f'<form class="console-lang-form" data-language-form data-endpoint="{escape(endpoint)}" data-method="{escape(method)}" data-field="{escape(field)}" data-fixed-language="{escape(fixed_language)}"'
        f'{" data-reload-on-save" if reload_on_save else ""}{_attrs_html(attrs)}>'
        f'<ol class="console-lang-list" role="list" id="{escape(list_id)}" data-language-list aria-label="{escape(labels["position"])}">{"".join(items)}</ol>'
        f'<div class="console-form-row"><label class="console-field" for="{escape(input_id)}">{escape(labels["input"])}'
        f'<input id="{escape(input_id)}" class="pref-input" data-lang-input list="{escape(list_id)}-suggest" autocomplete="off" maxlength="35" spellcheck="false"></label>'
        f'<button type="button" class="pref-btn" data-lang-add>{escape(labels["add"])}</button></div>'
        f'<datalist id="{escape(list_id)}-suggest">{suggestions}</datalist>'
        f'<div class="console-actions"><button type="submit" class="pref-btn pref-btn-accent">{escape(labels["save"])}</button>{follow_html}'
        f'<p class="result" role="status" aria-live="polite" data-language-result></p></div>'
        "</form>"
    )
