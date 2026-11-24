"""The laboratory (LABORATORY_DESIGN.md): the console area ``/laboratory/*`` for the server administrator only, with the measurements and the shadow evaluations in one place.

- ``GET /laboratory``: 302 to the first section.
- ``GET /laboratory/measurements``: measurement M0 (STT language hints, ``laboratory.stt_hints``) and the records of the runs.
- ``GET /laboratory/shadows``: the Jev shadow evaluation (moved from ``/admin/providers``), the same-subject source merge in mode ``shadow`` with its human review (moved from ``/admin/limits``), and the shadow embedding spaces (read only).
- ``GET /api/admin/laboratory/runs``, ``GET``/``DELETE /api/admin/laboratory/runs/{run_id}``, ``POST /api/admin/laboratory/stt-hints``.

Each other caller gets 404. A run and a shadow never change the knowledge graph.
"""

from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from typing import Any, Final

from starlette.requests import Request
from starlette.responses import JSONResponse, Response
from starlette.routing import Route

from ...accounts import languages as languages_mod
from ...i18n import interpolate, t
from ...laboratory import runs as lab_runs
from ...laboratory import stt_hints
from ..principal import request_principal
from ..security import read_json_body
from .admin import is_admin
from .common import AccountsContext, escape, json_error, not_found, open_db, same_origin
from .console import LABORATORY_SPEC_BY_SLUG, _current_settings, _render_console_section
from .console_ui import console_card, console_field, console_kv, console_notice, console_pill, console_result, console_table, console_toggle_row

LABORATORY_INDEX_PATH: Final = "/laboratory"
RUNS_API_PATH: Final = "/api/admin/laboratory/runs"
STT_HINTS_API_PATH: Final = "/api/admin/laboratory/stt-hints"
_STATUS_TONE: Final = {"running": "warn", "done": "ok", "failed": "danger"}


def _k(key: str, **params: Any) -> str:
    return t(f"console.laboratory.{key}", **params)


def _when(ts: float | None) -> str:
    return "-" if ts is None else datetime.fromtimestamp(float(ts), tz=timezone.utc).strftime("%Y-%m-%d %H:%M UTC")


def _code(text: str) -> str:
    return f'<code class="pref-mono">{escape(text)}</code>'


def _scripts_text(shares: dict[str, Any]) -> str:
    parts = [f"{_k(f'script.{name}')} {round(float(value) * 100)}%" for name, value in shares.items() if float(value or 0) > 0]
    return " · ".join(parts) or "-"


def _run_html(run: lab_runs.LabRun) -> str:
    """One run as a closed ``<details>``: the parameters, and for a finished run the two variants side by side."""
    status = console_pill(_k(f"status.{run.status}"), tone=_STATUS_TONE.get(run.status, "neutral"), attrs={"data-lab-status": run.status})
    agreement = (run.result or {}).get("agreement")
    summary_extra = f" · {escape(_k('agreement_short', value=f'{float(agreement) * 100:.1f}%'))}" if agreement is not None else ""
    summary = f'{escape(_when(run.created_at))} · {escape(_k("kind." + run.kind))} {status}{summary_extra}'
    params = run.params
    rows: list[tuple[str, str]] = [
        (_k("field.url"), f'<a href="{escape(str(params.get("url") or ""))}" rel="noopener noreferrer" target="_blank">{escape(str(params.get("url") or "-"))}</a>'),
        (_k("field.hints"), ", ".join(_code(str(c)) for c in params.get("hints") or []) or "-"),
    ]
    body = console_kv([{"label": label, "value_html": value} for label, value in rows])
    if run.status == "failed":
        body += console_notice(escape(_k("failed", error=str(run.error or "-"))), tone="danger")
    if run.status == "done" and run.result:
        result = run.result
        variants = result.get("variants") or {}
        head = [_k("col.item"), _k("variant.none"), _k("variant.hints")]
        none, hints = variants.get("none") or {}, variants.get("hints") or {}

        def cell(v: dict[str, Any], key: str) -> str:
            if key == "language_codes":
                return ", ".join(_code(str(c)) for c in v.get(key) or []) or escape(_k("no_hints"))
            if key == "scripts":
                return escape(_scripts_text(v.get(key) or {}))
            if key == "latency_ms":
                return escape(f"{float(v.get(key) or 0) / 1000:.1f} s")
            return escape(f"{int(v.get(key) or 0):,}")

        trs = "".join(f"<tr><td>{escape(_k('col.' + key))}</td><td>{cell(none, key)}</td><td>{cell(hints, key)}</td></tr>" for key in ("language_codes", "chars", "segments", "latency_ms", "scripts"))
        body += console_kv([
            {"label": _k("field.agreement"), "value_html": f'<strong data-lab-agreement>{float(result.get("agreement") or 0) * 100:.1f}%</strong> <span class="hint">{escape(_k("agreement_hint"))}</span>'},
            {"label": _k("field.audio"), "value": _k("audio_value", seconds=int(float(result.get("duration_sec") or 0)), model=str(result.get("model") or "-"))},
        ])
        body += console_table(head, trs, aria_label=_k("compare_label"))
        for name in ("none", "hints"):
            excerpt = str((variants.get(name) or {}).get("excerpt") or "")
            body += f'<details class="pref-disclosure"><summary>{escape(_k("excerpt", variant=_k("variant." + name)))}</summary><pre class="console-lab-excerpt">{escape(excerpt)}</pre></details>'
    if run.status != "running":
        body += f'<div class="console-actions"><button type="button" class="pref-btn pref-btn-danger pref-btn-sm" data-lab-delete="{escape(run.id)}">{escape(_k("delete"))}</button><p class="result" role="status" aria-live="polite"></p></div>'
    return f'<details class="pref-disclosure" data-lab-run="{escape(run.id)}"{" open" if run.status == "running" else ""}><summary>{summary}</summary>{body}</details>'


