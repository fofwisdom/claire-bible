"""웹 API의 ASGI 보안 경계.

Starlette 라우트와 Uvicorn 실행기는 이 모듈 안쪽에 둔다. 여기서는 public URL에서 도출한 정확한 Host, CORS/Origin, 요청 크기, 인증 scope와 query 없는 접근 로그를 한 번에 적용한다. Reverse proxy 헤더는 의도적으로 읽지 않는다. 예외는 `client_ip` 하나다: 로그인 실패 제한 키에 쓸 클라이언트 IP만, 바로 앞 피어가 ``CLAIRE_TRUSTED_PROXIES``(와 Cloudflare 모드의 Cloudflare 대역)에 있을 때 ``X-Forwarded-For``에서 읽는다.
"""

from __future__ import annotations

import asyncio
import hmac
import ipaddress
import json
import logging
import re
import secrets
import time
from collections.abc import Mapping
from dataclasses import dataclass, field
from http.cookies import SimpleCookie
from pathlib import Path
from typing import TYPE_CHECKING, Any, Literal
from urllib.parse import parse_qs, urlsplit

from starlette.exceptions import HTTPException
from starlette.requests import Request
from starlette.responses import (
    JSONResponse,
    PlainTextResponse,
    RedirectResponse,
    Response,
)
from starlette.routing import compile_path
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from ..store import accounts as accounts_store
from ..store.accounts import InstanceMode
from ..store import db as dbm
from .principal import ANONYMOUS, PRINCIPAL_STATE_KEY, Principal
from .namespace import (
    NAMESPACE_EXTRA_STATE_KEY,
    NAMESPACE_STATE_KEY,
    PARSED_PREFIX_STATE_KEY,
    NamespaceDirectory,
    NamespaceResolverMiddleware,
    ParsedPrefix,
    PrefixParserMiddleware,
    ResolvedNamespace,
    SqliteNamespaceDirectory,
    account_home_path,
    _Outcome,
    _spa_query,
)

if TYPE_CHECKING:
    from .host_router import HostRoles, LegacyHostDirectory

__all__ = [
    "CLOUDFLARE_IPV4_CIDRS",
    "CLOUDFLARE_IPV6_CIDRS",
    "CLOUDFLARE_NETWORKS",
    "CloudflareIPFilterMiddleware",
    "MAX_REQUEST_BODY",
    "ALL_MODES",
    "NETWORK_ONLY",
    "ROUTE_POLICY",
    "RouteRule",
    "RouteSurface",
    "LEGACY_COOKIE_FALLBACK_REMOVED",
    "ErrorBoundaryMiddleware",
    "InvalidJSONBody",
    "RequestBodyTooLarge",
    "WebRuntimeConfig",
    "clear_session_cookie",
    "client_ip",
    "parse_trusted_proxies",
    "is_cloudflare_ip",
    "lookup_route_rule",
    "read_json_body",
    "read_session_cookie",
    "request_auth_scope",
    "request_id",
    "request_origin_kind",
    "set_session_cookie",
    "wrap_web_app",
]

log = logging.getLogger("claire.api.access")

MAX_REQUEST_BODY = 1024 * 1024
_AUTH_SCOPE_KEY = "claire_auth_scope"
_REQUEST_ID_KEY = "claire_request_id"
_ORIGIN_KIND_KEY = "claire_origin_kind"
# 쿠키 채널에서 동일 origin(``Origin``이 공개 origin)을 요구하는 메서드. 쿠키는 브라우저가 자동으로 싣기 때문이다.
_STATE_CHANGING_METHODS = frozenset({"POST", "PUT", "PATCH", "DELETE"})
_DNS_NAME_RE = re.compile(
    r"(?=.{1,253}\Z)(?:[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\.)*"
    r"[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\Z",
    re.IGNORECASE,
)
_AUTHORITY_RE = re.compile(
    r"(?P<host>[A-Za-z0-9.-]+)(?::(?P<port>[0-9]{1,5}))?\Z"
)
_BEARER_RE = re.compile(r"Bearer[ \t]+([^ \t,]+)\Z", re.IGNORECASE)


def _build_content_security_policy(
    ga_measurement_id: str = "",
    *,
    usercontent_origin: str = "",
    media_origin: str = "",
) -> str:
    script_src = "'self' 'unsafe-inline' 'unsafe-eval' https://unpkg.com"
    connect_src = "'self'"
    if ga_measurement_id:
        script_src += (
            " https://www.googletagmanager.com https://*.googletagmanager.com"
            " https://www.google-analytics.com https://*.google-analytics.com"
            " https://google-analytics.com"
        )
        connect_src += (
            " https://*.google-analytics.com https://google-analytics.com"
            " https://*.analytics.google.com https://analytics.google.com"
            " https://*.googletagmanager.com https://googletagmanager.com"
            " https://stats.g.doubleclick.net https://*.doubleclick.net https://*.g.doubleclick.net"
        )
    frame_src_part = f"frame-src {usercontent_origin}; " if usercontent_origin else ""
    media_origin_part = f" {media_origin}" if media_origin else ""
    return (
        "default-src 'self'; "
        f"script-src {script_src}; "
        "style-src 'self' 'unsafe-inline' https://fonts.googleapis.com https://unpkg.com; "
        f"img-src 'self' data: https:{media_origin_part}; "
        f"media-src 'self' data: https:{media_origin_part}; "
        f"connect-src {connect_src}; "
        "font-src 'self' data: https://fonts.gstatic.com https://unpkg.com; "
        "worker-src 'self' blob:; "
        "object-src 'none'; "
        "base-uri 'none'; "
        "form-action 'self' https:; "
        f"{frame_src_part}"
        "frame-ancestors 'none'"
    )


_CONTENT_SECURITY_POLICY = _build_content_security_policy()

# Cloudflare 공식 공인 IP 대역 (https://www.cloudflare.com/ko-kr/ips/)
CLOUDFLARE_IPV4_CIDRS: tuple[str, ...] = (
    "173.245.48.0/20",
    "103.21.244.0/22",
    "103.22.200.0/22",
    "103.31.4.0/22",
    "141.101.64.0/18",
    "108.162.192.0/18",
    "190.93.240.0/20",
    "188.114.96.0/20",
    "197.234.240.0/22",
    "198.41.128.0/17",
    "162.158.0.0/15",
    "104.16.0.0/13",
    "104.24.0.0/14",
    "172.64.0.0/13",
    "131.0.72.0/22",
)

CLOUDFLARE_IPV6_CIDRS: tuple[str, ...] = (
    "2400:cb00::/32",
    "2606:4700::/32",
    "2803:f800::/32",
    "2405:b500::/32",
    "2405:8100::/32",
    "2a06:98c0::/29",
    "2c0f:f248::/32",
)

CLOUDFLARE_NETWORKS: tuple[ipaddress.IPv4Network | ipaddress.IPv6Network, ...] = tuple(
    ipaddress.ip_network(cidr)
    for cidr in CLOUDFLARE_IPV4_CIDRS + CLOUDFLARE_IPV6_CIDRS
)


def is_cloudflare_ip(ip: ipaddress.IPv4Address | ipaddress.IPv6Address) -> bool:
    """주어진 IP가 Cloudflare 공식 IP 대역에 속하는지 여부를 판별한다."""
    return any(ip in net for net in CLOUDFLARE_NETWORKS)


_IPNetwork = ipaddress.IPv4Network | ipaddress.IPv6Network
_IPAddress = ipaddress.IPv4Address | ipaddress.IPv6Address


def parse_trusted_proxies(value: Any) -> tuple[_IPNetwork, ...]:
    """``CLAIRE_TRUSTED_PROXIES``(쉼표로 구분한 CIDR 목록)를 읽는다. 빈 값이면 빈 튜플.

    항목이 CIDR로 읽히지 않거나(호스트 비트가 켜진 ``10.0.0.1/8`` 포함) 빈 항목(``a,,b``)이 있으면 ``ValueError``로 기동을 멈춘다(C1). 단일 주소는 ``/32``·``/128``로 쓴다.
    """
    if not isinstance(value, str):
        raise ValueError("CLAIRE_TRUSTED_PROXIES must be a comma-separated string of CIDRs")
    if not value.strip():
        return ()
    networks: list[_IPNetwork] = []
    for item in value.split(","):
        cidr = item.strip()
        if not cidr:
            raise ValueError(f"CLAIRE_TRUSTED_PROXIES has an empty entry: {value!r}")
        try:
            networks.append(ipaddress.ip_network(cidr, strict=True))
        except ValueError as exc:
            raise ValueError(f"CLAIRE_TRUSTED_PROXIES has an invalid CIDR: {cidr!r}") from exc
    return tuple(networks)


def _parse_ip(raw: str) -> _IPAddress | None:
    try:
        address = ipaddress.ip_address(raw)
    except ValueError:
        return None
    if isinstance(address, ipaddress.IPv6Address) and address.ipv4_mapped is not None:
        return address.ipv4_mapped
    return address


def _is_trusted(address: _IPAddress, trusted: tuple[_IPNetwork, ...]) -> bool:
    return any(address.version == net.version and address in net for net in trusted)


def client_ip(scope: Scope, trusted_proxies: tuple[_IPNetwork, ...]) -> str:
    """로그인 실패 제한 키로 쓸 클라이언트 IP.

    - 바로 앞 피어(ASGI ``client``)가 ``trusted_proxies``에 없으면 피어 주소를 쓴다. 목록이 비면 항상 피어다.
    - 신뢰 피어이면 ``X-Forwarded-For``를 오른쪽부터 읽어 신뢰 목록에 없는 첫 주소를 쓴다. 왼쪽 값은 클라이언트가 마음대로 넣을 수 있어 그보다 왼쪽은 보지 않는다. 모든 항목이 신뢰 대역이면 가장 왼쪽 항목을 쓴다.
    - 헤더가 없거나, 여러 줄이거나, 항목 하나라도 IP 주소가 아니면(포트·빈 항목·``unknown`` 등) 피어 주소를 쓴다. 헤더를 추측해 고치지 않는다(C1).

    접근 로그의 IP 표기는 이 함수를 쓰지 않는다.
    """
    client = scope.get("client")
    peer_raw = client[0] if client and client[0] else ""
    if not peer_raw:
        return "-"
    peer = _parse_ip(peer_raw)
    if peer is None or not trusted_proxies or not _is_trusted(peer, trusted_proxies):
        return peer_raw
    headers = _raw_headers(scope, b"x-forwarded-for")
    if len(headers) != 1:
        return peer_raw
    try:
        items = headers[0].decode("ascii").split(",")
    except UnicodeDecodeError:
        return peer_raw
    chain: list[_IPAddress] = []
    for item in items:
        address = _parse_ip(item.strip())
        if address is None:
            return peer_raw
        chain.append(address)
    for address in reversed(chain):
        if not _is_trusted(address, trusted_proxies):
            return str(address)
    return str(chain[0])


