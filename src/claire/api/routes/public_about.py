"""Public ``/about`` route (ABOUT_PAGE_AND_INTERACTIVE_PIPELINE_DESIGN.md).

Serves an anonymous, localized overview of the Claire Bible instance using ONLY safe KD15-compliant public data and the shared Gemini Neon pipeline component in ``public`` viewer mode.
"""

from __future__ import annotations

import asyncio
import os
import sqlite3
from pathlib import Path
from typing import Any

from starlette.requests import Request
from starlette.responses import Response
from starlette.routing import Route

from ...i18n import get_locale, t
from ...store import accounts as accounts_store
from ...store import db as dbm
from ..accounts_routes.common import AccountsContext, escape, render_template
from ..accounts_routes.pipeline import (
    build_pipeline_view_data,
    render_pipeline_mount_html,
    resolve_primary_language_info,
)
from ..public_kb_routes import public_appbar_html
from ..security import WebRuntimeConfig

_SOURCE_ROW_KEYS: tuple[str, ...] = ("web", "docs", "video", "adapters", "vmware", "feeds")
_EMBEDDED_BUILD_COMMIT_PATH = Path("/app/src/claire/BUILD_COMMIT")
_LOCAL_BUILD_COMMIT_PATH = Path(__file__).resolve().parents[2] / "BUILD_COMMIT"
_UNSET_COMMIT_VALUES = frozenset({"", "unknown", "null", "none"})


def _resolve_build_commit_short() -> str:
    """Return a 7-char build SHA from ``CLAIRE_BUILD_COMMIT`` or ``BUILD_COMMIT``, else ``""``."""
    raw = os.environ.get("CLAIRE_BUILD_COMMIT", "").strip()
    if raw.lower() in _UNSET_COMMIT_VALUES:
        raw = ""
        for candidate in (_EMBEDDED_BUILD_COMMIT_PATH, _LOCAL_BUILD_COMMIT_PATH):
            try:
                if candidate.is_file():
                    text = candidate.read_text(encoding="utf-8").strip()
                    if text.lower() not in _UNSET_COMMIT_VALUES:
                        raw = text
                        break
            except OSError:
                continue
    if not raw:
        return ""
    return raw[:7]


def _scalar_int(conn: sqlite3.Connection, sql: str, params: tuple[Any, ...] = ()) -> int:
    try:
        row = conn.execute(sql, params).fetchone()
    except sqlite3.OperationalError:
        return 0
    return int(row[0]) if row is not None and row[0] is not None else 0


def _collect_public_snapshot(settings: Any) -> dict[str, Any]:
    """Read strictly public, KD15-compliant instance counts and owner metadata."""
    mode = "single"
    public_profiles = 0
    public_shared_kbs = 0
    public_themes = 0
    public_documents = 0
    public_entities = 0
    curator_name = ""
    curator_handle = ""

    db_file = Path(getattr(settings, "db_file", "")) if getattr(settings, "db_file", None) else None
    if db_file is not None and db_file.is_file():
        conn = dbm.connect_existing(db_file, readonly=True)
        try:
            try:
                mode = accounts_store.AccountStore(conn).server_mode()
            except Exception:  # noqa: BLE001
                mode = "single"
            public_profiles = _scalar_int(conn, "SELECT COUNT(*) FROM accounts WHERE profile_public = 1 AND status = 'active'")
            public_shared_kbs = _scalar_int(conn, "SELECT COUNT(*) FROM knowledge_bases WHERE is_personal = 0 AND is_public = 1")
            public_themes = _scalar_int(conn, "SELECT COUNT(*) FROM themes WHERE state = 'active'")
            public_documents = _scalar_int(
                conn,
                "SELECT COUNT(*) FROM documents d JOIN knowledge_bases k ON k.id = d.kb_id LEFT JOIN accounts a ON a.id = k.account_id WHERE k.is_public = 1 AND (k.is_personal = 0 OR (a.status = 'active' AND (? = 'single' OR a.profile_public = 1))) AND COALESCE(d.hidden, 0) = 0 AND COALESCE(d.extraction_status, 'extracted') = 'extracted'",
                (mode,),
            )
            public_entities = _scalar_int(
                conn,
                "SELECT COUNT(*) FROM entities e JOIN knowledge_bases k ON k.id = e.kb_id LEFT JOIN accounts a ON a.id = k.account_id WHERE k.is_public = 1 AND (k.is_personal = 0 OR (a.status = 'active' AND (? = 'single' OR a.profile_public = 1))) AND COALESCE(e.provisional, 0) = 0",
                (mode,),
            )
            try:
                admin_row = conn.execute("SELECT username, display_name FROM accounts WHERE role = 'admin' LIMIT 1").fetchone()
            except sqlite3.OperationalError:
                admin_row = None
            if admin_row is not None:
                username = str(admin_row[0] or "").strip()
                display_name = str(admin_row[1] or "").strip()
                curator_name = display_name or username
                try:
                    public_host = WebRuntimeConfig.from_settings(settings).expected_authority.strip()
                except Exception:  # noqa: BLE001
                    public_host = ""
                if username and public_host:
                    curator_handle = f"@{username}@{public_host}"
        finally:
            conn.close()

    return {
        "mode": mode,
        "public_profiles": public_profiles,
        "public_shared_kbs": public_shared_kbs,
        "public_themes": public_themes,
        "public_documents": public_documents,
        "public_entities": public_entities,
        "curator_name": curator_name,
        "curator_handle": curator_handle,
        "build_commit_short": _resolve_build_commit_short(),
    }


