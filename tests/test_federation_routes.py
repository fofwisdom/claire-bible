"""Internal federation API endpoints against the shared contract fixtures (Fedify sidecar design Section 6.2, unit FP1-3).

The sidecar tests read the same files in ``tests/fixtures/federation_contract/``.
"""

from __future__ import annotations

import json
import os
import time
from dataclasses import dataclass
from pathlib import Path
from types import SimpleNamespace

import pytest
from starlette.testclient import TestClient

from claire.api import federation_routes, server
from claire.api.federation_internal import INTERNAL_PREFIX, SIGNATURE_HEADER, TIMESTAMP_HEADER, create_internal_federation_app, ensure_service_key, load_service_key, service_key_path, sign_request
from claire.store import accounts as accounts_store
from claire.store import actor_keys
from claire.store import auth_db
from claire.store import db as dbm
from claire.store.secrets import get_secret_service, reset_secret_service

FIXTURES = Path(__file__).parent / "fixtures" / "federation_contract"
NOW = 1_800_000_400
ALICE_PUBLIC_ID = 116412345678901234
CANARY_ISS = "https://idp-canary.example/issuer"
CANARY_SUB = "canary-subject-7f3a"
CANARY_PROVIDER = "canary-provider-91c2"


@dataclass
class _Settings:
    db_file: Path
    data_dir: Path
    environment: str = "production"
    public_url: str = "https://claire.example.com/"
    inject_token: str = "owner-" + "o" * 32
    readonly_token: str = "readonly-" + "r" * 32
    cors_allowed_origins: str = ""
    anonymous_readonly: bool = False
    fqdn: str = "claire.example.com"
    multi_theme: bool = True
    trusted_proxies: str = "10.0.0.0/8,fd00::/8"
    telemetry_retention_days: int = 30


def fixture(name: str) -> dict:
    return json.loads((FIXTURES / f"{name}.json").read_text(encoding="utf-8"))


@pytest.fixture(autouse=True)
def _isolate(monkeypatch):
    for key in list(os.environ):
        if key.startswith(("CLAIRE_", "CB_")):
            monkeypatch.delenv(key, raising=False)
    monkeypatch.setenv("CLAIRE_SECRET_KEY", "33" * 32)
    monkeypatch.setattr(federation_routes, "software_version", lambda: fixture("config")["response"]["software"]["version"])
    reset_secret_service()
    yield
    reset_secret_service()


def _add_account(conn, public_id: int, username: str, *, status: str = "active", profile_public: int = 1, display_name: str = "") -> int:
    cur = conn.execute(
        "INSERT INTO accounts (public_id, username, role, status, display_name, profile_public, created_at, activated_at, updated_at) VALUES (?, ?, 'member', ?, ?, ?, 1800000000, 1800000100.5, 1800000200.25)",
        (public_id, username, status, display_name, profile_public),
    )
    conn.commit()
    return int(cur.lastrowid)


class _Internal:
    """A signed client for the internal sub-application."""

    def __init__(self, settings: _Settings) -> None:
        self.settings = settings
        ensure_service_key(service_key_path(settings.data_dir))
        self.key = load_service_key(service_key_path(settings.data_dir))
        routes = federation_routes.federation_internal_routes(settings, now=lambda: float(NOW))
        self.client = TestClient(create_internal_federation_app(settings, routes=routes, now=lambda: float(NOW)))
        self.bodies: list[str] = []

    def call(self, request: dict):
        body = json.dumps(request["body"]).encode() if "body" in request else b""
        ts = str(NOW)
        headers = {TIMESTAMP_HEADER: ts, SIGNATURE_HEADER: sign_request(self.key, request["method"], request["path"], ts, body)}
        response = self.client.request(request["method"], request["path"], content=body, headers=headers)
        self.bodies.append(response.text)
        return response

    def get(self, path: str):
        return self.call({"method": "GET", "path": f"{INTERNAL_PREFIX}{path}"})

    def assert_fixture(self, name: str) -> None:
        fx = fixture(name)
        response = self.call(fx["request"])
        assert response.status_code == fx["status"], name
        assert response.json() == fx["response"], name
        assert response.headers["cache-control"] == "no-store"

    def store(self) -> accounts_store.AccountStore:
        return accounts_store.AccountStore(dbm.connect_existing(self.settings.db_file))

    def set_state(self, *, mode: str = "network", federation: str = "on") -> None:
        store = self.store()
        try:
            store.set_setting("mode", mode, updated_by=1)
            store.set_federation_state(federation, updated_by=1)
        finally:
            store.conn.close()


