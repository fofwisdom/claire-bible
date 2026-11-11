"""Federation readiness checks GF1 to GF7, the ``/api/admin/federation`` switch, and ``claire instance check --federation`` (Fedify sidecar design Section 12, unit FP1-6).

No test calls a public server. The GF7 fetch is a test function, or it goes to a server on the loopback address.
"""

from __future__ import annotations

import http.server
import json
import os
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
from starlette.testclient import TestClient

from claire import cli
from claire.accounts import federation_readiness
from claire.api import server
from claire.api.federation_internal import ensure_service_key, service_key_path
from claire.api.federation_routes import HEARTBEAT_FILE
from claire.store import accounts as accounts_store
from claire.store import auth_db
from claire.store import db as dbm
from claire.store.secrets import reset_secret_service

PUBLIC_URL = "http://127.0.0.1:8765"
OWNER_TOKEN = "owner-" + "o" * 32
NOW = 1_800_000_000.0
STALE = 16 * 60.0
ALL_IDS = ["GF1", "GF2", "GF3", "GF4", "GF5", "GF6", "GF7"]


@dataclass
class _Settings:
    db_file: Path
    data_dir: Path
    environment: str = "development"
    public_url: str = PUBLIC_URL
    inject_token: str = OWNER_TOKEN
    readonly_token: str = "readonly-" + "r" * 32
    cors_allowed_origins: str = ""
    anonymous_readonly: bool = True
    fqdn: str = "127.0.0.1:8765"
    multi_theme: bool = True
    telemetry_retention_days: int = 30


@pytest.fixture(autouse=True)
def _isolate(monkeypatch):
    for key in list(os.environ):
        if key.startswith(("CLAIRE_", "CB_")):
            monkeypatch.delenv(key, raising=False)
    monkeypatch.setenv("CLAIRE_SECRET_KEY", "44" * 32)
    reset_secret_service()
    yield
    reset_secret_service()


def _metadata_fetch(issuer: str = PUBLIC_URL, status: int = 200):
    def fetch(url: str) -> tuple[int, bytes]:
        assert url == issuer + "/.well-known/oauth-authorization-server"
        return status, json.dumps({"issuer": issuer, "token_endpoint": issuer + "/oauth/token"}).encode()

    return fetch


def _write_heartbeat(data_dir: Path, *, received_at: float, contract_version: str = "1.0", ok: bool | None = True) -> None:
    path = data_dir / HEARTBEAT_FILE
    path.parent.mkdir(parents=True, exist_ok=True)
    record = {"received_at": received_at, "contract_version": contract_version, "sidecar_version": "0.1.0", "queue_depth": 0, "self_check": {"ok": ok, "checked_at": received_at if ok is not None else None}}
    path.write_text(json.dumps(record), encoding="utf-8")


def _prepare(tmp_path: Path, *, heartbeat_at: float, **overrides: Any) -> tuple[_Settings, int]:
    """A network-mode instance that passes all the checks: admin, one member, service key, and a heartbeat."""
    settings = _Settings(db_file=tmp_path / "claire.db", data_dir=tmp_path, **overrides)
    conn = dbm.connect(settings.db_file)
    try:
        dbm.init_db(conn)
        store = accounts_store.AccountStore(conn)
        store.complete_setup(1, username="admin_kim", display_name="Admin Kim")
        store.set_setting("mode", "network", updated_by=1)
        cur = conn.execute("INSERT INTO accounts (public_id, username, role, status, display_name, created_at, activated_at, updated_at) VALUES (223456789012, 'member_alpha', 'member', 'active', 'Alpha', 0, 0, 0)")
        conn.commit()
        member = int(cur.lastrowid)
    finally:
        conn.close()
    auth_db.connect_auth_db(auth_db.auth_db_path(tmp_path)).close()
    ensure_service_key(service_key_path(tmp_path))
    _write_heartbeat(tmp_path, received_at=heartbeat_at)
    return settings, member


def _report(settings: _Settings, *, now: float = NOW, fetch=None) -> dict[str, dict[str, Any]]:
    conn = dbm.connect_existing(settings.db_file)
    try:
        report = federation_readiness.check_federation_readiness(conn, settings, now=now, fetch=fetch or _metadata_fetch())
    finally:
        conn.close()
    data = report.to_dict()
    assert [item["id"] for item in data["items"]] == ALL_IDS
    assert data["passed"] is all(item["passed"] for item in data["items"])
    return {item["id"]: item for item in data["items"]}


def _failed(items: dict[str, dict[str, Any]]) -> list[str]:
    return [item_id for item_id, item in items.items() if not item["passed"]]


