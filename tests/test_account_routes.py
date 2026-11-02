"""``/admin/accounts``, ``/me``, 단계 인증 (설계 §8.5–§8.7).

모든 상태는 ``tmp_path``의 data_dir에만 쓴다.
"""

from __future__ import annotations

import time
from dataclasses import dataclass
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
from starlette.testclient import TestClient

from claire.api import server
from claire.api.accounts_routes import require_recent_auth
from claire.api.principal import STEP_UP_WINDOW_SECONDS, Principal
from claire.store import accounts as accounts_store
from claire.store import auth_db
from claire.store import db as dbm

OWNER_TOKEN = "owner-" + "o" * 32
READONLY_TOKEN = "readonly-" + "r" * 32
PUBLIC_URL = "http://127.0.0.1:8765"
ORIGIN = PUBLIC_URL
USERNAME = "kim_lee"
PASSWORD = "correct-horse-battery"
NEW_PASSWORD = "brand-new-passphrase-42"


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


class _Client:
    def __init__(self, client: TestClient) -> None:
        self.client = client

    def request(self, method: str, path: str, *, session: str | None = None, bearer: str | None = None,
                **kwargs: Any):
        headers = dict(kwargs.pop("headers", {}) or {})
        if session is not None:
            headers["Cookie"] = f"claire_session={session}"
        if bearer is not None:
            headers["Authorization"] = f"Bearer {bearer}"
        if method not in {"GET", "HEAD"}:
            headers["Origin"] = ORIGIN
        self.client.cookies.clear()
        response = self.client.request(method, path, headers=headers, follow_redirects=False, **kwargs)
        self.client.cookies.clear()
        return response


@pytest.fixture
def settings(tmp_path: Path) -> _Settings:
    return _Settings(db_file=tmp_path / "claire.db", data_dir=tmp_path)


@pytest.fixture
def client(settings: _Settings):
    app = server.create_app(settings, SimpleNamespace(provider=SimpleNamespace(name="stub")))
    with TestClient(app, base_url=PUBLIC_URL) as tc:
        yield _Client(tc)


def _db(settings):
    return dbm.connect_existing(settings.db_file)


def _auth(settings):
    return auth_db.connect_auth_db(auth_db.auth_db_path(settings.data_dir))


def _activate_admin(settings, *, with_password: bool = True) -> None:
    conn = _db(settings)
    auth = _auth(settings)
    try:
        accounts_store.AccountStore(conn).complete_setup(1, username=USERNAME, display_name="관리자")
        if with_password:
            auth_db.set_password(auth, 1, PASSWORD, username=USERNAME)
    finally:
        auth.close()
        conn.close()


def _session(settings, account_id: int = 1, *, authenticated_ago: float = 0.0, scope: str = "owner") -> str:
    conn = _db(settings)
    try:
        return accounts_store.create_account_session(
            conn, account_id=account_id, scope=scope, now=time.time() - authenticated_ago
        )
    finally:
        conn.close()


def _add_member(settings, username: str = "member_one") -> int:
    conn = _db(settings)
    try:
        cur = conn.execute(
            "INSERT INTO accounts (public_id, username, role, status, created_at, updated_at) "
            "VALUES (?, ?, 'member', 'active', 0, 0)",
            (123456789012, username),
        )
        conn.commit()
        return int(cur.lastrowid)
    finally:
        conn.close()


def _session_exists(settings, token: str) -> bool:
    conn = _db(settings)
    try:
        return conn.execute("SELECT 1 FROM auth_sessions WHERE session_token=?", (token,)).fetchone() is not None
    finally:
        conn.close()


# --- /admin/accounts --------------------------------------------------------------


