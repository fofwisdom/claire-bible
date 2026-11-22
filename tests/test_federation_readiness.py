"""Federation readiness checks GF1 to GF20, the state route ``GET /api/admin/federation``, and ``claire server check --federation`` (Fedify sidecar design Section 12, unit FP1-6; knowledge federation design Section 14, unit KF6).

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
from claire.federation.knowledge import vocabulary
from claire.store import accounts as accounts_store
from claire.store import actor_keys, auth_db
from claire.store import db as dbm
from claire.store.secrets import SecretService, reset_secret_service

PUBLIC_URL = "http://127.0.0.1:8765"
OWNER_TOKEN = "owner-" + "o" * 32
NOW = 1_800_000_000.0
STALE = 16 * 60.0
ALL_IDS = [f"GF{i}" for i in range(1, 21)]
# The marker of the sidecar in the heartbeat. An answer of the sidecar has it in the header X-Claire-Fedify-Server.
MARKER = "marker-of-this-sidecar"
SIDECAR = {"x-claire-fedify-server": MARKER}
CLAIRE = {"x-claire-server": "api"}


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
        if key == ("GET", "/.well-known/oauth-authorization-server"):
            return federation_readiness.HttpResponse(status=200, headers={"content-type": "application/json", **CLAIRE}, body=json.dumps({"issuer": origin, "token_endpoint": origin + "/oauth/token"}).encode())
        if key == ("POST", "/ap/inbox"):
            return federation_readiness.HttpResponse(status=401, headers={**SIDECAR}, body=b"")
        if key == ("GET", "/ns/v1"):
            return federation_readiness.HttpResponse(status=200, headers={"content-type": vocabulary.CONTEXT_MEDIA_TYPE, "access-control-allow-origin": "*", **CLAIRE}, body=vocabulary.context_json().encode("utf-8"))
        if key == ("GET", "/ns"):
            return federation_readiness.HttpResponse(status=200, headers={"content-type": "text/html; charset=utf-8", **CLAIRE}, body=b"<!doctype html><title>Claire NS</title>")
        if key == ("GET", "/.well-known/nodeinfo"):
            return federation_readiness.HttpResponse(status=200, headers={"content-type": "application/json", **SIDECAR}, body=json.dumps({"links": [{"rel": "http://nodeinfo.diaspora.software/ns/schema/2.1", "href": f"{origin}/nodeinfo/2.1"}]}).encode("utf-8"))
        if key == ("GET", "/nodeinfo/2.1"):
            return federation_readiness.HttpResponse(status=200, headers={"content-type": "application/json", **SIDECAR}, body=json.dumps({"version": "2.1", "software": {"name": federation_routes.SOFTWARE_NAME, "version": federation_routes.software_version()}, "protocols": ["activitypub"]}).encode("utf-8"))
        if key == ("GET", "/ap/actors/server"):
            actor_id = f"{origin}/ap/actors/server"
            doc = {"type": "Application", "id": actor_id, "inbox": f"{origin}/ap/inbox", "publicKey": {"id": f"{actor_id}#main-key", "owner": actor_id, "publicKeyPem": _stored_pem(data_dir, account_id=actor_keys.SERVER_ACTOR_ACCOUNT_ID)}}
            return federation_readiness.HttpResponse(status=200, headers={"content-type": "application/activity+json", **SIDECAR}, body=json.dumps(doc).encode("utf-8"))
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
            return federation_readiness.HttpResponse(status=200, headers={"content-type": "application/activity+json", **SIDECAR}, body=json.dumps(doc).encode("utf-8"))
        if method == "GET" and (path in {"/ap/kb", "/ap/themes"} or path.endswith("/documents")):
            return federation_readiness.HttpResponse(status=404, headers={"content-type": "application/json", **SIDECAR}, body=b"{}")
        return federation_readiness.HttpResponse(status=404, headers={}, body=b"")

    return requester


def _probe_answer(path: str, problem: str = "not_routed", party: str = "claire", **fields: Any) -> dict[str, Any]:
    """One failed self-check request in the heartbeat (contract 1.13)."""
    return {"path": path, "problem": problem, "party": party, "status": 404, "error": None, "via_cdn": None, "server": None, "media_type": None, "mitigation": None, "received": None, **fields}


def _signed(draft_cavage: dict[str, Any] | None = None, rfc9421: dict[str, Any] | None = None) -> dict[str, Any]:
    """The two signed self-checks. An answer record makes the form fail."""
    return {key: {"result": "ok" if answer is None else "failed", "answer": answer} for key, answer in (("draft_cavage", draft_cavage), ("rfc9421", rfc9421))}


def _traffic(verified: int = 0, hosts: int = 0, rejected: int = 0, succeeded: int = 0, **failed: int) -> dict[str, Any]:
    failures = {"dns": 0, "tls": 0, "timeout": 0, "http_4xx": 0, "http_5xx": 0, "other": 0, **failed}
    return {"window_seconds": 86400, "inbound_verified": {"count": verified, "hosts": hosts, "last_at": None}, "inbound_rejected": {"count": rejected, "last_at": None}, "deliveries": {"succeeded": succeeded, "failed": failures, "last_success_at": None}}


def _write_heartbeat(
    data_dir: Path,
    *,
    received_at: float,
    contract_version: str = federation_routes.CONTRACT_VERSION,
    ok: bool | None = True,
    answer: dict[str, Any] | None = None,
    knowledge_self_check: dict[str, Any] | None = None,
    loops: dict[str, Any] | None = None,
    public_host: dict[str, Any] | None = None,
    traffic: dict[str, Any] | None = None,
) -> None:
    path = data_dir / HEARTBEAT_FILE
    path.parent.mkdir(parents=True, exist_ok=True)
    record = {
        "received_at": received_at,
        "contract_version": contract_version,
        "sidecar_version": "0.1.0",
        "queue_depth": 0,
        "marker": MARKER,
        "public_host": public_host if public_host is not None else {"host": "claire.example.com", "addresses": [{"address": "9.9.9.9", "class": "global"}], "error": None},
        "self_check": {"ok": ok, "checked_at": received_at if ok is not None else None, "answer": answer},
        "knowledge_self_check": (
            knowledge_self_check
            if knowledge_self_check is not None
            else {"checked_at": received_at, "failed_paths": [], "answers": [], "signed": _signed()}
        ),
        "loops": (
            loops
            if loops is not None
            else {
                "knowledge_reads": {"running": False, "progress_at": received_at, "error": None, "error_at": None},
                "knowledge_exchange": {"running": False, "progress_at": received_at, "error": None, "error_at": None},
            }
        ),
        # Another server sent one request: the warning GF19 passes on the ready server.
        "traffic": traffic if traffic is not None else _traffic(verified=1, hosts=1),
    }
    path.write_text(json.dumps(record), encoding="utf-8")


def _prepare(tmp_path: Path, *, heartbeat_at: float, **overrides: Any) -> tuple[_Settings, int]:
    """A network-mode server that passes all the checks: admin, one member, service key, the keys of the server actor, and a heartbeat."""
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
    # The sidecar makes the keys of the server actor at its first read. GF14 compares the document with the stored key.
    _seed_actor_keys(settings)
    _write_heartbeat(tmp_path, received_at=heartbeat_at)
    return settings, member


def _seed_actor_keys(settings: _Settings, *, kb_number: int | None = None) -> None:
    secret = SecretService(key_path=federation_readiness.master_key_path(settings.data_dir))
    auth_conn = auth_db.connect_auth_db(auth_db.auth_db_path(settings.data_dir))
    try:
        actor_keys.ensure_actor_keys(auth_conn, actor_keys.SERVER_ACTOR_ACCOUNT_ID, secret, now=NOW)
        if kb_number is not None:
            actor_keys.ensure_kb_actor_keys(auth_conn, kb_number, secret, now=NOW)
    finally:
        auth_conn.close()


def _report(settings: _Settings, *, now: float = NOW, request=None, resolve=None, request_timeout: float = 20.0) -> dict[str, dict[str, Any]]:
    conn = dbm.connect_existing(settings.db_file)
    try:
        report = federation_readiness.check_federation_readiness(
            conn,
            settings,
            now=now,
            request=request or _public_request(settings.public_url.rstrip("/"), data_dir=settings.data_dir),
            resolve=resolve or (lambda host: ["9.9.9.9"]),
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


# --- Checks GF1 to GF20 (module interface and file I/O) ---


def test_all_checks_pass_for_a_ready_server(ready):
    items = _report(ready)
    assert _failed(items) == []
    assert set(items["GF1"]) == {"id", "name", "group", "level", "applies", "passed", "code", "detail", "values"}
    # Federation has no switch: GF14 and GF16 apply on each server. GF15 needs a shared knowledge base.
    assert (items["GF14"]["applies"], items["GF15"]["applies"], items["GF16"]["applies"]) == (True, False, True)
    assert items["GF17"]["level"] == "warning"
    # GF18 applies to production only. GF20 does not apply without a delivery.
    assert [(items[i]["group"], items[i]["level"], items[i]["applies"]) for i in ("GF18", "GF19", "GF20")] == [("operation", "warning", False), ("operation", "warning", True), ("operation", "warning", False)]


def test_gf1_passes_in_single_mode_only_after_the_first_setup(ready):
    """KNOWLEDGE_FEDERATION_DESIGN.md Section 15 rule 5: federation works in single mode. The admin account must be active and have a USERNAME."""
    _store_call(ready, lambda store: store.set_setting("mode", "single", updated_by=1))
    assert _failed(_report(ready)) == []
    _store_call(ready, lambda store: (store.conn.execute("UPDATE accounts SET status='setup_required' WHERE role='admin'"), store.conn.commit()))
    assert _failed(_report(ready)) == ["GF1"]


def test_gf2_and_gf6_fail_when_there_is_no_heartbeat(ready):
    (ready.data_dir / HEARTBEAT_FILE).unlink()
    # Without the marker of the heartbeat, GF10 cannot tell that the answer came from this sidecar.
    items = _report(ready)
    assert _failed(items) == ["GF2", "GF6", "GF8", "GF9", "GF10", "GF11", "GF14", "GF16", "GF19", "GF20"]
    assert (items["GF10"]["code"], items["GF14"]["code"], items["GF19"]["code"]) == ("sidecar_unverified", "sidecar_unverified", "traffic_unknown")


def test_gf2_and_gf6_fail_when_the_heartbeat_is_old(ready):
    limit = federation_readiness.HEARTBEAT_MAX_AGE_SECONDS
    _write_heartbeat(ready.data_dir, received_at=NOW - limit)
    assert _failed(_report(ready)) == []
    _write_heartbeat(ready.data_dir, received_at=NOW - limit - 1)
    assert _failed(_report(ready)) == ["GF2", "GF6", "GF8", "GF9", "GF10", "GF14", "GF16", "GF19", "GF20"]


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
    # Without a master key the keys of the server actor cannot be decrypted (GF12).
    assert _failed(_report(ready)) == ["GF4", "GF12"]
    assert not key_file.exists()


def test_gf4_reads_the_master_key_file(ready, monkeypatch):
    monkeypatch.delenv("CLAIRE_SECRET_KEY")
    key_file = federation_readiness.master_key_path(ready.data_dir)
    key_file.write_bytes(b"m" * 32)
    # The file is a valid master key, but not the key that encrypted the keys of the server actor (GF12).
    assert _failed(_report(ready)) == ["GF12"]
    key_file.write_bytes(b"m" * 8)
    assert _failed(_report(ready)) == ["GF4", "GF12"]
    assert key_file.read_bytes() == b"m" * 8


def test_gf5_passes_for_an_https_origin_in_production(tmp_path):
    origin = "https://claire.example.com"
    settings, _member = _prepare(tmp_path, heartbeat_at=NOW - 10, environment="production", public_url=origin + "/", fqdn="claire.example.com")
    assert _failed(_report(settings, request=_public_request(origin, data_dir=settings.data_dir))) == []


def test_gf5_fails_for_an_http_origin_in_production(tmp_path):
    settings, _member = _prepare(tmp_path, heartbeat_at=NOW - 10, environment="production", public_url="http://claire.example/", fqdn="claire.example")
    items = _report(settings)
    assert items["GF5"]["passed"] is False
    assert "https" in items["GF5"]["detail"]
    # Invalid origin config makes GF5, GF7, GF10, GF13, GF14, GF16, GF17, and GF18 fail.
    assert _failed(items) == ["GF5", "GF7", "GF10", "GF13", "GF14", "GF16", "GF17", "GF18"]


def test_gf6_fails_when_the_public_self_check_did_not_pass_and_tells_who_answered(ready):
    _write_heartbeat(ready.data_dir, received_at=NOW - 10, ok=None)
    items = _report(ready)
    assert _failed(items) == ["GF6"]
    assert items["GF6"]["code"] == "webfinger_self_check_pending"
    # The code comes from the party of the answer of the self-check request (contract 1.13).
    cases = [
        (_probe_answer("/.well-known/webfinger"), "answered_by_claire"),
        (_probe_answer("/.well-known/webfinger", party="cdn_challenge", status=403, via_cdn="cloudflare", mitigation="challenge"), "cdn_challenge"),
        (_probe_answer("/.well-known/webfinger", party="cdn_block", status=403, mitigation="block"), "cdn_block"),
        (_probe_answer("/.well-known/webfinger", party="cdn_error", status=522), "cdn_error"),
        (_probe_answer("/.well-known/webfinger", party="intermediary", status=403, server="waf"), "intermediary"),
        (_probe_answer("/.well-known/webfinger", party="sidecar_other"), "sidecar_other"),
        (_probe_answer("/.well-known/webfinger", party="none", status=None, error="dns"), "dns_error"),
        (_probe_answer("/.well-known/webfinger", party="none", status=None, error="tls"), "tls_error"),
        (_probe_answer("/.well-known/webfinger", party="none", status=None, error="connect"), "connect_error"),
        (_probe_answer("/.well-known/webfinger", party="none", status=None, error="timeout"), "probe_timeout"),
        (_probe_answer("/.well-known/webfinger", problem="path_changed", party="sidecar", received="/webfinger"), "path_changed"),
        (_probe_answer("/.well-known/webfinger", problem="host_changed", party="sidecar", received="10.0.0.5:8790"), "host_changed"),
        (_probe_answer("/.well-known/webfinger", problem="answer_not_live", party="sidecar"), "answer_not_live"),
    ]
    for answer, code in cases:
        _write_heartbeat(ready.data_dir, received_at=NOW - 10, ok=False, answer=answer)
        items = _report(ready)
        assert _failed(items) == ["GF6"]
        assert (items["GF6"]["code"], items["GF6"]["values"]["path"], items["GF6"]["values"]["party"]) == (code, "/.well-known/webfinger", answer["party"])
    assert items["GF6"]["values"]["source"] == "sidecar"


def test_gf7_fails_when_the_public_origin_does_not_return_the_claire_metadata_and_tells_who_answered(ready):
    path = "/.well-known/oauth-authorization-server"

    def report(custom) -> dict[str, Any]:
        return _report(ready, request=_public_request(PUBLIC_URL, data_dir=ready.data_dir, overrides={("GET", path): custom}))["GF7"]

    response = federation_readiness.HttpResponse
    assert report(response(status=404, headers={**CLAIRE}, body=b""))["code"] == "oauth_unexpected_status"
    assert report(response(status=200, headers={**CLAIRE}, body=json.dumps({"issuer": "https://other.example"}).encode()))["code"] == "oauth_metadata_mismatch"
    # A wrong proxy rule sends the path to the sidecar.
    item = report(response(status=404, headers={**SIDECAR}, body=b"Not Found"))
    assert (item["passed"], item["code"], item["values"]["path"]) == (False, "answered_by_sidecar", path)
    # A CDN, another intermediary, and no answer.
    assert report(response(status=403, headers={"cf-mitigated": "challenge", "cf-ray": "1-ICN", "content-type": "text/html"}, body=b"<html>"))["code"] == "cdn_challenge"
    item = report(response(status=200, headers={"server": "waf"}, body=b"<html>login</html>"))
    assert (item["code"], item["values"]["server"], item["values"]["status"]) == ("intermediary", "waf", 200)
    item = report(OSError("connection refused"))
    assert (item["code"], item["values"]["error"]) == ("request_failed", "OSError")
    assert report(ConnectionRefusedError("connection refused"))["code"] == "connect_error"


def test_default_request_does_not_follow_a_redirect_and_sends_the_agent_of_the_checks(ready):
    """Call to the external library httpx. The server is on the loopback address."""
    seen: list[str] = []

    class Handler(http.server.BaseHTTPRequestHandler):
        def do_GET(self):  # noqa: N802
            seen.append(self.headers.get("User-Agent", ""))
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
        response = federation_readiness.request_public_origin("GET", f"http://127.0.0.1:{httpd.server_address[1]}/.well-known/oauth-authorization-server")
        # The checks of Claire send an agent in the form of fediverse software, not the default agent of httpx (FEDERATION_GO_LIVE_DESIGN.md Section 6).
        origin = f"http://127.0.0.1:{httpd.server_address[1]}"
        ready.public_url = origin + "/"
        ready.fqdn = origin.removeprefix("http://")
        item = _report(ready, request=federation_readiness.request_public_origin)["GF7"]
    finally:
        httpd.shutdown()
        httpd.server_close()
        thread.join(timeout=5)
    assert (response.status, response.body) == (302, b"")
    assert item["code"] == "intermediary"
    assert f"{federation_routes.SOFTWARE_NAME}/{federation_routes.software_version()} (+{origin}/)" in seen


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


def _audit_rows(settings: _Settings) -> list[tuple[int, str, str | None]]:
    conn = auth_db.connect_auth_db(auth_db.auth_db_path(settings.data_dir))
    try:
        return [tuple(row) for row in conn.execute("SELECT actor_account_id, action, detail_json FROM admin_audit ORDER BY id").fetchall()]
    finally:
        conn.close()


@pytest.fixture
def env(tmp_path, monkeypatch):
    settings, member = _prepare(tmp_path, heartbeat_at=time.time())
    monkeypatch.setattr(federation_readiness, "request_public_origin", _public_request(PUBLIC_URL, data_dir=settings.data_dir))
    app = server.create_app(settings, SimpleNamespace(provider=SimpleNamespace(name="stub")))
    with TestClient(app, base_url=PUBLIC_URL) as tc:
        yield SimpleNamespace(client=_Client(tc), settings=settings, member=member, admin=_session(settings, 1))


def test_status_shows_the_failed_checks_and_no_heartbeat(env):
    (env.settings.data_dir / HEARTBEAT_FILE).unlink()

    response = env.client.request("GET", "/api/admin/federation", session=env.admin)

    assert response.status_code == 200
    body = response.json()
    # The answer has no state of a switch: federation has no switch.
    assert set(body) == {"mode", "readiness", "heartbeat"}
    assert (body["mode"], body["heartbeat"]) == ("network", None)
    assert body["readiness"]["passed"] is False
    assert [item["id"] for item in body["readiness"]["items"] if not item["passed"]] == ["GF2", "GF6", "GF8", "GF9", "GF10", "GF11", "GF14", "GF16", "GF19", "GF20"]


def test_member_and_anonymous_principal_get_404(env):
    member = _session(env.settings, env.member)
    for session in (member, None):
        response = env.client.request("GET", "/api/admin/federation", session=session)
        assert response.status_code == 404
        assert response.text == "Not Found"


@pytest.mark.parametrize("payload", [{"federation": "off"}, {"federation": "on"}])
def test_there_is_no_route_that_changes_a_federation_switch(env, payload):
    """Federation is a function of each server (FEDERATION_GO_LIVE_DESIGN.md decision GD4). The old route is closed, and a stored value of the old switch has no effect."""
    response = env.client.request("POST", "/api/admin/federation", session=env.admin, json=payload)
    assert response.status_code in (404, 405)
    assert _store_call(env.settings, lambda store: store.get_setting("federation")) is None
    assert _audit_rows(env.settings) == []


# --- claire server check --federation (CLI output) ---


def _run_cli(capsys, settings: _Settings, monkeypatch, argv: list[str]) -> tuple[int, dict[str, Any]]:
    monkeypatch.setattr("claire.cli.get_settings", lambda: settings)
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
    assert [item["id"] for item in output["data"]["items"] if not item["passed"]] == ["GF2", "GF6", "GF8", "GF9", "GF10", "GF11", "GF14", "GF16", "GF19", "GF20"]


# --- End-to-end ---


def test_e2e_admin_reads_the_state_of_a_server_that_federates_without_a_switch(env):
    state = env.client.request("GET", "/api/admin/federation", session=env.admin)
    assert state.status_code == 200
    body = state.json()
    assert body["mode"] == "network" and "federation" not in body
    assert body["readiness"]["passed"] is True
    assert [item["id"] for item in body["readiness"]["items"]] == ALL_IDS
    # The checks of the documents apply at once: there is no switch to turn on first.
    assert [(item["id"], item["applies"], item["passed"]) for item in body["readiness"]["items"] if item["id"] in ("GF14", "GF16")] == [("GF14", True, True), ("GF16", True, True)]
    assert body["heartbeat"]["self_check"]["ok"] is True
    assert body["heartbeat"]["contract_version"] == federation_routes.CONTRACT_VERSION
    # A value that an older version stored for the switch is not read.
    _store_call(env.settings, lambda store: store.set_setting("federation", "off", updated_by=1))
    again = env.client.request("GET", "/api/admin/federation", session=env.admin).json()
    assert again["readiness"]["passed"] is True and "federation" not in again
    assert _audit_rows(env.settings) == []


# --- Checks GF8 to GF20, trust store, and deadline (KF6, GL1, GL2) ---


def _add_shared_kb(settings: _Settings, *, kb_number: int, auto_on: bool = False) -> None:
    """Add a public shared knowledge base with the keys of its actor."""
    conn = dbm.connect_existing(settings.db_file)
    try:
        store = accounts_store.AccountStore(conn)
        if auto_on:
            store.set_setting("kb_auto_contribution", "on", updated_by=1)
        conn.execute("INSERT INTO knowledge_bases (seq, label, is_personal, is_public, created_at, updated_at, number, knowledge_type) VALUES (?, 'Shared KB', 0, 1, 1, 1, ?, 'real')", (kb_number, kb_number))
        conn.commit()
    finally:
        conn.close()
    _seed_actor_keys(settings, kb_number=kb_number)


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


def test_gf8_fails_when_knowledge_paths_are_not_routed_or_pending_and_tells_who_answered(ready):
    answers = [_probe_answer("/ap/kb", party="intermediary", status=403, server="waf"), _probe_answer("/ap/themes")]
    _write_heartbeat(ready.data_dir, received_at=NOW - 5, knowledge_self_check={"checked_at": NOW - 5, "failed_paths": ["/ap/kb", "/ap/themes"], "answers": answers, "signed": _signed()})
    items = _report(ready)
    assert _failed(items) == ["GF8"]
    # The code comes from the first failed path. The values have all failed paths.
    assert items["GF8"]["code"] == "intermediary"
    assert items["GF8"]["values"] == {"path": "/ap/kb", "party": "intermediary", "source": "sidecar", "status": 403, "server": "waf", "paths": "/ap/kb, /ap/themes"}
    assert "/ap/kb, /ap/themes" in items["GF8"]["detail"]

    # A reverse proxy that removes the prefix /ap still reaches the sidecar: the path that the sidecar got shows the fault.
    stripped = [_probe_answer("/ap/actors/server", problem="path_changed", party="sidecar", received="/actors/server")]
    _write_heartbeat(ready.data_dir, received_at=NOW - 5, knowledge_self_check={"checked_at": NOW - 5, "failed_paths": ["/ap/actors/server"], "answers": stripped, "signed": _signed()})
    items = _report(ready)
    assert (items["GF8"]["code"], items["GF8"]["values"]["received"]) == ("path_changed", "/actors/server")

    _write_heartbeat(ready.data_dir, received_at=NOW - 5, knowledge_self_check={"checked_at": None, "failed_paths": [], "answers": [], "signed": _signed()})
    items = _report(ready)
    assert _failed(items) == ["GF8"]
    assert items["GF8"]["code"] == "knowledge_self_check_pending"


@pytest.mark.parametrize(
    ("answer", "expected_code"),
    [
        (_probe_answer("/ap/self-check"), "answered_by_claire"),
        (_probe_answer("/ap/self-check", problem="host_changed", party="sidecar", received="10.0.0.5:8790"), "host_changed"),
        (_probe_answer("/ap/self-check", problem="signature_invalid", party="sidecar"), "signed_signature_invalid"),
        (_probe_answer("/ap/self-check", party="none", status=None, error="tls"), "tls_error"),
        (_probe_answer("/ap/self-check", party="none", status=None, error="dns"), "dns_error"),
        (_probe_answer("/ap/self-check", party="intermediary", status=403, server="waf"), "intermediary"),
        (_probe_answer("/ap/self-check", party="cdn_challenge", status=403, mitigation="challenge"), "cdn_challenge"),
    ],
)
def test_gf9_needs_the_two_signature_forms_and_maps_each_failure(ready, answer, expected_code):
    # Both forms fail.
    _write_heartbeat(ready.data_dir, received_at=NOW - 5, knowledge_self_check={"checked_at": NOW - 5, "failed_paths": [], "answers": [], "signed": _signed(answer, answer)})
    items = _report(ready)
    assert _failed(items) == ["GF9"]
    assert (items["GF9"]["code"], items["GF9"]["values"]["forms"]) == (expected_code, "draft-cavage, RFC 9421")
    # One form fails: a server that sends that form cannot deliver.
    _write_heartbeat(ready.data_dir, received_at=NOW - 5, knowledge_self_check={"checked_at": NOW - 5, "failed_paths": [], "answers": [], "signed": _signed(None, answer)})
    items = _report(ready)
    assert _failed(items) == ["GF9"]
    assert (items["GF9"]["code"], items["GF9"]["values"]["forms"]) == (expected_code, "RFC 9421")
    # A form without a result: the self-check was not done.
    pending = _signed()
    pending["rfc9421"]["result"] = None
    _write_heartbeat(ready.data_dir, received_at=NOW - 5, knowledge_self_check={"checked_at": NOW - 5, "failed_paths": [], "answers": [], "signed": pending})
    assert _report(ready)["GF9"]["code"] == "knowledge_self_check_pending"


def test_gf10_sends_max_inbox_body_and_the_two_media_types_and_needs_this_sidecar(ready):
    seen: list[tuple[int, str, str]] = []

    def inspect_request(method: str, url: str, *, headers: dict[str, str] | None = None, body: bytes | None = None) -> federation_readiness.HttpResponse:
        if method == "POST" and url.endswith("/ap/inbox"):
            assert body is not None and headers is not None
            seen.append((len(body), headers["Content-Type"], headers["User-Agent"]))
        return _public_request(ready.public_url, data_dir=ready.data_dir)(method, url, headers=headers, body=body)

    assert _failed(_report(ready, request=inspect_request)) == []
    agent = f"{federation_routes.SOFTWARE_NAME}/{federation_routes.software_version()} (+{PUBLIC_URL}/)"
    assert seen == [(federation_readiness.GF10_INBOX_BODY_BYTES, "application/activity+json", agent), (15, 'application/ld+json; profile="https://www.w3.org/ns/activitystreams"', agent)]

    def report(custom) -> dict[str, Any]:
        items = _report(ready, request=_public_request(ready.public_url, data_dir=ready.data_dir, overrides={("POST", "/ap/inbox"): custom}))
        assert _failed(items) == ["GF10"]
        return items["GF10"]

    response = federation_readiness.HttpResponse
    assert report(response(status=413, headers={**SIDECAR}, body=b""))["code"] == "inbox_body_too_large"
    assert report(response(status=413, headers={"server": "waf"}, body=b""))["code"] == "inbox_body_too_large"
    assert report(response(status=404, headers={**CLAIRE}, body=b""))["code"] == "answered_by_claire"
    # A WAF that refuses the media type of a delivery.
    item = report(response(status=403, headers={"server": "waf"}, body=b"blocked"))
    assert (item["code"], item["values"]["media_type"], item["values"]["server"]) == ("intermediary", "application/activity+json", "waf")
    # The marker of another sidecar process is not the marker of the heartbeat.
    assert report(response(status=401, headers={"x-claire-fedify-server": "another"}, body=b""))["code"] == "sidecar_other"
    assert report(OSError("refused"))["code"] == "request_failed"

    # A WAF that refuses only the profile parameter of the second media type.
    def profile_blocked(method: str, url: str, *, headers: dict[str, str] | None = None, body: bytes | None = None) -> federation_readiness.HttpResponse:
        if method == "POST" and headers is not None and "profile=" in headers["Content-Type"]:
            return response(status=403, headers={"cf-mitigated": "block", "cf-ray": "1-ICN"}, body=b"")
        return _public_request(ready.public_url, data_dir=ready.data_dir)(method, url, headers=headers, body=body)

    item = _report(ready, request=profile_blocked)["GF10"]
    assert (item["code"], item["values"]["media_type"], item["values"]["value"]) == ("cdn_block", 'application/ld+json; profile="https://www.w3.org/ns/activitystreams"', "block")


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
    # A server without an actor key (the sidecar did not read the server actor yet).
    auth_conn = auth_db.connect_auth_db(auth_db.auth_db_path(ready.data_dir))
    try:
        auth_conn.execute("DELETE FROM actor_keys")
        auth_conn.commit()
    finally:
        auth_conn.close()
    assert _report(ready)["GF12"]["code"] == "actor_keys_empty"

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
    items = _report(prod, request=_public_request(origin, data_dir=prod.data_dir))
    assert _failed(items) == ["GF13"]
    assert items["GF13"]["code"] == "special_use_host"

    if suffix == "test":
        dev, _ = _prepare(tmp_path / "dev_test", heartbeat_at=NOW - 10, environment="development", public_url=origin, fqdn=f"claire.{suffix}")
        assert _failed(_report(dev, request=_public_request(origin, data_dir=dev.data_dir))) == []


def test_gf14_gf15_gf16_detect_faults(ready):
    _add_shared_kb(ready, kb_number=77)
    items = _report(ready)
    assert _failed(items) == []
    assert (items["GF14"]["applies"], items["GF15"]["applies"], items["GF16"]["applies"]) == (True, True, True)

    bad_nodeinfo = _public_request(ready.public_url, data_dir=ready.data_dir, overrides={("GET", "/.well-known/nodeinfo"): federation_readiness.HttpResponse(status=404, headers={**SIDECAR}, body=b"")})
    assert _report(ready, request=bad_nodeinfo)["GF14"]["code"] == "nodeinfo_failed"
    # A proxy that sends /nodeinfo/ to Claire.
    to_claire = _public_request(ready.public_url, data_dir=ready.data_dir, overrides={("GET", "/nodeinfo/2.1"): federation_readiness.HttpResponse(status=404, headers={**CLAIRE}, body=b"")})
    gf14 = _report(ready, request=to_claire)["GF14"]
    assert (gf14["code"], gf14["values"]["path"]) == ("answered_by_claire", "/nodeinfo/2.1")

    mismatch_actor_id = f"{ready.public_url}/ap/actors/server"
    wrong_key_doc = {"type": "Application", "id": mismatch_actor_id, "inbox": f"{ready.public_url}/ap/inbox", "publicKey": {"id": f"{mismatch_actor_id}#main-key", "owner": mismatch_actor_id, "publicKeyPem": "-----BEGIN PUBLIC KEY-----\nOTHER\n-----END PUBLIC KEY-----\n"}}
    bad_key = _public_request(ready.public_url, data_dir=ready.data_dir, overrides={("GET", "/ap/actors/server"): federation_readiness.HttpResponse(status=200, headers={"content-type": "application/activity+json", **SIDECAR}, body=json.dumps(wrong_key_doc).encode())})
    assert _report(ready, request=bad_key)["GF14"]["code"] == "server_actor_key_mismatch"

    stale_auto = _public_request(ready.public_url, data_dir=ready.data_dir, accepts_automatic=True)
    assert _report(ready, request=stale_auto)["GF15"]["code"] == "kb_actor_accepts_automatic_mismatch"

    bad_kb_type = _public_request(ready.public_url, data_dir=ready.data_dir, overrides={("GET", "/ap/kb/77"): federation_readiness.HttpResponse(status=200, headers={"content-type": "application/json", **SIDECAR}, body=b"{}")})
    assert _report(ready, request=bad_kb_type)["GF15"]["code"] == "kb_actor_failed"

    open_collection = _public_request(ready.public_url, data_dir=ready.data_dir, overrides={("GET", "/ap/themes"): federation_readiness.HttpResponse(status=200, headers={"content-type": "application/activity+json", **SIDECAR}, body=b"{}")})
    gf16_fail = _report(ready, request=open_collection)["GF16"]
    assert gf16_fail["passed"] is False and gf16_fail["code"] == "collection_not_refused"
    # A 404 of Claire does not show that the sidecar refuses the read.
    claire_404 = _public_request(ready.public_url, data_dir=ready.data_dir, overrides={("GET", "/ap/kb"): federation_readiness.HttpResponse(status=404, headers={**CLAIRE}, body=b"")})
    assert _report(ready, request=claire_404)["GF16"]["code"] == "answered_by_claire"

    # The server actor must come for each of the two media types of a read.
    accepts: list[str] = []

    def record_accept(method: str, url: str, *, headers: dict[str, str] | None = None, body: bytes | None = None) -> federation_readiness.HttpResponse:
        if url.endswith("/ap/actors/server") and headers is not None:
            accepts.append(headers["Accept"])
        return _public_request(ready.public_url, data_dir=ready.data_dir)(method, url, headers=headers, body=body)

    assert _failed(_report(ready, request=record_accept)) == []
    assert accepts == ["application/activity+json", 'application/ld+json; profile="https://www.w3.org/ns/activitystreams"']


def test_gf15_does_not_apply_without_shared_kbs(ready):
    items = _report(ready)
    assert _failed(items) == []
    assert items["GF15"]["applies"] is False and items["GF15"]["passed"] is True and items["GF15"]["code"] == "no_shared_kb"


def test_gf17_is_a_warning_and_never_fails_the_report(ready):
    no_cors = _public_request(ready.public_url, data_dir=ready.data_dir, overrides={("GET", "/ns/v1"): federation_readiness.HttpResponse(status=200, headers={"content-type": vocabulary.CONTEXT_MEDIA_TYPE, **CLAIRE}, body=vocabulary.context_json().encode())})
    conn = dbm.connect_existing(ready.db_file)
    try:
        report = federation_readiness.check_federation_readiness(conn, ready, now=NOW, request=no_cors)
    finally:
        conn.close()
    assert report.passed is True
    by_id = {item.id: item for item in report.items}
    assert by_id["GF17"].level == "warning" and by_id["GF17"].passed is False and by_id["GF17"].code == "ns_context_failed"

    bad_vocab = _public_request(ready.public_url, data_dir=ready.data_dir, overrides={("GET", "/ns"): federation_readiness.HttpResponse(status=404, headers={**CLAIRE}, body=b"")})
    items = _report(ready, request=bad_vocab)
    assert items["GF17"]["passed"] is False and items["GF17"]["code"] == "ns_vocabulary_failed"


def test_gf18_tells_when_the_checks_do_not_take_the_path_of_other_servers(tmp_path):
    origin = "https://claire.example.com"
    settings, _member = _prepare(tmp_path, heartbeat_at=NOW - 10, environment="production", public_url=origin + "/", fqdn="claire.example.com")
    hosts: list[str] = []

    def resolve(host: str) -> list[str]:
        hosts.append(host)
        return ["104.16.0.1"]

    # The answers of the request checks show the CDN, and the address of Claire is an address of that CDN.
    def through_cdn(method: str, url: str, *, headers: dict[str, str] | None = None, body: bytes | None = None) -> federation_readiness.HttpResponse:
        response = _public_request(origin, data_dir=settings.data_dir)(method, url, headers=headers, body=body)
        return federation_readiness.HttpResponse(status=response.status, headers={**response.headers, "cf-ray": "1-ICN"}, body=response.body)

    items = _report(settings, request=through_cdn, resolve=resolve)
    assert _failed(items) == []
    assert hosts == ["claire.example.com"]
    assert (items["GF18"]["applies"], items["GF18"]["code"], items["GF18"]["values"]) == (True, "public_host_global", {"host": "claire.example.com", "addresses": "104.16.0.1 (cloudflare), 9.9.9.9 (global)", "via_cdn": "cloudflare"})

    # A split DNS view: the sidecar gets a LAN address. The warning does not change the report result.
    _write_heartbeat(settings.data_dir, received_at=NOW - 10, public_host={"host": "claire.example.com", "addresses": [{"address": "192.168.10.5", "class": "private"}], "error": None})
    items = _report(settings, request=_public_request(origin, data_dir=settings.data_dir))
    assert _failed(items) == ["GF18"]
    assert (items["GF18"]["level"], items["GF18"]["code"], items["GF18"]["values"]) == ("warning", "public_host_private", {"host": "claire.example.com", "address": "192.168.10.5", "source": "sidecar"})
    assert "ops/federation_outside_probe.sh" in items["GF18"]["detail"]

    _write_heartbeat(settings.data_dir, received_at=NOW - 10)
    items = _report(settings, request=_public_request(origin, data_dir=settings.data_dir), resolve=lambda host: ["127.0.0.1"])
    assert (items["GF18"]["code"], items["GF18"]["values"]["source"]) == ("public_host_private", "Claire")

    def no_name(host: str) -> list[str]:
        raise OSError("name not known")

    assert _report(settings, request=_public_request(origin, data_dir=settings.data_dir), resolve=no_name)["GF18"]["code"] == "public_host_unresolved"


def test_default_resolver_gives_the_addresses_of_a_host():
    """Call to the system resolver. The name is an address, so no DNS request is made."""
    assert federation_readiness.resolve_host("127.0.0.1") == ["127.0.0.1"]


def test_gf19_and_gf20_read_the_traffic_counters(ready):
    # No request of another server and no delivery: GF19 is a failed warning, GF20 does not apply.
    _write_heartbeat(ready.data_dir, received_at=NOW - 10, traffic=_traffic())
    items = _report(ready)
    assert _failed(items) == ["GF19"]
    assert (items["GF19"]["level"], items["GF19"]["code"], items["GF19"]["values"]) == ("warning", "no_inbound", {"host": "127.0.0.1:8765", "rejected": 0})
    assert (items["GF20"]["applies"], items["GF20"]["code"]) == (False, "no_deliveries")

    _write_heartbeat(ready.data_dir, received_at=NOW - 10, traffic=_traffic(verified=5, hosts=2, rejected=1, succeeded=3, timeout=1))
    items = _report(ready)
    assert _failed(items) == []
    assert (items["GF19"]["code"], items["GF19"]["values"]) == ("inbound_ok", {"count": 5, "hosts": 2, "rejected": 1})
    assert (items["GF20"]["code"], items["GF20"]["values"]) == ("deliveries_ok", {"succeeded": 3, "failed": 1})

    _write_heartbeat(ready.data_dir, received_at=NOW - 10, traffic=_traffic(verified=1, hosts=1, http_4xx=4, dns=1))
    items = _report(ready)
    assert _failed(items) == ["GF20"]
    assert (items["GF20"]["level"], items["GF20"]["code"], items["GF20"]["values"]) == ("warning", "deliveries_failed", {"failed": 5, "class": "http_4xx"})

    # The counters are in the memory of the sidecar: without a recent heartbeat they are not known.
    limit = federation_readiness.HEARTBEAT_MAX_AGE_SECONDS
    _write_heartbeat(ready.data_dir, received_at=NOW - limit - 1, traffic=_traffic(verified=5, hosts=2, succeeded=3))
    items = _report(ready)
    assert (items["GF19"]["code"], items["GF20"]["code"]) == ("traffic_unknown", "traffic_unknown")


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


def test_a_failed_warning_does_not_change_the_result_of_the_report_or_the_exit_code(env, monkeypatch, capsys):
    warning_only = _public_request(PUBLIC_URL, data_dir=env.settings.data_dir, overrides={("GET", "/ns/v1"): federation_readiness.HttpResponse(status=302, headers={"location": "/other", **CLAIRE}, body=b"")})
    monkeypatch.setattr(federation_readiness, "request_public_origin", warning_only)
    monkeypatch.setattr("claire.cli.get_settings", lambda: env.settings)
    code = cli.main(["server", "check", "--federation", "--json"])
    assert code == 0
    cli_out = json.loads(capsys.readouterr().out)
    assert cli_out["data"]["passed"] is True
    assert next(i for i in cli_out["data"]["items"] if i["id"] == "GF17")["passed"] is False

    with pytest.raises(SystemExit):
        cli.build_parser().parse_args(["server", "check", "--help"])
    captured = capsys.readouterr().out
    assert "GF1" in captured and "GF20" in captured