def _store_call(settings: _Settings, fn):
    conn = dbm.connect_existing(settings.db_file)
    try:
        return fn(accounts_store.AccountStore(conn))
    finally:
        conn.close()


@pytest.fixture
def ready(tmp_path):
    settings, _member = _prepare(tmp_path, heartbeat_at=NOW - 10)
    return settings


# --- Checks GF1 to GF7 (module interface and file I/O) ---


def test_all_checks_pass_for_a_ready_instance(ready):
    items = _report(ready)
    assert _failed(items) == []
    assert set(items["GF1"]) == {"id", "name", "passed", "detail"}


def test_gf1_fails_in_single_mode(ready):
    _store_call(ready, lambda store: store.set_setting("mode", "single", updated_by=1))
    assert _failed(_report(ready)) == ["GF1"]


def test_gf2_and_gf6_fail_when_there_is_no_heartbeat(ready):
    (ready.data_dir / HEARTBEAT_FILE).unlink()
    assert _failed(_report(ready)) == ["GF2", "GF6"]


def test_gf2_and_gf6_fail_when_the_heartbeat_is_old(ready):
    limit = federation_readiness.HEARTBEAT_MAX_AGE_SECONDS
    _write_heartbeat(ready.data_dir, received_at=NOW - limit)
    assert _failed(_report(ready)) == []
    _write_heartbeat(ready.data_dir, received_at=NOW - limit - 1)
    assert _failed(_report(ready)) == ["GF2", "GF6"]


def test_gf2_fails_when_the_contract_major_version_is_different(ready):
    _write_heartbeat(ready.data_dir, received_at=NOW - 10, contract_version="1.7")
    assert _failed(_report(ready)) == []
    _write_heartbeat(ready.data_dir, received_at=NOW - 10, contract_version="2.0")
    assert _failed(_report(ready)) == ["GF2"]


def test_gf3_fails_when_the_service_key_file_is_absent(ready):
    service_key_path(ready.data_dir).unlink()
    assert _failed(_report(ready)) == ["GF3"]
    assert not service_key_path(ready.data_dir).exists()


def test_gf3_fails_when_the_service_key_mode_is_not_0600(ready):
    os.chmod(service_key_path(ready.data_dir), 0o644)
    assert _failed(_report(ready)) == ["GF3"]


def test_gf3_fails_when_the_service_key_length_is_wrong(ready):
    service_key_path(ready.data_dir).write_bytes(b"k" * 16)
    assert _failed(_report(ready)) == ["GF3"]


def test_gf4_fails_without_a_master_key_and_does_not_make_a_key_file(ready, monkeypatch):
    monkeypatch.delenv("CLAIRE_SECRET_KEY")
    key_file = federation_readiness.master_key_path(ready.data_dir)
    assert _failed(_report(ready)) == ["GF4"]
    assert not key_file.exists()


def test_gf4_reads_the_master_key_file(ready, monkeypatch):
    monkeypatch.delenv("CLAIRE_SECRET_KEY")
    key_file = federation_readiness.master_key_path(ready.data_dir)
    key_file.write_bytes(b"m" * 32)
    assert _failed(_report(ready)) == []
    key_file.write_bytes(b"m" * 8)
    assert _failed(_report(ready)) == ["GF4"]
    assert key_file.read_bytes() == b"m" * 8


def test_gf5_passes_for_an_https_origin_in_production(tmp_path):
    origin = "https://claire.example"
    settings, _member = _prepare(tmp_path, heartbeat_at=NOW - 10, environment="production", public_url=origin + "/", fqdn="claire.example")
    assert _failed(_report(settings, fetch=_metadata_fetch(origin))) == []


def test_gf5_fails_for_an_http_origin_in_production(tmp_path):
    settings, _member = _prepare(tmp_path, heartbeat_at=NOW - 10, environment="production", public_url="http://claire.example/", fqdn="claire.example")
    items = _report(settings)
    assert items["GF5"]["passed"] is False
    assert "https" in items["GF5"]["detail"]
    # There is no valid origin to send the GF7 request to.
    assert _failed(items) == ["GF5", "GF7"]


@pytest.mark.parametrize("ok", [False, None])
def test_gf6_fails_when_the_public_self_check_did_not_pass(ready, ok):
    _write_heartbeat(ready.data_dir, received_at=NOW - 10, ok=ok)
    assert _failed(_report(ready)) == ["GF6"]


