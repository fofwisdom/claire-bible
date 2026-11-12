"""Account locale contract (I18N design Sections 5 and 6.1, unit IP1-2): schema v19, ``AccountStore.update_locale``, ``Principal.locale``, ``GET/PATCH /api/me``, ``POST /api/locale`` and ``I18nMiddleware``.

All state is in ``tmp_path``.
"""

from __future__ import annotations

import sqlite3
import time
from dataclasses import dataclass
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
from starlette.testclient import TestClient

from claire.api import server
from claire.store import accounts as accounts_store
from claire.store import auth_db
from claire.store import db as dbm

OWNER_TOKEN = "owner-" + "o" * 32
READONLY_TOKEN = "readonly-" + "r" * 32
PUBLIC_URL = "http://127.0.0.1:8765"
USERNAME = "kim_lee"


@dataclass
class _Settings:
    db_file: Path
    data_dir: Path
    environment: str = "development"
    public_url: str = PUBLIC_URL
    inject_token: str = OWNER_TOKEN
    readonly_token: str = READONLY_TOKEN
    cors_allowed_origins: str = ""
    anonymous_readonly: bool = False
    ui_lang: str = ""


class _Client:
    def __init__(self, client: TestClient) -> None:
        self.client = client

    def request(self, method: str, path: str, *, session: str | None = None, cookies: dict[str, str] | None = None, origin: bool = True, **kwargs: Any):
        headers = dict(kwargs.pop("headers", {}) or {})
        jar = dict(cookies or {})
        if session is not None:
            jar["claire_session"] = session
        if jar:
            headers["Cookie"] = "; ".join(f"{k}={v}" for k, v in jar.items())
        if method not in {"GET", "HEAD"} and origin:
            headers["Origin"] = PUBLIC_URL
        self.client.cookies.clear()
        response = self.client.request(method, path, headers=headers, follow_redirects=False, **kwargs)
        self.client.cookies.clear()
        return response


def _make_client(settings: _Settings):
    app = server.create_app(settings, SimpleNamespace(provider=SimpleNamespace(name="stub")))
    return TestClient(app, base_url=PUBLIC_URL)


@pytest.fixture
def settings(tmp_path: Path) -> _Settings:
    return _Settings(db_file=tmp_path / "claire.db", data_dir=tmp_path)


@pytest.fixture
def client(settings: _Settings):
    with _make_client(settings) as tc:
        yield _Client(tc)


def _db(settings: _Settings) -> sqlite3.Connection:
    return dbm.connect_existing(settings.db_file)


def _activate_admin(settings: _Settings) -> None:
    conn = _db(settings)
    auth = auth_db.connect_auth_db(auth_db.auth_db_path(settings.data_dir))
    try:
        accounts_store.AccountStore(conn).complete_setup(1, username=USERNAME, display_name="Admin")
        auth_db.set_password(auth, 1, "correct-horse-battery", username=USERNAME)
    finally:
        auth.close()
        conn.close()


def _session(settings: _Settings, *, scope: str = "owner") -> str:
    conn = _db(settings)
    try:
        return accounts_store.create_account_session(conn, account_id=1, scope=scope, now=time.time())
    finally:
        conn.close()


def _stored_locale(settings: _Settings) -> str | None:
    conn = _db(settings)
    try:
        return accounts_store.AccountStore(conn).get(1).locale
    finally:
        conn.close()


def _locale_cookies(response) -> list[str]:
    return [v for v in response.headers.get_list("set-cookie") if v.startswith("claire_locale=")]


# --- schema v19 and the store ---------------------------------------------------


