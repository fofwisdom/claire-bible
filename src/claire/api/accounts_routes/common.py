"""계정 라우트 공용 헬퍼 (설계 §8.4–§8.7).

라우트 모듈(`setup`, `login`, `admin`, `me`)은 이 모듈만 거쳐 DB·쿠키·origin·화면을 다룬다. P3 레인이 모듈별로 나눠 고칠 수 있게 공용 규칙은 여기 한 곳에 둔다.
"""

from __future__ import annotations

import html
import logging
import math
import os
import re
import sqlite3
import string
import time
import unicodedata
from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

from starlette.requests import Request
from starlette.responses import HTMLResponse, JSONResponse, PlainTextResponse, Response

from ...accounts.username_policy import server_reserved_labels
from ...core.ports import get_web_ui_renderer
from ...i18n import SUPPORTED_LOCALES, browser_catalog_json, get_locale, interpolate, t
from ...i18n.catalog import BrowserCatalogScope
from ...store import auth_db
from ...store import client_db
from ...store import db as dbm
from .. import security
from ..principal import Principal, has_recent_auth

_log = logging.getLogger(__name__)
TEMPLATES_DIR = Path(__file__).resolve().parent.parent / "templates"
SESSION_CHANNELS = frozenset({"cookie", "x-session"})
DISPLAY_NAME_MAX_LENGTH = 64
SUMMARY_MAX_LENGTH = 500


@dataclass(frozen=True, slots=True)
class AccountsContext:
    """계정 라우트가 쓰는 설정. ``create_app``이 한 번 만든다."""

    db_file: Path
    auth_db_file: Path
    config: security.WebRuntimeConfig
    # §8.1-4 서버 예약 라벨(현재 호스트 설정값에서 뽑는다).
    server_reserved: frozenset[str]
    data_dir: Path | None = None
    # 계정 기능(번들·백업)이 앱 설정(공개 URL·테마 관리자)을 쓴다. 테스트 대역은 None일 수 있다.
    settings: Any = None
    settings_getter: Callable[[], Any] | None = None

    def current_settings(self) -> Any:
        """Return the active runtime settings when a live getter is bound."""
        if self.settings_getter is not None:
            return self.settings_getter()
        return self.settings

    @property
    def client_db_file(self) -> Path:
        """``client.db`` next to ``claire.db`` (DATA_STORE_LAYOUT_DESIGN.md, RD27)."""
        return client_db.client_db_path(self.db_file)


def _host_of(value: Any) -> str:
    raw = str(value or "").strip()
    if not raw:
        return ""
    if "://" in raw:
        parsed = urlsplit(raw)
        return parsed.netloc or parsed.path
    return raw.rstrip("/")


def server_hosts(settings: Any, config: security.WebRuntimeConfig) -> tuple[str, ...]:
    """USERNAME 서버 예약에 쓸 호스트 이름들: 공개 URL authority, ``CLAIRE_FQDN``, ``CLAIRE_SITE_DOMAIN``(P2에서 생기면)."""
    hosts = [config.expected_authority]
    for name in ("fqdn", "effective_fqdn", "site_domain"):
        try:
            value = getattr(settings, name, "")
        except Exception:  # noqa: BLE001  # 설정 객체의 계산 속성이 실패하면 그 값만 건너뛴다
            value = ""
        hosts.append(_host_of(value))
    return tuple(h for h in hosts if h)


def build_context(
    settings: Any,
    config: security.WebRuntimeConfig,
    *,
    settings_getter: Callable[[], Any] | None = None,
) -> AccountsContext:
    data_dir = Path(getattr(settings, "data_dir"))
    return AccountsContext(
        db_file=Path(settings.db_file),
        auth_db_file=auth_db.auth_db_path(data_dir),
        config=config,
        server_reserved=server_reserved_labels(server_hosts(settings, config)),
        data_dir=data_dir,
        settings=settings,
        settings_getter=settings_getter,
    )


def prepare_client_db(claire_db_file: Path | str) -> None:
    """Make ``client.db`` or bring its schema to the current version before the first request (RD27)."""
    client_db.connect_client_db(client_db.client_db_path(claire_db_file)).close()


