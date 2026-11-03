"""로컬 로그인·로그아웃 (설계 §8.5).

모든 상태는 ``tmp_path``의 data_dir에만 쓴다.
"""

from __future__ import annotations

import time
from dataclasses import dataclass
from http.cookies import SimpleCookie
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
from starlette.testclient import TestClient

from claire import cli
from claire.api import server
from claire.config import Settings
from claire.store import accounts as accounts_store
from claire.store import auth_db
from claire.store import db as dbm

OWNER_TOKEN = "owner-" + "o" * 32
READONLY_TOKEN = "readonly-" + "r" * 32
PUBLIC_URL = "http://127.0.0.1:8765"
ORIGIN = PUBLIC_URL
CROSS_ORIGIN = "http://ui.example.test:5173"
USERNAME = "kim_lee"
PASSWORD = "correct-horse-battery"
MEMBER_PASSWORD = "member-password-123"


@dataclass
class _Settings:
    db_file: Path
    data_dir: Path
    environment: str = "development"
    public_url: str = PUBLIC_URL
    inject_token: str = OWNER_TOKEN
    readonly_token: str = READONLY_TOKEN
    cors_allowed_origins: str = CROSS_ORIGIN
    anonymous_readonly: bool = False


class _Client:
    def __init__(self, client: TestClient) -> None:
        self.client = client

    def request(self, method: str, path: str, *, session: str | None = None,
                origin: str | None = ORIGIN, **kwargs: Any):
        headers = dict(kwargs.pop("headers", {}) or {})
        if session is not None:
            headers["Cookie"] = f"claire_session={session}"
        if origin is not None:
            headers["Origin"] = origin
        self.client.cookies.clear()
        response = self.client.request(method, path, headers=headers, follow_redirects=False, **kwargs)
        self.client.cookies.clear()
        return response

    def login(self, username: str, password: str, **kwargs: Any):
        return self.request("POST", "/login", data={"username": username, "password": password}, **kwargs)


def _set_cookie(response) -> tuple[str, str] | None:
    """응답의 ``claire_session`` Set-Cookie. 둘 이상이면 실패한다(브라우저는 마지막 것을 가진다)."""
    found: list[tuple[str, str]] = []
    for raw in response.headers.get_list("set-cookie"):
        cookie = SimpleCookie()
        cookie.load(raw)
        morsel = cookie.get("claire_session")
        if morsel is not None:
            found.append((morsel.value, raw))
    assert len(found) <= 1, f"claire_session Set-Cookie must appear at most once: {found}"
    return found[0] if found else None


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


def _activate_admin(settings) -> None:
    conn = _db(settings)
    auth = _auth(settings)
    try:
        accounts_store.AccountStore(conn).complete_setup(1, username=USERNAME, display_name="")
        auth_db.set_password(auth, 1, PASSWORD, username=USERNAME)
    finally:
        auth.close()
        conn.close()


def _add_member(settings, username: str, status: str) -> int:
    conn = _db(settings)
    auth = _auth(settings)
    try:
        cur = conn.execute(
            "INSERT INTO accounts (public_id, username, role, status, created_at, updated_at) "
            "VALUES (?, ?, 'member', ?, 0, 0)",
            (int(time.time() * 1000) << 8, username, status),
        )
        conn.commit()
        account_id = int(cur.lastrowid)
        auth_db.set_password(auth, account_id, MEMBER_PASSWORD, username=username)
        return account_id
    finally:
        auth.close()
        conn.close()


def _session_exists(settings, token: str) -> bool:
    conn = _db(settings)
    try:
        return conn.execute("SELECT 1 FROM auth_sessions WHERE session_token=?", (token,)).fetchone() is not None
    finally:
        conn.close()


def test_login_page_is_public_and_inline_script_free(client):
    response = client.request("GET", "/login")
    assert response.status_code == 200
    assert response.headers["cache-control"] == "no-store"
    assert "<script" not in response.text
    assert 'action="/login"' in response.text


