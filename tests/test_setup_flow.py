"""최초 관리자 설정 ``/setup`` (설계 §8.4, §18.6).

모든 상태는 ``tmp_path``의 data_dir에만 쓴다.
"""

from __future__ import annotations

import json
import threading
import time
from dataclasses import dataclass
from http.cookies import SimpleCookie
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
from starlette.testclient import TestClient

from claire.api import server
from claire.api.accounts_routes import common as accounts_common
from claire.api.accounts_routes import setup as setup_routes
from claire.api.principal import Principal
from claire.api.security import WebRuntimeConfig
from claire.store import accounts as accounts_store
from claire.store import auth_db
from claire.store import db as dbm

OWNER_TOKEN = "owner-" + "o" * 32
READONLY_TOKEN = "readonly-" + "r" * 32
PUBLIC_URL = "http://127.0.0.1:8765"
ORIGIN = PUBLIC_URL
PASSWORD = "correct-horse-battery"


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
    # 인스턴스 예약 라벨(§8.1-4)의 출처. 개발 모드 공개 URL은 IP라 이 값으로 확인한다.
    fqdn: str = "notes.kimhome.net"


class _Client:
    """쿠키 저장소를 쓰지 않고 요청마다 세션 쿠키를 직접 붙인다(한 요청에 자격증명 하나)."""

    def __init__(self, client: TestClient) -> None:
        self.client = client

    def request(
        self,
        method: str,
        path: str,
        *,
        session: str | None = None,
        bearer: str | None = None,
        x_session: str | None = None,
        origin: str | None = ORIGIN,
        **kwargs: Any,
    ):
        headers = dict(kwargs.pop("headers", {}) or {})
        if session is not None:
            headers["Cookie"] = f"claire_session={session}"
        if bearer is not None:
            headers["Authorization"] = f"Bearer {bearer}"
        if x_session is not None:
            headers["X-Session"] = x_session
        if origin is not None and method not in {"GET", "HEAD"}:
            headers["Origin"] = origin
        self.client.cookies.clear()
        response = self.client.request(method, path, headers=headers, follow_redirects=False, **kwargs)
        self.client.cookies.clear()
        return response


def _new_session_token(response) -> str | None:
    """응답이 설정한 ``claire_session`` 값. 모든 Set-Cookie를 읽고, ``claire_session``이 정확히 하나인지 단정한다.

    둘 이상이면 브라우저는 마지막 것을 갖는다. 미들웨어가 핸들러의 새 토큰 뒤에 옛 토큰을 다시 붙이던 결함(H2)을 여기서 잡는다.
    """
    values: list[str] = []
    for raw in response.headers.get_list("set-cookie"):
        cookie = SimpleCookie()
        cookie.load(raw)
        morsel = cookie.get("claire_session")
        if morsel is not None:
            values.append(morsel.value)
    assert len(values) == 1, f"expected exactly one claire_session Set-Cookie, got {values}"
    return values[0] or None


@pytest.fixture
def settings(tmp_path: Path) -> _Settings:
    return _Settings(db_file=tmp_path / "claire.db", data_dir=tmp_path)


@pytest.fixture
def client(settings: _Settings):
    app = server.create_app(settings, SimpleNamespace(provider=SimpleNamespace(name="stub")))
    with TestClient(app, base_url=PUBLIC_URL) as tc:
        yield _Client(tc)


def _db(settings: _Settings):
    return dbm.connect_existing(settings.db_file)


def _auth(settings: _Settings):
    return auth_db.connect_auth_db(auth_db.auth_db_path(settings.data_dir))


def _admin_session(settings: _Settings) -> str:
    conn = _db(settings)
    try:
        return accounts_store.create_account_session(conn, account_id=accounts_store.ADMIN_ACCOUNT_ID)
    finally:
        conn.close()


def _admin(settings: _Settings) -> accounts_store.Account:
    conn = _db(settings)
    try:
        return accounts_store.AccountStore(conn).admin()
    finally:
        conn.close()


def _form(username: str = "kim_lee", *, password: str = PASSWORD, confirm: str | None = None,
          display_name: str = "김 리", ack: bool = True) -> dict[str, str]:
    data = {
        "username": username,
        "display_name": display_name,
        "password": password,
        "password_confirm": password if confirm is None else confirm,
    }
    if ack:
        data["ack"] = "1"
    return data


def test_auth_db_is_prepared_at_startup(client, settings):
    path = auth_db.auth_db_path(settings.data_dir)
    assert path.is_file()
    assert path.is_relative_to(settings.data_dir)


