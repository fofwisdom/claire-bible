"""Federation readiness checks GF1 to GF17, the ``/api/admin/federation`` switch, and ``claire server check --federation`` (Fedify sidecar design Section 12, unit FP1-6; knowledge federation design Section 14, unit KF6).

No test calls a public server. The HTTP helpers use mock functions or servers on the loopback address.
"""

from __future__ import annotations

import datetime as dt
import http.server
import ipaddress
import json
import os
import ssl
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.x509.oid import NameOID
from starlette.testclient import TestClient

from claire import cli
from claire.accounts import federation_readiness
from claire.api import federation_routes, server
from claire.api.federation_internal import ensure_service_key, service_key_path
from claire.api.federation_routes import HEARTBEAT_FILE
from claire.knowledge_federation import vocabulary
from claire.store import accounts as accounts_store
from claire.store import actor_keys, auth_db
from claire.store import db as dbm
from claire.store.secrets import SecretService, reset_secret_service

PUBLIC_URL = "http://127.0.0.1:8765"
OWNER_TOKEN = "owner-" + "o" * 32
NOW = 1_800_000_000.0
STALE = 16 * 60.0
ALL_IDS = [f"GF{i}" for i in range(1, 18)]


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


def _stored_pem(data_dir: Path | None, *, account_id: int | None = None, kb_number: int | None = None) -> str:
    if data_dir is not None:
        auth_conn = auth_db.connect_auth_db(auth_db.auth_db_path(data_dir))
        try:
            jwks = actor_keys.load_public_jwks(auth_conn, int(account_id or 0)) if kb_number is None else actor_keys.load_kb_public_jwks(auth_conn, kb_number)
            if "rsa2048" in jwks:
                return actor_keys.public_key_pem(jwks["rsa2048"])
        finally:
            auth_conn.close()
    return "-----BEGIN PUBLIC KEY-----\nMIIBIjANBgkqhkiG9w0BAQEFAAOCAQ8AMIIBCgKCAQEAtest\n-----END PUBLIC KEY-----\n"


def _public_request(
    issuer: str = PUBLIC_URL,
    *,
    data_dir: Path | None = None,
    accepts_automatic: bool = False,
    overrides: dict[tuple[str, str], federation_readiness.HttpResponse | Exception] | None = None,
):
    origin = issuer.rstrip("/")
    override_map = dict(overrides or {})

    def requester(method: str, url: str, *, headers: dict[str, str] | None = None, body: bytes | None = None) -> federation_readiness.HttpResponse:
        path = url[len(origin) :] if url.startswith(origin) else url
        key = (method, path)
        if key in override_map:
            custom = override_map[key]
            if isinstance(custom, Exception):
                raise custom
            return custom
        if key == ("POST", "/ap/inbox"):
            return federation_readiness.HttpResponse(status=401, headers={"x-claire-fedify-server": "1"}, body=b"")
        if key == ("GET", "/ns/v1"):
            return federation_readiness.HttpResponse(status=200, headers={"content-type": vocabulary.CONTEXT_MEDIA_TYPE, "access-control-allow-origin": "*"}, body=vocabulary.context_json().encode("utf-8"))
        if key == ("GET", "/ns"):
            return federation_readiness.HttpResponse(status=200, headers={"content-type": "text/html; charset=utf-8"}, body=b"<!doctype html><title>Claire NS</title>")
        if key == ("GET", "/.well-known/nodeinfo"):
            return federation_readiness.HttpResponse(status=200, headers={"content-type": "application/json"}, body=json.dumps({"links": [{"rel": "http://nodeinfo.diaspora.software/ns/schema/2.1", "href": f"{origin}/nodeinfo/2.1"}]}).encode("utf-8"))
        if key == ("GET", "/nodeinfo/2.1"):
            return federation_readiness.HttpResponse(status=200, headers={"content-type": "application/json"}, body=json.dumps({"version": "2.1", "software": {"name": federation_routes.SOFTWARE_NAME, "version": federation_routes.software_version()}, "protocols": ["activitypub"]}).encode("utf-8"))
        if key == ("GET", "/ap/actors/server"):
            actor_id = f"{origin}/ap/actors/server"
            doc = {"type": "Application", "id": actor_id, "inbox": f"{origin}/ap/inbox", "publicKey": {"id": f"{actor_id}#main-key", "owner": actor_id, "publicKeyPem": _stored_pem(data_dir, account_id=actor_keys.SERVER_ACTOR_ACCOUNT_ID)}}
            return federation_readiness.HttpResponse(status=200, headers={"content-type": "application/activity+json"}, body=json.dumps(doc).encode("utf-8"))
        if method == "GET" and path.startswith("/ap/kb/") and path.count("/") == 3:
            kb_text = path.removeprefix("/ap/kb/")
            kb_number = int(kb_text)
            actor_id = f"{origin}/ap/kb/{kb_text}"
            doc = {
                "type": ["Group", vocabulary.KNOWLEDGE_BASE],
                "id": actor_id,
                "inbox": f"{actor_id}/inbox",
                "followers": f"{actor_id}/followers",
                "outbox": f"{actor_id}/outbox",
                vocabulary.DOCUMENTS: f"{actor_id}/documents",
                vocabulary.ACCEPTS_AUTOMATIC: accepts_automatic,
                "publicKey": {"id": f"{actor_id}#main-key", "owner": actor_id, "publicKeyPem": _stored_pem(data_dir, kb_number=kb_number)},
            }
            return federation_readiness.HttpResponse(status=200, headers={"content-type": "application/activity+json"}, body=json.dumps(doc).encode("utf-8"))
        if method == "GET" and (path in {"/ap/kb", "/ap/themes"} or path.endswith("/documents")):
            return federation_readiness.HttpResponse(status=404, headers={"content-type": "application/json"}, body=b"{}")
        return federation_readiness.HttpResponse(status=404, headers={}, body=b"")

    return requester


