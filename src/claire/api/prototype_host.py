"""Static prototype sub-application of the ``prototype`` host role (federation remote reading design, RD20).

The role serves one static page with mock data at ``/``. Every other path is 404. The page has no API route and gets no data from the server.
``HostRouter`` removes the ``Cookie`` request header and every ``Set-Cookie`` response header for this role, so the sub-application reads and writes no cookie.

The host name is not a setting. It is computed from ``CLAIRE_FQDN`` (``prototype_authority``):

- production: ``prototype.`` + the registrable domain of the app host, from the Public Suffix List snapshot that the ``publicsuffixlist`` package bundles (no network fetch). ``cb.netspheres.org`` gives ``prototype.netspheres.org``. An IP address, ``localhost``, a single label, or a host without a registrable domain gives no prototype host.
- development: the app IPv4 address with the fixed port ``DEV_PROTOTYPE_PORT`` (``127.0.0.1:8769``). ``docker-compose.dev.yml`` publishes the same port.

The package copy ``templates/prototype/federation_client_prototype.html`` is the served file. ``docs/design/prototypes/federation_client_prototype.html`` is the design copy. A test makes sure that the two files are identical.
"""

from __future__ import annotations

import ipaddress
from functools import lru_cache
from pathlib import Path

from starlette.types import Receive, Scope, Send

__all__ = ["DEV_PROTOTYPE_PORT", "PROTOTYPE_CSP", "PROTOTYPE_PAGE", "create_prototype_app", "prototype_authority", "registrable_domain"]

# Fixed development port of the prototype role. ``docker-compose.dev.yml`` publishes the same literal port.
DEV_PROTOTYPE_PORT = 8769
_PROTOTYPE_LABEL = "prototype"

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

@lru_cache(maxsize=1)
def _public_suffix_list():
    from publicsuffixlist import PublicSuffixList

    # ``source=None`` reads the list file bundled in the package. Only the separate ``publicsuffixlist.update`` script downloads a list, and Claire never runs it.
    # ``accept_unknown=False``: a host under a TLD that the list does not know (``localhost``, ``internal``) has no registrable domain.
    return PublicSuffixList(source=None, accept_unknown=False)


def registrable_domain(host: str) -> str | None:
    """The registrable domain (public suffix + one label) of a DNS host name, or None.

    None for an empty value, an IP address, a single label such as ``localhost``, a public suffix itself, or a host under an unknown TLD. A port and a final dot are removed. The result is lowercase.
    """
    value = str(host or "").strip().lower()
    if value.startswith("["):
        return None
    name, sep, port = value.rpartition(":")
    if sep and port.isdigit():
        value = name
    value = value.rstrip(".")
    if not value or "." not in value:
        return None
    try:
        ipaddress.ip_address(value)
    except ValueError:
        pass
    else:
        return None
    if any(not label for label in value.split(".")):
        return None
    return _public_suffix_list().privatesuffix(value)


def prototype_authority(app_authority: str, environment: str) -> str | None:
    """The authority of the prototype role for an app authority (``CLAIRE_FQDN``), or None when the environment has no prototype host."""
    app = str(app_authority or "").strip().lower()
    if environment == "development":
        ip, sep, _port = app.rpartition(":")
        try:
            address = ipaddress.IPv4Address(ip if sep else app)
        except ValueError:
            return None
        if address.is_unspecified:
            return None
        return f"{address}:{DEV_PROTOTYPE_PORT}"
    if environment != "production":
        return None
    domain = registrable_domain(app)
    return None if domain is None else f"{_PROTOTYPE_LABEL}.{domain}"


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
