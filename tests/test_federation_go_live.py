"""Answer attribution of the federation readiness checks, end to end (FEDERATION_GO_LIVE_DESIGN.md Section 11, units GL1 and GL2).

A real Claire process and a real sidecar process run behind a small intermediary in the test process. The intermediary is the public origin: it sends the federation paths to the sidecar and all other paths to Claire, as the reverse proxy rules of FEDERATION.md Section 10 tell. A fault of the intermediary makes its check fail with the code of the party that answered.

The test needs the ``deno`` program and its dependency cache. It is skipped when ``deno`` is not installed. No request goes to another host than the loopback address.
"""

from __future__ import annotations

import http.client
import http.server
import json
import os
import secrets
import shutil
import signal
import socket
import subprocess
import sys
import threading
import time
from collections.abc import Callable
from pathlib import Path

import pytest

from claire.api.federation_internal import federation_socket_path, service_key_path
from claire.api.federation_routes import read_heartbeat
from claire.store import accounts as accounts_store
from claire.store import db as dbm

ROOT = Path(__file__).resolve().parents[1]
SIDECAR_DIR = ROOT / "sidecar" / "fedify"
PROBE_SCRIPT = ROOT / "ops" / "federation_outside_probe.sh"
DENO = shutil.which("deno")
pytestmark = pytest.mark.skipif(DENO is None, reason="deno is not installed")

FEDERATION_PREFIXES = ("/nodeinfo/", "/ap/")
FEDERATION_EXACT = ("/.well-known/webfinger", "/.well-known/nodeinfo")
HOP_HEADERS = {"connection", "keep-alive", "transfer-encoding", "content-length"}

# A fault gets the request and gives an answer (status, headers, body), a change of the forwarded request (a dict with "target", "headers", or "upstream"), or None for no fault.
Request = tuple[str, str, dict[str, str], bytes]
Fault = Callable[[Request], "tuple[int, dict[str, str], bytes] | dict | None"]


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


class _Intermediary:
    """The public origin of the test: a reverse proxy with the rules of FEDERATION.md Section 10 and with faults that a test sets."""

    def __init__(self, claire_port: int, sidecar_port: int) -> None:
        self.claire_port = claire_port
        self.sidecar_port = sidecar_port
        self.faults: list[Fault] = []
        # The last answer of the sidecar for each target. The fault "cache" gives it again.
        self.cache: dict[str, tuple[int, dict[str, str], bytes]] = {}
        outer = self

        class Handler(http.server.BaseHTTPRequestHandler):
            protocol_version = "HTTP/1.1"

            def _serve(self) -> None:
                length = int(self.headers.get("Content-Length") or 0)
                body = self.rfile.read(length) if length else b""
                headers = {key: value for key, value in self.headers.items()}
                target = self.path
                path = target.split("?", 1)[0]
                # The rules of FEDERATION.md Section 10 select the upstream from the path of the request.
                to_sidecar = path in FEDERATION_EXACT or path.startswith(FEDERATION_PREFIXES)
                for fault in list(outer.faults):
                    outcome = fault((self.command, target, headers, body))
                    if outcome is None:
                        continue
                    if isinstance(outcome, tuple):
                        self._answer(*outcome)
                        return
                    target = outcome.get("target", target)
                    headers = outcome.get("headers", headers)
                    to_sidecar = outcome["upstream"] == "sidecar" if "upstream" in outcome else to_sidecar
                connection = http.client.HTTPConnection("127.0.0.1", outer.sidecar_port if to_sidecar else outer.claire_port, timeout=30)
                try:
                    connection.request(self.command, target, body=body or None, headers={key: value for key, value in headers.items() if key.lower() not in HOP_HEADERS})
                    response = connection.getresponse()
                    answer = (response.status, {key: value for key, value in response.getheaders() if key.lower() not in HOP_HEADERS}, response.read())
                finally:
                    connection.close()
                if to_sidecar:
                    outer.cache[self.path] = answer
                self._answer(*answer)

            def _answer(self, status: int, headers: dict[str, str], body: bytes) -> None:
                self.send_response_only(status)
                for key, value in headers.items():
                    self.send_header(key, value)
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                if self.command != "HEAD":
                    self.wfile.write(body)

            do_GET = do_POST = do_HEAD = _serve  # noqa: N815

            def log_message(self, format: str, *args: object) -> None:  # noqa: A002
                pass

        self.server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.port = int(self.server.server_address[1])
        self._thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self._thread.start()

    def close(self) -> None:
        self.server.shutdown()
        self.server.server_close()
        self._thread.join(timeout=5)