def _write_heartbeat(
    data_dir: Path,
    *,
    received_at: float,
    contract_version: str = federation_routes.CONTRACT_VERSION,
    ok: bool | None = True,
    knowledge_self_check: dict[str, Any] | None = None,
    loops: dict[str, Any] | None = None,
) -> None:
    path = data_dir / HEARTBEAT_FILE
    path.parent.mkdir(parents=True, exist_ok=True)
    record = {
        "received_at": received_at,
        "contract_version": contract_version,
        "sidecar_version": "0.1.0",
        "queue_depth": 0,
        "self_check": {"ok": ok, "checked_at": received_at if ok is not None else None},
        "knowledge_self_check": (
            knowledge_self_check
            if knowledge_self_check is not None
            else {"checked_at": received_at, "failed_paths": [], "signed": {"result": "ok"}}
        ),
        "loops": (
            loops
            if loops is not None
            else {
                "knowledge_reads": {"running": False, "progress_at": received_at, "error": None, "error_at": None},
                "knowledge_exchange": {"running": False, "progress_at": received_at, "error": None, "error_at": None},
            }
        ),
    }
    path.write_text(json.dumps(record), encoding="utf-8")


def _prepare(tmp_path: Path, *, heartbeat_at: float, **overrides: Any) -> tuple[_Settings, int]:
    """A network-mode server that passes all the checks: admin, one member, service key, and a heartbeat."""
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


def _report(settings: _Settings, *, now: float = NOW, fetch=None, request=None, request_timeout: float = 20.0) -> dict[str, dict[str, Any]]:
    conn = dbm.connect_existing(settings.db_file)
    try:
        report = federation_readiness.check_federation_readiness(
            conn,
            settings,
            now=now,
            fetch=fetch or _metadata_fetch(settings.public_url.rstrip("/")),
            request=request or _public_request(settings.public_url.rstrip("/"), data_dir=settings.data_dir),
            request_timeout=request_timeout,
        )
    finally:
        conn.close()
    data = report.to_dict()
    assert [item["id"] for item in data["items"]] == ALL_IDS
    assert data["passed"] is all(item["passed"] for item in data["items"] if item["level"] == "required")
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


# --- Checks GF1 to GF17 (module interface and file I/O) ---


