"""호스트 역할 판정과 하위 앱 분기 (설계 §5.1–§5.3, Rev. 2.3).

``HostRouter``는 ``SafeAccessLog``·``CloudflareIPFilter`` 바로 안쪽에서 ``Host`` 헤더(포트 포함 authority)로 역할을 정한다.

- ``app``: 기존 보안 래퍼(``PrefixParser`` → CORS → BodyLimit → ErrorBoundary → Authentication → NamespaceResolver → 라우터)
- ``usercontent``·``media``: 각자의 하위 앱. 쿠키를 읽지도 쓰지도 않는다(§5.2). P2에서는 하위 앱이 비어 있어 모든 경로가 404다(렌더링 표면은 P3 L3-h, 미디어는 P7)
- ``legacy``: ``legacy_hosts``에 있는 옛 테마 FQDN. 일몰 전 ``GET``·``HEAD``는 정본 테마 경로로 308, 그 밖의 메서드는 421, 일몰 뒤 410(또는 정책상 421)
- 그 밖: 421

``GateMiddleware``는 없앴다. 정적 자산도 이 라우터와 보안 헤더 안쪽을 지난다.
"""

from __future__ import annotations

import ipaddress
import time
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any, Literal, Protocol
from urllib.parse import quote

from starlette.responses import PlainTextResponse, Response
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from .security import (
    _parse_authority,
    _parse_dns_name,
    _parse_url,
    _raw_headers,
    _split_origins,
)

__all__ = [
    "EmptyLegacyHostDirectory",
    "HostRole",
    "HostRoles",
    "HostRouter",
    "LegacyHost",
    "LegacyHostDirectory",
    "LegacyHostPolicy",
    "not_found_app",
]

HostRole = Literal["app", "usercontent", "media", "legacy"]


@dataclass(frozen=True, slots=True)
class HostRoles:
    """역할 호스트 설정(§5.1). authority는 소문자 ``host[:port]``다(개발 환경은 ``IPv4:포트``)."""

    scheme: Literal["http", "https"]
    app: str
    usercontent: str | None
    media: str | None
    # 운영 환경의 등록 도메인(``CLAIRE_SITE_DOMAIN``). PSL로 추정하지 않는다(C1).
    site_domain: str | None

    @classmethod
    def from_settings(cls, settings: Any, *, environment: str, app_authority: str) -> HostRoles:
        """설정을 검증해 만든다. 다음이면 ``ValueError``로 기동을 거부한다.

        - 역할 호스트끼리 같거나, app 호스트의 상위 도메인이다
        - 운영 환경에서 app 외 역할 호스트가 있는데 ``CLAIRE_SITE_DOMAIN``이 없거나, 어떤 역할 호스트가 그 값과 같지도 그 서브도메인도 아니다
        - 개발 환경에서 역할 호스트가 app과 같은 IPv4가 아니거나 포트가 같다

        값 형식(O14)은 ``CLAIRE_FQDN``과 같다. 운영 환경은 포트 없는 DNS 이름(``usercontent.example.com``), 개발 환경은 ``IPv4:포트`` authority(``127.0.0.1:8767``)다. 빈 값이면 그 역할을 두지 않는다.
        ``CLAIRE_CORS_ALLOWED_ORIGINS``에 역할 호스트의 origin이 있어도 거부한다(§5.2: usercontent·media origin은 app의 CORS 허용 목록에 넣지 않는다).
        """
        if environment not in {"development", "production"}:
            raise ValueError("environment must be development or production")
        scheme: Literal["http", "https"] = "https" if environment == "production" else "http"
        app = _parse_authority(str(app_authority))
        if app is None:
            raise ValueError(f"invalid app authority: {app_authority!r}")

        usercontent = _role_authority(
            getattr(settings, "usercontent_fqdn", "") or "", "CLAIRE_USERCONTENT_FQDN", environment
        )
        media = _role_authority(getattr(settings, "media_fqdn", "") or "", "CLAIRE_MEDIA_FQDN", environment)
        site_domain = _site_domain(getattr(settings, "site_domain", "") or "")

        named = [("app", app), ("usercontent", usercontent), ("media", media)]
        present = [(name, value) for name, value in named if value is not None]

        if environment == "production":
            # 쿠키는 포트로 나뉘지 않으므로 운영 환경은 호스트 이름으로 비교한다.
            hosts = [(name, value.split(":", 1)[0]) for name, value in present]
            seen: dict[str, str] = {}
            for name, host in hosts:
                if host in seen:
                    raise ValueError(f"role hosts must differ: {seen[host]} and {name} are both {host!r}")
                seen[host] = name
            app_host = hosts[0][1]
            for name, host in hosts[1:]:
                if app_host.endswith("." + host):
                    raise ValueError(f"{name} host {host!r} must not be a parent domain of the app host")
            if len(hosts) > 1 and site_domain is None:
                raise ValueError("CLAIRE_SITE_DOMAIN is required in production when role hosts other than app are set")
            if site_domain is not None:
                for name, host in hosts:
                    if host != site_domain and not host.endswith("." + site_domain):
                        raise ValueError(f"{name} host {host!r} must equal CLAIRE_SITE_DOMAIN or be its subdomain")
        else:
            app_ip, app_port = _split_ipv4_authority(app)
            for name, value in present[1:]:
                ip, port = _split_ipv4_authority(value)
                if ip != app_ip:
                    raise ValueError(f"development {name} host must use the app IPv4 address {app_ip}")
                if port == app_port:
                    raise ValueError(f"development {name} host must use a port different from the app port")
            if usercontent is not None and media is not None and usercontent == media:
                raise ValueError("role hosts must differ: usercontent and media are the same authority")

        roles = cls(scheme=scheme, app=app, usercontent=usercontent, media=media, site_domain=site_domain)
        role_origins = {roles.origin(name) for name, _ in present[1:]}
        for raw_origin in _split_origins(getattr(settings, "cors_allowed_origins", "") or ""):
            try:
                _, _, normalized = _parse_url(raw_origin, environment=environment, origin_only=True)
            except ValueError:
                continue  # 형식 오류는 WebRuntimeConfig가 따로 거부한다.
            if normalized in role_origins:
                raise ValueError(f"CLAIRE_CORS_ALLOWED_ORIGINS must not contain the role host origin {normalized!r}")
        return roles

    def role_of(self, authority: str) -> HostRole | None:
        """app·usercontent·media 중 하나, 아니면 None(레거시 여부는 ``LegacyHostDirectory``가 본다)."""
        if authority == self.app:
            return "app"
        if self.usercontent is not None and authority == self.usercontent:
            return "usercontent"
        if self.media is not None and authority == self.media:
            return "media"
        return None

    def origin(self, role: Literal["app", "usercontent", "media"]) -> str | None:
        """``{scheme}://{authority}``. 설정되지 않은 역할이면 None. CSP·링크가 쓴다."""
        authority = {"app": self.app, "usercontent": self.usercontent, "media": self.media}.get(role)
        return None if authority is None else f"{self.scheme}://{authority}"