def test_setup_is_404_without_admin_session(client, settings):
    assert client.request("GET", "/setup").status_code == 404
    assert client.request("POST", "/setup", data=_form()).status_code == 404
    # bearer(환경 owner 토큰 = 관리자 계정)도 404다. 사람의 세션 채널만 설정할 수 있다.
    assert client.request("GET", "/setup", bearer=OWNER_TOKEN).status_code == 404
    assert client.request("POST", "/setup", bearer=OWNER_TOKEN, data=_form()).status_code == 404
    assert client.request("GET", "/setup", bearer=READONLY_TOKEN).status_code == 404
    assert _admin(settings).status == "setup_required"


def test_root_redirects_admin_session_to_setup_but_not_bearer(client, settings):
    token = _admin_session(settings)
    for kwargs in ({"session": token}, {"x_session": token}):
        response = client.request("GET", "/", **kwargs)
        assert response.status_code == 302
        assert response.headers["location"] == "/setup"
    # bearer API 응답은 그대로다(E1).
    assert client.request("GET", "/", bearer=OWNER_TOKEN).status_code == 200


def test_setup_page_shows_both_confirmation_notices(client, settings):
    token = _admin_session(settings)
    response = client.request("GET", "/setup", session=token)
    assert response.status_code == 200
    assert "You cannot change the USERNAME after you confirm it." in response.text
    assert "In network mode the address /@USERNAME is public." in response.text
    korean = client.request("GET", "/setup", session=token, headers={"Accept-Language": "ko"})
    assert "USERNAME은 확정한 뒤에 바꿀 수 없습니다." in korean.text and "네트워크 모드에서는 /@USERNAME 주소로 공개됩니다." in korean.text
    assert "<script" not in response.text
    assert " onclick=" not in response.text and " onsubmit=" not in response.text


def test_setup_success_rotates_session_and_closes_setup(client, settings):
    old = _admin_session(settings)
    response = client.request("POST", "/setup", session=old, data=_form())
    assert response.status_code == 303
    assert response.headers["location"] == "/"
    new = _new_session_token(response)
    assert new and new != old

    admin = _admin(settings)
    assert (admin.status, admin.username, admin.display_name) == ("active", "kim_lee", "김 리")
    auth = _auth(settings)
    try:
        assert auth_db.verify_password(auth, admin.id, PASSWORD)
    finally:
        auth.close()

    # 옛 토큰은 지워졌다(세션 고정 방지).
    assert client.request("GET", "/me", session=old).status_code == 404
    assert client.request("GET", "/me", session=new).status_code == 302
    assert client.request("GET", "/settings/account", session=old).status_code == 404
    assert client.request("GET", "/settings/account", session=new).status_code == 200
    # 확정 뒤에는 / 가 그대로 열리고 /setup은 다시 열리지 않는다.
    assert client.request("GET", "/", session=new).status_code == 200
    assert client.request("GET", "/setup", session=new).status_code == 404
    assert client.request("POST", "/setup", session=new, data=_form("park_lee")).status_code == 404
    assert _admin(settings).username == "kim_lee"


def test_setup_writes_audit_without_username(client, settings):
    token = _admin_session(settings)
    assert client.request("POST", "/setup", session=token, data=_form()).status_code == 303
    auth = _auth(settings)
    try:
        entries = auth_db.list_admin_audit(auth)
        raw_rows = auth.execute("SELECT action, detail_json FROM admin_audit").fetchall()
    finally:
        auth.close()
    setup_entries = [e for e in entries if e["action"] == "account.setup"]
    assert len(setup_entries) == 1
    assert setup_entries[0]["actor_account_id"] == 1
    assert setup_entries[0]["target_account_id"] == 1
    assert all("kim_lee" not in (row["detail_json"] or "") for row in raw_rows)
    assert "username" not in json.dumps(setup_entries[0]["detail"])


@pytest.mark.parametrize(
    ("form", "reason"),
    [
        (_form("Bad Name"), "syntax"),
        (_form("kim__lee"), "syntax"),
        (_form("admin_kim"), "reserved"),
        (_form("support"), "reserved"),
        (_form("kimhome"), "reserved"),  # CLAIRE_FQDN 라벨(인스턴스 예약)
        (_form(password="q9z!x"), "too_short"),
        (_form(password="xx-kim_lee-xx-yy"), "contains_username"),
        (_form(password="has spaces in it"), "invalid_characters"),
        (_form(password="한글비밀번호입니다아주길게"), "invalid_characters"),
        (_form(confirm=PASSWORD + "!"), "mismatch"),
    ],
)
def test_setup_rejects_with_reason_only(client, settings, form, reason):
    token = _admin_session(settings)
    response = client.request("POST", "/setup", session=token, data=form)
    assert response.status_code == 400
    assert f"({reason})" in response.text
    assert form["password"] not in response.text
    assert _admin(settings).status == "setup_required"
    auth = _auth(settings)
    try:
        assert not auth_db.has_password(auth, 1)
    finally:
        auth.close()
    # 거부 뒤에도 같은 세션으로 다시 시도할 수 있다.
    assert client.request("GET", "/setup", session=token).status_code == 200


