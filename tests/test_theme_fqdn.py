"""테마 FQDN 검증, 테마별 GA4, 레거시 테마 호스트 리다이렉트(설계 §5.3).

P2부터 테마 FQDN은 호스트로 테마를 서빙하지 않고 새로 쓸 수도 없다(O5). v16 이관이 ``themes.fqdn``을 ``legacy_hosts``로 옮기고, ``HostRouter``는 그 호스트만 정본 테마 경로로 308 리다이렉트한다. 나머지 호스트는 421이다.
"""

from __future__ import annotations

import os
import time
from pathlib import Path
from types import SimpleNamespace

import pytest
from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import JSONResponse
from starlette.routing import Route
from starlette.testclient import TestClient

from claire.api import security
from claire.api.server import create_app
from claire.config import Settings
from claire.store import db as dbm
from claire.store import legacy_hosts as lh
from claire.store.theme import (
    ThemeManager,
    validate_fqdn,
    validate_ga_measurement_id,
)

OWNER_TOKEN = "owner-" + ("o" * 32)
READONLY_TOKEN = "readonly-" + ("r" * 32)
OWNER_HEADERS = {"Authorization": f"Bearer {OWNER_TOKEN}"}
READONLY_HEADERS = {"Authorization": f"Bearer {READONLY_TOKEN}"}
APP_HOST = "127.0.0.1:8765"
APP_ORIGIN = f"http://{APP_HOST}"


@pytest.fixture(autouse=True)
def _isolate_claire_env(monkeypatch):
    for name in list(os.environ):
        if name.startswith("CLAIRE_"):
            monkeypatch.delenv(name, raising=False)


# ---------------------------------------------------------------------------
# 1. 검증기와 테마 FQDN 쓰기 폐지
# ---------------------------------------------------------------------------

def test_validate_fqdn_cases():
    # Empty or None means unassigned FQDN
    assert validate_fqdn("") == ""
    assert validate_fqdn("   ") == ""
    assert validate_fqdn(None) == ""

    # Valid FQDNs
    assert validate_fqdn("ai.example.com") == "ai.example.com"
    assert validate_fqdn("AI.EXAMPLE.COM") == "ai.example.com"  # case normalization
    assert validate_fqdn("finance-123.internal.corp") == "finance-123.internal.corp"
    assert validate_fqdn("sub.domain.co.kr") == "sub.domain.co.kr"

    # Invalid FQDNs
    for bad in (
        "http://ai.example.com",
        "ai.example.com/",
        "ai.example.com:8080",
        "ai..example.com",
        "-ai.example.com",
        "ai-.example.com",
        "ai_example.com",  # underscore not allowed in DNS hostname
        "localhost",  # single label, missing dot
        "ai.example.com.",  # trailing dot
        "a" * 64 + ".example.com",  # label > 63 chars
        ("a" * 50 + ".") * 6 + "com",  # total > 253 chars
    ):
        with pytest.raises(ValueError):
            validate_fqdn(bad)


def test_validate_ga_measurement_id_cases():
    # Empty or None means unassigned GA ID
    assert validate_ga_measurement_id("") == ""
    assert validate_ga_measurement_id("   ") == ""
    assert validate_ga_measurement_id(None) == ""

    # Valid GA IDs
    assert validate_ga_measurement_id("G-1234567890") == "G-1234567890"
    assert validate_ga_measurement_id("G-ABCDEF1234") == "G-ABCDEF1234"
    assert validate_ga_measurement_id("G-9Z8Y7X6W5V") == "G-9Z8Y7X6W5V"
    assert validate_ga_measurement_id("GTM-ABCDEF1") == "GTM-ABCDEF1"

    # Invalid GA IDs
    for bad in (
        "UA-12345-6",
        "G-",
        "G-123",  # too short
        "g-abcdef1234",  # lowercase
        "G-12345_6789",  # underscore
        "MEASUREMENT_ID",
    ):
        with pytest.raises(ValueError):
            validate_ga_measurement_id(bad)