def measurements_body(ctx: AccountsContext, *, account_languages: tuple[str, ...]) -> str:
    settings = _current_settings(ctx)
    key_set = bool(getattr(settings, "gemini_api_key", "")) if settings is not None else False
    conn = open_db(ctx)
    try:
        recent = lab_runs.recent(conn)
    finally:
        conn.close()
    running = any(r.status == "running" for r in recent)
    hints = ", ".join(account_languages or (languages_mod.ALWAYS_LANGUAGE,))
    notice = "" if key_set else console_notice(escape(_k("stt.no_key")), tone="warn", attrs={"data-lab-no-key": True})
    form = (
        f'<form class="console-lab-form" data-lab-form="stt-hints" data-endpoint="{STT_HINTS_API_PATH}">'
        + console_field(_k("field.url"), '<input name="url" type="url" class="pref-input" required placeholder="https://www.youtube.com/watch?v=…" autocomplete="off">', hint_html=escape(_k("stt.url_hint", seconds=stt_hints.MAX_AUDIO_SECONDS)))
        + console_field(_k("field.hints"), f'<input name="hints" class="pref-input" required value="{escape(hints)}" autocomplete="off" spellcheck="false">', hint_html=escape(_k("stt.hints_hint")))
        + f'<div class="console-actions"><button type="submit" class="pref-btn pref-btn-accent"{" disabled" if (running or not key_set) else ""}>{escape(_k("stt.start"))}</button>'
        + console_result(attrs={"data-lab-result": True})
        + "</div></form>"
    )
    cost = f'<p class="hint" data-lab-cost>{escape(_k("stt.cost", seconds=stt_hints.MAX_AUDIO_SECONDS))}</p>'
    card = console_card(escape(_k("stt.title")), notice + cost + form, id="lab-stt-hints", icon="mic", badge_html=console_pill("M0", tone="neutral"), hint_html=escape(_k("stt.hint")), split=False)
    if recent:
        runs_html = "".join(_run_html(r) for r in recent)
    else:
        runs_html = f'<p class="hint" data-lab-empty>{escape(_k("runs_empty"))}</p>'
    history = console_card(escape(_k("runs_title")), runs_html, id="lab-runs", icon="clock", hint_html=escape(_k("runs_hint")), split=False)
    return card + history


