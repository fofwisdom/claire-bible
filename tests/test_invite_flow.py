"""Tests for invitation pipeline (설계 §8.8, §13.3, §15 S12, P3 L3-b).

Contracts covered:
- Code normalization (O->0, I/L->1, case-insensitive, strip whitespace/hyphens) and parameter limits.
- HMAC indexing and AES-256-GCM envelope encryption for invite codes.
- Admin creation, collection pagination, and revocation; non-admin 404 hiding.
- GET/HEAD side-effect freedom, noindex headers, and external link rel="noreferrer noopener".
- Rate limiting on wrong/invalid codes (分당 10회 초과 시 429).
- Atomic redemption under concurrency (exactly 1 succeeds on last use) and failure compensation.
- Single-mode fail-closed 404 for all invitation routes.
- Full E2E flow: invite creation -> visitor signup -> account & default theme creation -> login.
"""

from __future__ import annotations

import concurrent.futures
import os
import sqlite3
import threading
import time
from dataclasses import dataclass
from http.cookies import SimpleCookie
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
from starlette.routing import compile_path
from starlette.testclient import TestClient

from claire import ids
from claire.accounts import invites
from claire.accounts.invites import (
    InvitationInfo,
    InviteInactiveError,
    InviteNotFoundError,
    InviteValidationError,
    generate_invite_code,
    mask_invite_code,
    normalize_invite_code,
    validate_invite_code_syntax,
    validate_invite_params,
)
from claire.api import accounts_routes as acc_mod
from claire.api import security, server
from claire.api.accounts_routes import invites as invites_routes
from claire.store import accounts as accounts_store
from claire.store import auth_db
from claire.store import db as dbm
from claire.store.secrets import SecretDecryptionError, SecretService, get_secret_service, reset_secret_service

PUBLIC_URL = "http://127.0.0.1:8765"
ORIGIN = PUBLIC_URL
ADMIN_PASSWORD = "Correct-Horse-Battery-123!"
MEMBER_PASSWORD = "Member-Password-456!"


@dataclass
class _Settings:
    db_file: Path
    data_dir: Path
    environment: str = "development"
    public_url: str = PUBLIC_URL
    inject_token: str = "owner-" + "o" * 32
    readonly_token: str = "readonly-" + "r" * 32
    cors_allowed_origins: str = ""
    anonymous_readonly: bool = False
    fqdn: str = "notes.kimhome.net"
    multi_theme: bool = True


class _Client:
    def __init__(self, client: TestClient) -> None:
        self.client = client

    def request(
        self,
        method: str,
        path: str,
        *,
        session: str | None = None,
        bearer: str | None = None,
        origin: str | None = ORIGIN,
        **kwargs: Any,
    ):
        headers = dict(kwargs.pop("headers", {}) or {})
        if session is not None:
            headers["Cookie"] = f"claire_session={session}"
        if bearer is not None:
            headers["Authorization"] = f"Bearer {bearer}"
        if origin is not None and method not in {"GET", "HEAD"}:
            headers["Origin"] = origin
        self.client.cookies.clear()
        response = self.client.request(method, path, headers=headers, follow_redirects=False, **kwargs)
        self.client.cookies.clear()
        return response


def _extract_session_token(response) -> str | None:
    for raw in response.headers.get_list("set-cookie"):
        cookie = SimpleCookie()
        cookie.load(raw)
        morsel = cookie.get("claire_session")
        if morsel is not None and morsel.value:
            return morsel.value
    return None


@pytest.fixture(autouse=True)
def _isolate_env_and_secrets(monkeypatch: pytest.MonkeyPatch):
    reset_secret_service()
    for key in list(os.environ):
        if key.startswith("CLAIRE_"):
            monkeypatch.delenv(key, raising=False)
    yield
    reset_secret_service()