def _repo_url(repo_or_url: str) -> str:
    cleaned = str(repo_or_url or "").strip()
    if not cleaned:
        return ""
    if cleaned.startswith(("https://", "http://")):
        return cleaned.rstrip("/")
    return f"https://github.com/{cleaned.lstrip('/')}"


def _build_sources_rows_html(locale: str) -> str:
    return "".join(
        f"<tr><td><strong>{escape(t(f'about.sources.row.{key}.type', locale=locale))}</strong></td><td>{escape(t(f'about.sources.row.{key}.method', locale=locale))}</td><td>{escape(t(f'about.sources.row.{key}.note', locale=locale))}</td></tr>"
        for key in _SOURCE_ROW_KEYS
    )


def _build_footer_links_html(settings: Any, *, locale: str) -> str:
    links: list[str] = [f'<a href="/reference">{escape(t("about.footer.api_docs", locale=locale))}</a>']
    github_repo = str(getattr(settings, "effective_github_repository", "") or "").strip()
    if github_repo:
        github_url = str(getattr(settings, "effective_source_base_url", "") or "").strip() or _repo_url(github_repo)
        if github_url:
            links.append(f'<a href="{escape(github_url)}" target="_blank" rel="noopener noreferrer">{escape(t("about.footer.github", locale=locale))}</a>')
    marketplace_repo = str(getattr(settings, "agent_marketplace_repo", "") or "").strip()
    if marketplace_repo:
        mp_url = _repo_url(marketplace_repo)
        if mp_url:
            links.append(f'<a href="{escape(mp_url)}" target="_blank" rel="noopener noreferrer">{escape(t("about.footer.marketplace", locale=locale))}</a>')
    return "".join(links)


def _build_kpi_build_html(settings: Any, *, short_sha: str, locale: str) -> str:
    if not short_sha:
        return ""
    value_text = escape(t("about.kpi.build_val", locale=locale, sha=short_sha))
    sub_text = escape(t("about.kpi.build_sub", locale=locale))
    github_repo = str(getattr(settings, "effective_github_repository", "") or "").strip()
    github_url = (str(getattr(settings, "effective_source_base_url", "") or "").strip() or _repo_url(github_repo)) if github_repo else ""
    value_html = f'<a href="{escape(github_url)}" target="_blank" rel="noopener noreferrer">{value_text}</a>' if github_url else value_text
    return f'<div class="about-kpi"><b>{value_html}</b><span>{sub_text}</span></div>'


def _build_curator_strip_html(*, curator_name: str, curator_handle: str, locale: str) -> str:
    if not curator_name:
        return ""
    handle_html = f' <code>{escape(curator_handle)}</code>' if curator_handle else ""
    return (
        '<div class="about-meta-strip">'
        '<span class="about-meta-item">'
        '<span data-icon="users"></span>'
        f'<span>{escape(t("about.curator.label", locale=locale))}: <strong>{escape(curator_name)}</strong>{handle_html}</span>'
        "</span>"
        "</div>"
    )


def build_public_about_routes(*, settings: Any, ctx: AccountsContext) -> list[Route]:
    """Build the public ``/about`` route."""

    async def public_about_route(request: Request) -> Response:
        locale = get_locale()

        def _render() -> Response:
            live_settings = ctx.current_settings() or settings
            snap = _collect_public_snapshot(live_settings)
            _, lang_order, primary_lang_name = resolve_primary_language_info(live_settings, locale=locale)
            pipeline_data = build_pipeline_view_data(
                viewer="public",
                settings=live_settings,
                lang_order=lang_order,
                locale=locale,
            )
            mode_key = "about.kpi.mode_network" if snap["mode"] == "network" else "about.kpi.mode_single"
            appbar_html = public_appbar_html(request, ctx, active="about")
            return render_template(
                "public_about.html",
                text={
                    "page_title": t("about.page_title", locale=locale),
                    "hero_lead": t("about.hero.lead", locale=locale, lang=primary_lang_name),
                    "kpi_mode_value": t(mode_key, locale=locale),
                    "kpi_mode_sub": t("about.kpi.mode_sub", locale=locale),
                    "kpi_public_value": t(
                        "about.kpi.public_counts_val",
                        locale=locale,
                        profiles=f"{snap['public_profiles']:,}",
                        kbs=f"{snap['public_shared_kbs']:,}",
                        themes=f"{snap['public_themes']:,}",
                    ),
                    "kpi_public_sub": t("about.kpi.public_counts_sub", locale=locale),
                    "kpi_content_value": t(
                        "about.kpi.docs_entities_val",
                        locale=locale,
                        docs=f"{snap['public_documents']:,}",
                        entities=f"{snap['public_entities']:,}",
                    ),
                    "kpi_content_sub": t("about.kpi.docs_entities_sub", locale=locale),
                },
                markup={
                    "common_appbar_html": appbar_html,
                    "kpi_build_html": _build_kpi_build_html(live_settings, short_sha=snap["build_commit_short"], locale=locale),
                    "curator_strip_html": _build_curator_strip_html(
                        curator_name=snap["curator_name"],
                        curator_handle=snap["curator_handle"],
                        locale=locale,
                    ),
                    "pipeline_mount_html": render_pipeline_mount_html(pipeline_data),
                    "sources_rows_html": _build_sources_rows_html(locale),
                    "footer_links_html": _build_footer_links_html(live_settings, locale=locale),
                },
                headers={"Vary": "Cookie, Authorization, Accept-Language"},
            )

        return await asyncio.to_thread(_render)

    return [Route("/about", public_about_route, methods=["GET", "HEAD"])]
