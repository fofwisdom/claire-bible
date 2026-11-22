"""Edge listener: the public application on a Unix socket for the edge service (docs/implementation/FEDERATION.md Section 10.1, FEDERATION_GO_LIVE_DESIGN.md Section 8.4).

- With ``CLAIRE_EDGE_LISTENER=1`` the ``api`` process opens a listener on ``data/run/edge.sock`` (mode 0660). The edge service (nginx) sends all requests of Claire to it.
- A connection on a Unix socket has no peer address. The edge gives the address of its own peer (the external reverse proxy) in the header ``X-Claire-Edge-Peer``. ``EdgePeerMiddleware`` puts that address in the ASGI ``client`` value and removes the header. Thus each rule that reads the peer address (the Cloudflare filter, the trusted proxies, the access log) works as without the edge.
- The header has a meaning only on this listener. The listener is selected by the socket that accepted the connection (ASGI ``scope["server"]``), which a client cannot change. On each other listener the header is not read.
- A request on the edge listener without one valid address in the header gets 400. The middleware does not guess an address.
"""

from __future__ import annotations

import ipaddress
import os
from pathlib import Path

from starlette.types import ASGIApp, Receive, Scope, Send

__all__ = ["EDGE_PEER_HEADER", "EdgePeerMiddleware", "edge_socket_path"]

EDGE_PEER_HEADER = "x-claire-edge-peer"
_EDGE_PEER_HEADER = EDGE_PEER_HEADER.encode("ascii")


def edge_socket_path(data_dir: str | Path) -> Path:
    """Absolute path of the edge listener socket. The listener and ``EdgePeerMiddleware`` must use the same value."""
    return Path(os.path.abspath(Path(data_dir) / "run" / "edge.sock"))


class EdgePeerMiddleware:
    """Give a request of the edge listener the peer address that the edge saw."""

    def __init__(self, app: ASGIApp, socket_path: Path) -> None:
        self.app = app
        self._socket_path = str(socket_path)

    def is_edge(self, scope: Scope) -> bool:
        server = scope.get("server")
        return isinstance(server, (tuple, list)) and len(server) == 2 and server[1] is None and server[0] == self._socket_path

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] not in ("http", "websocket") or not self.is_edge(scope):
            await self.app(scope, receive, send)
            return
        headers = list(scope.get("headers") or ())
        values = [value for key, value in headers if key.lower() == _EDGE_PEER_HEADER]
        peer: str | None = None
        if len(values) == 1:
            try:
                peer = str(ipaddress.ip_address(values[0].decode("ascii").strip()))
            except (UnicodeDecodeError, ValueError):
                peer = None
        if peer is None:
            if scope["type"] == "websocket":
                await send({"type": "websocket.close", "code": 1008})
                return
            body = b"Bad Request"
            await send({"type": "http.response.start", "status": 400, "headers": [(b"content-type", b"text/plain; charset=utf-8"), (b"content-length", str(len(body)).encode("ascii"))]})
            await send({"type": "http.response.body", "body": body})
            return
        edge_scope = dict(scope)
        edge_scope["client"] = (peer, 0)
        edge_scope["headers"] = [(key, value) for key, value in headers if key.lower() != _EDGE_PEER_HEADER]
        await self.app(edge_scope, receive, send)