def test_setup_rejects_retired_and_taken_usernames(client, settings):
    conn = _db(settings)
    try:
        conn.execute(
            "INSERT INTO username_tombstones (username, account_id, retired_at) VALUES ('old_name', 5, ?)",
            (time.time(),),
        )
        conn.execute(
            "INSERT INTO accounts (public_id, username, role, status, created_at, updated_at) "
            "VALUES (987654321, 'taken_name', 'member', 'active', 0, 0)"
        )
        conn.commit()
    finally:
        conn.close()
    token = _admin_session(settings)
    for username, reason in (("old_name", "retired"), ("taken_name", "taken")):
        response = client.request("POST", "/setup", session=token, data=_form(username))
        assert response.status_code == 400
        assert f"({reason})" in response.text
    assert _admin(settings).status == "setup_required"


def test_setup_requires_acknowledgement(client, settings):
    token = _admin_session(settings)
    response = client.request("POST", "/setup", session=token, data=_form(ack=False))
    assert response.status_code == 400
    assert _admin(settings).status == "setup_required"


def test_setup_post_with_cookie_requires_same_origin(client, settings):
    token = _admin_session(settings)
    response = client.request("POST", "/setup", session=token, data=_form(), origin=None)
    assert response.status_code == 403
    assert _admin(settings).status == "setup_required"


def test_concurrent_setup_only_one_wins_and_owns_the_password(client, settings):
    ctx = accounts_common.build_context(settings, WebRuntimeConfig.from_settings(settings))
    principal = Principal(1, "admin", "owner", "cookie", "session:test", time.time())
    barrier = threading.Barrier(2)
    results: dict[str, setup_routes.SetupOutcome] = {}

    def run(username: str, password: str) -> None:
        barrier.wait()
        results[username] = setup_routes.perform_setup(
            ctx,
            principal=principal,
            replace_token=None,
            raw_username=username,
            display_name="",
            password=password,
            password_confirm=password,
            acknowledged=True,
        )

    threads = [
        threading.Thread(target=run, args=("first_one", "first-password-123")),
        threading.Thread(target=run, args=("second_one", "second-password-456")),
    ]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=30)

    kinds = sorted(r.kind for r in results.values())
    assert kinds == ["closed", "ok"]
    winner = next(name for name, r in results.items() if r.kind == "ok")
    loser_password = "second-password-456" if winner == "first_one" else "first-password-123"
    winner_password = "first-password-123" if winner == "first_one" else "second-password-456"
    assert _admin(settings).username == winner
    auth = _auth(settings)
    try:
        assert auth_db.verify_password(auth, 1, winner_password)
        assert not auth_db.verify_password(auth, 1, loser_password)
        assert [e["action"] for e in auth_db.list_admin_audit(auth)] == ["account.setup"]
    finally:
        auth.close()


def test_password_store_failure_keeps_setup_and_points_to_me(client, settings, monkeypatch):
    def broken(*_a, **_k):
        raise OSError("disk full")

    monkeypatch.setattr(auth_db, "set_password", broken)
    token = _admin_session(settings)
    response = client.request("POST", "/setup", session=token, data=_form())
    assert response.status_code == 303
    assert response.headers["location"] == "/settings/account?notice=password_not_saved"
    new = _new_session_token(response)
    assert new and new != token
    assert _admin(settings).status == "active"
    # 콘솔 계정 화면이 안내문과 비밀번호 상태를 함께 보여 준다.
    page = client.request("GET", "/settings/account?notice=password_not_saved", session=new)
    assert page.status_code == 200
    assert 'data-account-notice="password_not_saved"' in page.text
    assert "비밀번호를 저장하지 못했습니다" in page.text
    assert "설정 안 됨" in page.text
    # 목록에 없는 notice 값은 아무것도 그리지 않는다.
    unknown = client.request("GET", "/settings/account?notice=<script>", session=new)
    assert unknown.status_code == 200
    assert "data-account-notice" not in unknown.text
    # D4: /me는 /settings/account로 가는 호환용 302다.
    legacy = client.request("GET", "/me", session=new)
    assert legacy.status_code == 302
    assert legacy.headers["location"] == "/settings/account"