def _path(request: Request) -> str:
    return request[1].split("?", 1)[0]


def _header(request: Request, name: str) -> str:
    return next((value for key, value in request[2].items() if key.lower() == name), "")


# --- Faults of an edge (FEDERATION_GO_LIVE_DESIGN.md Sections 3.2 and 11) ---


def cdn_challenge(path: str) -> Fault:
    """A bot rule of a CDN: a challenge page in place of the answer."""
    return lambda request: (403, {"cf-mitigated": "challenge", "cf-ray": "8f0-ICN", "Server": "cloudflare", "Content-Type": "text/html; charset=UTF-8"}, b"<html>challenge</html>") if _path(request) == path else None


def cdn_block_profile(request: Request):
    """A rule of a CDN that refuses the profile parameter of a media type."""
    return (403, {"cf-mitigated": "block", "cf-ray": "8f0-ICN", "Server": "cloudflare"}, b"") if "profile=" in _header(request, "content-type") else None


def waf_refuses_activity_json(request: Request):
    """A WAF with a list of allowed media types that does not have application/activity+json."""
    return (403, {"Server": "waf", "Content-Type": "text/html"}, b"<html>Request blocked</html>") if request[0] == "POST" and _header(request, "content-type") == "application/activity+json" else None


def strip_prefix(path: str) -> Fault:
    """A path rule that removes the prefix /ap. The request still gets to the sidecar."""
    return lambda request: {"target": request[1].removeprefix("/ap")} if _path(request) == path else None


def to_upstream(path: str, upstream: str) -> Fault:
    """A wrong path rule: the request goes to the other upstream."""
    return lambda request: {"upstream": upstream} if _path(request) == path else None


def change_host(path: str) -> Fault:
    """A proxy that sets the Host header to the address of the upstream."""
    return lambda request: {"headers": {**{key: value for key, value in request[2].items() if key.lower() != "host"}, "Host": "10.0.0.5:8790"}} if _path(request) == path else None


