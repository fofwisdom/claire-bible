"""Tests of the development network: two Claire servers with real sidecars, real TLS, real DNS, and the reverse proxy of production (docs/design/CONTRIBUTOR_DEVELOPMENT_ENVIRONMENT_DESIGN.md Section 8.4; acceptance steps KA1 to KA3 of KNOWLEDGE_FEDERATION_DESIGN.md Section 14.2).

Run: ``./cb-manuscript dev devnet up --servers a,b``, then ``./cb-manuscript dev devnet test``. The command runs pytest in the toolbox container on the network ``claire-devnet-edge``; the gate ``CB_DEVNET_TEST=1`` and the state file ``CB_DEVNET_STATE`` (the admin credentials of each server) come from it. Without the gate the tests are skipped.

The tests act only through the public routes and the federation paths of the servers, through the gateway: the admin routes with the admin of the state file, the account routes of accounts that the tests make with invitations, and the federation of the sidecars. No test writes a row in a database or calls an internal route. A test makes new names (a random suffix), so that the tests can run again on the same network. A test waits for a state with a time limit, never with a fixed sleep.
"""

from __future__ import annotations

import asyncio
import ast
import importlib.util
import json
import os
import secrets
import shutil
import subprocess
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any

import httpx
import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent


def _load_access_proxy():
    spec = importlib.util.spec_from_file_location("devnet_access_proxy", REPO_ROOT / "ops" / "dev" / "devnet" / "access_proxy.py")
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _devnet_only(func: Callable[..., Any]) -> Callable[..., Any]:
    """Mark live development-network integration tests so unit tests in this file run in normal pytest."""
    marked = pytest.mark.devnet(func)
    return pytest.mark.skipif(os.environ.get("CB_DEVNET_TEST") != "1", reason="the tests of the development network need CB_DEVNET_TEST=1 (./cb-manuscript dev devnet test)")(marked)


@pytest.mark.parametrize(
    ("target", "expected"),
    [
        ("claire.test:443", ("claire.test", 443)),
        ("a.claire.test:443", ("a.claire.test", 443)),
        ("uc.b.claire.test:443", ("uc.b.claire.test", 443)),
        ("A.CLAIRE.TEST:443", ("a.claire.test", 443)),
        ("example.com:443", None),
        ("evilclaire.test:443", None),
        ("claire.test.evil.com:443", None),
        (".claire.test:443", None),
        ("a..claire.test:443", None),
        ("a.claire.test:80", None),
        ("a.claire.test:8443", None),
        ("a.claire.test:0443", None),
        ("a.claire.test", None),
        ("127.0.0.1:443", None),
        ("user@a.claire.test:443", None),
        ("a.claire.test:443/path", None),
        ("[::1]:443", None),
        ("", None),
    ],
)
def test_access_proxy_validate_connect_target(target: str, expected: tuple[str, int] | None) -> None:
    proxy = _load_access_proxy()
    assert proxy.validate_connect_target(target) == expected