def test_all_checks_pass_for_a_ready_server(ready):
    items = _report(ready)
    assert _failed(items) == []
    assert set(items["GF1"]) == {"id", "name", "group", "level", "applies", "passed", "code", "detail", "values"}
    assert (items["GF14"]["applies"], items["GF15"]["applies"], items["GF16"]["applies"]) == (False, False, False)
    assert items["GF17"]["level"] == "warning"


def test_gf1_passes_in_single_mode_only_after_the_first_setup(ready):
    """KNOWLEDGE_FEDERATION_DESIGN.md Section 15 rule 5: federation works in single mode. The admin account must be active and have a USERNAME."""
    _store_call(ready, lambda store: store.set_setting("mode", "single", updated_by=1))
    assert _failed(_report(ready)) == []
    _store_call(ready, lambda store: (store.conn.execute("UPDATE accounts SET status='setup_required' WHERE role='admin'"), store.conn.commit()))
    assert _failed(_report(ready)) == ["GF1"]


def test_gf2_and_gf6_fail_when_there_is_no_heartbeat(ready):
    (ready.data_dir / HEARTBEAT_FILE).unlink()
    assert _failed(_report(ready)) == ["GF2", "GF6", "GF8", "GF9", "GF11"]


def test_gf2_and_gf6_fail_when_the_heartbeat_is_old(ready):
    limit = federation_readiness.HEARTBEAT_MAX_AGE_SECONDS
    _write_heartbeat(ready.data_dir, received_at=NOW - limit)
    assert _failed(_report(ready)) == []
    _write_heartbeat(ready.data_dir, received_at=NOW - limit - 1)
    assert _failed(_report(ready)) == ["GF2", "GF6", "GF8", "GF9"]


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
    origin = "https://claire.example.com"
    settings, _member = _prepare(tmp_path, heartbeat_at=NOW - 10, environment="production", public_url=origin + "/", fqdn="claire.example.com")
    assert _failed(_report(settings, fetch=_metadata_fetch(origin), request=_public_request(origin))) == []


def test_gf5_fails_for_an_http_origin_in_production(tmp_path):
    settings, _member = _prepare(tmp_path, heartbeat_at=NOW - 10, environment="production", public_url="http://claire.example/", fqdn="claire.example")
    items = _report(settings)
    assert items["GF5"]["passed"] is False
    assert "https" in items["GF5"]["detail"]
    # Invalid origin config makes GF5, GF7, GF10, GF13, and GF17 fail.
    assert _failed(items) == ["GF5", "GF7", "GF10", "GF13", "GF17"]


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
    monkeypatch.setattr(federation_readiness, "request_public_origin", _public_request(PUBLIC_URL, data_dir=settings.data_dir))
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
    assert [item["id"] for item in body["readiness"]["items"] if not item["passed"]] == ["GF2", "GF6", "GF8", "GF9", "GF11"]


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


# --- claire server check --federation (CLI output) ---


def _run_cli(capsys, settings: _Settings, monkeypatch, argv: list[str]) -> tuple[int, dict[str, Any]]:
    monkeypatch.setattr("claire.cli.get_settings", lambda: settings)
    monkeypatch.setattr(federation_readiness, "fetch_metadata", _metadata_fetch(settings.public_url.rstrip("/")))
    monkeypatch.setattr(federation_readiness, "request_public_origin", _public_request(settings.public_url.rstrip("/"), data_dir=settings.data_dir))
    code = cli.main(argv)
    return code, json.loads(capsys.readouterr().out)


def test_cli_federation_check_prints_the_report_and_exits_with_0(tmp_path, monkeypatch, capsys):
    settings, _member = _prepare(tmp_path, heartbeat_at=time.time())

    code, output = _run_cli(capsys, settings, monkeypatch, ["server", "check", "--federation", "--json"])

    assert code == 0
    assert (output["status"], output["exit_code"]) == ("success", 0)
    assert output["data"]["check"] == "federation"
    assert output["data"]["passed"] is True
    assert [item["id"] for item in output["data"]["items"]] == ALL_IDS


