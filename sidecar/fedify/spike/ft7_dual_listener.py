"""FT7: one uvicorn Server serves a TCP socket and a Unix domain socket in the same event loop. The ASGI ``scope["server"]`` value identifies the listener.

Run: uv run python sidecar/fedify/spike/ft7_dual_listener.py
"""

from __future__ import annotations

import asyncio
import json
import os
import socket
import tempfile

import httpx
import uvicorn


async def app(scope, receive, send):
    if scope["type"] == "lifespan":
        while True:
            message = await receive()
            if message["type"] == "lifespan.startup":
                await send({"type": "lifespan.startup.complete"})
            elif message["type"] == "lifespan.shutdown":
                await send({"type": "lifespan.shutdown.complete"})
                return
    body = json.dumps({"server": scope.get("server"), "client": scope.get("client"), "host": dict(scope["headers"]).get(b"host", b"").decode()}).encode()
    await send({"type": "http.response.start", "status": 200, "headers": [(b"content-type", b"application/json")]})
    await send({"type": "http.response.body", "body": body})


async def main() -> int:
    tmp = tempfile.mkdtemp(prefix="ft7-")
    uds_path = os.path.join(tmp, "federation.sock")
    tcp = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    tcp.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    tcp.bind(("127.0.0.1", 0))
    port = tcp.getsockname()[1]
    uds = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    uds.bind(uds_path)
    os.chmod(uds_path, 0o660)

    server = uvicorn.Server(uvicorn.Config(app, workers=1, access_log=False, proxy_headers=False, log_level="warning"))
    task = asyncio.create_task(server.serve(sockets=[tcp, uds]))
    while not server.started:
        await asyncio.sleep(0.05)

    async with httpx.AsyncClient() as client:
        via_tcp = (await client.get(f"http://127.0.0.1:{port}/x")).json()
        # A client on the public listener sends the Host value of the internal listener. The server value must not change.
        via_tcp_spoof = (await client.get(f"http://127.0.0.1:{port}/x", headers={"Host": "internal"})).json()
    async with httpx.AsyncClient(transport=httpx.AsyncHTTPTransport(uds=uds_path)) as client:
        via_uds = (await client.get("http://internal/x")).json()

    server.should_exit = True
    await task

    print("tcp        :", via_tcp)
    print("tcp spoofed:", via_tcp_spoof)
    print("uds        :", via_uds)
    tcp_server = tuple(via_tcp["server"])
    ok = tcp_server == ("127.0.0.1", port) and tuple(via_tcp_spoof["server"]) == tcp_server and via_uds["server"] is not None and via_uds["server"][0] == uds_path and via_uds["server"][1] is None
    print("FT7", "PASS" if ok else "FAIL", f"mode={oct(os.stat(uds_path).st_mode & 0o777) if os.path.exists(uds_path) else 'removed'}")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
