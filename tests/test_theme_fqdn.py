"""Comprehensive test suite for Theme FQDN reverse proxy and per-theme GA4 tracking."""

from __future__ import annotations

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
from claire.store.theme import (
    ThemeInfo,
    ThemeManager,
    validate_fqdn,
    validate_ga_measurement_id,
)

OWNER_TOKEN = "owner-" + ("o" * 32)
READONLY_TOKEN = "readonly-" + ("r" * 32)
OWNER_HEADERS = {"Authorization": f"Bearer {OWNER_TOKEN}"}
READONLY_HEADERS = {"Authorization": f"Bearer {READONLY_TOKEN}"}


# ---------------------------------------------------------------------------
# 1. Validation & ThemeManager Tests
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


def test_theme_manager_fqdn_lifecycle_and_collisions(tmp_path):
    data_dir = tmp_path / "data"
    vault_dir = tmp_path / "vault"
    data_dir.mkdir(parents=True)
    vault_dir.mkdir(parents=True)

    settings = Settings(
        CLAIRE_DB_PATH=str(data_dir / "claire.db"),
        CLAIRE_VAULT_PATH=str(vault_dir),
        CLAIRE_PROVIDER="mock",
        CLAIRE_MULTI_THEME=True,
        CLAIRE_FQDN="primary.example.com",
    )
    tm = ThemeManager(base_settings=settings)

    # 1. Register theme with FQDN and GA ID
    t1 = tm.define_theme(
        "AI 테마",
        fqdn="ai.example.com",
        ga_measurement_id="G-AI12345678",
    )
    assert t1.fqdn == "ai.example.com"
    assert t1.ga_measurement_id == "G-AI12345678"
    assert tm.has_registered_fqdn("ai.example.com") is True
    assert tm.get_theme_by_fqdn("ai.example.com").id == t1.id
    assert tm.has_any_ga_enabled() is True

    # 2. Collision with existing theme FQDN
    with pytest.raises(ValueError, match="이미 다른 테마"):
        tm.define_theme("금융 테마", fqdn="ai.example.com")

    # 3. Collision with system effective_fqdn
    with pytest.raises(ValueError, match="기본 서비스 도메인"):
        tm.define_theme("메인 테마", fqdn="primary.example.com")

    # 4. Define second theme
    t2 = tm.define_theme("금융 테마", fqdn="finance.example.com")
    assert tm.has_registered_fqdn("finance.example.com") is True
    assert tm.get_theme_by_fqdn("finance.example.com").id == t2.id

    # 5. Update theme 2 with collision
    with pytest.raises(ValueError, match="이미 다른 테마"):
        tm.update_theme(t2.id, fqdn="ai.example.com")

    with pytest.raises(ValueError, match="기본 서비스 도메인"):
        tm.update_theme(t2.id, fqdn="primary.example.com")

    # 6. Update theme 1 to change FQDN
    tm.update_theme(t1.id, fqdn="new-ai.example.com")
    assert tm.has_registered_fqdn("ai.example.com") is False
    assert tm.has_registered_fqdn("new-ai.example.com") is True

    # 7. Delete theme unregisters FQDN
    tm.delete_theme(t2.id)
    assert tm.has_registered_fqdn("finance.example.com") is False
    assert tm.get_theme_by_fqdn("finance.example.com") is None

    # 8. Clear FQDN on theme 1
    tm.update_theme(t1.id, fqdn="", ga_measurement_id="")
    assert tm.has_registered_fqdn("new-ai.example.com") is False
    assert tm.has_any_ga_enabled() is False


# ---------------------------------------------------------------------------
# 2. HostAuthorityMiddleware & Dynamic CSP Tests
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


@pytest.mark.asyncio
async def test_host_authority_dynamic_theme_fqdn(tmp_path):
    s = _make_security_test_settings(tmp_path)
    conn = dbm.connect(s.db_file)
    dbm.init_db(conn)
    conn.close()

    inner_app = Starlette(routes=[Route("/health", _inner_test_endpoint)])
    tm = ThemeManager(base_settings=Settings(CLAIRE_DB_PATH=str(s.db_file), CLAIRE_MULTI_THEME=True))
    app = security.wrap_web_app(inner_app, s, theme_manager=tm)

    # Primary host is allowed
    client = TestClient(app, base_url="http://192.0.2.10:8766")
    resp = client.get("/health")
    assert resp.status_code == 200

    # Unregistered host returns 421 Misdirected Request
    resp = client.get("/health", headers={"Host": "ai.example.com"})
    assert resp.status_code == 421

    # Register theme with FQDN
    t = tm.define_theme("AI 테마", fqdn="ai.example.com")
    assert tm.has_registered_fqdn("ai.example.com") is True

    # Now registered host is dynamically allowed without server restart!
    resp = client.get("/health", headers={"Host": "ai.example.com"})
    assert resp.status_code == 200

    # Port stripping test: ai.example.com:8766
    resp = client.get("/health", headers={"Host": "ai.example.com:8766"})
    assert resp.status_code == 200

    # Delete theme -> immediately 421 again
    tm.delete_theme(t.id)
    resp = client.get("/health", headers={"Host": "ai.example.com"})
    assert resp.status_code == 421


