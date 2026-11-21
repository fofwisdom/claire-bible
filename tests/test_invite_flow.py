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
from starlette.testclient import TestClient

from claire.core import ids
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
from claire.api import security, server
from claire.store import accounts as accounts_store
from claire.store import auth_db
from claire.store import db as dbm
from claire.store.secrets import SecretDecryptionError, SecretService, get_secret_service, reset_secret_service
from claire.store.token_store import get_token_store, reset_token_stores

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
    reset_token_stores()
    for key in list(os.environ):
        if key.startswith("CLAIRE_"):
            monkeypatch.delenv(key, raising=False)
    yield
    reset_secret_service()
    reset_token_stores()


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


def _setup_member(settings: _Settings, username: str = "member1") -> tuple[int, str]:
    conn = dbm.connect_existing(settings.db_file)
    try:
        salt = dbm.read_id_salt(conn)
        now_ts = time.time()
        pub_id = ids.insert_with_timestamp_id(
            conn,
            table="accounts",
            column="public_id",
            salt=salt,
            insert=lambda candidate: conn.execute(
                "INSERT INTO accounts (public_id, username, role, status, display_name, created_at, activated_at, updated_at) VALUES (?, ?, 'member', 'active', ?, ?, ?, ?)",
                (candidate, username, username, now_ts, now_ts, now_ts),
            ),
        )
        row = conn.execute("SELECT id FROM accounts WHERE public_id=?", (pub_id,)).fetchone()
        member_id = int(row["id"])
        conn.commit()
        token = accounts_store.create_account_session(conn, account_id=member_id, scope="owner", now=now_ts)
        return member_id, token
    finally:
        conn.close()


def _set_member_invites_enabled(settings: _Settings, enabled: bool) -> None:
    conn = dbm.connect_existing(settings.db_file)
    try:
        accounts_store.AccountStore(conn).set_member_invites_enabled(enabled, updated_by=1)
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
    assert res_get.headers.get("referrer-policy") == "same-origin"
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

    # Reject non-dict JSON and bool numeric fields
    assert client.request("POST", "/api/invites", session=admin_session, json=["bad"]).status_code == 400
    assert client.request("POST", "/api/invites", session=admin_session, json={"max_uses": True}).status_code == 400
    assert client.request("POST", "/api/invites", session=admin_session, json={"expires_in": True}).status_code == 400

    # Admin creates invite with note=None (coerced to "") and max_uses=3
    create_res = client.request(
        "POST",
        "/api/invites",
        session=admin_session,
        json={"max_uses": 3, "expires_in": 7200, "note": None},
    )
    assert create_res.status_code == 201
    assert create_res.headers.get("cache-control") == "no-store"
    created_data = create_res.json()
    code = created_data["code"]
    pub_id = created_data["public_id"]
    assert created_data["url"] == f"{PUBLIC_URL}/invite/{code}"
    assert created_data["max_uses"] == 3
    assert created_data["uses_count"] == 0
    assert created_data["note"] == ""
    assert created_data["status"] == "active"
    assert created_data["created_by"] == 1
    assert created_data["is_mine"] is True
    assert created_data["can_revoke"] is True

    # Simulate partial redemption (uses_count=1 < max_uses=3)
    auth_conn = auth_db.connect_auth_db(auth_db.auth_db_path(settings.data_dir))
    try:
        auth_db.redeem_invitation_atomic(auth_conn, int(pub_id), account_callback=lambda: 9001)
    finally:
        auth_conn.close()

    # Admin lists collection root
    list_root_res = client.request("GET", "/api/invites", session=admin_session)
    assert list_root_res.status_code == 200
    assert list_root_res.headers.get("cache-control") == "no-store"
    root_json = list_root_res.json()
    assert root_json["type"] == "OrderedCollection"
    assert root_json["totalItems"] >= 1
    assert root_json["member_invites_enabled"] is False
    assert root_json["can_issue"] is True

    # Admin lists page
    list_page_res = client.request("GET", "/api/invites?page=true&limit=10", session=admin_session)
    assert list_page_res.status_code == 200
    assert list_page_res.headers.get("cache-control") == "no-store"
    page_json = list_page_res.json()
    assert page_json["type"] == "OrderedCollectionPage"
    assert page_json["member_invites_enabled"] is False
    assert page_json["can_issue"] is True
    assert len(page_json["orderedItems"]) >= 1
    listed = page_json["orderedItems"][0]
    assert listed["url"] == f"{PUBLIC_URL}/invite/{code}"
    assert listed["status"] == "active"
    assert listed["uses_count"] == 1
    assert listed["max_uses"] == 3
    assert listed["is_mine"] is True
    assert listed["can_revoke"] is True
    assert listed["issuer_username"] == "admin_kim"

    # Reject invalid action on PATCH (including explicit null) and out-of-range id values
    bad_patch = client.request("PATCH", "/api/invites", session=admin_session, json={"public_id": pub_id, "action": "delete"})
    assert bad_patch.status_code == 400
    assert bad_patch.json()["error"] == "invalid_action"

    null_action_patch = client.request("PATCH", "/api/invites", session=admin_session, json={"public_id": pub_id, "action": None})
    assert null_action_patch.status_code == 400
    assert null_action_patch.json()["error"] == "invalid_action"

    for bad_id in (2**63, "9" * 50, -(2**63) - 1, 0):
        out_of_range_patch = client.request("PATCH", "/api/invites", session=admin_session, json={"id": bad_id})
        assert out_of_range_patch.status_code == 400
        assert out_of_range_patch.json()["error"] == "invalid_id"

    # Admin revokes active invite even when uses_count < max_uses
    revoke_res = client.request("PATCH", "/api/invites", session=admin_session, json={"public_id": pub_id, "action": "revoke"})
    assert revoke_res.status_code == 200
    assert revoke_res.headers.get("cache-control") == "no-store"
    revoke_json = revoke_res.json()
    assert revoke_json["revoked"] is True
    assert revoke_json["public_id"] == pub_id
    assert revoke_json["status"] == "revoked"

    # Revoked invite returns 410 on access
    inactive_res = client.request("GET", f"/invite/{code}?json=1")
    assert inactive_res.status_code == 410
    assert inactive_res.json()["error"] == "invitation_revoked"