AccessLevel = Literal["public", "read", "collaborator", "owner", "admin"]
AuthScope = Literal["public", "anonymous", "readonly", "collaborator", "owner"]
RouteKey = tuple[str, str]
# 라우트 표면(§9.1). root: 루트 GET / 전용, system: 루트에서만(접두 아래 404),
# namespaced: 네임스페이스 필요(N1–N3), any: 접두를 무시한다.
RouteSurface = Literal["root", "system", "namespaced", "any"]
ALL_MODES: frozenset[InstanceMode] = frozenset({"single", "network"})
NETWORK_ONLY: frozenset[InstanceMode] = frozenset({"network"})


@dataclass(frozen=True, slots=True)
class RouteRule:
    access: AccessLevel
    # ROUTE_POLICY의 모든 항목은 아래 표면별 생성 헬퍼(_root·_system·_namespaced·_any)로 표면을 명시한다.
    # 기본값에 기대지 않는다(tests/test_route_surface_contract.py가 AST로 확인한다).
    surface: RouteSurface = "namespaced"
    # 현재 모드가 없으면 404(N5).
    modes: frozenset[InstanceMode] = ALL_MODES


def _root(access: AccessLevel, *, modes: frozenset[InstanceMode] = ALL_MODES) -> RouteRule:
    """루트 ``GET /`` 전용(§9.1). 접두 아래에서는 namespaced 홈으로 동작한다."""
    return RouteRule(access=access, surface="root", modes=modes)


def _system(access: AccessLevel, *, modes: frozenset[InstanceMode] = ALL_MODES) -> RouteRule:
    """루트에서만 받는다. 접두 아래에서는 404(N5)."""
    return RouteRule(access=access, surface="system", modes=modes)


def _namespaced(access: AccessLevel, *, modes: frozenset[InstanceMode] = ALL_MODES) -> RouteRule:
    """네임스페이스가 필요하다(N1–N3)."""
    return RouteRule(access=access, surface="namespaced", modes=modes)


def _any(access: AccessLevel, *, modes: frozenset[InstanceMode] = ALL_MODES) -> RouteRule:
    """네임스페이스와 무관하다. 접두를 검증한 뒤 무시하고 자격증명 계정을 쓴다."""
    return RouteRule(access=access, surface="any", modes=modes)


def lookup_route_rule(method: str, path: str) -> RouteRule | None:
    """``(method, 정규 path)``의 정책. 정확한 키를 먼저 찾고, 없으면 경로 매개변수 키(``{name}``·``{name:path}``)를 Starlette ``compile_path``로 컴파일한 표에서 찾는다(§9.1). 없으면 None(fail-closed 404).

    ``path``는 ``PrefixParserMiddleware``가 접두를 뗀 남은 경로다.
    """
    method = method.upper()
    rule = _EXACT_ROUTE_POLICY.get((method, path))
    if rule is not None:
        return rule
    for key_method, regex, param_rule in _PARAM_ROUTE_POLICY:
        if key_method == method and regex.match(path):
            return param_rule
    return None


