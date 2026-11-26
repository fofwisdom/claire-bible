"""웹 API의 ASGI 보안 경계.

Starlette 라우트와 Uvicorn 실행기는 이 모듈 안쪽에 둔다. 여기서는 public URL에서 도출한 정확한 Host, CORS/Origin, 요청 크기, 인증 scope와 query 없는 접근 로그를 한 번에 적용한다. Reverse proxy 헤더는 의도적으로 읽지 않는다. 예외는 `client_ip` 하나다: 로그인 실패 제한 키에 쓸 클라이언트 IP만, 바로 앞 피어가 ``CLAIRE_TRUSTED_PROXIES``(와 Cloudflare 모드의 Cloudflare 대역)에 있을 때 ``X-Forwarded-For``에서 읽는다.
"""

from __future__ import annotations

import asyncio
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

from ..accounts import kb_access
from ..store import accounts as accounts_store
from ..store.accounts import ServerMode
from ..store import db as dbm
from .principal import ANONYMOUS, PRINCIPAL_STATE_KEY, Principal, has_recent_auth
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
    "SERVER_MARKER_HEADER",
    "ServerMarkerMiddleware",
    "CloudflareIPFilterMiddleware",
    "MAX_REQUEST_BODY",
    "ALL_MODES",
    "NETWORK_ONLY",
    "ROUTE_POLICY",
    "RouteRule",
    "RouteSurface",
    "LEGACY_COOKIE_FALLBACK_REMOVED",
    "OAUTH_TX_COOKIE_NAME",
    "LEGACY_OAUTH_TX_COOKIE_NAME",
    "oauth_tx_cookie_name",
    "set_oauth_tx_cookie",
    "clear_oauth_tx_cookie",
    "read_oauth_tx_cookie",
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
    "resolve_optional_session_principal",
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
    # ``'self'``: the sandbox frame of a rendition of another server is a page of this origin with its own ``sandbox`` policy (``/silvercord/{token}/renditions/remote/{key}``). Without a usercontent host, ``default-src 'self'`` gives the same rule.
    frame_src_part = f"frame-src 'self' {usercontent_origin}; " if usercontent_origin else ""
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


AccessLevel = Literal["public", "read", "write", "owner", "admin"]
AuthScope = Literal["public", "anonymous", "readonly", "write", "owner"]
RouteKey = tuple[str, str]
# 라우트 표면(§9.1). root: 루트 GET / 전용, system: 루트에서만(접두 아래 404),
# namespaced: 네임스페이스 필요(N1–N3), any: 접두를 무시한다.
# collection: at the root, the collection of the server (no namespace account); under a prefix, the collection of the account of the namespace (SHARED_KNOWLEDGE_BASES_AND_THEMES_DESIGN.md Section 7: ``/kb`` and ``/@{u}/kb``).
RouteSurface = Literal["root", "system", "namespaced", "any", "collection"]
ALL_MODES: frozenset[ServerMode] = frozenset({"single", "network"})
NETWORK_ONLY: frozenset[ServerMode] = frozenset({"network"})


@dataclass(frozen=True, slots=True)
class RouteRule:
    # The minimum scope of the credential (SHARED_KNOWLEDGE_BASES_AND_THEMES_DESIGN.md Section 6.6): ``read`` takes ``readonly`` and anonymous reads, ``write`` takes ``write`` and ``owner``, ``owner`` and ``admin`` take ``owner``.
    access: AccessLevel
    # The capability in the selected knowledge base (Section 6.2 rule 5, Section 6.9 rule 3). ``none``: the route does not act on a knowledge base.
    capability: str
    # ROUTE_POLICY의 모든 항목은 아래 표면별 생성 헬퍼(_root·_system·_namespaced·_any)로 표면을 명시한다.
    # 기본값에 기대지 않는다(tests/test_route_surface_contract.py가 AST로 확인한다).
    surface: RouteSurface
    # 현재 모드가 없으면 404(N5).
    modes: frozenset[ServerMode] = ALL_MODES
    # Section 6.8: the route needs a cookie session with a recent sign-in. A bearer credential never passes.
    step_up: bool = False

    def __post_init__(self) -> None:
        if self.capability not in kb_access.ROUTE_CAPABILITIES:
            raise ValueError(f"unknown route capability: {self.capability!r}")


def _root(access: AccessLevel, capability: str, *, modes: frozenset[ServerMode] = ALL_MODES) -> RouteRule:
    """루트 ``GET /`` 전용(§9.1). 접두 아래에서는 namespaced 홈으로 동작한다."""
    return RouteRule(access=access, capability=capability, surface="root", modes=modes)


def _system(access: AccessLevel, capability: str, *, modes: frozenset[ServerMode] = ALL_MODES, step_up: bool = False) -> RouteRule:
    """루트에서만 받는다. 접두 아래에서는 404(N5)."""
    return RouteRule(access=access, capability=capability, surface="system", modes=modes, step_up=step_up)


def _namespaced(access: AccessLevel, capability: str, *, modes: frozenset[ServerMode] = ALL_MODES, step_up: bool = False) -> RouteRule:
    """네임스페이스가 필요하다(N1–N3)."""
    return RouteRule(access=access, capability=capability, surface="namespaced", modes=modes, step_up=step_up)


def _any(access: AccessLevel, capability: str, *, modes: frozenset[ServerMode] = ALL_MODES) -> RouteRule:
    """네임스페이스와 무관하다. 접두를 검증한 뒤 무시하고 자격증명 계정을 쓴다."""
    return RouteRule(access=access, capability=capability, surface="any", modes=modes)


def _collection(access: AccessLevel, capability: str, *, modes: frozenset[ServerMode] = ALL_MODES) -> RouteRule:
    """A collection: at the root, the collection of the server; under a prefix, the collection of the account of the namespace."""
    return RouteRule(access=access, capability=capability, surface="collection", modes=modes)


