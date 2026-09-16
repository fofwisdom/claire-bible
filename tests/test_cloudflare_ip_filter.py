"""Comprehensive unit tests for Cloudflare IP filtering middleware and FQDN configuration migration."""

from __future__ import annotations

import ipaddress
from pathlib import Path
from types import SimpleNamespace
import pytest
from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import PlainTextResponse
from starlette.routing import Route
from starlette.testclient import TestClient

from claire.api import security
from claire.api.security import (
    CLOUDFLARE_IPV4_CIDRS,
    CLOUDFLARE_IPV6_CIDRS,
    CloudflareIPFilterMiddleware,
    is_cloudflare_ip,
    wrap_web_app,
)
from claire.config import Settings
from claire.store import db as dbm

OWNER_TOKEN = "owner-" + ("o" * 32)
READONLY_TOKEN = "readonly-" + ("r" * 32)


# ---------------------------------------------------------------------------
# 1. Cloudflare IP detection tests
# ---------------------------------------------------------------------------


def test_cloudflare_ip_detection():
    # Cloudflare IPv4
    assert is_cloudflare_ip(ipaddress.ip_address("173.245.48.1")) is True
    assert is_cloudflare_ip(ipaddress.ip_address("104.16.0.1")) is True
    assert is_cloudflare_ip(ipaddress.ip_address("104.24.10.20")) is True
    assert is_cloudflare_ip(ipaddress.ip_address("172.64.0.100")) is True
    assert is_cloudflare_ip(ipaddress.ip_address("131.0.72.1")) is True

    # Cloudflare IPv6
    assert is_cloudflare_ip(ipaddress.ip_address("2400:cb00::1")) is True
    assert is_cloudflare_ip(ipaddress.ip_address("2606:4700::1")) is True
    assert is_cloudflare_ip(ipaddress.ip_address("2a06:98c0::1")) is True

    # Non-Cloudflare Public IPs
    assert is_cloudflare_ip(ipaddress.ip_address("8.8.8.8")) is False
    assert is_cloudflare_ip(ipaddress.ip_address("93.184.216.34")) is False
    assert is_cloudflare_ip(ipaddress.ip_address("203.0.113.195")) is False
    assert is_cloudflare_ip(ipaddress.ip_address("2001:4860:4860::8888")) is False

    # Private and loopback IPs are not in Cloudflare ranges
    assert is_cloudflare_ip(ipaddress.ip_address("127.0.0.1")) is False
    assert is_cloudflare_ip(ipaddress.ip_address("192.168.1.1")) is False
    assert is_cloudflare_ip(ipaddress.ip_address("10.0.0.1")) is False
    assert is_cloudflare_ip(ipaddress.ip_address("::1")) is False


# ---------------------------------------------------------------------------
# 2. CloudflareIPFilterMiddleware tests
# ---------------------------------------------------------------------------


def _create_test_app():
    async def ping(_request: Request) -> PlainTextResponse:
        return PlainTextResponse("pong")

    return Starlette(routes=[Route("/ping", ping)])


def test_cloudflare_filter_middleware_blocks_direct_public_ips():
    inner = _create_test_app()
    app = CloudflareIPFilterMiddleware(inner)

    # 1. Non-Cloudflare public IP is blocked with 403
    client_non_cf = TestClient(app, client=("93.184.216.34", 12345))
    resp = client_non_cf.get("/ping")
    assert resp.status_code == 403
    assert "Direct public IP access is not allowed" in resp.text

    # 2. Another non-Cloudflare public IP
    client_google = TestClient(app, client=("8.8.8.8", 54321))
    resp = client_google.get("/ping")
    assert resp.status_code == 403

    # 3. Cloudflare IPv4 is allowed
    client_cf_v4 = TestClient(app, client=("104.16.1.1", 12345))
    resp = client_cf_v4.get("/ping")
    assert resp.status_code == 200
    assert resp.text == "pong"

    # 4. Cloudflare IPv6 is allowed
    client_cf_v6 = TestClient(app, client=("2400:cb00::1", 12345))
    resp = client_cf_v6.get("/ping")
    assert resp.status_code == 200
    assert resp.text == "pong"

    # 5. Private IPv4 (LAN, Docker bridge, etc.) is allowed
    for priv_ip in ("192.168.1.100", "10.0.0.5", "172.17.0.2"):
        client_priv = TestClient(app, client=(priv_ip, 12345))
        resp = client_priv.get("/ping")
        assert resp.status_code == 200
        assert resp.text == "pong"

    # 6. Loopback (127.0.0.1, ::1) is allowed
    client_loopback = TestClient(app, client=("127.0.0.1", 12345))
    resp = client_loopback.get("/ping")
    assert resp.status_code == 200

    client_loopback_v6 = TestClient(app, client=("::1", 12345))
    resp = client_loopback_v6.get("/ping")
    assert resp.status_code == 200

    # 7. Non-IP client identifier (e.g. testclient) is allowed
    client_default = TestClient(app, client=("testclient", 50000))
    resp = client_default.get("/ping")
    assert resp.status_code == 200