@pytest.fixture(autouse=True)
def _mount_invite_routes_for_tests(monkeypatch: pytest.MonkeyPatch):
    orig_build = acc_mod.build_account_routes

    def _patched_build(settings, config):
        ar = orig_build(settings, config)
        return acc_mod.AccountRoutes(context=ar.context, routes=(*ar.routes, *invites_routes.routes(ar.context)))

    monkeypatch.setattr(acc_mod, "build_account_routes", _patched_build)
    monkeypatch.setattr(server, "build_account_routes", _patched_build)

    extra_policy = {
        ("GET", "/invite/{code}"): security._system("public", modes=security.NETWORK_ONLY),
        ("HEAD", "/invite/{code}"): security._system("public", modes=security.NETWORK_ONLY),
        ("POST", "/invite/{code}"): security._system("public", modes=security.NETWORK_ONLY),
        ("GET", "/api/invites"): security._system("admin", modes=security.NETWORK_ONLY),
        ("HEAD", "/api/invites"): security._system("admin", modes=security.NETWORK_ONLY),
        ("POST", "/api/invites"): security._system("admin", modes=security.NETWORK_ONLY),
        ("PATCH", "/api/invites"): security._system("admin", modes=security.NETWORK_ONLY),
    }
    merged_policy = dict(security.ROUTE_POLICY)
    merged_policy.update(extra_policy)
    monkeypatch.setattr(security, "ROUTE_POLICY", merged_policy)

    new_exact = {k: v for k, v in merged_policy.items() if "{" not in k[1]}
    monkeypatch.setattr(security, "_EXACT_ROUTE_POLICY", new_exact)

    new_param = tuple(
        (method, compile_path(path)[0], rule)
        for (method, path), rule in merged_policy.items()
        if "{" in path
    )
    monkeypatch.setattr(security, "_PARAM_ROUTE_POLICY", new_param)


@pytest.fixture
def settings(tmp_path: Path) -> _Settings:
    return _Settings(db_file=tmp_path / "claire.db", data_dir=tmp_path)


def _make_client(settings: _Settings) -> _Client:
    app = server.create_app(settings, SimpleNamespace(provider=SimpleNamespace(name="stub")))
    tc = TestClient(app, base_url=PUBLIC_URL)
    return _Client(tc)


def _setup_admin(settings: _Settings) -> str:
    # First ensure DB is initialized with migrations
    conn = dbm.connect(settings.db_file)
    try:
        dbm.init_db(conn)
        store = accounts_store.AccountStore(conn)
        store.complete_setup(1, username="admin_kim", display_name="Admin Kim")
        auth_conn = auth_db.connect_auth_db(auth_db.auth_db_path(settings.data_dir))
        try:
            auth_db.set_password(auth_conn, 1, ADMIN_PASSWORD, username="admin_kim")
        finally:
            auth_conn.close()
        return accounts_store.create_account_session(conn, account_id=1, scope="owner")
    finally:
        conn.close()


def _set_mode(settings: _Settings, mode: str) -> None:
    conn = dbm.connect_existing(settings.db_file)
    try:
        store = accounts_store.AccountStore(conn)
        store.set_setting("mode", mode, updated_by=1)
    finally:
        conn.close()


# --- 1. 코드 정규화 및 상한 검증 ----------------------------------------------------


def test_invite_code_normalization_and_syntax():
    assert normalize_invite_code(" 7k-q2-m9-xa ") == "7KQ2M9XA"
    assert normalize_invite_code("o1il-O1IL") == "01110111"
    assert normalize_invite_code("a-b-c-d-e-f-g-h") == "ABCDEFGH"

    assert validate_invite_code_syntax("7KQ2M9XA") is True
    assert validate_invite_code_syntax("01110111") is True
    assert validate_invite_code_syntax("7KQ2M9X") is False
    assert validate_invite_code_syntax("7KQ2M9XAA") is False
    assert validate_invite_code_syntax("7KQ2M9XU") is False

    code = generate_invite_code()
    assert len(code) == 8
    assert validate_invite_code_syntax(code) is True