def open_client_db(ctx: AccountsContext) -> sqlite3.Connection:
    return client_db.connect_client_existing(ctx.client_db_file)


def _retire_legacy_api_tokens_file(data_dir: Path | str) -> bool:
    """Archive legacy ``data/auth/api_tokens.json`` without reading it so manual tokens cannot be honored."""
    auth_dir = Path(data_dir) / "auth"
    legacy_path = auth_dir / "api_tokens.json"
    retired_path = auth_dir / "api_tokens.json.retired"
    if not legacy_path.is_file() or retired_path.exists():
        return False
    os.replace(legacy_path, retired_path)
    _log.info(
        "Legacy manual token file %s has been retired to %s; use /account/mcp OAuth 2.1 tokens instead.",
        legacy_path,
        retired_path,
    )
    return True


def prepare_auth_db(data_dir: Path | str) -> int:
    """auth.db를 만들거나 스키마를 맞추고(E3), 만료된 로그인 실패 기록을 지운다. 지운 행 수를 반환한다."""
    _retire_legacy_api_tokens_file(data_dir)
    conn = auth_db.connect_auth_db(auth_db.auth_db_path(data_dir))
    try:
        return auth_db.prune_login_throttle(conn)
    finally:
        conn.close()


# --- DB ----------------------------------------------------------------------


def open_db(ctx: AccountsContext, *, readonly: bool = False) -> sqlite3.Connection:
    return dbm.connect_existing(ctx.db_file, readonly=readonly)


def open_auth_db(ctx: AccountsContext) -> sqlite3.Connection:
    """기동 때 준비한 auth.db를 연다. 없으면 만들지 않고 실패한다(C1: 기동 준비 누락은 결함이다)."""
    uri = ctx.auth_db_file.resolve().as_uri() + "?mode=rw"
    conn = sqlite3.connect(uri, uri=True, timeout=30.0)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys=ON;")
    conn.execute("PRAGMA busy_timeout=30000;")
    return conn


# --- 요청 ------------------------------------------------------------------------


def client_ip(request: Request, ctx: AccountsContext) -> str:
    """로그인 실패 제한 키의 IP. 바로 앞 피어가 신뢰 프록시일 때만 ``X-Forwarded-For``를 읽는다(`security.client_ip`)."""
    return security.client_ip(request.scope, ctx.config.trusted_proxies)


def request_session_token(request: Request, principal: Principal) -> str | None:
    """주체가 쓴 세션 토큰(cookie·x-session). 그 밖의 채널은 None."""
    if principal.channel == "cookie":
        _present, token = security.read_session_cookie(request.scope)
        return token
    if principal.channel == "x-session":
        value = request.headers.get("x-session")
        return value.strip() if value else None
    return None


def cookie_session_token(request: Request) -> str | None:
    """공개 라우트(`/login`, `/logout`)에서 브라우저가 보낸 세션 쿠키 값. 검증하지 않는다."""
    _present, token = security.read_session_cookie(request.scope)
    return token if token and dbm.plausible_session_token(token) else None


def same_origin(request: Request) -> bool:
    """Validate same-origin requests via Origin and Sec-Fetch-Site (accepts matching Origin, or missing Origin with Sec-Fetch-Site same-origin or none)."""
    raw_fetch_site = request.headers.get("sec-fetch-site")
    fetch_site = raw_fetch_site.strip().lower() if raw_fetch_site is not None else None
    if fetch_site is not None and fetch_site not in ("same-origin", "none"):
        return False
    kind = security.request_origin_kind(request)
    if kind == "same":
        return True
    if kind == "none":
        return fetch_site in ("same-origin", "none")
    return False


def set_session_cookie(response: Response, ctx: AccountsContext, token: str) -> None:
    security.set_session_cookie(response, ctx.config, token)


def clear_session_cookie(response: Response, ctx: AccountsContext) -> None:
    security.clear_session_cookie(response, ctx.config)


def retry_after_seconds(retry_after: float | None, *, now: float | None = None) -> int:
    if retry_after is None:
        return 1
    current = time.time() if now is None else now
    return max(1, math.ceil(retry_after - current))