@pytest.mark.asyncio
async def test_dynamic_csp_headers_on_theme_ga(tmp_path):
    s = _make_security_test_settings(tmp_path)
    conn = dbm.connect(s.db_file)
    dbm.init_db(conn)
    conn.close()

    inner_app = Starlette(routes=[Route("/health", _inner_test_endpoint)])
    tm = ThemeManager(base_settings=Settings(CLAIRE_DB_PATH=str(s.db_file), CLAIRE_MULTI_THEME=True))
    app = security.wrap_web_app(inner_app, s, theme_manager=tm)
    client = TestClient(app, base_url="http://192.0.2.10:8766")

    # Initial CSP: no Google Analytics domains
    resp = client.get("/health")
    csp = resp.headers.get("content-security-policy", "")
    assert "googletagmanager.com" not in csp
    assert "google-analytics.com" not in csp

    # Register theme with GA ID
    t = tm.define_theme("GA 테마", ga_measurement_id="G-DYNAMICTEST1")
    assert tm.has_any_ga_enabled() is True

    # Next request dynamically includes GA in CSP!
    resp = client.get("/health")
    csp = resp.headers.get("content-security-policy", "")
    assert "https://*.googletagmanager.com" in csp
    assert "https://*.google-analytics.com" in csp

    # Remove GA ID
    tm.update_theme(t.id, ga_measurement_id="")
    assert tm.has_any_ga_enabled() is False

    # Next request immediately removes GA from CSP!
    resp = client.get("/health")
    csp = resp.headers.get("content-security-policy", "")
    assert "googletagmanager.com" not in csp
    assert "google-analytics.com" not in csp


# ---------------------------------------------------------------------------
# 3. Server Route Host Resolution, Domain Pinning, & Private Theme Stealth Tests
# ---------------------------------------------------------------------------

@pytest.fixture
def multi_theme_server_env(tmp_path):
    data_dir = tmp_path / "data"
    vault_dir = tmp_path / "vault"
    data_dir.mkdir(parents=True)
    vault_dir.mkdir(parents=True)

    settings = Settings(
        CLAIRE_DB_PATH=str(data_dir / "claire.db"),
        CLAIRE_VAULT_PATH=str(vault_dir),
        CLAIRE_PROVIDER="mock",
        CLAIRE_ENVIRONMENT="development",
        CLAIRE_PUBLIC_URL="http://127.0.0.1:8765",
        CLAIRE_INJECT_TOKEN=OWNER_TOKEN,
        CLAIRE_READONLY_TOKEN=READONLY_TOKEN,
        CLAIRE_ANONYMOUS_READONLY=True,
        CLAIRE_MULTI_THEME=True,
    )
    app = create_app(settings=settings)
    client = TestClient(app, base_url=settings.public_url)
    return client, settings, app


def test_theme_routing_and_domain_pinning(multi_theme_server_env):
    client, settings, _ = multi_theme_server_env

    # 1. Create Public Theme 1 with FQDN
    r1 = client.post(
        "/themes",
        json={"label": "AI 전용", "fqdn": "ai.example.com", "is_public": True},
        headers=OWNER_HEADERS,
    )
    assert r1.status_code == 201
    t1_id = r1.json()["theme"]["id"]

    # 2. Create Public Theme 2 with FQDN
    r2 = client.post(
        "/themes",
        json={"label": "금융 전용", "fqdn": "finance.example.com", "is_public": True},
        headers=OWNER_HEADERS,
    )
    assert r2.status_code == 201
    t2_id = r2.json()["theme"]["id"]

    # 3. Access via Host: ai.example.com -> routes to Theme 1
    resp_ai = client.get("/documents", headers={"Host": "ai.example.com"})
    assert resp_ai.status_code == 200

    # 4. Domain Pinning Invariant:
    # When requesting via Host: ai.example.com, even if query param ?theme=2 is provided,
    # the request remains pinned to Theme 1 (ai.example.com).
    resp_ai_pinned = client.get(f"/documents?theme={t2_id}", headers={"Host": "ai.example.com"})
    assert resp_ai_pinned.status_code == 200

    # 5. On the primary domain, ?theme= query parameter is fully respected
    resp_primary_t2 = client.get(f"/documents?theme={t2_id}", headers={"Host": "127.0.0.1:8765"})
    assert resp_primary_t2.status_code == 200


