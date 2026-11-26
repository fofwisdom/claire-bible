"""Development network HTTP CONNECT proxy for host browsers (docs/design/CONTRIBUTOR_DEVELOPMENT_ENVIRONMENT_DESIGN.md Section 6.7).

Listens on 0.0.0.0:8888 inside the ``access`` container and forwards only ``CONNECT <host>:443`` where ``host`` is ``claire.test`` or a subdomain below ``claire.test``. All other methods, hosts, and ports return ``405`` or ``403``. Uses the container system/Docker DNS resolver (set to ``claire-devnet`` dnsmasq).
"""

from __future__ import annotations

import argparse
import asyncio
import re
import signal

LISTEN_HOST = "0.0.0.0"
LISTEN_PORT = 8888
ALLOWED_DOMAIN = "claire.test"
ALLOWED_PORT = 443
MAX_HEADER_LINE_BYTES = 8192
MAX_HEADER_LINES = 100
_DNS_LABEL_RE = re.compile(r"^[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?$")


def validate_connect_target(target: str) -> tuple[str, int] | None:
    """Return ``(host, 443)`` when ``target`` is ``claire.test:443`` or ``*.claire.test:443``, else ``None``."""
    if not target or any(ch.isspace() for ch in target) or target.count(":") != 1:
        return None
    if any(ch in target for ch in ("@", "/", "?", "#", "[", "]")):
        return None
    host_raw, port_raw = target.split(":", 1)
    if port_raw != str(ALLOWED_PORT):
        return None
    host = host_raw.lower()
    if not host or len(host) > 253:
        return None
    labels = host.split(".")
    if any(not _DNS_LABEL_RE.fullmatch(label) for label in labels):
        return None
    if host != ALLOWED_DOMAIN and not host.endswith(f".{ALLOWED_DOMAIN}"):
        return None
    return host, ALLOWED_PORT


def _http_error(status: int, reason: str, extra_headers: str = "") -> bytes:
    return f"HTTP/1.1 {status} {reason}\r\n{extra_headers}Connection: close\r\nContent-Length: 0\r\n\r\n".encode("ascii")


async def _close_writer(writer: asyncio.StreamWriter | None) -> None:
    if writer is None:
        return
    try:
        if not writer.is_closing():
            writer.close()
        await writer.wait_closed()
    except OSError:
        pass


async def _relay(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
    try:
        while chunk := await reader.read(65536):
            writer.write(chunk)
            await writer.drain()
        if writer.can_write_eof():
            writer.write_eof()
    except (OSError, asyncio.CancelledError):
        pass


async def handle_client(reader: asyncio.StreamReader, writer: asyncio.StreamWriter, *, connect_timeout: float = 10.0, handshake_timeout: float = 10.0) -> None:
    """Handle one HTTP proxy client connection."""
    upstream_writer: asyncio.StreamWriter | None = None
    try:
        try:
            raw_line = await asyncio.wait_for(reader.readline(), timeout=handshake_timeout)
        except (TimeoutError, OSError):
            return
        if not raw_line:
            return
        if len(raw_line) > MAX_HEADER_LINE_BYTES or not raw_line.endswith(b"\n"):
            writer.write(_http_error(400, "Bad Request"))
            await writer.drain()
            return
        parts = raw_line.decode("latin-1").rstrip("\r\n").split(" ")
        if len(parts) != 3 or not parts[2].startswith("HTTP/1."):
            writer.write(_http_error(400, "Bad Request"))
            await writer.drain()
            return
        method, target, _version = parts

        # Drain request headers with a hard line limit so one client cannot exhaust memory.
        for line_count in range(MAX_HEADER_LINES + 1):
            try:
                header_line = await asyncio.wait_for(reader.readline(), timeout=handshake_timeout)
            except (TimeoutError, OSError):
                return
            if not header_line:
                return
            if len(header_line) > MAX_HEADER_LINE_BYTES or line_count == MAX_HEADER_LINES:
                writer.write(_http_error(400, "Bad Request"))
                await writer.drain()
                return
            if header_line in (b"\r\n", b"\n"):
                break

        if method != "CONNECT":
            writer.write(_http_error(405, "Method Not Allowed", "Allow: CONNECT\r\n"))
            await writer.drain()
            return

        parsed = validate_connect_target(target)
        if parsed is None:
            writer.write(_http_error(403, "Forbidden"))
            await writer.drain()
            return

        host, port = parsed
        try:
            upstream_reader, upstream_writer = await asyncio.wait_for(asyncio.open_connection(host, port), timeout=connect_timeout)
        except (TimeoutError, OSError):
            writer.write(_http_error(502, "Bad Gateway"))
            await writer.drain()
            return

        writer.write(b"HTTP/1.1 200 Connection Established\r\n\r\n")
        await writer.drain()
        await asyncio.gather(_relay(reader, upstream_writer), _relay(upstream_reader, writer))
    except OSError:
        pass
    finally:
        await _close_writer(upstream_writer)
        await _close_writer(writer)


async def run_server(host: str = LISTEN_HOST, port: int = LISTEN_PORT) -> None:
    """Start the asyncio CONNECT proxy server and run until stopped."""
    server = await asyncio.start_server(handle_client, host, port)
    stop_event = asyncio.Event()
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        try:
            loop.add_signal_handler(sig, stop_event.set)
        except NotImplementedError:
            pass
    async with server:
        serve_task = asyncio.create_task(server.serve_forever())
        stop_task = asyncio.create_task(stop_event.wait())
        done, pending = await asyncio.wait({serve_task, stop_task}, return_when=asyncio.FIRST_COMPLETED)
        for task in pending:
            task.cancel()
        await asyncio.gather(*pending, return_exceptions=True)
        for task in done:
            if not task.cancelled():
                task.result()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Claire devnet HTTP CONNECT proxy for *.claire.test:443.")
    parser.add_argument("--host", default=LISTEN_HOST)
    parser.add_argument("--port", type=int, default=LISTEN_PORT)
    args = parser.parse_args(argv)
    try:
        asyncio.run(run_server(args.host, args.port))
    except KeyboardInterrupt:
        pass
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
