"""Internal federation API: listener, sub-application, service key, and signed requests.

Design: ``docs/design/ACCOUNT_IDP_AND_FEDIFY_SIDECAR_INTEGRATION_DESIGN.md`` Section 6.1 (unit FP1-1).

- The ``api`` process opens a second listener on a Unix domain socket (``data/run/federation.sock``, mode 0660).
- ``ListenerRouter`` selects the internal sub-application from the listener that accepted the connection (ASGI ``scope["server"]``). It does not read the ``Host`` header.
- The internal sub-application has its own route table. It does not use ``ROUTE_POLICY`` and it does not read a ``Principal`` credential.
- Each request must have a timestamp header and a signature header. The signature is HMAC-SHA256 with the service key (``data/auth/fedify_service.key``).

Signature input (UTF-8, lines divided by ``\\n``)::

    {METHOD}\\n{request target}\\n{timestamp}\\n{SHA-256 of the body, lowercase hex}

The request target is the path as sent on the wire. If the request has a query string, the target also contains ``?`` and the query string. The timestamp is Unix time in seconds as decimal digits. The signature header value is lowercase hex.
"""

from __future__ import annotations

import hashlib
import hmac
import logging
import os
import secrets
import socket
import stat
import time
from collections.abc import Callable, Sequence
from pathlib import Path
from typing import Any

from starlette.applications import Starlette
from starlette.exceptions import HTTPException
from starlette.requests import Request
from starlette.responses import JSONResponse
from starlette.routing import BaseRoute, Mount
from starlette.types import ASGIApp, Message, Receive, Scope, Send

__all__ = [
    "INTERNAL_PREFIX",
    "MAX_BODY_BYTES",
    "MAX_REQUEST_AGE_SECONDS",
    "SERVICE_KEY_BYTES",
    "SIGNATURE_HEADER",
    "TIMESTAMP_HEADER",
    "ListenerRouter",
    "ServiceKeyError",
    "SignedRequestMiddleware",
    "create_internal_federation_app",
    "ensure_service_key",
    "federation_socket_path",
    "load_service_key",
    "open_federation_socket",
    "service_key_path",
    "sign_request",
]

INTERNAL_PREFIX = "/internal/federation/v1"
TIMESTAMP_HEADER = "x-claire-timestamp"
SIGNATURE_HEADER = "x-claire-signature"
MAX_REQUEST_AGE_SECONDS = 60
MAX_BODY_BYTES = 1024 * 1024
SERVICE_KEY_BYTES = 32
SOCKET_MODE = 0o660

_log = logging.getLogger("claire.federation.internal")


class ServiceKeyError(Exception):
    """The service key file is absent or is not valid."""


# --- Paths -------------------------------------------------------------------


def federation_socket_path(data_dir: str | Path) -> Path:
    """Absolute path of the internal listener socket. The listener and ``ListenerRouter`` must use the same value."""
    return Path(os.path.abspath(Path(data_dir) / "run" / "federation.sock"))


def service_key_path(data_dir: str | Path) -> Path:
    return Path(data_dir) / "auth" / "fedify_service.key"


# --- Service key -------------------------------------------------------------