def test_invite_parameter_validation_bounds():
    validate_invite_params(max_uses=1, expires_in_seconds=1800, reserved_username=None)
    validate_invite_params(max_uses=5, expires_in_seconds=30 * 86400, reserved_username=None)
    validate_invite_params(max_uses=1, expires_in_seconds=86400, reserved_username="alice")

    with pytest.raises(InviteValidationError, match="max_uses"):
        validate_invite_params(max_uses=0, expires_in_seconds=3600)
    with pytest.raises(InviteValidationError, match="max_uses"):
        validate_invite_params(max_uses=6, expires_in_seconds=3600)

    with pytest.raises(InviteValidationError, match="expires_in"):
        validate_invite_params(max_uses=1, expires_in_seconds=1799)
    with pytest.raises(InviteValidationError, match="expires_in"):
        validate_invite_params(max_uses=1, expires_in_seconds=30 * 86400 + 1)

    with pytest.raises(InviteValidationError, match="reserved_username"):
        validate_invite_params(max_uses=2, expires_in_seconds=3600, reserved_username="bob")


def test_mask_invite_code():
    assert mask_invite_code("7KQ2M9XA") == "7K****XA"
    assert mask_invite_code("7k-q2-m9-xa") == "7K****XA"


# --- 2. auth.db CRUD, HMAC 색인, 암호화 --------------------------------------------


def test_auth_db_invitation_crud_and_encryption(settings: _Settings):
    auth_conn = auth_db.connect_auth_db(auth_db.auth_db_path(settings.data_dir))
    sec = get_secret_service(key_path=settings.data_dir / "auth" / "master.key")

    try:
        created = auth_db.create_invitation(
            auth_conn,
            created_by=1,
            max_uses=2,
            expires_in=3600,
            note="Test note",
            code="7KQ2M9XA",
            secret_service=sec,
        )
        assert created.code == "7KQ2M9XA"
        assert created.max_uses == 2
        assert created.uses_count == 0
        assert created.is_active is True
        assert created.status == "active"

        raw_row = auth_conn.execute("SELECT code_hmac, code_ciphertext, code_nonce FROM invitations WHERE id=?", (created.id,)).fetchone()
        assert raw_row["code_hmac"] == sec.hmac_index("7KQ2M9XA", purpose="invitation_code")
        assert raw_row["code_ciphertext"] != b"7KQ2M9XA"

        fetched = auth_db.get_invitation_by_code(auth_conn, "7kq2-m9xa", check_active=True, secret_service=sec)
        assert fetched.id == created.id
        assert fetched.code == "7KQ2M9XA"

        with pytest.raises(InviteNotFoundError):
            auth_db.get_invitation_by_code(auth_conn, "N0NEX1ST", secret_service=sec)

        revoked = auth_db.revoke_invitation(auth_conn, created.id, revoked_by=1)
        assert revoked is True
        assert auth_db.revoke_invitation(auth_conn, created.id, revoked_by=1) is False

        with pytest.raises(InviteInactiveError) as exc_info:
            auth_db.get_invitation_by_code(auth_conn, "7KQ2M9XA", check_active=True, secret_service=sec)
        assert exc_info.value.reason == "revoked"

        by_id = auth_db.get_invitation_by_id(auth_conn, created.public_id, secret_service=sec)
        assert by_id is not None
        assert by_id.status == "revoked"
    finally:
        auth_conn.close()


# --- 3. 동시성 원자적 차감 및 보정 트랜잭션 --------------------------------------------


