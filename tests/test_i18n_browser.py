"""End-to-end test of the language switch in real Chromium (I18N design unit IP2-1): the form without JavaScript on ``/login`` and the selector on ``/settings/account``.

The server runs on a free port of 127.0.0.1 with a data directory in ``tmp_path``. The test is skipped when Playwright Chromium is not installed.
"""

from __future__ import annotations

import socket
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from types import SimpleNamespace

import pytest

sync_api = pytest.importorskip("playwright.sync_api")
uvicorn = pytest.importorskip("uvicorn")

from claire.api import server  # noqa: E402
from claire.store import accounts as accounts_store  # noqa: E402
from claire.store import auth_db  # noqa: E402
from claire.store import db as dbm  # noqa: E402

USERNAME = "kim_locale"
PASSWORD = "Locale-Switch-Pass-2026!"


@dataclass
class _Settings:
    db_file: Path
    data_dir: Path
    public_url: str
    environment: str = "development"
    inject_token: str = "owner-" + "o" * 32
    readonly_token: str = ""
    cors_allowed_origins: str = ""
    anonymous_readonly: bool = False
    ui_lang: str = ""


@pytest.fixture
def live_server(tmp_path: Path):
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        port = int(s.getsockname()[1])
    base = f"http://127.0.0.1:{port}"
    settings = _Settings(db_file=tmp_path / "claire.db", data_dir=tmp_path, public_url=base)
    app = server.create_app(settings, SimpleNamespace(provider=SimpleNamespace(name="stub")))
    srv = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=port, log_level="warning", lifespan="on"))
    thread = threading.Thread(target=srv.run, daemon=True)
    thread.start()
    deadline = time.monotonic() + 15
    while not srv.started:
        if not thread.is_alive() or time.monotonic() > deadline:
            raise RuntimeError("test server did not start")
        time.sleep(0.05)
    try:
        yield base, settings
    finally:
        srv.should_exit = True
        thread.join(timeout=15)


@pytest.fixture
def browser():
    with sync_api.sync_playwright() as p:
        try:
            b = p.chromium.launch()
        except Exception as exc:  # noqa: BLE001  # no browser binary in this environment
            pytest.skip(f"Playwright Chromium is not available: {exc}")
        try:
            yield b
        finally:
            b.close()


def _stored_locale(settings: _Settings) -> str | None:
    conn = dbm.connect_existing(settings.db_file)
    try:
        return accounts_store.AccountStore(conn).get(accounts_store.ADMIN_ACCOUNT_ID).locale
    finally:
        conn.close()


def test_language_switch_on_login_and_in_account_settings(live_server, browser) -> None:
    base, settings = live_server
    conn = dbm.connect_existing(settings.db_file)
    auth = auth_db.connect_auth_db(auth_db.auth_db_path(settings.data_dir))
    try:
        accounts_store.AccountStore(conn).complete_setup(accounts_store.ADMIN_ACCOUNT_ID, username=USERNAME, display_name="Locale")
        auth_db.set_password(auth, accounts_store.ADMIN_ACCOUNT_ID, PASSWORD, username=USERNAME)
    finally:
        auth.close()
        conn.close()

    ctx = browser.new_context(locale="en-US")
    page = ctx.new_page()
    lang = lambda: page.get_attribute("html", "lang")  # noqa: E731

    def locale_cookie() -> str | None:
        values = [c["value"] for c in ctx.cookies(base) if c["name"] == "claire_locale"]
        return values[0] if values else None

    # 1. /login: English from the browser language. The form switch needs no JavaScript.
    response = page.goto(f"{base}/login")
    assert response.status == 200 and response.headers["content-language"] == "en"
    assert lang() == "en" and page.inner_text("h1") == "Log in"
    assert page.evaluate("typeof window.ClaireI18n") == "undefined"
    with page.expect_navigation():
        page.click("form.locale-switch button[value=ko]")
    assert page.url == f"{base}/login" and lang() == "ko" and page.inner_text("h1") == "로그인"
    assert locale_cookie() == "ko"

    # 2. Log in. The account has no preference, thus the cookie gives Korean in the console.
    page.fill("#username", USERNAME)
    page.fill("#password", PASSWORD)
    with page.expect_navigation():
        page.click("form[action='/login'] button[type=submit]")
    response = page.goto(f"{base}/settings/account")
    assert response.status == 200 and response.headers["content-language"] == "ko" and lang() == "ko"
    assert page.input_value("#pref-account-locale") == "" and _stored_locale(settings) is None
    assert page.evaluate("[ClaireI18n.getLocale(), ClaireI18n.t('common.action.save'), ClaireI18n.tn('doc.count', 2), ClaireI18n.t('missing.key')]") == ["ko", "저장", "2개 문서", "missing.key"]

    # 3. The selector stores English on the account and the page loads again in English.
    with page.expect_navigation():
        page.select_option("#pref-account-locale", "en")
    assert lang() == "en" and page.input_value("#pref-account-locale") == "en"
    assert _stored_locale(settings) == "en" and locale_cookie() == "en"
    assert page.inner_text("#settings-locale h2") == "🌐 Interface language"
    assert page.evaluate("ClaireI18n.tn('doc.count', 1)") == "1 document"

    # 4. A second browser with a Korean language setting and no cookie gets the account preference.
    other = browser.new_context(locale="ko-KR")
    other.add_cookies([c for c in ctx.cookies(base) if c["name"] == "claire_session"])
    other_page = other.new_page()
    other_page.goto(f"{base}/settings/account")
    assert other_page.get_attribute("html", "lang") == "en"
    other.close()

    # 5. Automatic: the account column is NULL, the cookie is removed, and the browser language applies.
    with page.expect_navigation():
        page.select_option("#pref-account-locale", "")
    assert _stored_locale(settings) is None and locale_cookie() is None and lang() == "en"
    ctx.close()