def test_cli_federation_check_exits_with_1_when_a_check_fails(tmp_path, monkeypatch, capsys):
    settings, _member = _prepare(tmp_path, heartbeat_at=time.time() - 3600)

    code, output = _run_cli(capsys, settings, monkeypatch, ["server", "check", "--federation", "--json"])

    assert code == 1
    assert (output["status"], output["exit_code"]) == ("error", 1)
    assert output["data"]["passed"] is False
    assert [item["id"] for item in output["data"]["items"] if not item["passed"]] == ["GF2", "GF6", "GF8", "GF9", "GF11"]


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
    assert after.json()["heartbeat"]["contract_version"] == federation_routes.CONTRACT_VERSION
    assert _federation(env.settings) == "on"
    assert _audit_rows(env.settings) == [(1, "federation.update", '{"federation": "on"}')]

    again = env.client.request("POST", "/api/admin/federation", session=env.admin, json={"federation": "on"})
    assert again.json() == {"federation": "on", "changed": False}
    assert len(_audit_rows(env.settings)) == 1


# --- Checks GF8 to GF17, trust store, and deadline (KF6) ---


def _enable_federation(settings: _Settings, *, kb_number: int | None = None, auto_on: bool = False) -> None:
    conn = dbm.connect_existing(settings.db_file)
    try:
        store = accounts_store.AccountStore(conn)
        store.set_federation_state("on", updated_by=1)
        if auto_on:
            store.set_setting("kb_auto_contribution", "on", updated_by=1)
        if kb_number is not None:
            conn.execute("INSERT INTO knowledge_bases (seq, label, is_personal, is_public, created_at, updated_at, number, knowledge_type) VALUES (?, 'Shared KB', 0, 1, 1, 1, ?, 'real')", (kb_number, kb_number))
            conn.commit()
    finally:
        conn.close()
    secret = SecretService(key_path=federation_readiness.master_key_path(settings.data_dir))
    auth_conn = auth_db.connect_auth_db(auth_db.auth_db_path(settings.data_dir))
    try:
        actor_keys.ensure_actor_keys(auth_conn, actor_keys.SERVER_ACTOR_ACCOUNT_ID, secret, now=NOW)
        if kb_number is not None:
            actor_keys.ensure_kb_actor_keys(auth_conn, kb_number, secret, now=NOW)
    finally:
        auth_conn.close()


def _write_self_signed_cert(tmp_path: Path) -> tuple[Path, Path]:
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    now = dt.datetime.now(dt.timezone.utc)
    subject = issuer = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "127.0.0.1")])
    cert = (
        x509.CertificateBuilder()
        .subject_name(subject)
        .issuer_name(issuer)
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now - dt.timedelta(minutes=5))
        .not_valid_after(now + dt.timedelta(hours=1))
        .add_extension(x509.SubjectAlternativeName([x509.IPAddress(ipaddress.ip_address("127.0.0.1"))]), critical=False)
        .sign(key, hashes.SHA256())
    )
    cert_path = tmp_path / "cert.pem"
    key_path = tmp_path / "key.pem"
    cert_path.write_bytes(cert.public_bytes(serialization.Encoding.PEM))
    key_path.write_bytes(key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.TraditionalOpenSSL, serialization.NoEncryption()))
    return cert_path, key_path


