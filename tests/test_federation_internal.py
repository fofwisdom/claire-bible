"""Internal federation API: service key file, signed requests, and listener binding (Fedify sidecar design Section 6.1, unit FP1-1).

All tests use ``tmp_path`` only and remove the ``CLAIRE_*`` and ``CB_*`` environment variables.
"""

from __future__ import annotations

import os
import secrets
import signal
import socket
import stat
import subprocess
import sys
import time
from pathlib import Path
from types import SimpleNamespace

import httpx
import pytest
from starlette.responses import JSONResponse
from starlette.routing import Route
from starlette.testclient import TestClient

from claire import cli
from claire.api.federation_internal import (
    INTERNAL_PREFIX,
    MAX_BODY_BYTES,
    SIGNATURE_HEADER,
    TIMESTAMP_HEADER,
    ListenerRouter,
    create_internal_federation_app,
    ensure_service_key,
    federation_socket_path,
    load_service_key,
    open_federation_socket,
    service_key_path,
    sign_request,
)

NOW = 1_800_000_000


@pytest.fixture(autouse=True)
def _isolate_env(monkeypatch):
    for name in list(os.environ):
        if name.startswith(("CLAIRE_", "CB_")):
            monkeypatch.delenv(name, raising=False)


def _mode(path: Path) -> int:
    return stat.S_IMODE(os.lstat(path).st_mode)


def _signed(key: bytes, method: str, target: str, body: bytes = b"", *, timestamp: int = NOW) -> dict[str, str]:
    ts = str(timestamp)
    return {TIMESTAMP_HEADER: ts, SIGNATURE_HEADER: sign_request(key, method, target, ts, body)}


# --- Service key file (I/O) ---


def test_service_key_file_is_made_one_time_with_mode_0600(tmp_path):
    path = service_key_path(tmp_path)
    assert ensure_service_key(path) is True
    key = load_service_key(path)
    assert len(key) == 32
    assert _mode(path) == 0o600

    os.chmod(path, 0o644)
    assert ensure_service_key(path) is False
    assert load_service_key(path) == key
    assert _mode(path) == 0o600


def test_migrate_makes_the_service_key(monkeypatch, tmp_path):
    monkeypatch.setenv("CLAIRE_APP_ROOT", str(tmp_path))
    monkeypatch.setenv("CLAIRE_DB_PATH", str(tmp_path / "data" / "claire.db"))
    monkeypatch.setenv("CLAIRE_PROVIDER", "mock")
    from claire.config import get_settings

    get_settings.cache_clear()
    try:
        assert cli.main(["migrate"]) == 0
        path = service_key_path(tmp_path / "data")
        key = path.read_bytes()
        assert len(key) == 32 and _mode(path) == 0o600
        assert cli.main(["migrate"]) == 0
        assert path.read_bytes() == key
    finally:
        get_settings.cache_clear()


# --- Signed requests (module interface) ---


@pytest.fixture
def internal(tmp_path):
    async def echo(request):
        return JSONResponse({"body": (await request.body()).decode(), "query": request.url.query})

    key_file = service_key_path(tmp_path)
    ensure_service_key(key_file)
    app = create_internal_federation_app(
        SimpleNamespace(data_dir=tmp_path),
        routes=[Route("/echo", echo, methods=["GET", "POST"])],
        now=lambda: float(NOW),
    )
    return TestClient(app), load_service_key(key_file)


def test_valid_signature_reaches_the_route(internal):
    client, key = internal
    target = f"{INTERNAL_PREFIX}/echo?max_id=5"
    r = client.post(target, content=b"payload", headers=_signed(key, "POST", target, b"payload"))
    assert r.status_code == 200
    assert r.json() == {"body": "payload", "query": "max_id=5"}


def test_absolute_form_request_target_is_read_as_its_path(tmp_path):
    # The Deno HTTP client of the sidecar uses the Unix socket as a proxy and sends the absolute form.
    import asyncio

    async def echo(request):
        return JSONResponse({"path": request.url.path, "query": request.url.query})

    key_file = service_key_path(tmp_path)
    ensure_service_key(key_file)
    key = load_service_key(key_file)
    app = create_internal_federation_app(SimpleNamespace(data_dir=tmp_path), routes=[Route("/echo", echo)], now=lambda: float(NOW))
    target = f"{INTERNAL_PREFIX}/echo?max_id=5"
    absolute = f"http://claire.internal{INTERNAL_PREFIX}/echo"

    async def call(headers: dict[str, str]):
        messages = []
        scope = {"type": "http", "method": "GET", "path": absolute, "raw_path": absolute.encode(), "query_string": b"max_id=5", "headers": [(k.encode(), v.encode()) for k, v in headers.items()], "server": None, "client": None, "scheme": "http", "http_version": "1.1"}

        async def receive():
            return {"type": "http.request", "body": b"", "more_body": False}

        async def send(message):
            messages.append(message)

        await app(scope, receive, send)
        return messages[0]["status"], b"".join(m.get("body", b"") for m in messages[1:])

    status, body = asyncio.run(call(_signed(key, "GET", target)))
    assert status == 200
    assert body == b'{"path":"/internal/federation/v1/echo","query":"max_id=5"}'
    # The signature is for the path. A signature for the absolute form is not valid.
    status, _ = asyncio.run(call(_signed(key, "GET", f"http://claire.internal{target}")))
    assert status == 401