def test_admin_accounts_hidden_from_everyone_but_the_admin(client, settings):
    _activate_admin(settings)
    member_id = _add_member(settings)
    member_session = _session(settings, member_id)
    for path in ("/admin/accounts", "/api/admin/accounts"):
        assert client.request("GET", path).status_code == 404
        assert client.request("GET", path, bearer=READONLY_TOKEN).status_code == 404
        assert client.request("GET", path, session=_session(settings, scope="readonly")).status_code == 404
        # 멤버의 owner 세션도 관리자 경로에서는 404다(존재를 숨긴다).
        assert client.request("GET", path, session=member_session).status_code == 404
        assert client.request("GET", path, session=_session(settings)).status_code == 200
        assert client.request("GET", path, bearer=OWNER_TOKEN).status_code == 200
    # 계정 메타데이터만 있고 쓰기·콘텐츠 경로는 없다(K5).
    assert client.request("PATCH", "/api/admin/accounts", session=_session(settings), json={}).status_code == 404


def test_admin_accounts_api_and_single_mode_card(client, settings):
    _activate_admin(settings)
    token = _session(settings)
    data = client.request("GET", "/api/admin/accounts", session=token).json()
    admin = next(a for a in data["accounts"] if a["role"] == "admin")
    assert isinstance(admin["public_id"], str) and admin["public_id"].isdigit()
    assert admin["username"] == USERNAME
    assert admin["status"] == "active"
    assert admin["theme_count"] >= 1
    assert admin["activated_at"] is not None
    assert set(admin) == {"public_id", "username", "status", "role", "created_at", "activated_at", "theme_count"}

    page = client.request("GET", "/admin/accounts", session=token)
    assert page.status_code == 200
    assert "관리자 계정" in page.text and admin["public_id"] in page.text and USERNAME in page.text
    assert "<script" not in page.text


# --- /me ------------------------------------------------------------------------


def test_me_requires_an_account_credential(client, settings):
    for method, path in (("GET", "/me"), ("GET", "/api/me"), ("PATCH", "/api/me"), ("POST", "/api/me/password")):
        assert client.request(method, path).status_code == 404


def test_me_read_and_display_name_update(client, settings):
    _activate_admin(settings)
    token = _session(settings)
    me = client.request("GET", "/api/me", session=token).json()
    assert me == {
        "public_id": me["public_id"],
        "username": USERNAME,
        "display_name": "관리자",
        "role": "admin",
        "scope": "owner",
        "status": "active",
        "has_password": True,
    }
    assert isinstance(me["public_id"], str)

    updated = client.request("PATCH", "/api/me", session=token, json={"display_name": "<b>김</b>"})
    assert updated.status_code == 200 and updated.json()["display_name"] == "<b>김</b>"
    page = client.request("GET", "/me", session=token)
    assert page.status_code == 200
    assert "&lt;b&gt;김&lt;/b&gt;" in page.text and "<b>김</b>" not in page.text
    # 인라인 스크립트·핸들러 없이 외부 스크립트만 쓴다(CSP).
    assert '<script src="/static/js/accounts.js" defer></script>' in page.text
    assert page.text.count("<script") == 1
    assert " onsubmit=" not in page.text and " onclick=" not in page.text

    assert client.request("PATCH", "/api/me", session=token, json={"username": "x"}).status_code == 400
    assert client.request(
        "PATCH", "/api/me", session=token, json={"display_name": "a", "role": "admin"}
    ).status_code == 400
    assert client.request("PATCH", "/api/me", session=token, json={"display_name": "a\nb"}).status_code == 400
    assert client.request("PATCH", "/api/me", session=token, json={"display_name": "x" * 65}).status_code == 400
    assert client.request("GET", "/api/me", session=token).json()["display_name"] == "<b>김</b>"


def test_me_bearer_owner_reads_admin_account(client, settings):
    _activate_admin(settings)
    me = client.request("GET", "/api/me", bearer=OWNER_TOKEN)
    assert me.status_code == 200 and me.json()["username"] == USERNAME


# --- 단계 인증과 비밀번호 변경 -------------------------------------------------------------


def test_require_recent_auth_contract():
    now = 1_000_000.0
    bearer = Principal(1, "admin", "owner", "bearer", "env:owner")
    stale = Principal(1, "admin", "owner", "cookie", "s", now - STEP_UP_WINDOW_SECONDS - 1)
    fresh = Principal(1, "admin", "owner", "x-session", "s", now - 5)
    denied = require_recent_auth(bearer, now=now)
    assert denied is not None and denied.status_code == 403
    denied = require_recent_auth(stale, now=now)
    assert denied is not None and denied.status_code == 401
    assert "reauth_required" in denied.body.decode()
    assert require_recent_auth(fresh, now=now) is None


