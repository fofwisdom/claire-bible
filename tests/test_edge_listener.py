"""Edge service (unit GL3, docs/implementation/FEDERATION.md Section 10.1): the edge listener of Claire, the switch CB_EDGE of cb-manuscript, the Compose model, and the edge image.

- The listener tests run a real Claire process and send requests on its Unix socket. They need no Docker.
- The Compose model test calls ``docker compose config`` and is skipped when Docker is not installed.
- The end-to-end test runs the edge image in front of a real Claire process and a real sidecar process. It needs Docker and ``deno`` and runs only with ``CB_EDGE_TEST=1``, as the DrFed tests need their gate. No request goes to another host than the loopback address.
"""

from __future__ import annotations

import json
import os
import secrets
import shutil
import signal
import socket
import subprocess
import sys
import time
from pathlib import Path

import httpx
import pytest

from claire.api.edge_listener import EDGE_PEER_HEADER, edge_socket_path
from claire.api.federation_internal import federation_socket_path, service_key_path
from claire.api.federation_routes import read_heartbeat
from claire.store import accounts as accounts_store
from claire.store import db as dbm
from ops import cb_manuscript

ROOT = Path(__file__).resolve().parents[1]
SIDECAR_DIR = ROOT / "sidecar" / "fedify"
PROBE_SCRIPT = ROOT / "ops" / "federation_outside_probe.sh"
DOCKER = shutil.which("docker")
DENO = shutil.which("deno")
METADATA = "/.well-known/oauth-authorization-server"


def _clean_env() -> dict[str, str]:
    return {k: v for k, v in os.environ.items() if not k.startswith(("CLAIRE_", "CB_"))}


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return int(s.getsockname()[1])


def _output(proc: subprocess.Popen) -> str:
    return proc.stdout.read() if proc.stdout is not None else ""


def _stop(proc: subprocess.Popen) -> str:
    if proc.poll() is None:
        proc.send_signal(signal.SIGTERM)
        try:
            return proc.communicate(timeout=30)[0]
        except subprocess.TimeoutExpired:
            proc.kill()
    return proc.communicate()[0]


def _claire_env(tmp_path: Path, *, claire_port: int, authority: str, **extra: str) -> dict[str, str]:
    data_dir = tmp_path / "data"
    env = _clean_env()
    env.update(
        CLAIRE_APP_ROOT=str(tmp_path),
        CLAIRE_ENVIRONMENT="development",
        CLAIRE_PROVIDER="mock",
        CLAIRE_DB_PATH=str(data_dir / "claire.db"),
        CB_DATA_DIR=str(data_dir),
        CLAIRE_INJECT_HOST="127.0.0.1",
        CLAIRE_INJECT_PORT=str(claire_port),
        CLAIRE_FQDN=authority,
        CLAIRE_INJECT_TOKEN=secrets.token_urlsafe(32),
        CLAIRE_SECRET_KEY="77" * 32,
        CLAIRE_EDGE_LISTENER="1",
        PYTHONDONTWRITEBYTECODE="1",
        **extra,
    )
    migrate = subprocess.run([sys.executable, "-m", "claire_bible.cli", "migrate"], cwd=tmp_path, env=env, capture_output=True, text=True)
    assert migrate.returncode == 0, migrate.stdout + migrate.stderr
    conn = dbm.connect_existing(data_dir / "claire.db")
    try:
        store = accounts_store.AccountStore(conn)
        store.complete_setup(1, username="admin_kim", display_name="Admin Kim")
        store.set_setting("mode", "network", updated_by=1)
    finally:
        conn.close()
    return env