def test_store_member_invites_setting_and_create_retry(settings: _Settings):
    _setup_admin(settings)
    conn = dbm.connect_existing(settings.db_file)
    try:
        store = accounts_store.AccountStore(conn)
        assert store.member_invites_enabled() is False
        with pytest.raises(ValueError):
            store.set_member_invites_enabled("true", updated_by=1)  # type: ignore[arg-type]
        store.set_member_invites_enabled(True, updated_by=1)
        assert store.member_invites_enabled() is True
        store.set_member_invites_enabled(False, updated_by=1)
        assert store.member_invites_enabled() is False
        conn.execute("UPDATE server_settings SET value='maybe' WHERE key=?", (accounts_store.MEMBER_INVITES_SETTING_KEY,))
        conn.commit()
        with pytest.raises(RuntimeError):
            store.member_invites_enabled()

        usernames = store.usernames_by_ids([1, 999999])
        assert usernames == {1: "admin_kim"}
    finally:
        conn.close()

    # Same-millisecond public_id collision retry encrypts with candidate public_id AAD
    auth_conn = auth_db.connect_auth_db(auth_db.auth_db_path(settings.data_dir))
    sec = get_secret_service(key_path=settings.data_dir / "auth" / "master.key")
    try:
        fixed_now = 1_760_000_000.123
        inv1 = auth_db.create_invitation(auth_conn, created_by=1, code="RETRY001", now=fixed_now, secret_service=sec)
        inv2 = auth_db.create_invitation(auth_conn, created_by=1, code="RETRY002", now=fixed_now, secret_service=sec)
        assert inv1.public_id != inv2.public_id
        assert auth_db.get_invitation_by_id(auth_conn, inv2.public_id, secret_service=sec).code == "RETRY002"
    finally:
        auth_conn.close()