@pytest.fixture
def internal(tmp_path):
    """A network-mode instance with federation on. ``alice`` is an actor. The other accounts are not."""
    settings = _Settings(db_file=tmp_path / "claire.db", data_dir=tmp_path)
    conn = dbm.connect(settings.db_file)
    try:
        dbm.init_db(conn)
        store = accounts_store.AccountStore(conn)
        store.complete_setup(1, username="admin_kim", display_name="Admin Kim")
        store.set_setting("mode", "network", updated_by=1)
        store.set_federation_state("on", updated_by=1)
        alice = _add_account(conn, ALICE_PUBLIC_ID, "alice", display_name="Alice Kim")
        _add_account(conn, 216412345678901234, "private_bob", profile_public=0)
        _add_account(conn, 316412345678901234, "suspended_carol", status="suspended")
        _add_account(conn, 416412345678901234, "deleting_dave", status="deleting")
    finally:
        conn.close()
    auth = auth_db.connect_auth_db(auth_db.auth_db_path(tmp_path))
    try:
        # External identity canary values (design Section 8.2 item 1).
        auth_db.link_account_identity(auth, iss=CANARY_ISS, sub=CANARY_SUB, account_id=alice, provider_id=CANARY_PROVIDER)
    finally:
        auth.close()
    client = _Internal(settings)
    client.alice = alice
    return client


def _store_fixture_keys(internal: _Internal, account_id: int, name: str) -> None:
    svc = get_secret_service(internal.settings.data_dir / "auth" / "master.key")
    auth = auth_db.connect_auth_db(auth_db.auth_db_path(internal.settings.data_dir))
    try:
        for key in fixture(name)["response"]["keys"]:
            sealed = svc.encrypt(json.dumps(key["private_jwk"]), f"actor_key|{account_id}|{key['alg']}")
            auth.execute("INSERT INTO actor_keys VALUES (?, ?, ?, ?, ?, ?, ?)", (account_id, key["alg"], json.dumps(key["public_jwk"]), sealed.ciphertext, sealed.nonce, sealed.key_version, float(NOW)))
        auth.commit()
    finally:
        auth.close()


# --- Instance setting (I/O) ---


def test_federation_setting_default_and_values(tmp_path):
    conn = dbm.connect(tmp_path / "claire.db")
    try:
        dbm.init_db(conn)
        store = accounts_store.AccountStore(conn)
        assert store.federation_state() == "off"
        store.set_federation_state("on", updated_by=1)
        assert store.federation_state() == "on"
        with pytest.raises(ValueError):
            store.set_federation_state("maybe", updated_by=1)  # type: ignore[arg-type]
        store.set_setting("federation", "maybe", updated_by=1)
        with pytest.raises(RuntimeError):
            store.federation_state()
    finally:
        conn.close()


# --- Each endpoint agrees with its fixture (module interface) ---


@pytest.mark.parametrize("name", ["config", "actor_by_username", "actor_by_username_not_found", "actor", "actor_not_found", "actor_keys_not_found", "heartbeat_version_mismatch"])
def test_endpoint_agrees_with_fixture(internal, name):
    internal.assert_fixture(name)


def test_heartbeat_agrees_with_fixture_and_is_recorded(internal):
    assert federation_routes.read_heartbeat(internal.settings.data_dir) is None
    internal.assert_fixture("heartbeat")
    record = federation_routes.read_heartbeat(internal.settings.data_dir)
    assert record == {"received_at": float(NOW), **fixture("heartbeat")["request"]["body"]}

    # A sidecar with a different major version does not replace the record.
    internal.assert_fixture("heartbeat_version_mismatch")
    assert federation_routes.read_heartbeat(internal.settings.data_dir) == record