def _start_claire(tmp_path: Path, env: dict[str, str]) -> subprocess.Popen:
    claire = subprocess.Popen([sys.executable, "-m", "claire_bible.cli", "serve-api"], cwd=tmp_path, env=env, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
    sock_path = edge_socket_path(tmp_path / "data")
    deadline = time.monotonic() + 60
    # The socket file exists after the bind. The server accepts connections a short time later.
    while True:
        assert claire.poll() is None, _output(claire)
        assert time.monotonic() < deadline, "serve-api did not start in time"
        if sock_path.exists():
            with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as probe:
                try:
                    probe.connect(str(sock_path))
                    return claire
                except OSError:
                    pass
        time.sleep(0.2)


def test_e2e_edge_listener_gives_each_request_the_peer_address_that_the_edge_saw(tmp_path):
    claire_port = _free_port()
    authority = f"127.0.0.1:{claire_port}"
    # The Cloudflare filter reads the peer address: it shows which address Claire uses for a request of the edge listener.
    env = _claire_env(tmp_path, claire_port=claire_port, authority=authority, CLAIRE_CLOUDFLARE_IPS_ONLY="1")
    claire = _start_claire(tmp_path, env)
    sock_path = edge_socket_path(tmp_path / "data")
    try:
        assert (sock_path.stat().st_mode & 0o777) == 0o660
        with httpx.Client(transport=httpx.HTTPTransport(uds=str(sock_path)), base_url=f"http://{authority}", timeout=10) as edge:
            # An address of Cloudflare: the request passes, and the answer has the marker of Claire.
            passed = edge.get(METADATA, headers={EDGE_PEER_HEADER: "104.16.0.1"})
            assert (passed.status_code, passed.headers["x-claire-server"], passed.json()["issuer"]) == (200, "api", f"http://{authority}")
            # A public address that is not an address of Cloudflare: the filter refuses it, as for a direct connection from that address.
            refused = edge.get(METADATA, headers={EDGE_PEER_HEADER: "9.9.9.9"})
            assert (refused.status_code, "Direct public IP access" in refused.text) == (403, True)
            # A private address (the external reverse proxy on the LAN) passes.
            assert edge.get(METADATA, headers={EDGE_PEER_HEADER: "192.168.10.1"}).status_code == 200
            # No header, a value that is not an address, and two headers: 400. The listener does not guess an address.
            assert edge.get(METADATA).status_code == 400
            assert edge.get(METADATA, headers={EDGE_PEER_HEADER: "unknown"}).status_code == 400
            assert edge.get(METADATA, headers=[(EDGE_PEER_HEADER, "104.16.0.1"), (EDGE_PEER_HEADER, "9.9.9.9")]).status_code == 400
        # On the TCP listener the header has no meaning: the peer is the loopback address, and a public address in the header does not make the filter refuse the request.
        direct = httpx.get(f"http://{authority}{METADATA}", headers={EDGE_PEER_HEADER: "9.9.9.9"}, timeout=10)
        assert direct.status_code == 200
    finally:
        _stop(claire)


def _runtime_values(**changes: str) -> dict[str, str]:
    values = {"CB_FEDERATION": "1", "CB_EDGE": "1", "CLAIRE_FQDN": "Claire.Example.com"}
    values.update(changes)
    return values


def test_cb_manuscript_edge_switch_is_explicit(monkeypatch):
    for key in ("CB_EDGE", "CB_FEDERATION"):
        monkeypatch.delenv(key, raising=False)
    validate = cb_manuscript._validate_edge
    assert validate(_runtime_values(), environment="production", federation_enabled=True) == "claire.example.com"
    assert validate(_runtime_values(CLAIRE_FQDN="", CLAIRE_PUBLIC_URL="https://claire.example.com/"), environment="production", federation_enabled=True) == "claire.example.com"
    assert validate(_runtime_values(CB_EDGE="0"), environment="production", federation_enabled=True) == ""
    assert validate({"CB_FEDERATION": "1", "CLAIRE_FQDN": "claire.example.com"}, environment="production", federation_enabled=True) == ""
    for values, environment, federation in (
        (_runtime_values(CB_EDGE="true"), "production", True),
        (_runtime_values(), "development", True),
        (_runtime_values(), "production", False),
    ):
        with pytest.raises(cb_manuscript.ManuscriptError):
            validate(values, environment=environment, federation_enabled=federation)


def test_edge_files_have_the_rules_of_the_reverse_proxy():
    routes = (ROOT / "ops" / "edge" / "claire" / "routes.conf").read_text(encoding="utf-8")
    # The four rules of FEDERATION.md Section 10, and no rule for the prefix /.well-known/.
    for rule in ("location = /.well-known/webfinger {", "location = /.well-known/nodeinfo {", "location ^~ /nodeinfo/ {", "location ^~ /ap/ {", "location / {"):
        assert rule in routes
    assert "location ^~ /.well-known/" not in routes and "location /.well-known/ " not in routes
    federation = (ROOT / "ops" / "edge" / "claire" / "federation_location.conf").read_text(encoding="utf-8")
    api = (ROOT / "ops" / "edge" / "claire" / "api_location.conf").read_text(encoding="utf-8")
    for text in (federation, api):
        # The Host and the path go without a change (no URI part in proxy_pass), and the edge gives the address of its peer.
        assert "proxy_set_header Host $http_host;" in text
        assert f"proxy_set_header {'X-Claire-Edge-Peer'} $remote_addr;" in text
        assert [line for line in text.splitlines() if line.startswith("proxy_pass ")] in (["proxy_pass http://$claire_federation_upstream;"], ["proxy_pass http://$claire_api_upstream;"])
        assert "proxy_cache" not in text and "sub_filter" not in text
    assert "client_max_body_size 1m;" in federation
    assert "proxy_buffering off;" in api and "client_max_body_size 0;" in api
    dockerfile = (ROOT / "ops" / "edge" / "Dockerfile").read_text(encoding="utf-8")
    assert "FROM nginx:1.30.7-alpine" in dockerfile


@pytest.mark.skipif(DOCKER is None, reason="docker is not installed")
def test_compose_model_of_the_edge_has_one_host_port_and_one_mount():
    env = {**os.environ, "CB_ENV_FILE": ".env.example", "CB_EDGE_APP_HOST": "claire.example.com"}
    for key in ("CB_DATA_DIR", "CB_UID", "CB_GID", "CB_API_BIND", "CB_API_PORT", "CB_FEDIFY_PORT", "CB_IMAGE_TAG", "CB_PROJECT_NAME", "CLAIRE_ENVIRONMENT"):
        env.pop(key, None)
    base = [str(DOCKER), "compose", "--project-directory", str(ROOT), "--env-file", str(ROOT / ".env.example")]

    def model(*args: str) -> dict:
        result = subprocess.run([*base, *args, "config", "--format", "json"], cwd=ROOT, env=env, capture_output=True, text=True, timeout=120)
        assert result.returncode == 0, result.stderr[-4000:]
        return json.loads(result.stdout)["services"]

    # Without the profile the service does not start, and api has its host port.
    plain = model("--profile", "federation", "-f", str(ROOT / "docker-compose.yml"))
    assert "edge" not in plain and len(plain["api"]["ports"]) == 1 and len(plain["fedify"]["ports"]) == 1

    services = model("--profile", "federation", "--profile", "edge", "-f", str(ROOT / "docker-compose.yml"), "-f", str(ROOT / "docker-compose.edge.yml"))
    edge = services["edge"]
    assert [(port["target"], port["published"]) for port in edge["ports"]] == [(8080, plain["api"]["ports"][0]["published"])]
    assert not services["api"].get("ports") and not services["fedify"].get("ports")
    assert services["api"]["environment"]["CLAIRE_EDGE_LISTENER"] == "1"
    assert "CLAIRE_EDGE_LISTENER" not in plain["api"]["environment"]
    # The edge gets the socket directory only, the authority of the public origin, and no env file.
    assert [(volume["target"], volume["bind"]["create_host_path"]) for volume in edge["volumes"]] == [("/data/run", False)]
    assert edge["volumes"][0]["source"].endswith("/run")
    assert set(edge["environment"]) == {"TZ", "CLAIRE_EDGE_PORT", "CLAIRE_EDGE_APP_HOST", "CLAIRE_EDGE_API_SOCKET", "CLAIRE_EDGE_FEDIFY", "CLAIRE_EDGE_RESOLVER"}
    assert edge["environment"]["CLAIRE_EDGE_APP_HOST"] == "claire.example.com"
    assert "env_file" not in edge and edge["user"] == services["fedify"]["user"]


@pytest.mark.skipif(DOCKER is None or DENO is None or os.environ.get("CB_EDGE_TEST") != "1", reason="needs docker, deno, and CB_EDGE_TEST=1")
def test_e2e_edge_image_passes_the_readiness_checks_with_one_upstream(tmp_path):
    """The edge image in front of a real Claire process and a real sidecar process. The edge is the public origin: one port for all paths."""
    claire_port, sidecar_port, edge_port = _free_port(), _free_port(), _free_port()
    authority = f"127.0.0.1:{edge_port}"
    origin = f"http://{authority}"
    data_dir = tmp_path / "data"
    env = _claire_env(tmp_path, claire_port=claire_port, authority=authority)
    image = f"claire-bible-edge:test-{edge_port}"
    build = subprocess.run([str(DOCKER), "build", "-q", "-t", image, str(ROOT / "ops" / "edge")], capture_output=True, text=True, timeout=600)
    assert build.returncode == 0, build.stderr[-4000:]

    sock_path = federation_socket_path(data_dir)
    sidecar_db = data_dir / "fedify" / "fedify.db"
    sidecar_db.parent.mkdir(parents=True)
    claire = _start_claire(tmp_path, env)
    sidecar = None
    container = f"claire-edge-test-{edge_port}"
    try:
        sidecar_env = {**_clean_env(), "NO_COLOR": "1", "CLAIRE_FEDIFY_SOCKET": str(sock_path), "CLAIRE_FEDIFY_SERVICE_KEY_FILE": str(service_key_path(data_dir)), "CLAIRE_FEDIFY_DB": str(sidecar_db), "CLAIRE_FEDIFY_HOST": "127.0.0.1", "CLAIRE_FEDIFY_PORT": str(sidecar_port)}
        command = [
            str(DENO), "run", "--frozen", "--cached-only",
            f"--allow-net=127.0.0.1,claire.internal:80,unix:{sock_path}",
            f"--allow-read={data_dir},{SIDECAR_DIR}",
            f"--allow-write={sidecar_db.parent},{sock_path}",
            "--allow-env=CLAIRE_FEDIFY_SOCKET,CLAIRE_FEDIFY_SERVICE_KEY_FILE,CLAIRE_FEDIFY_DB,CLAIRE_FEDIFY_HOST,CLAIRE_FEDIFY_PORT",
            "src/main.ts",
        ]  # fmt: skip
        # The edge container uses the network of the host, so that it reaches the sidecar process, and the user of the test, so that it can connect to the socket of Claire.
        run = subprocess.run(
            [
                str(DOCKER), "run", "-d", "--rm", "--name", container, "--network", "host", "--user", f"{os.getuid()}:{os.getgid()}", "--tmpfs", "/tmp",
                "-v", f"{data_dir / 'run'}:/data/run",
                "-e", f"CLAIRE_EDGE_PORT={edge_port}", "-e", f"CLAIRE_EDGE_APP_HOST={authority}", "-e", "CLAIRE_EDGE_API_SOCKET=/data/run/edge.sock",
                "-e", f"CLAIRE_EDGE_FEDIFY=127.0.0.1:{sidecar_port}", "-e", "CLAIRE_EDGE_RESOLVER=127.0.0.1",
                image,
            ],
            capture_output=True, text=True, timeout=120,
        )  # fmt: skip
        assert run.returncode == 0, run.stderr[-4000:]
        sidecar = subprocess.Popen(command, cwd=SIDECAR_DIR, env=sidecar_env, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
        deadline = time.monotonic() + 120
        while (record := read_heartbeat(data_dir)) is None:
            assert sidecar.poll() is None, _output(sidecar)
            assert time.monotonic() < deadline, "no heartbeat"
            time.sleep(0.2)

        # Each self-check request of the sidecar went through the edge and came back with its path, its Host, and its signature.
        assert record["self_check"]["ok"] is True, record
        assert record["knowledge_self_check"]["failed_paths"] == [] and {form["result"] for form in record["knowledge_self_check"]["signed"].values()} == {"ok"}, record
        check = subprocess.run([sys.executable, "-m", "claire_bible.cli", "server", "check", "--federation", "--json"], cwd=tmp_path, env=env, capture_output=True, text=True, timeout=90)
        items = {item["id"]: item for item in json.loads(check.stdout)["data"]["items"]}
        assert [item_id for item_id, item in items.items() if not item["passed"]] == [], check.stdout
        assert len(items) == 20

        # The federation paths of another host go to Claire, which gives 404: they are paths of the app host only.
        other_host = httpx.get(f"{origin}/ap/actors/server", headers={"Host": "other.example.com"}, timeout=10)
        assert "x-claire-fedify-server" not in other_host.headers and other_host.headers.get("x-claire-server") == "api"
        # A body over 1 MiB on an inbox path stops at the edge.
        assert httpx.post(f"{origin}/ap/inbox", content=b"x" * (1024 * 1024 + 1), headers={"Content-Type": "application/activity+json"}, timeout=30).status_code == 413

        probe = subprocess.run(["sh", str(PROBE_SCRIPT), origin], capture_output=True, text=True, timeout=120, env=_clean_env())
        assert probe.returncode == 0 and probe.stdout.splitlines()[-1] == "outside probe: PASS", probe.stdout + probe.stderr
    finally:
        logs = subprocess.run([str(DOCKER), "logs", container], capture_output=True, text=True, timeout=30)
        subprocess.run([str(DOCKER), "rm", "-f", container], capture_output=True, text=True, timeout=60)
        subprocess.run([str(DOCKER), "image", "rm", "-f", image], capture_output=True, text=True, timeout=60)
        if sidecar is not None:
            _stop(sidecar)
        _stop(claire)
        print(logs.stdout[-2000:], logs.stderr[-2000:])