# --- 응답 ------------------------------------------------------------------------


def not_found() -> Response:
    """존재를 숨기는 404. 미들웨어의 거부 응답과 같은 모양이다."""
    return PlainTextResponse("Not Found", status_code=404)


def forbidden() -> Response:
    return PlainTextResponse("Forbidden", status_code=403)


def json_error(status_code: int, error: str, **extra: Any) -> JSONResponse:
    return JSONResponse({"error": error, **extra}, status_code=status_code)


def require_recent_auth(principal: Principal, *, now: float | None = None) -> Response | None:
    """단계 인증(§8.5) 응답 규약. 통과하면 None.

    - 세션 채널(cookie·x-session)이 아니면 403 ``step_up_requires_session``: bearer 토큰은 사람이 다시 인증할 수 없다.
    - 세션이지만 최근 ``STEP_UP_WINDOW_SECONDS`` 안에 인증하지 않았으면 401 ``reauth_required``: 클라이언트는 ``POST /api/auth/reauth`` 뒤에 다시 시도한다.
    """
    if principal.channel not in SESSION_CHANNELS:
        return json_error(403, "step_up_requires_session")
    if not has_recent_auth(principal, now=now):
        return JSONResponse(
            {"error": "reauth_required", "reauth": "/api/auth/reauth"},
            status_code=401,
            headers={"WWW-Authenticate": 'Session realm="claire", error="reauth_required"'},
        )
    return None


def validate_display_name(value: Any) -> str | None:
    """표시 이름. 문자열, ``DISPLAY_NAME_MAX_LENGTH``자 이하, 제어 문자 없음이면 그대로 반환하고 아니면 None. 앞뒤 공백을 자르지 않는다(C1)."""
    if not isinstance(value, str) or len(value) > DISPLAY_NAME_MAX_LENGTH:
        return None
    if any(unicodedata.category(ch) in {"Cc", "Cf", "Zl", "Zp"} for ch in value):
        return None
    return value


def validate_summary(value: Any) -> str | None:
    """Plain-text account bio: string up to ``SUMMARY_MAX_LENGTH`` chars after CRLF normalization, no control characters except newline."""
    if not isinstance(value, str):
        return None
    normalized = value.replace("\r\n", "\n").replace("\r", "\n")
    if len(normalized) > SUMMARY_MAX_LENGTH:
        return None
    if any(ch != "\n" and unicodedata.category(ch) in {"Cc", "Cf", "Zl", "Zp"} for ch in normalized):
        return None
    return normalized


def format_ts(value: float | None) -> str:
    if value is None:
        return "-"
    return time.strftime("%Y-%m-%d %H:%M:%S UTC", time.gmtime(value))


# --- 템플릿 -----------------------------------------------------------------------


def escape(value: Any) -> str:
    return html.escape("" if value is None else str(value), quote=True)


# /admin/* 화면 중 실제 서버 설정에 닿지 않는 요소(프로토타입·시뮬레이션·브라우저 저장·정적 표)의 표식.
# 운영자가 실제 서버 설정으로 오인하지 않도록 서버가 그린다(JS 없이도 보임). 모양은 console_shell.html의 .proto-badge/.proto-note.
PROTOTYPE_KINDS = ("prototype", "simulation", "client_only", "static")


def icon_html(name: str) -> str:
    """An empty element that ``static/js/icons.js`` fills with an inline SVG line icon (HD4 of HOME_OVERVIEW_AND_CONTEXT_PANEL_DESIGN.md). An empty name gives no element."""
    return f'<span data-icon="{escape(name)}"></span>' if name else ""


def prototype_badge(marker: str, kind: str) -> str:
    if kind not in PROTOTYPE_KINDS:
        raise KeyError(kind)
    return f'<span class="proto-badge" data-prototype="{escape(marker)}" data-prototype-kind="{escape(kind)}">{escape(t(f"console.prototype.{kind}"))}</span>'


def prototype_note(marker: str, text: str) -> str:
    return f'<p class="proto-note" data-prototype-note="{escape(marker)}"><span data-icon="warning"></span> {escape(text)}</p>'