def test_http_helpers_use_ssl_cert_file_and_enforce_response_size(tmp_path, monkeypatch):
    """Calls to the external library httpx over loopback TLS with SSL_CERT_FILE."""
    cert_path, key_path = _write_self_signed_cert(tmp_path)
    monkeypatch.setenv("SSL_CERT_FILE", str(cert_path))

    class Handler(http.server.BaseHTTPRequestHandler):
        def do_GET(self):  # noqa: N802
            if self.path == "/huge":
                payload = b"a" * (federation_readiness.READINESS_RESPONSE_MAX_BYTES + 10)
            elif self.path == "/redirect":
                self.send_response(302)
                self.send_header("Location", "/other")
                self.send_header("Content-Length", "0")
                self.end_headers()
                return
            else:
                payload = b'{"ok":true}'
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("X-Claire-Fedify-Server", "1")
            self.send_header("Content-Length", str(len(payload)))
            self.end_headers()
            self.wfile.write(payload)

        def log_message(self, *args):
            pass

    httpd = http.server.HTTPServer(("127.0.0.1", 0), Handler)
    server_ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    server_ctx.load_cert_chain(certfile=str(cert_path), keyfile=str(key_path))
    httpd.socket = server_ctx.wrap_socket(httpd.socket, server_side=True)
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    try:
        origin = f"https://127.0.0.1:{httpd.server_address[1]}"
        status, body = federation_readiness.fetch_metadata(f"{origin}/.well-known/oauth-authorization-server")
        assert (status, body) == (200, b'{"ok":true}')

        resp = federation_readiness.request_public_origin("GET", f"{origin}/ok")
        assert resp.status == 200
        assert resp.headers["x-claire-fedify-server"] == "1"
        assert resp.body == b'{"ok":true}'

        redir = federation_readiness.request_public_origin("GET", f"{origin}/redirect")
        assert redir.status == 302

        with pytest.raises(federation_readiness.ResponseTooLargeError):
            federation_readiness.request_public_origin("GET", f"{origin}/huge")
    finally:
        httpd.shutdown()
        httpd.server_close()
        thread.join(timeout=5)


def test_gf8_fails_when_knowledge_paths_are_not_routed_or_pending(ready):
    _write_heartbeat(ready.data_dir, received_at=NOW - 5, knowledge_self_check={"checked_at": NOW - 5, "failed_paths": ["/ap/kb", "/ap/themes"], "signed": {"result": "ok"}})
    items = _report(ready)
    assert _failed(items) == ["GF8"]
    assert items["GF8"]["code"] == "paths_not_routed"
    assert items["GF8"]["values"] == {"paths": "/ap/kb, /ap/themes"}

    _write_heartbeat(ready.data_dir, received_at=NOW - 5, knowledge_self_check={"checked_at": None, "failed_paths": [], "signed": {"result": "ok"}})
    items = _report(ready)
    assert _failed(items) == ["GF8"]
    assert items["GF8"]["code"] == "knowledge_self_check_pending"


@pytest.mark.parametrize(
    ("result", "expected_code"),
    [
        ("not_routed", "signed_not_routed"),
        ("host_changed", "signed_host_changed"),
        ("signature_invalid", "signed_signature_invalid"),
        ("tls_error", "signed_tls_error"),
        ("dns_error", "signed_dns_error"),
        ("HTTP 502", "signed_request_failed"),
    ],
)
def test_gf9_maps_each_signed_self_check_failure_code(ready, result, expected_code):
    _write_heartbeat(ready.data_dir, received_at=NOW - 5, knowledge_self_check={"checked_at": NOW - 5, "failed_paths": [], "signed": {"result": result}})
    items = _report(ready)
    assert _failed(items) == ["GF9"]
    assert items["GF9"]["code"] == expected_code


def test_gf10_sends_max_inbox_body_and_checks_status_and_sidecar_header(ready):
    seen_lengths: list[int] = []

    def inspect_request(method: str, url: str, *, headers: dict[str, str] | None = None, body: bytes | None = None) -> federation_readiness.HttpResponse:
        if method == "POST" and url.endswith("/ap/inbox"):
            assert body is not None
            seen_lengths.append(len(body))
        return _public_request(ready.public_url, data_dir=ready.data_dir)(method, url, headers=headers, body=body)

    assert _failed(_report(ready, request=inspect_request)) == []
    assert seen_lengths == [federation_readiness.GF10_INBOX_BODY_BYTES]

    too_big = _public_request(ready.public_url, overrides={("POST", "/ap/inbox"): federation_readiness.HttpResponse(status=413, headers={"x-claire-fedify-server": "1"}, body=b"")})
    items = _report(ready, request=too_big)
    assert _failed(items) == ["GF10"]
    assert items["GF10"]["code"] == "inbox_body_too_large"

    not_sidecar = _public_request(ready.public_url, overrides={("POST", "/ap/inbox"): federation_readiness.HttpResponse(status=404, headers={}, body=b"")})
    items = _report(ready, request=not_sidecar)
    assert _failed(items) == ["GF10"]
    assert items["GF10"]["code"] == "inbox_not_sidecar"

    conn_err = _public_request(ready.public_url, overrides={("POST", "/ap/inbox"): OSError("refused")})
    items = _report(ready, request=conn_err)
    assert _failed(items) == ["GF10"]
    assert items["GF10"]["code"] == "request_failed"