def minimum_scope(rule: RouteRule) -> str:
    """The smallest credential scope that passes the access level of ``rule`` (Section 6.6)."""
    return {"public": "none", "read": "readonly", "write": "write", "owner": "owner", "admin": "owner"}[rule.access]


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
    ("GET", "/health"): _system("public", "none"),
    ("HEAD", "/health"): _system("public", "none"),
    ("GET", "/robots.txt"): _system("public", "none"),
    ("HEAD", "/robots.txt"): _system("public", "none"),
    # The vocabulary of the knowledge federation (KNOWLEDGE_FEDERATION_DESIGN.md Section 9.1): public.
    ("GET", "/ns"): _system("public", "none"),
    ("HEAD", "/ns"): _system("public", "none"),
    ("GET", "/ns/v1"): _system("public", "none"),
    ("HEAD", "/ns/v1"): _system("public", "none"),
    ("GET", "/ns/v2"): _system("public", "none"),
    ("HEAD", "/ns/v2"): _system("public", "none"),
    ("GET", "/sitemap.xml"): _system("public", "none"),
    ("HEAD", "/sitemap.xml"): _system("public", "none"),
    ("GET", "/favicon.ico"): _system("public", "none"),
    ("HEAD", "/favicon.ico"): _system("public", "none"),
    ("GET", "/favicon.svg"): _system("public", "none"),
    ("HEAD", "/favicon.svg"): _system("public", "none"),
    ("GET", "/apple-touch-icon.png"): _system("public", "none"),
    ("HEAD", "/apple-touch-icon.png"): _system("public", "none"),
    ("GET", "/apple-touch-icon-precomposed.png"): _system("public", "none"),
    ("HEAD", "/apple-touch-icon-precomposed.png"): _system("public", "none"),
    ("GET", "/manifest.json"): _system("public", "none"),
    ("HEAD", "/manifest.json"): _system("public", "none"),
    ("GET", "/site.webmanifest"): _system("public", "none"),
    ("HEAD", "/site.webmanifest"): _system("public", "none"),
    ("GET", "/browserconfig.xml"): _system("public", "none"),
    ("HEAD", "/browserconfig.xml"): _system("public", "none"),
    ("GET", "/sw.js"): _system("public", "none"),
    ("HEAD", "/sw.js"): _system("public", "none"),
    ("GET", "/icon"): _system("public", "none"),
    ("HEAD", "/icon"): _system("public", "none"),
    ("GET", "/font"): _system("public", "none"),
    ("HEAD", "/font"): _system("public", "none"),
    ("GET", "/fonts/NotoSansKR-Regular.woff2"): _system("public", "none"),
    ("HEAD", "/fonts/NotoSansKR-Regular.woff2"): _system("public", "none"),
    ("GET", "/fonts/NotoSansKR-Bold.woff2"): _system("public", "none"),
    ("HEAD", "/fonts/NotoSansKR-Bold.woff2"): _system("public", "none"),
    ("GET", "/fonts/NotoSerifKR-Regular.woff2"): _system("public", "none"),
    ("HEAD", "/fonts/NotoSerifKR-Regular.woff2"): _system("public", "none"),
    ("GET", "/fonts/NotoSerifKR-Bold.woff2"): _system("public", "none"),
    ("HEAD", "/fonts/NotoSerifKR-Bold.woff2"): _system("public", "none"),
    ("GET", "/fonts/D2Coding.woff2"): _system("public", "none"),
    ("HEAD", "/fonts/D2Coding.woff2"): _system("public", "none"),
    ("GET", "/fonts/D2CodingBold.woff2"): _system("public", "none"),
    ("HEAD", "/fonts/D2CodingBold.woff2"): _system("public", "none"),
    ("GET", "/p"): _system("public", "none"),
    ("HEAD", "/p"): _system("public", "none"),
    ("GET", "/p/presentation"): _system("public", "read"),
    ("HEAD", "/p/presentation"): _system("public", "read"),
    ("GET", "/image"): _system("public", "none"),
    ("HEAD", "/image"): _system("public", "none"),
    ("GET", "/images/{filename:path}"): _system("public", "none"),
    ("HEAD", "/images/{filename:path}"): _system("public", "none"),
    ("GET", "/p/images/{filename:path}"): _system("public", "none"),
    ("HEAD", "/p/images/{filename:path}"): _system("public", "none"),
    # Mount("/static/locales") and Mount("/static") static assets (O2). Under a prefix they are system routes and return 404.
    ("GET", "/static/locales/{path:path}"): _system("public", "none"),
    ("HEAD", "/static/locales/{path:path}"): _system("public", "none"),
    ("GET", "/static/{path:path}"): _system("public", "none"),
    ("HEAD", "/static/{path:path}"): _system("public", "none"),
    ("GET", "/"): _root("read", "read"),
    ("HEAD", "/"): _root("read", "read"),
    ("GET", "/syndication"): _system("owner", "none"),
    ("HEAD", "/syndication"): _system("owner", "none"),
    ("GET", "/syndication/preferences"): _system("owner", "read"),
    ("HEAD", "/syndication/preferences"): _system("owner", "read"),
    ("POST", "/api/syndication/dispatch"): _system("write", "write"),
    ("POST", "/syndication/dispatch"): _system("write", "write"),
    ("GET", "/api/syndication/dispatches"): _system("read", "none"),
    ("HEAD", "/api/syndication/dispatches"): _system("read", "none"),
    ("GET", "/syndication/dispatches"): _system("read", "none"),
    ("HEAD", "/syndication/dispatches"): _system("read", "none"),
    ("GET", "/whoami"): _namespaced("read", "read"),
    ("HEAD", "/whoami"): _namespaced("read", "read"),
    ("GET", "/home-profile"): _namespaced("public", "none"),
    ("HEAD", "/home-profile"): _namespaced("public", "none"),
    ("GET", "/home-timeline"): _namespaced("public", "none"),
    ("HEAD", "/home-timeline"): _namespaced("public", "none"),
    ("GET", "/stats"): _namespaced("read", "read"),
    ("HEAD", "/stats"): _namespaced("read", "read"),
    ("GET", "/graph"): _namespaced("read", "read"),
    ("HEAD", "/graph"): _namespaced("read", "read"),
    ("GET", "/node"): _namespaced("read", "read"),
    ("HEAD", "/node"): _namespaced("read", "read"),
    ("GET", "/documents"): _namespaced("read", "read"),
    ("HEAD", "/documents"): _namespaced("read", "read"),
    ("GET", "/document"): _namespaced("read", "read"),
    ("HEAD", "/document"): _namespaced("read", "read"),
    ("GET", "/document/export"): _namespaced("read", "read"),
    ("HEAD", "/document/export"): _namespaced("read", "read"),
    ("GET", "/document/raw"): _namespaced("write", "read"),
    ("HEAD", "/document/raw"): _namespaced("write", "read"),
    ("GET", "/document/presentation"): _namespaced("read", "read"),
    ("HEAD", "/document/presentation"): _namespaced("read", "read"),
    ("POST", "/document/presentation/generate"): _namespaced("owner", "maintain"),
    ("POST", "/document/presentation/compose"): _namespaced("owner", "maintain"),
    ("POST", "/document/presentation/compile"): _namespaced("owner", "maintain"),
    ("GET", "/document/presentation/adoc"): _namespaced("read", "read"),
    ("HEAD", "/document/presentation/adoc"): _namespaced("read", "read"),
    ("PUT", "/document/presentation/adoc"): _namespaced("owner", "maintain"),
    ("GET", "/resolution/decisions"): _namespaced("read", "read"),
    ("HEAD", "/resolution/decisions"): _namespaced("read", "read"),
    ("GET", "/ingest/active"): _namespaced("read", "read"),
    ("HEAD", "/ingest/active"): _namespaced("read", "read"),
    ("POST", "/search"): _namespaced("read", "read"),
    ("POST", "/mcp"): _system("read", "read"),
    ("GET", "/mcp"): _system("read", "read"),
    ("HEAD", "/mcp"): _system("read", "read"),
    # SHARED_KNOWLEDGE_BASES_AND_THEMES_DESIGN.md Section 7: ``/kb`` is the collection of shared knowledge bases, ``/@{u}/kb`` the collection of the shared knowledge bases where ``u`` is an active member, ``/themes`` the collection of themes (``/@{u}/themes`` gets 308 to ``/@{u}/kb``).
    ("GET", "/kb"): _collection("read", "none"),
    ("HEAD", "/kb"): _collection("read", "none"),
    ("GET", "/themes"): _collection("read", "none"),
    ("HEAD", "/themes"): _collection("read", "none"),
    # Lifecycle of a knowledge base (design Section 10.1). ``PATCH /`` and ``DELETE /`` act on the knowledge base that the path selects: ``/kb/{number}/`` (shared) or ``/@{u}/`` (personal). Each active account makes shared knowledge bases (``POST /kb``).
    ("POST", "/kb"): _any("owner", "none"),
    ("PATCH", "/"): _namespaced("owner", "settings"),
    ("DELETE", "/"): _namespaced("owner", "delete", step_up=True),
    # Membership and moderation (design Section 10.2). A role change in ``PATCH /members/{account}`` needs step-up; the handler asks for it, because the other actions of the route must be fast (Section 6.8 rule 2).
    ("POST", "/membership"): _namespaced("owner", "join"),
    ("DELETE", "/membership"): _namespaced("owner", "join"),
    ("GET", "/members"): _namespaced("read", "read"),
    ("HEAD", "/members"): _namespaced("read", "read"),
    ("PATCH", "/members/{account}"): _namespaced("owner", "people"),
    ("GET", "/moderation-log"): _namespaced("owner", "people"),
    ("HEAD", "/moderation-log"): _namespaced("owner", "people"),
    # Themes (design Sections 8.2 and 8.3): the admin sees each theme, sets the thresholds, starts a run, and makes the adjustments.
    # Automatic contribution and mutual collaboration (design Sections 9.9, 9.10): the decisions of the person, so the scope is ``owner``. The handlers ask for a recent sign-in to turn the rule on, to make it wider, and to request or accept a mutual collaboration.
    # KNOWLEDGE_FEDERATION_DESIGN.md Section 8.3 rule 4: the shared knowledge bases of this server and of other servers near a subject, before an account makes a new one.
    ("GET", "/api/kb/similar"): _system("read", "none"),
    ("HEAD", "/api/kb/similar"): _system("read", "none"),
    ("GET", "/api/auto-rule"): _system("read", "none"),
    ("HEAD", "/api/auto-rule"): _system("read", "none"),
    ("PUT", "/api/auto-rule"): _system("owner", "none"),
    ("POST", "/api/auto-rule/dry-run"): _system("owner", "none"),
    # The contributions with the credit of the account of the credential, each origin (``?origin=`` selects one).
    ("GET", "/api/contributions"): _system("read", "none"),
    ("HEAD", "/api/contributions"): _system("read", "none"),
    ("GET", "/api/contribution-blocks"): _system("read", "none"),
    ("HEAD", "/api/contribution-blocks"): _system("read", "none"),
    ("PUT", "/api/contribution-blocks/{document}"): _system("owner", "none"),
    ("DELETE", "/api/contribution-blocks/{document}"): _system("owner", "none"),
    ("GET", "/api/mutual-collaborations"): _system("read", "none"),
    ("HEAD", "/api/mutual-collaborations"): _system("read", "none"),
    ("POST", "/api/mutual-collaborations"): _system("owner", "none"),
    ("PUT", "/api/mutual-collaborations/{account}"): _system("owner", "none"),
    ("DELETE", "/api/mutual-collaborations/{account}"): _system("owner", "none"),
    ("GET", "/api/admin/auto-contribution"): _system("admin", "none"),
    ("HEAD", "/api/admin/auto-contribution"): _system("admin", "none"),
    ("PUT", "/api/admin/auto-contribution"): _system("admin", "none"),
    # Reputation (design Section 8.6): the account sees its own; the admin sees all levels and sets the numbers.
    ("GET", "/api/reputation"): _system("read", "none"),
    ("HEAD", "/api/reputation"): _system("read", "none"),
    ("GET", "/api/admin/expansion/shadow"): _system("admin", "none"),
    ("HEAD", "/api/admin/expansion/shadow"): _system("admin", "none"),
    ("PUT", "/api/admin/expansion/shadow/{record_id}/verdict"): _system("admin", "none"),
    ("GET", "/api/me/pair-review/items"): _system("read", "none"),
    ("HEAD", "/api/me/pair-review/items"): _system("read", "none"),
    ("PUT", "/api/me/pair-review/items/{item_id}/verdict"): _system("owner", "none"),
    ("DELETE", "/api/me/pair-review/items/{item_id}/verdict"): _system("owner", "none"),
    ("GET", "/api/me/pair-review/stats"): _system("read", "none"),
    ("HEAD", "/api/me/pair-review/stats"): _system("read", "none"),
    ("GET", "/api/admin/pair-review/report"): _system("admin", "none"),
    ("HEAD", "/api/admin/pair-review/report"): _system("admin", "none"),
    ("GET", "/api/admin/reputations"): _system("admin", "none"),
    ("HEAD", "/api/admin/reputations"): _system("admin", "none"),
    ("PUT", "/api/admin/reputations/settings"): _system("admin", "none"),
    ("GET", "/api/admin/themes"): _system("admin", "none"),
    ("HEAD", "/api/admin/themes"): _system("admin", "none"),
    ("POST", "/api/admin/themes"): _system("admin", "none"),
    ("PUT", "/api/admin/themes/settings"): _system("admin", "none"),
    ("POST", "/api/admin/themes/judge"): _system("admin", "none"),
    ("PATCH", "/api/admin/themes/{number}"): _system("admin", "none"),
    ("PUT", "/api/admin/themes/{number}/knowledge-bases/{kb}"): _system("admin", "none"),
    # KNOWLEDGE_FEDERATION_DESIGN.md Section 8.2: the admin adjustments for a shared knowledge base of another server in a local theme.
    ("PUT", "/api/admin/themes/{number}/remote-knowledge-bases/{remote_id}"): _system("admin", "none"),
    ("POST", "/api/admin/themes/{number}/merge"): _system("admin", "none"),
    # Contribution to a theme (design Section 9.2): a submission belongs to the account of the credential, so the prefix is ignored. The targets are checked one by one in the handler.
    ("POST", "/submissions"): _any("write", "none"),
    ("GET", "/submissions"): _any("read", "none"),
    ("HEAD", "/submissions"): _any("read", "none"),
    ("GET", "/submissions/{number}"): _any("read", "none"),
    ("HEAD", "/submissions/{number}"): _any("read", "none"),
    # Contributions (design Sections 9.1, 9.4 to 9.6). The withdrawal and the retry are for the contributor (the handler checks the credit); the scope ``write`` has them (Section 6.6).
    ("POST", "/contributions"): _namespaced("write", "contribute_direct"),
    ("GET", "/contributions"): _namespaced("read", "read"),
    ("HEAD", "/contributions"): _namespaced("read", "read"),
    # Curation and fork (design Sections 9.7, 9.8): decisions of the person, so the scope is ``owner``. The fork handler decides the capability ``fork``: a personal knowledge base that the requester reads gets 403 ``kb_fork_shared_only``.
    ("POST", "/curations"): _namespaced("owner", "curate"),
    # The sources of a curation on other servers (federation design Section 11): the tool of a reviewer, and the request can start a read of another server.
    ("GET", "/remote-sources"): _namespaced("owner", "curate"),
    ("HEAD", "/remote-sources"): _namespaced("owner", "curate"),
    # Remote collaborators (KNOWLEDGE_FEDERATION_DESIGN.md Section 12.2): a moderator gives and ends the grant of an account of another server. The routes need a recent sign-in in the handler, as the role change of ``/members/{account}``.
    ("GET", "/remote-collaborators"): _namespaced("owner", "people"),
    ("HEAD", "/remote-collaborators"): _namespaced("owner", "people"),
    ("POST", "/remote-collaborators"): _namespaced("owner", "people"),
    ("DELETE", "/remote-collaborators/{grant}"): _namespaced("owner", "people"),
    ("GET", "/api/kb/remote-grants"): _any("read", "none"),
    ("HEAD", "/api/kb/remote-grants"): _any("read", "none"),
    ("POST", "/forks"): _namespaced("owner", "read"),
    ("DELETE", "/contributions/{number}"): _namespaced("write", "read"),
    ("PATCH", "/contributions/{number}"): _namespaced("owner", "review"),
    ("POST", "/contributions/{number}/remove"): _namespaced("owner", "remove"),
    ("POST", "/contributions/{number}/retry"): _namespaced("write", "read"),
    ("PUT", "/api/admin/kb/{number}/moderators/{account}"): _system("admin", "moderators", step_up=True),
    ("DELETE", "/api/admin/kb/{number}/moderators/{account}"): _system("admin", "moderators", step_up=True),
    ("GET", "/providers"): _any("admin", "none"),
    ("HEAD", "/providers"): _any("admin", "none"),
    ("POST", "/providers"): _any("admin", "none"),
    ("PATCH", "/providers"): _any("admin", "none"),
    ("POST", "/providers/test"): _any("admin", "none"),
    ("POST", "/providers/check"): _any("admin", "none"),
    ("GET", "/limits"): _any("admin", "none"),
    ("HEAD", "/limits"): _any("admin", "none"),
    ("POST", "/limits"): _any("admin", "none"),
    ("PATCH", "/limits"): _any("admin", "none"),
    ("GET", "/ingest-limits"): _any("admin", "none"),
    ("HEAD", "/ingest-limits"): _any("admin", "none"),
    ("POST", "/ingest-limits"): _any("admin", "none"),
    ("PATCH", "/ingest-limits"): _any("admin", "none"),
    ("POST", "/ingest"): _namespaced("write", "write"),
    ("POST", "/ingest-stream"): _namespaced("write", "write"),
    ("POST", "/reingest"): _namespaced("write", "write"),
    ("POST", "/reingest-stream"): _namespaced("write", "write"),
    # Regeneration: in the personal knowledge base a write of its account (scope ``write``); in a shared knowledge base the capability ``maintain`` with the provider context of the requester (design Section 13.1 rule 5).
    ("POST", "/regenerate"): _namespaced("write", "maintain"),
    ("POST", "/regenerate-stream"): _namespaced("write", "maintain"),
    ("POST", "/document/regenerate"): _namespaced("write", "maintain"),
    ("POST", "/document/seen"): _namespaced("owner", "review"),
    ("POST", "/document/pin"): _namespaced("owner", "curate"),
    ("POST", "/document/hide"): _namespaced("owner", "review"),
    ("POST", "/document/title"): _namespaced("owner", "curate"),
    ("POST", "/document/purge"): _namespaced("owner", "remove"),
    ("POST", "/purge"): _namespaced("owner", "remove"),
    ("POST", "/document/matrix/purge"): _namespaced("owner", "remove"),
    # The two routes read the graph and call the LLM of the requester; they write no document.
    ("POST", "/synthesize"): _namespaced("owner", "read"),
    ("POST", "/research"): _namespaced("owner", "read"),
    ("POST", "/dedup/scan"): _namespaced("owner", "maintain"),
    ("POST", "/dedup/merge"): _namespaced("owner", "maintain"),
    ("POST", "/entity/primary-label"): _namespaced("owner", "maintain"),
    ("POST", "/resolution/rollback"): _namespaced("owner", "maintain"),
    ("POST", "/share"): _namespaced("read", "share"),
    ("DELETE", "/share"): _namespaced("write", "share"),
    ("POST", "/entities/{entity_id}/share"): _namespaced("read", "share"),
    ("DELETE", "/entities/{entity_id}/share"): _namespaced("write", "share"),
    # The permalink of a document, a note, or an entity (PERMALINK_UNIFICATION_DESIGN.md). The route decides the access from the item.
    ("GET", "/silvercord/{token}"): _system("public", "none"),
    ("HEAD", "/silvercord/{token}"): _system("public", "none"),
    ("GET", "/silvercord/{token}/graph"): _system("public", "none"),
    ("HEAD", "/silvercord/{token}/graph"): _system("public", "none"),
    ("GET", "/silvercord/{token}/opinions"): _system("public", "none"),
    ("HEAD", "/silvercord/{token}/opinions"): _system("public", "none"),
    ("GET", "/silvercord/{token}/renditions/{template}/opinions"): _system("public", "none"),
    ("HEAD", "/silvercord/{token}/renditions/{template}/opinions"): _system("public", "none"),
    ("GET", "/silvercord/{token}/opinions/{key}"): _system("public", "none"),
    ("HEAD", "/silvercord/{token}/opinions/{key}"): _system("public", "none"),
    ("GET", "/silvercord/{token}/renditions/{template}/opinions/{key}"): _system("public", "none"),
    ("HEAD", "/silvercord/{token}/renditions/{template}/opinions/{key}"): _system("public", "none"),
    ("GET", "/silvercord/{token}/renditions/remote/{key}/opinions"): _system("public", "none"),
    ("HEAD", "/silvercord/{token}/renditions/remote/{key}/opinions"): _system("public", "none"),
    ("GET", "/silvercord/{token}/renditions/remote/{rkey}/opinions/{key}"): _system("public", "none"),
    ("HEAD", "/silvercord/{token}/renditions/remote/{rkey}/opinions/{key}"): _system("public", "none"),
    ("GET", "/silvercord/{token}/renditions/remote/{key}"): _system("public", "none"),
    ("HEAD", "/silvercord/{token}/renditions/remote/{key}"): _system("public", "none"),
    ("GET", "/silvercord/{token}/renditions/{template}"): _system("public", "none"),
    ("HEAD", "/silvercord/{token}/renditions/{template}"): _system("public", "none"),
    ("GET", "/silvercord/{token}/workspace"): _system("public", "none"),
    ("HEAD", "/silvercord/{token}/workspace"): _system("public", "none"),
    # The address of an entity. It opens the graph of its knowledge base with the entity selected.
    ("GET", "/entity/{token}"): _system("public", "none"),
    ("HEAD", "/entity/{token}"): _system("public", "none"),
    ("POST", "/support/bundle"): _system("admin", "none"),
    ("GET", "/support/bundle/{token}"): _system("public", "none"),
    ("HEAD", "/support/bundle/{token}"): _system("public", "none"),
    # The data bundle of a shared knowledge base is for its moderators and the admin (design Section 13.2): the capability ``settings``. The owner of a personal knowledge base has each capability.
    ("POST", "/support/graph-bundle"): _namespaced("owner", "settings"),
    ("GET", "/support/graph-bundle/{token}"): _system("public", "none"),
    ("HEAD", "/support/graph-bundle/{token}"): _system("public", "none"),
    ("GET", "/reference"): _system("read", "none"),
    ("HEAD", "/reference"): _system("read", "none"),
    ("GET", "/openapi/v1.json"): _system("public", "none"),
    ("HEAD", "/openapi/v1.json"): _system("public", "none"),
    ("GET", "/openapi/account.json"): _system("owner", "none"),
    ("HEAD", "/openapi/account.json"): _system("owner", "none"),
    ("GET", "/openapi/admin.json"): _system("admin", "none"),
    ("HEAD", "/openapi/admin.json"): _system("admin", "none"),
    ("GET", "/openapi.yaml"): _system("public", "none"),
    ("HEAD", "/openapi.yaml"): _system("public", "none"),
    ("GET", "/.well-known/oauth-protected-resource"): _system("public", "none"),
    ("HEAD", "/.well-known/oauth-protected-resource"): _system("public", "none"),
    ("GET", "/.well-known/oauth-authorization-server"): _system("public", "none"),
    ("HEAD", "/.well-known/oauth-authorization-server"): _system("public", "none"),
    ("POST", "/oauth/register"): _system("public", "none"),
    ("GET", "/oauth/authorize"): _system("public", "none"),
    ("HEAD", "/oauth/authorize"): _system("public", "none"),
    ("POST", "/oauth/authorize"): _system("public", "none"),
    ("POST", "/oauth/authorize/telegram-push"): _system("public", "none"),
    ("GET", "/oauth/authorize/poll"): _system("public", "none"),
    ("HEAD", "/oauth/authorize/poll"): _system("public", "none"),
    ("POST", "/oauth/token"): _system("public", "none"),
    ("POST", "/oauth/revoke"): _system("public", "none"),
    ("POST", "/oauth/device_authorization"): _system("public", "none"),
    ("GET", "/device"): _system("public", "none"),
    ("HEAD", "/device"): _system("public", "none"),
    ("POST", "/device"): _system("public", "none"),
    ("GET", "/api/me/oauth/grants"): _any("owner", "none"),
    ("HEAD", "/api/me/oauth/grants"): _any("owner", "none"),
    ("POST", "/api/me/oauth/grants"): _any("owner", "none"),
    ("DELETE", "/api/me/oauth/grants"): _any("owner", "none"),
    # 계정 (설계 §8.4–§8.7). /admin/*·/api/admin/*는 admin 접근 수준(§9.1)이고, 설정 전용 조건은 핸들러가 404로 판정한다(api.accounts_routes).
    ("GET", "/setup"): _system("owner", "none"),
    ("HEAD", "/setup"): _system("owner", "none"),
    ("POST", "/setup"): _system("owner", "none"),
    # /login·/logout은 공개다. 로그인 CSRF·강제 로그아웃은 핸들러가 동일 origin 검사로 막는다.
    ("GET", "/login"): _system("public", "none"),
    ("HEAD", "/login"): _system("public", "none"),
    ("POST", "/login"): _system("public", "none"),
    ("POST", "/logout"): _system("public", "none"),
    # I18N design Section 5.4: the locale switch of an anonymous visitor. The handler accepts the same origin only.
    ("POST", "/api/locale"): _system("public", "none"),
    ("POST", "/api/auth/reauth"): _system("owner", "none"),
    ("GET", "/admin/accounts"): _system("admin", "none"),
    ("HEAD", "/admin/accounts"): _system("admin", "none"),
    ("GET", "/api/admin/accounts"): _system("admin", "none"),
    ("HEAD", "/api/admin/accounts"): _system("admin", "none"),
    ("PATCH", "/api/admin/accounts"): _system("admin", "none"),
    ("GET", "/api/admin/accounts/signup-trace"): _system("admin", "none"),
    ("HEAD", "/api/admin/accounts/signup-trace"): _system("admin", "none"),
    ("GET", "/api/admin/usernames/retired"): _system("admin", "none"),
    ("HEAD", "/api/admin/usernames/retired"): _system("admin", "none"),
    ("POST", "/api/admin/usernames/release"): _system("admin", "none"),
    ("GET", "/admin/server"): _system("admin", "none"),
    ("HEAD", "/admin/server"): _system("admin", "none"),
    ("GET", "/api/admin/server"): _system("admin", "none"),
    ("HEAD", "/api/admin/server"): _system("admin", "none"),
    ("POST", "/api/admin/server/mode"): _system("admin", "none"),
    # LABORATORY_DESIGN.md LB1-LB3: the laboratory area and its API (the server administrator only).
    ("GET", "/laboratory"): _system("admin", "none"),
    ("HEAD", "/laboratory"): _system("admin", "none"),
    ("GET", "/laboratory/measurements"): _system("admin", "none"),
    ("HEAD", "/laboratory/measurements"): _system("admin", "none"),
    ("GET", "/laboratory/shadows"): _system("admin", "none"),
    ("HEAD", "/laboratory/shadows"): _system("admin", "none"),
    ("GET", "/api/admin/laboratory/runs"): _system("admin", "none"),
    ("HEAD", "/api/admin/laboratory/runs"): _system("admin", "none"),
    ("GET", "/api/admin/laboratory/runs/{run_id}"): _system("admin", "none"),
    ("HEAD", "/api/admin/laboratory/runs/{run_id}"): _system("admin", "none"),
    ("DELETE", "/api/admin/laboratory/runs/{run_id}"): _system("admin", "none"),
    ("POST", "/api/admin/laboratory/stt-hints"): _system("admin", "none"),
    # ACCOUNT_PREFERRED_LANGUAGES_DESIGN.md P1, P3: the server default language.
    ("PUT", "/api/admin/server/languages"): _system("admin", "none"),
    ("POST", "/api/admin/server/invites"): _system("admin", "none"),
    ("POST", "/api/admin/server/legacy-hosts/sunset"): _system("admin", "none"),
    ("GET", "/api/admin/federation"): _system("admin", "none"),
    ("HEAD", "/api/admin/federation"): _system("admin", "none"),
    # Fedify sidecar design Sections 9.2 and 11 (unit FP2-4): domain blocks and the console section for the admin, the actor state and the followers for the owner of an account.
    ("GET", "/api/admin/federation/reading"): _system("admin", "none"),
    ("HEAD", "/api/admin/federation/reading"): _system("admin", "none"),
    ("PUT", "/api/admin/federation/reading"): _system("admin", "none"),
    ("GET", "/api/admin/federation/domain-blocks"): _system("admin", "none"),
    ("HEAD", "/api/admin/federation/domain-blocks"): _system("admin", "none"),
    ("POST", "/api/admin/federation/domain-blocks"): _system("admin", "none"),
    ("DELETE", "/api/admin/federation/domain-blocks"): _system("admin", "none"),
    # KNOWLEDGE_FEDERATION_DESIGN.md Sections 6.2, 6.5, and 13 (unit KF1): the known servers and the inbox journal of the federation of shared knowledge, for the admin.
    ("GET", "/api/admin/federation/known-servers"): _system("admin", "none"),
    ("HEAD", "/api/admin/federation/known-servers"): _system("admin", "none"),
    ("POST", "/api/admin/federation/known-servers"): _system("admin", "none"),
    ("DELETE", "/api/admin/federation/known-servers"): _system("admin", "none"),
    ("GET", "/api/admin/federation/journal"): _system("admin", "none"),
    ("HEAD", "/api/admin/federation/journal"): _system("admin", "none"),
    ("GET", "/api/admin/federation/journal/{entry_id}"): _system("admin", "none"),
    ("HEAD", "/api/admin/federation/journal/{entry_id}"): _system("admin", "none"),
    ("POST", "/api/admin/federation/journal/{entry_id}/replay"): _system("admin", "none"),
    ("GET", "/admin/federation"): _system("admin", "none"),
    ("HEAD", "/admin/federation"): _system("admin", "none"),
    ("GET", "/api/federation/status"): _any("owner", "none"),
    ("HEAD", "/api/federation/status"): _any("owner", "none"),
    ("GET", "/api/federation/following"): _any("owner", "none"),
    ("HEAD", "/api/federation/following"): _any("owner", "none"),
    ("POST", "/api/federation/following"): _any("owner", "none"),
    ("DELETE", "/api/federation/following/{following_id}"): _any("owner", "none"),
    ("GET", "/api/federation/timeline"): _any("owner", "none"),
    ("HEAD", "/api/federation/timeline"): _any("owner", "none"),
    ("GET", "/api/federation/timeline/{item_id}"): _any("owner", "none"),
    ("HEAD", "/api/federation/timeline/{item_id}"): _any("owner", "none"),
    ("PATCH", "/api/federation/timeline/{item_id}"): _any("owner", "none"),
    ("POST", "/api/federation/timeline/{item_id}/ingest"): _any("owner", "write"),
    ("POST", "/api/federation/timeline/{item_id}/context"): _any("owner", "none"),
    ("GET", "/api/federation/objects"): _any("owner", "none"),
    ("HEAD", "/api/federation/objects"): _any("owner", "none"),
    ("GET", "/admin/kb"): _system("admin", "none"),
    ("HEAD", "/admin/kb"): _system("admin", "none"),
    # Outbound interactions (FEDERATION_REMOTE_READING_DESIGN.md Sections 5.3 and 5.4, unit FP4-9): own notes, favourite, and boost.
    ("POST", "/api/federation/notes"): _any("owner", "none"),
    ("PATCH", "/api/federation/notes/{note_id}"): _any("owner", "none"),
    ("DELETE", "/api/federation/notes/{note_id}"): _any("owner", "none"),
    ("POST", "/api/renditions/plus-one"): _any("owner", "none"),
    ("POST", "/api/federation/opinions/hide"): _any("owner", "none"),
    ("POST", "/api/federation/opinions/item"): _any("owner", "none"),
    ("POST", "/api/federation/objects/{object_id}/opinions"): _any("owner", "none"),
    ("POST", "/api/federation/timeline/{item_id}/favourite"): _any("owner", "none"),
    ("DELETE", "/api/federation/timeline/{item_id}/favourite"): _any("owner", "none"),
    ("POST", "/api/federation/timeline/{item_id}/boost"): _any("owner", "none"),
    ("DELETE", "/api/federation/timeline/{item_id}/boost"): _any("owner", "none"),
    # Quotes (FEP-044f, FEDERATION_REMOTE_READING_DESIGN.md Sections 5.4 rule 6 and 6.6 rule 5, unit FP4-9Q): the quote requests for own objects, and the quote policy of a published document.
    ("GET", "/api/federation/quotes"): _any("owner", "none"),
    ("HEAD", "/api/federation/quotes"): _any("owner", "none"),
    ("PATCH", "/api/federation/quotes/{quote_id}"): _any("owner", "none"),
    ("GET", "/render/remote/{item_id}"): _any("owner", "none"),
    ("HEAD", "/render/remote/{item_id}"): _any("owner", "none"),
    # Both modes (unit FP4-F3): the body of a feed item has media too, and feeds work in single mode (RD15).
    ("GET", "/media/remote/{url_hash}"): _any("owner", "none"),
    ("HEAD", "/media/remote/{url_hash}"): _any("owner", "none"),
    ("GET", "/api/federation/followers"): _any("owner", "none"),
    ("HEAD", "/api/federation/followers"): _any("owner", "none"),
    ("DELETE", "/api/federation/followers"): _any("owner", "none"),
    # Unit FP3-1: a publish or a retract causes an outbound activity. A ``readonly`` credential cannot call these routes (design Section 8.3 item 4).
    ("POST", "/api/federation/publish"): _any("write", "write"),
    ("POST", "/api/federation/retract"): _any("write", "write"),
    # Account feeds (FEDERATION_REMOTE_READING_DESIGN.md Section 1.3 rule 7, units FP4-F1 and FP4-F2): the owner of the account, in both modes (RD15).
    ("GET", "/api/feeds"): _any("owner", "none"),
    ("HEAD", "/api/feeds"): _any("owner", "none"),
    ("POST", "/api/feeds"): _any("owner", "none"),
    ("DELETE", "/api/feeds"): _any("owner", "none"),
    ("POST", "/api/feeds/poll"): _any("owner", "none"),
    ("GET", "/api/feeds/opml"): _any("owner", "none"),
    ("HEAD", "/api/feeds/opml"): _any("owner", "none"),
    ("POST", "/api/feeds/opml"): _any("owner", "none"),
    ("GET", "/api/feeds/items"): _any("owner", "none"),
    ("HEAD", "/api/feeds/items"): _any("owner", "none"),
    ("PATCH", "/api/feeds/items"): _any("owner", "none"),
    # Unit FP4-F3 (feed view): one item, feed discovery, ingest of one item, and the body of an item on the app origin (only without a ``usercontent`` host).
    ("GET", "/api/feeds/items/{item_id}"): _any("owner", "none"),
    ("HEAD", "/api/feeds/items/{item_id}"): _any("owner", "none"),
    ("POST", "/api/feeds/items/{item_id}/ingest"): _any("owner", "write"),
    ("POST", "/api/feeds/discover"): _any("owner", "none"),
    ("GET", "/render/feed/{item_id}"): _any("owner", "none"),
    ("HEAD", "/render/feed/{item_id}"): _any("owner", "none"),
    ("GET", "/invite/{code}"): _system("public", "none", modes=NETWORK_ONLY),
    ("HEAD", "/invite/{code}"): _system("public", "none", modes=NETWORK_ONLY),
    ("POST", "/invite/{code}"): _system("public", "none", modes=NETWORK_ONLY),
    # The public account directory, in both modes (KNOWLEDGE_FEDERATION_DESIGN.md Section 8.3 rule 2). The old path ``/accounts`` answers with a 308 redirect; the older path ``/explore/accounts`` is removed.
    ("GET", "/directory"): _system("public", "none"),
    ("HEAD", "/directory"): _system("public", "none"),
    ("GET", "/about"): _system("public", "none"),
    ("HEAD", "/about"): _system("public", "none"),
    ("GET", "/accounts"): _system("public", "none"),
    ("HEAD", "/accounts"): _system("public", "none"),
    ("GET", "/api/invites"): _any("owner", "none", modes=NETWORK_ONLY),
    ("HEAD", "/api/invites"): _any("owner", "none", modes=NETWORK_ONLY),
    ("POST", "/api/invites"): _any("owner", "none", modes=NETWORK_ONLY),
    ("PATCH", "/api/invites"): _any("owner", "none", modes=NETWORK_ONLY),
    ("GET", "/me"): _any("owner", "none"),
    ("HEAD", "/me"): _any("owner", "none"),
    ("GET", "/api/me"): _any("owner", "none"),
    ("HEAD", "/api/me"): _any("owner", "none"),
    ("PATCH", "/api/me"): _any("owner", "none"),
    ("POST", "/api/me/password"): _any("owner", "none"),
    ("POST", "/api/me/username"): _any("owner", "none"),
    # P4 계정 API 키(§11.2, 쓰기 전용)와 서버 등급(§10.3)
    ("GET", "/api/me/provider-keys"): _any("owner", "none"),
    ("HEAD", "/api/me/provider-keys"): _any("owner", "none"),
    ("PUT", "/api/me/provider-keys"): _any("owner", "none"),
    ("PATCH", "/api/me/provider-keys"): _any("owner", "none"),
    ("DELETE", "/api/me/provider-keys"): _any("owner", "none"),
    ("POST", "/api/me/provider-keys/verify"): _any("owner", "none"),
    # 유저 단위 기능(텔레그램 봇·서포트 번들·데이터 번들·백업). 자격증명 계정의 것만 다룬다(api.accounts_routes.features).
    ("GET", "/api/me/telegram"): _any("owner", "none"),
    ("HEAD", "/api/me/telegram"): _any("owner", "none"),
    ("PUT", "/api/me/telegram"): _any("owner", "none"),
    ("PATCH", "/api/me/telegram"): _any("owner", "none"),
    ("DELETE", "/api/me/telegram"): _any("owner", "none"),
    # Notifications and Web Push of the account (PWA_NOTIFICATION_DESIGN.md PN5).
    ("GET", "/api/me/notifications"): _any("owner", "none"),
    ("HEAD", "/api/me/notifications"): _any("owner", "none"),
    ("POST", "/api/me/notifications/read"): _any("owner", "none"),
    ("GET", "/api/me/push"): _any("owner", "none"),
    ("HEAD", "/api/me/push"): _any("owner", "none"),
    ("POST", "/api/me/push/subscriptions"): _any("owner", "none"),
    ("DELETE", "/api/me/push/subscriptions"): _any("owner", "none"),
    ("PUT", "/api/me/push/kinds/{kind}"): _any("owner", "none"),
    ("POST", "/api/me/push/test"): _any("owner", "none"),
    # The support bundles of /support/bundles: each account (scope "account"); the handler gives the scope "server" to the administrator only.
    ("GET", "/api/support/bundles"): _any("owner", "none"),
    ("HEAD", "/api/support/bundles"): _any("owner", "none"),
    ("POST", "/api/support/bundles"): _any("owner", "none"),
    ("DELETE", "/api/support/bundles/{bundle_id}"): _any("owner", "none"),
    ("GET", "/api/me/data-bundles"): _any("owner", "none"),
    ("HEAD", "/api/me/data-bundles"): _any("owner", "none"),
    ("POST", "/api/me/data-bundles"): _any("owner", "none"),
    ("DELETE", "/api/me/data-bundles"): _any("owner", "none"),
    ("GET", "/api/me/backups"): _any("owner", "none"),
    ("HEAD", "/api/me/backups"): _any("owner", "none"),
    ("POST", "/api/me/backups"): _any("owner", "none"),
    ("DELETE", "/api/me/backups"): _any("owner", "none"),
    ("GET", "/api/me/backups/download"): _any("owner", "none"),
    ("HEAD", "/api/me/backups/download"): _any("owner", "none"),
    ("PUT", "/api/me/backups/policy"): _any("owner", "none"),
    ("GET", "/api/admin/account-backups"): _system("admin", "none"),
    ("HEAD", "/api/admin/account-backups"): _system("admin", "none"),
    ("POST", "/api/admin/account-backups/restore"): _system("admin", "none"),
    ("GET", "/api/admin/server-tier"): _system("admin", "none"),
    ("HEAD", "/api/admin/server-tier"): _system("admin", "none"),
    ("PUT", "/api/admin/server-tier"): _system("admin", "none"),
    # P5 L5-b IdP, 계정 연결, 초대 가입, 관리자 IdP (§12, §13.3)
    ("GET", "/auth/oidc/{id}/start"): _system("public", "none"),
    ("HEAD", "/auth/oidc/{id}/start"): _system("public", "none"),
    ("GET", "/auth/oidc/{id}/callback"): _system("public", "none"),
    ("HEAD", "/auth/oidc/{id}/callback"): _system("public", "none"),
    ("POST", "/api/me/identities/oidc/{id}/start"): _any("owner", "none"),
    ("GET", "/api/me/identities"): _any("owner", "none"),
    ("HEAD", "/api/me/identities"): _any("owner", "none"),
    ("DELETE", "/api/me/identities/oidc/{id}"): _any("owner", "none"),
    ("GET", "/invite/complete"): _system("public", "none", modes=NETWORK_ONLY),
    ("HEAD", "/invite/complete"): _system("public", "none", modes=NETWORK_ONLY),
    ("POST", "/invite/complete"): _system("public", "none", modes=NETWORK_ONLY),
    ("GET", "/admin/idp"): _system("admin", "none"),
    ("HEAD", "/admin/idp"): _system("admin", "none"),
    ("GET", "/api/admin/idp"): _system("admin", "none"),
    ("HEAD", "/api/admin/idp"): _system("admin", "none"),
    ("PUT", "/api/admin/idp"): _system("admin", "none"),
    # 통합 콘솔 (admin/ 및 settings/ 기능 경로 분리, D1–D3)
    ("GET", "/admin"): _system("admin", "none"),
    ("HEAD", "/admin"): _system("admin", "none"),
    ("GET", "/admin/providers"): _system("admin", "none"),
    ("HEAD", "/admin/providers"): _system("admin", "none"),
    ("GET", "/admin/limits"): _system("admin", "none"),
    ("HEAD", "/admin/limits"): _system("admin", "none"),
    ("GET", "/admin/themes"): _system("admin", "none"),
    ("HEAD", "/admin/themes"): _system("admin", "none"),
    ("GET", "/admin/backups"): _system("admin", "none"),
    ("HEAD", "/admin/backups"): _system("admin", "none"),
    ("GET", "/admin/plugins"): _system("admin", "none"),
    ("HEAD", "/admin/plugins"): _system("admin", "none"),
    ("GET", "/api/admin/plugins"): _system("admin", "none"),
    ("HEAD", "/api/admin/plugins"): _system("admin", "none"),
    ("PUT", "/api/admin/plugins/{id}"): _system("admin", "none"),
    ("DELETE", "/api/admin/plugins/{id}"): _system("admin", "none"),
    ("GET", "/admin/storage"): _system("admin", "none"),
    ("HEAD", "/admin/storage"): _system("admin", "none"),
    ("GET", "/api/admin/storage"): _system("admin", "none"),
    ("HEAD", "/api/admin/storage"): _system("admin", "none"),
    ("PUT", "/api/admin/storage"): _system("admin", "none"),
    ("POST", "/api/admin/storage/test"): _system("admin", "none"),
    ("GET", "/admin/audit"): _system("admin", "none"),
    ("HEAD", "/admin/audit"): _system("admin", "none"),
    ("GET", "/admin/mcp"): _system("admin", "none"),
    ("HEAD", "/admin/mcp"): _system("admin", "none"),
    ("GET", "/admin/webmcp"): _system("admin", "none"),
    ("HEAD", "/admin/webmcp"): _system("admin", "none"),
    ("GET", "/api/admin/mcp/telemetry"): _system("admin", "none"),
    ("HEAD", "/api/admin/mcp/telemetry"): _system("admin", "none"),
    # The account area: the pages of the account of the caller. The old /settings/* paths of these pages below answer with a 308 redirect.
    ("GET", "/account"): _system("owner", "none"),
    ("HEAD", "/account"): _system("owner", "none"),
    ("GET", "/account/profile"): _system("owner", "none"),
    ("HEAD", "/account/profile"): _system("owner", "none"),
    ("GET", "/account/security"): _system("owner", "none"),
    ("HEAD", "/account/security"): _system("owner", "none"),
    ("GET", "/account/invites"): _system("owner", "none"),
    ("HEAD", "/account/invites"): _system("owner", "none"),
    ("GET", "/account/federation"): _system("owner", "none"),
    ("HEAD", "/account/federation"): _system("owner", "none"),
    ("GET", "/account/telegram"): _system("owner", "none"),
    ("HEAD", "/account/telegram"): _system("owner", "none"),
    ("GET", "/account/notifications"): _system("owner", "none"),
    ("HEAD", "/account/notifications"): _system("owner", "none"),
    ("GET", "/account/bundles"): _system("owner", "none"),
    ("HEAD", "/account/bundles"): _system("owner", "none"),
    ("GET", "/support/bundles"): _system("owner", "none"),
    ("HEAD", "/support/bundles"): _system("owner", "none"),
    ("GET", "/support/pipeline"): _system("owner", "none"),
    ("HEAD", "/support/pipeline"): _system("owner", "none"),
    ("GET", "/account/backups"): _system("owner", "none"),
    ("HEAD", "/account/backups"): _system("owner", "none"),
    ("GET", "/account/providers"): _system("owner", "none"),
    ("HEAD", "/account/providers"): _system("owner", "none"),
    ("GET", "/account/mcp"): _system("owner", "none"),
    ("HEAD", "/account/mcp"): _system("owner", "none"),
    ("GET", "/account/webmcp"): _system("owner", "none"),
    ("HEAD", "/account/webmcp"): _system("owner", "none"),
    ("GET", "/settings"): _system("owner", "none"),
    ("HEAD", "/settings"): _system("owner", "none"),
    ("GET", "/settings/account"): _system("owner", "none"),
    ("HEAD", "/settings/account"): _system("owner", "none"),
    ("GET", "/settings/invites"): _system("owner", "none"),
    ("HEAD", "/settings/invites"): _system("owner", "none"),
    ("GET", "/settings/telegram"): _system("owner", "none"),
    ("HEAD", "/settings/telegram"): _system("owner", "none"),
    ("GET", "/settings/notifications"): _system("owner", "none"),
    ("HEAD", "/settings/notifications"): _system("owner", "none"),
    ("GET", "/settings/bundles"): _system("owner", "none"),
    ("HEAD", "/settings/bundles"): _system("owner", "none"),
    ("GET", "/settings/backups"): _system("owner", "none"),
    ("HEAD", "/settings/backups"): _system("owner", "none"),
    ("GET", "/settings/federation"): _system("owner", "none"),
    ("HEAD", "/settings/federation"): _system("owner", "none"),
    ("GET", "/settings/keys"): _system("owner", "none"),
    ("HEAD", "/settings/keys"): _system("owner", "none"),
    ("GET", "/settings/themes"): _system("owner", "none"),
    ("HEAD", "/settings/themes"): _system("owner", "none"),
    ("GET", "/settings/reader"): _system("owner", "none"),
    ("HEAD", "/settings/reader"): _system("owner", "none"),
    ("GET", "/settings/feeds"): _system("owner", "none"),
    ("HEAD", "/settings/feeds"): _system("owner", "none"),
    ("GET", "/settings/tags"): _system("owner", "none"),
    ("HEAD", "/settings/tags"): _system("owner", "none"),
    ("GET", "/settings/rules"): _system("owner", "none"),
    ("HEAD", "/settings/rules"): _system("owner", "none"),
    ("GET", "/settings/opml"): _system("owner", "none"),
    ("HEAD", "/settings/opml"): _system("owner", "none"),
    ("GET", "/settings/mcp"): _system("owner", "none"),
    ("HEAD", "/settings/mcp"): _system("owner", "none"),
    ("GET", "/settings/webmcp"): _system("owner", "none"),
    ("HEAD", "/settings/webmcp"): _system("owner", "none"),
    ("GET", "/settings/kb"): _system("owner", "none"),
    ("HEAD", "/settings/kb"): _system("owner", "none"),
    ("GET", "/settings/kb/manage"): _system("owner", "none"),
    ("HEAD", "/settings/kb/manage"): _system("owner", "none"),
    ("GET", "/settings/kb/{kb_number:int}"): _system("owner", "none"),
    ("HEAD", "/settings/kb/{kb_number:int}"): _system("owner", "none"),
    # Moderation console routes are separate root-only system surfaces.
    ("GET", "/moderation"): _system("owner", "none"),
    ("HEAD", "/moderation"): _system("owner", "none"),
    ("GET", "/moderation/kb"): _system("owner", "none"),
    ("HEAD", "/moderation/kb"): _system("owner", "none"),
    ("GET", "/moderation/kb/{kb_number:int}"): _system("owner", "none"),
    ("HEAD", "/moderation/kb/{kb_number:int}"): _system("owner", "none"),
    ("GET", "/moderation/oversight"): _system("admin", "none"),
    ("HEAD", "/moderation/oversight"): _system("admin", "none"),
    ("GET", "/moderation/themes"): _system("admin", "none"),
    ("HEAD", "/moderation/themes"): _system("admin", "none"),
    ("POST", "/api/client-extraction/stage"): _any("write", "write"),
    ("GET", "/api/client-extraction/sources"): _any("write", "write"),
    ("HEAD", "/api/client-extraction/sources"): _any("write", "write"),
    ("POST", "/api/client-extraction/sources/{document_id}/claim"): _any("write", "write"),
    ("POST", "/api/client-extraction/sources/{document_id}/renew"): _any("write", "write"),
    ("GET", "/api/client-extraction/spec"): _any("read", "none"),
    ("HEAD", "/api/client-extraction/spec"): _any("read", "none"),
    ("GET", "/api/client-extraction/sources/{document_id}"): _any("write", "write"),
    ("HEAD", "/api/client-extraction/sources/{document_id}"): _any("write", "write"),
    ("POST", "/api/client-extraction/sources/{document_id}/submit"): _any("write", "write"),
    # Versioned public REST API (/api/v1/*, docs/design/API_V1_AND_REFERENCE_DESIGN.md).
    ("GET", "/api/v1/health"): _system("public", "none"),
    ("HEAD", "/api/v1/health"): _system("public", "none"),
    ("GET", "/api/v1/me"): _system("read", "none"),
    ("HEAD", "/api/v1/me"): _system("read", "none"),
    ("GET", "/api/v1/kbs"): _system("read", "none"),
    ("HEAD", "/api/v1/kbs"): _system("read", "none"),
    ("POST", "/api/v1/kbs"): _system("owner", "none"),
    ("GET", "/api/v1/themes"): _system("read", "none"),
    ("HEAD", "/api/v1/themes"): _system("read", "none"),
    ("POST", "/api/v1/kbs/personal/ingest"): _system("write", "write"),
    ("POST", "/api/v1/kbs/personal/ingest/stream"): _system("write", "write"),
    ("GET", "/api/v1/kbs/{kb}"): _system("read", "read"),
    ("HEAD", "/api/v1/kbs/{kb}"): _system("read", "read"),
    ("PATCH", "/api/v1/kbs/{kb}"): _system("owner", "settings"),
    ("DELETE", "/api/v1/kbs/{kb}"): _system("admin", "delete", step_up=True),
    ("GET", "/api/v1/kbs/{kb}/documents"): _system("read", "read"),
    ("HEAD", "/api/v1/kbs/{kb}/documents"): _system("read", "read"),
    ("GET", "/api/v1/kbs/{kb}/documents/{doc_id}"): _system("read", "read"),
    ("HEAD", "/api/v1/kbs/{kb}/documents/{doc_id}"): _system("read", "read"),
    ("GET", "/api/v1/kbs/{kb}/documents/{doc_id}/raw"): _system("read", "read"),
    ("HEAD", "/api/v1/kbs/{kb}/documents/{doc_id}/raw"): _system("read", "read"),
    ("GET", "/api/v1/kbs/{kb}/documents/{doc_id}/export"): _system("read", "read"),
    ("HEAD", "/api/v1/kbs/{kb}/documents/{doc_id}/export"): _system("read", "read"),
    ("PATCH", "/api/v1/kbs/{kb}/documents/{doc_id}"): _system("owner", "curate"),
    ("GET", "/api/v1/kbs/{kb}/documents/{doc_id}/renditions"): _system("read", "read"),
    ("HEAD", "/api/v1/kbs/{kb}/documents/{doc_id}/renditions"): _system("read", "read"),
    ("POST", "/api/v1/kbs/{kb}/documents/{doc_id}/renditions"): _system("owner", "curate"),
    ("DELETE", "/api/v1/kbs/{kb}/documents/{doc_id}/renditions/{template}"): _system("owner", "curate"),
    ("DELETE", "/api/v1/kbs/{kb}/documents/{doc_id}"): _system("owner", "remove"),
    ("POST", "/api/v1/kbs/{kb}/search"): _system("read", "read"),
    ("GET", "/api/v1/kbs/{kb}/graph"): _system("read", "read"),
    ("HEAD", "/api/v1/kbs/{kb}/graph"): _system("read", "read"),
    ("GET", "/api/v1/kbs/{kb}/entities/{entity_id}"): _system("read", "read"),
    ("HEAD", "/api/v1/kbs/{kb}/entities/{entity_id}"): _system("read", "read"),
    ("GET", "/api/v1/kbs/{kb}/entities/{entity_id}/neighbors"): _system("read", "read"),
    ("HEAD", "/api/v1/kbs/{kb}/entities/{entity_id}/neighbors"): _system("read", "read"),
    ("GET", "/api/v1/kbs/{kb}/graph/path"): _system("read", "read"),
    ("HEAD", "/api/v1/kbs/{kb}/graph/path"): _system("read", "read"),
    ("GET", "/api/v1/kbs/{kb}/contributions"): _system("read", "read"),
    ("HEAD", "/api/v1/kbs/{kb}/contributions"): _system("read", "read"),
    ("POST", "/api/v1/kbs/{kb}/contributions"): _system("write", "contribute_direct"),
    ("PATCH", "/api/v1/kbs/{kb}/contributions/{number}"): _system("owner", "review"),
    ("DELETE", "/api/v1/kbs/{kb}/contributions/{number}"): _system("write", "read"),
    ("POST", "/api/v1/kbs/{kb}/contributions/{number}/retry"): _system("write", "read"),
    ("POST", "/api/v1/kbs/{kb}/contributions/{number}/remove"): _system("owner", "remove"),
    ("GET", "/api/v1/kbs/{kb}/members"): _system("read", "read"),
    ("HEAD", "/api/v1/kbs/{kb}/members"): _system("read", "read"),
    ("POST", "/api/v1/kbs/{kb}/members"): _system("owner", "join"),
    ("DELETE", "/api/v1/kbs/{kb}/members"): _system("owner", "join"),
    ("PATCH", "/api/v1/kbs/{kb}/members/{account}"): _system("owner", "people"),
    ("GET", "/api/v1/extraction/spec"): _system("public", "none"),
    ("HEAD", "/api/v1/extraction/spec"): _system("public", "none"),
    ("POST", "/api/v1/extraction/stage"): _system("write", "write"),
    ("GET", "/api/v1/extraction/sources"): _system("write", "write"),
    ("HEAD", "/api/v1/extraction/sources"): _system("write", "write"),
    ("GET", "/api/v1/extraction/sources/{document_id}"): _system("write", "write"),
    ("HEAD", "/api/v1/extraction/sources/{document_id}"): _system("write", "write"),
    ("POST", "/api/v1/extraction/sources/{document_id}/claim"): _system("write", "write"),
    ("POST", "/api/v1/extraction/sources/{document_id}/renew"): _system("write", "write"),
    ("POST", "/api/v1/extraction/sources/{document_id}/submit"): _system("write", "write"),
    ("GET", "/api/v1/federation/status"): _system("owner", "none"),
    ("HEAD", "/api/v1/federation/status"): _system("owner", "none"),
    ("POST", "/api/v1/federation/publish"): _system("write", "write"),
    ("POST", "/api/v1/federation/retract"): _system("write", "write"),
    ("GET", "/api/v1/feeds"): _system("owner", "none"),
    ("HEAD", "/api/v1/feeds"): _system("owner", "none"),
    ("POST", "/api/v1/feeds"): _system("owner", "none"),
    ("DELETE", "/api/v1/feeds"): _system("owner", "none"),
    ("GET", "/api/v1/feeds/items"): _system("owner", "none"),
    ("HEAD", "/api/v1/feeds/items"): _system("owner", "none"),
    ("PATCH", "/api/v1/feeds/items"): _system("owner", "none"),
    ("POST", "/api/v1/feeds/items/{item_id}/ingest"): _system("owner", "write"),
    ("GET", "/api/v1/me/oauth/grants"): _system("owner", "none"),
    ("HEAD", "/api/v1/me/oauth/grants"): _system("owner", "none"),
    ("DELETE", "/api/v1/me/oauth/grants"): _system("owner", "none"),
    ("GET", "/api/v1/support/bundles"): _system("owner", "none"),
    ("HEAD", "/api/v1/support/bundles"): _system("owner", "none"),
    ("POST", "/api/v1/support/bundles"): _system("owner", "none"),
    ("DELETE", "/api/v1/support/bundles/{bundle_id}"): _system("owner", "none"),
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