async def test_access_proxy_rejects_disallowed_and_tunnels_allowed(monkeypatch: pytest.MonkeyPatch) -> None:
    proxy = _load_access_proxy()

    async def echo_handler(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        try:
            while chunk := await reader.read(4096):
                writer.write(chunk)
                await writer.drain()
        finally:
            writer.close()
            await writer.wait_closed()

    upstream = await asyncio.start_server(echo_handler, "127.0.0.1", 0)
    upstream_port = upstream.sockets[0].getsockname()[1]
    proxy_server = await asyncio.start_server(proxy.handle_client, "127.0.0.1", 0)
    proxy_port = proxy_server.sockets[0].getsockname()[1]

    real_open_connection = asyncio.open_connection

    async def fake_open_connection(host: str, port: int):
        assert (host, port) == ("a.claire.test", 443)
        return await real_open_connection("127.0.0.1", upstream_port)

    monkeypatch.setattr(proxy.asyncio, "open_connection", fake_open_connection)

    async def send_raw(payload: bytes) -> bytes:
        reader, writer = await real_open_connection("127.0.0.1", proxy_port)
        try:
            writer.write(payload)
            await writer.drain()
            return await reader.read(4096)
        finally:
            writer.close()
            await writer.wait_closed()

    try:
        resp_get = await send_raw(b"GET http://a.claire.test/ HTTP/1.1\r\nHost: a.claire.test\r\n\r\n")
        assert resp_get.startswith(b"HTTP/1.1 405 Method Not Allowed\r\n")

        resp_host = await send_raw(b"CONNECT example.com:443 HTTP/1.1\r\nHost: example.com:443\r\n\r\n")
        assert resp_host.startswith(b"HTTP/1.1 403 Forbidden\r\n")

        resp_port = await send_raw(b"CONNECT a.claire.test:80 HTTP/1.1\r\nHost: a.claire.test:80\r\n\r\n")
        assert resp_port.startswith(b"HTTP/1.1 403 Forbidden\r\n")

        reader, writer = await real_open_connection("127.0.0.1", proxy_port)
        try:
            writer.write(b"CONNECT a.claire.test:443 HTTP/1.1\r\nHost: a.claire.test:443\r\n\r\n")
            await writer.drain()
            head = await reader.readuntil(b"\r\n\r\n")
            assert head == b"HTTP/1.1 200 Connection Established\r\n\r\n"
            writer.write(b"hello-devnet")
            await writer.drain()
            writer.write_eof()
            echoed = await reader.readexactly(len(b"hello-devnet"))
            assert echoed == b"hello-devnet"
        finally:
            writer.close()
            await writer.wait_closed()
    finally:
        proxy_server.close()
        await proxy_server.wait_closed()
        upstream.close()
        await upstream.wait_closed()


def test_devnet_info_and_base_env(tmp_path: Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch) -> None:
    from ops import devnet

    net = devnet.Devnet(tmp_path)
    base_env = net._base_env()
    assert base_env["CB_DEVNET_ACCESS_PORT"] == devnet.ACCESS_PORT == "18443"

    monkeypatch.setattr(net, "_running_servers", lambda: ["a", "b"])
    assert net.info() == 0
    out = capsys.readouterr().out
    assert "127.0.0.1:18443" in out
    assert str(net.ca) in out
    assert "chromium" in out and "--proxy-server=http://127.0.0.1:18443" in out
    assert "firefox" in out and "--profile" in out
    assert "not implemented" not in out


def test_f12_drfed_stack_generate_actors_uses_server_id() -> None:
    from tests import drfed_stack

    source = (REPO_ROOT / "tests" / "drfed_stack.py").read_text(encoding="utf-8")
    tree = ast.parse(source)
    names = {node.id for node in ast.walk(tree) if isinstance(node, ast.Name)}
    assert "instance_id" not in names

    seen: list[tuple[str, dict[str, Any] | None]] = []

    class _FakeStack(drfed_stack.DrFedStack):
        def graphql(self, query: str, variables: dict[str, Any] | None = None, *, token: bool = True) -> dict[str, Any]:
            seen.append((query, variables))
            return {
                "generateActors": {
                    "__typename": "CreateActorsSuccess",
                    "actors": [{"id": "act-1", "iri": "https://a.drfed.test/actors/1", "handle": "@a@a.drfed.test", "inboxUrl": "https://a.drfed.test/actors/1/inbox"}],
                }
            }

    fake = _FakeStack(run_dir="/tmp/drfed", project="p", edge_prefix="172.31.251", backend_prefix="172.31.252", loopback_port=0, email="t@drfed.test")
    actors = drfed_stack.generate_actors(fake, "srv-123", 1)
    assert seen and seen[0][1] == {"instance": "srv-123", "size": 1}
    assert actors == [drfed_stack.DrFedActor(id="act-1", iri="https://a.drfed.test/actors/1", handle="@a@a.drfed.test", inbox="https://a.drfed.test/actors/1/inbox")]


def _load_faults():
    spec = importlib.util.spec_from_file_location("devnet_faults", REPO_ROOT / "ops" / "dev" / "devnet" / "faults.py")
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_render_gateway_conf_normal_matches_gateway_conf_and_rejects_unknown() -> None:
    faults = _load_faults()
    assert faults.PROFILES == ("normal", "gf8", "gf9", "gf10", "gf14", "gf15", "gf16", "gf17")
    normal_conf = faults.render_gateway_conf("normal")
    assert normal_conf == (REPO_ROOT / "ops" / "dev" / "devnet" / "gateway.conf").read_text(encoding="utf-8")
    assert "events {" not in normal_conf and "http {" not in normal_conf
    assert "server {" in normal_conf and "location / {" in normal_conf
    compose_core = (REPO_ROOT / "ops" / "dev" / "devnet" / "compose.core.yml").read_text(encoding="utf-8")
    assert "/gateway.conf:/etc/nginx/conf.d/default.conf:ro" in compose_core
    assert "./gateway.conf:/etc/nginx/nginx.conf:ro" not in compose_core

    for bad in ("unknown", "", "gf1", "GF8"):
        with pytest.raises(ValueError, match="unknown gateway fault profile"):
            faults.render_gateway_conf(bad)


@pytest.mark.parametrize(
    ("profile", "expected_snippet"),
    [
        ("gf8", "location = /ap/kb/0"),
        ("gf9", "proxy_set_header Host $devnet_edge;"),
        ("gf10", "client_max_body_size 64k;"),
        ("gf14", "location = /nodeinfo/2.1"),
        ("gf15", "location ~ ^/ap/kb/[1-9][0-9]*$"),
        ("gf16", "location = /ap/kb"),
        ("gf17", "proxy_hide_header Access-Control-Allow-Origin;"),
    ],
)
def test_render_gateway_conf_fault_profiles(profile: str, expected_snippet: str) -> None:
    faults = _load_faults()
    rendered = faults.render_gateway_conf(profile)
    assert expected_snippet in rendered
    assert "location / {" in rendered
    assert rendered != faults.render_gateway_conf("normal")


def test_devnet_up_fault_logs_and_cli_dispatch(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from ops import devnet

    faults_dir = tmp_path / "ops" / "dev" / "devnet"
    faults_dir.mkdir(parents=True)
    shutil.copy2(REPO_ROOT / "ops" / "dev" / "devnet" / "faults.py", faults_dir / "faults.py")
    faults = _load_faults()

    calls: list[tuple[list[str], dict[str, str] | None]] = []

    def fake_run(command: list[str], *, env: dict[str, str] | None = None, capture: bool = False, check: bool = True) -> subprocess.CompletedProcess[str]:
        calls.append((list(command), env))
        if "compose.core.yml" in " ".join(command) and "up" in command:
            assert (tmp_path / ".dev" / "devnet" / "gateway.conf").read_text(encoding="utf-8") == faults.render_gateway_conf("normal")
        return subprocess.CompletedProcess(command, 0, stdout="", stderr="")

    monkeypatch.setattr(devnet, "_run", fake_run)

    net = devnet.Devnet(tmp_path)
    assert net.fault("gf10") == 0
    assert (net.run_dir / "gateway.conf").read_text(encoding="utf-8") == faults.render_gateway_conf("gf10")
    assert calls[-1][0] == ["docker", "compose", "-f", str(net.ops / "compose.core.yml"), "exec", "-T", "gateway", "nginx", "-s", "reload"]

    with pytest.raises(devnet.DevnetError):
        net.fault("invalid")

    assert net.logs("a") == 0
    assert calls[-1][0][-3:] == ["logs", "--tail", "200"]
    assert calls[-1][1] is not None and calls[-1][1]["CB_DEVNET_SERVER"] == "a"

    assert net.logs("b", "fedify") == 0
    assert calls[-1][0][-4:] == ["logs", "--tail", "200", "fedify"]
    assert calls[-1][1] is not None and calls[-1][1]["CB_DEVNET_SERVER"] == "b"

    with pytest.raises(devnet.DevnetError):
        net.logs("c")

    assert devnet.main(tmp_path, ["fault", "gf17"]) == 0
    assert (net.run_dir / "gateway.conf").read_text(encoding="utf-8") == faults.render_gateway_conf("gf17")
    assert devnet.main(tmp_path, ["logs", "a", "api"]) == 0
    assert calls[-1][0][-4:] == ["logs", "--tail", "200", "api"]

    with pytest.raises(SystemExit):
        devnet.main(tmp_path, ["fault", "unknown"])
    with pytest.raises(SystemExit):
        devnet.main(tmp_path, ["logs", "c"])

    monkeypatch.setattr(devnet.shutil, "which", lambda name: "/usr/bin/docker")
    monkeypatch.setattr(net, "_bootstrap", lambda server: None)
    monkeypatch.setattr(net, "info", lambda: 0)
    assert net.up(["a"], bot=False) == 0
    assert any("compose.core.yml" in " ".join(cmd) and "up" in cmd for cmd, _env in calls)

    net.run_dir.mkdir(parents=True, exist_ok=True)
    net.state.write_text("{}", encoding="utf-8")

    def fake_toolbox(command: list[str], *, state_writable: bool = False, workdir: str | None = None, fault_ctl_dir: Path | None = None) -> int:
        assert fault_ctl_dir is not None
        monkeypatch.setenv("CB_DEVNET_FAULT_CTL", str(fault_ctl_dir))
        _apply_gateway_fault("gf8")
        assert (net.run_dir / "gateway.conf").read_text(encoding="utf-8") == faults.render_gateway_conf("gf8")
        return 0

    monkeypatch.setattr(net, "_toolbox", fake_toolbox)
    assert net.test([]) == 0
    assert (net.run_dir / "gateway.conf").read_text(encoding="utf-8") == faults.render_gateway_conf("normal")

# The loops of the sidecar run each 5 or 60 seconds: a wait has three intervals of the slow loop.
WAIT_SECONDS = 200


def _state() -> dict[str, Any]:
    path = os.environ.get("CB_DEVNET_STATE")
    if not path:
        pytest.fail("CB_DEVNET_STATE is not set")
    return json.loads(Path(path).read_text(encoding="utf-8"))


def wait_for(what: str, probe: Callable[[], Any], timeout: float = WAIT_SECONDS) -> Any:
    """Call ``probe`` until it gives a true value; fail after ``timeout`` seconds."""
    deadline = time.monotonic() + timeout
    last: Any = None
    while time.monotonic() < deadline:
        last = probe()
        if last:
            return last
        time.sleep(1)
    pytest.fail(f"time limit: {what} (last value: {last!r})")


class Session:
    """The requests of one account of one server, with the session of a sign-in through ``/login``."""

    def __init__(self, origin: str, client: httpx.Client, username: str) -> None:
        self.origin = origin
        self.client = client
        self.username = username

    def __call__(self, method: str, path: str, body: Any = None, *, data: dict | None = None) -> httpx.Response:
        headers = {} if method in ("GET", "HEAD") else {"Origin": self.origin}
        return self.client.request(method, f"{self.origin}{path}", json=body, data=data, headers=headers)

    def ok(self, method: str, path: str, body: Any = None, *, status: tuple[int, ...] = (200, 201, 202)) -> Any:
        answer = self(method, path, body)
        assert answer.status_code in status, f"{method} {self.origin}{path}: {answer.status_code} {answer.text[:500]}"
        return answer.json() if answer.content else None


def sign_in(origin: str, username: str, password: str) -> Session:
    client = httpx.Client(timeout=60, follow_redirects=False)
    answer = client.post(f"{origin}/login", data={"username": username, "password": password}, headers={"Origin": origin})
    assert answer.status_code == 303, f"login {username}@{origin}: {answer.status_code}"
    return Session(origin, client, username)


@pytest.fixture(scope="module")
def admins() -> dict[str, Session]:
    state = _state()
    return {name: sign_in(entry["origin"], entry["username"], entry["password"]) for name, entry in state.items()}


def _readiness(admin: Session) -> dict[str, dict]:
    return {item["id"]: item for item in admin.ok("GET", "/api/admin/federation")["readiness"]["items"]}


@_devnet_only
def test_ka1_ka2_readiness_of_both_servers(admins: dict[str, Session]) -> None:
    """KA1 and KA2: on each server each required check passes through the public origin, and GF17 passes. GF15 applies when the server has a shared knowledge base: each server gets one."""
    assert set(admins) == {"a", "b"}
    for name, admin in admins.items():
        if not any(kb.get("is_personal") is False for kb in admin.ok("GET", "/kb").get("knowledge_bases", [])):
            admin.ok("POST", "/kb", {"label": f"Readiness {name.upper()}", "knowledge_type": "real"})

        def failures(admin: Session = admin) -> dict[str, tuple[str, str]]:
            return {key: (item["code"], item["detail"]) for key, item in _readiness(admin).items() if item["level"] == "required" and item["applies"] and not item["passed"]}

        # A new sidecar sends its first heartbeat and its loops make progress some seconds after the start.
        deadline = time.monotonic() + WAIT_SECONDS
        while (failed := failures()) and time.monotonic() < deadline:
            time.sleep(2)
        assert failed == {}, f"server {name}: {failed}"
        items = _readiness(admin)
        assert items["GF15"]["applies"] is True, items["GF15"]
        assert items["GF17"]["passed"] is True, items["GF17"]


@_devnet_only
def test_de4_a_reads_the_server_of_b_through_the_gateway(admins: dict[str, Session]) -> None:
    """Phase DE4: after the admin of A adds b.claire.test as a known server, the sidecar of A reads NodeInfo and the server actor of B through the gateway with TLS and the DNS of the network."""
    a = admins["a"]
    known = a("POST", "/api/admin/federation/known-servers", {"domain": "b.claire.test"})
    assert known.status_code in (200, 201), known.text

    def read() -> dict | None:
        for server in a.ok("GET", "/api/admin/federation/known-servers")["servers"]:
            if server["domain"] == "b.claire.test" and server["last_read_at"] and server["software_name"]:
                return server
        return None

    server = wait_for("the read of b.claire.test by A", read)
    assert (server["server_actor"], server["software_name"], server["error_count"]) == ("https://b.claire.test/ap/actors/server", "claire-bible", 0), server


# ── KA3: the completion criteria of KF1 to KF5 between A and B ────────────────────────────────

# The subject of the shared knowledge bases. Each name gets the suffix of the run, so that the knowledge bases of an earlier run on the same network are another subject.
CITIES = ["Seoul", "Busan", "Daegu", "Incheon", "Gwangju", "Daejeon", "Ulsan", "Suwon"]


def actor_of(session: Session) -> str:
    """The actor IRI of the account of ``session``, through WebFinger (a public route)."""
    host = session.origin.removeprefix("https://")
    found = session.client.get(f"{session.origin}/.well-known/webfinger", params={"resource": f"acct:{session.username}@{host}"}, headers={"Accept": "application/jrd+json"})
    assert found.status_code == 200, found.text
    return next(link["href"] for link in found.json()["links"] if link.get("rel") == "self")


def add_document(session: Session, title: str, entities: list[str], *, url: str | None = None) -> str:
    """One document of the personal knowledge base with these entities, through the path of the agent client (stage, claim, submit): the test gives the extraction, as an agent does. With ``url`` the server fetches the page (a web document); else the document is a pasted text."""
    text = f"{title}. " + " ".join(f"{name} is a city of Korea." for name in entities)
    staged = session.ok("POST", "/api/v1/extraction/stage", {"url_or_text": url or text, "title": title})
    document = staged["document_id"]
    claim = session.ok("POST", f"/api/v1/extraction/sources/{document}/claim", {"client_id": "devnet-test", "lease_seconds": 120})
    payload = {
        "schema_version": "agent-extract-v1",
        "summary": f"A note about {len(entities)} cities of Korea: " + ", ".join(entities) + ".",
        "detail": f"= {title}\n\n{text}",
        "entities": [{"name": name, "type": "Concept", "aliases": [], "observations": [f"{name} is a city of Korea."]} for name in entities],
        "relations": [],
        "model": "devnet-test",
    }
    done = session.ok("POST", f"/api/v1/extraction/sources/{document}/submit", {"claim_token": claim["claim_token"], "expected_content_hash": staged["content_hash"], "dry_run": False, "payload": payload})
    assert done["committed"] is True, done
    return document


def contribute(session: Session, kb_number: str, document: str) -> dict:
    """The founder contributes a document of its personal knowledge base to a shared knowledge base (a shared knowledge base takes documents only by contribution)."""
    return session.ok("POST", f"/kb/{kb_number}/contributions", {"document": document})


def fill_shared_kb(session: Session, kb_number: str, names: list[str], label: str) -> None:
    """Six documents with three entities each: the judgement gives the knowledge base a signature (at least five counted documents)."""
    for index in range(6):
        contribute(session, kb_number, add_document(session, f"{label} {index}", [names[(index + step) % len(names)] for step in range(3)]))


def known_b(a: Session) -> dict | None:
    return next((server for server in a.ok("GET", "/api/admin/federation/known-servers")["servers"] if server["domain"] == "b.claire.test"), None)


def read_b_again(a: Session) -> None:
    """A reads the /kb collection of B now: the admin of A removes B as a known server and adds it again (a read of a new known server starts at once). The daily read would come later."""
    if known_b(a) is not None:
        a.ok("DELETE", "/api/admin/federation/known-servers", {"domain": "b.claire.test"})
    a.ok("POST", "/api/admin/federation/known-servers", {"domain": "b.claire.test"})


def record_of(a: Session, kb_actor: str) -> dict | None:
    server = known_b(a)
    return None if server is None else next((kb for kb in server.get("kbs", []) if kb["iri"] == kb_actor), None)


def network_mode(admin: Session) -> None:
    """The root page of the network mode (KF2) needs the network mode; the switch needs a server tier (``none`` is valid)."""
    if admin.ok("GET", "/api/admin/server")["mode"] == "network":
        return
    admin.ok("PUT", "/api/admin/server-tier", {"tier": {"provider": "none"}})
    admin.ok("POST", "/api/admin/server/mode", {"mode": "network"})


@pytest.fixture(scope="module")
def world(admins: dict[str, Session]) -> dict[str, Any]:
    """A and B, each with a shared knowledge base about the same cities; A read B, and the judgement of A put Cities of B in the theme of Places."""
    a, b = admins["a"], admins["b"]
    sfx = secrets.token_hex(3)
    names = [f"{name} {sfx}" for name in CITIES]
    network_mode(a)
    cities = b.ok("POST", "/kb", {"label": f"Cities {sfx}", "knowledge_type": "real"})["knowledge_base"]
    fill_shared_kb(b, cities["number"], names, f"Cities {sfx}")
    b.ok("POST", "/api/admin/themes/judge", {})
    places = a.ok("POST", "/kb", {"label": f"Places {sfx}", "knowledge_type": "real"})["knowledge_base"]
    fill_shared_kb(a, places["number"], names, f"Places {sfx}")
    kb_actor = f"https://b.claire.test/ap/kb/{cities['number']}"
    read_b_again(a)
    record = wait_for("the record of Cities on A", lambda: record_of(a, kb_actor))
    a.ok("POST", "/api/admin/themes/judge", {})
    theme = next((t for t in a.ok("GET", "/themes")["themes"] if any(kb.get("number") == places["number"] for kb in a.ok("GET", f"/themes/{t['number']}/")["knowledge_bases"])), None)
    assert theme is not None, "the judgement of A made no theme with Places"
    return {"sfx": sfx, "names": names, "cities": cities, "places": places, "kb_actor": kb_actor, "remote_id": record["id"], "theme": theme["number"]}


@_devnet_only
def test_ka3_kf2_a_shared_knowledge_base_of_b_is_in_a_theme_and_on_the_root_page_of_a(admins: dict[str, Session], world: dict[str, Any]) -> None:
    """KF2: a shared knowledge base on server B appears in a theme of server A and on the root page of A, without any agreement."""
    a = admins["a"]
    theme = a.ok("GET", f"/themes/{world['theme']}/")
    assert [(kb["remote"], kb["server"], kb["url"]) for kb in theme["knowledge_bases"] if kb["remote"]] == [(True, "b.claire.test", f"https://b.claire.test/kb/{world['cities']['number']}/")], theme["knowledge_bases"]
    root = a.client.get(f"{a.origin}/", headers={"Accept": "text/html"})
    assert root.status_code == 200 and f'href="https://b.claire.test/kb/{world["cities"]["number"]}/"' in root.text


def remote_targets(session: Session, submission: str) -> list[dict]:
    return [target for item in session.ok("GET", f"/submissions/{submission}")["items"] for target in item["targets"] if target.get("remote")]


def decided(session: Session, submission: str) -> list[dict] | None:
    """The remote targets of a submission after the other server answered."""
    targets = remote_targets(session, submission)
    return targets if targets and all(target["state"] != "pending" for target in targets) else None


def pending_of(b: Session, kb_number: str, credit: str, title: str) -> dict | None:
    contributions = b.ok("GET", f"/kb/{kb_number}/contributions?state=pending")["contributions"]
    return next((c for c in contributions if (c.get("remote") or {}).get("credit") == credit and c["title"] == title), None)


@_devnet_only
def test_ka3_kf4_contributions_across_servers_with_approval_grant_revoke_and_reject(admins: dict[str, Session], world: dict[str, Any]) -> None:
    """KF4: an account of A contributes to a shared knowledge base of B; a reviewer of B approves; the submission page on A shows the decision; after a grant, the next contribution needs no approval; after Revoke, it needs approval again (and a rejection reaches A)."""
    a, b = admins["a"], admins["b"]
    cities, kb_actor, sfx = world["cities"]["number"], world["kb_actor"], world["sfx"]
    a_actor = actor_of(a)
    # 1. The account of A contributes to the theme that has Cities of B: its account actor sends Offer.
    first = add_document(a, f"Offer {sfx} 1", world["names"][:3])
    submission = a.ok("POST", "/submissions", {"documents": [first], "theme": world["theme"]})["submission"]["number"]
    waiting = wait_for("the Offer on B", lambda: pending_of(b, cities, a_actor, f"Offer {sfx} 1"))
    assert waiting["offer"]["server"] == "a.claire.test", waiting
    # 2. The reviewer of B approves: Accept with result reaches the submission page of A.
    b.ok("PATCH", f"/kb/{cities}/contributions/{waiting['number']}", {"action": "approve"})
    targets = wait_for("the Accept on A", lambda: decided(a, submission))
    assert [(t["state"], t["approved"], t["result"]) for t in targets] == [("accepted", True, f"{kb_actor}/documents/{waiting['number']}")], targets

    # 3. The reviewer of B gives the account of A the role collaborator: Grant. The next contribution, a direct choice of Cities, needs no approval.
    grant = b.ok("POST", f"/kb/{cities}/remote-collaborators", {"actor": a_actor})["grant"]
    wait_for("the Grant on A", lambda: [g for g in a.ok("GET", "/api/kb/remote-grants")["grants"] if g["knowledge_base"] == kb_actor and g["state"] == "active"])
    second = add_document(a, f"Offer {sfx} 2", world["names"][1:4])
    direct = a.ok("POST", "/submissions", {"documents": [second], "remote_knowledge_base": world["remote_id"]})["submission"]["number"]
    targets = wait_for("the Accept of the direct contribution on A", lambda: decided(a, direct))
    assert [(t["state"], t["approved"], t["with_grant"]) for t in targets] == [("accepted", True, True)], targets

    # 4. The reviewer of B ends the grant: Revoke. A refuses a direct choice now, and a contribution through the theme waits for approval; the reviewer rejects it with a reason code, and A shows the reason.
    b.ok("DELETE", f"/kb/{cities}/remote-collaborators/{grant['id']}")
    wait_for("the Revoke on A", lambda: [g for g in a.ok("GET", "/api/kb/remote-grants")["grants"] if g["knowledge_base"] == kb_actor and g["state"] == "revoked"])
    third = add_document(a, f"Offer {sfx} 3", world["names"][2:5])
    refused = a("POST", "/submissions", {"documents": [third], "remote_knowledge_base": world["remote_id"]})
    assert (refused.status_code, refused.json()["error"]) == (403, "grant_required"), refused.text
    again = a.ok("POST", "/submissions", {"documents": [third], "theme": world["theme"]})["submission"]["number"]
    waiting = wait_for("the third Offer on B", lambda: pending_of(b, cities, a_actor, f"Offer {sfx} 3"))
    b.ok("PATCH", f"/kb/{cities}/contributions/{waiting['number']}", {"action": "reject", "reason": "off_subject"})
    targets = wait_for("the Reject on A", lambda: decided(a, again))
    assert [(t["state"], t["approved"], t["reason"]) for t in targets] == [("rejected", False, "off_subject")], targets


class _Page:
    """Web pages that the test serves on the network claire-devnet-edge (the documents of KF3 and KF5). The content of a page can change (a revision)."""

    def __init__(self) -> None:
        import http.server
        import socket
        import threading

        pages: dict[str, str] = {}
        self.pages = pages

        class Handler(http.server.BaseHTTPRequestHandler):
            def do_GET(self) -> None:  # noqa: N802 - the name of the standard library
                body = pages.get(self.path)
                if body is None:
                    self.send_error(404)
                    return
                data = body.encode()
                self.send_response(200)
                self.send_header("Content-Type", "text/html; charset=utf-8")
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)

            def log_message(self, format: str, *args: Any) -> None:  # noqa: A002 - the name of the standard library
                return

        self.server = http.server.ThreadingHTTPServer(("0.0.0.0", 0), Handler)
        threading.Thread(target=self.server.serve_forever, daemon=True).start()
        # The address of this container on the network: the one that a connection to the gateway uses.
        probe = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        probe.connect((socket.gethostbyname("a.claire.test"), 443))
        self.address = probe.getsockname()[0]
        probe.close()

    def set(self, path: str, title: str, text: str) -> str:
        """Serve a page; return its URL. The fetcher refuses a thin body, so a page has some paragraphs of text."""
        self.pages[path] = f"<!doctype html><html><head><title>{title}</title></head><body><article><h1>{title}</h1>" + f"<p>{text}</p>" * 12 + "</article></body></html>"
        return f"http://{self.address}:{self.server.server_address[1]}{path}"


@_devnet_only
def test_ka3_kf3_curation_then_a_revision_and_a_removal_at_the_origin(admins: dict[str, Session], world: dict[str, Any]) -> None:
    """KF3: a revision on B reaches the copy on A without approval; a removal on B hides the copy on A and puts it in the review queue."""
    a, b = admins["a"], admins["b"]
    cities, places, kb_actor, sfx = world["cities"]["number"], world["places"]["number"], world["kb_actor"], world["sfx"]
    page = _Page()
    try:
        # B: a document from a web page, contributed to Cities.
        url = page.set(f"/harbours-{sfx}", f"Harbours {sfx}", "Busan has the largest harbour of Korea, and ships from many countries come to it each day.")
        ingested = b.ok("POST", "/ingest", {"payload": url, "extraction_mode": "provider", "renditions": []})
        assert ingested.get("error") is None, ingested
        root = ingested["document_id"]
        contribute(b, cities, root)
        number = next(c["number"] for c in b.ok("GET", f"/kb/{cities}/contributions")["contributions"] if c["document"] == root or c["title"] == f"Harbours {sfx}")
        document_iri = f"{kb_actor}/documents/{number}"

        # 1. A moderator of A opens Cities of B in Places: the sidecar of A reads the documents collection; then the document with its extraction.
        a.ok("GET", f"/kb/{places}/remote-sources?source={world['remote_id']}")
        wait_for("the documents of Cities on A", lambda: [d for d in (a.ok("GET", f"/kb/{places}/remote-sources?source={world['remote_id']}")["selected"] or {}).get("documents", []) if d["iri"] == document_iri])
        a.ok("POST", f"/kb/{places}/curations", {"remote_source": world["remote_id"], "documents": [document_iri]})
        wait_for("the curation on A", lambda: [c for c in a.ok("GET", f"/kb/{places}/remote-sources")["curations"] if c["document"] == document_iri and c["state"] == "done"])
        copy = next(c for c in a.ok("GET", f"/kb/{places}/contributions")["contributions"] if (c.get("remote") or {}).get("document") == document_iri)
        assert (copy["origin"], copy["state"]) == ("curation", "visible"), copy
        wait_for("the follow of Cities by A", lambda: [s for s in a.ok("GET", f"/kb/{places}/remote-sources")["sources"] if s["id"] == world["remote_id"] and s["follow"] == "accepted"])

        # 2. A revision on B: the page has a new content, and B fetches it again. The knowledge base actor sends Update; the copy on A has the new version without approval.
        page.set(f"/harbours-{sfx}", f"Harbours {sfx} revised", "Incheon has a large harbour too, near the capital, and the airport of the capital is near it.")
        again = b.ok("POST", "/reingest", {"target": root})
        assert again.get("title") == f"Harbours {sfx} revised", again

        def revised() -> dict | None:
            now = next(c for c in a.ok("GET", f"/kb/{places}/contributions")["contributions"] if c["number"] == copy["number"])
            return now if now["document"] != copy["document"] and [r["state"] for r in now.get("revisions", [])][-1:] == ["done"] else None

        now = wait_for("the revision on A", revised)
        assert now["state"] == "visible", now

        # 3. A removal on B: the knowledge base actor sends Delete with the reason code; the copy on A is hidden and waits in the review queue of Places.
        b.ok("POST", f"/kb/{cities}/contributions/{number}/remove", {"reason": "spam"})
        queued = wait_for("the removal on A", lambda: [c for c in a.ok("GET", f"/kb/{places}/contributions?state=hidden")["contributions"] if c["number"] == copy["number"]])
        assert (queued[0]["origin_removed"]["reason"], queued[0]["origin_removed"]["waiting"]) == ("spam", True), queued
    finally:
        page.server.shutdown()


@_devnet_only
def test_ka3_kf5_a_new_shared_knowledge_base_on_b_gets_the_matching_documents_of_an_account_of_a(admins: dict[str, Session], world: dict[str, Any]) -> None:
    """KF5: with kb_auto_contribution on both servers, a new shared knowledge base on B gets the matching documents of an account of A after a run."""
    a, b = admins["a"], admins["b"]
    sfx, names = world["sfx"], world["names"]
    a_actor = actor_of(a)
    for admin in (a, b):
        assert admin.ok("PUT", "/api/admin/auto-contribution", {"state": "on"})["state"] == "on"
    # The account of A: two web pages of the subject and one of another subject (an automatic rule never sends a pasted text, Section 9.9 rule 2), and an automatic rule with the theme of Places.
    page = _Page()
    try:
        matching = {f"Auto {sfx} {index}": add_document(a, f"Auto {sfx} {index}", names[index : index + 3], url=page.set(f"/auto-{sfx}-{index}", f"Auto {sfx} {index}", f"A guide to {', '.join(names[index : index + 3])}.")) for index in (4, 5)}
        add_document(a, f"Planets {sfx}", [f"Mars {sfx}", f"Jupiter {sfx}", f"Saturn {sfx}"], url=page.set(f"/planets-{sfx}", f"Planets {sfx}", "A guide to Mars, Jupiter, and Saturn."))
    finally:
        page.server.shutdown()
    rule = a.ok("PUT", "/api/auto-rule", {"state": "active", "memberships": False, "mutual": False, "include_uploads": False, "themes": [world["theme"]]})
    assert rule["rule"]["state"] == "active", rule

    # A new shared knowledge base on B about the same subject. A reads the /kb collection of B: the record tells that B accepts automatic contributions.
    towns = b.ok("POST", "/kb", {"label": f"Towns {sfx}", "knowledge_type": "real"})["knowledge_base"]
    fill_shared_kb(b, towns["number"], names, f"Towns {sfx}")
    b.ok("POST", "/api/admin/themes/judge", {})
    towns_actor = f"https://b.claire.test/ap/kb/{towns['number']}"
    read_b_again(a)
    wait_for("the record of Towns on A", lambda: record_of(a, towns_actor))

    # A run on A (the judgement run of the admin runs the rules after it): the account actor sends one automatic Offer for each matching document.
    a.ok("POST", "/api/admin/themes/judge", {})

    def offered() -> list[dict] | None:
        found = [c for c in b.ok("GET", f"/kb/{towns['number']}/contributions?state=pending")["contributions"] if (c.get("remote") or {}).get("credit") == a_actor]
        return found if {c["title"] for c in found} >= set(matching) else None

    pending = wait_for("the automatic offers on Towns of B", offered)
    assert {(c["title"], c["origin"]) for c in pending if c["title"] in matching} == {(title, "auto") for title in matching}, pending
    assert not [c for c in pending if c["title"] == f"Planets {sfx}"]

    # The reviewer of B approves one: the list of automatic contributions of the account of A shows the decision of B.
    approved = next(c for c in pending if c["title"] == f"Auto {sfx} 4")
    b.ok("PATCH", f"/kb/{towns['number']}/contributions/{approved['number']}", {"action": "approve"})

    def accepted() -> dict | None:
        rows = [c for c in a.ok("GET", "/api/contributions?origin=auto")["contributions"] if (c.get("remote_target") or {}).get("actor") == towns_actor]
        return next((row for row in rows if row["document"] == matching[f"Auto {sfx} 4"] and row["state"] == "accepted"), None)

    row = wait_for("the Accept of the automatic contribution on A", accepted)
    assert row["remote_target"]["result"] == f"{towns_actor}/documents/{approved['number']}", row


@_devnet_only
def test_ka3_kf1_servers_know_each_other_a_blocked_server_gets_404_and_the_single_mode_root(admins: dict[str, Session], world: dict[str, Any]) -> None:
    """KF1: two servers know each other after a contribution or an admin entry; a blocked server gets 404 on the shared-knowledge collections; in single mode / gives 302 to /@{username}/.

    The development network has two servers, so B blocks A (the criterion names a third server). The replay of a failed activity is not in this test: no public route makes an activity fail on purpose (tests/test_knowledge_federation_kf1.py has it)."""
    a, b = admins["a"], admins["b"]
    # A knows B by the admin entry (phase DE4); B knows A by the contributions of KF4 (they ran before this test with the same world).
    known_a = next((server for server in b.ok("GET", "/api/admin/federation/known-servers")["servers"] if server["domain"] == "a.claire.test"), None)
    assert known_a is not None and known_a["server_actor"] == "https://a.claire.test/ap/actors/server", known_a
    # B is in single mode: its root goes to the page of its admin.
    root = b.client.get(f"{b.origin}/")
    assert (root.status_code, root.headers.get("location")) == (302, f"/@{b.username}/"), (root.status_code, root.headers)

    # B blocks A: the signed read of the /kb collection of B by A gets 404 (unit KF1), and A records the failed read.
    started = time.time()
    b.ok("POST", "/api/admin/federation/domain-blocks", {"domain": "a.claire.test", "note": "devnet test of KF1"})
    try:
        read_b_again(a)

        def refused() -> dict | None:
            state = (known_b(a) or {}).get("kb_read") or {}
            return state if (state.get("last_error_at") or 0) >= started else None

        # The sidecar of A records a collection that does not load (here the 404 of B) as collection_unavailable; it does not keep the status.
        state = wait_for("the refused read of B by A", refused)
        assert state["last_error"] == "collection_unavailable", state
    finally:
        b.ok("DELETE", "/api/admin/federation/domain-blocks", {"domain": "a.claire.test"})
    # Without the block, the next read of A gets the collection again.
    read_b_again(a)
    wait_for("the read of B by A after the block", lambda: record_of(a, world["kb_actor"]))


@_devnet_only
def test_ka3_kf5_switch_off_stops_and_resumes(admins: dict[str, Session], world: dict[str, Any]) -> None:
    """KF5 switch off on B blocks automatic offers; turning it on and re-reading resumes them, and switch off on A pauses sending until turned on."""
    a, b = admins["a"], admins["b"]
    sfx = secrets.token_hex(3)
    prev_a = a.ok("GET", "/api/admin/auto-contribution")["state"]
    prev_b = b.ok("GET", "/api/admin/auto-contribution")["state"]
    rule_body = {"state": "active", "memberships": False, "mutual": False, "include_uploads": False, "themes": [world["theme"]]}
    try:
        assert a.ok("PUT", "/api/admin/auto-contribution", {"state": "on"})["state"] == "on"
        assert b.ok("PUT", "/api/admin/auto-contribution", {"state": "off"})["state"] == "off"
        assert a.ok("PUT", "/api/auto-rule", rule_body)["rule"]["state"] == "active"

        page = _Page()
        try:
            doc_b_off = add_document(
                a,
                f"AutoSwitchOff {sfx}",
                world["names"][:3],
                url=page.set(f"/auto-sw-off-{sfx}", f"AutoSwitchOff {sfx}", f"A guide to {', '.join(world['names'][:3])}."),
            )
            doc_a_off = add_document(
                a,
                f"AutoSenderOff {sfx}",
                world["names"][1:4],
                url=page.set(f"/auto-a-off-{sfx}", f"AutoSenderOff {sfx}", f"A guide to {', '.join(world['names'][1:4])}."),
            )
        finally:
            page.server.shutdown()

        villages = b.ok("POST", "/kb", {"label": f"Villages {sfx}", "knowledge_type": "real"})["knowledge_base"]
        fill_shared_kb(b, villages["number"], world["names"], f"Villages {sfx}")
        b.ok("POST", "/api/admin/themes/judge", {})
        villages_actor = f"https://b.claire.test/ap/kb/{villages['number']}"

        read_b_again(a)
        wait_for("the record of Villages on A while B switch is off", lambda: record_of(a, villages_actor))
        a.ok("POST", "/api/admin/themes/judge", {})

        dry_closed = a.ok("POST", "/api/auto-rule/dry-run", rule_body)
        target_closed = next((t for t in dry_closed["targets"] if t.get("actor") == villages_actor), None)
        assert target_closed is not None and (target_closed["mode"], target_closed["documents"]) == ("closed", []), target_closed
        assert not [c for c in b.ok("GET", f"/kb/{villages['number']}/contributions?state=pending")["contributions"] if c["title"] == f"AutoSwitchOff {sfx}"]

        assert b.ok("PUT", "/api/admin/auto-contribution", {"state": "on"})["state"] == "on"
        read_b_again(a)

        def target_open() -> dict | None:
            dry = a.ok("POST", "/api/auto-rule/dry-run", rule_body)
            target = next((t for t in dry["targets"] if t.get("actor") == villages_actor), None)
            return target if target is not None and target["mode"] == "pending" else None

        wait_for("Villages target mode pending on A after B turns switch on", target_open)
        a.ok("POST", "/api/admin/themes/judge", {})
        wait_for(
            "automatic offers on Villages after B turns switch on",
            lambda: [c for c in b.ok("GET", f"/kb/{villages['number']}/contributions?state=pending")["contributions"] if c["title"] == f"AutoSwitchOff {sfx}"],
        )

        # Turn A off, add a fresh matching web page, verify judgement does not offer it while off, then turn A on to resume.
        assert a.ok("PUT", "/api/admin/auto-contribution", {"state": "off"})["state"] == "off"
        page2 = _Page()
        try:
            doc_paused = add_document(
                a,
                f"AutoPaused {sfx}",
                world["names"][2:5],
                url=page2.set(f"/auto-paused-{sfx}", f"AutoPaused {sfx}", f"A guide to {', '.join(world['names'][2:5])}."),
            )
        finally:
            page2.server.shutdown()
        a.ok("POST", "/api/admin/themes/judge", {})
        dry_a_off = a.ok("POST", "/api/auto-rule/dry-run", rule_body)
        assert dry_a_off["server_switch"] is False, dry_a_off
        assert not [c for c in a.ok("GET", "/api/contributions?origin=auto")["contributions"] if c["document"] == doc_paused and (c.get("remote_target") or {}).get("actor") == villages_actor]

        assert a.ok("PUT", "/api/admin/auto-contribution", {"state": "on"})["state"] == "on"
        a.ok("POST", "/api/admin/themes/judge", {})
        wait_for(
            "paused document delivered to Villages after A turns switch on",
            lambda: [c for c in b.ok("GET", f"/kb/{villages['number']}/contributions?state=pending")["contributions"] if c["title"] == f"AutoPaused {sfx}"],
        )
        assert [c for c in a.ok("GET", "/api/contributions?origin=auto")["contributions"] if c["document"] in (doc_b_off, doc_a_off, doc_paused)]
    finally:
        a.ok("PUT", "/api/admin/auto-contribution", {"state": prev_a})
        b.ok("PUT", "/api/admin/auto-contribution", {"state": prev_b})


@_devnet_only
def test_ka3_failed_activity_replay(admins: dict[str, Session], world: dict[str, Any]) -> None:
    """Admin inbox journal and replay routes on a live server: inspect entries, replay any failed row, and verify 409/404/400 guards."""
    a, b = admins["a"], admins["b"]
    entries = b.ok("GET", "/api/admin/federation/journal")["entries"]
    if not entries:
        sfx = secrets.token_hex(3)
        doc = add_document(a, f"JournalSeed {sfx}", world["names"][:3])
        a.ok("POST", "/submissions", {"documents": [doc], "theme": world["theme"]})
        entries = wait_for("an inbox journal entry on B", lambda: b.ok("GET", "/api/admin/federation/journal")["entries"])

    failed_entries = b.ok("GET", "/api/admin/federation/journal?status=failed")["entries"]
    for failed_entry in failed_entries:
        replayed = b.ok("POST", f"/api/admin/federation/journal/{failed_entry['id']}/replay", {})
        assert replayed["entry"]["id"] == failed_entry["id"]

    processed = next((e for e in b.ok("GET", "/api/admin/federation/journal")["entries"] if e["status"] == "processed"), None)
    assert processed is not None, entries
    detail = b.ok("GET", f"/api/admin/federation/journal/{processed['id']}")["entry"]
    assert detail["id"] == processed["id"] and isinstance(detail["payload"], dict) and detail["replayable"] is False

    conflict = b("POST", f"/api/admin/federation/journal/{processed['id']}/replay", {})
    assert (conflict.status_code, conflict.json()) == (409, {"error": "journal_entry_not_replayable", "states": ["failed", "received"]}), conflict.text
    missing = b("POST", "/api/admin/federation/journal/999999999/replay", {})
    assert (missing.status_code, missing.json()) == (404, {"error": "journal_entry_not_found"}), missing.text
    bad_body = b("POST", f"/api/admin/federation/journal/{processed['id']}/replay", {"extra": 1})
    assert bad_body.status_code == 400 and bad_body.json()["error"] == "invalid_request", bad_body.text
    with httpx.Client(timeout=30, follow_redirects=False) as anon:
        unauth = anon.post(f"{b.origin}/api/admin/federation/journal/{processed['id']}/replay", json={}, headers={"Origin": b.origin})
        assert unauth.status_code == 404


_FAULT_TARGETS: tuple[tuple[str, str], ...] = (
    ("gf8", "GF8"),
    ("gf9", "GF9"),
    ("gf10", "GF10"),
    ("gf14", "GF14"),
    ("gf15", "GF15"),
    ("gf16", "GF16"),
    ("gf17", "GF17"),
)
_FAULT_CTL_SEQ = 0


def _apply_gateway_fault(profile: str) -> None:
    global _FAULT_CTL_SEQ
    ctl_dir = os.environ.get("CB_DEVNET_FAULT_CTL")
    if ctl_dir:
        _FAULT_CTL_SEQ += 1
        seq = str(_FAULT_CTL_SEQ)
        ctl = Path(ctl_dir)
        req_tmp = ctl / "request.tmp"
        req = ctl / "request"
        resp = ctl / "response"
        resp.unlink(missing_ok=True)
        req_tmp.write_text(f"{seq} {profile}\n", encoding="utf-8")
        os.replace(req_tmp, req)
        deadline = time.monotonic() + 30.0
        while time.monotonic() < deadline:
            if resp.is_file():
                text = resp.read_text(encoding="utf-8").strip()
                if text.startswith(f"{seq} "):
                    resp.unlink(missing_ok=True)
                    if text == f"{seq} ok":
                        return
                    raise RuntimeError(f"gateway fault {profile} failed: {text}")
            time.sleep(0.05)
        raise TimeoutError(f"gateway fault control timed out for {profile}")
    if shutil.which("docker") is not None:
        from ops import devnet

        devnet.Devnet(REPO_ROOT).fault(profile)
        return
    pytest.skip("gateway fault control requires CB_DEVNET_FAULT_CTL or docker")


@_devnet_only
def test_de5_kf6_fault_profiles_and_recovery(admins: dict[str, Session], world: dict[str, Any]) -> None:
    """DE5 / KF6: each gateway fault profile fails only its target readiness check, and restoring normal recovers all checks."""
    a = admins["a"]
    target_ids = tuple(gf for _profile, gf in _FAULT_TARGETS)

    def all_ready() -> dict[str, dict] | None:
        items = _readiness(a)
        return items if all(items[gf]["applies"] and items[gf]["passed"] for gf in target_ids) else None

    wait_for("all target GF checks passing before fault injection", all_ready)
    for profile, target_gf in _FAULT_TARGETS:
        def target_failed(gf: str = target_gf) -> dict[str, dict] | None:
            items = _readiness(a)
            return items if not items[gf]["passed"] else None

        def all_recovered() -> dict[str, dict] | None:
            items = _readiness(a)
            return items if all(items[gf]["passed"] for gf in target_ids) else None

        try:
            _apply_gateway_fault(profile)
            items = wait_for(f"{target_gf} fails under gateway fault {profile}", target_failed)
            assert items[target_gf]["passed"] is False, items[target_gf]
            extra_failures = [gf for gf in target_ids if gf != target_gf and not items[gf]["passed"]]
            assert extra_failures == [], f"fault {profile} broke extra checks: {extra_failures}"
        finally:
            _apply_gateway_fault("normal")
            wait_for(f"readiness recovery after gateway fault {profile}", all_recovered)