def test_gf11_checks_knowledge_loops_progress_and_last_error(ready):
    _write_heartbeat(ready.data_dir, received_at=NOW - 5, loops={"knowledge_reads": {"running": False, "progress_at": NOW - 5, "error": None, "error_at": None}})
    items = _report(ready)
    assert _failed(items) == ["GF11"]
    assert items["GF11"]["code"] == "loops_missing"

    limit = federation_readiness.LOOP_MAX_AGE_SECONDS
    _write_heartbeat(
        ready.data_dir,
        received_at=NOW - 5,
        loops={
            "knowledge_reads": {"running": False, "progress_at": NOW - limit, "error": None, "error_at": None},
            "knowledge_exchange": {"running": False, "progress_at": NOW - limit, "error": None, "error_at": None},
        },
    )
    assert _failed(_report(ready)) == []

    _write_heartbeat(
        ready.data_dir,
        received_at=NOW - 5,
        loops={
            "knowledge_reads": {"running": False, "progress_at": NOW - limit, "error": None, "error_at": None},
            "knowledge_exchange": {"running": False, "progress_at": NOW - limit - 1, "error": "ECONNREFUSED", "error_at": NOW - 10},
        },
    )
    items = _report(ready)
    assert _failed(items) == ["GF11"]
    assert items["GF11"]["code"] == "loop_stalled"
    assert "ECONNREFUSED" in items["GF11"]["detail"]


def test_gf12_verifies_actor_key_decryption_and_lists_up_to_five_owners(ready, monkeypatch):
    items = _report(ready)
    assert items["GF12"]["code"] == "actor_keys_empty"

    secret = SecretService(key_path=federation_readiness.master_key_path(ready.data_dir))
    auth_conn = auth_db.connect_auth_db(auth_db.auth_db_path(ready.data_dir))
    try:
        actor_keys.ensure_actor_keys(auth_conn, 0, secret, now=NOW)
        for kb_num in (10, 20, 30, 40, 50, 60):
            actor_keys.ensure_kb_actor_keys(auth_conn, kb_num, secret, now=NOW)
    finally:
        auth_conn.close()

    items = _report(ready)
    assert _failed(items) == []
    assert items["GF12"]["code"] == "actor_keys_ok"
    assert items["GF12"]["values"] == {"count": 14}

    # Rotate the key without re-encrypting stored actor keys: decryption fails and shows at most 5 actor numbers.
    monkeypatch.setenv("CLAIRE_SECRET_KEY", "55" * 32)
    reset_secret_service()
    items = _report(ready)
    assert _failed(items) == ["GF12"]
    assert items["GF12"]["code"] == "actor_keys_decrypt_failed"
    assert items["GF12"]["values"]["count"] == 14
    listed_actors = items["GF12"]["values"]["actors"].split(", ")
    assert len(listed_actors) == 5 and all(int(a) < 0 for a in listed_actors)

    # Corrupt auth.db triggers actor_keys_unreadable.
    auth_db.auth_db_path(ready.data_dir).write_bytes(b"not-sqlite")
    assert _report(ready)["GF12"]["code"] == "actor_keys_unreadable"


@pytest.mark.parametrize(
    ("key", "value"),
    [
        ("kb_auto_contribution", "maybe"),
        ("submission_max_documents", "0"),
        ("submission_max_documents", "many"),
        ("remote_revision_min_seconds", "-1"),
        ("theme_match_threshold", "1.5"),
        ("auto_match_threshold", "nan"),
        ("auto_run_documents", "0"),
    ],
)
def test_gf13_rejects_invalid_knowledge_settings(ready, key, value):
    conn = dbm.connect_existing(ready.db_file)
    try:
        conn.execute("INSERT INTO server_settings (key, value, updated_at) VALUES (?, ?, 0) ON CONFLICT(key) DO UPDATE SET value=excluded.value", (key, value))
        conn.commit()
    finally:
        conn.close()
    items = _report(ready)
    assert _failed(items) == ["GF13"]
    assert items["GF13"]["code"] == "setting_invalid"
    assert items["GF13"]["values"]["key"] == key