def _is_test_domain_host(host: str) -> bool:
    """Return True when host is a DNS name below the special-use top-level label test (RFC 6761)."""
    try:
        normalized = _parse_dns_name(host)
    except ValueError:
        return False
    labels = normalized.split(".")
    return len(labels) >= 2 and labels[-1] == "test"


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
        raise ValueError("Public URL must use the service root path")
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
            if address is not None:
                if not isinstance(address, ipaddress.IPv4Address) or address.is_unspecified:
                    raise ValueError("development public URL must use a concrete IPv4 address")
                if parsed.scheme != "http":
                    raise ValueError("development public URL must use http")
            else:
                if not _is_test_domain_host(host):
                    raise ValueError("development public URL must use a concrete IPv4 address or a DNS name below .test")
                if parsed.scheme != "https":
                    raise ValueError("development public URL must use https for a .test name")
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
                fqdn_text = str(fqdn_val).strip()
                fqdn_host = fqdn_text.split(":", 1)[0].lower()
                scheme = "https" if (environment == "production" or (environment == "development" and _is_test_domain_host(fqdn_host))) else "http"
                raw_pub = f"{scheme}://{fqdn_text}/"
        if not raw_pub:
            raw_pub = _setting(settings, "public_url")

        _, authority, public_origin = _parse_url(
            str(raw_pub), environment=environment
        )

        origins: set[str] = set()
        for raw_origin in _split_origins(
            getattr(settings, "cors_allowed_origins", "") or ""
        ):
            _, _, origin = _parse_url(
                raw_origin, environment=environment, origin_only=True
            )
            origins.add(origin)

        ga_id = str(getattr(settings, "ga_measurement_id", "") or "").strip()

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


