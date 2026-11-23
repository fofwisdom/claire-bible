"""Contract tests and static guardrails for the unified Console UI system."""

from __future__ import annotations

import os
import re
import time
from html.parser import HTMLParser
from pathlib import Path
from types import SimpleNamespace

import pytest
from starlette.applications import Starlette
from starlette.routing import Route
from starlette.testclient import TestClient

from claire.api import server
from claire.api.accounts_routes import console_ui
from claire.core.config import Settings
from claire.store import accounts as accounts_store
from claire.store import db as dbm
from claire.store.knowledge_base import KnowledgeBaseInfo, KnowledgeBaseManager
from claire.store.secrets import reset_secret_service
from claire.store.token_store import get_token_store, reset_token_stores

ROOT = Path(__file__).resolve().parents[1]
PUBLIC_URL = "http://127.0.0.1:8765"
CARD_CLASSES = frozenset({"card", "console-subcard", "pref-card", "claire-card"})
VOID_HTML_TAGS = frozenset({"area", "base", "br", "col", "embed", "hr", "img", "input", "link", "meta", "param", "source", "track", "wbr"})
STYLE_ATTR_RE = re.compile(r'style\s*=\s*(?:"([^"]*)"|\'([^\']*)\')', re.IGNORECASE)
FAKE_CARD_BG_RE = re.compile(r"background\s*:\s*var\(--sec-bg\)", re.IGNORECASE)
FAKE_CARD_BORDER_RE = re.compile(r"border\s*:\s*1px\s+solid\b", re.IGNORECASE)


