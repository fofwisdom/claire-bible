"""요청 주체(Principal) 매핑 (설계 §8.2, §8.3, §8.5, §13.1).

`store.accounts`의 검증 함수와 ``AccountStore.get``은 대역으로 바꿔 끼운다. 이 파일은 미들웨어가 자격증명을 어느 계정·범위·채널로 매핑하는지만 본다.
"""

from __future__ import annotations

import dataclasses
import json
from pathlib import Path
from types import SimpleNamespace

import pytest
from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import JSONResponse
from starlette.routing import Route
from starlette.testclient import TestClient

from claire.api import principal as principal_mod
from claire.api import security
from claire.api.principal import (
    ANONYMOUS,
    STEP_UP_WINDOW_SECONDS,
    Principal,
    PrincipalMissing,
    has_recent_auth,
    request_principal,
)
from claire.store import accounts as accounts_store
from claire.store import db as dbm

DEV_ORIGIN = "http://192.0.2.10:8766"

COOKIE_SESSION = "s" * 43
X_SESSION = "x" * 43
SUSPENDED_SESSION = "z" * 43
ORPHAN_SESSION = "q" * 43
OAUTH_TOKEN = "oauth-" + "a" * 40
OAUTH_TOKEN_SUSPENDED = "oauth-" + "s" * 40
MANUAL_TOKEN_RETIRED = "cb_" + "l" * 40

ADMIN_AUTH_AT = 1_700_000_000.0


def _account(account_id: int, role: str, status: str) -> accounts_store.Account:
    return accounts_store.Account(
        id=account_id,
        public_id=account_id,
        username=None,
        role=role,  # type: ignore[arg-type]
        status=status,  # type: ignore[arg-type]
        display_name="",
        profile_public=False,
        created_at=0.0,
        activated_at=None,
        delete_after=None,
        updated_at=0.0,
    )


ACCOUNTS = {
    1: _account(1, "admin", "setup_required"),
    7: _account(7, "member", "active"),
    9: _account(9, "member", "suspended"),
}

SESSIONS = {
    COOKIE_SESSION: accounts_store.CredentialBinding(
        scope="owner", account_id=1, authenticated_at=ADMIN_AUTH_AT, credential_id="session:aaaa"
    ),
    X_SESSION: accounts_store.CredentialBinding(
        scope="write", account_id=7, authenticated_at=ADMIN_AUTH_AT + 5, credential_id="session:bbbb"
    ),
    SUSPENDED_SESSION: accounts_store.CredentialBinding(
        scope="owner", account_id=9, authenticated_at=ADMIN_AUTH_AT, credential_id="session:cccc"
    ),
    ORPHAN_SESSION: accounts_store.CredentialBinding(
        scope="owner", account_id=404, authenticated_at=ADMIN_AUTH_AT, credential_id="session:dddd"
    ),
}

OAUTH = {
    OAUTH_TOKEN: accounts_store.CredentialBinding(
        scope="readonly", account_id=7, authenticated_at=None, credential_id="oauth:eeee"
    ),
    OAUTH_TOKEN_SUSPENDED: accounts_store.CredentialBinding(
        scope="write", account_id=9, authenticated_at=None, credential_id="oauth:ffff"
    ),
}


class _FakeConn:
    def close(self) -> None:
        pass


@pytest.fixture
def fake_accounts(monkeypatch):
    def get(self, account_id):
        try:
            return ACCOUNTS[account_id]
        except KeyError:
            raise accounts_store.AccountNotFound(account_id) from None

    def validate_session_binding(_conn, token, *, scopes=("owner", "write", "readonly")):
        binding = SESSIONS.get(token)
        return binding if binding is not None and binding.scope in scopes else None

    def validate_oauth_binding(_conn, token):
        return OAUTH.get(token)

    monkeypatch.setattr(accounts_store.AccountStore, "get", get)
    monkeypatch.setattr(accounts_store, "validate_session_binding", validate_session_binding)
    monkeypatch.setattr(accounts_store, "validate_oauth_binding", validate_oauth_binding)
    monkeypatch.setattr(dbm, "connect_existing", lambda *_a, **_k: _FakeConn())


@pytest.fixture
def data_dir(tmp_path: Path) -> Path:
    # OAuth 검증은 DB 파일이 있을 때만 시도한다.
    (tmp_path / "claire.db").touch()
    return tmp_path


