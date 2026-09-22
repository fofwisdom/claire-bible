"""안전한 HTTP 클라이언트 (SSRF 방지, 온프레미스 사설망 정책 제어, 크기 제한, 타임아웃)."""

from __future__ import annotations

import ipaddress
import socket
import urllib.parse
from collections.abc import Sequence
from typing import Any

import httpx

from ...config import get_settings
from .base import FetchError
from .http_policy import BROWSER_USER_AGENT

# 어떤 환경에서도 절대 허용되지 않는 보안 위험 대역 (클라우드 메타데이터, 링크 로컬, 멀티캐스트, 브로드캐스트)
STRICTLY_PROHIBITED_NETWORKS = (
    ipaddress.ip_network("169.254.0.0/16"),      # AWS/GCP/Azure/OpenStack IMDS (169.254.169.254)
    ipaddress.ip_network("224.0.0.0/4"),        # IPv4 Multicast
    ipaddress.ip_network("0.0.0.0/8"),          # This host on this network
    ipaddress.ip_network("255.255.255.255/32"),  # Broadcast
    ipaddress.ip_network("fe80::/10"),          # IPv6 Link-Local
    ipaddress.ip_network("ff00::/8"),           # IPv6 Multicast
    ipaddress.ip_network("::/128"),             # IPv6 Unspecified
)

# 온프레미스/사내망 사설 IP 대역 (CLAIRE_ALLOW_PRIVATE_NETWORKS=true 시 허용 가능)
PRIVATE_NETWORKS = (
    ipaddress.ip_network("10.0.0.0/8"),         # RFC 1918 Class A
    ipaddress.ip_network("172.16.0.0/12"),      # RFC 1918 Class B
    ipaddress.ip_network("192.168.0.0/16"),     # RFC 1918 Class C
    ipaddress.ip_network("127.0.0.0/8"),        # IPv4 Loopback
    ipaddress.ip_network("100.64.0.0/10"),      # Carrier-grade NAT / Tailscale
    ipaddress.ip_network("fc00::/7"),           # IPv6 Unique Local (ULA)
    ipaddress.ip_network("::1/128"),            # IPv6 Loopback
)


def _matches_allowlist(target_ip: ipaddress.IPv4Address | ipaddress.IPv6Address, hostname: str, allowlist: Sequence[str]) -> bool:
    """호스트명 또는 IP가 화이트리스트(CIDR 또는 도메인 패턴)에 부합하는지 검사."""
    h_lower = hostname.lower().strip()
    for entry in allowlist:
        entry = entry.strip()
        if not entry:
            continue
        # 1. CIDR 네트워크 형태 검사 (예: 10.20.0.0/16, 192.168.1.50)
        try:
            net = ipaddress.ip_network(entry, strict=False)
            if target_ip in net:
                return True
            continue
        except ValueError:
            pass

        # 2. 도메인/호스트명 매칭 (예: wiki.internal.corp, *.internal.net, localhost)
        e_lower = entry.lower()
        if e_lower.startswith("*.") and (h_lower.endswith(e_lower[1:]) or h_lower == e_lower[2:]):
            return True
        if h_lower == e_lower:
            return True
    return False


def check_ip_safety(
    ip_str: str,
    *,
    allow_private: bool = False,
    allowlist: Sequence[str] | None = None,
    hostname: str = "",
) -> tuple[bool, str | None]:
    """IP 주소 및 호스트의 SSRF 안전성을 검증.

    반환: (is_safe, error_reason)
    """
    try:
        ip = ipaddress.ip_address(ip_str)
    except ValueError:
        return False, f"Invalid IP address format: {ip_str}"

    # 1. 클라우드 메타데이터(IMDS) 및 링크로컬/멀티캐스트는 어떤 온프레미스 설정에서도 절대 차단
    for prohibited in STRICTLY_PROHIBITED_NETWORKS:
        if ip in prohibited:
            return False, f"Access to cloud metadata or prohibited network {ip} is strictly denied"

    # 2. 화이트리스트에 명시된 IP/CIDR/도메인이면 즉시 허용
    if allowlist and _matches_allowlist(ip, hostname, allowlist):
        return True, None

    # 3. 사설망 대역 검사
    is_private_subnet = any(ip in net for net in PRIVATE_NETWORKS) or ip.is_private or ip.is_loopback
    if is_private_subnet:
        if allow_private:
            return True, None
        return False, f"Private network address {ip} blocked (on-premise access disabled)"

    # 4. 공인 인터넷 주소는 기본 허용
    return True, None


def is_safe_ip(ip_str: str) -> bool:
    """하위 호환성을 위한 단순 불리언 검사기 (기본 설정 기준)."""
    is_safe, _ = check_ip_safety(ip_str, allow_private=False)
    return is_safe


