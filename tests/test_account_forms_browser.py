"""실제 Chromium으로 계정 양식(``/setup``, ``/login``, ``/logout``)을 제출한다.

전역 ``Referrer-Policy: no-referrer`` 아래에서는 Chromium이 양식 POST에 ``Origin: null``을 실어
``CORSPolicyMiddleware``가 403으로 막았다(H1). 헤더 단정(`test_account_routes`)만으로는 브라우저 동작을
보장하지 못하므로, 설치된 Playwright Chromium이 있으면 실제 제출을 확인한다. 없으면 건너뛴다.

서버는 ``tmp_path``의 data_dir로 127.0.0.1의 빈 포트에서 띄운다.
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
from claire.store import db as dbm  # noqa: E402

OWNER_TOKEN = "owner-" + "o" * 32
USERNAME = "kim_browser"
PASSWORD = "Browser-Form-Pass-2026!"


@dataclass
class _Settings:
    db_file: Path
    data_dir: Path
    public_url: str
    environment: str = "development"
    inject_token: str = OWNER_TOKEN
    readonly_token: str = ""
    cors_allowed_origins: str = ""
    anonymous_readonly: bool = False


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return int(s.getsockname()[1])


@pytest.fixture
def live_server(tmp_path: Path):
    port = _free_port()
    base = f"http://127.0.0.1:{port}"
    settings = _Settings(db_file=tmp_path / "claire.db", data_dir=tmp_path, public_url=base)
    app = server.create_app(settings, SimpleNamespace(provider=SimpleNamespace(name="stub")))
    config = uvicorn.Config(app, host="127.0.0.1", port=port, log_level="warning", lifespan="on")
    srv = uvicorn.Server(config)
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
        except Exception as exc:  # noqa: BLE001  # 브라우저 바이너리가 없는 환경
            pytest.skip(f"Playwright Chromium is not available: {exc}")
        try:
            yield b
        finally:
            b.close()


def _live_sessions(settings: _Settings) -> set[str]:
    conn = dbm.connect_existing(settings.db_file)
    try:
        return {r[0] for r in conn.execute("SELECT session_token FROM auth_sessions WHERE approved=1")}
    finally:
        conn.close()


def test_setup_login_logout_forms_submit_from_real_chromium(live_server, browser):
    base, settings = live_server
    conn = dbm.connect_existing(settings.db_file)
    try:
        first = accounts_store.create_account_session(conn, account_id=accounts_store.ADMIN_ACCOUNT_ID)
    finally:
        conn.close()

    ctx = browser.new_context()
    ctx.add_cookies([{"name": "claire_session", "value": first, "url": base}])
    origins: list[str | None] = []
    ctx.on("request", lambda r: origins.append(r.headers.get("origin")) if r.method == "POST" else None)
    page = ctx.new_page()

    def session_cookie() -> str | None:
        values = [c["value"] for c in ctx.cookies(base) if c["name"] == "claire_session"]
        return values[0] if values else None

    # /setup 확정 → / 로 이동하고, 브라우저 쿠키는 새로 발급된 살아 있는 세션이다.
    assert page.goto(f"{base}/setup").status == 200
    page.fill("#username", USERNAME)
    page.fill("#display_name", "브라우저")
    page.fill("#password", PASSWORD)
    page.fill("#password_confirm", PASSWORD)
    page.check("input[name=ack]")
    with page.expect_navigation():
        page.click("button[type=submit]")
    assert page.url.rstrip("/") == base, page.url
    after_setup = session_cookie()
    live = _live_sessions(settings)
    assert after_setup and after_setup != first and after_setup in live
    assert first not in live
    assert page.goto(f"{base}/me").status == 200

    # 로그아웃 양식 → /login, 세션 삭제
    with page.expect_navigation():
        page.click("form[action='/logout'] button")
    assert page.url == f"{base}/login"
    assert after_setup not in _live_sessions(settings)
    assert page.goto(f"{base}/me").status == 404

    # 로그인 양식 → /, 새 세션
    page.goto(f"{base}/login")
    page.fill("#username", USERNAME)
    page.fill("#password", PASSWORD)
    with page.expect_navigation():
        page.click("button[type=submit]")
    assert page.url.rstrip("/") == base, page.url
    assert session_cookie() in _live_sessions(settings)
    assert page.goto(f"{base}/me").status == 200

    # 세 양식 제출 모두 실제 origin을 실었다(Origin: null이 아니다).
    assert origins and all(o == base for o in origins), origins
    ctx.close()