def _settings(data_dir: Path) -> SimpleNamespace:
    return SimpleNamespace(
        environment="development",
        public_url=DEV_ORIGIN,
        cors_allowed_origins="",
        db_file=data_dir / "claire.db",
        data_dir=data_dir,
    )


async def _echo(request: Request) -> JSONResponse:
    return JSONResponse(
        {
            "auth_scope": security.request_auth_scope(request),
            "principal": dataclasses.asdict(request_principal(request)),
        }
    )


def _client(data_dir: Path) -> TestClient:
    inner = Starlette(
        routes=[
            Route("/whoami", _echo, methods=["GET"]),
            Route("/health", _echo, methods=["GET"]),
            Route("/syndication", _echo, methods=["GET"]),
            Route("/ingest", _echo, methods=["POST"]),
        ]
    )
    app = security.wrap_web_app(inner, _settings(data_dir))
    return TestClient(app, base_url=DEV_ORIGIN)


def _cookie(token: str) -> dict[str, str]:
    # 요청마다 헤더로 보낸다. 클라이언트 쿠키 저장소는 다음 요청에 자격증명을 섞는다.
    return {"Cookie": f"claire_session={token}"}


def _bearer(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


def _principal(response) -> dict:
    assert response.status_code == 200, response.text
    return response.json()["principal"]


# --- 익명 -------------------------------------------------------------------


def test_anonymous_read_gets_anonymous_principal(fake_accounts, data_dir):
    response = _client(data_dir).get("/whoami")
    assert response.json()["auth_scope"] == "anonymous"
    assert _principal(response) == dataclasses.asdict(ANONYMOUS)


def test_public_route_is_anonymous_and_does_not_validate_credentials(fake_accounts, data_dir, monkeypatch):
    calls: list[str] = []

    async def spy(_config, token):
        calls.append(token)
        return None

    monkeypatch.setattr(security, "_validate_session", spy)
    response = _client(data_dir).get("/health", headers=_cookie(COOKIE_SESSION))
    assert response.json()["auth_scope"] == "public"
    assert _principal(response) == dataclasses.asdict(ANONYMOUS)
    assert calls == []


# --- 퇴역 수동 토큰 거부 ---------------------------------------------------------


def test_retired_manual_bearer_tokens_are_rejected(fake_accounts, data_dir):
    auth_dir = data_dir / "auth"
    auth_dir.mkdir(exist_ok=True)
    (auth_dir / "api_tokens.json").write_text(
        json.dumps({"version": 1, "tokens": [{"id": 3, "token": MANUAL_TOKEN_RETIRED, "name": "legacy", "scope": "owner", "account_id": 1}]}),
        encoding="utf-8",
    )
    assert _client(data_dir).post("/ingest", headers=_bearer(MANUAL_TOKEN_RETIRED)).status_code == 404


# --- 세션, OAuth ----------------------------------------------------------------


def test_cookie_session_carries_authenticated_at(fake_accounts, data_dir):
    principal = _principal(
        _client(data_dir).get("/whoami", headers=_cookie(COOKIE_SESSION))
    )
    assert principal == {
        "account_id": 1,
        "server_role": "admin",
        "scope": "owner",
        "channel": "cookie",
        "credential_id": "session:aaaa",
        "authenticated_at": ADMIN_AUTH_AT,
        "locale": None,
        "entity_language": None,
    }


def test_x_session_carries_authenticated_at(fake_accounts, data_dir):
    principal = _principal(_client(data_dir).get("/whoami", headers={"X-Session": X_SESSION}))
    assert principal["channel"] == "x-session"
    assert principal["account_id"] == 7
    assert principal["server_role"] == "member"
    assert principal["scope"] == "write"
    assert principal["authenticated_at"] == ADMIN_AUTH_AT + 5


def test_session_as_bearer_has_no_authenticated_at(fake_accounts, data_dir):
    principal = _principal(_client(data_dir).get("/whoami", headers=_bearer(COOKIE_SESSION)))
    assert principal["channel"] == "bearer"
    assert principal["account_id"] == 1
    assert principal["authenticated_at"] is None


def test_oauth_token_maps_to_bound_account(fake_accounts, data_dir):
    principal = _principal(_client(data_dir).get("/whoami", headers=_bearer(OAUTH_TOKEN)))
    assert principal == {
        "account_id": 7,
        "server_role": "member",
        "scope": "readonly",
        "channel": "bearer",
        "credential_id": "oauth:eeee",
        "authenticated_at": None,
        "locale": None,
        "entity_language": None,
    }


# --- 계정 상태 ------------------------------------------------------------------


@pytest.mark.parametrize(
    "kwargs",
    [
        {"headers": _cookie(SUSPENDED_SESSION)},
        {"headers": {"X-Session": SUSPENDED_SESSION}},
        {"headers": _bearer(OAUTH_TOKEN_SUSPENDED)},
        {"headers": _cookie(ORPHAN_SESSION)},
    ],
)
def test_credentials_of_non_accepting_accounts_look_like_unknown_credentials(fake_accounts, data_dir, kwargs):
    client = _client(data_dir)
    rejected = client.get("/syndication", **kwargs)
    unknown_values = {
        "Cookie": "claire_session=" + "u" * 43,
        "X-Session": "u" * 43,
        "Authorization": "Bearer cb_" + "u" * 40,
    }
    unknown = client.get(
        "/syndication", headers={name: unknown_values[name] for name in kwargs["headers"]}
    )
    assert rejected.status_code == unknown.status_code == 404
    assert rejected.content == unknown.content
    assert rejected.headers.get("set-cookie") == unknown.headers.get("set-cookie")


def test_suspended_account_falls_back_to_anonymous_only_like_unknown_cookie(fake_accounts, data_dir):
    client = _client(data_dir)
    response = client.get("/whoami", headers=_cookie(SUSPENDED_SESSION))
    assert _principal(response) == dataclasses.asdict(ANONYMOUS)
    assert "Max-Age=0" in response.headers["set-cookie"]


def test_admin_setup_required_status_accepts_credentials(fake_accounts, data_dir):
    assert ACCOUNTS[1].status == "setup_required"
    assert "setup_required" in accounts_store.CREDENTIAL_ACCEPTING_STATUSES
    response = _client(data_dir).post("/ingest", headers=_bearer(COOKIE_SESSION))
    assert _principal(response)["scope"] == "owner"


# --- credential_id ----------------------------------------------------------------


def test_credential_id_never_contains_token_value(fake_accounts, data_dir):
    client = _client(data_dir)
    cases = [
        (OAUTH_TOKEN, {"headers": _bearer(OAUTH_TOKEN)}),
        (COOKIE_SESSION, {"headers": _cookie(COOKIE_SESSION)}),
        (X_SESSION, {"headers": {"X-Session": X_SESSION}}),
        (COOKIE_SESSION, {"headers": _bearer(COOKIE_SESSION)}),
    ]
    for token, kwargs in cases:
        client.cookies.clear()  # 앞 요청이 갱신한 세션 쿠키가 섞이지 않게 한다
        response = client.get("/whoami", **kwargs)
        credential_id = _principal(response)["credential_id"]
        assert credential_id
        assert token not in credential_id
        assert token[:16] not in credential_id
        assert token not in response.text


# --- request_principal, has_recent_auth -----------------------------------------


def test_request_principal_without_middleware_raises():
    async def endpoint(request: Request) -> JSONResponse:
        request_principal(request)
        return JSONResponse({})

    client = TestClient(Starlette(routes=[Route("/", endpoint)]), raise_server_exceptions=True)
    with pytest.raises(PrincipalMissing):
        client.get("/")


def _session_principal(channel: str, authenticated_at: float | None) -> Principal:
    return Principal(
        account_id=1,
        server_role="admin",
        scope="owner",
        channel=channel,  # type: ignore[arg-type]
        credential_id="session:aaaa",
        authenticated_at=authenticated_at,
    )


@pytest.mark.parametrize("channel", ["cookie", "x-session"])
def test_has_recent_auth_window_boundary(channel):
    auth_at = 1_000.0
    p = _session_principal(channel, auth_at)
    assert has_recent_auth(p, now=auth_at)
    assert has_recent_auth(p, now=auth_at + STEP_UP_WINDOW_SECONDS)
    assert not has_recent_auth(p, now=auth_at + STEP_UP_WINDOW_SECONDS + 0.001)
    assert not has_recent_auth(p, now=auth_at - 1)  # 미래 시각은 믿지 않는다
    assert STEP_UP_WINDOW_SECONDS == 15 * 60


def test_has_recent_auth_is_false_for_bearer_anonymous_and_missing_time(monkeypatch):
    monkeypatch.setattr(principal_mod.time, "time", lambda: 1_000.0)
    assert not has_recent_auth(_session_principal("bearer", 1_000.0))
    assert not has_recent_auth(_session_principal("cookie", None))
    assert not has_recent_auth(ANONYMOUS)
    assert has_recent_auth(_session_principal("cookie", 1_000.0))