def test_login_success_sets_new_session(client, settings):
    _activate_admin(settings)
    response = client.login(USERNAME, PASSWORD)
    assert response.status_code == 303
    assert response.headers["location"] == "/"
    token, raw = _set_cookie(response)
    assert token and "HttpOnly" in raw and "SameSite=lax" in raw
    me = client.request("GET", "/api/me", session=token)
    assert me.status_code == 200
    assert me.json()["username"] == USERNAME
    # 입력은 소문자로 정규화한다.
    assert client.login(USERNAME.upper(), PASSWORD).status_code == 303


def test_login_replaces_existing_cookie_session(client, settings):
    """세션 고정 방지: 브라우저가 이미 가진 세션 토큰은 로그인 뒤 쓸 수 없다."""
    _activate_admin(settings)
    conn = _db(settings)
    try:
        planted = accounts_store.create_account_session(conn, account_id=1)
    finally:
        conn.close()
    response = client.login(USERNAME, PASSWORD, session=planted)
    assert response.status_code == 303
    token, _raw = _set_cookie(response)
    assert token != planted
    assert not _session_exists(settings, planted)
    assert client.request("GET", "/api/me", session=planted).status_code == 404
    assert client.request("GET", "/api/me", session=token).status_code == 200


def test_login_failures_are_indistinguishable(client, settings):
    _activate_admin(settings)
    _add_member(settings, "suspended_one", "suspended")
    nopass_id = _add_member(settings, "nopass_one", "active")
    auth = _auth(settings)
    try:
        auth.execute("DELETE FROM account_passwords WHERE account_id=?", (nopass_id,))
        auth.commit()
    finally:
        auth.close()

    attempts = [
        (USERNAME, "wrong-password-1234"),          # 틀린 비밀번호
        ("nobody_here", PASSWORD),                  # 없는 계정
        ("suspended_one", MEMBER_PASSWORD),         # 정지 (비밀번호는 맞음)
        ("nopass_one", MEMBER_PASSWORD),            # 비밀번호 행 없음
        ("Ｋim", PASSWORD),                         # 정규화 거부(비ASCII)
    ]
    responses = [client.login(name, pw) for name, pw in attempts]
    statuses = {r.status_code for r in responses}
    assert statuses == {401}
    bodies = {r.text.replace(f'value="{name}"', 'value=""') for r, (name, _pw) in zip(responses, attempts)}
    assert len(bodies) == 1
    assert all(_set_cookie(r) is None for r in responses)


def test_setup_required_admin_cannot_log_in(client, settings):
    # 설정 전 관리자는 USERNAME도 비밀번호도 없다. 어떤 이름으로도 로그인할 수 없다.
    response = client.login("admin", PASSWORD)
    assert response.status_code == 401
    conn = _db(settings)
    try:
        conn.execute("UPDATE accounts SET username='kim_lee' WHERE id=1")
        conn.commit()
    finally:
        conn.close()
    assert client.login(USERNAME, PASSWORD).status_code == 401


def test_login_throttle_returns_429_without_counting(client, settings):
    _activate_admin(settings)
    for _ in range(auth_db.THROTTLE_FREE_FAILURES + 1):
        assert client.login(USERNAME, "wrong-password-1234").status_code == 401
    auth = _auth(settings)
    try:
        before = auth.execute("SELECT key, failures FROM login_throttle ORDER BY key").fetchall()
    finally:
        auth.close()
    throttled = client.login(USERNAME, PASSWORD)
    assert throttled.status_code == 429
    assert int(throttled.headers["retry-after"]) >= 1
    assert _set_cookie(throttled) is None
    auth = _auth(settings)
    try:
        after = auth.execute("SELECT key, failures FROM login_throttle ORDER BY key").fetchall()
    finally:
        auth.close()
    assert [tuple(r) for r in after] == [tuple(r) for r in before]


@pytest.mark.parametrize(
    "headers",
    [
        {},                                             # Origin·Sec-Fetch-Site 모두 없음
        {"Sec-Fetch-Site": "cross-site"},
        {"Sec-Fetch-Site": "same-site"},
    ],
)
def test_login_rejects_requests_without_same_origin_proof(client, settings, headers):
    _activate_admin(settings)
    response = client.login(USERNAME, PASSWORD, origin=None, headers=headers)
    assert response.status_code == 403
    assert _set_cookie(response) is None


