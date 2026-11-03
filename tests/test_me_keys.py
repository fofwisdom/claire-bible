"""``/api/me/keys`` 쓰기 전용 계정 API 키 (설계 §11, §13.3 ``account_provider_keys``, P4 L4-b).

- 네트워크 모드 멤버 전용. 싱글 모드 404, 관리자 403 ``admin_uses_provider_settings``, 익명 404.
- 응답·헤더·로그 어디에도 카나리아 키가 없다. 시험 호출(``verify_key``)은 monkeypatch한다(외부 호출 없음).
- AAD(``account_id|provider|key_id``) 때문에 암호문을 다른 행으로 옮기면 복호화가 실패한다.

모든 상태는 ``tmp_path``에만 쓰고, ``CLAIRE_*``·``GEMINI_*`` 환경변수를 격리한다.
"""

from __future__ import annotations

import logging
import os
import time
from dataclasses import dataclass
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
from starlette.testclient import TestClient

from claire import secret_scrub
from claire.api import server
from claire.api.principal import STEP_UP_WINDOW_SECONDS
from claire.store import accounts as accounts_store
from claire.store import auth_db
from claire.store import db as dbm
from claire.store import provider_keys
from claire.store.secrets import SecretService, reset_secret_service

PUBLIC_URL = "http://127.0.0.1:8765"
ORIGIN = PUBLIC_URL
OWNER_TOKEN = "owner-" + "o" * 32
ADMIN_PASSWORD = "Correct-Horse-Battery-123!"
TEST_MASTER_KEY_HEX = "11" * 32
# 키 모양 패턴(AIza…, sk-…)에 걸리지 않는 카나리아. 정규식 스크럽이 아니라 "싣지 않음"을 확인한다.
CANARY = "canary-member-key-7f3a9c1e5b2d4f60"
CANARY_2 = "canary-member-key-replacement-0b1c2d3e"
CANARY_B = "canary-other-member-key-99aa88bb77cc"
META_FIELDS = {"provider", "fingerprint", "status", "created_at", "last_verified_at", "last_used_at"}


@dataclass
class _Settings:
    db_file: Path
    data_dir: Path
    environment: str = "development"
    public_url: str = PUBLIC_URL
    inject_token: str = OWNER_TOKEN
    readonly_token: str = "readonly-" + "r" * 32
    cors_allowed_origins: str = ""
    anonymous_readonly: bool = False
    fqdn: str = "notes.kimhome.net"
    multi_theme: bool = True


class _Client:
    """모든 응답을 모아 두고 카나리아 검사에 쓴다."""

    def __init__(self, client: TestClient) -> None:
        self.client = client
        self.responses: list[Any] = []

    def request(self, method: str, path: str, *, session: str | None = None, bearer: str | None = None, **kwargs: Any):
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
        self.responses.append(response)
        return response

    def assert_never_leaked(self, *values: str) -> None:
        for response in self.responses:
            blob = response.content.decode("utf-8", errors="replace") + repr(list(response.headers.items()))
            for value in values:
                assert value not in blob, f"{response.request.method} {response.request.url} leaked a key"


@pytest.fixture(autouse=True)
def _isolate_env_and_secrets(monkeypatch: pytest.MonkeyPatch):
    for key in list(os.environ):
        if key.startswith("CLAIRE_") or key.startswith("GEMINI_") or key.startswith("GOOGLE_API"):
            monkeypatch.delenv(key, raising=False)
    # 저장소 루트 data/auth/master.key를 만들지 않도록 마스터 키를 환경변수로 준다.
    monkeypatch.setenv("CLAIRE_SECRET_KEY", TEST_MASTER_KEY_HEX)
    reset_secret_service()
    secret_scrub._reset_for_tests()
    yield
    reset_secret_service()
    secret_scrub._reset_for_tests()