@pytest.mark.parametrize("suffix", ["test", "example", "invalid", "localhost", "local"])
def test_gf13_rejects_special_use_domains_in_production_and_allows_test_in_dev(tmp_path, suffix):
    origin = f"https://claire.{suffix}"
    prod, _ = _prepare(tmp_path / f"prod_{suffix}", heartbeat_at=NOW - 10, environment="production", public_url=origin, fqdn=f"claire.{suffix}")
    (tmp_path / f"prod_{suffix}").mkdir(exist_ok=True)
    items = _report(prod, fetch=_metadata_fetch(origin), request=_public_request(origin))
    assert _failed(items) == ["GF13"]
    assert items["GF13"]["code"] == "special_use_host"

    if suffix == "test":
        dev, _ = _prepare(tmp_path / "dev_test", heartbeat_at=NOW - 10, environment="development", public_url=origin, fqdn=f"claire.{suffix}")
        assert _failed(_report(dev, fetch=_metadata_fetch(origin), request=_public_request(origin))) == []


def test_gf14_gf15_gf16_apply_when_federation_is_on_and_detect_faults(ready):
    _enable_federation(ready, kb_number=77, auto_on=False)
    items = _report(ready)
    assert _failed(items) == []
    assert (items["GF14"]["applies"], items["GF15"]["applies"], items["GF16"]["applies"]) == (True, True, True)

    bad_nodeinfo = _public_request(ready.public_url, data_dir=ready.data_dir, overrides={("GET", "/.well-known/nodeinfo"): federation_readiness.HttpResponse(status=404, headers={}, body=b"")})
    assert _report(ready, request=bad_nodeinfo)["GF14"]["code"] == "nodeinfo_failed"

    mismatch_actor_id = f"{ready.public_url}/ap/actors/server"
    wrong_key_doc = {"type": "Application", "id": mismatch_actor_id, "inbox": f"{ready.public_url}/ap/inbox", "publicKey": {"id": f"{mismatch_actor_id}#main-key", "owner": mismatch_actor_id, "publicKeyPem": "-----BEGIN PUBLIC KEY-----\nOTHER\n-----END PUBLIC KEY-----\n"}}
    bad_key = _public_request(ready.public_url, data_dir=ready.data_dir, overrides={("GET", "/ap/actors/server"): federation_readiness.HttpResponse(status=200, headers={"content-type": "application/activity+json"}, body=json.dumps(wrong_key_doc).encode())})
    assert _report(ready, request=bad_key)["GF14"]["code"] == "server_actor_key_mismatch"

    stale_auto = _public_request(ready.public_url, data_dir=ready.data_dir, accepts_automatic=True)
    assert _report(ready, request=stale_auto)["GF15"]["code"] == "kb_actor_accepts_automatic_mismatch"

    bad_kb_type = _public_request(ready.public_url, data_dir=ready.data_dir, overrides={("GET", "/ap/kb/77"): federation_readiness.HttpResponse(status=200, headers={"content-type": "application/json"}, body=b"{}")})
    assert _report(ready, request=bad_kb_type)["GF15"]["code"] == "kb_actor_failed"

    open_collection = _public_request(ready.public_url, data_dir=ready.data_dir, overrides={("GET", "/ap/themes"): federation_readiness.HttpResponse(status=200, headers={"content-type": "application/activity+json"}, body=b"{}")})
    gf16_fail = _report(ready, request=open_collection)["GF16"]
    assert gf16_fail["passed"] is False and gf16_fail["code"] == "collection_not_refused"