def test_theme_manager_rejects_new_fqdn_but_keeps_ga(tmp_path):
    data_dir = tmp_path / "data"
    data_dir.mkdir(parents=True)
    settings = Settings(
        _env_file=None,
        CLAIRE_DB_PATH=str(data_dir / "claire.db"),
        CLAIRE_VAULT_PATH=str(tmp_path / "vault"),
        CLAIRE_PROVIDER="mock",
        CLAIRE_MULTI_THEME=True,
    )
    tm = ThemeManager(base_settings=settings)
    with pytest.raises(ValueError):
        tm.define_theme("AI 테마", fqdn="ai.example.com")
    t = tm.define_theme("AI 테마", ga_measurement_id="G-AI12345678")
    assert t.ga_measurement_id == "G-AI12345678" and tm.has_any_ga_enabled() is True
    with pytest.raises(ValueError):
        tm.update_theme(t.id, fqdn="ai.example.com")
    tm.update_theme(t.id, fqdn="", ga_measurement_id="")
    assert tm.has_any_ga_enabled() is False


# ---------------------------------------------------------------------------
# 2. Host 판정과 CSP
# ---------------------------------------------------------------------------

async def _inner_test_endpoint(request: Request) -> JSONResponse:
    return JSONResponse({"status": "ok", "host": request.headers.get("host")})


def _make_security_test_settings(tmp_path: Path) -> SimpleNamespace:
    return SimpleNamespace(
        environment="development",
        public_url="http://192.0.2.10:8766",
        cors_allowed_origins="",
        inject_token=OWNER_TOKEN,
        readonly_token=READONLY_TOKEN,
        anonymous_readonly=True,
        db_file=tmp_path / "claire.db",
        effective_ga_measurement_id="",
    )


def _theme_manager(db_file: Path) -> ThemeManager:
    return ThemeManager(
        base_settings=Settings(_env_file=None, CLAIRE_DB_PATH=str(db_file), CLAIRE_MULTI_THEME=True)
    )


def _set_legacy_fqdn(db_file: Path, theme_id: int, fqdn: str) -> None:
    """P2 이전 설치처럼 ``themes.fqdn``을 직접 쓰고, v16 이관이 ``legacy_hosts``로 옮기게 한다."""
    conn = dbm.connect(db_file)
    try:
        conn.execute("DELETE FROM legacy_hosts WHERE theme_id=?", (theme_id,))
        conn.execute("UPDATE themes SET fqdn=?, number=NULL WHERE id=?", (fqdn, theme_id))
        conn.commit()
        dbm.init_db(conn)
        conn.commit()
    finally:
        conn.close()


@pytest.mark.asyncio
async def test_theme_origin_and_host_are_not_trusted_without_legacy_directory(tmp_path):
    s = _make_security_test_settings(tmp_path)
    conn = dbm.connect(s.db_file)
    dbm.init_db(conn)
    conn.close()

    inner_app = Starlette(routes=[Route("/health", _inner_test_endpoint)])
    tm = _theme_manager(s.db_file)
    theme = tm.define_theme("AI 테마")
    _set_legacy_fqdn(s.db_file, theme.id, "ai.example.com")
    tm.reload()
    assert tm.has_registered_fqdn("ai.example.com") is True

    app = security.wrap_web_app(inner_app, s, theme_manager=tm)
    client = TestClient(app, base_url="http://192.0.2.10:8766")
    assert client.get("/health").status_code == 200
    # 디렉터리를 주지 않은 래퍼에서는 테마 FQDN도 미등록 호스트다(421).
    for host in ("ai.example.com", "ai.example.com:8766"):
        assert client.get("/health", headers={"Host": host}).status_code == 421
    # CORSPolicyMiddleware의 테마 origin 허용은 없앴다.
    assert client.get("/health", headers={"Origin": "http://ai.example.com"}).status_code == 403