@pytest.fixture
def verify_calls(monkeypatch: pytest.MonkeyPatch) -> list[tuple[str, str]]:
    """``verify_key`` 대역. ``bad`` 가 들어간 키는 거부하고, 거부 문구에 키 원문을 일부러 섞는다(스크럽 확인)."""
    calls: list[tuple[str, str]] = []
    _REJECT_ALL[0] = False
    _UNAVAILABLE[0] = False

    def _fake(provider: str, api_key: str) -> tuple[bool, str]:
        calls.append((provider, api_key))
        if _UNAVAILABLE[0]:
            raise provider_keys.ProviderKeyVerifyUnavailable(f"503 upstream down for {api_key}")
        if "bad" in api_key or _REJECT_ALL[0]:
            return False, f"API key not valid: {api_key}"
        return True, "ok"

    monkeypatch.setattr(provider_keys, "verify_key", _fake)
    return calls


_REJECT_ALL = [False]
_UNAVAILABLE = [False]


def _reject_all(flag: bool) -> None:
    _REJECT_ALL[0] = flag


@pytest.fixture
def settings(tmp_path: Path) -> _Settings:
    return _Settings(db_file=tmp_path / "claire.db", data_dir=tmp_path)


def _auth(settings: _Settings):
    return auth_db.connect_auth_db(auth_db.auth_db_path(settings.data_dir))


def _setup_admin(settings: _Settings) -> None:
    conn = dbm.connect(settings.db_file)
    try:
        dbm.init_db(conn)
        accounts_store.AccountStore(conn).complete_setup(1, username="admin_kim", display_name="Admin Kim")
        auth = _auth(settings)
        try:
            auth_db.set_password(auth, 1, ADMIN_PASSWORD, username="admin_kim")
        finally:
            auth.close()
    finally:
        conn.close()


def _set_mode(settings: _Settings, mode: str) -> None:
    conn = dbm.connect_existing(settings.db_file)
    try:
        accounts_store.AccountStore(conn).set_setting("mode", mode, updated_by=1)
    finally:
        conn.close()


def _add_member(settings: _Settings, username: str, public_id: int) -> int:
    conn = dbm.connect_existing(settings.db_file)
    try:
        cur = conn.execute(
            "INSERT INTO accounts (public_id, username, role, status, created_at, updated_at) "
            "VALUES (?, ?, 'member', 'active', 0, 0)",
            (public_id, username),
        )
        conn.commit()
        return int(cur.lastrowid)
    finally:
        conn.close()


def _session(settings: _Settings, account_id: int, *, authenticated_ago: float = 0.0, scope: str = "owner") -> str:
    conn = dbm.connect_existing(settings.db_file)
    try:
        return accounts_store.create_account_session(
            conn, account_id=account_id, scope=scope, now=time.time() - authenticated_ago
        )
    finally:
        conn.close()


@pytest.fixture
def env(settings: _Settings):
    """네트워크 모드, 관리자 1 + 멤버 2."""
    _setup_admin(settings)
    member_a = _add_member(settings, "member_alpha", 223456789012)
    member_b = _add_member(settings, "member_bravo", 323456789012)
    _set_mode(settings, "network")
    app = server.create_app(settings, SimpleNamespace(provider=SimpleNamespace(name="stub")))
    with TestClient(app, base_url=PUBLIC_URL) as tc:
        yield SimpleNamespace(client=_Client(tc), settings=settings, a=member_a, b=member_b)


def _put(client: _Client, session: str, api_key: str, provider: str = "gemini"):
    return client.request("PUT", "/api/me/keys", session=session, json={"provider": provider, "api_key": api_key})


def _rows(settings: _Settings) -> list[dict[str, Any]]:
    auth = _auth(settings)
    try:
        return [dict(r) for r in auth.execute("SELECT * FROM account_provider_keys ORDER BY account_id").fetchall()]
    finally:
        auth.close()


# --- 모드·역할 경계 ----------------------------------------------------------------