def _role_authority(raw: Any, name: str, environment: str) -> str | None:
    value = str(raw)
    if not value.strip():
        return None
    if value != value.strip():
        raise ValueError(f"{name} must not contain outer whitespace")
    if environment == "production":
        if ":" in value:
            raise ValueError(f"{name} must be a DNS host name without a port in production")
        try:
            ipaddress.ip_address(value)
        except ValueError:
            pass
        else:
            raise ValueError(f"{name} must be a DNS host name in production")
        try:
            return _parse_dns_name(value)
        except ValueError as exc:
            raise ValueError(f"{name}: {exc}") from exc
    authority = _parse_authority(value)
    if authority is None:
        raise ValueError(f"{name} must be an IPv4:port authority in development")
    host, sep, port = authority.partition(":")
    try:
        address = ipaddress.IPv4Address(host)
    except ValueError as exc:
        raise ValueError(f"{name} must be an IPv4:port authority in development") from exc
    if not sep or address.is_unspecified:
        raise ValueError(f"{name} must be a concrete IPv4:port authority in development")
    # 브라우저는 기본 포트를 Host에 싣지 않는다(``_parse_url``의 정규화와 같다).
    return host if port == "80" else authority


def _site_domain(raw: Any) -> str | None:
    value = str(raw)
    if not value.strip():
        return None
    if value != value.strip():
        raise ValueError("CLAIRE_SITE_DOMAIN must not contain outer whitespace")
    try:
        ipaddress.ip_address(value)
    except ValueError:
        pass
    else:
        raise ValueError("CLAIRE_SITE_DOMAIN must be a DNS name")
    try:
        return _parse_dns_name(value)
    except ValueError as exc:
        raise ValueError(f"CLAIRE_SITE_DOMAIN: {exc}") from exc


def _split_ipv4_authority(authority: str) -> tuple[str, int]:
    host, sep, port = authority.partition(":")
    try:
        ipaddress.IPv4Address(host)
    except ValueError as exc:
        raise ValueError(f"development host must be an IPv4 authority: {authority!r}") from exc
    return host, int(port) if sep else 80


@dataclass(frozen=True, slots=True)
class LegacyHost:
    """``legacy_hosts`` 행(§13.2)."""

    host: str
    theme_id: int
    created_at: float
    activated_at: float | None   # 일몰 시계 시작. None이면 아직 활성화하지 않음(무기한 308)
    sunset_at: float | None      # None이면 유예·무기한 308


@dataclass(frozen=True, slots=True)
class LegacyHostPolicy:
    """``instance_settings.legacy_host_policy``(JSON). 일몰 뒤 응답과 기본 유예 일수."""

    after_sunset_status: Literal[410, 421] = 410
    sunset_days: int = 30


