"""Public root observatory data builder and HTML renderer (``DESIGN-PUBLIC-ROOT-DIRECTORY-KB-20261127``).

This module reads only public instance counts, active themes, recent cross-server exchange activity, and active (non-blocked) known peer servers. It never exposes blocked domains, software versions, private accounts, private or hidden documents, API keys, or admin diagnostics.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

from ...federation.knowledge import known_servers, remote_kbs
from ...i18n import t
from ...i18n.locales import LocaleCode
from ...store import accounts as accounts_store
from ...store import db as dbm
from ...store import federation as federation_store
from ..accounts_routes.common import escape
from ..security import WebRuntimeConfig

__all__ = [
    "build_federation_observatory_snapshot",
    "render_observatory_stage_html",
    "render_observatory_themes_html",
]

_BRIDGE_THEME_LIMIT = 4
_VISIBLE_PEER_LIMIT = 5
_THEME_GRID_LIMIT = 6


def _scalar_int(conn: sqlite3.Connection, sql: str, params: tuple[Any, ...] = ()) -> int:
    try:
        row = conn.execute(sql, params).fetchone()
    except sqlite3.OperationalError:
        return 0
    return int(row[0]) if row is not None and row[0] is not None else 0


def _resolve_instance_domain(settings: Any, origin: str) -> str:
    try:
        authority = WebRuntimeConfig.from_settings(settings).expected_authority.strip()
        if authority:
            return authority
    except Exception:  # noqa: BLE001
        pass
    netloc = urlsplit(str(origin or "")).netloc.strip()
    return netloc or "claire.local"


def _is_blocked_domain(conn: sqlite3.Connection, domain: str) -> bool:
    try:
        return federation_store.blocking_domain(conn, domain) is not None
    except sqlite3.OperationalError:
        return False


def build_federation_observatory_snapshot(
    db_file: Path | None,
    *,
    settings: Any,
    origin: str,
    theme_views: list[dict[str, Any]],
    recent: list[dict[str, Any]],
) -> dict[str, Any]:
    """Assemble strictly public observatory data for ``/`` from ``claire.db``, active themes, and recent exchanges."""
    instance_domain = _resolve_instance_domain(settings, origin)
    mode = "network"
    public_profiles = 0
    local_shared_kbs = 0
    local_docs = 0
    known_rows: list[known_servers.KnownServer] = []
    remote_rows: list[remote_kbs.RemoteKnowledgeBase] = []
    blocked_cache: dict[str, bool] = {}

    if db_file is not None and Path(db_file).is_file():
        conn = dbm.connect_existing(Path(db_file), readonly=True)
        try:
            try:
                mode = accounts_store.AccountStore(conn).server_mode()
            except Exception:  # noqa: BLE001
                mode = "network"
            public_profiles = _scalar_int(conn, "SELECT COUNT(*) FROM accounts WHERE profile_public = 1 AND status = 'active'")
            local_shared_kbs = _scalar_int(conn, "SELECT COUNT(*) FROM knowledge_bases WHERE is_personal = 0 AND is_public = 1")
            local_docs = _scalar_int(
                conn,
                "SELECT COUNT(*) FROM documents d JOIN knowledge_bases k ON k.id = d.kb_id LEFT JOIN accounts a ON a.id = k.account_id WHERE k.is_public = 1 AND (k.is_personal = 0 OR (a.status = 'active' AND (? = 'single' OR a.profile_public = 1))) AND COALESCE(d.hidden, 0) = 0 AND COALESCE(d.extraction_status, 'extracted') = 'extracted'",
                (mode,),
            )
            try:
                raw_known = known_servers.list_servers(conn)
            except sqlite3.OperationalError:
                raw_known = []
            for srv in raw_known:
                blocked_cache[srv.domain] = _is_blocked_domain(conn, srv.domain)
                if not blocked_cache[srv.domain]:
                    known_rows.append(srv)

            try:
                raw_remote = remote_kbs.list_all(conn)
            except sqlite3.OperationalError:
                raw_remote = []
            for rkb in raw_remote:
                if rkb.domain not in blocked_cache:
                    blocked_cache[rkb.domain] = _is_blocked_domain(conn, rkb.domain)
                if not blocked_cache[rkb.domain]:
                    remote_rows.append(rkb)
        finally:
            conn.close()

    # Map remote KBs and theme bridges by active peer domain.
    remote_by_domain: dict[str, list[dict[str, str]]] = {}
    for rkb in remote_rows:
        remote_by_domain.setdefault(rkb.domain, []).append({"label": rkb.label, "url": rkb.url})

    themes_by_domain: dict[str, list[dict[str, str]]] = {}
    bridge_candidates: list[dict[str, Any]] = []
    for tv in theme_views:
        peer_domains = sorted({str(kb["server"]) for kb in tv.get("knowledge_bases", []) if kb.get("remote") and kb.get("server") and not blocked_cache.get(str(kb["server"]), False)})
        for domain in peer_domains:
            entry = {"number": str(tv["number"]), "label": str(tv["label"])}
            themes_by_domain.setdefault(domain, [])
            if all(existing["number"] != entry["number"] for existing in themes_by_domain[domain]):
                themes_by_domain[domain].append(entry)
        bridge_candidates.append(
            {
                "number": str(tv["number"]),
                "label": str(tv["label"]),
                "knowledge_type": str(tv.get("knowledge_type") or "real"),
                "shared_knowledge_bases": int(tv.get("shared_knowledge_bases") or 0),
                "remote_knowledge_bases": int(tv.get("remote_knowledge_bases") or 0),
                "visible_contributions": int(tv.get("visible_contributions") or 0),
                "top_entities": [str(name) for name in list(tv.get("top_entities") or [])[:4]],
                "peer_domains": peer_domains,
            }
        )

    # Prefer themes that already bridge remote knowledge bases, then contribution volume.
    bridge_candidates.sort(key=lambda item: (-(1 if item["remote_knowledge_bases"] > 0 else 0), -item["remote_knowledge_bases"], -item["visible_contributions"]))
    bridge_themes = bridge_candidates[:_BRIDGE_THEME_LIMIT]

    recent_stats: dict[str, dict[str, int]] = {}
    total_curations = 0
    total_contributions = 0
    for item in recent:
        domain = str(item.get("server") or "").strip()
        if not domain or blocked_cache.get(domain, False):
            continue
        bucket = recent_stats.setdefault(domain, {"curation": 0, "accepted_contribution": 0, "remote_knowledge_base": 0})
        kind = str(item.get("kind") or "")
        if kind in bucket:
            bucket[kind] += 1
        if kind == "curation":
            total_curations += 1
        elif kind == "accepted_contribution":
            total_contributions += 1

    known_by_domain = {srv.domain: srv for srv in known_rows}
    all_active_domains = sorted(set(known_by_domain.keys()) | set(remote_by_domain.keys()) | set(themes_by_domain.keys()) | set(recent_stats.keys()))

    peers: list[dict[str, Any]] = []
    for domain in all_active_domains:
        srv = known_by_domain.get(domain)
        domain_kbs = remote_by_domain.get(domain, [])
        domain_themes = themes_by_domain.get(domain, [])
        stats = recent_stats.get(domain, {"curation": 0, "accepted_contribution": 0, "remote_knowledge_base": 0})
        shared_kb_count = max(int(srv.shared_kb_count or 0) if srv is not None else 0, len(domain_kbs))
        peers.append(
            {
                "domain": domain,
                "shared_kb_count": shared_kb_count,
                "curations_in": stats["curation"],
                "contributions_out": stats["accepted_contribution"],
                "remote_kb_events": stats["remote_knowledge_base"],
                "bridged_themes": domain_themes[:4],
                "primary_url": f"/themes/{domain_themes[0]['number']}/" if domain_themes else "/themes",
                "remote_kbs": domain_kbs[:3],
            }
        )

    peers.sort(key=lambda item: (-(item["curations_in"] + item["contributions_out"] + len(item["bridged_themes"])), -item["shared_kb_count"], item["domain"]))

    return {
        "instance_domain": instance_domain,
        "mode": mode,
        "public_profiles": public_profiles,
        "local_shared_kbs": local_shared_kbs,
        "local_docs": local_docs,
        "bridge_themes": bridge_themes,
        "peers": peers,
        "cycle": {
            "peer_count": len(peers),
            "active_themes": len(theme_views),
            "remote_kb_total": len(remote_rows),
            "curations_count": total_curations,
            "contributions_count": total_contributions,
            "recent_exchange_count": len(recent),
            "cold_start": len(peers) == 0,
        },
    }


def _knowledge_type_tag_html(knowledge_type: str, *, locale: LocaleCode) -> str:
    is_fictional = knowledge_type == "fictional"
    cls = "fic" if is_fictional else "acc"
    label = t("kb.knowledge_type.fictional" if is_fictional else "kb.knowledge_type.real", locale=locale)
    return f'<span class="claire-tag {cls}">{escape(label)}</span>'


def render_observatory_stage_html(
    snapshot: dict[str, Any],
    *,
    locale: LocaleCode,
    workspace_href: str | None = None,
) -> str:
    """Render Tier 1 of ``/``: the Calm Federated Observatory stage, directed topology, reflux arc, and declarative hovercards."""
    cycle = snapshot["cycle"]
    cold_start = bool(cycle["cold_start"])
    status_state = "standby" if cold_start else "active"
    status_label = t("home_obs.status_standby" if cold_start else "home_obs.status_active", locale=locale)

    ws_cta_html = ""
    if workspace_href:
        ws_cta_html = f'<a class="claire-btn pri obs-ws-cta" href="{escape(workspace_href)}"><span data-icon="home"></span><span>{escape(t("directory.my_workspace", locale=locale))}</span></a>'

    header_html = (
        '<header class="obs-header">'
        '<div class="obs-identity">'
        '<div class="obs-eyebrow">'
        f'<span class="obs-status-dot" data-state="{status_state}" aria-hidden="true"></span>'
        f'<code class="obs-domain">{escape(snapshot["instance_domain"])}</code>'
        f'<span class="obs-status-text">{escape(status_label)}</span>'
        '</div>'
        f'<h1 id="home-obs-h1" class="obs-title">{escape(t("home_obs.title", locale=locale))}</h1>'
        f'<p class="obs-subtitle">{escape(t("home_obs.subtitle", locale=locale))}</p>'
        f'{ws_cta_html}'
        '</div>'
        '<dl class="obs-metrics">'
        f'<div class="obs-metric"><dt>{escape(t("home_obs.metric.peers", locale=locale))}</dt><dd>{int(cycle["peer_count"]):,}</dd></div>'
        f'<div class="obs-metric"><dt>{escape(t("home_obs.metric.themes", locale=locale))}</dt><dd>{int(cycle["active_themes"]):,}</dd></div>'
        f'<div class="obs-metric"><dt>{escape(t("home_obs.metric.exchanges", locale=locale))}</dt><dd>{int(cycle["recent_exchange_count"]):,}</dd></div>'
        '</dl>'
        '</header>'
    )

    local_docs_str = f"{int(snapshot['local_docs']):,}"
    local_kbs_str = f"{int(snapshot['local_shared_kbs']):,}"
    local_profiles_str = f"{int(snapshot['public_profiles']):,}"
    local_lane_html = (
        '<div class="obs-lane" data-obs-lane="local">'
        f'<div class="obs-lane-head"><span class="obs-lane-cue obs-cue-local" aria-hidden="true"></span><span>{escape(t("home_obs.lane.local", locale=locale))}</span></div>'
        '<div class="obs-node obs-node-local" data-obs-node="local">'
        f'<div class="obs-node-top"><span data-icon="database"></span><code class="obs-node-code">{escape(snapshot["instance_domain"])}</code></div>'
        '<ul class="obs-local-facts">'
        f'<li>{escape(t("home_obs.local.docs", locale=locale, count=local_docs_str))}</li>'
        f'<li>{escape(t("home_obs.local.kbs", locale=locale, count=local_kbs_str))}</li>'
        f'<li>{escape(t("home_obs.local.profiles", locale=locale, count=local_profiles_str))}</li>'
        '</ul>'
        f'<p class="obs-node-sub">{escape(t("home_obs.local.sub", locale=locale))}</p>'
        '</div>'
        '</div>'
    )

    bridge_themes = snapshot.get("bridge_themes", [])
    hovercards: list[str] = []
    if bridge_themes:
        theme_nodes_html_parts: list[str] = []
        for th in bridge_themes:
            num = th["number"]
            hint_id = f"obs-hc-theme-{num}"
            peers_attr = ",".join(th["peer_domains"])
            theme_nodes_html_parts.append(
                f'<a class="obs-node obs-node-theme" href="/themes/{escape(num)}/" data-obs-node="theme-{escape(num)}" data-obs-peers="{escape(peers_attr)}" interestfor="{escape(hint_id)}">'
                f'<span class="obs-node-top"><span class="obs-node-title">{escape(th["label"])}</span>{_knowledge_type_tag_html(th["knowledge_type"], locale=locale)}</span>'
                f'<span class="obs-node-meta">{escape(t("home_obs.theme.meta", locale=locale, local=th["shared_knowledge_bases"], remote=th["remote_knowledge_bases"], contribs=th["visible_contributions"]))}</span>'
                '</a>'
            )
            entities_markup = "".join(f'<span class="claire-tag">{escape(name)}</span>' for name in th["top_entities"])
            hovercards.append(
                f'<div id="{escape(hint_id)}" popover="hint" class="obs-hovercard">'
                f'<div class="obs-hc-head"><strong>{escape(th["label"])}</strong>{_knowledge_type_tag_html(th["knowledge_type"], locale=locale)}</div>'
                f'<p class="obs-hc-meta">{escape(t("home_obs.theme.meta", locale=locale, local=th["shared_knowledge_bases"], remote=th["remote_knowledge_bases"], contribs=th["visible_contributions"]))}</p>'
                + (f'<div class="obs-hc-tags">{entities_markup}</div>' if entities_markup else "")
                + '</div>'
            )
        themes_lane_body = "".join(theme_nodes_html_parts)
    else:
        themes_lane_body = (
            '<div class="obs-node obs-node-standby">'
            f'<strong class="obs-node-title">{escape(t("home_obs.theme.empty_title", locale=locale))}</strong>'
            f'<p class="obs-node-sub">{escape(t("home_obs.theme.empty_desc", locale=locale))}</p>'
            '</div>'
        )

    themes_lane_html = (
        '<div class="obs-lane" data-obs-lane="themes">'
        f'<div class="obs-lane-head"><span class="obs-lane-cue obs-cue-theme" aria-hidden="true"></span><span>{escape(t("home_obs.lane.themes", locale=locale))}</span></div>'
        f'<div class="obs-node-stack">{themes_lane_body}</div>'
        '</div>'
    )

    peers = snapshot.get("peers", [])
    visible_peers = peers[:_VISIBLE_PEER_LIMIT]
    if visible_peers:
        peer_nodes_parts: list[str] = []
        for idx, peer in enumerate(visible_peers):
            hint_id = f"obs-hc-peer-{idx}"
            domain = peer["domain"]
            peer_nodes_parts.append(
                f'<a class="obs-node obs-node-peer" href="{escape(peer["primary_url"])}" data-obs-node="peer-{idx}" data-obs-peer="{escape(domain)}" interestfor="{escape(hint_id)}">'
                f'<span class="obs-node-top"><span data-icon="globe"></span><code class="obs-node-code">{escape(domain)}</code></span>'
                f'<span class="obs-node-meta">{escape(t("home_obs.peer.kbs", locale=locale, count=peer["shared_kb_count"]))} · {escape(t("home_obs.peer.flow", locale=locale, curations=peer["curations_in"], contribs=peer["contributions_out"]))}</span>'
                '</a>'
            )
            hc_themes = ""
            if peer["bridged_themes"]:
                hc_themes = (
                    f'<div class="obs-hc-section"><span>{escape(t("home_obs.peer.themes_label", locale=locale))}</span>'
                    '<div class="obs-hc-tags">'
                    + "".join(f'<span class="claire-tag">{escape(bt["label"])}</span>' for bt in peer["bridged_themes"])
                    + '</div></div>'
                )
            hc_kbs = ""
            if peer["remote_kbs"]:
                hc_kbs = (
                    f'<div class="obs-hc-section"><span>{escape(t("home_obs.peer.remote_kbs_label", locale=locale))}</span>'
                    '<ul class="obs-hc-list">'
                    + "".join(f'<li><a href="{escape(kb["url"])}" target="_blank" rel="noopener noreferrer">{escape(kb["label"])}</a></li>' for kb in peer["remote_kbs"])
                    + '</ul></div>'
                )
            hovercards.append(
                f'<div id="{escape(hint_id)}" popover="hint" class="obs-hovercard">'
                f'<div class="obs-hc-head"><span data-icon="globe"></span><code class="obs-node-code">{escape(domain)}</code></div>'
                f'<p class="obs-hc-meta">{escape(t("home_obs.peer.kbs", locale=locale, count=peer["shared_kb_count"]))} · {escape(t("home_obs.peer.flow", locale=locale, curations=peer["curations_in"], contribs=peer["contributions_out"]))}</p>'
                f'{hc_themes}{hc_kbs}'
                '</div>'
            )
        overflow_count = len(peers) - len(visible_peers)
        if overflow_count > 0:
            peer_nodes_parts.append(f'<div class="obs-peer-more">{escape(t("home_obs.peer.more", locale=locale, count=overflow_count))}</div>')
        peers_lane_body = "".join(peer_nodes_parts)
    else:
        peers_lane_body = (
            '<div class="obs-node obs-node-standby" data-obs-standby="true">'
            '<span class="obs-node-top"><span data-icon="globe"></span>'
            f'<strong class="obs-node-title">{escape(t("home_obs.standby.title", locale=locale))}</strong></span>'
            f'<p class="obs-node-sub">{escape(t("home_obs.standby.desc", locale=locale))}</p>'
            '</div>'
        )

    peers_lane_html = (
        '<div class="obs-lane" data-obs-lane="peers">'
        f'<div class="obs-lane-head"><span class="obs-lane-cue obs-cue-peer" aria-hidden="true"></span><span>{escape(t("home_obs.lane.peers", locale=locale))}</span></div>'
        f'<div class="obs-node-stack">{peers_lane_body}</div>'
        '</div>'
    )

    reflux_text = (
        t("home_obs.reflux.standby", locale=locale)
        if cold_start
        else t(
            "home_obs.reflux.label",
            locale=locale,
            curations=cycle["curations_count"],
            contribs=cycle["contributions_count"],
            remote_kbs=cycle["remote_kb_total"],
        )
    )
    reflux_html = (
        '<div class="obs-reflux">'
        '<svg class="obs-reflux-svg" viewBox="0 0 600 28" preserveAspectRatio="none" aria-hidden="true" focusable="false">'
        '<path class="obs-reflux-path" d="M 592 4 C 520 24, 80 24, 12 6" fill="none"/>'
        '<polyline class="obs-reflux-arrow" points="20,2 10,6 18,13" fill="none"/>'
        '</svg>'
        f'<div class="obs-reflux-caption"><span data-icon="repeat"></span><span>{escape(reflux_text)}</span></div>'
        '</div>'
    )

    topology_html = (
        f'<div class="obs-topology" role="region" aria-label="{escape(t("home_obs.diagram_label", locale=locale))}">'
        '<div class="obs-flow-shell">'
        '<svg class="obs-flow-svg" aria-hidden="true" focusable="false"></svg>'
        '<div class="obs-flow-grid">'
        f'{local_lane_html}{themes_lane_html}{peers_lane_html}'
        '</div>'
        '</div>'
        f'{reflux_html}'
        '</div>'
    )

    return f'<section class="obs-stage" id="home-observatory" aria-labelledby="home-obs-h1">{header_html}{topology_html}{"".join(hovercards)}</section>'


def render_observatory_themes_html(
    theme_views: list[dict[str, Any]],
    *,
    locale: LocaleCode,
    public_profiles: int,
    workspace_href: str | None = None,
) -> str:
    """Render Tier 2 of ``/``: subgrid-aligned active federated themes and a compact directory entry bar."""
    parts: list[str] = []
    if theme_views:
        cards: list[str] = []
        for view in theme_views[:_THEME_GRID_LIMIT]:
            entities_html = "".join(f'<span class="claire-tag">{escape(str(name))}</span>' for name in view.get("top_entities", [])[:4])
            kb_items: list[str] = []
            for kb in view.get("knowledge_bases", [])[:4]:
                if kb.get("url"):
                    target_attrs = ' target="_blank" rel="noopener noreferrer"' if kb.get("remote") else ""
                    label_html = f'<a href="{escape(kb["url"])}"{target_attrs}>{escape(kb["label"])}</a>'
                else:
                    label_html = escape(kb["label"])
                origin_label = kb["server"] if kb.get("remote") else t("themes.kb_personal" if kb.get("is_personal") else "themes.kb_shared", locale=locale)
                kb_items.append(f'<li>{label_html}<span class="themes-count">({escape(origin_label)})</span></li>')

            cards.append(
                '<article class="obs-theme-card">'
                '<div class="obs-theme-head">'
                f'<h3><a href="/themes/{escape(view["number"])}/">{escape(view["label"])}</a></h3>'
                f'<div class="obs-theme-badges">{_knowledge_type_tag_html(str(view["knowledge_type"]), locale=locale)}'
                f'<span class="obs-theme-counts">{escape(t("home_obs.theme.meta", locale=locale, local=view["shared_knowledge_bases"], remote=view["remote_knowledge_bases"], contribs=view["visible_contributions"]))}</span></div>'
                '</div>'
                f'<div class="obs-theme-entities">{entities_html}</div>'
                f'<ul class="themes-list obs-theme-kbs">{"".join(kb_items)}</ul>'
                '</article>'
            )

        parts.append(
            '<section class="obs-themes-section" id="home-themes" aria-labelledby="home-themes-h2">'
            f'<div class="obs-section-head"><h2 id="home-themes-h2"><span data-icon="folder"></span><span>{escape(t("home_obs.themes.heading", locale=locale))}</span></h2>'
            f'<a class="obs-more-link" href="/themes">{escape(t("home_obs.themes.all", locale=locale))} &rarr;</a></div>'
            f'<div class="obs-theme-grid">{"".join(cards)}</div>'
            '</section>'
        )

    if public_profiles == 0 and not theme_views:
        action_html = (
            f'<a class="claire-btn pri" href="{escape(workspace_href)}">{escape(t("directory.go_workspace", locale=locale))}</a>'
            if workspace_href
            else f'<a class="claire-btn pri" href="/login">{escape(t("directory.login", locale=locale))}</a>'
        )
        parts.append(
            '<section class="obs-directory-strip obs-directory-empty" id="home-directory-link" aria-label="'
            + escape(t("directory.title", locale=locale))
            + '">'
            '<div class="obs-dir-copy">'
            f'<strong>{escape(t("directory.empty_title", locale=locale))}</strong>'
            f'<span>{escape(t("directory.empty", locale=locale))}</span>'
            '</div>'
            f'<div class="obs-dir-actions"><a class="claire-btn sec" href="/directory"><span data-icon="users"></span><span>{escape(t("directory.title", locale=locale))}</span></a>{action_html}</div>'
            '</section>'
        )
    else:
        parts.append(
            '<section class="obs-directory-strip" id="home-directory-link" aria-label="'
            + escape(t("directory.title", locale=locale))
            + '">'
            f'<div class="obs-dir-copy"><span data-icon="users"></span><span>{escape(t("home_obs.directory_bar", locale=locale))}</span></div>'
            f'<div class="obs-dir-actions"><a class="obs-more-link" href="/directory">{escape(t("directory.title", locale=locale))} &rarr;</a></div>'
            '</section>'
        )

    return "".join(parts)