# 등록되지 않은 (method, path)는 fail-closed(404)다. Starlette는 GET Route에 HEAD를
# 자동 추가하므로 HEAD도 명시해 실제 라우터와 인증 계약을 정확히 맞춘다.
ROUTE_POLICY: Mapping[RouteKey, RouteRule] = {
    ("GET", "/health"): _system("public"),
    ("HEAD", "/health"): _system("public"),
    ("GET", "/robots.txt"): _system("public"),
    ("HEAD", "/robots.txt"): _system("public"),
    ("GET", "/sitemap.xml"): _system("public"),
    ("HEAD", "/sitemap.xml"): _system("public"),
    ("GET", "/favicon.ico"): _system("public"),
    ("HEAD", "/favicon.ico"): _system("public"),
    ("GET", "/favicon.svg"): _system("public"),
    ("HEAD", "/favicon.svg"): _system("public"),
    ("GET", "/apple-touch-icon.png"): _system("public"),
    ("HEAD", "/apple-touch-icon.png"): _system("public"),
    ("GET", "/apple-touch-icon-precomposed.png"): _system("public"),
    ("HEAD", "/apple-touch-icon-precomposed.png"): _system("public"),
    ("GET", "/manifest.json"): _system("public"),
    ("HEAD", "/manifest.json"): _system("public"),
    ("GET", "/site.webmanifest"): _system("public"),
    ("HEAD", "/site.webmanifest"): _system("public"),
    ("GET", "/browserconfig.xml"): _system("public"),
    ("HEAD", "/browserconfig.xml"): _system("public"),
    ("GET", "/icon"): _system("public"),
    ("HEAD", "/icon"): _system("public"),
    ("GET", "/font"): _system("public"),
    ("HEAD", "/font"): _system("public"),
    ("GET", "/fonts/NotoSansKR-Regular.woff2"): _system("public"),
    ("HEAD", "/fonts/NotoSansKR-Regular.woff2"): _system("public"),
    ("GET", "/fonts/NotoSansKR-Bold.woff2"): _system("public"),
    ("HEAD", "/fonts/NotoSansKR-Bold.woff2"): _system("public"),
    ("GET", "/fonts/NotoSerifKR-Regular.woff2"): _system("public"),
    ("HEAD", "/fonts/NotoSerifKR-Regular.woff2"): _system("public"),
    ("GET", "/fonts/NotoSerifKR-Bold.woff2"): _system("public"),
    ("HEAD", "/fonts/NotoSerifKR-Bold.woff2"): _system("public"),
    ("GET", "/fonts/D2Coding.woff2"): _system("public"),
    ("HEAD", "/fonts/D2Coding.woff2"): _system("public"),
    ("GET", "/fonts/D2CodingBold.woff2"): _system("public"),
    ("HEAD", "/fonts/D2CodingBold.woff2"): _system("public"),
    ("GET", "/p"): _system("public"),
    ("HEAD", "/p"): _system("public"),
    ("GET", "/p/presentation"): _system("public"),
    ("HEAD", "/p/presentation"): _system("public"),
    ("GET", "/image"): _system("public"),
    ("HEAD", "/image"): _system("public"),
    ("GET", "/images/{filename:path}"): _system("public"),
    ("HEAD", "/images/{filename:path}"): _system("public"),
    ("GET", "/p/images/{filename:path}"): _system("public"),
    ("HEAD", "/p/images/{filename:path}"): _system("public"),
    # Mount("/static")의 정적 자산(O2). 접두 아래에서는 system이라 404다.
    ("GET", "/static/{path:path}"): _system("public"),
    ("HEAD", "/static/{path:path}"): _system("public"),
    ("GET", "/"): _root("read"),
    ("HEAD", "/"): _root("read"),
    ("GET", "/syndication"): _namespaced("read"),
    ("HEAD", "/syndication"): _namespaced("read"),
    ("GET", "/syndication/preferences"): _namespaced("owner"),
    ("HEAD", "/syndication/preferences"): _namespaced("owner"),
    ("POST", "/api/syndication/dispatch"): _namespaced("collaborator"),
    ("POST", "/syndication/dispatch"): _namespaced("collaborator"),
    ("GET", "/api/syndication/dispatches"): _namespaced("read"),
    ("HEAD", "/api/syndication/dispatches"): _namespaced("read"),
    ("GET", "/syndication/dispatches"): _namespaced("read"),
    ("HEAD", "/syndication/dispatches"): _namespaced("read"),
    ("GET", "/whoami"): _namespaced("read"),
    ("HEAD", "/whoami"): _namespaced("read"),
    ("GET", "/stats"): _namespaced("read"),
    ("HEAD", "/stats"): _namespaced("read"),
    ("GET", "/graph"): _namespaced("read"),
    ("HEAD", "/graph"): _namespaced("read"),
    ("GET", "/node"): _namespaced("read"),
    ("HEAD", "/node"): _namespaced("read"),
    ("GET", "/documents"): _namespaced("read"),
    ("HEAD", "/documents"): _namespaced("read"),
    ("GET", "/document"): _namespaced("read"),
    ("HEAD", "/document"): _namespaced("read"),
    ("GET", "/document/raw"): _namespaced("collaborator"),
    ("HEAD", "/document/raw"): _namespaced("collaborator"),
    ("GET", "/document/presentation"): _namespaced("read"),
    ("HEAD", "/document/presentation"): _namespaced("read"),
    ("POST", "/document/presentation/generate"): _namespaced("collaborator"),
    ("POST", "/document/presentation/compose"): _namespaced("collaborator"),
    ("POST", "/document/presentation/compile"): _namespaced("collaborator"),
    ("GET", "/document/presentation/adoc"): _namespaced("read"),
    ("HEAD", "/document/presentation/adoc"): _namespaced("read"),
    ("PUT", "/document/presentation/adoc"): _namespaced("collaborator"),
    ("GET", "/resolution/decisions"): _namespaced("read"),
    ("HEAD", "/resolution/decisions"): _namespaced("read"),
    ("GET", "/ingest/active"): _namespaced("read"),
    ("HEAD", "/ingest/active"): _namespaced("read"),
    ("POST", "/search"): _namespaced("read"),
    ("POST", "/mcp"): _system("read"),
    ("GET", "/mcp"): _system("read"),
    ("HEAD", "/mcp"): _system("read"),
    ("GET", "/themes"): _namespaced("read"),
    ("HEAD", "/themes"): _namespaced("read"),
    ("POST", "/themes"): _namespaced("owner"),
    ("PATCH", "/themes"): _namespaced("owner"),
    ("DELETE", "/themes"): _namespaced("owner"),
    ("GET", "/providers"): _any("admin"),
    ("HEAD", "/providers"): _any("admin"),
    ("POST", "/providers"): _any("admin"),
    ("PATCH", "/providers"): _any("admin"),
    ("POST", "/providers/test"): _any("admin"),
    ("GET", "/limits"): _any("admin"),
    ("HEAD", "/limits"): _any("admin"),
    ("POST", "/limits"): _any("admin"),
    ("PATCH", "/limits"): _any("admin"),
    ("GET", "/tokens"): _any("owner"),
    ("HEAD", "/tokens"): _any("owner"),
    ("POST", "/tokens"): _any("owner"),
    ("POST", "/tokens/revoke"): _any("owner"),
    ("DELETE", "/tokens"): _any("owner"),
    ("GET", "/ingest-limits"): _any("admin"),
    ("HEAD", "/ingest-limits"): _any("admin"),
    ("POST", "/ingest-limits"): _any("admin"),
    ("PATCH", "/ingest-limits"): _any("admin"),
    ("POST", "/ingest"): _namespaced("collaborator"),
    ("POST", "/ingest-stream"): _namespaced("collaborator"),
    ("POST", "/reingest"): _namespaced("collaborator"),
    ("POST", "/reingest-stream"): _namespaced("collaborator"),
    ("POST", "/regenerate"): _namespaced("collaborator"),
    ("POST", "/regenerate-stream"): _namespaced("collaborator"),
    ("POST", "/document/regenerate"): _namespaced("collaborator"),
    ("POST", "/document/seen"): _namespaced("owner"),
    ("POST", "/document/pin"): _namespaced("owner"),
    ("POST", "/document/hide"): _namespaced("owner"),
    ("POST", "/document/title"): _namespaced("owner"),
    ("POST", "/document/purge"): _namespaced("owner"),
    ("POST", "/purge"): _namespaced("owner"),
    ("POST", "/document/matrix/purge"): _namespaced("owner"),
    ("POST", "/synthesize"): _namespaced("owner"),
    ("POST", "/research"): _namespaced("owner"),
    ("POST", "/dedup/scan"): _namespaced("owner"),
    ("POST", "/dedup/merge"): _namespaced("owner"),
    ("POST", "/entity/primary-label"): _namespaced("owner"),
    ("POST", "/resolution/rollback"): _namespaced("owner"),
    ("POST", "/share"): _namespaced("read"),
    ("POST", "/support/bundle"): _system("admin"),
    ("GET", "/support/bundle"): _system("public"),
    ("HEAD", "/support/bundle"): _system("public"),
    ("POST", "/graph/bundle"): _namespaced("owner"),
    ("GET", "/graph/bundle"): _namespaced("public"),
    ("HEAD", "/graph/bundle"): _namespaced("public"),
    ("GET", "/reference"): _system("public"),
    ("HEAD", "/reference"): _system("public"),
    ("GET", "/openapi.yaml"): _system("public"),
    ("HEAD", "/openapi.yaml"): _system("public"),
    ("GET", "/.well-known/oauth-protected-resource"): _system("public"),
    ("HEAD", "/.well-known/oauth-protected-resource"): _system("public"),
    ("GET", "/.well-known/oauth-authorization-server"): _system("public"),
    ("HEAD", "/.well-known/oauth-authorization-server"): _system("public"),
    ("POST", "/oauth/register"): _system("public"),
    ("GET", "/oauth/authorize"): _system("public"),
    ("HEAD", "/oauth/authorize"): _system("public"),
    ("POST", "/oauth/authorize"): _system("public"),
    ("POST", "/oauth/authorize/telegram-push"): _system("public"),
    ("GET", "/oauth/authorize/poll"): _system("public"),
    ("HEAD", "/oauth/authorize/poll"): _system("public"),
    ("POST", "/oauth/token"): _system("public"),
    # 계정 (설계 §8.4–§8.7). /admin/*·/api/admin/*는 admin 접근 수준(§9.1)이고, 설정 전용 조건은 핸들러가 404로 판정한다(api.accounts_routes).
    ("GET", "/setup"): _system("owner"),
    ("HEAD", "/setup"): _system("owner"),
    ("POST", "/setup"): _system("owner"),
    # /login·/logout은 공개다. 로그인 CSRF·강제 로그아웃은 핸들러가 동일 origin 검사로 막는다.
    ("GET", "/login"): _system("public"),
    ("HEAD", "/login"): _system("public"),
    ("POST", "/login"): _system("public"),
    ("POST", "/logout"): _system("public"),
    ("POST", "/api/auth/reauth"): _system("owner"),
    ("GET", "/admin/accounts"): _system("admin"),
    ("HEAD", "/admin/accounts"): _system("admin"),
    ("GET", "/api/admin/accounts"): _system("admin"),
    ("HEAD", "/api/admin/accounts"): _system("admin"),
    ("PATCH", "/api/admin/accounts"): _system("admin"),
    ("GET", "/api/admin/usernames/retired"): _system("admin"),
    ("HEAD", "/api/admin/usernames/retired"): _system("admin"),
    ("POST", "/api/admin/usernames/release"): _system("admin"),
    ("GET", "/admin/instance"): _system("admin"),
    ("HEAD", "/admin/instance"): _system("admin"),
    ("GET", "/api/admin/instance"): _system("admin"),
    ("HEAD", "/api/admin/instance"): _system("admin"),
    ("POST", "/api/admin/instance/mode"): _system("admin"),
    ("POST", "/api/admin/instance/legacy-hosts/sunset"): _system("admin"),
    ("GET", "/invite/{code}"): _system("public", modes=NETWORK_ONLY),
    ("HEAD", "/invite/{code}"): _system("public", modes=NETWORK_ONLY),
    ("POST", "/invite/{code}"): _system("public", modes=NETWORK_ONLY),
    ("GET", "/explore/accounts"): _system("public", modes=NETWORK_ONLY),
    ("HEAD", "/explore/accounts"): _system("public", modes=NETWORK_ONLY),
    ("GET", "/api/invites"): _any("admin", modes=NETWORK_ONLY),
    ("HEAD", "/api/invites"): _any("admin", modes=NETWORK_ONLY),
    ("POST", "/api/invites"): _any("admin", modes=NETWORK_ONLY),
    ("PATCH", "/api/invites"): _any("admin", modes=NETWORK_ONLY),
    ("GET", "/me"): _any("owner"),
    ("HEAD", "/me"): _any("owner"),
    ("GET", "/api/me"): _any("owner"),
    ("HEAD", "/api/me"): _any("owner"),
    ("PATCH", "/api/me"): _any("owner"),
    ("POST", "/api/me/password"): _any("owner"),
    # P4 계정 API 키(§11.2, 쓰기 전용)와 서버 등급(§10.3)
    ("GET", "/api/me/keys"): _any("owner", modes=NETWORK_ONLY),
    ("HEAD", "/api/me/keys"): _any("owner", modes=NETWORK_ONLY),
    ("PUT", "/api/me/keys"): _any("owner", modes=NETWORK_ONLY),
    ("DELETE", "/api/me/keys"): _any("owner", modes=NETWORK_ONLY),
    ("POST", "/api/me/keys/verify"): _any("owner", modes=NETWORK_ONLY),
    ("GET", "/api/admin/server-tier"): _system("admin"),
    ("HEAD", "/api/admin/server-tier"): _system("admin"),
    ("PUT", "/api/admin/server-tier"): _system("admin"),
    # P5 L5-b IdP, 계정 연결, 초대 가입, 관리자 IdP (§12, §13.3)
    ("GET", "/auth/oidc/{id}/start"): _system("public"),
    ("HEAD", "/auth/oidc/{id}/start"): _system("public"),
    ("GET", "/auth/oidc/{id}/callback"): _system("public"),
    ("HEAD", "/auth/oidc/{id}/callback"): _system("public"),
    ("POST", "/api/me/identities/oidc/{id}/start"): _any("owner"),
    ("GET", "/api/me/identities"): _any("owner"),
    ("HEAD", "/api/me/identities"): _any("owner"),
    ("DELETE", "/api/me/identities/oidc/{id}"): _any("owner"),
    ("GET", "/invite/complete"): _system("public", modes=NETWORK_ONLY),
    ("HEAD", "/invite/complete"): _system("public", modes=NETWORK_ONLY),
    ("POST", "/invite/complete"): _system("public", modes=NETWORK_ONLY),
    ("GET", "/admin/idp"): _system("admin"),
    ("HEAD", "/admin/idp"): _system("admin"),
    ("GET", "/api/admin/idp"): _system("admin"),
    ("HEAD", "/api/admin/idp"): _system("admin"),
    ("PUT", "/api/admin/idp"): _system("admin"),
    # 통합 콘솔 (admin/ 및 settings/ 기능 경로 분리, D1–D3)
    ("GET", "/admin"): _system("admin"),
    ("HEAD", "/admin"): _system("admin"),
    ("GET", "/admin/providers"): _system("admin"),
    ("HEAD", "/admin/providers"): _system("admin"),
    ("GET", "/admin/limits"): _system("admin"),
    ("HEAD", "/admin/limits"): _system("admin"),
    ("GET", "/admin/themes"): _system("admin"),
    ("HEAD", "/admin/themes"): _system("admin"),
    ("GET", "/admin/tokens"): _system("admin"),
    ("HEAD", "/admin/tokens"): _system("admin"),
    ("GET", "/admin/mcp"): _system("admin"),
    ("HEAD", "/admin/mcp"): _system("admin"),
    ("GET", "/api/admin/mcp/telemetry"): _system("admin"),
    ("HEAD", "/api/admin/mcp/telemetry"): _system("admin"),
    ("GET", "/settings"): _system("owner"),
    ("HEAD", "/settings"): _system("owner"),
    ("GET", "/settings/account"): _system("owner"),
    ("HEAD", "/settings/account"): _system("owner"),
    ("GET", "/settings/keys"): _system("owner"),
    ("HEAD", "/settings/keys"): _system("owner"),
    ("GET", "/settings/themes"): _system("owner"),
    ("HEAD", "/settings/themes"): _system("owner"),
    ("GET", "/settings/tokens"): _system("owner"),
    ("HEAD", "/settings/tokens"): _system("owner"),
    ("GET", "/settings/reader"): _system("owner"),
    ("HEAD", "/settings/reader"): _system("owner"),
    ("GET", "/settings/connections"): _system("owner"),
    ("HEAD", "/settings/connections"): _system("owner"),
    ("GET", "/settings/feeds"): _system("owner"),
    ("HEAD", "/settings/feeds"): _system("owner"),
    ("GET", "/settings/tags"): _system("owner"),
    ("HEAD", "/settings/tags"): _system("owner"),
    ("GET", "/settings/rules"): _system("owner"),
    ("HEAD", "/settings/rules"): _system("owner"),
    ("GET", "/settings/outbound"): _system("owner"),
    ("HEAD", "/settings/outbound"): _system("owner"),
    ("GET", "/settings/opml"): _system("owner"),
    ("HEAD", "/settings/opml"): _system("owner"),
    ("GET", "/settings/mcp"): _system("owner"),
    ("HEAD", "/settings/mcp"): _system("owner"),
    ("GET", "/settings/webmcp"): _system("owner"),
    ("HEAD", "/settings/webmcp"): _system("owner"),
}


# 정확 키와 경로 매개변수 키(§9.1). 매개변수 키는 Starlette 라우터와 같은 정규식으로 컴파일한다.
_EXACT_ROUTE_POLICY: dict[RouteKey, RouteRule] = {
    key: rule for key, rule in ROUTE_POLICY.items() if "{" not in key[1]
}
_PARAM_ROUTE_POLICY: tuple[tuple[str, re.Pattern[str], RouteRule], ...] = tuple(
    (method, compile_path(path)[0], rule)
    for (method, path), rule in ROUTE_POLICY.items()
    if "{" in path
)


class InvalidJSONBody(HTTPException):
    def __init__(self, detail: str = "invalid json") -> None:
        super().__init__(status_code=400, detail=detail)


class RequestBodyTooLarge(HTTPException):
    def __init__(self) -> None:
        super().__init__(status_code=413, detail="request body too large")


def _setting(settings: Any, name: str) -> Any:
    value = getattr(settings, name, None)
    if value is None:
        raise ValueError(f"{name} is required")
    return value


def _parse_dns_name(host: str) -> str:
    host = host.lower()
    if host.endswith(".") or not _DNS_NAME_RE.fullmatch(host):
        raise ValueError("hostname must be an ASCII DNS name without a trailing dot")
    return host


