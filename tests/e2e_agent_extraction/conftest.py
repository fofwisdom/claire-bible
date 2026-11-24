"""Shared opaque-box fixtures and helpers for Claire Bible E2E tests (Tracks C & D)."""

from __future__ import annotations

import base64
import hashlib
import json
import os
import secrets
import subprocess
import sys
import time
from collections.abc import Callable, Iterator
from contextlib import ExitStack
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlencode, urlparse

import pytest
from starlette.testclient import TestClient

from claire.api import server
from claire.core.config import Settings, get_settings
from claire.ontology.base import Document
from claire.store import accounts as accounts_store
from claire.store import auth_db
from claire.store import db as dbm
from claire.store.knowledge_base import KnowledgeBaseManager
from claire.store.secrets import reset_secret_service
from claire.store.token_store import reset_token_stores

REPO_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_SECRET_KEY = "44" * 32


def pytest_configure(config: pytest.Config) -> None:
    """Register custom markers for Track C and Track D E2E tiers."""
    markers = [
        "track_c: Track C unified OAuth 2.1, Telegram-free login, and manual token deprecation tests",
        "track_d: Track D agent marketplace, staging, and client extraction tests",
        "ae1a: AE1a agent marketplace source validation and contract tests",
        "ae1b: AE1b marketplace metadata and settings UI guidance tests",
        "ae4: AE4 extraction mode of each ingest request, full-content staging, and pending isolation tests",
        "ae5: AE5 client extraction MCP/HTTP contract, validation gates, and atomic commit tests",
        "ae7: AE7 reference agent plugin extraction flow and documentation tests",
        "tier1: Tier 1 isolated happy-path E2E tests",
        "tier2: Tier 2 boundary, negative, and security-invariant E2E tests",
        "tier3: Tier 3 pairwise cross-feature combination E2E tests",
        "tier4: Tier 4 multi-step real-world application scenario E2E tests",
    ]
    for marker in markers:
        config.addinivalue_line("markers", marker)


@dataclass(frozen=True, slots=True)
class SeededAccount:
    account_id: int
    username: str
    display_name: str
    password: str
    personal_kb_id: int | None
    personal_kb_number: str | None


@dataclass
class ClaireTestEnv:
    tmp_path: Path
    data_dir: Path
    db_path: Path
    auth_db_path: Path
    plugins_dir: Path
    settings: Settings
    app: Any
    client: TestClient
    public_url: str
    admin: SeededAccount
    alice: SeededAccount | None
    bob: SeededAccount | None
    accounts: dict[str, SeededAccount] = field(default_factory=dict)
    session_cookie_name: str = "claire_session"
    oauth_tx_cookie_name: str = "claire_oauth_tx"


def _clear_sensitive_env(monkeypatch: pytest.MonkeyPatch) -> None:
    for key in list(os.environ):
        if key.startswith("CLAIRE_") or key.startswith("TELEGRAM_") or key.startswith("GEMINI_"):
            monkeypatch.delenv(key, raising=False)


def _is_secure_url(public_url: str, environment: str) -> bool:
    parsed = urlparse(public_url)
    host = (parsed.hostname or "").lower()
    return parsed.scheme == "https" or environment == "production" or host.endswith(".test")