def test_member_invite_access_policy_and_issuance_toggle(settings: _Settings):
    admin_session = _setup_admin(settings)
    _set_mode(settings, "network")
    member_id, member_session = _setup_member(settings, "member1")
    conn = dbm.connect_existing(settings.db_file)
    try:
        write_session = accounts_store.create_account_session(conn, account_id=member_id, scope="write")
        readonly_session = accounts_store.create_account_session(conn, account_id=member_id, scope="readonly")
    finally:
        conn.close()

    client = _make_client(settings)

    # Non-owner tokens remain blocked with 404
    assert client.request("GET", "/api/invites", session=write_session).status_code == 404
    assert client.request("GET", "/api/invites", session=readonly_session).status_code == 404

    # Member can read empty list while issuance is disabled
    get_disabled = client.request("GET", "/api/invites?page=true", session=member_session)
    assert get_disabled.status_code == 200
    assert get_disabled.json()["member_invites_enabled"] is False
    assert get_disabled.json()["can_issue"] is False
    assert get_disabled.json()["orderedItems"] == []

    # Member POST blocked with 403 invite_issuance_disabled
    blocked_post = client.request("POST", "/api/invites", session=member_session, json={"max_uses": 1})
    assert blocked_post.status_code == 403
    assert blocked_post.json()["error"] == "invite_issuance_disabled"

    # Admin enables member invite issuance via /api/admin/server/invites
    toggle_res = client.request("POST", "/api/admin/server/invites", session=admin_session, json={"member_invites_enabled": True})
    assert toggle_res.status_code == 200
    assert toggle_res.json() == {"member_invites_enabled": True}

    # Member can now issue invite
    allowed_post = client.request("POST", "/api/invites", session=member_session, json={"max_uses": 2, "note": "Friend"})
    assert allowed_post.status_code == 201
    created = allowed_post.json()
    assert created["created_by"] == member_id
    assert created["is_mine"] is True
    assert created["can_revoke"] is True
    assert created["url"] == f"{PUBLIC_URL}/invite/{created['code']}"


def test_member_scoped_listing_and_revocation_rules(settings: _Settings):
    admin_session = _setup_admin(settings)
    _set_mode(settings, "network")
    _set_member_invites_enabled(settings, True)
    m1_id, m1_session = _setup_member(settings, "member1")
    m2_id, m2_session = _setup_member(settings, "member2")
    client = _make_client(settings)

    admin_inv = client.request("POST", "/api/invites", session=admin_session, json={"max_uses": 1, "note": "admin-inv"}).json()
    m1_inv = client.request("POST", "/api/invites", session=m1_session, json={"max_uses": 3, "note": "m1-inv"}).json()
    m2_inv = client.request("POST", "/api/invites", session=m2_session, json={"max_uses": 2, "note": "m2-inv"}).json()

    # Member 1 sees only Member 1's invite (both root totalItems and page orderedItems)
    m1_root = client.request("GET", "/api/invites", session=m1_session)
    assert m1_root.status_code == 200
    assert m1_root.json()["totalItems"] == 1
    m1_page = client.request("GET", "/api/invites?page=true", session=m1_session)
    assert m1_page.status_code == 200
    m1_items = m1_page.json()["orderedItems"]
    assert [it["code"] for it in m1_items] == [m1_inv["code"]]
    assert m1_items[0]["issuer_username"] == "member1"
    assert m1_items[0]["is_mine"] is True
    assert m1_items[0]["can_revoke"] is True

    # Member 1 cannot revoke Member 2's or Admin's invite by code, public_id, or id
    auth_conn = auth_db.connect_auth_db(auth_db.auth_db_path(settings.data_dir))
    try:
        m2_row = auth_db.get_invitation_by_id(auth_conn, int(m2_inv["public_id"]))
        assert m2_row is not None
        m2_internal_id = m2_row.id
        # Partially redeem m1_inv so uses_count=1 < max_uses=3
        auth_db.redeem_invitation_atomic(auth_conn, int(m1_inv["public_id"]), account_callback=lambda: 9101)
    finally:
        auth_conn.close()

    for payload in ({"code": m2_inv["code"]}, {"public_id": m2_inv["public_id"]}, {"id": m2_internal_id}, {"code": admin_inv["code"]}):
        denied = client.request("PATCH", "/api/invites", session=m1_session, json=payload)
        assert denied.status_code == 404
        assert denied.json()["error"] == "invitation_not_found"

    # Admin disables member issuance; Member 1 can still revoke own active partially-used invite
    _set_member_invites_enabled(settings, False)
    m1_revoke = client.request("PATCH", "/api/invites", session=m1_session, json={"public_id": m1_inv["public_id"]})
    assert m1_revoke.status_code == 200
    assert m1_revoke.json()["status"] == "revoked"
    assert client.request("GET", f"/invite/{m1_inv['code']}?json=1").status_code == 410

    # Admin sees all 3 invites and can revoke Member 2's invite
    admin_page = client.request("GET", "/api/invites?page=true", session=admin_session)
    assert admin_page.status_code == 200
    by_code = {it["code"]: it for it in admin_page.json()["orderedItems"]}
    assert set(by_code) >= {admin_inv["code"], m1_inv["code"], m2_inv["code"]}
    assert by_code[m2_inv["code"]]["issuer_username"] == "member2"
    assert by_code[m2_inv["code"]]["is_mine"] is False
    assert by_code[m2_inv["code"]]["can_revoke"] is True
    assert by_code[m2_inv["code"]]["created_by"] == m2_id

    admin_revoke_m2 = client.request("PATCH", "/api/invites", session=admin_session, json={"code": m2_inv["code"]})
    assert admin_revoke_m2.status_code == 200
    assert admin_revoke_m2.json()["revoked"] is True