def test_keys_agree_with_fixtures(internal):
    _store_fixture_keys(internal, internal.alice, "actor_keys")
    _store_fixture_keys(internal, actor_keys.INSTANCE_ACTOR_ACCOUNT_ID, "instance_actor_keys")
    internal.assert_fixture("actor_keys")
    internal.assert_fixture("instance_actor_keys")


def test_fixture_files_all_have_a_test(internal):
    covered = {"config", "heartbeat", "heartbeat_version_mismatch", "actor_by_username", "actor_by_username_not_found", "actor", "actor_not_found", "actor_keys", "actor_keys_not_found", "instance_actor_keys"}
    assert {p.stem for p in FIXTURES.glob("*.json")} == covered
    for path in FIXTURES.glob("*.json"):
        assert set(json.loads(path.read_text())) == {"request", "status", "response"}


def test_response_models_are_sealed():
    for name in ("config", "actor", "heartbeat", "actor_keys"):
        model = {"config": federation_routes.ConfigResponse, "actor": federation_routes.ActorRecord, "heartbeat": federation_routes.HeartbeatResponse, "actor_keys": federation_routes.ActorKeysResponse}[name]
        body = fixture(name)["response"]
        model.model_validate(body)
        with pytest.raises(ValueError):
            model.model_validate({**body, "extra_field": 1})


@pytest.mark.parametrize(
    "body",
    [
        b"",
        b"[]",
        b'{"contract_version": "1.0"}',
        b'{"contract_version": "1.0", "sidecar_version": "0.1.0", "queue_depth": "3", "self_check": {"ok": true, "checked_at": 1.0}}',
        b'{"contract_version": "1.0", "sidecar_version": "0.1.0", "queue_depth": 3, "self_check": {"ok": true, "checked_at": 1.0}, "extra": 1}',
        b'{"contract_version": "one", "sidecar_version": "0.1.0", "queue_depth": 3, "self_check": {"ok": true, "checked_at": 1.0}}',
    ],
)
def test_heartbeat_refuses_a_request_outside_the_model(internal, body):
    path = f"{INTERNAL_PREFIX}/heartbeat"
    ts = str(NOW)
    response = internal.client.post(path, content=body, headers={TIMESTAMP_HEADER: ts, SIGNATURE_HEADER: sign_request(internal.key, "POST", path, ts, body)})
    assert response.status_code == 400
    assert federation_routes.read_heartbeat(internal.settings.data_dir) is None


# --- Actor rule FK4: all states that are not federated give the same 404 ---


@pytest.mark.parametrize("username, public_id", [("private_bob", 216412345678901234), ("suspended_carol", 316412345678901234), ("deleting_dave", 416412345678901234), ("nobody", 999999999999999999)])
def test_non_federated_account_gets_the_same_404(internal, username, public_id):
    reference = internal.get("/actors/by-username/no_such_account")
    assert reference.status_code == 404
    for path in (f"/actors/by-username/{username}", f"/actors/{public_id}", f"/actors/{public_id}/keys"):
        response = internal.get(path)
        assert (response.status_code, response.content) == (404, reference.content), path
    auth = auth_db.connect_auth_db(auth_db.auth_db_path(internal.settings.data_dir))
    try:
        assert auth.execute("SELECT COUNT(*) FROM actor_keys").fetchone()[0] == 0
    finally:
        auth.close()


@pytest.mark.parametrize("state", [{"federation": "off"}, {"mode": "single"}])
def test_no_actor_when_federation_is_off_or_the_mode_is_single(internal, state):
    internal.set_state(**state)
    for path in ("/actors/by-username/alice", f"/actors/{ALICE_PUBLIC_ID}", f"/actors/{ALICE_PUBLIC_ID}/keys", "/actors/instance/keys"):
        assert internal.get(path).status_code == 404, path
    # The config endpoint stays available. The sidecar reads the state from it.
    config = internal.get("/config").json()
    assert (config["mode"], config["federation"]) == (state.get("mode", "network"), state.get("federation", "on"))
    assert config["federated_actors"] == 0
    assert "alice" not in "".join(internal.bodies)


