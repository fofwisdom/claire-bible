"""웹 API의 ASGI 보안 경계.

Starlette 라우트와 Uvicorn 실행기는 이 모듈 안쪽에 둔다. 여기서는 public URL에서
도출한 정확한 Host, CORS/Origin, 요청 크기, 인증 scope와 query 없는 접근 로그를
한 번에 적용한다. Reverse proxy 헤더는 의도적으로 읽지 않는다.
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
from typing import Any, Literal
from urllib.parse import parse_qs, urlsplit

from starlette.exceptions import HTTPException
from starlette.requests import Request
from starlette.responses import (
    JSONResponse,
    PlainTextResponse,
    RedirectResponse,
    Response,
)
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from ..store import db as dbm

__all__ = [
    "MAX_REQUEST_BODY",
    "ROUTE_POLICY",
    "ErrorBoundaryMiddleware",
    "InvalidJSONBody",
    "RequestBodyTooLarge",
    "WebRuntimeConfig",
    "read_json_body",
    "request_auth_scope",
    "request_id",
    "wrap_web_app",
]

log = logging.getLogger("claire.api.access")

MAX_REQUEST_BODY = 1024 * 1024
_AUTH_SCOPE_KEY = "claire_auth_scope"
_REQUEST_ID_KEY = "claire_request_id"
_ORIGIN_KIND_KEY = "claire_origin_kind"
_DNS_NAME_RE = re.compile(
    r"(?=.{1,253}\Z)(?:[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\.)*"
    r"[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\Z",
    re.IGNORECASE,
)
_AUTHORITY_RE = re.compile(
    r"(?P<host>[A-Za-z0-9.-]+)(?::(?P<port>[0-9]{1,5}))?\Z"
)
_BEARER_RE = re.compile(r"Bearer[ \t]+([^ \t,]+)\Z", re.IGNORECASE)


def _build_content_security_policy(ga_measurement_id: str = "") -> str:
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
    return (
        "default-src 'self'; "
        f"script-src {script_src}; "
        "style-src 'self' 'unsafe-inline' https://fonts.googleapis.com https://unpkg.com; "
        "img-src 'self' data: https:; "
        f"connect-src {connect_src}; "
        "font-src 'self' data: https://fonts.gstatic.com https://unpkg.com; "
        "object-src 'none'; "
        "base-uri 'none'; "
        "form-action 'self'; "
        "frame-ancestors 'none'"
    )


_CONTENT_SECURITY_POLICY = _build_content_security_policy()

AccessLevel = Literal["public", "read", "owner"]
AuthScope = Literal["public", "anonymous", "readonly", "owner"]
RouteKey = tuple[str, str]


@dataclass(frozen=True, slots=True)
class RouteRule:
    access: AccessLevel


def _rule(access: AccessLevel) -> RouteRule:
    return RouteRule(access)


# 등록되지 않은 (method, path)는 fail-closed(404)다. Starlette는 GET Route에 HEAD를
# 자동 추가하므로 HEAD도 명시해 실제 라우터와 인증 계약을 정확히 맞춘다.
ROUTE_POLICY: Mapping[RouteKey, RouteRule] = {
    ("GET", "/health"): _rule("public"),
    ("HEAD", "/health"): _rule("public"),
    ("GET", "/favicon.ico"): _rule("public"),
    ("HEAD", "/favicon.ico"): _rule("public"),
    ("GET", "/favicon.svg"): _rule("public"),
    ("HEAD", "/favicon.svg"): _rule("public"),
    ("GET", "/apple-touch-icon.png"): _rule("public"),
    ("HEAD", "/apple-touch-icon.png"): _rule("public"),
    ("GET", "/apple-touch-icon-precomposed.png"): _rule("public"),
    ("HEAD", "/apple-touch-icon-precomposed.png"): _rule("public"),
    ("GET", "/manifest.json"): _rule("public"),
    ("HEAD", "/manifest.json"): _rule("public"),
    ("GET", "/site.webmanifest"): _rule("public"),
    ("HEAD", "/site.webmanifest"): _rule("public"),
    ("GET", "/browserconfig.xml"): _rule("public"),
    ("HEAD", "/browserconfig.xml"): _rule("public"),
    ("GET", "/icon"): _rule("public"),
    ("HEAD", "/icon"): _rule("public"),
    ("GET", "/font"): _rule("public"),
    ("HEAD", "/font"): _rule("public"),
    ("GET", "/fonts/NotoSansKR-Regular.woff2"): _rule("public"),
    ("HEAD", "/fonts/NotoSansKR-Regular.woff2"): _rule("public"),
    ("GET", "/fonts/NotoSansKR-Bold.woff2"): _rule("public"),
    ("HEAD", "/fonts/NotoSansKR-Bold.woff2"): _rule("public"),
    ("GET", "/fonts/NotoSerifKR-Regular.woff2"): _rule("public"),
    ("HEAD", "/fonts/NotoSerifKR-Regular.woff2"): _rule("public"),
    ("GET", "/fonts/NotoSerifKR-Bold.woff2"): _rule("public"),
    ("HEAD", "/fonts/NotoSerifKR-Bold.woff2"): _rule("public"),
    ("GET", "/fonts/D2Coding.woff2"): _rule("public"),
    ("HEAD", "/fonts/D2Coding.woff2"): _rule("public"),
    ("GET", "/fonts/D2CodingBold.woff2"): _rule("public"),
    ("HEAD", "/fonts/D2CodingBold.woff2"): _rule("public"),
    ("GET", "/p"): _rule("public"),
    ("HEAD", "/p"): _rule("public"),
    ("GET", "/image"): _rule("public"),
    ("HEAD", "/image"): _rule("public"),
    ("GET", "/"): _rule("read"),
    ("HEAD", "/"): _rule("read"),
    ("GET", "/whoami"): _rule("read"),
    ("HEAD", "/whoami"): _rule("read"),
    ("GET", "/stats"): _rule("read"),
    ("HEAD", "/stats"): _rule("read"),
    ("GET", "/graph"): _rule("read"),
    ("HEAD", "/graph"): _rule("read"),
    ("GET", "/node"): _rule("read"),
    ("HEAD", "/node"): _rule("read"),
    ("GET", "/documents"): _rule("read"),
    ("HEAD", "/documents"): _rule("read"),
    ("GET", "/document"): _rule("read"),
    ("HEAD", "/document"): _rule("read"),
    ("POST", "/search"): _rule("read"),
    ("POST", "/mcp"): _rule("read"),
    ("GET", "/mcp"): _rule("read"),
    ("HEAD", "/mcp"): _rule("read"),
    ("POST", "/ingest"): _rule("owner"),
    ("POST", "/ingest-stream"): _rule("owner"),
    ("POST", "/document/seen"): _rule("owner"),
    ("POST", "/document/pin"): _rule("owner"),
    ("POST", "/document/hide"): _rule("owner"),
    ("POST", "/document/title"): _rule("owner"),
    ("POST", "/synthesize"): _rule("owner"),
    ("POST", "/research"): _rule("owner"),
    ("POST", "/dedup/scan"): _rule("owner"),
    ("POST", "/dedup/merge"): _rule("owner"),
    ("POST", "/share"): _rule("owner"),
}


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
    db_file: Any = field(repr=False)
    ga_measurement_id: str = ""

    @classmethod
    def from_settings(cls, settings: Any) -> WebRuntimeConfig:
        raw_environment = str(_setting(settings, "environment"))
        environment = raw_environment.strip().lower()
        if raw_environment != environment:
            raise ValueError("CLAIRE_ENVIRONMENT must use its canonical lowercase value")
        if environment not in {"development", "production"}:
            raise ValueError("CLAIRE_ENVIRONMENT must be development or production")
        _, authority, public_origin = _parse_url(
            str(_setting(settings, "public_url")), environment=environment
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

        return cls(
            environment=environment,
            public_origin=public_origin,
            expected_authority=authority,
            cors_allowed_origins=frozenset(origins),
            secure_cookie=environment == "production",
            anonymous_readonly=anonymous_readonly,
            owner_token=owner_token,
            readonly_token=readonly_token,
            db_file=_setting(settings, "db_file"),
            ga_measurement_id=ga_id,
        )


async def read_json_body(
    request: Request, *, max_bytes: int = MAX_REQUEST_BODY
) -> dict[str, Any]:
    """JSON body를 제한 크기 안에서 읽는다.

    InvalidJSONBody(400)와 RequestBodyTooLarge(413)는 Starlette의 HTTPException
    처리기를 통과하므로 handler가 넓은 ``except Exception``으로 덮어쓰면 안 된다.
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