def test_private_theme_fqdn_fail_closed_stealth(multi_theme_server_env):
    """비공개 테마(is_public=False)에 FQDN이 연결되었을 때 익명 접근 시 404 스텔스 검증."""
    client, settings, _ = multi_theme_server_env

    # Create Private Theme with FQDN
    r = client.post(
        "/themes",
        json={"label": "기밀 테마", "fqdn": "secret.example.com", "is_public": False},
        headers=OWNER_HEADERS,
    )
    assert r.status_code == 201
    secret_theme = r.json()["theme"]
    assert secret_theme["is_public"] is False

    # 1. Anonymous visitor accessing root / via Host: secret.example.com -> HTTP 404 (Fail-closed)
    resp_root = client.get("/", headers={"Host": "secret.example.com"})
    assert resp_root.status_code == 404
    # Ensure no title/label leakage
    assert "기밀 테마" not in resp_root.text

    # 2. Anonymous visitor accessing /documents via Host: secret.example.com -> HTTP 404
    resp_docs = client.get("/documents", headers={"Host": "secret.example.com"})
    assert resp_docs.status_code == 404

    # 3. Authenticated owner accessing via Host: secret.example.com -> HTTP 200
    resp_owner = client.get("/documents", headers={"Host": "secret.example.com", **OWNER_HEADERS})
    assert resp_owner.status_code == 200


def test_theme_api_endpoints_fqdn_and_ga_lifecycle(multi_theme_server_env):
    """POST /themes 및 PATCH /themes 에서 FQDN 및 GA4 설정 생성, 갱신, 검증."""
    client, _, _ = multi_theme_server_env

    # 1. POST /themes with invalid FQDN -> 400
    r_bad_fqdn = client.post(
        "/themes",
        json={"label": "잘못된 FQDN", "fqdn": "http://invalid-fqdn/"},
        headers=OWNER_HEADERS,
    )
    assert r_bad_fqdn.status_code == 400
    assert "유효하지 않은 FQDN 형식입니다" in r_bad_fqdn.json().get("error", "")

    # 2. POST /themes with invalid GA ID -> 400
    r_bad_ga = client.post(
        "/themes",
        json={"label": "잘못된 GA", "ga_measurement_id": "UA-12345"},
        headers=OWNER_HEADERS,
    )
    assert r_bad_ga.status_code == 400
    assert "유효하지 않은 Google Analytics 측정 ID 형식입니다" in r_bad_ga.json().get("error", "")

    # 3. POST /themes valid
    r_ok = client.post(
        "/themes",
        json={
            "label": "신규 연구",
            "fqdn": "research.example.com",
            "ga_measurement_id": "G-RESEARCH01",
        },
        headers=OWNER_HEADERS,
    )
    assert r_ok.status_code == 201
    created = r_ok.json()["theme"]
    theme_id = created["id"]
    assert created["fqdn"] == "research.example.com"
    assert created["ga_measurement_id"] == "G-RESEARCH01"

    # 4. Duplicate FQDN on POST -> 400
    r_dup = client.post(
        "/themes",
        json={"label": "중복 도메인", "fqdn": "research.example.com"},
        headers=OWNER_HEADERS,
    )
    assert r_dup.status_code == 400
    assert "이미 다른 테마" in r_dup.json().get("error", "")

    # 5. PATCH /themes update FQDN and GA ID
    r_patch = client.patch(
        "/themes",
        json={
            "id": theme_id,
            "fqdn": "new-research.example.com",
            "ga_measurement_id": "G-RESEARCH02",
        },
        headers=OWNER_HEADERS,
    )
    assert r_patch.status_code == 200
    updated = r_patch.json()["theme"]
    assert updated["fqdn"] == "new-research.example.com"
    assert updated["ga_measurement_id"] == "G-RESEARCH02"

    # Old FQDN is no longer valid
    resp_old = client.get("/documents", headers={"Host": "research.example.com"})
    assert resp_old.status_code == 421

    # New FQDN works
    resp_new = client.get("/documents", headers={"Host": "new-research.example.com"})
    assert resp_new.status_code == 200

    # 6. PATCH /themes clear FQDN and GA ID
    r_clear = client.patch(
        "/themes",
        json={"id": theme_id, "fqdn": "", "ga_measurement_id": ""},
        headers=OWNER_HEADERS,
    )
    assert r_clear.status_code == 200
    cleared = r_clear.json()["theme"]
    assert not cleared.get("fqdn")
    assert not cleared.get("ga_measurement_id")

    # New FQDN is now cleared and returns 421
    resp_cleared = client.get("/documents", headers={"Host": "new-research.example.com"})
    assert resp_cleared.status_code == 421


def test_graph_ui_renders_theme_specific_ga_tag(multi_theme_server_env):
    """FQDN으로 접속 시 테마에 설정된 GA4 태그 및 커스텀 차원 주입 검증."""
    client, _, _ = multi_theme_server_env

    # Create Public Theme with GA ID and FQDN
    r = client.post(
        "/themes",
        json={
            "label": "빅데이터 분석",
            "fqdn": "bigdata.example.com",
            "ga_measurement_id": "G-BIGDATA123",
            "is_public": True,
        },
        headers=OWNER_HEADERS,
    )
    assert r.status_code == 201
    theme_id = r.json()["theme"]["id"]

    # Access / with Host: bigdata.example.com
    resp = client.get("/", headers={"Host": "bigdata.example.com"})
    assert resp.status_code == 200
    html = resp.text

    # Verify GA script tag and config
    assert "googletagmanager.com/gtag/js?id=G-BIGDATA123" in html
    assert 'gtag("config", "G-BIGDATA123"' in html
    assert f"theme_id: {theme_id}" in html
    assert 'theme_label: "빅데이터 분석"' in html