def test_redeem_invitation_atomic_concurrency_last_use(settings: _Settings):
    auth_path = auth_db.auth_db_path(settings.data_dir)
    init_conn = auth_db.connect_auth_db(auth_path)
    try:
        inv = auth_db.create_invitation(init_conn, created_by=1, max_uses=1, expires_in=3600, code="C0NCERR1")
    finally:
        init_conn.close()

    success_accounts: list[int] = []
    inactive_errors: list[str] = []
    barrier = threading.Barrier(10)

    def _worker(worker_id: int):
        conn = sqlite3.connect(str(auth_path), timeout=10.0)
        conn.row_factory = sqlite3.Row
        try:
            barrier.wait()
            acc_id = auth_db.redeem_invitation_atomic(
                conn,
                inv.id,
                account_callback=lambda: 1000 + worker_id,
            )
            success_accounts.append(acc_id)
        except InviteInactiveError as exc:
            inactive_errors.append(exc.reason)
        finally:
            conn.close()

    with concurrent.futures.ThreadPoolExecutor(max_workers=10) as executor:
        futures = [executor.submit(_worker, i) for i in range(10)]
        concurrent.futures.wait(futures)

    assert len(success_accounts) == 1
    assert len(inactive_errors) == 9
    assert all(r == "exhausted" for r in inactive_errors)

    verify_conn = auth_db.connect_auth_db(auth_path)
    try:
        row = verify_conn.execute("SELECT uses_count, max_uses FROM invitations WHERE id=?", (inv.id,)).fetchone()
        assert row["uses_count"] == 1

        redemptions = verify_conn.execute("SELECT status, account_id FROM invitation_redemptions WHERE invitation_id=?", (inv.id,)).fetchall()
        assert len(redemptions) == 1
        assert redemptions[0]["status"] == "completed"
        assert redemptions[0]["account_id"] == success_accounts[0]
    finally:
        verify_conn.close()


def test_redeem_invitation_atomic_compensation_on_failure(settings: _Settings):
    auth_conn = auth_db.connect_auth_db(auth_db.auth_db_path(settings.data_dir))
    try:
        inv = auth_db.create_invitation(auth_conn, created_by=1, max_uses=1, expires_in=3600, code="FA1LC0MP")

        def _bad_callback() -> int:
            raise RuntimeError("disk full simulation")

        with pytest.raises(RuntimeError, match="disk full simulation"):
            auth_db.redeem_invitation_atomic(auth_conn, inv.id, account_callback=_bad_callback)

        row = auth_conn.execute("SELECT uses_count FROM invitations WHERE id=?", (inv.id,)).fetchone()
        assert row["uses_count"] == 0

        redemption = auth_conn.execute("SELECT status FROM invitation_redemptions WHERE invitation_id=?", (inv.id,)).fetchone()
        assert redemption["status"] == "failed"

        acc_id = auth_db.redeem_invitation_atomic(auth_conn, inv.id, account_callback=lambda: 2001)
        assert acc_id == 2001

        row2 = auth_conn.execute("SELECT uses_count FROM invitations WHERE id=?", (inv.id,)).fetchone()
        assert row2["uses_count"] == 1
    finally:
        auth_conn.close()


def test_prune_stale_redemptions(settings: _Settings):
    auth_conn = auth_db.connect_auth_db(auth_db.auth_db_path(settings.data_dir))
    try:
        inv = auth_db.create_invitation(auth_conn, created_by=1, max_uses=2, expires_in=3600, code="PRNETST1")
        now = time.time()
        with auth_conn:
            auth_conn.execute("UPDATE invitations SET uses_count=1 WHERE id=?", (inv.id,))
            auth_conn.execute(
                "INSERT INTO invitation_redemptions (invitation_id, method, status, started_at) VALUES (?, 'local', 'pending', ?)",
                (inv.id, now - 700.0),
            )
        pruned = auth_db.prune_stale_redemptions(auth_conn, now=now, stale_seconds=600.0)
        assert pruned == 1

        row = auth_conn.execute("SELECT uses_count FROM invitations WHERE id=?", (inv.id,)).fetchone()
        assert row["uses_count"] == 0

        red = auth_conn.execute("SELECT status FROM invitation_redemptions WHERE invitation_id=?", (inv.id,)).fetchone()
        assert red["status"] == "failed"
    finally:
        auth_conn.close()