def _set_session_cookie(
    response: Response,
    config: WebRuntimeConfig,
    token: str,
) -> None:
    response.set_cookie(
        "claire_session",
        token,
        max_age=int(dbm.SESSION_TTL),
        path="/",
        secure=config.secure_cookie,
        httponly=True,
        samesite="lax",
    )


def _clear_session_cookie(
    response: Response,
    config: WebRuntimeConfig,
) -> None:
    response.delete_cookie(
        "claire_session",
        path="/",
        secure=config.secure_cookie,
        httponly=True,
        samesite="lax",
    )


def _session_cookie_header(config: WebRuntimeConfig, token: str) -> bytes:
    response = Response()
    _set_session_cookie(response, config, token)
    return next(
        value
        for name, value in response.raw_headers
        if name.lower() == b"set-cookie"
    )


def _clear_session_cookie_header(config: WebRuntimeConfig) -> bytes:
    response = Response()
    _clear_session_cookie(response, config)
    return next(
        value
        for name, value in response.raw_headers
        if name.lower() == b"set-cookie"
    )


def _session_cookie(scope: Scope) -> tuple[bool, str | None]:
    values = _raw_headers(scope, b"cookie")
    if not values:
        return False, None
    decoded = [value.decode("latin-1") for value in values]
    occurrences = sum(
        1
        for header in decoded
        for item in header.split(";")
        if item.strip().split("=", 1)[0].strip() == "claire_session"
    )
    if occurrences == 0:
        return False, None
    if occurrences != 1:
        return True, None
    try:
        cookie = SimpleCookie()
        for value in decoded:
            cookie.load(value)
    except Exception:  # noqa: BLE001
        return True, None
    morsel = cookie.get("claire_session")
    return True, morsel.value if morsel is not None else None