def _parse_url(
    raw: str, *, environment: str, origin_only: bool = False
) -> tuple[str, str, str]:
    if not raw or raw != raw.strip():
        raise ValueError("URL is required and must not contain outer whitespace")
    if raw in {"*", "null"}:
        raise ValueError("wildcard and null origins are not allowed")
    try:
        parsed = urlsplit(raw)
        port = parsed.port
    except ValueError as exc:
        raise ValueError(f"invalid URL: {raw!r}") from exc
    if parsed.scheme not in {"http", "https"}:
        raise ValueError("URL scheme must be http or https")
    if parsed.username is not None or parsed.password is not None:
        raise ValueError("URL userinfo is not allowed")
    if parsed.query or parsed.fragment:
        raise ValueError("URL query and fragment are not allowed")
    if origin_only:
        if parsed.path:
            raise ValueError("CORS origins must not contain a path or trailing slash")
    elif parsed.path not in {"", "/"}:
        raise ValueError("CLAIRE_PUBLIC_URL must use the service root path")
    if parsed.hostname is None:
        raise ValueError("URL hostname is required")
    if port is not None and port < 1:
        raise ValueError("URL port must be in the 1-65535 range")

    host = parsed.hostname.lower()
    try:
        address = ipaddress.ip_address(host)
    except ValueError:
        address = None

    if not origin_only:
        if environment == "development":
            if not isinstance(address, ipaddress.IPv4Address) or address.is_unspecified:
                raise ValueError("development public URL must use a concrete IPv4 address")
            if parsed.scheme != "http":
                raise ValueError("development public URL must use http")
        else:
            if address is not None:
                raise ValueError("production public URL must use a DNS hostname")
            _parse_dns_name(host)
            if parsed.scheme != "https":
                raise ValueError("production public URL must use https")
    else:
        if address is None:
            _parse_dns_name(host)
        if environment == "production" and parsed.scheme != "https":
            raise ValueError("production CORS origins must use https")

    if (parsed.scheme == "http" and port == 80) or (
        parsed.scheme == "https" and port == 443
    ):
        port = None
    authority_host = f"[{host}]" if isinstance(address, ipaddress.IPv6Address) else host
    authority = authority_host if port is None else f"{authority_host}:{port}"
    origin = f"{parsed.scheme}://{authority}"
    return parsed.scheme, authority, origin


def _split_origins(value: Any) -> tuple[str, ...]:
    if isinstance(value, str):
        items = value.split(",")
    elif isinstance(value, (tuple, list, set, frozenset)):
        items = list(value)
    else:
        raise ValueError("cors_allowed_origins must be a comma-separated string or sequence")
    return tuple(str(item).strip() for item in items if str(item).strip())


@dataclass(frozen=True, slots=True)
class WebRuntimeConfig:
    environment: Literal["development", "production"]
    public_origin: str
    expected_authority: str
    cors_allowed_origins: frozenset[str]
    secure_cookie: bool
    anonymous_readonly: bool
    owner_token: str = field(repr=False)
    readonly_token: str = field(repr=False)
    collaborator_token: str = field(repr=False)
    db_file: Any = field(repr=False)
    ga_measurement_id: str = ""
    cloudflare_ips_only: bool = False
    data_dir: Path | None = None
    # 클라이언트 IP 판정(`client_ip`)에서 X-Forwarded-For를 믿을 바로 앞 피어 대역.
    # ``CLAIRE_TRUSTED_PROXIES``와, ``cloudflare_ips_only``이면 ``CLOUDFLARE_NETWORKS``를 합친 것이다.
    trusted_proxies: tuple[ipaddress.IPv4Network | ipaddress.IPv6Network, ...] = ()
    # 역할 호스트(§5.1). ``from_settings``가 ``HostRoles.from_settings``로 검증해 채운다. None이면 app만 있는 것으로 본다.
    host_roles: HostRoles | None = None

    @classmethod
    def from_settings(cls, settings: Any) -> WebRuntimeConfig:
        raw_environment = str(_setting(settings, "environment"))
        environment = raw_environment.strip().lower()
        if raw_environment != environment:
            raise ValueError("CLAIRE_ENVIRONMENT must use its canonical lowercase value")
        if environment not in {"development", "production"}:
            raise ValueError("CLAIRE_ENVIRONMENT must be development or production")

        raw_pub = getattr(settings, "public_url", None)
        if not raw_pub:
            fqdn_val = getattr(settings, "fqdn", "") or getattr(settings, "effective_fqdn", "")
            if fqdn_val:
                scheme = "https" if environment == "production" else "http"
                raw_pub = f"{scheme}://{str(fqdn_val).strip()}/"
        if not raw_pub:
            raw_pub = _setting(settings, "public_url")

        _, authority, public_origin = _parse_url(
            str(raw_pub), environment=environment
        )

        owner_token = str(_setting(settings, "inject_token"))
        if not owner_token or owner_token != owner_token.strip():
            raise ValueError(
                "CLAIRE_INJECT_TOKEN must not be empty or contain outer whitespace"
            )
        if not dbm.plausible_session_token(owner_token):
            raise ValueError(
                "CLAIRE_INJECT_TOKEN must be a 32-128 character URL-safe token"
            )
        readonly_token = str(getattr(settings, "readonly_token", "") or "")
        if readonly_token != readonly_token.strip():
            raise ValueError("CLAIRE_READONLY_TOKEN must not contain outer whitespace")
        if readonly_token and not dbm.plausible_session_token(readonly_token):
            raise ValueError(
                "CLAIRE_READONLY_TOKEN must be a 32-128 character URL-safe token"
            )
        if readonly_token and _constant_equal(owner_token, readonly_token):
            raise ValueError("owner and readonly tokens must be different")

        collaborator_token = str(getattr(settings, "collaborator_token", "") or "")
        if collaborator_token != collaborator_token.strip():
            raise ValueError("CLAIRE_COLLABORATOR_TOKEN must not contain outer whitespace")
        if collaborator_token and not dbm.plausible_session_token(collaborator_token):
            raise ValueError(
                "CLAIRE_COLLABORATOR_TOKEN must be a 32-128 character URL-safe token"
            )
        if collaborator_token and _constant_equal(owner_token, collaborator_token):
            raise ValueError("owner and collaborator tokens must be different")
        if (
            collaborator_token
            and readonly_token
            and _constant_equal(readonly_token, collaborator_token)
        ):
            raise ValueError("readonly and collaborator tokens must be different")

        anonymous_readonly = getattr(settings, "anonymous_readonly", False)
        if not isinstance(anonymous_readonly, bool):
            raise ValueError("CLAIRE_ANONYMOUS_READONLY must be a boolean")

        origins: set[str] = set()
        for raw_origin in _split_origins(
            getattr(settings, "cors_allowed_origins", "") or ""
        ):
            _, _, origin = _parse_url(
                raw_origin, environment=environment, origin_only=True
            )
            origins.add(origin)

        ga_id = str(
            getattr(
                settings,
                "effective_ga_measurement_id",
                getattr(settings, "ga_measurement_id", ""),
            )
            or ""
        ).strip()

        cf_ips_only = bool(getattr(settings, "cloudflare_ips_only", False))
        trusted_proxies = parse_trusted_proxies(getattr(settings, "trusted_proxies", "") or "")
        if cf_ips_only:
            trusted_proxies = trusted_proxies + CLOUDFLARE_NETWORKS

        raw_data_dir = getattr(settings, "data_dir", None)
        if raw_data_dir is not None:
            data_dir = Path(raw_data_dir)
        else:
            db_val = getattr(settings, "db_file", None)
            data_dir = Path(db_val).parent if db_val else None

        from .host_router import HostRoles

        host_roles = HostRoles.from_settings(
            settings, environment=environment, app_authority=authority
        )

        return cls(
            environment=environment,
            public_origin=public_origin,
            expected_authority=authority,
            cors_allowed_origins=frozenset(origins),
            secure_cookie=environment == "production",
            anonymous_readonly=anonymous_readonly,
            owner_token=owner_token,
            readonly_token=readonly_token,
            collaborator_token=collaborator_token,
            db_file=_setting(settings, "db_file"),
            ga_measurement_id=ga_id,
            cloudflare_ips_only=cf_ips_only,
            data_dir=data_dir,
            trusted_proxies=trusted_proxies,
            host_roles=host_roles,
        )


async def read_json_body(
    request: Request, *, max_bytes: int = MAX_REQUEST_BODY
) -> dict[str, Any]:
    """JSON body를 제한 크기 안에서 읽는다.

    InvalidJSONBody(400)와 RequestBodyTooLarge(413)는 Starlette의 HTTPException 처리기를 통과하므로 handler가 넓은 ``except Exception``으로 덮어쓰면 안 된다.
    """

    content_type = request.headers.get("content-type", "").split(";", 1)[0].strip().lower()
    if content_type != "application/json" and not content_type.endswith("+json"):
        raise InvalidJSONBody("content-type must be application/json")

    body = bytearray()
    async for chunk in request.stream():
        body.extend(chunk)
        if len(body) > max_bytes:
            raise RequestBodyTooLarge()
    try:
        value = json.loads(body)
    except (json.JSONDecodeError, UnicodeDecodeError, TypeError) as exc:
        raise InvalidJSONBody() from exc
    if not isinstance(value, dict):
        raise InvalidJSONBody("json body must be an object")
    return value


def request_auth_scope(request: Request) -> str | None:
    return getattr(request.state, _AUTH_SCOPE_KEY, None)


def request_origin_kind(request: Request) -> str:
    """``CORSPolicyMiddleware``가 판정한 ``Origin`` 종류: ``none``, ``same``, ``cross``. 판정 전이면 ``RuntimeError``."""
    kind = getattr(request.state, _ORIGIN_KIND_KEY, None)
    if kind not in {"none", "same", "cross"}:
        raise RuntimeError("origin kind is not set; CORSPolicyMiddleware did not run for this request")
    return kind


def request_id(request: Request) -> str:
    return str(getattr(request.state, _REQUEST_ID_KEY, "-"))


def _raw_headers(scope: Scope, name: bytes) -> list[bytes]:
    return [value for key, value in scope.get("headers", []) if key.lower() == name]


def _parse_authority(raw: str) -> str | None:
    if (
        not raw
        or raw != raw.strip()
        or any(ord(char) < 33 or ord(char) == 127 for char in raw)
        or any(char in raw for char in "/\\,@?#")
    ):
        return None
    match = _AUTHORITY_RE.fullmatch(raw)
    if match is None:
        return None
    host = match.group("host").lower()
    port_raw = match.group("port")
    if port_raw is not None and not (1 <= int(port_raw) <= 65535):
        return None
    try:
        ipaddress.IPv4Address(host)
    except ValueError:
        try:
            _parse_dns_name(host)
        except ValueError:
            return None
    return host if port_raw is None else f"{host}:{int(port_raw)}"