@pytest.mark.parametrize("raw", ["0", "01", "+1", "-1", "1e3", "abc", "%EF%BC%91%EF%BC%92", "9" * 20, "Instance"])
def test_invalid_public_id_is_404(internal, raw):
    assert internal.get(f"/actors/{raw}").status_code == 404
    assert internal.get(f"/actors/{raw}/keys").status_code == 404


def test_username_lookup_ignores_case_and_instance_is_not_an_actor_record(internal):
    assert internal.get("/actors/by-username/ALICE").json()["public_id"] == str(ALICE_PUBLIC_ID)
    assert internal.get("/actors/instance").status_code == 404


# --- Keys ---


def test_keys_are_generated_one_time_and_decrypt_for_the_actor_only(internal):
    first = internal.get(f"/actors/{ALICE_PUBLIC_ID}/keys")
    assert first.status_code == 200
    assert [k["alg"] for k in first.json()["keys"]] == ["rsa2048", "ed25519"]
    assert internal.get(f"/actors/{ALICE_PUBLIC_ID}/keys").json() == first.json()

    instance = internal.get("/actors/instance/keys").json()
    assert instance["public_id"] == "instance"
    assert instance["keys"] != first.json()["keys"]
    auth = auth_db.connect_auth_db(auth_db.auth_db_path(internal.settings.data_dir))
    try:
        owners = [tuple(r) for r in auth.execute("SELECT account_id, alg FROM actor_keys ORDER BY account_id, alg")]
    finally:
        auth.close()
    assert owners == [(0, "ed25519"), (0, "rsa2048"), (internal.alice, "ed25519"), (internal.alice, "rsa2048")]


# --- No auth.db identity field in a response (design Section 8.2 item 1) ---


def test_no_identity_field_in_any_response(internal):
    for name in ("config", "heartbeat", "actor_by_username", "actor"):
        internal.call(fixture(name)["request"])
    internal.get(f"/actors/{ALICE_PUBLIC_ID}/keys")
    internal.get("/actors/instance/keys")
    blob = "".join(internal.bodies)
    for canary in (CANARY_ISS, CANARY_SUB, CANARY_PROVIDER, "idp-canary"):
        assert canary not in blob
    record = internal.get(f"/actors/{ALICE_PUBLIC_ID}").json()
    assert set(record) == {"public_id", "username", "display_name", "activated_at", "updated_at", "profile_url"}
    # The internal account ID and the instance role are not in the record.
    assert internal.alice not in record.values() and "member" not in record.values()


# --- Mode change (design Section 8.1) and wiring ---


def test_route_table_is_wired_into_the_application(tmp_path):
    settings = _Settings(db_file=tmp_path / "claire.db", data_dir=tmp_path, environment="development", public_url="http://127.0.0.1:8765", fqdn="127.0.0.1:8765", trusted_proxies="")
    conn = dbm.connect(settings.db_file)
    try:
        dbm.init_db(conn)
    finally:
        conn.close()
    ensure_service_key(service_key_path(tmp_path))
    key = load_service_key(service_key_path(tmp_path))
    app = server.create_app(settings, SimpleNamespace(provider=SimpleNamespace(name="stub")))
    path = f"{INTERNAL_PREFIX}/config"
    ts = str(int(time.time()))
    headers = {TIMESTAMP_HEADER: ts, SIGNATURE_HEADER: sign_request(key, "GET", path, ts, b"")}

    async def call(server_value):
        messages = []
        scope = {"type": "http", "method": "GET", "path": path, "raw_path": path.encode(), "query_string": b"", "headers": [(k.encode(), v.encode()) for k, v in {**headers, "host": "127.0.0.1:8765"}.items()], "server": server_value, "client": None, "scheme": "http", "http_version": "1.1"}

        async def receive():
            return {"type": "http.request", "body": b"", "more_body": False}

        async def send(message):
            messages.append(message)

        await app(scope, receive, send)
        return messages[0]["status"], b"".join(m.get("body", b"") for m in messages[1:])

    import asyncio

    from claire.api.federation_internal import federation_socket_path

    status, body = asyncio.run(call((str(federation_socket_path(tmp_path)), None)))
    assert status == 200
    assert json.loads(body)["mode"] == "single" and json.loads(body)["federation"] == "off"
    status, _body = asyncio.run(call(("127.0.0.1", 8765)))
    assert status == 404