def test_gf7_fails_when_the_public_origin_does_not_return_the_claire_metadata(ready):
    def unreachable(url: str) -> tuple[int, bytes]:
        raise OSError("connection refused")

    assert _failed(_report(ready, fetch=_metadata_fetch(status=404))) == ["GF7"]
    assert _failed(_report(ready, fetch=_metadata_fetch("https://other.example"))) == ["GF7"]
    assert _failed(_report(ready, fetch=lambda url: (200, b"<html>sidecar</html>"))) == ["GF7"]
    assert _failed(_report(ready, fetch=unreachable)) == ["GF7"]


def test_default_fetch_does_not_follow_a_redirect():
    """Call to the external library httpx. The server is on the loopback address."""

    class Handler(http.server.BaseHTTPRequestHandler):
        def do_GET(self):  # noqa: N802
            self.send_response(302)
            self.send_header("Location", "/other")
            self.send_header("Content-Length", "0")
            self.end_headers()

        def log_message(self, *args):
            pass

    httpd = http.server.HTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    try:
        status, body = federation_readiness.fetch_metadata(f"http://127.0.0.1:{httpd.server_address[1]}/.well-known/oauth-authorization-server")
    finally:
        httpd.shutdown()
        httpd.server_close()
        thread.join(timeout=5)
    assert (status, body) == (302, b"")


# --- /api/admin/federation (module interface: route, policy, store, audit) ---


class _Client:
    def __init__(self, client: TestClient) -> None:
        self.client = client

    def request(self, method: str, path: str, *, session: str | None = None, headers: dict[str, str] | None = None, **kwargs: Any):
        headers = dict(headers or {})
        if session is not None:
            headers["Cookie"] = f"claire_session={session}"
        if method not in {"GET", "HEAD"}:
            headers.setdefault("Origin", PUBLIC_URL)
        self.client.cookies.clear()
        response = self.client.request(method, path, headers=headers, follow_redirects=False, **kwargs)
        self.client.cookies.clear()
        return response


def _session(settings: _Settings, account_id: int, *, authenticated_ago: float = 0.0) -> str:
    conn = dbm.connect_existing(settings.db_file)
    try:
        return accounts_store.create_account_session(conn, account_id=account_id, scope="owner", now=time.time() - authenticated_ago)
    finally:
        conn.close()


def _federation(settings: _Settings) -> str:
    return _store_call(settings, lambda store: store.federation_state())


def _audit_rows(settings: _Settings) -> list[tuple[int, str, str | None]]:
    conn = auth_db.connect_auth_db(auth_db.auth_db_path(settings.data_dir))
    try:
        return [tuple(row) for row in conn.execute("SELECT actor_account_id, action, detail_json FROM admin_audit ORDER BY id").fetchall()]
    finally:
        conn.close()


@pytest.fixture
def env(tmp_path, monkeypatch):
    settings, member = _prepare(tmp_path, heartbeat_at=time.time())
    monkeypatch.setattr(federation_readiness, "fetch_metadata", _metadata_fetch())
    app = server.create_app(settings, SimpleNamespace(provider=SimpleNamespace(name="stub")))
    with TestClient(app, base_url=PUBLIC_URL) as tc:
        yield SimpleNamespace(client=_Client(tc), settings=settings, member=member, admin=_session(settings, 1))


def test_switch_refuses_on_when_a_readiness_check_fails(env):
    _write_heartbeat(env.settings.data_dir, received_at=time.time(), ok=False)

    response = env.client.request("POST", "/api/admin/federation", session=env.admin, json={"federation": "on"})

    assert response.status_code == 400
    body = response.json()
    assert body["error"] == "readiness_failed"
    assert [item["id"] for item in body["failures"]] == ["GF6"]
    assert _federation(env.settings) == "off"
    assert _audit_rows(env.settings) == []


def test_switch_accepts_off_when_the_readiness_checks_fail(env):
    _store_call(env.settings, lambda store: store.set_federation_state("on", updated_by=1))
    (env.settings.data_dir / HEARTBEAT_FILE).unlink()
    service_key_path(env.settings.data_dir).unlink()

    response = env.client.request("POST", "/api/admin/federation", session=env.admin, json={"federation": "off"})

    assert response.status_code == 200
    assert response.json() == {"federation": "off", "changed": True}
    assert _federation(env.settings) == "off"
    assert _audit_rows(env.settings) == [(1, "federation.update", '{"federation": "off"}')]


def test_status_shows_the_failed_checks_and_no_heartbeat(env):
    (env.settings.data_dir / HEARTBEAT_FILE).unlink()

    response = env.client.request("GET", "/api/admin/federation", session=env.admin)

    assert response.status_code == 200
    body = response.json()
    assert set(body) == {"federation", "mode", "readiness", "heartbeat"}
    assert (body["federation"], body["mode"], body["heartbeat"]) == ("off", "network", None)
    assert body["readiness"]["passed"] is False
    assert [item["id"] for item in body["readiness"]["items"] if not item["passed"]] == ["GF2", "GF6"]


