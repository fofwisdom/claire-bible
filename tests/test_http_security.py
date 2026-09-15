"""SSRF 방지 및 온프레미스 사설망 접근 제어 단위 테스트."""

from __future__ import annotations

import socket
import pytest

from claire.ingest.fetchers.base import FetchError
from claire.ingest.fetchers.http import (
    SafeHttpClient,
    check_ip_safety,
    is_safe_ip,
)


def test_public_ip_is_allowed_by_default():
    # 공인 IP: Cloudflare DNS (1.1.1.1), Google DNS (8.8.8.8)
    safe, reason = check_ip_safety("1.1.1.1", allow_private=False)
    assert safe is True
    assert reason is None

    safe, reason = check_ip_safety("8.8.8.8", allow_private=False)
    assert safe is True
    assert is_safe_ip("8.8.8.8") is True


def test_private_ips_blocked_by_default():
    # 기본 모드: 사설 IP 대역 차단
    for private_ip in ("10.0.0.1", "172.16.5.10", "192.168.1.1", "127.0.0.1", "100.64.0.1"):
        safe, reason = check_ip_safety(private_ip, allow_private=False)
        assert safe is False
        assert "blocked" in reason
        assert is_safe_ip(private_ip) is False


def test_private_ips_allowed_in_onpremise_mode():
    # 온프레미스 모드 (allow_private=True): 사설 IP 수집 허용
    for private_ip in ("10.10.20.5", "172.20.1.50", "192.168.0.100", "127.0.0.1", "100.64.1.2"):
        safe, reason = check_ip_safety(private_ip, allow_private=True)
        assert safe is True
        assert reason is None


def test_cloud_metadata_imds_strictly_blocked_even_in_onpremise_mode():
    # AWS/GCP/Azure/OpenStack IMDS 및 링크로컬: allow_private=True 여도 절대 차단
    for prohibited in ("169.254.169.254", "169.254.1.1", "224.0.0.1", "255.255.255.255", "0.0.0.0"):
        safe, reason = check_ip_safety(prohibited, allow_private=True)
        assert safe is False
        assert "strictly denied" in reason


def test_allowlist_permits_specific_cidr_only():
    # allow_private=False 이지만 특정 사내망 CIDR(10.20.0.0/16)만 화이트리스트 지정
    allowlist = ["10.20.0.0/16", "192.168.100.5"]
    
    # allowlist 범위 내 IP -> 허용
    safe, _ = check_ip_safety("10.20.1.5", allow_private=False, allowlist=allowlist)
    assert safe is True
    safe, _ = check_ip_safety("192.168.100.5", allow_private=False, allowlist=allowlist)
    assert safe is True

    # allowlist 외의 다른 사설망 IP -> 차단
    safe, reason = check_ip_safety("10.30.1.5", allow_private=False, allowlist=allowlist)
    assert safe is False
    assert "blocked" in reason

    safe, reason = check_ip_safety("192.168.1.1", allow_private=False, allowlist=allowlist)
    assert safe is False


def test_allowlist_permits_specific_domain_name():
    allowlist = ["wiki.internal.corp", "*.corp.local"]

    # 도메인 일치 시 허용
    safe, _ = check_ip_safety("10.5.5.5", allow_private=False, allowlist=allowlist, hostname="wiki.internal.corp")
    assert safe is True

    safe, _ = check_ip_safety("10.5.5.5", allow_private=False, allowlist=allowlist, hostname="jira.corp.local")
    assert safe is True

    # 미등록 도메인의 사설 IP -> 차단
    safe, reason = check_ip_safety("10.5.5.5", allow_private=False, allowlist=allowlist, hostname="other.corp")
    assert safe is False


def test_safe_http_client_blocks_imds_with_fetch_error(monkeypatch):
    client = SafeHttpClient(allow_private_networks=True)
    
    # 169.254.169.254 로 해석되도록 getaddrinfo 모의
    monkeypatch.setattr(
        socket, "getaddrinfo",
        lambda host, port: [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("169.254.169.254", 80))]
    )
    
    with pytest.raises(FetchError) as exc_info:
        client.get("http://169.254.169.254/latest/meta-data/")
    assert "strictly denied" in str(exc_info.value)


def test_safe_http_client_allows_private_ip_when_enabled(monkeypatch):
    client = SafeHttpClient(allow_private_networks=True)
    
    # 10.10.20.5 로 해석
    monkeypatch.setattr(
        socket, "getaddrinfo",
        lambda host, port: [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("10.10.20.5", 80))]
    )
    
    # _resolve_and_check 가 FetchError 를 던지지 않고 정상 통과하는지 검증
    client._resolve_and_check("http://internal-wiki.corp/page")