async def _validate_session(config: WebRuntimeConfig, token: str) -> str | None:
    if not dbm.plausible_session_token(token):
        return None

    def _validate() -> str | None:
        conn = dbm.connect_existing(config.db_file)
        try:
            return dbm.validate_session_scope(conn, token)
        finally:
            conn.close()

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


async def _send_response(response: Response, scope: Scope, receive: Receive, send: Send) -> None:
    await response(scope, receive, send)


class HostAuthorityMiddleware:
    def __init__(self, app: ASGIApp, config: WebRuntimeConfig) -> None:
        self.app = app
        self.config = config

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        hosts = _raw_headers(scope, b"host")
        if len(hosts) != 1:
            await _send_response(
                PlainTextResponse("Bad Request", status_code=400), scope, receive, send
            )
            return
        try:
            raw_host = hosts[0].decode("ascii")
        except UnicodeDecodeError:
            raw_host = ""
        authority = _parse_authority(raw_host)
        if authority is None:
            await _send_response(
                PlainTextResponse("Bad Request", status_code=400), scope, receive, send
            )
            return
        if authority != self.config.expected_authority:
            await _send_response(
                PlainTextResponse("Misdirected Request", status_code=421),
                scope,
                receive,
                send,
            )
            return
        await self.app(scope, receive, send)