def _parse_bearer(scope: Scope) -> tuple[bool, str | None]:
    values = _raw_headers(scope, b"authorization")
    if not values:
        return False, None
    if len(values) != 1:
        return True, None
    raw = values[0].decode("latin-1")
    match = _BEARER_RE.fullmatch(raw)
    return True, match.group(1) if match else None


def _constant_equal(candidate: str, expected: str) -> bool:
    if not candidate or not expected:
        return False
    return hmac.compare_digest(candidate.encode("utf-8"), expected.encode("utf-8"))


# 세션 쿠키 이름(§5.2, Rev. 2.3). 운영 환경(``secure_cookie``)은 ``__Host-`` 접두로 서브도메인의 쿠키 토싱을 막는다.
# development(HTTP)는 ``__Host-``를 쓸 수 없으므로 옛 이름을 유지한다.
SESSION_COOKIE_NAME = "__Host-claire_session"
LEGACY_SESSION_COOKIE_NAME = "claire_session"
# P3 G9에서 True로 바뀌며 운영 환경 과도기 폴백이 완전히 제거된다(§5.2, §17.2).
LEGACY_COOKIE_FALLBACK_REMOVED = True
_SESSION_COOKIE_NAMES = (SESSION_COOKIE_NAME, LEGACY_SESSION_COOKIE_NAME)


def session_cookie_name(config: WebRuntimeConfig) -> str:
    return SESSION_COOKIE_NAME if config.secure_cookie else LEGACY_SESSION_COOKIE_NAME


def set_session_cookie(
    response: Response,
    config: WebRuntimeConfig,
    token: str,
) -> None:
    # ``__Host-``: Secure, Path=/, Domain 없음이어야 브라우저가 받는다.
    response.set_cookie(
        session_cookie_name(config),
        token,
        max_age=int(dbm.SESSION_TTL),
        path="/",
        secure=config.secure_cookie,
        httponly=True,
        samesite="lax",
    )


def _delete_cookie(response: Response, name: str, config: WebRuntimeConfig) -> None:
    response.delete_cookie(
        name,
        path="/",
        secure=config.secure_cookie,
        httponly=True,
        samesite="lax",
    )


def clear_session_cookie(
    response: Response,
    config: WebRuntimeConfig,
) -> None:
    """세션 쿠키를 지운다. 운영 환경은 ``__Host-``와 과도기의 host-only ``claire_session``을 함께 지운다."""
    _delete_cookie(response, session_cookie_name(config), config)
    if config.secure_cookie:
        # TODO(P3 G9): 과도기 폴백을 없앨 때 이 삭제도 없앤다.
        _delete_cookie(response, LEGACY_SESSION_COOKIE_NAME, config)


def _set_cookie_headers(response: Response) -> list[bytes]:
    return [value for name, value in response.raw_headers if name.lower() == b"set-cookie"]


def _session_cookie_header(config: WebRuntimeConfig, token: str) -> bytes:
    response = Response()
    set_session_cookie(response, config, token)
    return _set_cookie_headers(response)[0]


def _clear_session_cookie_headers(config: WebRuntimeConfig) -> list[bytes]:
    response = Response()
    clear_session_cookie(response, config)
    return _set_cookie_headers(response)


def _clear_legacy_session_cookie_header(config: WebRuntimeConfig) -> bytes:
    response = Response()
    _delete_cookie(response, LEGACY_SESSION_COOKIE_NAME, config)
    return _set_cookie_headers(response)[0]


def _sets_session_cookie(headers: list[tuple[bytes, bytes]]) -> bool:
    """응답 헤더에 세션 쿠키(``__Host-claire_session`` 또는 ``claire_session``)를 설정하거나 지우는 ``Set-Cookie``가 이미 있는지."""
    names = {name.encode("ascii") for name in _SESSION_COOKIE_NAMES}
    return any(
        name.lower() == b"set-cookie"
        and value.split(b"=", 1)[0].strip() in names
        for name, value in headers
    )


@dataclass(frozen=True, slots=True)
class _SessionCookieRead:
    """요청의 세션 쿠키 판독 결과.

    ``present``: 세션 쿠키로 볼 값이 있었다(무효여도). ``token``: 검증할 값. 모호하거나 깨졌으면 None.
    ``legacy``: ``__Host-`` 없이 옛 이름 ``claire_session``에서 읽었다. 운영 환경에서는 과도기 폴백이므로 검증에 성공하면 ``__Host-``로 재발급한다.
    """

    present: bool
    token: str | None
    legacy: bool = False


def _cookie_occurrences(decoded: list[str], name: str) -> int:
    return sum(
        1
        for header in decoded
        for item in header.split(";")
        if item.strip().split("=", 1)[0].strip() == name
    )


def _cookie_value(decoded: list[str], name: str) -> str | None:
    try:
        cookie = SimpleCookie()
        for value in decoded:
            cookie.load(value)
    except Exception:  # noqa: BLE001
        return None
    morsel = cookie.get(name)
    return morsel.value if morsel is not None else None


def _read_session_cookies(scope: Scope) -> _SessionCookieRead:
    """세션 쿠키 판독의 단일 지점(§5.2 쿠키 토싱 방어, S11).

    - ``__Host-claire_session``이 정확히 1개면 그것만 본다. 함께 온 ``claire_session``(서브도메인이 ``Domain=.{site}``로 심었을 수 있다)은 완전히 무시한다. 중복으로 보거나 승격하지 않는다.
    - ``__Host-claire_session``이 2개 이상이면 무효 자격이다.
    - ``__Host-claire_session``이 없을 때만 ``claire_session``을 본다. 정확히 1개여야 하고, 2개 이상이면 무효 자격이다.
      development에서는 이것이 정상 경로이고, 운영 환경에서는 과도기 폴백이다.
    """
    values = _raw_headers(scope, b"cookie")
    if not values:
        return _SessionCookieRead(False, None)
    decoded = [value.decode("latin-1") for value in values]
    host_count = _cookie_occurrences(decoded, SESSION_COOKIE_NAME)
    if host_count == 1:
        return _SessionCookieRead(True, _cookie_value(decoded, SESSION_COOKIE_NAME))
    if host_count > 1:
        return _SessionCookieRead(True, None)
    # TODO(P3 G9): 운영 환경의 ``claire_session`` 과도기 폴백을 없앤다(이 분기와 재발급·삭제 헤더).
    legacy_count = _cookie_occurrences(decoded, LEGACY_SESSION_COOKIE_NAME)
    if legacy_count == 0:
        return _SessionCookieRead(False, None)
    if legacy_count != 1:
        return _SessionCookieRead(True, None, legacy=True)
    return _SessionCookieRead(True, _cookie_value(decoded, LEGACY_SESSION_COOKIE_NAME), legacy=True)


def read_session_cookie(scope: Scope) -> tuple[bool, str | None]:
    """``(쿠키가 있었는지, 검증할 토큰)``. 규칙은 ``_read_session_cookies``. 세션 쿠키를 직접 읽는 코드는 모두 이 함수를 쓴다."""
    result = _read_session_cookies(scope)
    return result.present, result.token


@dataclass(frozen=True, slots=True)
class _Credential:
    """검증을 통과한 자격증명과 그 계정. 토큰 값은 담지 않는다(설계 §8.3)."""

    scope: str
    account_id: int
    instance_role: Literal["admin", "member"]
    credential_id: str
    authenticated_at: float | None = None

    def principal(self, channel: Literal["cookie", "bearer", "x-session"]) -> Principal:
        return Principal(
            account_id=self.account_id,
            instance_role=self.instance_role,
            scope=self.scope,  # type: ignore[arg-type]  # 허용 판정이 scope 값을 거른 뒤에만 쓴다
            channel=channel,
            credential_id=self.credential_id,
            # 사람이 인증한 시각은 브라우저 세션 채널에서만 의미가 있다(§8.5).
            authenticated_at=(
                self.authenticated_at if channel in ("cookie", "x-session") else None
            ),
        )


def _env_token_credential(scope: str) -> _Credential:
    """환경변수 토큰은 인스턴스 설정이므로 관리자 계정의 것이다(§8.3). DB를 읽지 않는다."""
    return _Credential(
        scope=scope,
        account_id=accounts_store.ADMIN_ACCOUNT_ID,
        instance_role="admin",
        credential_id=f"env:{scope}",
    )


def _bind_account(
    conn: Any,
    *,
    scope: str,
    account_id: int,
    credential_id: str,
    authenticated_at: float | None,
) -> _Credential | None:
    """자격증명의 계정을 읽는다. 계정이 없거나 자격증명을 받지 않는 상태면 None(없는 자격증명과 같다, §8.2)."""
    try:
        account = accounts_store.AccountStore(conn).get(account_id)
    except accounts_store.AccountNotFound:
        log.warning("credential %s refers to a missing account", credential_id)
        return None
    if account.status not in accounts_store.CREDENTIAL_ACCEPTING_STATUSES:
        return None
    return _Credential(
        scope=scope,
        account_id=account.id,
        instance_role=account.role,
        credential_id=credential_id,
        authenticated_at=authenticated_at,
    )


async def _validate_session(config: WebRuntimeConfig, token: str) -> _Credential | None:
    if not dbm.plausible_session_token(token):
        return None

    def _validate() -> _Credential | None:
        conn = dbm.connect_existing(config.db_file)
        try:
            binding = accounts_store.validate_session_binding(conn, token)
            if binding is None:
                return None
            return _bind_account(
                conn,
                scope=binding.scope,
                account_id=binding.account_id,
                credential_id=binding.credential_id,
                authenticated_at=binding.authenticated_at,
            )
        finally:
            conn.close()

    return await asyncio.to_thread(_validate)


async def _validate_oauth_token(config: WebRuntimeConfig, token: str) -> _Credential | None:
    if not token or len(token) < 20:
        return None
    if not Path(config.db_file).is_file():
        return None

    def _validate() -> _Credential | None:
        try:
            conn = dbm.connect_existing(config.db_file)
            try:
                binding = accounts_store.validate_oauth_binding(conn, token)
                if binding is None:
                    return None
                return _bind_account(
                    conn,
                    scope=binding.scope,
                    account_id=binding.account_id,
                    credential_id=binding.credential_id,
                    authenticated_at=None,
                )
            finally:
                conn.close()
        except Exception:
            return None

    return await asyncio.to_thread(_validate)


async def _validate_api_token(config: WebRuntimeConfig, token: str) -> _Credential | None:
    if not token or len(token) < 16:
        return None
    data_dir = config.data_dir or (Path(config.db_file).parent if config.db_file else None)
    try:
        from ..store.token_store import get_token_store

        store = get_token_store(data_dir=data_dir)
        entry = store.validate_api_token(token)
    except Exception:
        return None
    if entry is None:
        return None

    def _bind() -> _Credential | None:
        conn = dbm.connect_existing(config.db_file)
        try:
            return _bind_account(
                conn,
                scope=str(entry["scope"]),
                account_id=int(entry["account_id"]),
                credential_id=f"api:{int(entry['id'])}",
                authenticated_at=None,
            )
        finally:
            conn.close()

    return await asyncio.to_thread(_bind)