def test_password_change_requires_step_up(client, settings):
    _activate_admin(settings)
    stale = _session(settings, authenticated_ago=STEP_UP_WINDOW_SECONDS + 60)
    body = {"current_password": PASSWORD, "new_password": NEW_PASSWORD}

    response = client.request("POST", "/api/me/password", session=stale, json=body)
    assert response.status_code == 401 and response.json()["error"] == "reauth_required"
    response = client.request("POST", "/api/me/password", bearer=OWNER_TOKEN, json=body)
    assert response.status_code == 403 and response.json()["error"] == "step_up_requires_session"

    wrong = client.request("POST", "/api/auth/reauth", session=stale, json={"password": "nope-nope-nope"})
    assert wrong.status_code == 403 and wrong.json()["error"] == "invalid_password"
    assert client.request("POST", "/api/auth/reauth", bearer=OWNER_TOKEN, json={"password": PASSWORD}).status_code == 403
    ok = client.request("POST", "/api/auth/reauth", session=stale, json={"password": PASSWORD})
    assert ok.status_code == 200 and ok.json()["ok"] is True

    response = client.request("POST", "/api/me/password", session=stale, json=body)
    assert response.status_code == 200


def test_password_change_checks_current_password_and_policy(client, settings):
    _activate_admin(settings)
    token = _session(settings)
    wrong = client.request(
        "POST", "/api/me/password", session=token,
        json={"current_password": "wrong-password-1", "new_password": NEW_PASSWORD},
    )
    assert wrong.status_code == 403 and wrong.json()["error"] == "invalid_password"
    missing = client.request("POST", "/api/me/password", session=token, json={"new_password": NEW_PASSWORD})
    assert missing.status_code == 403
    short = client.request(
        "POST", "/api/me/password", session=token, json={"current_password": PASSWORD, "new_password": "q9z!x"}
    )
    assert short.status_code == 400 and short.json()["reason"] == "too_short"
    contains = client.request(
        "POST", "/api/me/password", session=token,
        json={"current_password": PASSWORD, "new_password": f"my-{USERNAME}-secret!"},
    )
    assert contains.status_code == 400 and contains.json()["reason"] == "contains_username"
    for bad in ("new password with spaces", "새비밀번호새비밀번호새비밀번호", "tab\tseparated-pass1"):
        rejected = client.request(
            "POST", "/api/me/password", session=token, json={"current_password": PASSWORD, "new_password": bad}
        )
        assert rejected.status_code == 400 and rejected.json()["reason"] == "invalid_characters"
        assert "표준 특수문자" in rejected.json()["message"]
    auth = _auth(settings)
    try:
        assert auth_db.verify_password(auth, 1, PASSWORD)
    finally:
        auth.close()


def test_password_change_revokes_other_sessions_only(client, settings):
    _activate_admin(settings)
    current = _session(settings)
    others = [_session(settings), _session(settings, scope="readonly")]
    member_id = _add_member(settings)
    member_session = _session(settings, member_id)

    response = client.request(
        "POST", "/api/me/password", session=current,
        json={"current_password": PASSWORD, "new_password": NEW_PASSWORD},
    )
    assert response.status_code == 200
    assert response.json() == {"ok": True, "other_sessions_revoked": 2}
    assert _session_exists(settings, current)
    assert not any(_session_exists(settings, t) for t in others)
    assert _session_exists(settings, member_session)
    # 환경변수·API 토큰은 비밀번호에서 나온 자격증명이 아니므로 그대로다.
    assert client.request("GET", "/api/me", bearer=OWNER_TOKEN).status_code == 200

    auth = _auth(settings)
    try:
        assert auth_db.verify_password(auth, 1, NEW_PASSWORD)
        actions = [e["action"] for e in auth_db.list_admin_audit(auth)]
    finally:
        auth.close()
    assert "account.password_change" in actions
    login = client.request("POST", "/login", data={"username": USERNAME, "password": NEW_PASSWORD})
    assert login.status_code == 303