def test_login_rejects_cross_origin_and_accepts_fetch_metadata(client, settings):
    _activate_admin(settings)
    # CORS 허용 목록에 있어도 로그인은 같은 origin만 받는다(로그인 CSRF).
    assert client.login(USERNAME, PASSWORD, origin=CROSS_ORIGIN).status_code == 403
    assert client.login(USERNAME, PASSWORD, origin="http://evil.example").status_code == 403
    ok = client.login(USERNAME, PASSWORD, origin=None, headers={"Sec-Fetch-Site": "same-origin"})
    assert ok.status_code == 303


def test_logout_deletes_session_and_cookie(client, settings):
    _activate_admin(settings)
    token, _raw = _set_cookie(client.login(USERNAME, PASSWORD))
    # 교차 origin 로그아웃은 거부하고 세션을 남긴다.
    assert client.request("POST", "/logout", session=token, origin=CROSS_ORIGIN).status_code == 403
    assert _session_exists(settings, token)
    # GET 로그아웃은 없다.
    assert client.request("GET", "/logout", session=token).status_code == 404

    response = client.request("POST", "/logout", session=token)
    assert response.status_code == 303
    assert response.headers["location"] == "/login"
    cleared = _set_cookie(response)
    assert cleared is not None and cleared[0] == ""
    assert not _session_exists(settings, token)
    assert client.request("GET", "/api/me", session=token).status_code == 404


def test_prune_login_throttle_keeps_active_backoff(tmp_path):
    conn = auth_db.connect_auth_db(auth_db.auth_db_path(tmp_path))
    try:
        now = 10_000_000.0
        window = auth_db.THROTTLE_WINDOW_SECONDS
        conn.executemany(
            "INSERT INTO login_throttle (key, failures, window_start, retry_after) VALUES (?, ?, ?, ?)",
            [
                ("old_no_retry", 2, now - window - 1, None),             # 지움
                ("old_retry_long_ago", 9, now - 3 * window, now - window),  # 지움
                ("old_retry_recent", 9, now - 3 * window, now - 10),    # 대기는 끝났지만 창이 덜 지남: 남김
                ("fresh", 1, now - 5, None),                             # 남김
            ],
        )
        conn.commit()
        assert auth_db.prune_login_throttle(conn, now=now) == 2
        keys = {r[0] for r in conn.execute("SELECT key FROM login_throttle")}
        assert keys == {"old_retry_recent", "fresh"}
    finally:
        conn.close()


def test_migrate_initializes_auth_db(monkeypatch, tmp_path, capsys):
    monkeypatch.setenv("CLAIRE_DB_PATH", str(tmp_path / "m.db"))
    monkeypatch.setenv("CLAIRE_VAULT_PATH", str(tmp_path / "vault"))
    monkeypatch.setenv("CLAIRE_PROVIDER", "mock")
    s = Settings()
    monkeypatch.setattr(cli, "get_settings", lambda: s)

    assert cli.main(["migrate"]) == 0
    assert f"auth_db schema_version={auth_db.AUTH_DB_SCHEMA_VERSION}" in capsys.readouterr().out
    path = auth_db.auth_db_path(tmp_path)
    assert path.is_file()
    conn = auth_db.connect_auth_db(path)
    try:
        assert conn.execute("SELECT value FROM meta WHERE key='schema_version'").fetchone()[0] == str(auth_db.AUTH_DB_SCHEMA_VERSION)
    finally:
        conn.close()


# --- 양식 화면의 Referrer-Policy (H1) ------------------------------------------------------


def test_login_page_sends_same_origin_referrer_policy(client, settings):
    """``no-referrer``이면 Chromium이 양식 POST에 ``Origin: null``을 실어 403이 된다. 계정 양식 화면만 ``same-origin``이다."""
    _activate_admin(settings)
    assert client.request("GET", "/login").headers["referrer-policy"] == "same-origin"
    failed = client.login(USERNAME, "wrong-password-1234")
    assert failed.status_code == 401
    assert failed.headers["referrer-policy"] == "same-origin"
    # 다른 화면의 전역 정책은 그대로다.
    assert client.request("GET", "/health").headers["referrer-policy"] == "no-referrer"