# 세션 쿠키 이름(§5.2, Rev. 2.3). 운영 환경(``secure_cookie``)은 ``__Host-`` 접두로 서브도메인의 쿠키 토싱을 막는다.
# development(HTTP)는 ``__Host-``를 쓸 수 없으므로 옛 이름을 유지한다.
SESSION_COOKIE_NAME = "__Host-claire_session"
LEGACY_SESSION_COOKIE_NAME = "claire_session"
# P3 G9에서 True로 바뀌며 운영 환경 과도기 폴백이 완전히 제거된다(§5.2, §17.2).
LEGACY_COOKIE_FALLBACK_REMOVED = True
_SESSION_COOKIE_NAMES = (SESSION_COOKIE_NAME, LEGACY_SESSION_COOKIE_NAME)
OAUTH_TX_COOKIE_NAME = "__Host-claire_oauth_tx"
LEGACY_OAUTH_TX_COOKIE_NAME = "claire_oauth_tx"


def oauth_tx_cookie_name(config: WebRuntimeConfig) -> str:
    return OAUTH_TX_COOKIE_NAME if config.secure_cookie else LEGACY_OAUTH_TX_COOKIE_NAME


def set_oauth_tx_cookie(
    response: Response,
    config: WebRuntimeConfig,
    tx_id: str,
    *,
    max_age: int = 600,
) -> None:
    response.set_cookie(
        oauth_tx_cookie_name(config),
        tx_id,
        max_age=max_age,
        path="/",
        secure=config.secure_cookie,
        httponly=True,
        samesite="lax",
    )