async def _bootstrap_session(
    config: WebRuntimeConfig, token: str
) -> tuple[str, str] | None:
    if not dbm.plausible_session_token(token):
        return None

    def _exchange() -> tuple[str, str] | None:
        conn = dbm.connect_existing(config.db_file)
        try:
            exchanged = dbm.exchange_session_token(
                conn,
                token,
                scopes=("owner", "collaborator"),
            )
            if exchanged is not None:
                return exchanged
            scope = dbm.validate_session_scope(
                conn,
                token,
                scopes=("readonly",),
            )
            return (scope, token) if scope == "readonly" else None
        finally:
            conn.close()

    return await asyncio.to_thread(_exchange)


async def _send_response(response: Response, scope: Scope, receive: Receive, send: Send) -> None:
    await response(scope, receive, send)


class CloudflareIPFilterMiddleware:
    """공인 대역 IP 중 Cloudflare 공식 IP 대역의 요청만 허용하는 미들웨어.

    사설 망/루프백/링크로컬 IP 등 비공인 대역은 내부 통신, 헬스체크 및 로컬 프록시 연동을 위해 필터링하지 않고 통과시킨다.
    """

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        client = scope.get("client")
        if client and client[0]:
            raw_host = client[0].strip()
            try:
                ip = ipaddress.ip_address(raw_host)
            except ValueError:
                ip = None

            if ip is not None and ip.is_global:
                if not is_cloudflare_ip(ip):
                    await _send_response(
                        PlainTextResponse(
                            "Forbidden: Direct public IP access is not allowed",
                            status_code=403,
                        ),
                        scope,
                        receive,
                        send,
                    )
                    return

        await self.app(scope, receive, send)


class CORSPolicyMiddleware:
    _ALLOWED_METHODS = frozenset({"GET", "HEAD", "POST"})
    _ALLOWED_HEADERS = frozenset({"authorization", "content-type"})

    """app 호스트 전용. 테마 FQDN origin은 더 이상 같은 origin으로 보지 않는다(§5.3). usercontent·media 하위 앱에는 씌우지 않는다(§5.2)."""

    def __init__(self, app: ASGIApp, config: WebRuntimeConfig) -> None:
        self.app = app
        self.config = config

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        state = scope.setdefault("state", {})
        origins = _raw_headers(scope, b"origin")
        if len(origins) > 1:
            await _send_response(
                PlainTextResponse("Forbidden", status_code=403), scope, receive, send
            )
            return

        origin = origins[0].decode("latin-1") if origins else None
        cross_origin: str | None = None
        if origin is None:
            state[_ORIGIN_KIND_KEY] = "none"
        elif origin == self.config.public_origin:
            state[_ORIGIN_KIND_KEY] = "same"
        elif origin in self.config.cors_allowed_origins:
            state[_ORIGIN_KIND_KEY] = "cross"
            cross_origin = origin
        else:
            await _send_response(
                PlainTextResponse("Forbidden", status_code=403), scope, receive, send
            )
            return

        is_preflight = (
            scope["method"] == "OPTIONS"
            and bool(_raw_headers(scope, b"access-control-request-method"))
        )
        if is_preflight:
            requested_methods = _raw_headers(scope, b"access-control-request-method")
            requested_headers = _raw_headers(scope, b"access-control-request-headers")
            if (
                cross_origin is None
                or len(requested_methods) != 1
                or len(requested_headers) > 1
            ):
                await _send_response(
                    PlainTextResponse("Forbidden", status_code=403), scope, receive, send
                )
                return
            method = requested_methods[0].decode("latin-1").upper()
            headers = {
                item.strip().lower()
                for item in (
                    requested_headers[0].decode("latin-1").split(",")
                    if requested_headers
                    else []
                )
                if item.strip()
            }
            # 경로는 PrefixParserMiddleware가 접두를 뗀 정규 경로다(§6.7).
            route_rule = lookup_route_rule(method, scope.get("path", ""))
            if (
                method not in self._ALLOWED_METHODS
                or route_rule is None
                or not headers <= self._ALLOWED_HEADERS
            ):
                await _send_response(
                    PlainTextResponse("Forbidden", status_code=403), scope, receive, send
                )
                return
            response = Response(status_code=204)
            response.headers["Access-Control-Allow-Origin"] = cross_origin
            response.headers["Access-Control-Allow-Methods"] = "GET, HEAD, POST"
            response.headers["Access-Control-Allow-Headers"] = "Authorization, Content-Type"
            response.headers["Access-Control-Max-Age"] = "600"
            response.headers["Vary"] = "Origin"
            await _send_response(response, scope, receive, send)
            return

        if cross_origin is None:
            await self.app(scope, receive, send)
            return
        if scope["method"] not in self._ALLOWED_METHODS:
            await _send_response(
                PlainTextResponse("Forbidden", status_code=403), scope, receive, send
            )
            return

        async def cors_send(message: Message) -> None:
            if message["type"] == "http.response.start":
                headers = list(message.get("headers", []))
                headers.append((b"access-control-allow-origin", cross_origin.encode("latin-1")))
                headers.append((b"vary", b"Origin"))
                message = {**message, "headers": headers}
            await send(message)

        await self.app(scope, receive, cors_send)


class BodyLimitMiddleware:
    def __init__(self, app: ASGIApp, max_bytes: int = MAX_REQUEST_BODY) -> None:
        self.app = app
        self.max_bytes = max_bytes

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        lengths = _raw_headers(scope, b"content-length")
        transfer_encodings = _raw_headers(scope, b"transfer-encoding")
        length = 0
        if len(lengths) > 1:
            await _send_response(
                PlainTextResponse("Bad Request", status_code=400), scope, receive, send
            )
            return
        if lengths:
            try:
                raw_length = lengths[0].decode("ascii")
                length = int(raw_length) if raw_length.isdigit() else -1
            except (UnicodeDecodeError, ValueError):
                length = -1
            if length < 0:
                await _send_response(
                    PlainTextResponse("Bad Request", status_code=400), scope, receive, send
                )
                return
            if length > self.max_bytes:
                await _send_response(
                    PlainTextResponse("Request Entity Too Large", status_code=413),
                    scope,
                    receive,
                    send,
                )
                return

        if len(transfer_encodings) > 1 or (transfer_encodings and lengths):
            await _send_response(
                PlainTextResponse("Bad Request", status_code=400), scope, receive, send
            )
            return
        if transfer_encodings:
            try:
                transfer_encoding = transfer_encodings[0].decode("ascii").lower()
            except UnicodeDecodeError:
                transfer_encoding = ""
            if transfer_encoding != "chunked":
                await _send_response(
                    PlainTextResponse("Bad Request", status_code=400),
                    scope,
                    receive,
                    send,
                )
                return

        bodyless = scope["method"] in {"GET", "HEAD", "OPTIONS"} or (
            scope["method"] == "POST" and scope.get("path") == "/dedup/scan"
        )
        if bodyless and (transfer_encodings or length > 0):
            await _send_response(
                PlainTextResponse("Bad Request", status_code=400), scope, receive, send
            )
            return

        consumed = 0
        response_started = False

        async def limited_receive() -> Message:
            nonlocal consumed
            message = await receive()
            if message["type"] == "http.request":
                consumed += len(message.get("body", b""))
                if consumed > self.max_bytes:
                    raise RequestBodyTooLarge()
            return message

        async def tracking_send(message: Message) -> None:
            nonlocal response_started
            if message["type"] == "http.response.start":
                response_started = True
            await send(message)

        try:
            await self.app(scope, limited_receive, tracking_send)
        except RequestBodyTooLarge:
            if response_started:
                raise
            await _send_response(
                PlainTextResponse("Request Entity Too Large", status_code=413),
                scope,
                receive,
                send,
            )


class ErrorBoundaryMiddleware:
    """Starlette 바깥 인증/DB 경계의 예외를 안전한 500으로 바꾼다."""

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        response_started = False

        async def tracking_send(message: Message) -> None:
            nonlocal response_started
            if message["type"] == "http.response.start":
                response_started = True
            await send(message)

        try:
            await self.app(scope, receive, tracking_send)
        except Exception as exc:
            if response_started:
                raise
            request_id = scope.get("state", {}).get(_REQUEST_ID_KEY, "-")
            log.warning(
                "request failed id=%s error_type=%s",
                request_id,
                type(exc).__name__,
            )
            await _send_response(
                JSONResponse(
                    {"error": "internal server error"},
                    status_code=500,
                ),
                scope,
                receive,
                send,
            )


def _mcp_auth_error_response(
    status_code: int = 401,
    error: str = "invalid_token",
    description: str = "Authentication required",
    resource_metadata: str | None = None,
) -> Response:
    body = json.dumps({"error": error, "error_description": description}).encode("utf-8")
    meta_param = f', resource_metadata="{resource_metadata}"' if resource_metadata else ""
    www_auth = f'Bearer error="{error}", error_description="{description}"{meta_param}'
    return Response(
        content=body,
        status_code=status_code,
        media_type="application/json",
        headers={
            "WWW-Authenticate": www_auth,
            "Content-Length": str(len(body)),
        },
    )