@pytest.fixture
def claire_factory(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[Callable[..., ClaireTestEnv]]:
    """Build isolated Claire ASGI instances backed by tmp_path SQLite files and MockProvider."""
    _clear_sensitive_env(monkeypatch)
    monkeypatch.setenv("CLAIRE_SECRET_KEY", DEFAULT_SECRET_KEY)
    reset_token_stores()
    reset_secret_service()
    get_settings.cache_clear()

    stack = ExitStack()
    instance_counter = 0

    def _build(
        *,
        environment: str = "development",
        public_url: str = "http://127.0.0.1:8765",
        fqdn: str = "",
        site_domain: str = "",
        usercontent_fqdn: str = "",
        media_fqdn: str = "",
        multi_theme: bool = True,
        provider: str = "mock",
        inject_token: str = "owner-" + ("o" * 32),
        readonly_token: str = "",
        seed_members: tuple[str, ...] = ("alice", "bob"),
        server_mode: str = "network",
        manual_tokens_retired: bool = False,
        plugins_dir: Path | None = None,
        extra_env: dict[str, str] | None = None,
    ) -> ClaireTestEnv:
        nonlocal instance_counter
        instance_counter += 1
        root_dir = tmp_path / f"instance_{instance_counter}"
        data_dir = root_dir / "data"
        data_dir.mkdir(parents=True, exist_ok=True)
        resolved_plugins_dir = plugins_dir if plugins_dir is not None else (root_dir / "plugins")
        resolved_plugins_dir.mkdir(parents=True, exist_ok=True)

        _clear_sensitive_env(monkeypatch)
        monkeypatch.setenv("CLAIRE_SECRET_KEY", DEFAULT_SECRET_KEY)
        if manual_tokens_retired:
            monkeypatch.setenv("CLAIRE_MANUAL_TOKENS_RETIRED", "1")
        if extra_env:
            for env_key, env_val in extra_env.items():
                monkeypatch.setenv(env_key, env_val)

        reset_token_stores()
        reset_secret_service()
        get_settings.cache_clear()

        db_path = data_dir / "claire.db"
        settings = Settings(
            _env_file=None,
            CLAIRE_DB_PATH=str(db_path),
            CLAIRE_DATA_DIR=str(data_dir),
            CLAIRE_PLUGINS_DIR=str(resolved_plugins_dir),
            CLAIRE_PROVIDER=provider,
            CLAIRE_ENVIRONMENT=environment,
            CLAIRE_PUBLIC_URL=public_url,
            CLAIRE_FQDN=fqdn,
            CLAIRE_SITE_DOMAIN=site_domain,
            CLAIRE_USERCONTENT_FQDN=usercontent_fqdn,
            CLAIRE_MEDIA_FQDN=media_fqdn,
            CLAIRE_MULTI_THEME=multi_theme,
            CLAIRE_INJECT_TOKEN=inject_token,
            CLAIRE_READONLY_TOKEN=readonly_token,
        )

        app = server.create_app(settings)

        admin_pw = "AdminSecret#987!"
        conn = dbm.connect_existing(db_path)
        try:
            acct_store = accounts_store.AccountStore(conn)
            acct_store.complete_setup(1, username="adminuser", display_name="Admin")
            if server_mode and acct_store.server_mode() != server_mode:
                acct_store.set_setting("mode", server_mode, updated_by=1)
            member_records: list[tuple[int, str, str]] = []
            for idx, uname in enumerate(seed_members, start=1):
                pw = f"MemberVault!987#{idx}Pass"
                created = acct_store.create_member(username=uname, display_name=uname.capitalize())
                member_records.append((created.id, uname, pw))
        finally:
            conn.close()

        auth_path = auth_db.auth_db_path(data_dir)
        auth_conn = auth_db.connect_auth_db(auth_path)
        try:
            auth_db.set_password(auth_conn, 1, admin_pw, username="adminuser")
            for acct_id, uname, pw in member_records:
                auth_db.set_password(auth_conn, acct_id, pw, username=uname)
        finally:
            auth_conn.close()

        seeded_accounts: dict[str, SeededAccount] = {}
        if multi_theme:
            kb_mgr = KnowledgeBaseManager(settings)
            admin_kb = kb_mgr.personal_kb_for(1)
            seeded_accounts["adminuser"] = SeededAccount(
                account_id=1,
                username="adminuser",
                display_name="Admin",
                password=admin_pw,
                personal_kb_id=admin_kb.id,
                personal_kb_number=str(admin_kb.to_dict()["number"]),
            )
            for acct_id, uname, pw in member_records:
                member_kb = kb_mgr.define_kb(f"{uname.capitalize()} KB", account_id=acct_id, is_personal=True)
                seeded_accounts[uname] = SeededAccount(
                    account_id=acct_id,
                    username=uname,
                    display_name=uname.capitalize(),
                    password=pw,
                    personal_kb_id=member_kb.id,
                    personal_kb_number=str(member_kb.to_dict()["number"]),
                )
        else:
            seeded_accounts["adminuser"] = SeededAccount(
                account_id=1,
                username="adminuser",
                display_name="Admin",
                password=admin_pw,
                personal_kb_id=0,
                personal_kb_number=None,
            )
            for acct_id, uname, pw in member_records:
                seeded_accounts[uname] = SeededAccount(
                    account_id=acct_id,
                    username=uname,
                    display_name=uname.capitalize(),
                    password=pw,
                    personal_kb_id=None,
                    personal_kb_number=None,
                )

        client = stack.enter_context(TestClient(app, base_url=public_url))
        secure = _is_secure_url(public_url, environment)
        return ClaireTestEnv(
            tmp_path=root_dir,
            data_dir=data_dir,
            db_path=db_path,
            auth_db_path=auth_path,
            plugins_dir=resolved_plugins_dir,
            settings=settings,
            app=app,
            client=client,
            public_url=public_url.rstrip("/"),
            admin=seeded_accounts["adminuser"],
            alice=seeded_accounts.get("alice"),
            bob=seeded_accounts.get("bob"),
            accounts=seeded_accounts,
            session_cookie_name="__Host-claire_session" if secure else "claire_session",
            oauth_tx_cookie_name="__Host-claire_oauth_tx" if secure else "claire_oauth_tx",
        )

    try:
        yield _build
    finally:
        stack.close()
        reset_token_stores()
        reset_secret_service()
        get_settings.cache_clear()


@pytest.fixture
def claire_env(claire_factory: Callable[..., ClaireTestEnv]) -> ClaireTestEnv:
    """Default isolated Claire instance for standard Track C/D E2E tests."""
    return claire_factory()


def pkce_pair() -> tuple[str, str]:
    """Generate an OAuth 2.1 PKCE (code_verifier, S256 code_challenge) pair."""
    verifier = secrets.token_urlsafe(48)
    digest = hashlib.sha256(verifier.encode("ascii")).digest()
    challenge = base64.urlsafe_b64encode(digest).decode("ascii").rstrip("=")
    return verifier, challenge


def same_origin_headers(origin: str) -> dict[str, str]:
    """Return Origin and Sec-Fetch-Site headers required for same-origin state-changing requests."""
    return {"Origin": origin.rstrip("/"), "Sec-Fetch-Site": "same-origin"}


def issue_session_cookie(env: ClaireTestEnv, account_id: int, *, scope: str = "owner", stale: bool = False, set_on_client: bool = True) -> str:
    """Mint a real account session via AccountStore and optionally attach it to env.client cookies."""
    now = (time.time() - 3600.0) if stale else time.time()
    conn = dbm.connect_existing(env.db_path)
    try:
        token = accounts_store.create_account_session(conn, account_id=account_id, scope=scope, now=now)
    finally:
        conn.close()
    if set_on_client:
        from urllib.parse import urlsplit

        env.client.cookies.clear()
        host = urlsplit(env.public_url).hostname or ""
        env.client.cookies.set(env.session_cookie_name, token, domain=host, path="/")
    return token


def register_oauth_client(client: TestClient, *, redirect_uri: str = "http://127.0.0.1:19999/cb", client_name: str = "E2E Client") -> dict[str, Any]:
    """Register an OAuth 2.1 client via POST /oauth/register."""
    resp = client.post("/oauth/register", json={"client_name": client_name, "redirect_uris": [redirect_uri]})
    assert resp.status_code == 201, f"OAuth register failed: {resp.status_code} {resp.text}"
    return resp.json()


def run_oauth_pkce_flow(
    client: TestClient,
    *,
    origin: str,
    username: str,
    password: str,
    client_id: str,
    client_secret: str | None = None,
    redirect_uri: str = "http://127.0.0.1:19999/cb",
    scope: str = "readonly write",
    step_up_password: str | None = None,
    state: str = "e2e-state",
) -> dict[str, Any]:
    """Execute full opaque-box OAuth 2.1 S256 PKCE + /login?tx= + consent + token exchange flow."""
    client.cookies.clear()
    verifier, challenge = pkce_pair()
    auth_query = urlencode(
        {
            "response_type": "code",
            "client_id": client_id,
            "redirect_uri": redirect_uri,
            "state": state,
            "code_challenge": challenge,
            "code_challenge_method": "S256",
            "scope": scope,
        }
    )
    unauth = client.get(f"/oauth/authorize?{auth_query}", follow_redirects=False)
    assert unauth.status_code == 303, f"Expected 303 to /login?tx=, got {unauth.status_code}: {unauth.text}"
    login_loc = unauth.headers["location"]
    assert login_loc.startswith("/login?tx="), f"Unexpected redirect location: {login_loc}"
    tx_id = parse_qs(urlparse(login_loc).query)["tx"][0]

    login_page = client.get(login_loc)
    assert login_page.status_code == 200

    login_post = client.post(
        "/login",
        data={"username": username, "password": password, "tx": tx_id},
        headers=same_origin_headers(origin),
        follow_redirects=False,
    )
    assert login_post.status_code == 303, f"Login failed: {login_post.status_code}: {login_post.text}"
    consent_loc = login_post.headers["location"]
    assert consent_loc == f"/oauth/authorize?tx={tx_id}"

    consent_get = client.get(consent_loc)
    assert consent_get.status_code == 200

    approve_data: dict[str, str] = {"tx": tx_id, "action": "approve"}
    if step_up_password is not None:
        approve_data["step_up_password"] = step_up_password

    approve_post = client.post(
        "/oauth/authorize",
        data=approve_data,
        headers=same_origin_headers(origin),
        follow_redirects=False,
    )
    assert approve_post.status_code == 302, f"Consent approve failed: {approve_post.status_code}: {approve_post.text}"
    callback_loc = approve_post.headers["location"]
    assert callback_loc.startswith(redirect_uri), f"Unexpected callback redirect: {callback_loc}"
    qs = parse_qs(urlparse(callback_loc).query)
    assert qs.get("state") == [state]
    code = qs["code"][0]

    token_data: dict[str, str] = {
        "grant_type": "authorization_code",
        "code": code,
        "client_id": client_id,
        "redirect_uri": redirect_uri,
        "code_verifier": verifier,
    }
    if client_secret is not None:
        token_data["client_secret"] = client_secret
    tok_resp = client.post("/oauth/token", data=token_data)
    assert tok_resp.status_code == 200, f"Token exchange failed: {tok_resp.status_code}: {tok_resp.text}"
    client.cookies.clear()
    return tok_resp.json()


def mcp_tools_list(client: TestClient, token: str, host: str | None = None, *, req_id: int = 1) -> tuple[Any, list[dict[str, Any]]]:
    """Call JSON-RPC 2.0 tools/list over Streamable HTTP POST /mcp."""
    headers = {"Authorization": f"Bearer {token}"}
    if host:
        headers["Host"] = host
    resp = client.post("/mcp", headers=headers, json={"jsonrpc": "2.0", "id": req_id, "method": "tools/list"})
    tools: list[dict[str, Any]] = []
    if resp.status_code == 200:
        body = resp.json()
        if isinstance(body, dict) and "result" in body and isinstance(body["result"], dict):
            tools = list(body["result"].get("tools") or [])
    return resp, tools


def mcp_call(client: TestClient, token: str, tool_name: str, arguments: dict[str, Any] | None = None, host: str | None = None, *, req_id: int = 2) -> tuple[Any, Any]:
    """Call a tool via JSON-RPC 2.0 tools/call over Streamable HTTP POST /mcp and parse its text payload."""
    headers = {"Authorization": f"Bearer {token}"}
    if host:
        headers["Host"] = host
    resp = client.post(
        "/mcp",
        headers=headers,
        json={"jsonrpc": "2.0", "id": req_id, "method": "tools/call", "params": {"name": tool_name, "arguments": arguments or {}}},
    )
    parsed: Any = None
    if resp.status_code == 200:
        body = resp.json()
        result = body.get("result") if isinstance(body, dict) else None
        if isinstance(result, dict) and isinstance(result.get("content"), list) and result["content"]:
            raw_text = result["content"][0].get("text", "")
            try:
                parsed = json.loads(raw_text)
            except Exception:
                parsed = raw_text
        else:
            parsed = body
    return resp, parsed


def seed_personal_document(env: ClaireTestEnv, account: SeededAccount, *, doc_id: str, title: str, url: str, raw_text: str = "Sample raw document body", summary: str = "Sample concise summary") -> None:
    """Seed an extracted document inside an account's personal KB for MCP/HTTP read or contribution tests."""
    kb_id = account.personal_kb_id if account.personal_kb_id is not None else 0
    db_file = env.db_path
    if env.settings.multi_theme and kb_id != 0:
        db_file = KnowledgeBaseManager(env.settings).get_settings_for_kb(kb_id, env.settings).db_file
    conn = dbm.connect_existing(db_file)
    try:
        content_hash = hashlib.sha256(raw_text.encode("utf-8")).hexdigest()
        dbm.insert_document(
            conn,
            Document(
                id=doc_id,
                url=url,
                title=title,
                summary=summary,
                raw_text=raw_text,
                source_type="web",
                content_hash=content_hash,
            ),
            kb_id=kb_id,
        )
        conn.commit()
    finally:
        conn.close()


def run_claire_cli(env: ClaireTestEnv, args: list[str], extra_env: dict[str, str] | None = None) -> subprocess.CompletedProcess[str]:
    """Execute claire CLI in an isolated subprocess bound to env's data_dir and db_path."""
    sub_env = {
        k: v
        for k, v in os.environ.items()
        if not (k.startswith("CLAIRE_") or k.startswith("TELEGRAM_") or k.startswith("GEMINI_"))
    }
    sub_env.update(
        {
            "PYTHONPATH": str(REPO_ROOT / "src"),
            "CLAIRE_DB_PATH": str(env.db_path),
            "CLAIRE_DATA_DIR": str(env.data_dir),
            "CLAIRE_PLUGINS_DIR": str(env.plugins_dir),
            "CLAIRE_ENVIRONMENT": env.settings.environment or "development",
            "CLAIRE_PUBLIC_URL": env.public_url,
            "CLAIRE_PROVIDER": env.settings.provider,
            "CLAIRE_MULTI_THEME": "1" if env.settings.multi_theme else "0",
            "CLAIRE_SECRET_KEY": DEFAULT_SECRET_KEY,
        }
    )
    if extra_env:
        sub_env.update(extra_env)
    venv_python = REPO_ROOT / ".venv" / "bin" / "python"
    python_bin = str(venv_python) if venv_python.exists() else sys.executable
    return subprocess.run(
        [python_bin, "-m", "claire_bible.cli", *args],
        cwd=str(env.tmp_path),
        env=sub_env,
        capture_output=True,
        text=True,
        check=False,
    )