# --- 4. HTTP 라우트: GET/HEAD 무부작용, noindex 헤더, 오용 방지 ---------------------


def test_invite_get_and_head_side_effect_free_and_headers(settings: _Settings):
    _setup_admin(settings)
    _set_mode(settings, "network")
    client = _make_client(settings)

    auth_conn = auth_db.connect_auth_db(auth_db.auth_db_path(settings.data_dir))
    try:
        inv = auth_db.create_invitation(auth_conn, created_by=1, max_uses=2, expires_in=3600, code="TESTHEAD")
    finally:
        auth_conn.close()

    res_get = client.request("GET", f"/invite/{inv.code}")
    assert res_get.status_code == 200
    assert res_get.headers.get("x-robots-tag") == "noindex, nofollow"
    assert res_get.headers.get("cache-control") == "no-store"
    assert "rel=\"noreferrer noopener\"" in res_get.text

    res_head = client.request("HEAD", f"/invite/{inv.code}")
    assert res_head.status_code == 200
    assert res_head.headers.get("x-robots-tag") == "noindex, nofollow"
    assert res_head.text == ""

    # JSON requested
    res_json = client.request("GET", f"/invite/{inv.code}?json=1")
    assert res_json.status_code == 200
    assert res_json.json()["status"] == "active"
    assert res_json.json()["max_uses"] == 2

    # Verify zero side effects on uses_count
    auth_conn = auth_db.connect_auth_db(auth_db.auth_db_path(settings.data_dir))
    try:
        row = auth_conn.execute("SELECT uses_count FROM invitations WHERE id=?", (inv.id,)).fetchone()
        assert row["uses_count"] == 0
    finally:
        auth_conn.close()


def test_invite_rate_limiting_on_wrong_codes(settings: _Settings):
    _setup_admin(settings)
    _set_mode(settings, "network")
    client = _make_client(settings)

    for i in range(11):
        res = client.request("GET", f"/invite/WR0NG{i:03d}")
        assert res.status_code == 404

    res_blocked = client.request("GET", "/invite/WR0NG999")
    assert res_blocked.status_code == 429
    assert "retry-after" in res_blocked.headers


# --- 5. 싱글 모드 차단 404 ---------------------------------------------------------


def test_single_mode_returns_404_for_all_invite_routes(settings: _Settings):
    admin_session = _setup_admin(settings)
    _set_mode(settings, "single")
    client = _make_client(settings)

    assert client.request("GET", "/invite/7KQ2M9XA").status_code == 404
    assert client.request("POST", "/invite/7KQ2M9XA", json={"username": "user1"}).status_code == 404
    assert client.request("GET", "/api/invites", session=admin_session).status_code == 404
    assert client.request("POST", "/api/invites", session=admin_session, json={"max_uses": 1}).status_code == 404
    assert client.request("PATCH", "/api/invites", session=admin_session, json={"public_id": "123"}).status_code == 404


# --- 6. 관리자 전용 API 및 비인가자 404 차단 ---------------------------------------