def test_wrap_web_app_cloudflare_ips_only_integration(tmp_path):
    data_dir = tmp_path / "data"
    vault_dir = tmp_path / "vault"
    data_dir.mkdir(parents=True)
    vault_dir.mkdir(parents=True)

    # 1. Enabled (cloudflare_ips_only=True)
    settings_enabled = Settings(
        CLAIRE_DB_PATH=str(data_dir / "claire.db"),
        CLAIRE_VAULT_PATH=str(vault_dir),
        CLAIRE_PROVIDER="mock",
        CLAIRE_ENVIRONMENT="production",
        CLAIRE_FQDN="claire.example.com",
        CLAIRE_INJECT_TOKEN=OWNER_TOKEN,
        CLAIRE_CLOUDFLARE_IPS_ONLY=True,
    )
    conn = dbm.connect(settings_enabled.db_file)
    dbm.init_db(conn)
    conn.close()

    inner = _create_test_app()
    app_enabled = wrap_web_app(inner, settings_enabled)

    # Public attacker direct connection -> 403
    client_blocked = TestClient(
        app_enabled,
        base_url="https://claire.example.com",
        client=("93.184.216.34", 12345),
    )
    resp = client_blocked.get("/ping", headers={"Host": "claire.example.com"})
    assert resp.status_code == 403
    assert resp.headers.get("x-request-id") is not None

    # Cloudflare connection -> 404 (ping is not registered in ROUTE_POLICY, but passed through middleware)
    client_cf = TestClient(
        app_enabled,
        base_url="https://claire.example.com",
        client=("104.16.1.1", 12345),
    )
    resp = client_cf.get("/ping", headers={"Host": "claire.example.com"})
    assert resp.status_code == 404  # Passes IP check and host check, reaches route policy

    # 2. Disabled (cloudflare_ips_only=False)
    settings_disabled = Settings(
        CLAIRE_DB_PATH=str(data_dir / "claire.db"),
        CLAIRE_VAULT_PATH=str(vault_dir),
        CLAIRE_PROVIDER="mock",
        CLAIRE_ENVIRONMENT="production",
        CLAIRE_FQDN="claire.example.com",
        CLAIRE_INJECT_TOKEN=OWNER_TOKEN,
        CLAIRE_CLOUDFLARE_IPS_ONLY=False,
    )
    app_disabled = wrap_web_app(inner, settings_disabled)
    client_allowed = TestClient(
        app_disabled,
        base_url="https://claire.example.com",
        client=("93.184.216.34", 12345),
    )
    resp = client_allowed.get("/ping", headers={"Host": "claire.example.com"})
    assert resp.status_code == 404  # Not blocked by IP filter


# ---------------------------------------------------------------------------
# 3. Settings FQDN & legacy public_url migration tests
# ---------------------------------------------------------------------------


def test_settings_fqdn_and_legacy_migration(tmp_path):
    data_dir = tmp_path / "data"
    vault_dir = tmp_path / "vault"
    data_dir.mkdir(parents=True)
    vault_dir.mkdir(parents=True)

    # 1. Primary CLAIRE_FQDN in production
    s1 = Settings(
        CLAIRE_DB_PATH=str(data_dir / "claire.db"),
        CLAIRE_VAULT_PATH=str(vault_dir),
        CLAIRE_PROVIDER="mock",
        CLAIRE_ENVIRONMENT="production",
        CLAIRE_FQDN="claire.example.com",
    )
    assert s1.fqdn == "claire.example.com"
    assert s1.effective_fqdn == "claire.example.com"
    assert s1.public_url == "https://claire.example.com/"

    # 2. Primary CLAIRE_FQDN in development
    s2 = Settings(
        CLAIRE_DB_PATH=str(data_dir / "claire.db"),
        CLAIRE_VAULT_PATH=str(vault_dir),
        CLAIRE_PROVIDER="mock",
        CLAIRE_ENVIRONMENT="development",
        CLAIRE_FQDN="127.0.0.1:8766",
    )
    assert s2.effective_fqdn == "127.0.0.1:8766"
    assert s2.public_url == "http://127.0.0.1:8766/"

    # 3. Legacy CLAIRE_PUBLIC_URL auto-migration
    s3 = Settings(
        CLAIRE_DB_PATH=str(data_dir / "claire.db"),
        CLAIRE_VAULT_PATH=str(vault_dir),
        CLAIRE_PROVIDER="mock",
        CLAIRE_ENVIRONMENT="production",
        CLAIRE_PUBLIC_URL="https://migrated.example.com/",
    )
    assert s3.effective_fqdn == "migrated.example.com"
    assert s3.public_url == "https://migrated.example.com/"

    # 4. Cloudflare ips only validator boolean parsing
    s4 = Settings(
        CLAIRE_DB_PATH=str(data_dir / "claire.db"),
        CLAIRE_VAULT_PATH=str(vault_dir),
        CLAIRE_PROVIDER="mock",
        CLAIRE_CLOUDFLARE_IPS_ONLY="1",
    )
    assert s4.cloudflare_ips_only is True

    s5 = Settings(
        CLAIRE_DB_PATH=str(data_dir / "claire.db"),
        CLAIRE_VAULT_PATH=str(vault_dir),
        CLAIRE_PROVIDER="mock",
        CLAIRE_CLOUDFLARE_IPS_ONLY="0",
    )
    assert s5.cloudflare_ips_only is False

    with pytest.raises(ValueError, match="CLAIRE_CLOUDFLARE_IPS_ONLY"):
        Settings(
            CLAIRE_DB_PATH=str(data_dir / "claire.db"),
            CLAIRE_VAULT_PATH=str(vault_dir),
            CLAIRE_PROVIDER="mock",
            CLAIRE_CLOUDFLARE_IPS_ONLY="invalid_boolean",
        )