class CORSPolicyMiddleware:
    _ALLOWED_METHODS = frozenset({"GET", "HEAD", "POST"})
    _ALLOWED_HEADERS = frozenset({"authorization", "content-type"})

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
            route_rule = ROUTE_POLICY.get((method, scope.get("path", "")))
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
) -> Response:
    body = json.dumps({"error": error, "error_description": description}).encode("utf-8")
    www_auth = f'Bearer error="{error}", error_description="{description}"'
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
    def __init__(self, app: ASGIApp, config: WebRuntimeConfig) -> None:
        self.app = app
        self.config = config

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        method = str(scope.get("method", "")).upper()
        path = scope.get("path", "")
        rule = ROUTE_POLICY.get((method, path))
        if rule is None:
            await _send_response(
                PlainTextResponse("Not Found", status_code=404), scope, receive, send
            )
            return

        state = scope.setdefault("state", {})
        if path == "/" and method in {"GET", "HEAD"}:
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
                        _clear_session_cookie(response, self.config)
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
                        _clear_session_cookie(response, self.config)
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
                if session_scope not in {"owner", "readonly"}:
                    if self.config.anonymous_readonly:
                        response = RedirectResponse("/", status_code=302)
                        _clear_session_cookie(response, self.config)
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
                response = RedirectResponse("/", status_code=302)
                _set_session_cookie(response, self.config, cookie_token)
                response.headers["Cache-Control"] = "no-store"
                response.headers["Referrer-Policy"] = "no-referrer"
                await _send_response(response, scope, receive, send)
                return

        if rule.access == "public":
            state[_AUTH_SCOPE_KEY] = "public"
            await self.app(scope, receive, send)
            return

        origin_kind = state.get(_ORIGIN_KIND_KEY, "none")
        if _raw_headers(scope, b"x-token"):
            if path == "/mcp":
                await _send_response(
                    _mcp_auth_error_response(
                        401, "invalid_token", "X-Token header not supported"
                    ),
                    scope,
                    receive,
                    send,
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
                await _send_response(
                    _mcp_auth_error_response(
                        401, "invalid_token", "Multiple X-Session headers provided"
                    ),
                    scope,
                    receive,
                    send,
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
        cookie_present, cookie_token = _session_cookie(scope)
        x_session_present = bool(x_session_token)

        credentials_count = (
            int(authorization_present)
            + int(cookie_present)
            + int(x_session_present)
        )
        if credentials_count > 1:
            if path == "/mcp":
                await _send_response(
                    _mcp_auth_error_response(
                        401,
                        "invalid_token",
                        "Multiple authentication credentials provided",
                    ),
                    scope,
                    receive,
                    send,
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
                await _send_response(
                    _mcp_auth_error_response(
                        403, "insufficient_scope", "Forbidden"
                    ),
                    scope,
                    receive,
                    send,
                )
                return
            await _send_response(
                PlainTextResponse("Forbidden", status_code=403), scope, receive, send
            )
            return

        if authorization_present and bearer is None:
            if path == "/mcp":
                await _send_response(
                    _mcp_auth_error_response(
                        401, "invalid_token", "Invalid authorization header"
                    ),
                    scope,
                    receive,
                    send,
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
        cookie_invalid_or_expired = False

        if bearer is not None:
            if _constant_equal(bearer, self.config.owner_token):
                auth_scope, auth_channel = "owner", "bearer"
            elif _constant_equal(bearer, self.config.readonly_token):
                auth_scope, auth_channel = "readonly", "bearer"
            elif origin_kind != "cross":
                auth_scope = await _validate_session(self.config, bearer)
                auth_channel = "bearer" if auth_scope is not None else None

        if origin_kind == "cross" and auth_channel != "bearer":
            if path == "/mcp":
                await _send_response(
                    _mcp_auth_error_response(
                        403,
                        "insufficient_scope",
                        "Cross-origin requests require Bearer token",
                    ),
                    scope,
                    receive,
                    send,
                )
                return
            await _send_response(
                PlainTextResponse("Forbidden", status_code=403), scope, receive, send
            )
            return

        if auth_scope is None and origin_kind != "cross":
            if cookie_present:
                if cookie_token:
                    auth_scope = await _validate_session(self.config, cookie_token)
                    if auth_scope is not None:
                        auth_channel = "cookie"
                    else:
                        cookie_invalid_or_expired = True
                else:
                    cookie_invalid_or_expired = True
            elif x_session_token:
                auth_scope = await _validate_session(self.config, x_session_token)
                auth_channel = "x-session" if auth_scope is not None else None

        if (
            auth_channel == "cookie"
            and method == "POST"
            and origin_kind != "same"
        ):
            if path == "/mcp":
                await _send_response(
                    _mcp_auth_error_response(
                        403, "insufficient_scope", "Forbidden"
                    ),
                    scope,
                    receive,
                    send,
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
            and rule.access == "read"
            and path != "/mcp"
            and origin_kind in {"none", "same"}
        ):
            auth_scope = "anonymous"
            auth_channel = (
                "anonymous_cleared_cookie"
                if cookie_present
                else "anonymous"
            )

        allowed = auth_scope == "owner" or (
            auth_scope in {"anonymous", "readonly"}
            and rule.access == "read"
            and (path != "/mcp" or auth_scope != "anonymous")
        )
        if not allowed:
            if path == "/mcp":
                desc = (
                    "Invalid token"
                    if credential_present
                    else "Authentication required"
                )
                await _send_response(
                    _mcp_auth_error_response(401, "invalid_token", desc),
                    scope,
                    receive,
                    send,
                )
                return
            response = PlainTextResponse("Not Found", status_code=404)
            if cookie_present and cookie_invalid_or_expired:
                _clear_session_cookie(response, self.config)
            await _send_response(response, scope, receive, send)
            return

        state[_AUTH_SCOPE_KEY] = auth_scope
        if auth_channel == "cookie" and cookie_token:
            refreshed_cookie = _session_cookie_header(self.config, cookie_token)

            async def refresh_cookie_send(message: Message) -> None:
                if message["type"] == "http.response.start":
                    headers = list(message.get("headers", []))
                    headers.append((b"set-cookie", refreshed_cookie))
                    message = {**message, "headers": headers}
                await send(message)

            await self.app(scope, receive, refresh_cookie_send)
            return

        if auth_channel == "anonymous_cleared_cookie":
            cleared_cookie = _clear_session_cookie_header(self.config)

            async def clear_cookie_send(message: Message) -> None:
                if message["type"] == "http.response.start":
                    headers = list(message.get("headers", []))
                    headers.append((b"set-cookie", cleared_cookie))
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
        self, app: ASGIApp, config: WebRuntimeConfig | None = None
    ) -> None:
        self.app = app
        self.config = config
        ga_id = config.ga_measurement_id if config else ""
        self.csp_header_value = _build_content_security_policy(ga_id).encode("ascii")

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
                add_header(b"x-frame-options", b"DENY")
                route_rule = ROUTE_POLICY.get(
                    (
                        str(scope.get("method", "")).upper(),
                        str(scope.get("path", "")),
                    )
                )
                if route_rule is None or route_rule.access != "public":
                    add_header(b"cache-control", b"no-store")
                add_header(
                    b"content-security-policy",
                    self.csp_header_value,
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


def wrap_web_app(app: ASGIApp, settings: Any) -> ASGIApp:
    """Starlette 앱 바깥에 보안 경계를 조립한다."""

    config = WebRuntimeConfig.from_settings(settings)
    secured: ASGIApp = AuthenticationMiddleware(app, config)
    secured = ErrorBoundaryMiddleware(secured)
    secured = BodyLimitMiddleware(secured)
    secured = CORSPolicyMiddleware(secured, config)
    secured = HostAuthorityMiddleware(secured, config)
    return SafeAccessLogMiddleware(secured, config)