class MediaResponseDetected(FetchError):
    """미디어 컨텐츠(video/audio) 응답이 감지되었을 때 발생하는 예외."""

    def __init__(self, message: str, url: str, content_type: str = ""):
        super().__init__(message)
        self.url = url
        self.content_type = content_type


MEDIA_CONTENT_TYPES = (
    "video/",
    "audio/",
    "application/vnd.apple.mpegurl",
    "application/x-mpegurl",
    "application/dash+xml",
    "application/ogg",
)

MEDIA_FILE_EXTENSIONS = (
    ".mp4", ".m3u8", ".mpd", ".webm", ".mov", ".mkv", ".avi", ".ts", ".m4v", ".flv",
    ".mp3", ".m4a", ".wav", ".aac", ".flac", ".ogg", ".opus", ".wma",
)


def is_media_content_type(ctype: str) -> bool:
    c = (ctype or "").lower().strip()
    return any(c.startswith(m) or m in c for m in MEDIA_CONTENT_TYPES)


def has_media_disposition(cdisp: str) -> bool:
    c = (cdisp or "").lower()
    if not c:
        return False
    return any(ext in c for ext in MEDIA_FILE_EXTENSIONS)


class SafeHttpClient:
    """온프레미스 사설망 정책 제어, SSRF 방지, 크기 제한, 타임아웃이 내장된 보안 HTTP 클라이언트."""

    def __init__(
        self,
        timeout: float = 30.0,
        max_size: int = 10 * 1024 * 1024,
        allow_private_networks: bool | None = None,
        private_network_allowlist: Sequence[str] | str | None = None,
    ):
        settings = get_settings()
        if allow_private_networks is None:
            self.allow_private = getattr(settings, "allow_private_networks", False)
        else:
            self.allow_private = allow_private_networks

        if private_network_allowlist is None:
            raw_allowlist = getattr(settings, "private_network_allowlist", "")
        else:
            raw_allowlist = private_network_allowlist

        if isinstance(raw_allowlist, str):
            self.allowlist = [item.strip() for item in raw_allowlist.split(",") if item.strip()]
        else:
            self.allowlist = list(raw_allowlist or [])

        self.timeout = timeout
        self.max_size = max_size
        self.headers = {
            "User-Agent": BROWSER_USER_AGENT,
            "Accept": "application/vnd.oasis.opendocument.text, application/pdf;q=0.9, text/html;q=0.8, application/xhtml+xml, */*;q=0.1",
        }

    def _resolve_and_check(self, url: str) -> None:
        parsed = urllib.parse.urlsplit(url)
        hostname = parsed.hostname
        if not hostname:
            raise FetchError(f"Invalid URL: missing hostname in {url}")

        try:
            # DNS Rebinding 방어: 해석된 모든 IP 주소에 대해 안전성을 전수 검사
            addr_info = socket.getaddrinfo(hostname, None)
            resolved_ips = {item[4][0] for item in addr_info if item and len(item) > 4 and item[4]}
        except Exception as e:
            raise FetchError(f"Failed to resolve host {hostname}: {e}")

        if not resolved_ips:
            raise FetchError(f"No IP address resolved for {hostname}")

        for ip_str in resolved_ips:
            safe, reason = check_ip_safety(
                ip_str,
                allow_private=self.allow_private,
                allowlist=self.allowlist,
                hostname=hostname,
            )
            if not safe:
                raise FetchError(f"SSRF policy violation: {reason}")

    def get(self, url: str, **kwargs: Any) -> httpx.Response:
        self._resolve_and_check(url)
        client_kwargs = {
            "follow_redirects": True,
            "timeout": self.timeout,
            "headers": self.headers,
        }
        client_kwargs.update(kwargs)
        try:
            with httpx.Client(**client_kwargs) as client:
                resp = client.get(url)
                ctype = resp.headers.get("content-type", "").lower()
                cdisp = resp.headers.get("content-disposition", "").lower()

                # 미디어 컨텐츠 감지 시 즉시 전용 예외 송출 (웹페이지 크기 초과 에러 방지)
                if is_media_content_type(ctype) or has_media_disposition(cdisp):
                    raise MediaResponseDetected(
                        f"Media response detected ({ctype or cdisp})",
                        url=str(resp.url),
                        content_type=ctype,
                    )

                content_length = resp.headers.get("Content-Length")
                if content_length and int(content_length) > self.max_size:
                    raise FetchError(f"Response too large: {content_length} bytes > {self.max_size}")
                return resp
        except MediaResponseDetected:
            raise
        except httpx.RequestError as e:
            raise FetchError(f"HTTP request failed: {e}")