def t_code(key: str, **codes: Any) -> str:
    """The escaped translation of ``key`` as HTML. Each parameter is literal text (a path, a field name, a value) and becomes a ``<code>`` element."""
    return interpolate(escape(t(key)), {name: f"<code>{escape(value)}</code>" for name, value in codes.items()})


# 계정 양식 화면의 ``Referrer-Policy``. 전역 기본값 ``no-referrer``(`security.SafeAccessLogMiddleware`)이면
# Chromium은 그 화면에서 제출한 양식 POST(navigate 모드)에 ``Origin: null``을 싣고, ``CORSPolicyMiddleware``가
# 403으로 막는다. ``same-origin``은 같은 origin 요청에만 Referer·실제 Origin을 싣고 다른 origin으로는 아무것도 보내지 않는다.
# 이 값은 이 모듈의 화면(`render_template`: /setup, /login, /me, /admin/accounts)에만 붙인다.
FORM_PAGE_REFERRER_POLICY = "same-origin"


# A translation slot in a template file: ``${t:auth.login.title}``. It is resolved before ``string.Template`` runs, thus request values cannot add a slot.
_TRANSLATION_SLOT = re.compile(r"\$\{t:([a-z0-9_.]+)\}")


def i18n_bootstrap_html(scope: BrowserCatalogScope = "page") -> str:
    """The inline catalog of the active locale and the browser runtime. The catalog is in the document, thus ``ClaireI18n.t`` works before all network requests (I18N design Section 6.3). ``scope`` selects the console script keys (``i18n.catalog.browser_catalog``)."""
    return f'<script id="claire-i18n-catalog" type="application/json">{browser_catalog_json(scope=scope)}</script><script src="/static/js/i18n.js"></script>'


# Pages that have no JavaScript on purpose (credential isolation). Their language switch is a plain form, and ``POST /api/locale`` sends the browser back to one of these paths only.
LOCALE_FORM_RETURN_PATHS = {"login.html": "/login", "setup.html": "/setup"}


def locale_switch_html(return_path: str) -> str:
    """The language switch of a page without JavaScript: one submit button for each locale in a form that posts to ``/api/locale``. The button of the active locale is disabled."""
    current = get_locale()
    active_attrs = ' aria-current="true" disabled'
    buttons = "".join(
        f'<button type="submit" class="link" name="locale" value="{code}" lang="{code}"{active_attrs if code == current else ""}>{escape(t(f"locale.name.{code}"))}</button>'
        for code in SUPPORTED_LOCALES
    )
    label = escape(t("console.account.locale_label"))
    return f'<form class="locale-switch" method="post" action="/api/locale" aria-label="{label}"><input type="hidden" name="next" value="{escape(return_path)}">{buttons}</form>'