def test_single_mode_hides_every_key_route(settings: _Settings, verify_calls):
    _setup_admin(settings)
    member = _add_member(settings, "member_alpha", 223456789012)
    app = server.create_app(settings, SimpleNamespace(provider=SimpleNamespace(name="stub")))
    with TestClient(app, base_url=PUBLIC_URL) as tc:
        client = _Client(tc)
        for sess in (_session(settings, member), _session(settings, 1)):
            assert client.request("GET", "/api/me/keys", session=sess).status_code == 404
            assert _put(client, sess, CANARY).status_code == 404
            assert client.request("POST", "/api/me/keys/verify", session=sess, json={"provider": "gemini"}).status_code == 404
            assert client.request("DELETE", "/api/me/keys", session=sess).status_code == 404
        page = client.request("GET", "/me", session=_session(settings, member))
        assert page.status_code == 200 and "내 API 키" not in page.text
    assert verify_calls == []
    assert _rows(settings) == []


def test_admin_gets_403_and_anonymous_404(env, verify_calls):
    c = env.client
    admin = _session(env.settings, 1)
    for kwargs in ({"session": admin}, {"bearer": OWNER_TOKEN}):
        r = c.request("GET", "/api/me/keys", **kwargs)
        assert r.status_code == 403 and r.json() == {"error": "admin_uses_provider_settings"}
        assert c.request("PUT", "/api/me/keys", json={"provider": "gemini", "api_key": CANARY}, **kwargs).status_code == 403
        assert c.request("POST", "/api/me/keys/verify", json={"provider": "gemini"}, **kwargs).status_code == 403
        assert c.request("DELETE", "/api/me/keys", **kwargs).status_code == 403
    # 익명은 존재를 숨긴다(ROUTE_POLICY owner 경로의 fail-closed 404).
    assert c.request("GET", "/api/me/keys").status_code == 404
    assert c.request("PUT", "/api/me/keys", json={"provider": "gemini", "api_key": CANARY}).status_code == 404
    assert c.request("POST", "/api/me/keys/verify", json={"provider": "gemini"}).status_code == 404
    assert c.request("DELETE", "/api/me/keys").status_code == 404
    # readonly 세션은 owner 경로에 닿지 않는다.
    ro = _session(env.settings, env.a, scope="readonly")
    assert c.request("GET", "/api/me/keys", session=ro).status_code in {401, 403, 404}
    assert verify_calls == []
    assert _rows(env.settings) == []
    admin_page = c.request("GET", "/me", session=admin)
    assert admin_page.status_code == 200 and "내 API 키" not in admin_page.text
    c.assert_never_leaked(CANARY)


# --- 등록·조회·교체·삭제 ------------------------------------------------------------


def test_register_returns_metadata_only_and_never_leaks(env, verify_calls, caplog):
    caplog.set_level(logging.DEBUG)
    c = env.client
    sess = _session(env.settings, env.a)

    assert c.request("GET", "/api/me/keys", session=sess).json() == {"keys": []}
    r = _put(c, sess, f"  {CANARY}  ")
    assert r.status_code == 200, r.text
    meta = r.json()["key"]
    assert set(meta) == META_FIELDS
    assert meta["provider"] == "gemini" and meta["status"] == "active"
    assert len(meta["fingerprint"]) == 8 and meta["last_verified_at"] is not None and meta["last_used_at"] is None
    assert verify_calls == [("gemini", CANARY)]  # 앞뒤 공백만 걷어 낸 값으로 시험한다

    listed = c.request("GET", "/api/me/keys", session=sess)
    assert listed.status_code == 200 and listed.headers.get("cache-control") == "no-store"
    assert listed.json() == {"keys": [meta]}
    assert c.request("HEAD", "/api/me/keys", session=sess).status_code == 200

    # fingerprint는 용도별 파생 HMAC 앞 8자리이고 키의 어떤 부분 문자열도 아니다(D8).
    expected = SecretService(master_key=bytes.fromhex(TEST_MASTER_KEY_HEX)).compute_fingerprint(
        CANARY, purpose=provider_keys.FINGERPRINT_PURPOSE
    )[:8]
    assert meta["fingerprint"] == expected and meta["fingerprint"] not in CANARY

    row = _rows(env.settings)[0]
    assert row["account_id"] == env.a and CANARY.encode() not in bytes(row["ciphertext"])

    page = c.request("GET", "/me", session=sess)
    assert page.status_code == 200
    assert "내 API 키" in page.text and "root" in page.text and meta["fingerprint"] in page.text
    assert "다른 키로 바꿔 쓰지 않습니다" in page.text
    assert page.text.count("<script") == 1

    # 다시 검증과 삭제까지 거친 뒤 전체 응답·로그를 확인한다.
    v = c.request("POST", "/api/me/keys/verify", session=sess, json={"provider": "gemini"})
    assert v.status_code == 200 and v.json()["ok"] is True and set(v.json()["key"]) == META_FIELDS
    assert c.request("DELETE", "/api/me/keys", session=sess).json() == {"ok": True, "deleted": 1}

    c.assert_never_leaked(CANARY)
    assert CANARY not in caplog.text
    for record in caplog.records:
        assert CANARY not in record.getMessage()