def test_password_can_be_set_without_current_when_missing(client, settings):
    """설정 중 비밀번호 저장이 실패한 계정의 복구 경로(§8.4): 단계 인증만으로 설정한다."""
    _activate_admin(settings, with_password=False)
    token = _session(settings)
    assert client.request("GET", "/api/me", session=token).json()["has_password"] is False
    assert client.request(
        "POST", "/api/auth/reauth", session=token, json={"password": "anything-at-all"}
    ).status_code == 409
    response = client.request("POST", "/api/me/password", session=token, json={"new_password": NEW_PASSWORD})
    assert response.status_code == 200
    assert client.request("GET", "/api/me", session=token).json()["has_password"] is True
    # 이제는 현재 비밀번호가 필요하다.
    again = client.request("POST", "/api/me/password", session=token, json={"new_password": "another-passphrase-7"})
    assert again.status_code == 403


def test_password_change_rejected_before_setup(client, settings):
    token = _session(settings)
    response = client.request("POST", "/api/me/password", session=token, json={"new_password": NEW_PASSWORD})
    assert response.status_code == 409 and response.json()["error"] == "account_not_active"


# --- 양식 화면의 Referrer-Policy (H1), 세션 쿠키 하나 (H2), 쿠키 PATCH의 동일 origin (M4) ---------


def _session_cookies(response) -> list[str]:
    return [raw for raw in response.headers.get_list("set-cookie") if raw.startswith("claire_session=")]


def test_account_form_pages_send_same_origin_referrer_policy(client, settings):
    """양식을 담은 계정 화면은 ``Referrer-Policy: same-origin``이다. 전역 ``no-referrer``면 Chromium 양식 POST가 ``Origin: null``이 된다."""
    setup_token = _session(settings)
    setup_page = client.request("GET", "/setup", session=setup_token)
    assert setup_page.status_code == 200
    assert setup_page.headers["referrer-policy"] == "same-origin"

    _activate_admin(settings)
    token = _session(settings)
    for path in ("/me", "/admin/accounts"):
        page = client.request("GET", path, session=token)
        assert page.status_code == 200, path
        assert page.headers["referrer-policy"] == "same-origin", path
        assert 'action="/logout"' in page.text
    assert client.request("GET", "/login").headers["referrer-policy"] == "same-origin"
    # JSON API와 404는 전역 정책 그대로다.
    assert client.request("GET", "/api/me", session=token).headers["referrer-policy"] == "no-referrer"
    assert client.request("GET", "/me").headers["referrer-policy"] == "no-referrer"


def test_cookie_routes_emit_exactly_one_session_cookie(client, settings):
    _activate_admin(settings)
    token = _session(settings)
    # 핸들러가 쿠키를 건드리지 않는 응답: 미들웨어의 갱신 쿠키 하나.
    for method, path, kwargs in (
        ("GET", "/me", {}),
        ("PATCH", "/api/me", {"json": {"display_name": "a"}}),
        ("POST", "/api/me/password", {"json": {"current_password": PASSWORD, "new_password": NEW_PASSWORD}}),
    ):
        response = client.request(method, path, session=token, **kwargs)
        assert response.status_code == 200, (path, response.text)
        cookies = _session_cookies(response)
        assert len(cookies) == 1 and cookies[0].startswith(f"claire_session={token};"), (path, cookies)
    # 로그아웃은 공개 라우트다. 지우는 쿠키 하나만 낸다.
    logout = client.request("POST", "/logout", session=token)
    assert logout.status_code == 303
    cookies = _session_cookies(logout)
    assert len(cookies) == 1 and token not in cookies[0]


def test_cookie_patch_without_same_origin_is_forbidden(client, settings):
    _activate_admin(settings)
    token = _session(settings)
    headers = {"Cookie": f"claire_session={token}"}
    client.client.cookies.clear()
    no_origin = client.client.request(
        "PATCH", "/api/me", headers=headers, json={"display_name": "x"}, follow_redirects=False
    )
    assert no_origin.status_code == 403
    assert client.request("GET", "/api/me", session=token).json()["display_name"] == "관리자"