@pytest.mark.parametrize("age", [-61, 61, 3600])
def test_timestamp_outside_the_window_is_refused(internal, age):
    client, key = internal
    target = f"{INTERNAL_PREFIX}/echo"
    r = client.get(target, headers=_signed(key, "GET", target, timestamp=NOW - age))
    assert r.status_code == 401


def test_timestamp_at_the_limit_is_accepted(internal):
    client, key = internal
    target = f"{INTERNAL_PREFIX}/echo"
    assert client.get(target, headers=_signed(key, "GET", target, timestamp=NOW - 60)).status_code == 200


def test_bad_signatures_are_refused(internal):
    client, key = internal
    target = f"{INTERNAL_PREFIX}/echo"
    good = _signed(key, "POST", target, b"payload")

    assert client.post(target, content=b"payload").status_code == 401
    assert client.post(target, content=b"payload", headers={TIMESTAMP_HEADER: good[TIMESTAMP_HEADER]}).status_code == 401
    assert client.post(target, content=b"payload", headers={**good, SIGNATURE_HEADER: "0" * 64}).status_code == 401
    # A signature from a different key.
    assert client.post(target, content=b"payload", headers=_signed(secrets.token_bytes(32), "POST", target, b"payload")).status_code == 401
    # A signature for a different body, method, path, or query.
    assert client.post(target, content=b"changed", headers=good).status_code == 401
    assert client.get(target, headers=good).status_code == 401
    assert client.post(f"{INTERNAL_PREFIX}/other", content=b"payload", headers=good).status_code == 401
    assert client.post(f"{target}?max_id=5", content=b"payload", headers=good).status_code == 401
    # A timestamp that is not decimal digits.
    assert client.post(target, content=b"payload", headers={**good, TIMESTAMP_HEADER: f" {NOW}"}).status_code == 401


def test_principal_credentials_are_not_valid(internal):
    client, _key = internal
    target = f"{INTERNAL_PREFIX}/echo"
    r = client.get(target, headers={"Authorization": "Bearer any-token", "Cookie": "claire_session=any"})
    assert r.status_code == 401
    assert r.json() == {"error": "invalid_signature"}


def test_path_outside_the_route_table_is_404_after_the_signature_check(internal):
    client, key = internal
    for target in (f"{INTERNAL_PREFIX}/unknown", "/health", "/api/me"):
        assert client.get(target).status_code == 401
        r = client.get(target, headers=_signed(key, "GET", target))
        assert r.status_code == 404
        assert r.json() == {"error": "not_found"}


def test_body_larger_than_the_limit_is_refused(internal):
    client, key = internal
    target = f"{INTERNAL_PREFIX}/echo"
    body = b"x" * (MAX_BODY_BYTES + 1)
    assert client.post(target, content=body, headers=_signed(key, "POST", target, body)).status_code == 413


def test_absent_or_invalid_key_file_refuses_all_requests(tmp_path):
    app = create_internal_federation_app(SimpleNamespace(data_dir=tmp_path), now=lambda: float(NOW))
    client = TestClient(app)
    target = f"{INTERNAL_PREFIX}/echo"
    assert client.get(target, headers=_signed(b"k" * 32, "GET", target)).status_code == 503

    path = service_key_path(tmp_path)
    path.parent.mkdir(parents=True)
    path.write_bytes(b"short")
    assert client.get(target, headers=_signed(b"short", "GET", target)).status_code == 503


# --- Listener binding (module interface and I/O) ---


def _marker(name: str):
    async def app(scope, receive, send):
        await JSONResponse({"app": name})(scope, receive, send)

    return app


def test_listener_router_selects_by_scope_server_only(tmp_path):
    sock_path = federation_socket_path(tmp_path)
    router = ListenerRouter(_marker("public"), _marker("internal"), sock_path)
    base = {"type": "http", "headers": [(b"host", b"internal")]}

    assert router.is_internal({**base, "server": (str(sock_path), None)})
    assert router.is_internal({**base, "server": [str(sock_path), None]})
    # A TCP listener, with a forged Host header or a forged host name.
    assert not router.is_internal({**base, "server": ("127.0.0.1", 8766)})
    assert not router.is_internal({**base, "server": (str(sock_path), 80)})
    # A different Unix socket, or no listener data.
    assert not router.is_internal({**base, "server": (str(tmp_path / "other.sock"), None)})
    assert not router.is_internal({**base, "server": None})
    assert not router.is_internal(base)