@pytest.mark.asyncio
async def test_dynamic_csp_headers_on_theme_ga(tmp_path):
    s = _make_security_test_settings(tmp_path)
    conn = dbm.connect(s.db_file)
    dbm.init_db(conn)
    conn.close()

    inner_app = Starlette(routes=[Route("/health", _inner_test_endpoint)])
    tm = _theme_manager(s.db_file)
    app = security.wrap_web_app(inner_app, s, theme_manager=tm)
    client = TestClient(app, base_url="http://192.0.2.10:8766")

    csp = client.get("/health").headers.get("content-security-policy", "")
    assert "googletagmanager.com" not in csp
    assert "google-analytics.com" not in csp

    t = tm.define_theme("GA 테마", ga_measurement_id="G-DYNAMICTEST1")
    csp = client.get("/health").headers.get("content-security-policy", "")
    assert "https://*.googletagmanager.com" in csp
    assert "https://*.google-analytics.com" in csp

    tm.update_theme(t.id, ga_measurement_id="")
    csp = client.get("/health").headers.get("content-security-policy", "")
    assert "googletagmanager.com" not in csp
    assert "google-analytics.com" not in csp


# ---------------------------------------------------------------------------
# 3. create_app: 레거시 테마 호스트 리다이렉트
# ---------------------------------------------------------------------------

@pytest.fixture
def multi_theme_server_env(tmp_path):
    data_dir = tmp_path / "data"
    vault_dir = tmp_path / "vault"
    data_dir.mkdir(parents=True)
    vault_dir.mkdir(parents=True)

    settings = Settings(
        _env_file=None,
        CLAIRE_DB_PATH=str(data_dir / "claire.db"),
        CLAIRE_VAULT_PATH=str(vault_dir),
        CLAIRE_PROVIDER="mock",
        CLAIRE_ENVIRONMENT="development",
        CLAIRE_PUBLIC_URL=APP_ORIGIN,
        CLAIRE_INJECT_TOKEN=OWNER_TOKEN,
        CLAIRE_READONLY_TOKEN=READONLY_TOKEN,
        CLAIRE_ANONYMOUS_READONLY=True,
        CLAIRE_MULTI_THEME=True,
    )
    app = create_app(settings=settings)
    client = TestClient(app, base_url=settings.public_url)
    return client, settings, app


def _create_theme(client: TestClient, label: str, **extra) -> tuple[int, str]:
    r = client.post("/themes", json={"label": label, "is_public": True, **extra}, headers=OWNER_HEADERS)
    assert r.status_code == 201, r.text
    theme = r.json()["theme"]
    return theme["id"], theme["number"]


def _theme_number(settings: Settings, theme_id: int) -> int:
    conn = dbm.connect(settings.db_file)
    try:
        return int(conn.execute("SELECT number FROM themes WHERE id=?", (theme_id,)).fetchone()[0])
    finally:
        conn.close()


def test_migrated_theme_fqdn_redirects_to_canonical_theme_path(multi_theme_server_env):
    client, settings, _ = multi_theme_server_env
    theme_id, _ = _create_theme(client, "AI 전용")
    _set_legacy_fqdn(settings.db_file, theme_id, " AI.Example.com ")
    number = _theme_number(settings, theme_id)

    noisy = {"Cookie": "claire_session=" + "s" * 43, "Origin": "http://ai.example.com"}
    resp = client.get(
        "/documents?theme=2&q=x", headers={"Host": "ai.example.com", **noisy}, follow_redirects=False
    )
    assert resp.status_code == 308
    assert resp.headers["location"] == f"{APP_ORIGIN}/themes/{number}/documents?theme=2&q=x"
    assert "set-cookie" not in resp.headers
    assert "access-control-allow-origin" not in resp.headers

    root = client.head("/", headers={"Host": "AI.example.com:8765"}, follow_redirects=False)
    assert root.status_code == 308
    assert root.headers["location"] == f"{APP_ORIGIN}/themes/{number}/"

    # 쓰기 메서드는 리다이렉트하지 않는다.
    assert client.post("/search", json={"q": "x"}, headers={"Host": "ai.example.com"}).status_code == 421
    assert (
        client.post("/themes", json={"label": "x"}, headers={"Host": "ai.example.com", **OWNER_HEADERS}).status_code
        == 421
    )
    # 정적 자산도 레거시 호스트에서 직접 열리지 않고 정본 경로로 간다.
    static = client.get("/static/js/app.js", headers={"Host": "ai.example.com"}, follow_redirects=False)
    assert static.status_code == 308
    assert static.headers["location"] == f"{APP_ORIGIN}/themes/{number}/static/js/app.js"