class _ConsoleHtmlInspector(HTMLParser):
    """Inspect rendered console HTML for shell assets, containers, and nested cards."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.stylesheets: list[str] = []
        self.scripts: list[str] = []
        self.ids: set[str] = set()
        self.nested_cards: list[tuple[str, str]] = []
        self._stack: list[tuple[str, bool, str]] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        self._process_tag(tag, attrs, self_closing=(tag.lower() in VOID_HTML_TAGS))

    def handle_startendtag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        self._process_tag(tag, attrs, self_closing=True)

    def handle_endtag(self, tag: str) -> None:
        tag_lower = tag.lower()
        for idx in range(len(self._stack) - 1, -1, -1):
            if self._stack[idx][0] == tag_lower:
                del self._stack[idx:]
                break

    def _process_tag(self, tag: str, attrs: list[tuple[str, str | None]], *, self_closing: bool) -> None:
        tag_lower = tag.lower()
        attr_map = {k.lower(): (v or "") for k, v in attrs}
        el_id = attr_map.get("id", "")
        if el_id:
            self.ids.add(el_id)
        if tag_lower == "link" and "stylesheet" in attr_map.get("rel", "").split():
            href = attr_map.get("href", "")
            if href:
                self.stylesheets.append(href)
        if tag_lower == "script" and attr_map.get("src"):
            self.scripts.append(attr_map["src"])

        classes = set(attr_map.get("class", "").split())
        is_card = bool(classes & CARD_CLASSES)
        desc = f"{tag_lower}#{el_id}" if el_id else f"{tag_lower}.{'.'.join(sorted(classes))}"
        if is_card:
            for _, parent_is_card, parent_desc in self._stack:
                if parent_is_card:
                    self.nested_cards.append((parent_desc, desc))
                    break
        if not self_closing:
            self._stack.append((tag_lower, is_card, desc))


@pytest.fixture(autouse=True)
def _isolate_env(monkeypatch: pytest.MonkeyPatch):
    reset_secret_service()
    reset_token_stores()
    for key in list(os.environ):
        if key.startswith("CLAIRE_"):
            monkeypatch.delenv(key, raising=False)
    yield
    reset_secret_service()
    reset_token_stores()


def _upsert_account(conn, account_id: int, public_id: int, username: str, role: str) -> None:
    now = time.time()
    conn.execute(
        "INSERT INTO accounts (id, public_id, username, role, status, display_name, profile_public, created_at, activated_at, updated_at) VALUES (?, ?, ?, ?, 'active', ?, 1, ?, ?, ?) ON CONFLICT(id) DO UPDATE SET public_id=excluded.public_id, username=excluded.username, role=excluded.role, status='active', profile_public=1, updated_at=excluded.updated_at",
        (account_id, public_id, username, role, username.capitalize(), now, now, now),
    )
    conn.commit()


def _insert_theme(tm: KnowledgeBaseManager, settings: Settings, *, kb_id: int, seq: int, number: int, label: str, account_id: int, is_personal: bool) -> KnowledgeBaseInfo:
    conn = dbm.connect(settings.db_file)
    now = time.time()
    try:
        conn.execute(
            "INSERT INTO knowledge_bases (id, seq, number, label, description, icon, is_personal, is_public, account_id, db_path, default_focus, created_at, updated_at) VALUES (?, ?, ?, ?, ?, 'KB', ?, 1, ?, ?, '', ?, ?)",
            (kb_id, seq, number, label, f"Desc {label}", 1 if is_personal else 0, account_id, str(settings.db_file), now, now),
        )
        conn.commit()
    finally:
        conn.close()
    tm.reload()
    return tm.get_kb(kb_id)


@pytest.fixture
def console_env(tmp_path: Path):
    data_dir = tmp_path / "data"
    data_dir.mkdir(parents=True)
    db_file = data_dir / "claire.db"

    conn = dbm.connect(db_file)
    try:
        dbm.init_db(conn)
        _upsert_account(conn, 1, (1 << 57) + 101, "admin_operator", "admin")
        _upsert_account(conn, 2, (1 << 57) + 202, "alice_member", "member")
        accounts_store.AccountStore(conn).set_setting("mode", "network", updated_by=1)
    finally:
        conn.close()

    settings = Settings(
        CLAIRE_DB_PATH=str(db_file),
        CLAIRE_DATA_DIR=str(data_dir),
        CLAIRE_PROVIDER="mock",
        CLAIRE_ENVIRONMENT="development",
        CLAIRE_PUBLIC_URL=PUBLIC_URL,
        CLAIRE_ANONYMOUS_READONLY=True,
        CLAIRE_MULTI_THEME=True,
    )
    tm = KnowledgeBaseManager(settings)
    shared_kb = _insert_theme(tm, settings, kb_id=11, seq=1, number=(1 << 56) | 11, label="Shared Research", account_id=1, is_personal=False)
    alice_def = _insert_theme(tm, settings, kb_id=20, seq=2, number=(2 << 56) | 20, label="Alice Default", account_id=2, is_personal=True)

    tok_store = get_token_store(data_dir=data_dir)
    admin_owner_tok = tok_store.create_token(name="admin-owner", scope="owner", account_id=1)["token"]
    alice_owner_tok = tok_store.create_token(name="alice-owner", scope="owner", account_id=2)["token"]

    app = server.create_app(settings, SimpleNamespace(provider=SimpleNamespace(name="mock")))
    client = TestClient(app, base_url=PUBLIC_URL)
    return SimpleNamespace(
        app=app,
        client=client,
        shared_kb=shared_kb,
        alice_def=alice_def,
        admin_owner={"Authorization": f"Bearer {admin_owner_tok}"},
        alice_owner={"Authorization": f"Bearer {alice_owner_tok}"},
    )


def _find_starlette_routes(app: object) -> list[Route]:
    cur = app
    while cur is not None and not isinstance(cur, Starlette):
        cur = getattr(cur, "app", None)
    assert isinstance(cur, Starlette), "Could not locate Starlette application from server.create_app"
    return [route for route in cur.routes if isinstance(route, Route)]


# The console pages outside the four console prefixes (the token links under /support/ are archives, not pages).
CONSOLE_PAGES_OUTSIDE_PREFIXES = ("/support/bundles",)


def _is_console_route_path(path: str) -> bool:
    return path in ("/account", "/settings", "/admin", "/moderation", *CONSOLE_PAGES_OUTSIDE_PREFIXES) or path.startswith(("/account/", "/settings/", "/admin/", "/moderation/"))


def _inspect_html(html_text: str) -> _ConsoleHtmlInspector:
    inspector = _ConsoleHtmlInspector()
    inspector.feed(html_text)
    inspector.close()
    return inspector


def test_console_ui_card_and_form_card_builders_escape_and_validate() -> None:
    card = console_ui.console_card(
        "Title",
        "<p>Body</p>",
        id='c"1',
        icon="lock",
        badge_html='<span class="pref-pill">ok</span>',
        hint_html="Hint text",
        actions_html='<button type="button" class="pref-btn">Act</button>',
        split=False,
        aria_label='Card <Label>',
        extra_class="extra-cls",
        attrs={"data-kind": 'a"b', "data-flag": True, "data-off": False, "data-none": None},
    )
    assert '<section class="console-subcard no-split extra-cls"' in card
    assert 'id="c&quot;1"' in card
    assert 'aria-label="Card &lt;Label&gt;"' in card
    assert 'data-kind="a&quot;b"' in card
    assert " data-flag" in card
    assert "data-off" not in card
    assert "data-none" not in card
    assert '<div class="console-card-head"><h2><span data-icon="lock"></span> Title <span class="pref-pill">ok</span></h2><button type="button" class="pref-btn">Act</button></div>' in card
    assert '<p class="hint">Hint text</p><p>Body</p></section>' in card

    form = console_ui.console_form_card("Form", "<input/>", action="/save?x=1&y=2", method="post")
    assert form.startswith('<form class="console-subcard" action="/save?x=1&amp;y=2" method="post"><h2>Form</h2><input/></form>')

    with pytest.raises(ValueError, match="Unsupported console card tag"):
        console_ui.console_card("Bad", "<p>x</p>", tag="script")
    with pytest.raises(ValueError, match="Invalid HTML attribute name"):
        console_ui.console_card("Bad", "<p>x</p>", attrs={"bad attr": "1"})


def test_console_ui_host_and_control_builders() -> None:
    admin_host = console_ui.console_host("admin-providers-host", loading_text="<Loading>", panel_host="admin:providers", outer_id="outer<1>")
    assert admin_host == '<div id="outer&lt;1&gt;"><div class="console-host" id="admin-providers-host" data-admin-panel-host="providers" data-console-host="providers"><p class="hint">&lt;Loading&gt;</p></div></div>'
    assert "console-subcard" not in admin_host
    assert 'class="card"' not in admin_host

    settings_host = console_ui.console_host("settings-tokens-host", panel_host="settings:tokens")
    assert 'data-settings-panel-host="tokens"' in settings_host
    assert 'data-console-host="tokens"' in settings_host

    inferred_host = console_ui.console_host("settings-kb-host", panel_host="kb")
    assert 'data-settings-panel-host="kb"' in inferred_host

    default_host = console_ui.console_host("custom-host")
    assert 'data-console-host="custom-host"' in default_host

    field_html = console_ui.console_field("API <Key>", '<input id="k1" class="pref-input"/>', for_id='k"1', hint_html="Required")
    assert '<label class="console-field" for="k&quot;1">API &lt;Key&gt;<input id="k1" class="pref-input"/><small class="console-field-hint">Required</small></label>' == field_html

    toggle_html = console_ui.console_toggle_row("Enable <X>", "sw-1", desc_html="Details", checked=True, disabled=True, attrs={"data-test": "v"})
    assert '<div class="pref-row">' in toggle_html
    assert '<label for="sw-1"><strong>Enable &lt;X&gt;</strong></label><span>Details</span>' in toggle_html
    assert '<input id="sw-1" type="checkbox" switch checked disabled data-test="v"/>' in toggle_html
    assert "claire-switch" not in toggle_html

    table_html = console_ui.console_table(["Col <1>", "Col 2"], "<tr><td>A</td><td>B</td></tr>", tbody_id="tb-1", aria_label='Table "A"', caption_html="Cap", extra_class="dense")
    assert table_html == '<div class="pref-table-wrap"><table class="pref-table dense" aria-label="Table &quot;A&quot;"><caption>Cap</caption><thead><tr><th>Col &lt;1&gt;</th><th>Col 2</th></tr></thead><tbody id="tb-1"><tr><td>A</td><td>B</td></tr></tbody></table></div>'

    notice_html = console_ui.console_notice("<strong>Alert</strong>", tone='warn"x')
    assert notice_html == '<div class="pref-notice" data-tone="warn&quot;x"><strong>Alert</strong></div>'

    kv_html = console_ui.console_kv([("<Key>", "<Val>"), {"label_html": "<b>H</b>", "value": "<Plain>", "dt_attrs": {"data-k": "1"}, "attrs": {"data-v": "2"}}])
    assert kv_html == '<dl class="console-kv"><dt>&lt;Key&gt;</dt><dd>&lt;Val&gt;</dd><dt data-k="1"><b>H</b></dt><dd data-v="2">&lt;Plain&gt;</dd></dl>'

    assert console_ui.console_result(id="res-1") == '<p class="result" role="status" aria-live="polite" id="res-1"></p>'
    assert console_ui.console_stat_row(["<div>1</div>", "<div>2</div>"]) == '<div class="pref-stat-row"><div>1</div><div>2</div></div>'
    assert console_ui.console_actions('<button class="pref-btn">Save</button>') == '<div class="console-actions"><button class="pref-btn">Save</button></div>'
    assert console_ui.console_pill("Active <Now>", tone="ok") == '<span class="pref-pill" data-tone="ok">Active &lt;Now&gt;</span>'


def test_all_console_html_routes_match_shell_contract_and_isolation(console_env) -> None:
    routes = _find_starlette_routes(console_env.app)
    get_paths = sorted({r.path for r in routes if "GET" in (r.methods or set()) and _is_console_route_path(r.path)})
    assert "/moderation/kb/{kb_number:int}" in get_paths

    shared_number = str(console_env.shared_kb.number)
    resolved_paths = [p.replace("{kb_number:int}", shared_number) for p in get_paths]

    rendered_admin_pages: list[str] = []
    for path in resolved_paths:
        resp = console_env.client.get(path, headers=console_env.admin_owner, follow_redirects=False)
        assert resp.status_code in (200, 302, 308), (path, resp.status_code)
        if resp.status_code != 200:
            continue
        rendered_admin_pages.append(path)
        assert "text/html" in resp.headers.get("content-type", ""), path
        assert "var(--error)" not in resp.text, path
        inspector = _inspect_html(resp.text)
        assert "/static/css/console_ui.css" in inspector.stylesheets, path
        assert "/static/js/console_ui.js" in inspector.scripts, path
        assert "/static/js/console_shell.js" in inspector.scripts, path
        assert "console-section-body" in inspector.ids, path
        assert "console-sidebar" in inspector.ids, path
        assert inspector.nested_cards == [], (path, inspector.nested_cards)

    assert len(rendered_admin_pages) >= 24
    assert f"/moderation/kb/{shared_number}" in rendered_admin_pages
    assert "/admin/server" in rendered_admin_pages
    assert "/account/profile" in rendered_admin_pages

    css_resp = console_env.client.get("/static/css/console_ui.css")
    assert css_resp.status_code == 200
    assert "var(--error)" not in css_resp.text

    non_admin_paths = [p for p in resolved_paths if p in ("/account", "/settings", "/moderation", *CONSOLE_PAGES_OUTSIDE_PREFIXES) or p.startswith(("/account/", "/settings/", "/moderation/kb"))]
    rendered_member_pages: list[str] = []
    for path in non_admin_paths:
        resp = console_env.client.get(path, headers=console_env.alice_owner, follow_redirects=False)
        assert resp.status_code in (200, 302, 308), (path, resp.status_code)
        if resp.status_code != 200:
            continue
        rendered_member_pages.append(path)
        assert "var(--error)" not in resp.text, path
        assert "/admin/" not in resp.text, path
        assert "console_admin.js" not in resp.text, path
        assert "console.js.admin." not in resp.text, path
        inspector = _inspect_html(resp.text)
        assert "/static/css/console_ui.css" in inspector.stylesheets, path
        assert "/static/js/console_ui.js" in inspector.scripts, path
        assert "/static/js/console_shell.js" in inspector.scripts, path
        assert "console-section-body" in inspector.ids, path
        assert "console-sidebar" in inspector.ids, path
        assert inspector.nested_cards == [], (path, inspector.nested_cards)

    assert "/account/tokens" in rendered_member_pages and "/support/bundles" in rendered_member_pages
    assert f"/moderation/kb/{shared_number}" in rendered_member_pages


def test_console_static_sources_have_no_legacy_switch_error_var_or_fake_cards() -> None:
    js_files = [
        ROOT / "src" / "bible" / "static" / "js" / "console_kb.js",
        ROOT / "src" / "bible" / "static" / "js" / "console_admin.js",
        ROOT / "src" / "bible" / "static" / "js" / "console_account.js",
        ROOT / "src" / "bible" / "static" / "js" / "console_invites.js",
        ROOT / "src" / "bible" / "static" / "js" / "console_features.js",
        ROOT / "src" / "bible" / "static" / "js" / "console_syndication.js",
        ROOT / "src" / "bible" / "static" / "js" / "manage_panels.js",
    ]
    partial_files = sorted((ROOT / "src" / "claire" / "api" / "templates" / "partials" / "settings_syndication").glob("*.html"))
    assert partial_files, "Expected syndication partial templates"

    css_file = ROOT / "src" / "bible" / "static" / "css" / "console_ui.css"
    assert "var(--error)" not in css_file.read_text(encoding="utf-8")

    for path in [*js_files, *partial_files]:
        assert path.is_file(), f"Missing console source file: {path}"
        text = path.read_text(encoding="utf-8")
        rel = path.relative_to(ROOT)
        assert "claire-switch" not in text, f"Forbidden claire-switch in {rel}"
        assert "var(--error)" not in text, f"Forbidden var(--error) in {rel}"
        for match in STYLE_ATTR_RE.finditer(text):
            style_val = match.group(1) if match.group(1) is not None else (match.group(2) or "")
            assert not (FAKE_CARD_BG_RE.search(style_val) and FAKE_CARD_BORDER_RE.search(style_val)), f"Legacy inline-styled fake card in {rel}: {style_val}"