def test_invite_post_browser_form_errors_render_html(settings: _Settings):
    admin_session = _setup_admin(settings)
    _set_mode(settings, "network")
    client = _make_client(settings)

    inv = client.request("POST", "/api/invites", session=admin_session, json={"max_uses": 1, "reserved_username": "reserved_alice"}).json()
    code = inv["code"]

    # Reserved username mismatch via HTML form submission renders 400 HTML
    res_mismatch = client.request(
        "POST",
        f"/invite/{code}",
        data={"username": "wrong_alice", "display_name": "Alice", "password": MEMBER_PASSWORD},
        headers={"Accept": "text/html"},
    )
    assert res_mismatch.status_code == 400
    assert "text/html" in res_mismatch.headers.get("content-type", "")
    assert res_mismatch.headers.get("referrer-policy") == "same-origin"
    assert '<ul class="errors"' in res_mismatch.text

    # Short password via HTML form submission renders 400 HTML and preserves display_name
    res_short_pw = client.request(
        "POST",
        f"/invite/{code}",
        data={"username": "reserved_alice", "display_name": "Alice Cooper", "password": "short"},
        headers={"Accept": "text/html"},
    )
    assert res_short_pw.status_code == 400
    assert "text/html" in res_short_pw.headers.get("content-type", "")
    assert '<ul class="errors"' in res_short_pw.text
    assert 'value="Alice Cooper"' in res_short_pw.text

    # Revoke and submit HTML form -> renders 410 inactive HTML (never raw JSON)
    assert client.request("PATCH", "/api/invites", session=admin_session, json={"code": code}).status_code == 200
    res_revoked = client.request(
        "POST",
        f"/invite/{code}",
        data={"username": "reserved_alice", "display_name": "Alice Cooper", "password": MEMBER_PASSWORD},
        headers={"Accept": "text/html"},
    )
    assert res_revoked.status_code == 410
    assert "text/html" in res_revoked.headers.get("content-type", "")
    assert res_revoked.headers.get("referrer-policy") == "same-origin"
    assert "You cannot use this invitation link" in res_revoked.text