def test_admin_api_authorization_and_revocation(settings: _Settings):
    admin_session = _setup_admin(settings)
    _set_mode(settings, "network")
    client = _make_client(settings)

    assert client.request("GET", "/api/invites").status_code == 404
    assert client.request("POST", "/api/invites", json={"max_uses": 1}).status_code == 404

    # Admin creates invite
    create_res = client.request(
        "POST",
        "/api/invites",
        session=admin_session,
        json={"max_uses": 2, "expires_in": 7200, "note": "VIP Member"},
    )
    assert create_res.status_code == 201
    created_data = create_res.json()
    code = created_data["code"]
    pub_id = created_data["public_id"]
    assert created_data["max_uses"] == 2
    assert created_data["status"] == "active"

    # Admin lists collection root
    list_root_res = client.request("GET", "/api/invites", session=admin_session)
    assert list_root_res.status_code == 200
    root_json = list_root_res.json()
    assert root_json["type"] == "OrderedCollection"
    assert root_json["totalItems"] >= 1

    # Admin lists page
    list_page_res = client.request("GET", "/api/invites?page=true&limit=10", session=admin_session)
    assert list_page_res.status_code == 200
    page_json = list_page_res.json()
    assert page_json["type"] == "OrderedCollectionPage"
    assert len(page_json["orderedItems"]) >= 1

    # Admin revokes invite
    revoke_res = client.request("PATCH", "/api/invites", session=admin_session, json={"public_id": pub_id})
    assert revoke_res.status_code == 200
    assert revoke_res.json()["revoked"] is True

    # Revoked invite returns 410 on access
    inactive_res = client.request("GET", f"/invite/{code}?json=1")
    assert inactive_res.status_code == 410
    assert inactive_res.json()["error"] == "invitation_revoked"


# --- 7. 최소 1개 E2E 테스트: 생성 -> 가입 -> 테마 및 계정 생성 -> 로그인 ----------------


def test_e2e_invitation_signup_and_login_flow(settings: _Settings):
    admin_session = _setup_admin(settings)
    _set_mode(settings, "network")
    client = _make_client(settings)

    # 1. Admin creates invitation with max_uses=1 and reserved_username="charlie"
    create_res = client.request(
        "POST",
        "/api/invites",
        session=admin_session,
        json={"max_uses": 1, "expires_in": 3600, "reserved_username": "charlie", "note": "Charlie's invite"},
    )
    assert create_res.status_code == 201
    code = create_res.json()["code"]

    # 2. Visitor opens invite page
    page_res = client.request("GET", f"/invite/{code}")
    assert page_res.status_code == 200
    assert "charlie" in page_res.text

    # 3. Visitor submits with mismatched username -> rejected
    mismatch_res = client.request(
        "POST",
        f"/invite/{code}",
        json={"username": "wrong_user", "display_name": "Charlie C", "password": MEMBER_PASSWORD},
    )
    assert mismatch_res.status_code == 400
    assert mismatch_res.json()["error"] == "reserved_username_mismatch"

    # 4. Visitor submits valid registration
    signup_res = client.request(
        "POST",
        f"/invite/{code}",
        json={"username": "charlie", "display_name": "Charlie Brown", "password": MEMBER_PASSWORD},
    )
    assert signup_res.status_code == 201
    assert signup_res.json()["username"] == "charlie"
    member_session = _extract_session_token(signup_res)
    assert member_session is not None

    # 5. Verify account and default theme in claire.db
    conn = dbm.connect_existing(settings.db_file)
    try:
        store = accounts_store.AccountStore(conn)
        acc = store.find_by_username("charlie")
        assert acc is not None
        assert acc.role == "member"
        assert acc.status == "active"
        assert acc.display_name == "Charlie Brown"

        theme = conn.execute("SELECT id, number, label, is_default FROM themes WHERE account_id=?", (acc.id,)).fetchone()
        assert theme is not None
        assert theme["is_default"] == 1
        assert theme["label"] == "General"
    finally:
        conn.close()

    # 6. Verify invite is now exhausted (410)
    exhausted_res = client.request("GET", f"/invite/{code}?json=1")
    assert exhausted_res.status_code == 410
    assert exhausted_res.json()["error"] == "invitation_exhausted"

    # 7. Charlie logs in via /login with password (form submission)
    login_res = client.request(
        "POST",
        "/login",
        data={"username": "charlie", "password": MEMBER_PASSWORD},
    )
    assert login_res.status_code == 303
    assert login_res.headers.get("location") == "/@charlie/"
    new_token = _extract_session_token(login_res)
    assert new_token is not None