def _jev_card(ctx: AccountsContext) -> str:
    from ...core.provider_manager import ProviderManager
    from ...store import pair_review

    cfg = ProviderManager(data_dir=ctx.data_dir or ctx.db_file.parent, auto_migrate=False).get_sanitized_config()
    jev = (cfg.get("providers") or {}).get("jev") or {}
    enabled = bool(jev.get("enabled"))
    conn = open_db(ctx)
    try:
        report = pair_review.report(conn)
    finally:
        conn.close()
    items = report.get("items") or {}
    models = ", ".join(sorted((report.get("jev_by_model") or {}).keys())) or "-"
    stats = console_kv([
        (_k("jev.pairs"), f"{int(items.get('total') or 0):,}"),
        (_k("jev.labeled"), f"{int(items.get('with_consensus') or 0):,}"),
        (_k("jev.models"), models),
    ], attrs={"data-lab-jev-stats": True})
    key_placeholder = _k("jev.key_set") if jev.get("has_api_key") else _k("jev.key_unset")
    form = (
        '<form class="console-lab-form" data-lab-form="jev" data-endpoint="/providers">'
        + console_toggle_row(_k("jev.enabled"), "lab-jev-enabled", desc_html=escape(_k("jev.enabled_hint")), checked=enabled)
        + console_field(_k("jev.key"), f'<input id="lab-jev-key" name="api_key" type="password" class="pref-input" autocomplete="off" placeholder="{escape(key_placeholder)}">')
        + console_field(_k("jev.url"), f'<input id="lab-jev-url" name="base_url" class="pref-input" value="{escape(str(jev.get("base_url") or ""))}">')
        + f'<div class="console-actions"><button type="submit" class="pref-btn pref-btn-accent">{escape(_k("jev.save"))}</button>'
        + console_result(attrs={"data-lab-result": True})
        + "</div></form>"
    )
    pill = console_pill(_k("on" if enabled else "off"), tone="ok" if enabled else "neutral", attrs={"data-lab-jev-state": "on" if enabled else "off"})
    privacy = f'<p class="hint">{escape(_k("jev.privacy"))}</p>'
    return console_card(escape(_k("jev.title")), stats + privacy + form, id="lab-jev", icon="bolt", badge_html=pill, hint_html=escape(_k("jev.hint")), split=False)


def _merge_card(ctx: AccountsContext) -> str:
    from ...core.ingest_limit_manager import IngestLimitManager

    mode = str(IngestLimitManager(data_dir=ctx.data_dir or ctx.db_file.parent, auto_migrate=False).get_settings_dict().get("same_subject_merge") or "off")
    options = "".join(f'<option value="{m}"{" selected" if mode == m else ""}>{escape(_k("merge.mode_" + m))}</option>' for m in ("off", "shadow"))
    form = (
        '<form class="console-lab-form" data-lab-form="merge" data-endpoint="/limits">'
        + console_field(_k("merge.mode"), f'<select name="same_subject_merge" class="pref-select" data-lab-merge-mode>{options}</select>', hint_html=escape(_k("merge.mode_hint")))
        + f'<div class="console-actions"><button type="submit" class="pref-btn pref-btn-accent">{escape(_k("merge.save"))}</button>'
        + console_result(attrs={"data-lab-result": True})
        + "</div></form>"
    )
    pill = console_pill(_k("merge.mode_" + mode), tone="ok" if mode == "shadow" else "neutral", attrs={"data-lab-merge-state": mode})
    return console_card(escape(_k("merge.title")), form, id="lab-merge", icon="merge", badge_html=pill, hint_html=escape(_k("merge.hint")), split=False)


def _embedding_card(ctx: AccountsContext) -> str:
    conn = open_db(ctx)
    try:
        rows = conn.execute(
            "SELECT number, label, active_space_id, target_space_id FROM knowledge_bases ORDER BY seq"
        ).fetchall()
        spaces = {str(r[0]): (str(r[1]), str(r[2] or "")) for r in conn.execute("SELECT space_id, status, eos_date FROM embedding_spaces").fetchall()}
    finally:
        conn.close()
    from ...core import ids

    trs: list[str] = []
    for number, label, active, target in rows:
        status, eos = spaces.get(str(active or ""), ("", ""))
        state = console_pill(_k("embed.shadow"), tone="warn") if target and target != active else console_pill(_k("embed.single"), tone="neutral")
        eos_html = f' <span class="hint">{escape(_k("embed.eos", date=eos))}</span>' if eos else ""
        ref = ids.format_public_id(number) if number is not None else "-"
        trs.append(f'<tr data-lab-kb="{escape(ref)}"><td>{escape(str(label))} <code class="pref-mono">{escape(ref)}</code></td><td>{_code(str(active or "-"))}{eos_html}</td><td>{_code(str(target)) if target and target != active else "-"}</td><td>{state}</td></tr>')
    table = console_table([_k("embed.col_kb"), _k("embed.col_active"), _k("embed.col_target"), _k("embed.col_state")], "".join(trs), aria_label=_k("embed.title"))
    commands = interpolate(escape(_k("embed.commands")), {"backfill": _code("claire re-embed --target-space <space> --mode batch"), "calibrate": _code("claire re-embed --calibrate"), "cutover": _code("claire re-embed --cutover")})
    return console_card(escape(_k("embed.title")), table + f'<p class="hint">{commands}</p>', id="lab-embedding", icon="database", hint_html=escape(_k("embed.hint")), split=False)


def shadows_body(ctx: AccountsContext) -> str:
    review_host = '<div class="console-host" id="admin-expansion-review-host" data-console-host="admin-expansion-review"><p class="hint">' + escape(t("console.common.loading")) + "</p></div>"
    return _jev_card(ctx) + _merge_card(ctx) + review_host + _embedding_card(ctx)