def test_invite_post_atomic_revocation_race_returns_410_not_500(settings: _Settings, monkeypatch: pytest.MonkeyPatch):
    admin_session = _setup_admin(settings)
    _set_mode(settings, "network")
    client = _make_client(settings)

    inv = client.request("POST", "/api/invites", session=admin_session, json={"max_uses": 2}).json()
    code = inv["code"]

    def _raise_revoked(*args, **kwargs):
        raise InviteInactiveError("revoked")

    monkeypatch.setattr(auth_db, "redeem_invitation_atomic", _raise_revoked)

    json_race = client.request("POST", f"/invite/{code}", json={"username": "racer1", "display_name": "Racer One", "password": MEMBER_PASSWORD})
    assert json_race.status_code == 410
    assert json_race.json()["error"] == "invitation_revoked"

    html_race = client.request(
        "POST",
        f"/invite/{code}",
        data={"username": "racer2", "display_name": "Racer Two", "password": MEMBER_PASSWORD},
        headers={"Accept": "text/html"},
    )
    assert html_race.status_code == 410
    assert "text/html" in html_race.headers.get("content-type", "")
    assert "You cannot use this invitation link" in html_race.text

    def _raise_not_found(*args, **kwargs):
        raise InviteNotFoundError("missing")

    monkeypatch.setattr(auth_db, "redeem_invitation_atomic", _raise_not_found)
    missing_race = client.request("POST", f"/invite/{code}", json={"username": "racer3", "display_name": "Racer Three", "password": MEMBER_PASSWORD})
    assert missing_race.status_code == 404


def test_bearer_owner_api_token_invite_post_and_patch_without_origin(settings: _Settings):
    _setup_admin(settings)
    _set_mode(settings, "network")
    member_id, _ = _setup_member(settings, "bearer_member")
    _set_member_invites_enabled(settings, True)

    tok_store = get_token_store(data_dir=settings.data_dir)
    admin_bearer = tok_store.create_token(name="admin-owner-cli", scope="owner", account_id=1)["token"]
    member_bearer = tok_store.create_token(name="member-owner-cli", scope="owner", account_id=member_id)["token"]
    client = _make_client(settings)

    admin_create = client.request("POST", "/api/invites", bearer=admin_bearer, origin=None, json={"max_uses": 2, "note": "cli-admin"})
    assert admin_create.status_code == 201
    admin_pub_id = admin_create.json()["public_id"]

    member_create = client.request("POST", "/api/invites", bearer=member_bearer, origin=None, json={"max_uses": 1, "note": "cli-member"})
    assert member_create.status_code == 201
    member_pub_id = member_create.json()["public_id"]

    member_revoke = client.request("PATCH", "/api/invites", bearer=member_bearer, origin=None, json={"public_id": member_pub_id, "action": "revoke"})
    assert member_revoke.status_code == 200
    assert member_revoke.json()["revoked"] is True

    admin_revoke = client.request("PATCH", "/api/invites", bearer=admin_bearer, origin=None, json={"public_id": admin_pub_id, "action": "revoke"})
    assert admin_revoke.status_code == 200
    assert admin_revoke.json()["revoked"] is True


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

        theme = conn.execute("SELECT id, number, label, is_personal FROM knowledge_bases WHERE account_id=?", (acc.id,)).fetchone()
        assert theme is not None
        assert theme["is_personal"] == 1
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