def ensure_service_key(path: Path) -> bool:
    """Make the service key file if it does not exist. Return ``True`` when this call made the file.

    An existing file is not changed, but its mode is set to 0600.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        fd = os.open(str(path), os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    except FileExistsError:
        os.chmod(path, 0o600)
        load_service_key(path)
        return False
    with open(fd, "wb") as f:
        f.write(secrets.token_bytes(SERVICE_KEY_BYTES))
    os.chmod(path, 0o600)
    return True


def load_service_key(path: Path) -> bytes:
    try:
        raw = path.read_bytes()
    except OSError as exc:
        raise ServiceKeyError(f"service key file is not readable: {path}") from exc
    if len(raw) != SERVICE_KEY_BYTES:
        raise ServiceKeyError(f"service key file must contain {SERVICE_KEY_BYTES} bytes: {path}")
    return raw


# --- Signature ---------------------------------------------------------------


def sign_request(key: bytes, method: str, target: str, timestamp: str, body: bytes) -> str:
    """Return the signature header value for one request."""
    message = "\n".join((method.upper(), target, timestamp, hashlib.sha256(body).hexdigest()))
    return hmac.new(key, message.encode("utf-8"), hashlib.sha256).hexdigest()


def _request_target(scope: Scope) -> str:
    raw_path = scope.get("raw_path")
    path = raw_path.decode("latin-1") if isinstance(raw_path, (bytes, bytearray)) else str(scope.get("path", ""))
    query = scope.get("query_string") or b""
    return f"{path}?{query.decode('latin-1')}" if query else path


def _header(scope: Scope, name: str) -> str | None:
    """Return the value of a header that occurs one time. A header that occurs more than one time is not valid."""
    wanted = name.encode("latin-1")
    values = [value for key, value in scope.get("headers") or () if key.lower() == wanted]
    if len(values) != 1:
        return None
    return values[0].decode("latin-1")


def _error(status: int, code: str) -> JSONResponse:
    return JSONResponse({"error": code}, status_code=status, headers={"Cache-Control": "no-store"})


class SignedRequestMiddleware:
    """Refuse a request that has no valid service signature. The check occurs before routing."""

    def __init__(self, app: ASGIApp, key_path: Path, *, now: Callable[[], float] = time.time) -> None:
        self.app = app
        self._key_path = key_path
        self._now = now
        self._key: bytes | None = None

    def _load_key(self) -> bytes:
        if self._key is None:
            self._key = load_service_key(self._key_path)
        return self._key

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            if scope["type"] == "websocket":
                await send({"type": "websocket.close", "code": 1008})
            return
        try:
            key = self._load_key()
        except ServiceKeyError as exc:
            _log.error("internal federation request refused: %s", exc)
            await _error(503, "service_key_unavailable")(scope, receive, send)
            return

        timestamp = _header(scope, TIMESTAMP_HEADER)
        signature = _header(scope, SIGNATURE_HEADER)
        if timestamp is None or signature is None:
            await self._refuse(scope, receive, send, "missing_signature")
            return
        if not (timestamp.isascii() and timestamp.isdigit() and len(timestamp) <= 12):
            await self._refuse(scope, receive, send, "invalid_timestamp")
            return
        if abs(self._now() - int(timestamp)) > MAX_REQUEST_AGE_SECONDS:
            await self._refuse(scope, receive, send, "stale_timestamp")
            return

        declared = _header(scope, "content-length")
        if declared is not None and declared.isascii() and declared.isdigit() and int(declared) > MAX_BODY_BYTES:
            await _error(413, "body_too_large")(scope, receive, send)
            return
        chunks: list[bytes] = []
        size = 0
        while True:
            message = await receive()
            if message["type"] != "http.request":
                return
            chunk = message.get("body", b"")
            size += len(chunk)
            if size > MAX_BODY_BYTES:
                await _error(413, "body_too_large")(scope, receive, send)
                return
            chunks.append(chunk)
            if not message.get("more_body", False):
                break
        body = b"".join(chunks)

        expected = sign_request(key, str(scope["method"]), _request_target(scope), timestamp, body)
        if not hmac.compare_digest(expected.encode("ascii"), signature.encode("latin-1")):
            await self._refuse(scope, receive, send, "invalid_signature")
            return

        sent = False

        async def replay() -> Message:
            nonlocal sent
            if not sent:
                sent = True
                return {"type": "http.request", "body": body, "more_body": False}
            return await receive()

        await self.app(scope, replay, send)

    async def _refuse(self, scope: Scope, receive: Receive, send: Send, reason: str) -> None:
        _log.warning("internal federation request refused: %s %s (%s)", scope.get("method"), scope.get("path"), reason)
        # All signature failures have the same response body.
        await _error(401, "invalid_signature")(scope, receive, send)


# --- Sub-application ---------------------------------------------------------


async def _http_error(_request: Request, exc: Exception) -> JSONResponse:
    status = exc.status_code if isinstance(exc, HTTPException) else 500
    code = {404: "not_found", 405: "method_not_allowed"}.get(status, "error")
    return _error(status, code)


async def _server_error(_request: Request, exc: Exception) -> JSONResponse:
    _log.error("internal federation handler failed: %s", type(exc).__name__)
    return _error(500, "internal_error")


def create_internal_federation_app(
    settings: Any,
    *,
    routes: Sequence[BaseRoute] = (),
    now: Callable[[], float] = time.time,
) -> ASGIApp:
    """Make the internal sub-application.

    ``routes`` is the route table under ``INTERNAL_PREFIX``. The table is separate from ``ROUTE_POLICY``. A path that is not in the table returns 404 after the signature check.
    """
    app = Starlette(
        routes=[Mount(INTERNAL_PREFIX, routes=list(routes))],
        exception_handlers={HTTPException: _http_error, Exception: _server_error},
    )
    return SignedRequestMiddleware(app, service_key_path(settings.data_dir), now=now)


# --- Listener ----------------------------------------------------------------


class ListenerRouter:
    """Send a connection from the internal socket to the internal sub-application. Send all other connections to the public application.

    uvicorn sets ``scope["server"]`` to ``(socket path, None)`` for a Unix socket (FT7). A client cannot change this value.
    """

    def __init__(self, public: ASGIApp, internal: ASGIApp, socket_path: Path) -> None:
        self.app = public
        self.internal = internal
        self._socket_path = str(socket_path)

    def is_internal(self, scope: Scope) -> bool:
        server = scope.get("server")
        return isinstance(server, (tuple, list)) and len(server) == 2 and server[1] is None and server[0] == self._socket_path

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "lifespan" and self.is_internal(scope):
            await self.internal(scope, receive, send)
            return
        await self.app(scope, receive, send)


def open_federation_socket(path: Path) -> socket.socket:
    """Bind the internal listener socket with mode 0660. Remove a socket file that an earlier process left."""
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        if stat.S_ISSOCK(os.lstat(path).st_mode):
            os.unlink(path)
    except FileNotFoundError:
        pass
    sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    # The umask makes the mode correct at the time of the bind. No other thread runs at this time.
    previous = os.umask(0o777 & ~SOCKET_MODE)
    try:
        sock.bind(str(path))
    except OSError:
        sock.close()
        raise
    finally:
        os.umask(previous)
    os.chmod(path, SOCKET_MODE)
    sock.set_inheritable(True)
    return sock
