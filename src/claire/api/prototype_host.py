"""Static prototype sub-application of the ``prototype`` host role (federation remote reading design, RD20).

The role serves one static page with mock data at ``/``. Every other path is 404. The page has no API route and gets no data from the server.
``HostRouter`` removes the ``Cookie`` request header and every ``Set-Cookie`` response header for this role, so the sub-application reads and writes no cookie.

The package copy ``templates/prototype/federation_client_prototype.html`` is the served file. ``docs/design/prototypes/federation_client_prototype.html`` is the design copy. A test makes sure that the two files are identical.
"""

from __future__ import annotations

from pathlib import Path

from starlette.types import Receive, Scope, Send

__all__ = ["PROTOTYPE_CSP", "PROTOTYPE_PAGE", "create_prototype_app"]

PROTOTYPE_PAGE = Path(__file__).resolve().parent.parent / "templates" / "prototype" / "federation_client_prototype.html"

# The page has one inline <style>, one inline <script>, inline event handlers, and style attributes. It loads no other resource and opens no network connection.
PROTOTYPE_CSP = (
    "default-src 'none'; "
    "script-src 'unsafe-inline'; "
    "style-src 'unsafe-inline'; "
    "img-src data:; "
    "connect-src 'none'; "
    "font-src 'none'; "
    "media-src 'none'; "
    "object-src 'none'; "
    "frame-src 'none'; "
    "worker-src 'none'; "
    "manifest-src 'none'; "
    "base-uri 'none'; "
    "form-action 'none'; "
    "frame-ancestors 'none'"
)

_COMMON_HEADERS: tuple[tuple[bytes, bytes], ...] = (
    (b"content-security-policy", PROTOTYPE_CSP.encode("ascii")),
    (b"x-robots-tag", b"noindex, nofollow"),
    (b"x-frame-options", b"DENY"),
    (b"x-content-type-options", b"nosniff"),
    (b"cache-control", b"no-cache"),
)


def create_prototype_app(page: Path = PROTOTYPE_PAGE):
    """Return the ASGI sub-application. The page is read once, when the application starts."""
    body = page.read_bytes()

    async def app(scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            return
        method = str(scope.get("method", "")).upper()
        if scope.get("path") == "/" and method in {"GET", "HEAD"}:
            status, content_type, content = 200, b"text/html; charset=utf-8", body
        else:
            status, content_type, content = 404, b"text/plain; charset=utf-8", b"Not Found"
        headers = [*_COMMON_HEADERS, (b"content-type", content_type), (b"content-length", str(len(content)).encode("ascii"))]
        await send({"type": "http.response.start", "status": status, "headers": headers})
        await send({"type": "http.response.body", "body": b"" if method == "HEAD" else content})

    return app