def test_concurrent_multi_use_invite_signup_no_500(settings: _Settings):
    admin_session = _setup_admin(settings)
    _set_mode(settings, "network")
    app = server.create_app(settings, SimpleNamespace(provider=SimpleNamespace(name="stub")))
    admin_client = _Client(TestClient(app, base_url=PUBLIC_URL))

    rounds = 8
    workers = 5
    created_invites: list[tuple[str, str]] = []

    for round_idx in range(rounds):
        inv_res = admin_client.request("POST", "/api/invites", session=admin_session, json={"max_uses": workers, "note": f"concurrent-round-{round_idx}"})
        assert inv_res.status_code == 201
        inv_data = inv_res.json()
        code = inv_data["code"]
        pub_id = inv_data["public_id"]
        created_invites.append((code, pub_id))

        barrier = threading.Barrier(workers)

        def _signup(worker_idx: int, invite_code: str = code, r_idx: int = round_idx) -> tuple[int, dict[str, Any]]:
            tc = TestClient(app, base_url=PUBLIC_URL)
            uname = f"mu_{r_idx}_{worker_idx}"
            barrier.wait(timeout=10.0)
            res = tc.post(f"/invite/{invite_code}", headers={"Origin": ORIGIN}, json={"username": uname, "display_name": f"Multi User {r_idx}-{worker_idx}", "password": MEMBER_PASSWORD})
            return res.status_code, res.json()

        with concurrent.futures.ThreadPoolExecutor(max_workers=workers) as executor:
            futures = [executor.submit(_signup, i) for i in range(workers)]
            results = [f.result(timeout=20.0) for f in futures]

        statuses = [status for status, _ in results]
        assert statuses == [201] * workers, f"round={round_idx} results={results}"

    conn = dbm.connect_existing(settings.db_file)
    try:
        acc_rows = conn.execute("SELECT id, public_id, username FROM accounts WHERE username LIKE 'mu_%'").fetchall()
        assert len(acc_rows) == rounds * workers
        assert len({int(r["public_id"]) for r in acc_rows}) == rounds * workers

        acc_ids = [int(r["id"]) for r in acc_rows]
        placeholders = ",".join("?" for _ in acc_ids)
        kb_rows = conn.execute(f"SELECT id, seq, number, label, account_id, is_personal FROM knowledge_bases WHERE account_id IN ({placeholders})", acc_ids).fetchall()
        assert len(kb_rows) == rounds * workers
        assert len({int(r["id"]) for r in kb_rows}) == rounds * workers
        assert len({int(r["seq"]) for r in kb_rows}) == rounds * workers
        assert len({int(r["number"]) for r in kb_rows}) == rounds * workers
        assert all(r["label"] == "General" and int(r["is_personal"]) == 1 for r in kb_rows)
    finally:
        conn.close()

    auth_conn = auth_db.connect_auth_db(auth_db.auth_db_path(settings.data_dir))
    try:
        for _, pub_id in created_invites:
            inv_info = auth_db.get_invitation_by_id(auth_conn, int(pub_id))
            assert inv_info is not None
            assert inv_info.uses_count == workers
            redemptions = auth_conn.execute("SELECT status, account_id FROM invitation_redemptions WHERE invitation_id=?", (inv_info.id,)).fetchall()
            assert len(redemptions) == workers
            assert all(r["status"] == "completed" and r["account_id"] is not None for r in redemptions)
    finally:
        auth_conn.close()


def test_concurrent_invite_signup_duplicate_username_returns_400_and_rolls_back(settings: _Settings):
    admin_session = _setup_admin(settings)
    _set_mode(settings, "network")
    app = server.create_app(settings, SimpleNamespace(provider=SimpleNamespace(name="stub")))
    admin_client = _Client(TestClient(app, base_url=PUBLIC_URL))

    inv_res = admin_client.request("POST", "/api/invites", session=admin_session, json={"max_uses": 2, "note": "dup-race"})
    assert inv_res.status_code == 201
    code = inv_res.json()["code"]
    pub_id = inv_res.json()["public_id"]

    barrier = threading.Barrier(2)

    def _signup_dup() -> tuple[int, dict[str, Any]]:
        tc = TestClient(app, base_url=PUBLIC_URL)
        barrier.wait(timeout=10.0)
        res = tc.post(f"/invite/{code}", headers={"Origin": ORIGIN}, json={"username": "race_dup_alice", "display_name": "Race Dup", "password": MEMBER_PASSWORD})
        return res.status_code, res.json()

    with concurrent.futures.ThreadPoolExecutor(max_workers=2) as executor:
        futures = [executor.submit(_signup_dup) for _ in range(2)]
        results = [f.result(timeout=20.0) for f in futures]

    statuses = sorted(status for status, _ in results)
    assert statuses == [201, 400], f"unexpected results={results}"
    errors = [payload.get("error") for status, payload in results if status == 400]
    assert errors == ["username_taken"]

    auth_conn = auth_db.connect_auth_db(auth_db.auth_db_path(settings.data_dir))
    try:
        inv_info = auth_db.get_invitation_by_id(auth_conn, int(pub_id))
        assert inv_info is not None
        assert inv_info.uses_count == 1
    finally:
        auth_conn.close()