def render_template(
    name: str,
    *,
    text: Mapping[str, Any] | None = None,
    markup: Mapping[str, str] | None = None,
    status_code: int = 200,
    headers: Mapping[str, str] | None = None,
) -> HTMLResponse:
    """``templates/<name>``의 ``$이름`` 자리를 채운다.

    ``text`` 값은 모두 HTML 이스케이프한다. ``markup``은 이 패키지가 `escape`로 이미 만든 조각만 넣는다. 빠진 자리가 있으면 ``KeyError``(C1).

    I18N design Section 6.2: every template gets ``$html_lang`` (the active locale), ``$i18n_bootstrap_html`` (inline catalog and ``i18n.js``, for pages that have scripts) and ``$locale_switch_html`` (the form of the pages in ``LOCALE_FORM_RETURN_PATHS``, empty for other pages). A slot ``${t:catalog.key}`` becomes the escaped translation; the ``text`` values are its ``{name}`` parameters.
    """
    raw_source = (TEMPLATES_DIR / name).read_text(encoding="utf-8")
    tx = text or {}
    mk = markup or {}
    source = _TRANSLATION_SLOT.sub(lambda m: escape(t(m.group(1), **tx)).replace("$", "$$"), raw_source)
    template = string.Template(source)
    return_path = LOCALE_FORM_RETURN_PATHS.get(name)
    # Only an administrator area page or an administrator moderation page gets the administrator script keys.
    scope: BrowserCatalogScope = "page"
    if name == "console_shell.html":
        scope = "console_admin" if (tx.get("active_area") == "admin" or bool(mk.get("admin_scripts_html"))) else "console"
    values: dict[str, str] = {"html_lang": get_locale(), "i18n_bootstrap_html": i18n_bootstrap_html(scope), "locale_switch_html": locale_switch_html(return_path) if return_path else "", "extra_styles_html": ""}
    if name == "console_shell.html" and "drawer_title_html" not in mk:
        is_mod = tx.get("active_area") == "moderation"
        d_icon = "shield" if is_mod else "settings"
        d_key = "nav.topbar.moderation" if is_mod else "nav.topbar.console"
        values["drawer_title_html"] = f'<span data-icon="{d_icon}"></span> {escape(t(d_key, **tx))}'
    values.update({k: escape(v) for k, v in tx.items()})
    values.update(mk)
    body = template.substitute(values)
    web_ui = get_web_ui_renderer()
    if "<!-- __COMMON_APPBAR__ -->" in body:
        appbar_raw = mk.get("common_appbar_html")
        if appbar_raw is None:
            active_area = tx.get("active_area")
            is_admin_area = active_area == "admin"
            is_moderation_area = active_area == "moderation"
            graph_href = str(tx.get("graph_href", "/"))
            synd_href = str(tx.get("syndication_href", "/syndication"))
            scope_badge_escaped = escape(tx.get("scope_badge", ""))
            brand_icon = "shield" if is_moderation_area else "settings"
            brand_key = "nav.topbar.moderation" if is_moderation_area else "nav.topbar.console"
            appbar_raw = web_ui.render_appbar_html(
                bar_id="console-bar",
                brand_id="console-back-graph",
                brand_href=graph_href,
                brand_extra_html="",
                center_id="console-bar-center",
                kb_picker_html=mk.get("theme_selector_html", ""),
                center_extra_html=f'<span class="appbar-title console-brand"><span data-icon="{brand_icon}"></span> ${{t:{brand_key}}}</span><span class="console-badge" id="console-scope-badge">{scope_badge_escaped}</span>',
                end_id="console-bar-end",
                ingest_enabled=False,
                home_href=graph_href,
                synd_href=synd_href,
                moderation_href="/moderation",
                moderation_current=is_moderation_area,
                console_href="/admin" if is_admin_area else "/account",
                console_icon="wrench" if is_admin_area else "settings",
                console_current=not is_moderation_area,
                show_logout=True,
            )
        appbar_rendered = _TRANSLATION_SLOT.sub(lambda m: escape(t(m.group(1), **tx)), appbar_raw)
        body = body.replace("<!-- __COMMON_APPBAR__ -->", appbar_rendered)
    if "<!-- __COMMON_CMDPALETTE__ -->" in body:
        palette_rendered = _TRANSLATION_SLOT.sub(lambda m: escape(t(m.group(1), **tx)), web_ui.render_cmdpalette_html())
        body = body.replace("<!-- __COMMON_CMDPALETTE__ -->", palette_rendered)
    if "<!-- __COMMON_INGEST_MODALS__ -->" in body:
        ingest_rendered = _TRANSLATION_SLOT.sub(lambda m: escape(t(m.group(1), **tx)), web_ui.render_ingest_modals_html())
        body = body.replace("<!-- __COMMON_INGEST_MODALS__ -->", ingest_rendered)
    include_script = name not in ("login.html", "setup.html")
    body = web_ui.inject_favicon_head_tags(body, include_pwa_register=include_script)
    response = HTMLResponse(body, status_code=status_code)
    response.headers["Cache-Control"] = "no-store"
    response.headers["Referrer-Policy"] = FORM_PAGE_REFERRER_POLICY
    for key, value in (headers or {}).items():
        response.headers[key] = value
    return response


def error_list(messages: Iterable[str]) -> str:
    items = "".join(f"<li>{escape(m)}</li>" for m in messages)
    return f'<ul class="errors" role="alert">{items}</ul>' if items else ""