class LegacyHostDirectory(Protocol):
    def lookup(self, host: str) -> LegacyHost | None:
        """포트를 뗀 소문자 호스트 이름으로 찾는다. 메모리 캐시와 변경 시 무효화는 구현 몫이다."""
        ...

    def policy(self) -> LegacyHostPolicy: ...

    def theme_location(self, legacy: LegacyHost) -> str | None:
        """정본 테마 경로(§5.3). 싱글 모드 ``/themes/{n}/``, 네트워크 모드 ``/@{owner}/themes/{n}/``. 테마가 사라졌으면 None(421)."""
        ...


class HostRouter:
    def __init__(
        self,
        *,
        app: ASGIApp,
        roles: HostRoles,
        legacy: LegacyHostDirectory,
        usercontent: ASGIApp | None = None,
        media: ASGIApp | None = None,
        now: Callable[[], float] | None = None,
    ) -> None:
        self.app = app
        self.roles = roles
        self.legacy = legacy
        self.usercontent = usercontent
        self.media = media
        self.now = now

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        hosts = _raw_headers(scope, b"host")
        authority: str | None = None
        if len(hosts) == 1:
            try:
                authority = _parse_authority(hosts[0].decode("ascii"))
            except UnicodeDecodeError:
                authority = None
        if authority is None:
            await _plain(400, "Bad Request")(scope, receive, send)
            return

        role = self.roles.role_of(authority)
        if role == "app":
            await self.app(scope, receive, send)
            return
        if role in ("usercontent", "media"):
            sub = self.usercontent if role == "usercontent" else self.media
            if sub is None:
                await _plain(421, "Misdirected Request")(scope, receive, send)
                return
            await sub(_without_cookies(scope), receive, _cookieless_send(send))
            return

        legacy = self.legacy.lookup(authority.split(":", 1)[0])
        if legacy is None:
            await _plain(421, "Misdirected Request")(scope, receive, send)
            return
        await self._legacy_response(scope, legacy)(scope, receive, send)

    def _legacy_response(self, scope: Scope, legacy: LegacyHost) -> Response:
        """§5.3. 쿠키를 읽지 않고, ``Set-Cookie``·CORS 헤더를 붙이지 않는다."""
        now = self.now() if self.now is not None else time.time()
        if legacy.sunset_at is not None and now >= legacy.sunset_at:
            status = self.legacy.policy().after_sunset_status
            return _plain(status, "Gone" if status == 410 else "Misdirected Request")
        if str(scope.get("method", "")).upper() not in {"GET", "HEAD"}:
            return _plain(421, "Misdirected Request")
        base = self.legacy.theme_location(legacy)
        app_origin = self.roles.origin("app")
        if base is None or app_origin is None:
            return _plain(421, "Misdirected Request")
        location = app_origin + base.rstrip("/") + "/" + _request_path(scope).lstrip("/")
        query = scope.get("query_string", b"")
        if query:
            location += "?" + quote(query.decode("latin-1"), safe=_QUERY_SAFE)
        response = Response(status_code=308, headers={"Location": location})
        return response


# RFC 3986 pchar와 이미 인코딩된 %XX는 그대로 둔다. 그 밖(공백, 제어 문자, 비ASCII)은 인코딩한다.
_PATH_SAFE = "/%:@!$&'()*+,;=-._~"
_QUERY_SAFE = _PATH_SAFE + "?"


def _request_path(scope: Scope) -> str:
    raw_path = scope.get("raw_path")
    if isinstance(raw_path, (bytes, bytearray)):
        path = bytes(raw_path).split(b"?", 1)[0].decode("latin-1")
    else:
        path = str(scope.get("path", "/"))
    return quote(path, safe=_PATH_SAFE)


def _plain(status: int, text: str) -> Response:
    return PlainTextResponse(text, status_code=status)


def _without_cookies(scope: Scope) -> Scope:
    """하위 앱(usercontent·media)은 ``Cookie``를 인증에 절대 쓰지 않는다(§5.2). 헤더째 지운다."""
    headers = [(name, value) for name, value in scope.get("headers", []) if name.lower() != b"cookie"]
    return {**scope, "headers": headers}


def _cookieless_send(send: Send) -> Send:
    async def wrapped(message: Message) -> None:
        if message["type"] == "http.response.start":
            headers = [
                (name, value)
                for name, value in message.get("headers", [])
                if name.lower() != b"set-cookie"
            ]
            message = {**message, "headers": headers}
        await send(message)

    return wrapped


async def not_found_app(scope: Scope, receive: Receive, send: Send) -> None:
    """P2의 usercontent·media 하위 앱. 모든 경로가 404다(렌더링 표면은 P3 L3-h, 미디어는 P7)."""
    if scope["type"] != "http":
        return
    await _plain(404, "Not Found")(scope, receive, send)


class EmptyLegacyHostDirectory:
    """레거시 호스트가 없는 디렉터리. ``wrap_web_app``에 디렉터리를 주지 않았을 때 쓴다."""

    def lookup(self, host: str) -> LegacyHost | None:
        return None

    def policy(self) -> LegacyHostPolicy:
        return LegacyHostPolicy()

    def theme_location(self, legacy: LegacyHost) -> str | None:
        return None