def clear_oauth_tx_cookie(response: Response, config: WebRuntimeConfig) -> None:
    _delete_cookie(response, oauth_tx_cookie_name(config), config)
    if config.secure_cookie:
        _delete_cookie(response, LEGACY_OAUTH_TX_COOKIE_NAME, config)


def read_oauth_tx_cookie(scope: Scope) -> str | None:
    """Read the opaque OAuth login transaction cookie with __Host- priority and single-occurrence enforcement."""
    values = _raw_headers(scope, b"cookie")
    if not values:
        return None
    decoded = [value.decode("latin-1") for value in values]
    host_count = _cookie_occurrences(decoded, OAUTH_TX_COOKIE_NAME)
    if host_count == 1:
        return _cookie_value(decoded, OAUTH_TX_COOKIE_NAME)
    if host_count > 1:
        return None
    legacy_count = _cookie_occurrences(decoded, LEGACY_OAUTH_TX_COOKIE_NAME)
    if legacy_count != 1:
        return None
    return _cookie_value(decoded, LEGACY_OAUTH_TX_COOKIE_NAME)


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
    server_role: Literal["admin", "member"]
    credential_id: str
    authenticated_at: float | None = None
    # accounts.locale of the bound account. The environment token credential does not read the database and has None.
    locale: str | None = None
    # accounts.entity_language of the bound account. None for the environment token credential.
    entity_language: str | None = None

    def principal(self, channel: Literal["cookie", "bearer", "x-session"]) -> Principal:
        return Principal(
            account_id=self.account_id,
            server_role=self.server_role,
            scope=self.scope,  # type: ignore[arg-type]  # 허용 판정이 scope 값을 거른 뒤에만 쓴다
            channel=channel,
            credential_id=self.credential_id,
            # 사람이 인증한 시각은 브라우저 세션 채널에서만 의미가 있다(§8.5).
            authenticated_at=(
                self.authenticated_at if channel in ("cookie", "x-session") else None
            ),
            locale=self.locale,  # type: ignore[arg-type]  # AccountStore returns 'en', 'ko' or None
            entity_language=self.entity_language,
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
        server_role=account.role,
        credential_id=credential_id,
        authenticated_at=authenticated_at,
        locale=account.locale,
        entity_language=account.entity_language,
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


async def resolve_optional_session_principal(config: WebRuntimeConfig, scope: Scope) -> Principal | None:
    """Resolve an optional browser session Principal from ``scope`` using S11 cookie-tossing protection. Returns None when no valid session cookie is present."""
    cookie_present, cookie_token = read_session_cookie(scope)
    if not cookie_present or not cookie_token:
        return None
    if not Path(config.db_file).is_file():
        return None
    credential = await _validate_session(config, cookie_token)
    if credential is None:
        return None
    return credential.principal("cookie")


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
                scopes=("owner",),
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


def step_up_response(principal: Principal) -> Response | None:
    """The answer of a step-up route (Section 6.8) when the request has no recent sign-in in a session, else None. Same answers as ``accounts_routes.common.require_recent_auth``."""
    if principal.channel not in ("cookie", "x-session"):
        return JSONResponse({"error": "step_up_requires_session"}, status_code=403)
    if not has_recent_auth(principal):
        return JSONResponse(
            {"error": "reauth_required", "reauth": "/api/auth/reauth"},
            status_code=401,
            headers={"WWW-Authenticate": 'Session realm="claire", error="reauth_required"'},
        )
    return None


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


# Each answer of the public listener has this header. A federation readiness check uses it to tell an answer of Claire from an answer of the sidecar, a CDN, or another intermediary (FEDERATION_GO_LIVE_DESIGN.md Section 5.2). The value is a constant: the header shows only that the software is Claire, as the marker header of the sidecar does.
SERVER_MARKER_HEADER = "x-claire-server"
_SERVER_MARKER = (SERVER_MARKER_HEADER.encode("ascii"), b"api")


class ServerMarkerMiddleware:
    """Add ``SERVER_MARKER_HEADER`` to each HTTP answer."""

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        async def send_marked(message: Message) -> None:
            if message["type"] == "http.response.start":
                headers = [(key, value) for key, value in message.get("headers") or () if key.lower() != _SERVER_MARKER[0]]
                headers.append(_SERVER_MARKER)
                message = {**message, "headers": headers}
            await send(message)

        await self.app(scope, receive, send_marked)


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
        elif origin.strip().lower() == "null":
            await _send_response(
                PlainTextResponse("Forbidden", status_code=403), scope, receive, send
            )
            return
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

    def _mode(self) -> ServerMode:
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
            # 계정 홈을 만들 수 없으면 서버 루트로 보낸다(관리자 이름으로 대신하지 않는다).
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
                exchanged: tuple[str, str] | None = None
                if len(token_values) == 1 and token_values[0]:
                    exchanged = await _bootstrap_session(self.config, token_values[0])
                if exchanged is None or exchanged[0] not in {"owner", "write", "readonly"}:
                    response = RedirectResponse("/", status_code=302)
                    clear_session_cookie(response, self.config)
                    response.headers["Cache-Control"] = "no-store"
                    response.headers["Referrer-Policy"] = "no-referrer"
                    await _send_response(response, scope, receive, send)
                    return
                _session_scope, cookie_token = exchanged
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
                    or (credential is not None and credential.server_role == "admin")
                )
            )
            or (
                auth_scope == "write"
                and access in {"read", "write"}
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
            if (
                credential is not None
                and auth_scope in ("readonly", "write")
                and access in ("write", "owner")
                and rule.capability != kb_access.NO_KB
                and not (isinstance(parsed, ParsedPrefix) and parsed.username is not None)
            ):
                # SHARED_KNOWLEDGE_BASES_AND_THEMES_DESIGN.md Section 6.6 and namespace design Section 9.2: on a route that acts on a knowledge base, a credential of an account whose scope is too small gets 403. Under ``/@{u}/`` the namespace can be of another account, so the answer stays the 404 that hides it. Account and console routes keep the 404.
                await _send_response(
                    JSONResponse({"error": "scope_insufficient", "scope": auth_scope, "required": minimum_scope(rule)}, status_code=403),
                    scope,
                    receive,
                    send,
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
            resolved_principal = credential.principal(auth_channel)
            state[PRINCIPAL_STATE_KEY] = resolved_principal
            if resolved_principal.account_id is not None and cookie_present:
                from ..store.secrets import BYOK_SEAL_COOKIE, register_volatile_user_seal

                decoded_cookies = [value.decode("latin-1") for value in _raw_headers(scope, b"cookie")]
                seal_cookie = _cookie_value(decoded_cookies, BYOK_SEAL_COOKIE)
                if seal_cookie:
                    register_volatile_user_seal(resolved_principal.account_id, seal_cookie)
        else:
            # 허용 판정을 통과했는데 자격증명이 없으면 배선 결함이다(C1).
            raise RuntimeError("authenticated request without a resolved credential")
        if rule.step_up:
            # SHARED_KNOWLEDGE_BASES_AND_THEMES_DESIGN.md Section 6.8: a recent sign-in in a session. A bearer credential never passes, so it never changes a role or a standing rule.
            refused = step_up_response(state[PRINCIPAL_STATE_KEY])
            if refused is not None:
                await _send_response(refused, scope, receive, send)
                return
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
        if getattr(namespace, "pinned_kb_id", None) is not None:
            extra = state.get(NAMESPACE_EXTRA_STATE_KEY) or {}
            if extra.get("ga_measurement_id"):
                return self._ga_csp_header_value
            return self._no_ga_csp_header_value
        if getattr(namespace, "account_id", None) is not None:
            return self._default_csp_header_value
        return self.csp_header_value

    @property
    def csp_header_value(self) -> bytes:
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
                csp_val = self._default_csp_header_value if (300 <= status < 400 and state.get(NAMESPACE_STATE_KEY) is None) else self._csp_for(state)
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

    def _root(self, mode: ServerMode, scope: Scope) -> _Outcome | Response:
        if mode == "single":
            return super()._root(mode, scope)
        query_string = scope.get("query_string", b"") or b""
        query_params = parse_qs(query_string.decode("latin-1"), keep_blank_values=True)
        if query_params.get("pwa") == ["1"]:
            return _Outcome(ResolvedNamespace(mode, None, None, None, "", False))
        if _spa_query(query_string):
            return super()._root(mode, scope)
        return _Outcome(ResolvedNamespace(mode, None, None, None, "", False))


def _prototype_app(roles: HostRoles) -> ASGIApp | None:
    """The static prototype sub-application (RD20), or None when the environment has no prototype host (``prototype_host.prototype_authority``)."""
    if roles.prototype is None:
        return None
    from .prototype_host import create_prototype_app

    return create_prototype_app()


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

    ``SafeAccessLog`` → (``CloudflareIPFilter``) → ``HostRouter``. ``HostRouter``의 app 역할만 PrefixParser → CORS → BodyLimit → ErrorBoundary → Authentication → I18n → NamespaceResolver → 라우터를 지난다. usercontent·media 하위 앱(P2에서는 모든 경로 404)과 레거시 호스트 응답은 쿠키를 읽지도 쓰지도 않는다.
    ``legacy_hosts``가 없으면 레거시 호스트가 없는 것으로 본다(``create_app``이 ``SqliteLegacyHostDirectory``를 넘긴다).
    ``namespace_directory``가 없으면 ``settings.db_file``로 ``SqliteNamespaceDirectory``를 만든다.
    """
    from .host_router import EmptyLegacyHostDirectory, HostRoles, HostRouter, not_found_app

    if theme_manager is None:
        try:
            from ..store.knowledge_base import get_kb_manager

            theme_manager = get_kb_manager(settings)
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
    # I18nMiddleware is immediately inside Authentication: it reads Principal.locale.
    from .i18n_middleware import I18nMiddleware

    secured = I18nMiddleware(secured, default_lang=str(getattr(settings, "ui_lang", "") or ""))
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
        prototype=_prototype_app(roles),
    )
    if config.cloudflare_ips_only:
        secured = CloudflareIPFilterMiddleware(secured)
    secured = SafeAccessLogMiddleware(secured, config, theme_manager=theme_manager)
    secured = ServerMarkerMiddleware(secured)
    # The internal federation API (docs/implementation/FEDERATION.md) is selected by the listener, not by the Host header. It is outside all public middleware.
    data_dir = getattr(settings, "data_dir", None)
    if data_dir is None:
        return secured
    # The edge listener (CLAIRE_EDGE_LISTENER=1) is outside the public middleware: each one then reads the peer address that the edge saw.
    if getattr(settings, "edge_listener", False) is True:
        from .edge_listener import EdgePeerMiddleware, edge_socket_path

        secured = EdgePeerMiddleware(secured, edge_socket_path(data_dir))
    from .federation_internal import ListenerRouter, create_internal_federation_app, federation_socket_path

    from .federation_routes import federation_internal_routes

    internal = create_internal_federation_app(settings, routes=federation_internal_routes(settings))
    return ListenerRouter(secured, internal, federation_socket_path(data_dir))