def test_gf15_does_not_apply_when_federation_is_on_without_shared_kbs(ready):
    _enable_federation(ready, kb_number=None)
    items = _report(ready)
    assert _failed(items) == []
    assert items["GF15"]["applies"] is False and items["GF15"]["passed"] is True and items["GF15"]["code"] == "no_shared_kb"


def test_gf17_is_a_warning_and_never_fails_the_report(ready):
    no_cors = _public_request(ready.public_url, overrides={("GET", "/ns/v1"): federation_readiness.HttpResponse(status=200, headers={"content-type": vocabulary.CONTEXT_MEDIA_TYPE}, body=vocabulary.context_json().encode())})
    conn = dbm.connect_existing(ready.db_file)
    try:
        report = federation_readiness.check_federation_readiness(conn, ready, now=NOW, fetch=_metadata_fetch(ready.public_url), request=no_cors)
    finally:
        conn.close()
    assert report.passed is True
    by_id = {item.id: item for item in report.items}
    assert by_id["GF17"].level == "warning" and by_id["GF17"].passed is False and by_id["GF17"].code == "ns_context_failed"

    bad_vocab = _public_request(ready.public_url, overrides={("GET", "/ns"): federation_readiness.HttpResponse(status=404, headers={}, body=b"")})
    items = _report(ready, request=bad_vocab)
    assert items["GF17"]["passed"] is False and items["GF17"]["code"] == "ns_vocabulary_failed"


def test_request_checks_enforce_shared_deadline(ready):
    release = threading.Event()

    def slow_request(method: str, url: str, *, headers: dict[str, str] | None = None, body: bytes | None = None) -> federation_readiness.HttpResponse:
        if "/ns" in url:
            release.wait(timeout=5)
        return _public_request(ready.public_url, data_dir=ready.data_dir)(method, url, headers=headers, body=body)

    started = time.monotonic()
    try:
        items = _report(ready, request=slow_request, request_timeout=0.05)
    finally:
        release.set()
    assert time.monotonic() - started < 1.0
    assert items["GF17"]["passed"] is False
    assert items["GF17"]["code"] == "check_timeout"
    assert items["GF17"]["detail"] == "The check did not end in 20 seconds."


def test_pq14_switch_blocks_on_kf_required_failure_but_allows_warning_only(env, monkeypatch, capsys):
    _write_heartbeat(env.settings.data_dir, received_at=time.time(), knowledge_self_check={"checked_at": time.time(), "failed_paths": ["/ap/actors/server"], "signed": {"result": "ok"}})
    blocked = env.client.request("POST", "/api/admin/federation", session=env.admin, json={"federation": "on"})
    assert blocked.status_code == 400
    assert [item["id"] for item in blocked.json()["failures"]] == ["GF8"]

    _write_heartbeat(env.settings.data_dir, received_at=time.time())
    warning_only = _public_request(PUBLIC_URL, data_dir=env.settings.data_dir, overrides={("GET", "/ns/v1"): federation_readiness.HttpResponse(status=302, headers={"location": "/other"}, body=b"")})
    monkeypatch.setattr(federation_readiness, "request_public_origin", warning_only)

    allowed = env.client.request("POST", "/api/admin/federation", session=env.admin, json={"federation": "on"})
    assert allowed.status_code == 200
    assert allowed.json() == {"federation": "on", "changed": True}

    conn = dbm.connect_existing(env.settings.db_file)
    try:
        accounts_store.AccountStore(conn).set_federation_state("off", updated_by=1)
    finally:
        conn.close()
    monkeypatch.setattr("claire.cli.get_settings", lambda: env.settings)
    monkeypatch.setattr(federation_readiness, "fetch_metadata", _metadata_fetch(PUBLIC_URL))
    code = cli.main(["server", "check", "--federation", "--json"])
    assert code == 0
    cli_out = json.loads(capsys.readouterr().out)
    assert cli_out["data"]["passed"] is True
    assert next(i for i in cli_out["data"]["items"] if i["id"] == "GF17")["passed"] is False

    with pytest.raises(SystemExit):
        cli.build_parser().parse_args(["server", "check", "--help"])
    captured = capsys.readouterr().out
    assert "GF1" in captured and "GF17" in captured