def test_verify_failure_does_not_store_and_scrubs_message(env, verify_calls, caplog):
    caplog.set_level(logging.DEBUG)
    c = env.client
    sess = _session(env.settings, env.a)
    bad = CANARY + "-bad"
    r = _put(c, sess, bad)
    assert r.status_code == 422
    body = r.json()
    assert body["error"] == "key_verification_failed"
    assert "[REDACTED]" in body["message"]
    assert _rows(env.settings) == []
    assert c.request("GET", "/api/me/keys", session=sess).json() == {"keys": []}
    c.assert_never_leaked(bad, CANARY)
    assert bad not in caplog.text


def test_input_validation(env, verify_calls):
    c = env.client
    sess = _session(env.settings, env.a)
    assert _put(c, sess, CANARY, provider="openai").json()["error"] == "unsupported_provider"
    assert _put(c, sess, CANARY, provider="antigravity").status_code == 400
    assert c.request("PUT", "/api/me/keys", session=sess, json={"provider": "gemini"}).status_code == 400
    assert c.request(
        "PUT", "/api/me/keys", session=sess, json={"provider": "gemini", "api_key": CANARY, "extra": 1}
    ).status_code == 400
    for value in ("", "   ", "has space inside", "x" * 513, 12345):
        r = c.request("PUT", "/api/me/keys", session=sess, json={"provider": "gemini", "api_key": value})
        assert r.status_code == 400, value
    assert c.request("POST", "/api/me/keys/verify", session=sess, json={}).status_code == 400
    assert c.request("DELETE", "/api/me/keys?provider=openai", session=sess).status_code == 400
    assert verify_calls == []
    assert _rows(env.settings) == []
    c.assert_never_leaked(CANARY)


def test_replace_changes_fingerprint_and_key_id(env, verify_calls):
    c = env.client
    sess = _session(env.settings, env.a)
    first = _put(c, sess, CANARY).json()["key"]
    first_row = _rows(env.settings)[0]
    second = _put(c, sess, CANARY_2).json()["key"]
    rows = _rows(env.settings)
    assert len(rows) == 1
    assert second["fingerprint"] != first["fingerprint"]
    assert rows[0]["id"] != first_row["id"] and bytes(rows[0]["nonce"]) != bytes(first_row["nonce"])
    auth = _auth(env.settings)
    try:
        active = provider_keys.load_active_key(auth, account_id=env.a)
    finally:
        auth.close()
    assert active is not None and active.api_key == CANARY_2 and CANARY_2 not in repr(active)
    c.assert_never_leaked(CANARY, CANARY_2)