def test_legacy_host_sunset_and_deleted_theme(multi_theme_server_env):
    client, settings, _ = multi_theme_server_env
    theme_id, _ = _create_theme(client, "일몰 테마")
    _set_legacy_fqdn(settings.db_file, theme_id, "old.example.com")

    def status(host: str = "old.example.com") -> int:
        return client.get("/", headers={"Host": host}, follow_redirects=False).status_code

    assert status() == 308  # 활성화 전: 무기한 308
    conn = dbm.connect(settings.db_file)
    try:
        lh.activate_sunset_clock(conn, now=time.time() - 31 * 86400)
        assert status() == 410  # 기본 30일이 지났다
        lh.write_legacy_host_policy(conn, lh.LegacyHostPolicy(after_sunset_status=421), updated_by=1)
        assert status() == 421
        lh.extend_sunset(conn, "old.example.com", days=30)
        assert status() == 308
        lh.keep_indefinitely(conn, "old.example.com")
        assert status() == 308
    finally:
        conn.close()

    # 테마를 지우면 legacy_hosts 행도 사라져 421이다.
    assert client.request("DELETE", "/themes", json={"id": theme_id}, headers=OWNER_HEADERS).status_code == 200
    assert status() == 421


def test_unregistered_hosts_are_misdirected_for_everyone(multi_theme_server_env):
    client, _, _ = multi_theme_server_env
    _create_theme(client, "기밀 테마", is_public=False)
    for host in ("ai.example.com", "secret.example.com"):
        for path in ("/", "/documents", "/themes"):
            anon = client.get(path, headers={"Host": host})
            assert anon.status_code == 421, (host, path)
            assert "기밀 테마" not in anon.text
            assert client.get(path, headers={"Host": host, **OWNER_HEADERS}).status_code == 421


def test_theme_api_rejects_fqdn_and_keeps_ga_lifecycle(multi_theme_server_env):
    """POST·PATCH /themes: FQDN 입력은 폐지됐고(O5) GA4 설정은 그대로다."""
    client, _, _ = multi_theme_server_env

    for fqdn in ("research.example.com", "http://invalid-fqdn/"):
        assert client.post("/themes", json={"label": "FQDN", "fqdn": fqdn}, headers=OWNER_HEADERS).status_code == 400

    r_bad_ga = client.post(
        "/themes",
        json={"label": "잘못된 GA", "ga_measurement_id": "UA-12345"},
        headers=OWNER_HEADERS,
    )
    assert r_bad_ga.status_code == 400
    assert "유효하지 않은 Google Analytics 측정 ID 형식입니다" in r_bad_ga.json().get("error", "")

    theme_id, _ = _create_theme(client, "신규 연구", ga_measurement_id="G-RESEARCH01")
    r_patch_fqdn = client.patch(
        "/themes", json={"id": theme_id, "fqdn": "new-research.example.com"}, headers=OWNER_HEADERS
    )
    assert r_patch_fqdn.status_code == 400

    r_patch = client.patch(
        "/themes", json={"id": theme_id, "ga_measurement_id": "G-RESEARCH02"}, headers=OWNER_HEADERS
    )
    assert r_patch.status_code == 200
    assert r_patch.json()["theme"]["ga_measurement_id"] == "G-RESEARCH02"

    r_clear = client.patch("/themes", json={"id": theme_id, "ga_measurement_id": ""}, headers=OWNER_HEADERS)
    assert r_clear.status_code == 200
    assert not r_clear.json()["theme"].get("ga_measurement_id")