class AuthenticationMiddleware:
    def __init__(
        self,
        app: ASGIApp,
        config: WebRuntimeConfig,
        namespace_directory: NamespaceDirectory | None = None,
    ) -> None:
        self.app = app
        self.config = config
        # 모드(§4)를 읽는다. 없으면 싱글 모드다(DB 없는 단위 시험).
        self.namespace_directory = namespace_directory

    def _mode(self) -> InstanceMode:
        if self.namespace_directory is None:
            return "single"
        return self.namespace_directory.mode()

    async def _magic_link_destination(self, cookie_token: str) -> str:
        """§6.6: 싱글 모드는 ``/``, 네트워크 모드는 세션이 귀속된 계정의 홈(``/@{username}/``)."""
        mode = self._mode()
        if mode == "single":
            return account_home_path(mode, None)

        def _username() -> str | None:
            conn = dbm.connect_existing(self.config.db_file, readonly=True)
            try:
                row = conn.execute(
                    "SELECT account_id FROM auth_sessions WHERE session_token=?", (cookie_token,)
                ).fetchone()
                if row is None or row[0] is None:
                    return None
                try:
                    return accounts_store.AccountStore(conn).get(int(row[0])).username
                except accounts_store.AccountNotFound:
                    return None
            finally:
                conn.close()

        username = await asyncio.to_thread(_username)
        if not username:
            # 계정 홈을 만들 수 없으면 인스턴스 루트로 보낸다(관리자 이름으로 대신하지 않는다).
            return "/"
        return account_home_path(mode, username)

    def _record_mcp_auth_failure(
        self,
        status_code: int = 401,
        error: str = "invalid_token",
        description: str = "Authentication required",
    ) -> None:
        if not self.config.data_dir:
            return
        try:
            from ..store.telemetry import record_mcp_telemetry

            record_mcp_telemetry(
                self.config.data_dir,
                call_type="http_mcp",
                tool_name=None,
                status="UNAUTHORIZED" if status_code == 401 else "FORBIDDEN" if status_code == 403 else "ERROR",
                error_code=status_code,
                error_message=f"{error}: {description}" if description else error,
            )
        except Exception:
            pass

    async def _send_mcp_error(
        self,
        scope: Scope,
        receive: Receive,
        send: Send,
        status_code: int = 401,
        error: str = "invalid_token",
        description: str = "Authentication required",
        resource_metadata: str | None = None,
    ) -> None:
        self._record_mcp_auth_failure(status_code, error, description)
        await _send_response(
            _mcp_auth_error_response(
                status_code, error, description, resource_metadata=resource_metadata
            ),
            scope,
            receive,
            send,
        )

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        method = str(scope.get("method", "")).upper()
        path = scope.get("path", "")
        rule = lookup_route_rule(method, path)
        if rule is None:
            await _send_response(
                PlainTextResponse("Not Found", status_code=404), scope, receive, send
            )
            return

        state = scope.setdefault("state", {})
        parsed = state.get(PARSED_PREFIX_STATE_KEY)
        has_prefix = isinstance(parsed, ParsedPrefix) and bool(parsed.prefix)
        access: AccessLevel = rule.access
        if rule.surface == "root" and not has_prefix and self._mode() == "network":
            # 네트워크 모드 루트는 공개 디렉터리(§4.4, §9.1). P2에서는 308·404만 한다(O8).
            access = "public"
        if path == "/" and not has_prefix and method in {"GET", "HEAD"}:
            query = parse_qs(
                scope.get("query_string", b"").decode("ascii", "ignore"),
                keep_blank_values=True,
            )
            token_values = query.get("t")
            if token_values is not None:
                if method != "GET":
                    await _send_response(
                        PlainTextResponse("Not Found", status_code=404),
                        scope,
                        receive,
                        send,
                    )
                    return
                if state.get(_ORIGIN_KIND_KEY) == "cross":
                    await _send_response(
                        PlainTextResponse("Forbidden", status_code=403),
                        scope,
                        receive,
                        send,
                    )
                    return
                if len(token_values) != 1 or not token_values[0]:
                    if self.config.anonymous_readonly:
                        response = RedirectResponse("/", status_code=302)
                        clear_session_cookie(response, self.config)
                        response.headers["Cache-Control"] = "no-store"
                        response.headers["Referrer-Policy"] = "no-referrer"
                        await _send_response(response, scope, receive, send)
                        return
                    await _send_response(
                        PlainTextResponse("Not Found", status_code=404),
                        scope,
                        receive,
                        send,
                    )
                    return
                token = token_values[0]
                exchanged = await _bootstrap_session(self.config, token)
                if exchanged is None:
                    if self.config.anonymous_readonly:
                        response = RedirectResponse("/", status_code=302)
                        clear_session_cookie(response, self.config)
                        response.headers["Cache-Control"] = "no-store"
                        response.headers["Referrer-Policy"] = "no-referrer"
                        await _send_response(response, scope, receive, send)
                        return
                    await _send_response(
                        PlainTextResponse("Not Found", status_code=404),
                        scope,
                        receive,
                        send,
                    )
                    return
                session_scope, cookie_token = exchanged
                if session_scope not in {"owner", "collaborator", "readonly"}:
                    if self.config.anonymous_readonly:
                        response = RedirectResponse("/", status_code=302)
                        clear_session_cookie(response, self.config)
                        response.headers["Cache-Control"] = "no-store"
                        response.headers["Referrer-Policy"] = "no-referrer"
                        await _send_response(response, scope, receive, send)
                        return
                    await _send_response(
                        PlainTextResponse("Not Found", status_code=404),
                        scope,
                        receive,
                        send,
                    )
                    return
                response = RedirectResponse(
                    await self._magic_link_destination(cookie_token), status_code=302
                )
                set_session_cookie(response, self.config, cookie_token)
                response.headers["Cache-Control"] = "no-store"
                response.headers["Referrer-Policy"] = "no-referrer"
                await _send_response(response, scope, receive, send)
                return

        if access == "public":
            state[_AUTH_SCOPE_KEY] = "public"
            # 공개 라우트는 자격증명을 검증하지 않는다. 정적 자산마다 DB를 열지 않기 위해서다.
            # 계정을 알아야 하는 화면(GET /, /setup)은 public이 아닌 규칙으로 둔다.
            state[PRINCIPAL_STATE_KEY] = ANONYMOUS
            await self.app(scope, receive, send)
            return

        origin_kind = state.get(_ORIGIN_KIND_KEY, "none")
        if _raw_headers(scope, b"x-token"):
            if path == "/mcp":
                await self._send_mcp_error(
                    scope,
                    receive,
                    send,
                    401,
                    "invalid_token",
                    "X-Token header not supported",
                )
                return
            status = 403 if origin_kind == "cross" else 404
            await _send_response(
                PlainTextResponse(
                    "Forbidden" if status == 403 else "Not Found",
                    status_code=status,
                ),
                scope,
                receive,
                send,
            )
            return

        x_session_headers = _raw_headers(scope, b"x-session")
        if len(x_session_headers) > 1:
            if path == "/mcp":
                await self._send_mcp_error(
                    scope,
                    receive,
                    send,
                    401,
                    "invalid_token",
                    "Multiple X-Session headers provided",
                )
                return
            status = 403 if origin_kind == "cross" else 404
            await _send_response(
                PlainTextResponse(
                    "Forbidden" if status == 403 else "Not Found",
                    status_code=status,
                ),
                scope,
                receive,
                send,
            )
            return
        x_session_token = (
            x_session_headers[0].decode("latin-1").strip()
            if x_session_headers
            else None
        )

        authorization_present, bearer = _parse_bearer(scope)
        session_cookie = _read_session_cookies(scope)
        cookie_present, cookie_token = session_cookie.present, session_cookie.token
        x_session_present = bool(x_session_token)

        credentials_count = (
            int(authorization_present)
            + int(cookie_present)
            + int(x_session_present)
        )
        if credentials_count > 1:
            if path == "/mcp":
                await self._send_mcp_error(
                    scope,
                    receive,
                    send,
                    401,
                    "invalid_token",
                    "Multiple authentication credentials provided",
                )
                return
            status = 403 if origin_kind == "cross" else 404
            await _send_response(
                PlainTextResponse(
                    "Forbidden" if status == 403 else "Not Found", status_code=status
                ),
                scope,
                receive,
                send,
            )
            return

        if origin_kind == "cross" and _raw_headers(scope, b"cookie"):
            if path == "/mcp":
                await self._send_mcp_error(
                    scope,
                    receive,
                    send,
                    403,
                    "insufficient_scope",
                    "Forbidden",
                )
                return
            await _send_response(
                PlainTextResponse("Forbidden", status_code=403), scope, receive, send
            )
            return

        if authorization_present and bearer is None:
            if path == "/mcp":
                await self._send_mcp_error(
                    scope,
                    receive,
                    send,
                    401,
                    "invalid_token",
                    "Invalid authorization header",
                )
                return
            status = 403 if origin_kind == "cross" else 404
            await _send_response(
                PlainTextResponse(
                    "Forbidden" if status == 403 else "Not Found", status_code=status
                ),
                scope,
                receive,
                send,
            )
            return

        credential_present = (
            authorization_present or cookie_present or x_session_present
        )
        auth_scope: AuthScope | None = None
        auth_channel: str | None = None
        credential: _Credential | None = None
        cookie_invalid_or_expired = False

        if bearer is not None:
            credential = await _validate_api_token(self.config, bearer)
            if credential is None:
                if self.config.owner_token and _constant_equal(bearer, self.config.owner_token):
                    credential = _env_token_credential("owner")
                elif self.config.collaborator_token and _constant_equal(bearer, self.config.collaborator_token):
                    credential = _env_token_credential("collaborator")
                elif self.config.readonly_token and _constant_equal(bearer, self.config.readonly_token):
                    credential = _env_token_credential("readonly")
                else:
                    credential = await _validate_oauth_token(self.config, bearer)
                    if credential is None and origin_kind != "cross":
                        credential = await _validate_session(self.config, bearer)
            if credential is not None:
                auth_scope, auth_channel = credential.scope, "bearer"

        if origin_kind == "cross" and auth_channel != "bearer":
            if path == "/mcp":
                res_meta = f"{self.config.public_origin}/.well-known/oauth-protected-resource"
                await self._send_mcp_error(
                    scope,
                    receive,
                    send,
                    403,
                    "insufficient_scope",
                    "Cross-origin requests require Bearer token",
                    resource_metadata=res_meta,
                )
                return
            await _send_response(
                PlainTextResponse("Forbidden", status_code=403), scope, receive, send
            )
            return

        if auth_scope is None and origin_kind != "cross":
            if cookie_present:
                if cookie_token:
                    credential = await _validate_session(self.config, cookie_token)
                    if credential is not None:
                        auth_scope, auth_channel = credential.scope, "cookie"
                    else:
                        cookie_invalid_or_expired = True
                else:
                    cookie_invalid_or_expired = True
            elif x_session_token:
                credential = await _validate_session(self.config, x_session_token)
                if credential is not None:
                    auth_scope, auth_channel = credential.scope, "x-session"

        if (
            auth_channel == "cookie"
            and method in _STATE_CHANGING_METHODS
            and origin_kind != "same"
        ):
            if path == "/mcp":
                res_meta = f"{self.config.public_origin}/.well-known/oauth-protected-resource"
                await self._send_mcp_error(
                    scope,
                    receive,
                    send,
                    403,
                    "insufficient_scope",
                    "Forbidden",
                    resource_metadata=res_meta,
                )
                return
            await _send_response(
                PlainTextResponse("Forbidden", status_code=403), scope, receive, send
            )
            return

        can_fallback_anonymous = (
            not authorization_present
            and not x_session_present
            and (not cookie_present or cookie_invalid_or_expired)
        )

        if (
            auth_scope is None
            and can_fallback_anonymous
            and self.config.anonymous_readonly
            and access == "read"
            and path != "/mcp"
            and origin_kind in {"none", "same"}
        ):
            auth_scope = "anonymous"
            auth_channel = (
                "anonymous_cleared_cookie"
                if cookie_present
                else "anonymous"
            )

        allowed = (
            (
                auth_scope == "owner"
                # admin: owner 범위이면서 관리자 계정일 때만(§9.1). 아니면 존재를 숨기는 404.
                and (
                    access != "admin"
                    or (credential is not None and credential.instance_role == "admin")
                )
            )
            or (
                auth_scope == "collaborator"
                and access in {"read", "collaborator"}
            )
            or (
                auth_scope in {"anonymous", "readonly"}
                and access == "read"
                and (path != "/mcp" or auth_scope != "anonymous")
            )
        )
        if not allowed:
            if path == "/mcp":
                desc = (
                    "Invalid token"
                    if credential_present
                    else "Authentication required"
                )
                res_meta = f"{self.config.public_origin}/.well-known/oauth-protected-resource"
                await self._send_mcp_error(
                    scope,
                    receive,
                    send,
                    401,
                    "invalid_token",
                    desc,
                    resource_metadata=res_meta,
                )
                return
            response = PlainTextResponse("Not Found", status_code=404)
            if cookie_present and cookie_invalid_or_expired:
                clear_session_cookie(response, self.config)
            await _send_response(response, scope, receive, send)
            return

        state[_AUTH_SCOPE_KEY] = auth_scope
        if auth_scope == "anonymous":
            state[PRINCIPAL_STATE_KEY] = ANONYMOUS
        elif credential is not None and auth_channel in ("cookie", "bearer", "x-session"):
            state[PRINCIPAL_STATE_KEY] = credential.principal(auth_channel)
        else:
            # 허용 판정을 통과했는데 자격증명이 없으면 배선 결함이다(C1).
            raise RuntimeError("authenticated request without a resolved credential")
        if auth_channel == "cookie" and cookie_token:
            refreshed_cookies = [_session_cookie_header(self.config, cookie_token)]
            if session_cookie.legacy and self.config.secure_cookie:
                # TODO(P3 G9): 과도기 폴백. 옛 이름으로 검증된 세션을 ``__Host-``로 재발급하고 host-only ``claire_session``을 지운다(§5.2).
                refreshed_cookies.append(_clear_legacy_session_cookie_header(self.config))

            async def refresh_cookie_send(message: Message) -> None:
                if message["type"] == "http.response.start":
                    headers = list(message.get("headers", []))
                    # 핸들러가 세션을 바꾸거나 지웠으면(/setup 확정 등) 그 결정이 우선한다.
                    # 옛 토큰을 덧붙이면 브라우저가 지워진 세션을 갖게 된다.
                    if not _sets_session_cookie(headers):
                        headers.extend((b"set-cookie", value) for value in refreshed_cookies)
                        message = {**message, "headers": headers}
                await send(message)

            await self.app(scope, receive, refresh_cookie_send)
            return

        if auth_channel == "anonymous_cleared_cookie":
            cleared_cookies = _clear_session_cookie_headers(self.config)

            async def clear_cookie_send(message: Message) -> None:
                if message["type"] == "http.response.start":
                    headers = list(message.get("headers", []))
                    if not _sets_session_cookie(headers):
                        headers.extend((b"set-cookie", value) for value in cleared_cookies)
                        message = {**message, "headers": headers}
                await send(message)

            await self.app(scope, receive, clear_cookie_send)
            return

        await self.app(scope, receive, send)


