"""Interface language contract (I18N design Sections 5 and 6.1, ACCOUNT_PREFERRED_LANGUAGES_DESIGN.md): schema v46, ``AccountStore.update_preferred_languages``, ``Principal.locale`` (the first supported item of the preferred languages), ``GET/PATCH /api/me``, ``POST /api/locale``, and ``I18nMiddleware``.

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

from claire.accounts import languages
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
    """The interface language of the account (from its preferred languages)."""
    conn = _db(settings)
    try:
        return accounts_store.AccountStore(conn).get(1).locale
    finally:
        conn.close()


def _own_languages(settings: _Settings) -> tuple[str, ...] | None:
    conn = _db(settings)
    try:
        return accounts_store.AccountStore(conn).get(1).preferred_languages
    finally:
        conn.close()


def _locale_cookies(response) -> list[str]:
    return [v for v in response.headers.get_list("set-cookie") if v.startswith("claire_locale=")]


# --- schema v46 and the store ---------------------------------------------------


def test_v45_database_migrates_the_locale_to_the_preferred_languages_and_the_migration_is_idempotent(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    path = tmp_path / "claire.db"
    monkeypatch.delenv("CLAIRE_PREFERRED_LANGUAGES", raising=False)
    with monkeypatch.context() as m:
        m.setattr(dbm, "SCHEMA_VERSION", 45)
        m.setattr(dbm, "_migrate_v46_preferred_languages", lambda conn: None)
        conn = dbm.connect(path)
        try:
            dbm.init_db(conn)
            assert dbm.stored_schema_version(conn) == 45
            conn.execute("ALTER TABLE accounts ADD COLUMN locale TEXT CHECK (locale IS NULL OR locale IN ('en', 'ko'))")
            now = time.time()
            for account_id, public_id, locale in ((2, 223456789011, "en"), (3, 223456789012, None)):
                conn.execute(
                    "INSERT INTO accounts (id, public_id, username, role, status, display_name, profile_public, created_at, updated_at, locale) VALUES (?, ?, ?, 'member', 'active', 'M', 0, ?, ?, ?)",
                    (account_id, public_id, f"member{account_id}", now, now, locale),
                )
            conn.execute("UPDATE accounts SET locale='ko' WHERE id=1")
            conn.commit()
        finally:
            conn.close()

    for _ in range(2):
        conn = dbm.connect(path)
        try:
            dbm.init_db(conn)
            assert dbm.stored_schema_version(conn) == dbm.SCHEMA_VERSION
            assert "locale" not in dbm._column_names(conn, "accounts")
            store = accounts_store.AccountStore(conn)
            # An upgraded database without the variable gets the old default of CLAIRE_PREFERRED_LANGUAGES (ko), then en.
            assert languages.server_languages(conn) == ("ko", "en")
            # locale ko gives the server default language: the account follows it; locale en gives an own list; no locale: the server default language.
            assert (store.get(1).preferred_languages, store.get(1).locale) == (None, "ko")
            assert (store.get(2).preferred_languages, store.get(2).locale) == (("en", "ko"), "en")
            assert (store.get(3).preferred_languages, store.get(3).languages, store.get(3).locale) == (None, ("ko", "en"), "ko")
        finally:
            conn.close()


def test_new_database_has_no_server_default_language_unless_the_variable_is_set(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("CLAIRE_PREFERRED_LANGUAGES", raising=False)
    conn = dbm.connect(tmp_path / "new.db")
    try:
        dbm.init_db(conn)
        assert languages.server_languages(conn) == ()
        # The copy runs one time: a server default language that the administrator removes stays removed after the next start.
        languages.set_server_languages(conn, ["ko"], updated_by=1)
        languages.set_server_languages(conn, None, updated_by=1)
        dbm.init_db(conn)
        assert languages.server_languages(conn) == ()
    finally:
        conn.close()
    monkeypatch.setenv("CLAIRE_PREFERRED_LANGUAGES", "JA, ko")
    conn = dbm.connect(tmp_path / "seeded.db")
    try:
        dbm.init_db(conn)
        assert languages.server_languages(conn) == ("ja", "ko", "en")
    finally:
        conn.close()


def test_account_store_update_preferred_languages_contract(client, settings) -> None:
    conn = _db(settings)
    try:
        store = accounts_store.AccountStore(conn)
        account = store.update_preferred_languages(1, ["ko", "ja"], now=123.0)
        assert (account.preferred_languages, account.languages, account.locale) == (("ko", "ja", "en"), ("ko", "ja", "en"), "ko")
        assert store.get(1).updated_at == 123.0
        # en can come first: then the interface is English.
        assert store.update_preferred_languages(1, ["en", "ko"]).locale == "en"
        # A language that the interface does not support is skipped for the interface.
        assert store.update_preferred_languages(1, ["ja", "ko"]).locale == "ko"
        # None: the account follows the server default language; a new database has none, thus no interface language comes from the account.
        follow = store.update_preferred_languages(1, None)
        assert (follow.preferred_languages, follow.languages, follow.locale) == (None, (), None)
        for bad in (["ko!"], ["k"], [""] * 0 + ["x" * 9], [f"l{i}a" for i in range(11)]):
            with pytest.raises(languages.InvalidLanguages):
                store.update_preferred_languages(1, bad)
        assert store.get(1).preferred_languages is None
        with pytest.raises(accounts_store.AccountNotFound):
            store.update_preferred_languages(999, ["ko"])
    finally:
        conn.close()


# --- GET/PATCH /api/me ----------------------------------------------------------


def test_me_reports_the_languages_and_the_effective_locale(client, settings) -> None:
    _activate_admin(settings)
    token = _session(settings)
    me = client.request("GET", "/api/me", session=token).json()
    assert (me["preferred_languages"], me["languages"], me["effective_locale"]) == (None, [], "en")
    # No server default language and no own list: the automatic chain (cookie, header) gives the interface language.
    assert client.request("GET", "/api/me", session=token, headers={"Accept-Language": "ko-KR,ko;q=0.9"}).json()["effective_locale"] == "ko"
    assert client.request("GET", "/api/me", session=token, cookies={"claire_locale": "ko"}, headers={"Accept-Language": "en"}).json()["effective_locale"] == "ko"
    # A selected server default language wins over the browser (P1).
    assert client.request("PUT", "/api/admin/server/languages", session=token, json={"languages": ["en"]}).status_code == 200
    assert client.request("GET", "/api/me", session=token, headers={"Accept-Language": "ko"}).json()["effective_locale"] == "en"


def test_me_patch_preferred_languages_persists_and_sets_the_cookie(client, settings) -> None:
    _activate_admin(settings)
    token = _session(settings)

    set_ko = client.request("PATCH", "/api/me", session=token, json={"preferred_languages": ["ko", "ja"]})
    assert set_ko.status_code == 200
    assert (set_ko.json()["preferred_languages"], set_ko.json()["effective_locale"]) == (["ko", "ja", "en"], "ko")
    cookies = _locale_cookies(set_ko)
    assert len(cookies) == 1 and cookies[0].startswith("claire_locale=ko;")
    assert "Max-Age=31536000" in cookies[0] and "Path=/" in cookies[0] and "SameSite=lax" in cookies[0] and "HttpOnly" not in cookies[0]
    assert client.request("GET", "/api/me", session=token, cookies={"claire_locale": "en"}, headers={"Accept-Language": "en"}).json()["effective_locale"] == "ko"

    # A change of a different field does not touch the languages or the cookie.
    name_only = client.request("PATCH", "/api/me", session=token, json={"display_name": "New"})
    assert name_only.json()["preferred_languages"] == ["ko", "ja", "en"] and _locale_cookies(name_only) == []

    # null: the account follows the server default language again. Without one, the cookie is cleared and the header decides.
    reset = client.request("PATCH", "/api/me", session=token, json={"preferred_languages": None, "display_name": "Both"}, headers={"Accept-Language": "ko"})
    assert (reset.json()["preferred_languages"], reset.json()["languages"], reset.json()["effective_locale"], reset.json()["display_name"]) == (None, [], "ko", "Both")
    assert "Max-Age=0" in _locale_cookies(reset)[0]
    # A list equal to the server default language is stored as null: the account keeps following it.
    client.request("PUT", "/api/admin/server/languages", session=token, json={"languages": ["ja", "en"]})
    assert client.request("PATCH", "/api/me", session=token, json={"preferred_languages": ["ja"]}).json()["preferred_languages"] is None
    assert _own_languages(settings) is None


@pytest.mark.parametrize("bad", ["ko", ["ko!"], [123], {"code": "ko"}, True, [f"l{i}a" for i in range(11)]])
def test_me_patch_rejects_invalid_languages_before_all_writes(client, settings, bad: Any) -> None:
    _activate_admin(settings)
    token = _session(settings)
    response = client.request("PATCH", "/api/me", session=token, json={"preferred_languages": bad, "display_name": "Changed"})
    assert response.status_code == 400 and response.json() == {"error": "invalid_languages"}
    assert _locale_cookies(response) == []
    me = client.request("GET", "/api/me", session=token).json()
    assert me["preferred_languages"] is None and me["display_name"] == "Admin"
    # The old field is closed: it is an unknown field.
    assert client.request("PATCH", "/api/me", session=token, json={"locale": "ko"}).json()["error"] == "invalid_request"


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
    assert _own_languages(settings) is None


def test_locale_post_moves_the_locale_first_in_the_list_for_an_owner_session_only(client, settings) -> None:
    _activate_admin(settings)
    readonly = _session(settings, scope="readonly")
    assert client.request("POST", "/api/locale", session=readonly, json={"locale": "ko"}).status_code == 200
    assert _own_languages(settings) is None
    assert client.request("POST", "/api/locale", session="s" * 43, json={"locale": "ko"}).status_code == 200
    assert _own_languages(settings) is None

    owner = _session(settings)
    assert client.request("POST", "/api/locale", session=owner, json={"locale": "ko"}).json() == {"locale": "ko", "effective_locale": "ko"}
    assert (_own_languages(settings), _stored_locale(settings)) == (("ko", "en"), "ko")
    # English first is the server default language (en): the account follows it again.
    client.request("POST", "/api/locale", session=owner, json={"locale": "en"})
    assert (_own_languages(settings), _stored_locale(settings)) == (("en", "ko"), "en")
    # null changes only the cookie.
    client.request("POST", "/api/locale", session=owner, json={"locale": None})
    assert _own_languages(settings) == ("en", "ko")


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

    # 2. The visitor selects Korean on the login page. The cookie wins over the header.
    switched = client.request("POST", "/api/locale", json={"locale": "ko"}, headers={"Accept-Language": "en-US"})
    cookie_value = _locale_cookies(switched)[0].split(";")[0].split("=")[1]
    assert cookie_value == "ko"
    assert client.request("GET", "/login", cookies={"claire_locale": cookie_value}, headers={"Accept-Language": "en-US"}).headers["content-language"] == "ko"

    # 3. After login the account has no list and the server has no default language: the cookie still applies in the console.
    _activate_admin(settings)
    token = _session(settings)
    page = client.request("GET", "/account/profile", session=token, cookies={"claire_locale": cookie_value}, headers={"Accept-Language": "en-US"})
    assert page.status_code == 200 and page.headers["content-language"] == "ko"

    # 4. The administrator makes Korean the server default language: the account that follows it gets Korean in a second browser.
    saved = client.request("PUT", "/api/admin/server/languages", session=token, json={"languages": ["ko", "en"]})
    assert saved.status_code == 200 and saved.json() == {"languages": ["ko", "en"]}
    assert client.request("GET", "/account/profile", session=token, headers={"Accept-Language": "en-US"}).headers["content-language"] == "ko"

    # 5. The owner saves an own list with English first. After logout the cookie from the save keeps English on the public login page.
    own = client.request("PATCH", "/api/me", session=token, json={"preferred_languages": ["en", "ko"]})
    assert own.json()["effective_locale"] == "en" and _locale_cookies(own)[0].startswith("claire_locale=en;")
    assert client.request("POST", "/logout", session=token, cookies={"claire_locale": "en"}).status_code == 303
    assert client.request("GET", "/login", cookies={"claire_locale": "en"}, headers={"Accept-Language": "ko"}).headers["content-language"] == "en"

    # 6. Back to the server default language (ko, en).
    token = _session(settings)
    reset = client.request("PATCH", "/api/me", session=token, json={"preferred_languages": None})
    assert reset.json()["preferred_languages"] is None and reset.json()["effective_locale"] == "ko"
    assert client.request("GET", "/account/profile", session=token).headers["content-language"] == "ko"


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
    for bad_next in ("/", "//evil.example/login", "https://evil.example/login", "/login?x=1", "/account/profile"):
        response = client.request("POST", "/api/locale", data={"locale": "ko", "next": bad_next})
        assert response.status_code == 400 and response.json()["error"] == "invalid_request" and _locale_cookies(response) == [], bad_next
    assert client.request("POST", "/api/locale", data={"locale": "ko"}).status_code == 400
    assert client.request("POST", "/api/locale", data={"locale": "ja", "next": "/login"}).json() == {"error": "invalid_locale"}
    assert client.request("POST", "/api/locale", data={"locale": "ko", "next": "/login"}, origin=False).status_code == 403


def test_settings_account_has_the_languages_card_and_the_inline_catalog(client, settings) -> None:
    from claire.i18n import browser_catalog, load_catalog

    _activate_admin(settings)
    token = _session(settings)
    page = client.request("GET", "/account/profile", session=token)
    assert '<html lang="en">' in page.text
    assert 'id="settings-languages"' in page.text and 'data-languages-state="none"' in page.text and "data-languages-none" in page.text and 'id="settings-locale"' not in page.text
    assert '<li data-lang="en">' in page.text and "data-lang-follow" not in page.text
    assert '<script src="/static/js/console_languages.js" defer></script>' in page.text
    bootstrap = _bootstrap_json(page.text, "console-bootstrap")
    assert (bootstrap["preferred_languages"], bootstrap["languages"], bootstrap["effective_locale"]) == (None, [], "en")
    inline = _bootstrap_json(page.text, "claire-i18n-catalog")
    # The inline catalog has no server-only domains (auth pages, bot, CLI). Of the console domain, it has only the keys of the console scripts; a page that is not in the administrator area does not get the keys of the administrator scripts.
    assert inline == browser_catalog("en", scope="console") and inline["common.action.save"] == "Save"
    assert not [key for key in inline if key.startswith(("auth.", "bot.", "cli."))]
    assert not [key for key in inline if key.startswith("console.") and not key.startswith("console.js.")] and not [key for key in inline if key.startswith("console.js.admin.")]
    assert "console.js.languages.saved" in inline and "console.js.languages.saved" not in browser_catalog("en")
    # i18n.js comes after the inline catalog and before the deferred console scripts.
    assert page.text.index('id="claire-i18n-catalog"') < page.text.index('<script src="/static/js/i18n.js"></script>') < page.text.index("/static/js/console_account.js")

    client.request("PATCH", "/api/me", session=token, json={"preferred_languages": ["ko"]})
    page = client.request("GET", "/account/profile", session=token, headers={"Accept-Language": "en"})
    assert '<html lang="ko">' in page.text and "선호 언어" in page.text and 'data-languages-state="own"' in page.text and "data-lang-follow" in page.text
    assert page.text.index('<li data-lang="ko">') < page.text.index('<li data-lang="en">')
    bootstrap = _bootstrap_json(page.text, "console-bootstrap")
    assert bootstrap["preferred_languages"] == ["ko", "en"] and bootstrap["effective_locale"] == "ko"
    assert _bootstrap_json(page.text, "claire-i18n-catalog") == browser_catalog("ko", scope="console")

    script = client.request("GET", "/static/js/i18n.js")
    assert script.status_code == 200 and "ClaireI18n" in script.text
    assert client.request("GET", "/static/locales/ko.json").json() == load_catalog("ko")