def test_login_with_origin_null_is_still_rejected(client, settings):
    _activate_admin(settings)
    response = client.login(USERNAME, PASSWORD, origin="null")
    assert response.status_code == 403
    assert _set_cookie(response) is None


# --- 실패 제한 TOCTOU (M1) -----------------------------------------------------------------


def test_concurrent_login_attempts_reach_verification_at_most_free_plus_one(settings, monkeypatch):
    """확인과 기록이 한 트랜잭션이라 동시 요청이 무료 범위를 넘겨 비밀번호 검증에 닿지 못한다."""
    import threading

    from claire.api import security
    from claire.api.accounts_routes import common as accounts_common
    from claire.api.accounts_routes import login as login_routes

    server.create_app(settings, SimpleNamespace(provider=SimpleNamespace(name="stub")))
    _activate_admin(settings)
    ctx = accounts_common.build_context(settings, security.WebRuntimeConfig.from_settings(settings))

    verified = 0
    lock = threading.Lock()
    real_verify = auth_db.verify_password

    def counting_verify(conn, account_id, password):
        nonlocal verified
        with lock:
            verified += 1
        time.sleep(0.05)  # 검증 시간 동안 다른 요청이 끼어들 틈을 넓힌다
        return real_verify(conn, None, password)

    monkeypatch.setattr(auth_db, "verify_password", counting_verify)
    attempts = 40
    barrier = threading.Barrier(attempts)
    kinds: list[str] = []

    def worker(i: int) -> None:
        barrier.wait()
        outcome = login_routes.perform_login(
            ctx, raw_username=USERNAME, password=f"guess-{i:08d}-x", ip="203.0.113.9", replace_token=None
        )
        with lock:
            kinds.append(outcome.kind)

    threads = [threading.Thread(target=worker, args=(i,)) for i in range(attempts)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert len(kinds) == attempts
    assert verified <= auth_db.THROTTLE_FREE_FAILURES + 1
    assert kinds.count("failed") == verified
    assert kinds.count("throttled") == attempts - verified


def test_successful_login_does_not_leave_a_counted_attempt_on_the_ip_key(client, settings):
    _activate_admin(settings)
    for _ in range(auth_db.THROTTLE_FREE_FAILURES + 3):
        assert client.login(USERNAME, PASSWORD).status_code == 303
    auth = _auth(settings)
    try:
        assert auth.execute("SELECT COUNT(*) FROM login_throttle WHERE failures > 0").fetchone()[0] == 0
    finally:
        auth.close()


# --- 신뢰 프록시 뒤의 클라이언트 IP (M2) ------------------------------------------------------


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("trusted", "peer", "expected_ip"),
    [
        ("", "10.0.0.5", "10.0.0.5"),                     # 목록이 비면 지금과 같다
        ("10.0.0.0/8", "10.0.0.5", "203.0.113.9"),        # 신뢰 피어: XFF의 오른쪽부터
        ("10.0.0.0/8", "198.51.100.4", "198.51.100.4"),  # 비신뢰 피어의 XFF는 무시
    ],
)
async def test_login_throttle_key_uses_trusted_forwarded_ip(tmp_path, trusted, peer, expected_ip):
    import httpx

    settings = _Settings(db_file=tmp_path / "claire.db", data_dir=tmp_path)
    settings.trusted_proxies = trusted  # type: ignore[attr-defined]
    app = server.create_app(settings, SimpleNamespace(provider=SimpleNamespace(name="stub")))
    _activate_admin(settings)
    transport = httpx.ASGITransport(app=app, client=(peer, 4321))
    async with httpx.AsyncClient(transport=transport, base_url=PUBLIC_URL) as ac:
        response = await ac.post(
            "/login",
            data={"username": USERNAME, "password": "wrong-password-1234"},
            headers={"Origin": ORIGIN, "X-Forwarded-For": "1.1.1.1, 203.0.113.9"},
        )
    assert response.status_code == 401
    auth = _auth(settings)
    try:
        keys = {r[0] for r in auth.execute("SELECT key FROM login_throttle")}
    finally:
        auth.close()
    assert keys == set(auth_db._throttle_keys(USERNAME, expected_ip))