def test_e2e_each_fault_of_the_edge_gives_the_code_of_the_party_that_answered(tmp_path):
    claire_port, sidecar_port = _free_port(), _free_port()
    edge = _Intermediary(claire_port, sidecar_port)
    origin = f"http://127.0.0.1:{edge.port}"
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
        CLAIRE_FQDN=f"127.0.0.1:{edge.port}",
        CLAIRE_INJECT_TOKEN=secrets.token_urlsafe(32),
        CLAIRE_SECRET_KEY="66" * 32,
        PYTHONDONTWRITEBYTECODE="1",
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

    sock_path = federation_socket_path(data_dir)
    sidecar_db = data_dir / "fedify" / "fedify.db"
    sidecar_db.parent.mkdir(parents=True)
    sidecar_env = {
        **_clean_env(),
        "NO_COLOR": "1",
        "CLAIRE_FEDIFY_SOCKET": str(sock_path),
        "CLAIRE_FEDIFY_SERVICE_KEY_FILE": str(service_key_path(data_dir)),
        "CLAIRE_FEDIFY_DB": str(sidecar_db),
        "CLAIRE_FEDIFY_HOST": "127.0.0.1",
        "CLAIRE_FEDIFY_PORT": str(sidecar_port),
    }
    sidecar_command = [
        str(DENO), "run", "--frozen", "--cached-only",
        f"--allow-net=127.0.0.1,claire.internal:80,unix:{sock_path}",
        f"--allow-read={data_dir},{SIDECAR_DIR}",
        f"--allow-write={sidecar_db.parent},{sock_path}",
        "--allow-env=CLAIRE_FEDIFY_SOCKET,CLAIRE_FEDIFY_SERVICE_KEY_FILE,CLAIRE_FEDIFY_DB,CLAIRE_FEDIFY_HOST,CLAIRE_FEDIFY_PORT",
        "src/main.ts",
    ]  # fmt: skip

    claire = subprocess.Popen([sys.executable, "-m", "claire_bible.cli", "serve-api"], cwd=tmp_path, env=env, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
    sidecars: list[subprocess.Popen] = []

    def start_sidecar() -> dict:
        """Start a new sidecar process and wait for its first heartbeat. The self-checks of the sidecar run at each heartbeat, so a new process gives the result of the faults that are set now."""
        before = (read_heartbeat(data_dir) or {}).get("marker")
        for old in sidecars:
            _stop(old)
        sidecar = subprocess.Popen(sidecar_command, cwd=SIDECAR_DIR, env=sidecar_env, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
        sidecars.append(sidecar)
        deadline = time.monotonic() + 120
        while (record := read_heartbeat(data_dir)) is None or record.get("marker") == before:
            assert sidecar.poll() is None, _output(sidecar)
            assert time.monotonic() < deadline, "no heartbeat"
            time.sleep(0.2)
        return record

    def report() -> dict[str, dict]:
        check = subprocess.run([sys.executable, "-m", "claire_bible.cli", "server", "check", "--federation", "--json"], cwd=tmp_path, env=env, capture_output=True, text=True, timeout=90)
        data = json.loads(check.stdout)["data"]
        assert [item["id"] for item in data["items"]] == [f"GF{i}" for i in range(1, 21)], check.stdout
        return {item["id"]: item for item in data["items"]}

    try:
        deadline = time.monotonic() + 60
        while not sock_path.exists():
            assert claire.poll() is None, _output(claire)
            assert time.monotonic() < deadline, "serve-api did not start in time"
            time.sleep(0.2)

        # 1. An edge without a fault: each required check passes. Only the warning GF19 fails, because no other server sent a request to this new server.
        heartbeat = start_sidecar()
        assert heartbeat["self_check"] == {"ok": True, "checked_at": heartbeat["self_check"]["checked_at"], "answer": None}, heartbeat
        assert heartbeat["knowledge_self_check"]["failed_paths"] == [] and heartbeat["knowledge_self_check"]["signed"] == {"draft_cavage": {"result": "ok", "answer": None}, "rfc9421": {"result": "ok", "answer": None}}, heartbeat
        assert heartbeat["public_host"] == {"host": "127.0.0.1", "addresses": [{"address": "127.0.0.1", "class": "loopback"}], "error": None}
        assert heartbeat["traffic"]["inbound_verified"]["count"] == 0
        items = report()
        assert [item_id for item_id, item in items.items() if not item["passed"]] == ["GF19"], items
        assert (items["GF14"]["applies"], items["GF14"]["passed"], items["GF16"]["passed"]) == (True, True, True)

        # The outside probe gets each path from its party.
        probe = subprocess.run(["sh", str(PROBE_SCRIPT), origin], capture_output=True, text=True, timeout=120, env=_clean_env())
        assert probe.returncode == 0, probe.stdout + probe.stderr
        lines = probe.stdout.splitlines()
        assert lines[-1] == "outside probe: PASS" and len(lines) == 13 and all(line.startswith("OK ") for line in lines[:-1]), probe.stdout
        assert subprocess.run(["sh", str(PROBE_SCRIPT), "claire.example.com"], capture_output=True, text=True, timeout=10).returncode == 2

        # 2. Faults of a CDN and a WAF, and wrong path rules.
        edge.faults = [
            cdn_challenge("/.well-known/webfinger"),
            strip_prefix("/ap/actors/server"),
            to_upstream("/ap/themes", "claire"),
            waf_refuses_activity_json,
            cdn_block_profile,
            to_upstream("/.well-known/oauth-authorization-server", "sidecar"),
        ]
        heartbeat = start_sidecar()
        items = report()
        assert [item_id for item_id, item in items.items() if not item["passed"]] == ["GF6", "GF7", "GF8", "GF9", "GF10", "GF14", "GF16", "GF19"], items
        # The same faults show in the checks of the documents: the server actor on a changed path, and a collection that Claire answers.
        assert (items["GF14"]["code"], items["GF16"]["code"], items["GF16"]["values"]["path"]) == ("server_actor_failed", "answered_by_claire", "/ap/themes")
        assert (items["GF6"]["code"], items["GF6"]["values"]["via_cdn"]) == ("cdn_challenge", "cloudflare")
        assert (items["GF7"]["code"], items["GF7"]["values"]["path"]) == ("answered_by_sidecar", "/.well-known/oauth-authorization-server")
        # The marker header alone shows no fault for a removed prefix: the path that the sidecar got does.
        assert (items["GF8"]["code"], items["GF8"]["values"]["received"], items["GF8"]["values"]["paths"]) == ("path_changed", "/actors/server", "/ap/actors/server, /ap/themes")
        # The signed request has the media type of a delivery, so the WAF rule shows in the check.
        assert (items["GF9"]["code"], items["GF9"]["values"]["server"], items["GF9"]["values"]["forms"]) == ("intermediary", "waf", "draft-cavage, RFC 9421")
        assert (items["GF10"]["code"], items["GF10"]["values"]["server"], items["GF10"]["values"]["media_type"]) == ("intermediary", "waf", "application/activity+json")
        probe = subprocess.run(["sh", str(PROBE_SCRIPT), origin], capture_output=True, text=True, timeout=120, env=_clean_env())
        assert probe.returncode == 1 and "answered=cdn_challenge expected=sidecar" in probe.stdout and probe.stdout.splitlines()[-1].startswith("outside probe: FAIL"), probe.stdout

        # 3. A changed Host, a cache in front of the sidecar, and a block of the profile parameter only.
        cached = dict(edge.cache)
        edge.faults = [
            change_host("/ap/self-check"),
            lambda request: cached["/.well-known/nodeinfo"] if _path(request) == "/.well-known/nodeinfo" else None,
            cdn_block_profile,
        ]
        old_marker = heartbeat["marker"]
        heartbeat = start_sidecar()
        items = report()
        assert [item_id for item_id, item in items.items() if not item["passed"]] == ["GF8", "GF9", "GF10", "GF14", "GF19"], items
        assert items["GF14"]["code"] == "sidecar_other"
        # The cache gives the answer of the sidecar process of step 2: its marker is not the marker of the process that runs now.
        assert cached["/.well-known/nodeinfo"][1]["x-claire-fedify-server"] == old_marker != heartbeat["marker"]
        assert (items["GF8"]["code"], items["GF8"]["values"]["path"]) == ("sidecar_other", "/.well-known/nodeinfo")
        assert (items["GF9"]["code"], items["GF9"]["values"]["received"]) == ("host_changed", "10.0.0.5:8790")
        assert (items["GF10"]["code"], items["GF10"]["values"]["value"]) == ("cdn_block", "block")

        # 4. A cache that gives an old answer of the process that runs now: the marker is correct, but the sidecar did not get the request.
        edge.faults = []
        start_sidecar()
        assert [item_id for item_id, item in report().items() if not item["passed"]] == ["GF19"]
        live = dict(edge.cache)
        edge.faults = [lambda request: live["/ap/kb"] if _path(request) == "/ap/kb" else None]
        deadline = time.monotonic() + 60
        while (items := report())["GF8"]["passed"]:
            assert time.monotonic() < deadline, "the next heartbeat did not show the cache"
            time.sleep(2)
        assert (items["GF8"]["code"], items["GF8"]["values"]["path"]) == ("answer_not_live", "/ap/kb")
    finally:
        for sidecar in sidecars:
            _stop(sidecar)
        _stop(claire)
        edge.close()