def test_v18_database_migrates_to_v19_and_the_migration_is_idempotent(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    path = tmp_path / "claire.db"
    with monkeypatch.context() as m:
        m.setattr(dbm, "SCHEMA_VERSION", 18)
        m.setattr(dbm, "_migrate_v19_account_locale", lambda conn: None)
        conn = dbm.connect(path)
        try:
            dbm.init_db(conn)
            assert dbm.stored_schema_version(conn) == 18
            assert "locale" not in dbm._column_names(conn, "accounts")
        finally:
            conn.close()

    for _ in range(2):
        conn = dbm.connect(path)
        try:
            dbm.init_db(conn)
            assert dbm.SCHEMA_VERSION == 19 and dbm.stored_schema_version(conn) == 19
            dbm.require_current_schema(conn)
            assert "locale" in dbm._column_names(conn, "accounts")
            # An account that was there before the migration has automatic negotiation.
            assert accounts_store.AccountStore(conn).get(1).locale is None
            with pytest.raises(sqlite3.IntegrityError):
                conn.execute("UPDATE accounts SET locale='ja' WHERE id=1")
            conn.rollback()
        finally:
            conn.close()


def test_account_store_update_locale_contract(client, settings) -> None:
    conn = _db(settings)
    try:
        store = accounts_store.AccountStore(conn)
        assert store.update_locale(1, "ko", now=123.0).locale == "ko"
        assert store.get(1).updated_at == 123.0
        assert store.update_locale(1, "en").locale == "en"
        assert store.update_locale(1, "").locale is None
        store.update_locale(1, "ko")
        assert store.update_locale(1, None).locale is None
        for bad in ("ja", "ko-KR", "KO", 1):
            with pytest.raises(ValueError, match="invalid_locale"):
                store.update_locale(1, bad)  # type: ignore[arg-type]
        assert store.get(1).locale is None
        with pytest.raises(accounts_store.AccountNotFound):
            store.update_locale(999, "ko")
    finally:
        conn.close()


# --- GET/PATCH /api/me ----------------------------------------------------------


def test_me_reports_the_stored_and_the_effective_locale(client, settings) -> None:
    _activate_admin(settings)
    token = _session(settings)
    me = client.request("GET", "/api/me", session=token).json()
    assert me["locale"] is None and me["effective_locale"] == "en"
    assert client.request("GET", "/api/me", session=token, headers={"Accept-Language": "ko-KR,ko;q=0.9,en;q=0.8"}).json()["effective_locale"] == "ko"
    assert client.request("GET", "/api/me", session=token, cookies={"claire_locale": "ko"}, headers={"Accept-Language": "en"}).json()["effective_locale"] == "ko"


def test_me_patch_locale_persists_and_sets_the_cookie(client, settings) -> None:
    _activate_admin(settings)
    token = _session(settings)

    set_ko = client.request("PATCH", "/api/me", session=token, json={"locale": "ko"})
    assert set_ko.status_code == 200
    assert set_ko.json()["locale"] == "ko" and set_ko.json()["effective_locale"] == "ko"
    cookies = _locale_cookies(set_ko)
    assert len(cookies) == 1 and cookies[0].startswith("claire_locale=ko;")
    assert "Max-Age=31536000" in cookies[0] and "Path=/" in cookies[0] and "SameSite=lax" in cookies[0] and "HttpOnly" not in cookies[0]
    assert _stored_locale(settings) == "ko"
    # The account setting has priority over the cookie and the header (decision ID3). The step needs no recent authentication.
    assert client.request("GET", "/api/me", session=token, cookies={"claire_locale": "en"}, headers={"Accept-Language": "en"}).json()["effective_locale"] == "ko"

    # A change of a different field does not touch the locale or its cookie.
    name_only = client.request("PATCH", "/api/me", session=token, json={"display_name": "New"})
    assert name_only.json()["locale"] == "ko" and _locale_cookies(name_only) == []

    for reset_value in (None, ""):
        client.request("PATCH", "/api/me", session=token, json={"locale": "en"})
        reset = client.request("PATCH", "/api/me", session=token, json={"locale": reset_value, "display_name": "Both"}, cookies={"claire_locale": "en"}, headers={"Accept-Language": "ko"})
        assert reset.status_code == 200
        # The cookie is cleared, thus the effective locale comes from Accept-Language.
        assert reset.json()["locale"] is None and reset.json()["effective_locale"] == "ko" and reset.json()["display_name"] == "Both"
        cookies = _locale_cookies(reset)
        assert len(cookies) == 1 and "Max-Age=0" in cookies[0]
        assert _stored_locale(settings) is None


@pytest.mark.parametrize("bad", ["ja", "ko-KR", "english", 123, True, ["ko"], {"code": "ko"}])
def test_me_patch_rejects_an_invalid_locale_before_all_writes(client, settings, bad: Any) -> None:
    _activate_admin(settings)
    token = _session(settings)
    response = client.request("PATCH", "/api/me", session=token, json={"locale": bad, "display_name": "Changed"})
    assert response.status_code == 400 and response.json() == {"error": "invalid_locale"}
    assert _locale_cookies(response) == []
    me = client.request("GET", "/api/me", session=token).json()
    assert me["locale"] is None and me["display_name"] == "Admin"


# --- POST /api/locale -----------------------------------------------------------


def test_locale_post_for_an_anonymous_caller_sets_and_clears_the_cookie(client, settings) -> None:
    set_ko = client.request("POST", "/api/locale", json={"locale": "ko"})
    assert set_ko.status_code == 200 and set_ko.json() == {"locale": "ko", "effective_locale": "ko"}
    cookies = _locale_cookies(set_ko)
    assert len(cookies) == 1 and cookies[0].startswith("claire_locale=ko;") and "Secure" not in cookies[0]

    cleared = client.request("POST", "/api/locale", json={"locale": None}, cookies={"claire_locale": "ko"}, headers={"Accept-Language": "ko"})
    assert cleared.status_code == 200 and cleared.json() == {"locale": None, "effective_locale": "ko"}
    assert "Max-Age=0" in _locale_cookies(cleared)[0]
    assert client.request("POST", "/api/locale", json={"locale": ""}).json() == {"locale": None, "effective_locale": "en"}

    for bad in ("ja", "ko-KR", 5, False):
        response = client.request("POST", "/api/locale", json={"locale": bad})
        assert response.status_code == 400 and response.json() == {"error": "invalid_locale"} and _locale_cookies(response) == []
    assert client.request("POST", "/api/locale", json={}).status_code == 400
    assert client.request("POST", "/api/locale", json={"locale": "ko", "extra": 1}).json()["error"] == "invalid_request"
    # The route is public, thus the handler accepts the same origin only.
    assert client.request("POST", "/api/locale", json={"locale": "ko"}, origin=False).status_code == 403
    assert client.request("POST", "/api/locale", json={"locale": "ko"}, origin=False, headers={"Origin": "https://evil.example"}).status_code == 403
    assert _stored_locale(settings) is None


def test_locale_post_persists_on_the_account_for_an_owner_session_only(client, settings) -> None:
    _activate_admin(settings)
    readonly = _session(settings, scope="readonly")
    assert client.request("POST", "/api/locale", session=readonly, json={"locale": "ko"}).status_code == 200
    assert _stored_locale(settings) is None
    assert client.request("POST", "/api/locale", session="s" * 43, json={"locale": "ko"}).status_code == 200
    assert _stored_locale(settings) is None

    owner = _session(settings)
    assert client.request("POST", "/api/locale", session=owner, json={"locale": "ko"}).json() == {"locale": "ko", "effective_locale": "ko"}
    assert _stored_locale(settings) == "ko"
    assert client.request("POST", "/api/locale", session=owner, json={"locale": None}).json()["locale"] is None
    assert _stored_locale(settings) is None


# --- I18nMiddleware ---------------------------------------------------------------


def test_middleware_sets_content_language_on_html_and_reads_claire_lang(tmp_path: Path) -> None:
    settings = _Settings(db_file=tmp_path / "claire.db", data_dir=tmp_path, ui_lang="ko")
    with _make_client(settings) as tc:
        client = _Client(tc)
        # CLAIRE_LANG is the last step before the default.
        assert client.request("GET", "/login").headers["content-language"] == "ko"
        assert client.request("GET", "/login", headers={"Accept-Language": "en-US,en;q=0.9"}).headers["content-language"] == "en"
        assert client.request("GET", "/login", cookies={"claire_locale": "ja"}, headers={"Accept-Language": "fr"}).headers["content-language"] == "ko"
        # Responses that are not HTML do not get the header: the locale does not change API and protocol payloads.
        assert "content-language" not in client.request("GET", "/health").headers
        assert client.request("POST", "/api/locale", json={"locale": ""}).json()["effective_locale"] == "ko"


# --- end-to-end ---------------------------------------------------------------------


def test_e2e_locale_journey_from_anonymous_negotiation_to_account_preference(client, settings) -> None:
    # 1. Anonymous visitor: default en, then negotiation from the browser header.
    assert client.request("GET", "/login").headers["content-language"] == "en"
    assert client.request("GET", "/login", headers={"Accept-Language": "ko-KR,ko;q=0.9"}).headers["content-language"] == "ko"

    # 2. The visitor selects English on the login page. The cookie wins over the header.
    switched = client.request("POST", "/api/locale", json={"locale": "en"}, headers={"Accept-Language": "ko-KR"})
    cookie_value = _locale_cookies(switched)[0].split(";")[0].split("=")[1]
    assert cookie_value == "en"
    assert client.request("GET", "/login", cookies={"claire_locale": cookie_value}, headers={"Accept-Language": "ko-KR"}).headers["content-language"] == "en"

    # 3. After login the account has no preference: the cookie still applies in the console.
    _activate_admin(settings)
    token = _session(settings)
    page = client.request("GET", "/settings/account", session=token, cookies={"claire_locale": cookie_value})
    assert page.status_code == 200 and page.headers["content-language"] == "en"

    # 4. The owner selects Korean for the account. A second browser without the cookie gets Korean.
    saved = client.request("PATCH", "/api/me", session=token, json={"locale": "ko"}, cookies={"claire_locale": cookie_value})
    assert saved.json()["effective_locale"] == "ko" and _locale_cookies(saved)[0].startswith("claire_locale=ko;")
    other_browser = client.request("GET", "/settings/account", session=token, headers={"Accept-Language": "en-US"})
    assert other_browser.headers["content-language"] == "ko"

    # 5. After logout the cookie from step 4 keeps Korean on the public login page.
    assert client.request("POST", "/logout", session=token, cookies={"claire_locale": "ko"}).status_code == 303
    assert client.request("GET", "/login", cookies={"claire_locale": "ko"}, headers={"Accept-Language": "en-US"}).headers["content-language"] == "ko"

    # 6. Reset to automatic: the account column is NULL and the chain goes back to the header and the default.
    token = _session(settings)
    reset = client.request("PATCH", "/api/me", session=token, json={"locale": None}, cookies={"claire_locale": "ko"})
    assert reset.json()["locale"] is None and reset.json()["effective_locale"] == "en"
    assert client.request("GET", "/settings/account", session=token).headers["content-language"] == "en"


# --- IP2-1: auth pages, inline catalog, language selector -------------------------


def _bootstrap_json(page_text: str, element_id: str) -> Any:
    import json
    import re

    match = re.search(rf'<script id="{element_id}" type="application/json">(.*?)</script>', page_text, re.S)
    assert match is not None, element_id
    return json.loads(match.group(1))


def test_login_page_is_translated_and_has_a_switch_without_javascript(client, settings) -> None:
    english = client.request("GET", "/login")
    assert '<html lang="en">' in english.text and "<h1>Log in</h1>" in english.text and "<title>Log in · Claire</title>" in english.text
    korean = client.request("GET", "/login", cookies={"claire_locale": "ko"})
    assert '<html lang="ko">' in korean.text and "<h1>로그인</h1>" in korean.text and korean.headers["content-language"] == "ko"
    # Credential pages have no script. The switch is a form: the active locale is disabled, the other one is a submit button.
    assert "<script" not in korean.text
    assert '<form class="locale-switch" method="post" action="/api/locale"' in korean.text and '<input type="hidden" name="next" value="/login">' in korean.text
    assert 'name="locale" value="ko" lang="ko" aria-current="true" disabled>' in korean.text and 'name="locale" value="en" lang="en">English (en)</button>' in korean.text

    _activate_admin(settings)
    failed = client.request("POST", "/login", data={"username": USERNAME, "password": "wrong-password-0000"}, cookies={"claire_locale": "ko"})
    assert failed.status_code == 401 and "USERNAME 또는 비밀번호가 맞지 않습니다." in failed.text
    failed_en = client.request("POST", "/login", data={"username": USERNAME, "password": "wrong-password-0000"})
    assert failed_en.status_code == 401 and "The USERNAME or the password is not correct." in failed_en.text


def test_setup_page_escapes_translations_and_their_parameters(client, settings) -> None:
    token = _session(settings)
    page = client.request("GET", "/setup", session=token, headers={"Accept-Language": "ko"})
    assert page.status_code == 200 and '<html lang="ko">' in page.text and "<h1>관리자 계정 설정</h1>" in page.text
    # The parameter {password_chars} has $, <, > and braces. It is HTML-escaped and string.Template does not read it.
    assert "표준 특수문자(!&quot;#$%&amp;&#x27;()*+,-./:;&lt;=&gt;?@[\\]^_`{|}~)" in page.text
    assert '<input type="hidden" name="next" value="/setup">' in page.text and "<script" not in page.text
    rejected = client.request("POST", "/setup", session=token, data={"username": "x", "password": "short", "password_confirm": "short"}, headers={"Accept-Language": "ko"})
    assert rejected.status_code == 400 and "USERNAME 형식이 맞지 않습니다." in rejected.text and "(syntax)" in rejected.text


def test_locale_form_post_redirects_to_an_allowed_page_only(client, settings) -> None:
    switched = client.request("POST", "/api/locale", data={"locale": "ko", "next": "/login"})
    assert switched.status_code == 303 and switched.headers["location"] == "/login" and switched.headers["cache-control"] == "no-store"
    assert _locale_cookies(switched)[0].startswith("claire_locale=ko;")
    assert client.request("POST", "/api/locale", data={"locale": "en", "next": "/setup"}).headers["location"] == "/setup"
    for bad_next in ("/", "//evil.example/login", "https://evil.example/login", "/login?x=1", "/settings/account"):
        response = client.request("POST", "/api/locale", data={"locale": "ko", "next": bad_next})
        assert response.status_code == 400 and response.json()["error"] == "invalid_request" and _locale_cookies(response) == [], bad_next
    assert client.request("POST", "/api/locale", data={"locale": "ko"}).status_code == 400
    assert client.request("POST", "/api/locale", data={"locale": "ja", "next": "/login"}).json() == {"error": "invalid_locale"}
    assert client.request("POST", "/api/locale", data={"locale": "ko", "next": "/login"}, origin=False).status_code == 403


def test_settings_account_has_the_language_selector_and_the_inline_catalog(client, settings) -> None:
    from claire.i18n import browser_catalog, load_catalog

    _activate_admin(settings)
    token = _session(settings)
    page = client.request("GET", "/settings/account", session=token)
    assert '<html lang="en">' in page.text
    assert '<select id="pref-account-locale" class="pref-input" data-settings-locale><option value="" selected>Automatic (browser / system)</option><option value="en">English (en)</option><option value="ko">한국어 (ko)</option></select>' in page.text
    bootstrap = _bootstrap_json(page.text, "console-bootstrap")
    assert bootstrap["locale"] is None and bootstrap["effective_locale"] == "en"
    inline = _bootstrap_json(page.text, "claire-i18n-catalog")
    # The inline catalog has no server-only domains (auth pages, bot, CLI).
    assert inline == browser_catalog("en") and inline["common.action.save"] == "Save"
    assert not [key for key in inline if key.startswith(("auth.", "bot.", "cli."))]
    # i18n.js comes after the inline catalog and before the deferred console scripts.
    assert page.text.index('id="claire-i18n-catalog"') < page.text.index('<script src="/static/js/i18n.js"></script>') < page.text.index("/static/js/console_account.js")

    client.request("PATCH", "/api/me", session=token, json={"locale": "ko"})
    page = client.request("GET", "/settings/account", session=token, headers={"Accept-Language": "en"})
    assert '<html lang="ko">' in page.text and "<h2>🌐 인터페이스 언어</h2>" in page.text
    assert '<option value="">자동 (브라우저 / 시스템)</option><option value="en">English (en)</option><option value="ko" selected>한국어 (ko)</option>' in page.text
    bootstrap = _bootstrap_json(page.text, "console-bootstrap")
    assert bootstrap["locale"] == "ko" and bootstrap["effective_locale"] == "ko"
    assert _bootstrap_json(page.text, "claire-i18n-catalog") == browser_catalog("ko")

    script = client.request("GET", "/static/js/i18n.js")
    assert script.status_code == 200 and "ClaireI18n" in script.text
    assert client.request("GET", "/static/locales/ko.json").json() == load_catalog("ko")