def test_reverify_marks_rejected_and_inactive(env, verify_calls):
    c = env.client
    sess = _session(env.settings, env.a)
    assert _put(c, sess, CANARY).status_code == 200
    _reject_all(True)
    r = c.request("POST", "/api/me/keys/verify", session=sess, json={"provider": "gemini"})
    assert r.status_code == 200
    body = r.json()
    assert body["ok"] is False and body["key"]["status"] == "rejected" and "[REDACTED]" in body["message"]
    auth = _auth(env.settings)
    try:
        assert provider_keys.load_active_key(auth, account_id=env.a) is None
        assert provider_keys.has_key(auth, account_id=env.a) is True
    finally:
        auth.close()
    _reject_all(False)
    again = c.request("POST", "/api/me/keys/verify", session=sess, json={"provider": "gemini"}).json()
    assert again["ok"] is True and again["key"]["status"] == "active"
    c.assert_never_leaked(CANARY)


def test_put_does_not_store_when_verification_unavailable(env, verify_calls, caplog):
    caplog.set_level(logging.DEBUG)
    c = env.client
    sess = _session(env.settings, env.a)
    _UNAVAILABLE[0] = True
    r = _put(c, sess, CANARY)
    assert r.status_code == 503
    assert r.json()["error"] == "key_verification_unavailable" and "[REDACTED]" in r.json()["message"]
    assert _rows(env.settings) == [] and c.request("GET", "/api/me/keys", session=sess).json() == {"keys": []}
    c.assert_never_leaked(CANARY)
    assert CANARY not in caplog.text


def test_reverify_unavailable_leaves_status_untouched(env, verify_calls):
    c = env.client
    sess = _session(env.settings, env.a)
    assert _put(c, sess, CANARY).status_code == 200
    before = _rows(env.settings)[0]
    _UNAVAILABLE[0] = True
    r = c.request("POST", "/api/me/keys/verify", session=sess, json={"provider": "gemini"})
    assert r.status_code == 503 and r.json()["error"] == "key_verification_unavailable"
    after = _rows(env.settings)[0]
    assert (after["status"], after["last_verified_at"]) == (before["status"], before["last_verified_at"]) == (
        "active", before["last_verified_at"]
    )
    auth = _auth(env.settings)
    try:
        assert provider_keys.load_active_key(auth, account_id=env.a) is not None
    finally:
        auth.close()
    c.assert_never_leaked(CANARY)


def test_step_up_required_for_put_and_delete(env, verify_calls):
    c = env.client
    stale = _session(env.settings, env.a, authenticated_ago=STEP_UP_WINDOW_SECONDS + 60)
    r = _put(c, stale, CANARY)
    assert r.status_code == 401 and r.json()["error"] == "reauth_required"
    assert verify_calls == [] and _rows(env.settings) == []
    # 등록 뒤 오래된 세션으로는 지우지 못하지만 조회·재검증은 된다.
    assert _put(c, _session(env.settings, env.a), CANARY).status_code == 200
    d = c.request("DELETE", "/api/me/keys", session=stale)
    assert d.status_code == 401 and d.json()["error"] == "reauth_required"
    assert len(_rows(env.settings)) == 1
    assert c.request("GET", "/api/me/keys", session=stale).status_code == 200
    assert c.request("POST", "/api/me/keys/verify", session=stale, json={"provider": "gemini"}).status_code == 200
    ok = c.request("DELETE", "/api/me/keys?provider=gemini", session=_session(env.settings, env.a))
    assert ok.json() == {"ok": True, "deleted": 1} and _rows(env.settings) == []
    c.assert_never_leaked(CANARY)