def _safe_log_path(path: str) -> str:
    safe = "".join(
        char if ord(char) >= 32 and ord(char) != 127 else "?" for char in path
    )
    return safe[:512]


class SafeAccessLogMiddleware:
    def __init__(
        self,
        app: ASGIApp,
        config: WebRuntimeConfig | None = None,
        theme_manager: Any | None = None,
    ) -> None:
        self.app = app
        self.config = config
        self.theme_manager = theme_manager
        ga_id = config.ga_measurement_id if config else ""
        uc_origin = (config.host_roles.origin("usercontent") if (config and config.host_roles) else "") or ""
        media_origin = (config.host_roles.origin("media") if (config and config.host_roles) else "") or ""
        self._default_csp_header_value = _build_content_security_policy(ga_id, usercontent_origin=uc_origin, media_origin=media_origin).encode("ascii")
        self._ga_csp_header_value = _build_content_security_policy("G-ACTIVE", usercontent_origin=uc_origin, media_origin=media_origin).encode("ascii")
        self._no_ga_csp_header_value = _build_content_security_policy("", usercontent_origin=uc_origin, media_origin=media_origin).encode("ascii")

    def _csp_for(self, state: Mapping[str, Any]) -> bytes:
        """§5.3: 경로가 테마를 고정했으면 그 테마의 GA 측정 ID로 고른다. 고정이 없으면 기존 전역 설정이다."""
        namespace = state.get(NAMESPACE_STATE_KEY)
        if getattr(namespace, "pinned_theme_id", None) is not None:
            extra = state.get(NAMESPACE_EXTRA_STATE_KEY) or {}
            if extra.get("ga_measurement_id"):
                return self._ga_csp_header_value
            return self._no_ga_csp_header_value
        return self.csp_header_value

    @property
    def csp_header_value(self) -> bytes:
        if self.theme_manager is not None and getattr(getattr(self.theme_manager, "settings", None), "multi_theme", False):
            has_ga = self.theme_manager.has_any_ga_enabled()
        else:
            has_ga = bool(self.config and self.config.ga_measurement_id) or (
                self.theme_manager is not None and self.theme_manager.has_any_ga_enabled()
            )
        return self._ga_csp_header_value if has_ga else self._default_csp_header_value

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        state = scope.setdefault("state", {})
        request_id = secrets.token_hex(8)
        state[_REQUEST_ID_KEY] = request_id
        started = time.perf_counter()
        status = 500

        async def safe_send(message: Message) -> None:
            nonlocal status
            if message["type"] == "http.response.start":
                status = int(message["status"])
                headers = list(message.get("headers", []))
                existing = {name.lower() for name, _value in headers}

                def add_header(name: bytes, value: bytes) -> None:
                    if name not in existing:
                        headers.append((name, value))
                        existing.add(name)

                add_header(b"x-request-id", request_id.encode("ascii"))
                add_header(b"referrer-policy", b"no-referrer")
                add_header(b"x-content-type-options", b"nosniff")
                req_path = str(scope.get("path", ""))
                is_presentation_route = req_path.startswith("/p/presentation")
                if is_presentation_route:
                    add_header(b"x-frame-options", b"SAMEORIGIN")
                else:
                    add_header(b"x-frame-options", b"DENY")
                # 이 미들웨어는 원래 경로를 본다. 정책은 접두를 뗀 남은 경로로 찾는다(§6.7).
                parsed = state.get(PARSED_PREFIX_STATE_KEY)
                policy_path = getattr(parsed, "inner_path", req_path)
                route_rule = lookup_route_rule(str(scope.get("method", "")), policy_path)
                if route_rule is None or route_rule.access != "public":
                    add_header(b"cache-control", b"no-store")
                csp_val = self._csp_for(state)
                if is_presentation_route:
                    csp_val = csp_val.replace(b"frame-ancestors 'none'", b"frame-ancestors 'self'")
                add_header(
                    b"content-security-policy",
                    csp_val,
                )
                add_header(
                    b"permissions-policy",
                    b"camera=(), microphone=(), geolocation=()",
                )
                if state.get("claire_theme_deprecated"):
                    add_header(b"deprecation", b"@1798761600")
                    add_header(b"sunset", b"Sun, 01 Jan 2027 00:00:00 GMT")
                message = {**message, "headers": headers}
            await send(message)

        try:
            await self.app(scope, receive, safe_send)
        finally:
            elapsed_ms = (time.perf_counter() - started) * 1000
            log.info(
                "request id=%s method=%s path=%s status=%d duration_ms=%.1f",
                request_id,
                scope.get("method", "-"),
                _safe_log_path(scope.get("path", "")),
                status,
                elapsed_ms,
            )


class _PublicDirectoryNamespaceResolver(NamespaceResolverMiddleware):
    """네트워크 모드 루트(GET /)를 공개 디렉터리로 라우팅한다 (P3 L3-g, D4)."""

    def _root(self, mode: InstanceMode, scope: Scope) -> _Outcome | Response:
        if mode == "single":
            return super()._root(mode, scope)
        query_string = scope.get("query_string", b"") or b""
        if _spa_query(query_string):
            return super()._root(mode, scope)
        return _Outcome(ResolvedNamespace(mode, None, None, None, "", False))


def wrap_web_app(
    app: ASGIApp,
    settings: Any,
    *,
    theme_manager: Any | None = None,
    legacy_hosts: LegacyHostDirectory | None = None,
    namespace_directory: NamespaceDirectory | None = None,
    secret_service: Any | None = None,
) -> ASGIApp:
    """Starlette 앱 바깥에 보안 경계를 조립한다(§6.7).

    ``SafeAccessLog`` → (``CloudflareIPFilter``) → ``HostRouter``. ``HostRouter``의 app 역할만 PrefixParser → CORS → BodyLimit → ErrorBoundary → Authentication → NamespaceResolver → 라우터를 지난다. usercontent·media 하위 앱(P2에서는 모든 경로 404)과 레거시 호스트 응답은 쿠키를 읽지도 쓰지도 않는다.
    ``legacy_hosts``가 없으면 레거시 호스트가 없는 것으로 본다(``create_app``이 ``SqliteLegacyHostDirectory``를 넘긴다).
    ``namespace_directory``가 없으면 ``settings.db_file``로 ``SqliteNamespaceDirectory``를 만든다.
    """
    from .host_router import EmptyLegacyHostDirectory, HostRoles, HostRouter, not_found_app

    if theme_manager is None and getattr(settings, "multi_theme", False):
        try:
            from ..store.theme import get_theme_manager

            theme_manager = get_theme_manager(settings)
        except Exception:
            pass

    config = WebRuntimeConfig.from_settings(settings)
    roles = config.host_roles or HostRoles.from_settings(
        settings, environment=config.environment, app_authority=config.expected_authority
    )
    directory: NamespaceDirectory = (
        namespace_directory
        if namespace_directory is not None
        else SqliteNamespaceDirectory(getattr(settings, "db_file", None))
    )
    # NamespaceResolver는 Authentication 바로 안쪽, ErrorBoundary 안쪽이다. DB 예외가 정제된 500이 된다.
    secured: ASGIApp = _PublicDirectoryNamespaceResolver(app, directory)
    secured = AuthenticationMiddleware(secured, config, namespace_directory=directory)
    secured = ErrorBoundaryMiddleware(secured)
    secured = BodyLimitMiddleware(secured)
    secured = CORSPolicyMiddleware(secured, config)
    # PrefixParser는 CORS 바깥이다. CORS·인증이 접두를 뗀 정규 경로로 정책을 찾는다(§6.7).
    secured = PrefixParserMiddleware(secured)
    from .usercontent import create_usercontent_app
    from .media import create_media_app

    if config.host_roles is None:
        try:
            config.host_roles = roles
        except Exception:
            pass

    sec_svc = secret_service or getattr(settings, "secret_service", None)
    usercontent_app = (
        create_usercontent_app(settings, theme_manager=theme_manager, secret_service=sec_svc, roles=roles)
        if roles.usercontent is not None
        else None
    )
    media_app = (
        create_media_app(settings, secret_service=sec_svc, roles=roles)
        if roles.media is not None
        else None
    )
    secured = HostRouter(
        app=secured,
        roles=roles,
        legacy=legacy_hosts if legacy_hosts is not None else EmptyLegacyHostDirectory(),
        usercontent=usercontent_app,
        media=media_app,
    )
    if config.cloudflare_ips_only:
        secured = CloudflareIPFilterMiddleware(secured)
    return SafeAccessLogMiddleware(secured, config, theme_manager=theme_manager)