def test_open_federation_socket_mode_and_stale_file(tmp_path):
    path = federation_socket_path(tmp_path)
    first = open_federation_socket(path)
    first.close()
    assert stat.S_ISSOCK(os.lstat(path).st_mode)
    # The file of the closed socket stays. A new process replaces it.
    second = open_federation_socket(path)
    try:
        assert _mode(path) == 0o660
    finally:
        second.close()

    # A file that is not a socket is not removed.
    regular = tmp_path / "run" / "regular.sock"
    regular.write_text("data")
    with pytest.raises(OSError):
        open_federation_socket(regular)
    assert regular.read_text() == "data"


# --- End-to-end: a real serve-api process with the public listener and the internal socket ---


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def test_e2e_internal_paths_answer_on_the_socket_only(tmp_path):
    port = _free_port()
    data_dir = tmp_path / "data"
    env = {k: v for k, v in os.environ.items() if not k.startswith(("CLAIRE_", "CB_"))}
    env.update(
        CLAIRE_APP_ROOT=str(tmp_path),
        CLAIRE_ENVIRONMENT="development",
        CLAIRE_PROVIDER="mock",
        CLAIRE_DB_PATH=str(data_dir / "claire.db"),
        CB_DATA_DIR=str(data_dir),
        CLAIRE_INJECT_HOST="127.0.0.1",
        CLAIRE_INJECT_PORT=str(port),
        CLAIRE_FQDN=f"127.0.0.1:{port}",
        CLAIRE_INJECT_TOKEN=secrets.token_urlsafe(32),
        PYTHONDONTWRITEBYTECODE="1",
    )
    migrate = subprocess.run([sys.executable, "-m", "claire.cli", "migrate"], cwd=tmp_path, env=env, capture_output=True, text=True)
    assert migrate.returncode == 0, migrate.stdout + migrate.stderr
    key = load_service_key(service_key_path(data_dir))
    sock_path = federation_socket_path(data_dir)
    target = f"{INTERNAL_PREFIX}/config"
    public = f"http://127.0.0.1:{port}"

    proc = subprocess.Popen([sys.executable, "-m", "claire.cli", "serve-api"], cwd=tmp_path, env=env, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
    try:
        deadline = time.monotonic() + 60
        while True:
            if proc.poll() is not None:
                raise AssertionError(f"serve-api exited early: {proc.returncode}\n{proc.stdout.read()}")
            try:
                if httpx.get(f"{public}/health", timeout=2).status_code == 200 and sock_path.exists():
                    break
            except httpx.HTTPError:
                pass
            assert time.monotonic() < deadline, "serve-api did not start in time"
            time.sleep(0.2)

        assert _mode(sock_path) == 0o660

        def fresh(method: str, path: str) -> dict[str, str]:
            return _signed(key, method, path, timestamp=int(time.time()))

        with httpx.Client(transport=httpx.HTTPTransport(uds=str(sock_path)), base_url="http://internal") as internal:
            # The internal sub-application answers on the socket.
            assert internal.get(target).status_code == 401
            assert internal.get(target, headers={**fresh("GET", target), SIGNATURE_HEADER: "0" * 64}).status_code == 401
            assert internal.get(target, headers=_signed(key, "GET", target, timestamp=int(time.time()) - 600)).status_code == 401
            signed = internal.get(target, headers=fresh("GET", target))
            assert signed.status_code == 200
            assert signed.json()["canonical_origin"] == public
            assert (signed.json()["mode"], signed.json()["federation"]) == ("single", "off")
            unknown = f"{INTERNAL_PREFIX}/unknown"
            assert internal.get(unknown, headers=fresh("GET", unknown)).json() == {"error": "not_found"}
            # The instance actor has no keys while federation is off.
            keys = f"{INTERNAL_PREFIX}/actors/instance/keys"
            assert internal.get(keys, headers=fresh("GET", keys)).status_code == 404
            # The Host header of the public listener does not select the public application.
            assert internal.get("/health", headers={"Host": f"127.0.0.1:{port}"}).status_code == 401
            assert internal.get("/health", headers={**fresh("GET", "/health"), "Host": f"127.0.0.1:{port}"}).json() == {"error": "not_found"}

        # The public listener has no internal paths, also for a correctly signed request.
        for headers in ({}, fresh("GET", target)):
            r = httpx.get(f"{public}{target}", headers=headers, timeout=5)
            assert r.status_code == 404
            assert r.text != signed.text
        # The Host value of the socket client does not select the internal sub-application. HostRouter refuses the unknown host.
        r = httpx.get(f"{public}{target}", headers={**fresh("GET", target), "Host": "internal"}, timeout=5)
        assert r.status_code == 421
        assert r.text != signed.text

        proc.send_signal(signal.SIGTERM)
        out, _ = proc.communicate(timeout=30)
    finally:
        if proc.poll() is None:
            proc.kill()
            proc.communicate()
    assert proc.returncode in (0, -signal.SIGTERM), out