def test_other_accounts_cannot_touch_the_key(env, verify_calls):
    c = env.client
    sess_a = _session(env.settings, env.a)
    sess_b = _session(env.settings, env.b)
    assert _put(c, sess_a, CANARY).status_code == 200
    assert c.request("GET", "/api/me/keys", session=sess_b).json() == {"keys": []}
    r = c.request("POST", "/api/me/keys/verify", session=sess_b, json={"provider": "gemini"})
    assert r.status_code == 404 and r.json()["error"] == "key_not_found"
    assert c.request("DELETE", "/api/me/keys", session=sess_b).json() == {"ok": True, "deleted": 0}
    assert [row["account_id"] for row in _rows(env.settings)] == [env.a]
    # 본문에 다른 계정 번호를 넣을 자리가 없다.
    assert c.request(
        "PUT", "/api/me/keys", session=sess_b, json={"provider": "gemini", "api_key": CANARY_B, "account_id": env.a}
    ).status_code == 400
    assert len(c.request("GET", "/api/me/keys", session=sess_a).json()["keys"]) == 1
    c.assert_never_leaked(CANARY, CANARY_B)


def test_admin_list_shows_only_has_provider_key(env, verify_calls):
    c = env.client
    admin = _session(env.settings, 1)

    def _by_name() -> dict[str, dict[str, Any]]:
        data = c.request("GET", "/api/admin/accounts", session=admin).json()
        return {a["username"]: a for a in data["accounts"]}

    before = _by_name()
    assert before["member_alpha"]["has_provider_key"] is False
    assert "has_provider_key" not in before["admin_kim"]
    assert _put(c, _session(env.settings, env.a), CANARY).status_code == 200
    after = _by_name()
    assert after["member_alpha"]["has_provider_key"] is True
    assert after["member_bravo"]["has_provider_key"] is False
    # 계정 status는 원래 있는 필드다. 키 메타(fingerprint·provider·검증 시각)는 싣지 않는다.
    assert set(after["member_alpha"]) - set(before["member_alpha"]) == set()
    assert not ({"fingerprint", "provider", "last_verified_at", "last_used_at"} & set(after["member_alpha"]))
    c.assert_never_leaked(CANARY)


# --- 저장소 계약 ----------------------------------------------------------------------


def test_aad_binds_ciphertext_to_its_row(env, verify_calls):
    c = env.client
    assert _put(c, _session(env.settings, env.a), CANARY).status_code == 200
    assert _put(c, _session(env.settings, env.b), CANARY_B).status_code == 200
    auth = _auth(env.settings)
    try:
        assert provider_keys.load_active_key(auth, account_id=env.b).api_key == CANARY_B
        a_row = auth.execute(
            "SELECT ciphertext, nonce FROM account_provider_keys WHERE account_id=?", (env.a,)
        ).fetchone()
        # A의 암호문을 B 행으로 옮긴다.
        with auth:
            auth.execute(
                "UPDATE account_provider_keys SET ciphertext=?, nonce=? WHERE account_id=?",
                (a_row["ciphertext"], a_row["nonce"], env.b),
            )
        with pytest.raises(provider_keys.ProviderKeyError) as excinfo:
            provider_keys.load_active_key(auth, account_id=env.b)
        assert CANARY not in str(excinfo.value)
        # A의 행 자체를 다른 계정으로 옮겨도 실패한다.
        with auth:
            auth.execute("DELETE FROM account_provider_keys WHERE account_id=?", (env.b,))
            auth.execute("UPDATE account_provider_keys SET account_id=? WHERE account_id=?", (env.b, env.a))
        with pytest.raises(provider_keys.ProviderKeyError):
            provider_keys.load_active_key(auth, account_id=env.b)
    finally:
        auth.close()
    # 재검증 경로도 복호화 실패를 409로 드러내고 폴백하지 않는다.
    r = c.request("POST", "/api/me/keys/verify", session=_session(env.settings, env.b), json={"provider": "gemini"})
    assert r.status_code == 409 and r.json()["error"] == "key_unreadable"
    c.assert_never_leaked(CANARY, CANARY_B)