def routes(ctx: AccountsContext) -> list[Route]:
    async def index(request: Request) -> Response:
        if not is_admin(request_principal(request)):
            return not_found()
        return Response(status_code=302, headers={"Location": LABORATORY_SPEC_BY_SLUG["measurements"].path, "Cache-Control": "no-store"})

    async def section_page(request: Request) -> Response:
        principal = request_principal(request)
        if not is_admin(principal):
            return not_found()
        slug = request.url.path.rsplit("/", 1)[-1]
        spec = LABORATORY_SPEC_BY_SLUG.get(slug)
        if spec is None:
            return not_found()

        def _render() -> Response:
            if slug == "measurements":
                conn = open_db(ctx)
                try:
                    account_langs = languages_mod.account_languages(conn, int(principal.account_id or 0))
                finally:
                    conn.close()
                body = measurements_body(ctx, account_languages=account_langs)
            else:
                body = shadows_body(ctx)
            return _render_console_section(ctx, spec=spec, principal=principal, raw_theme=None, custom_body_html=body, extra_scripts_html='<script src="/static/js/laboratory.js" defer></script>')

        return await asyncio.to_thread(_render)

    async def runs_list(request: Request) -> Response:
        if not is_admin(request_principal(request)):
            return not_found()

        def _list() -> dict[str, Any]:
            conn = open_db(ctx)
            try:
                return {"runs": [r.to_dict() for r in lab_runs.recent(conn)]}
            finally:
                conn.close()

        return JSONResponse(await asyncio.to_thread(_list), headers={"Cache-Control": "no-store"})

    async def run_get(request: Request) -> Response:
        if not is_admin(request_principal(request)):
            return not_found()
        run_id = request.path_params["run_id"]

        def _get() -> dict[str, Any] | None:
            conn = open_db(ctx)
            try:
                lab_runs.recent(conn, limit=1)  # closes a stale run
                return lab_runs.get(conn, run_id).to_dict()
            except LookupError:
                return None
            finally:
                conn.close()

        found = await asyncio.to_thread(_get)
        return not_found() if found is None else JSONResponse({"run": found}, headers={"Cache-Control": "no-store"})

    async def run_delete(request: Request) -> Response:
        if not is_admin(request_principal(request)):
            return not_found()
        if not same_origin(request):
            return json_error(403, "forbidden")
        run_id = request.path_params["run_id"]

        def _delete() -> bool:
            conn = open_db(ctx)
            try:
                return lab_runs.delete(conn, run_id)
            finally:
                conn.close()

        return JSONResponse({"deleted": run_id}) if await asyncio.to_thread(_delete) else not_found()

    async def stt_hints_start(request: Request) -> Response:
        principal = request_principal(request)
        if not is_admin(principal):
            return not_found()
        if not same_origin(request):
            return json_error(403, "forbidden")
        body = await read_json_body(request)
        if not isinstance(body, dict) or set(body) != {"url", "hints"} or not isinstance(body.get("url"), str) or not (isinstance(body.get("hints"), list) and all(isinstance(h, str) for h in body["hints"])):
            return json_error(400, "invalid_request", fields=["url", "hints"])
        settings = await asyncio.to_thread(_current_settings, ctx)
        if settings is None or not getattr(settings, "gemini_api_key", ""):
            return json_error(409, "no_api_key")

        def _start() -> dict[str, Any]:
            run = stt_hints.start_run(ctx.db_file, settings, url=body["url"], hints=body["hints"], created_by=principal.account_id)
            return run.to_dict()

        try:
            run = await asyncio.to_thread(_start)
        except stt_hints.LabRunError as exc:
            return json_error(400, exc.code)
        except lab_runs.RunInProgress:
            return json_error(409, "run_in_progress")
        return JSONResponse({"run": run}, status_code=202, headers={"Cache-Control": "no-store"})

    return [
        Route(LABORATORY_INDEX_PATH, index, methods=["GET", "HEAD"]),
        *[Route(spec.path, section_page, methods=["GET", "HEAD"]) for spec in LABORATORY_SPEC_BY_SLUG.values()],
        Route(RUNS_API_PATH, runs_list, methods=["GET", "HEAD"]),
        Route(RUNS_API_PATH + "/{run_id}", run_get, methods=["GET", "HEAD"]),
        Route(RUNS_API_PATH + "/{run_id}", run_delete, methods=["DELETE"]),
        Route(STT_HINTS_API_PATH, stt_hints_start, methods=["POST"]),
    ]