@pytest.mark.parametrize("method,payload", [("GET", None), ("POST", {"federation": "on"})])
def test_member_and_anonymous_principal_get_404(env, method, payload):
    member = _session(env.settings, env.member)
    kwargs = {} if payload is None else {"json": payload}

    for session in (member, None):
        response = env.client.request(method, "/api/admin/federation", session=session, **kwargs)
        assert response.status_code == 404
        assert response.text == "Not Found"
    assert _federation(env.settings) == "off"


@pytest.mark.parametrize("payload", [{"federation": "on", "force": True}, {"federation": "enabled"}, {"federation": True}, {}])
def test_switch_refuses_unknown_fields_and_values(env, payload):
    response = env.client.request("POST", "/api/admin/federation", session=env.admin, json=payload)

    assert response.status_code == 400
    assert response.json()["error"] == "invalid_request"
    assert _federation(env.settings) == "off"


def test_switch_needs_recent_authentication_and_the_same_origin(env):
    stale = _session(env.settings, 1, authenticated_ago=STALE)

    no_step_up = env.client.request("POST", "/api/admin/federation", session=stale, json={"federation": "on"})
    cross_origin = env.client.request("POST", "/api/admin/federation", session=env.admin, headers={"Origin": "https://attacker.example"}, json={"federation": "on"})

    assert no_step_up.status_code == 401
    assert no_step_up.json()["error"] == "reauth_required"
    assert cross_origin.status_code == 403
    assert _federation(env.settings) == "off"


# --- claire instance check --federation (CLI output) ---


def _run_cli(capsys, settings: _Settings, monkeypatch, argv: list[str]) -> tuple[int, dict[str, Any]]:
    monkeypatch.setattr("claire.cli.get_settings", lambda: settings)
    monkeypatch.setattr(federation_readiness, "fetch_metadata", _metadata_fetch())
    code = cli.main(argv)
    return code, json.loads(capsys.readouterr().out)


def test_cli_federation_check_prints_the_report_and_exits_with_0(tmp_path, monkeypatch, capsys):
    settings, _member = _prepare(tmp_path, heartbeat_at=time.time())

    code, output = _run_cli(capsys, settings, monkeypatch, ["instance", "check", "--federation", "--json"])

    assert code == 0
    assert (output["status"], output["exit_code"]) == ("success", 0)
    assert output["data"]["check"] == "federation"
    assert output["data"]["passed"] is True
    assert [item["id"] for item in output["data"]["items"]] == ALL_IDS


def test_cli_federation_check_exits_with_1_when_a_check_fails(tmp_path, monkeypatch, capsys):
    settings, _member = _prepare(tmp_path, heartbeat_at=time.time() - 3600)

    code, output = _run_cli(capsys, settings, monkeypatch, ["instance", "check", "--federation", "--json"])

    assert code == 1
    assert (output["status"], output["exit_code"]) == ("error", 1)
    assert output["data"]["passed"] is False
    assert [item["id"] for item in output["data"]["items"] if not item["passed"]] == ["GF2", "GF6"]


# --- End-to-end ---


def test_e2e_admin_turns_federation_on_and_reads_the_state(env):
    before = env.client.request("GET", "/api/admin/federation", session=env.admin)
    assert before.status_code == 200
    assert before.json()["federation"] == "off"
    assert before.json()["readiness"]["passed"] is True
    assert [item["id"] for item in before.json()["readiness"]["items"]] == ALL_IDS

    changed = env.client.request("POST", "/api/admin/federation", session=env.admin, json={"federation": "on"})
    assert changed.status_code == 200
    assert changed.json() == {"federation": "on", "changed": True}

    after = env.client.request("GET", "/api/admin/federation", session=env.admin)
    assert after.json()["federation"] == "on"
    assert after.json()["mode"] == "network"
    assert after.json()["heartbeat"]["self_check"]["ok"] is True
    assert after.json()["heartbeat"]["contract_version"] == "1.0"
    assert _federation(env.settings) == "on"
    assert _audit_rows(env.settings) == [(1, "federation.update", '{"federation": "on"}')]

    again = env.client.request("POST", "/api/admin/federation", session=env.admin, json={"federation": "on"})
    assert again.json() == {"federation": "on", "changed": False}
    assert len(_audit_rows(env.settings)) == 1