def test_store_helpers_roundtrip(tmp_path: Path):
    auth = auth_db.connect_auth_db(auth_db.auth_db_path(tmp_path))
    sec = SecretService(master_key=bytes.fromhex(TEST_MASTER_KEY_HEX))
    try:
        assert provider_keys.load_active_key(auth, account_id=7, secret_service=sec) is None
        assert provider_keys.has_key(auth, account_id=7) is False
        meta = provider_keys.put_key(
            auth, account_id=7, provider="gemini", api_key=CANARY, status="unverified", secret_service=sec, now=100.0
        )
        assert meta.created_at == 100.0 and meta.last_verified_at is None and meta.status == "unverified"
        assert provider_keys.load_active_key(auth, account_id=7, secret_service=sec) is None
        provider_keys.set_key_status(auth, account_id=7, provider="gemini", status="active", verified_at=150.0)
        provider_keys.touch_last_used(auth, account_id=7, provider="gemini", now=200.0)
        (m,) = provider_keys.get_key_meta(auth, account_id=7)
        assert (m.status, m.last_verified_at, m.last_used_at) == ("active", 150.0, 200.0)
        key = provider_keys.load_active_key(auth, account_id=7, secret_service=sec)
        assert key is not None and key.api_key == CANARY and CANARY not in repr(key)
        # 복호화한 값은 스크러버에 등록된다.
        assert CANARY not in secret_scrub.scrub_text(f"boom {CANARY}")
        with pytest.raises(ValueError):
            provider_keys.set_key_status(auth, account_id=7, provider="gemini", status="bogus")  # type: ignore[arg-type]
        assert provider_keys.delete_key(auth, account_id=7) == 1
        assert provider_keys.delete_key(auth, account_id=7) == 0
    finally:
        auth.close()


def test_verify_key_scrubs_provider_errors(monkeypatch: pytest.MonkeyPatch):
    """실제 ``verify_key``를 외부 호출 없이: SDK 클라이언트를 대역으로 바꿔 오류 문구에 키가 남지 않음을 본다."""
    import sys
    import types

    class _ApiError(Exception):
        def __init__(self, code: int | None, text: str) -> None:
            super().__init__(text)
            if code is not None:
                self.code = code

    raised: list[BaseException] = []

    class _Models:
        def count_tokens(self, *, model: str, contents: str):
            raise raised[0]

    class _Client:
        key = ""

        def __init__(self, *, api_key: str) -> None:
            _Client.key = api_key
            self.models = _Models()

    genai = types.ModuleType("google.genai")
    genai.Client = _Client  # type: ignore[attr-defined]
    google = types.ModuleType("google")
    google.genai = genai  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "google", google)
    monkeypatch.setitem(sys.modules, "google.genai", genai)

    def _set(exc: BaseException) -> None:
        raised[:] = [exc]

    # 인증·권한 거부만 (False, msg).
    for exc in (
        RuntimeError(f"API key not valid. key={CANARY}"),
        _ApiError(401, f"UNAUTHENTICATED key={CANARY}"),
        _ApiError(403, f"PERMISSION_DENIED key={CANARY}"),
        _ApiError(400, f"API_KEY_INVALID key={CANARY}"),
    ):
        _set(exc)
        ok, message = provider_keys.verify_key("gemini", CANARY)
        assert ok is False and CANARY not in message and "[REDACTED]" in message
    # 판정 불가: 네트워크·타임아웃·429·5xx·그 밖의 4xx.
    for exc in (
        ConnectionError(f"connection reset ?key={CANARY}"),
        TimeoutError(f"timed out {CANARY}"),
        _ApiError(429, f"RESOURCE_EXHAUSTED {CANARY}"),
        _ApiError(503, f"UNAVAILABLE {CANARY}"),
        _ApiError(404, f"model not found {CANARY}"),
    ):
        _set(exc)
        with pytest.raises(provider_keys.ProviderKeyVerifyUnavailable) as excinfo:
            provider_keys.verify_key("gemini", CANARY)
        assert CANARY not in str(excinfo.value) and "[REDACTED]" in str(excinfo.value)
        assert excinfo.value.__cause__ is None and excinfo.value.__suppress_context__
    assert provider_keys.verify_key("openai", CANARY)[0] is False
