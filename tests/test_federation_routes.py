"""Internal federation API endpoints against the shared contract fixtures (Fedify sidecar design Sections 6.2 and 6.3, units FP1-3 and FP2-2).

The sidecar tests read the same files in ``tests/fixtures/federation_contract/``.
"""

from __future__ import annotations

import json
import os
import secrets
import socket
import subprocess
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from types import SimpleNamespace

import httpx
import pytest
from starlette.testclient import TestClient

from claire.api import federation_routes, server
from claire.api.federation_internal import INTERNAL_PREFIX, SIGNATURE_HEADER, TIMESTAMP_HEADER, create_internal_federation_app, ensure_service_key, load_service_key, service_key_path, sign_request
from claire.store import accounts as accounts_store
from claire.store import actor_keys
from claire.store import auth_db
from claire.store import db as dbm
from claire.store import federation as federation_store
from claire.store.secrets import get_secret_service, reset_secret_service

FIXTURES = Path(__file__).parent / "fixtures" / "federation_contract"
NOW = 1_800_000_400
ALICE_PUBLIC_ID = 116412345678901234
ALICE_IRI = f"https://claire.example.com/ap/actors/{ALICE_PUBLIC_ID}"
BOB_IRI = "https://remote.example/users/bob"
INBOX_EVENT_FIXTURES = ["inbox_event_follow", "inbox_event_undo_follow", "inbox_event_delete_actor", "inbox_event_update_actor", "inbox_event_blocked_domain", "inbox_event_not_an_actor", "inbox_event_ignored"]
FP2_FIXTURES = ["followers", "followers_next", "followers_not_found", "domain_blocks", *INBOX_EVENT_FIXTURES]
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
    """A network-mode instance with federation on. ``alice`` is an actor and has three followers. The other accounts are not actors. One domain is blocked."""
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
        for item in reversed(fixture("followers")["response"]["items"] + fixture("followers_next")["response"]["items"]):
            conn.execute("INSERT INTO federation_followers (public_id, account_id, actor_iri, inbox_iri, shared_inbox_iri, created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?)", (int(item["id"]), alice, item["actor_iri"], item["inbox_iri"], item["shared_inbox_iri"], 1800000300.0, 1800000300.0))
        federation_store.add_domain_block(conn, "blocked.example", created_by=1, now=1800000300.0)
        conn.commit()
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


@pytest.mark.parametrize("name", ["config", "actor_by_username", "actor_by_username_not_found", "actor", "actor_not_found", "actor_keys_not_found", "heartbeat_version_mismatch", *FP2_FIXTURES])
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
    covered = {"config", "heartbeat", "heartbeat_version_mismatch", "actor_by_username", "actor_by_username_not_found", "actor", "actor_not_found", "actor_keys", "actor_keys_not_found", "instance_actor_keys", *FP2_FIXTURES}
    assert {p.stem for p in FIXTURES.glob("*.json")} == covered
    for path in FIXTURES.glob("*.json"):
        assert set(json.loads(path.read_text())) == {"request", "status", "response"}


def test_response_models_are_sealed():
    models = {"config": federation_routes.ConfigResponse, "actor": federation_routes.ActorRecord, "heartbeat": federation_routes.HeartbeatResponse, "actor_keys": federation_routes.ActorKeysResponse, "followers": federation_routes.FollowersResponse, "domain_blocks": federation_routes.DomainBlocksResponse, "inbox_event_follow": federation_routes.InboxEventResponse}
    for name, model in models.items():
        body = fixture(name)["response"]
        model.model_validate(body)
        with pytest.raises(ValueError):
            model.model_validate({**body, "extra_field": 1})


@pytest.mark.parametrize(
    "body",
    [
        b"",
        b"[]",
        b'{"contract_version": "1.1"}',
        b'{"contract_version": "1.1", "sidecar_version": "0.1.0", "queue_depth": "3", "self_check": {"ok": true, "checked_at": 1.0}}',
        b'{"contract_version": "1.1", "sidecar_version": "0.1.0", "queue_depth": 3, "self_check": {"ok": true, "checked_at": 1.0}, "extra": 1}',
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
    for path in (f"/actors/by-username/{username}", f"/actors/{public_id}", f"/actors/{public_id}/keys", f"/actors/{public_id}/followers"):
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
    for path in ("/actors/by-username/alice", f"/actors/{ALICE_PUBLIC_ID}", f"/actors/{ALICE_PUBLIC_ID}/keys", f"/actors/{ALICE_PUBLIC_ID}/followers", "/actors/instance/keys"):
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
    for name in ("config", "heartbeat", "actor_by_username", "actor", *FP2_FIXTURES):
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


# --- Inbox events (design Section 6.3, unit FP2-2) ---


def _event(type_: str = "Follow", **changes) -> dict:
    """The body of the ``Follow`` fixture with changes. ``actor`` replaces fields of the actor."""
    body = json.loads(json.dumps(fixture("inbox_event_follow")["request"]["body"]))
    body["type"] = type_
    body["actor"].update(changes.pop("actor", {}))
    body.update(changes)
    return body


def _post_event(internal: _Internal, body: dict):
    return internal.call({"method": "POST", "path": f"{INTERNAL_PREFIX}/inbox-events", "body": body})


def _follower_rows(internal: _Internal) -> list[tuple]:
    conn = dbm.connect_existing(internal.settings.db_file, readonly=True)
    try:
        return [tuple(r) for r in conn.execute("SELECT account_id, actor_iri, inbox_iri, shared_inbox_iri FROM federation_followers ORDER BY public_id")]
    finally:
        conn.close()


def test_repeated_follow_makes_one_row_and_undo_removes_it(internal):
    before = _follower_rows(internal)
    first = _post_event(internal, _event())
    second = _post_event(internal, _event(event_id="https://remote.example/activities/retry"))
    assert first.json() == second.json() == {"decision": "accepted", "reply": "accept", "reason": None}
    assert sorted(_follower_rows(internal)) == sorted([*before, (internal.alice, BOB_IRI, f"{BOB_IRI}/inbox", "https://remote.example/inbox")])
    assert internal.get(f"/actors/{ALICE_PUBLIC_ID}/followers").json()["total"] == 4
    # The number of the new row has the time of the event. The rows of the fixture have larger numbers.
    made = internal.get(f"/actors/{ALICE_PUBLIC_ID}/followers").json()["items"][-1]
    assert made["actor_iri"] == BOB_IRI and int(made["id"]) >> 16 == NOW * 1000

    for _ in range(2):
        assert _post_event(internal, _event("Undo", object_type="Follow")).json() == {"decision": "accepted", "reply": None, "reason": None}
    assert _follower_rows(internal) == before


def test_event_from_a_blocked_domain_is_rejected(internal):
    before = _follower_rows(internal)
    blocked = {"decision": "rejected", "reply": None, "reason": "blocked_domain"}
    assert _post_event(internal, _event(actor={"iri": "https://blocked.example/users/eve"})).json() == blocked
    # The block also applies to the inbox addresses, and to all event types.
    assert _post_event(internal, _event(actor={"inbox": "https://sub.blocked.example/inbox"})).json() == blocked
    assert _post_event(internal, _event(actor={"shared_inbox": "https://BLOCKED.example/inbox"})).json() == blocked
    assert _post_event(internal, _event("Like", actor={"iri": "https://blocked.example/users/eve"})).json() == blocked
    assert _follower_rows(internal) == before

    # A block that is added later stops an Update from an actor that is a follower.
    conn = dbm.connect_existing(internal.settings.db_file)
    try:
        federation_store.add_domain_block(conn, "remote.example", created_by=1, now=float(NOW))
        conn.commit()
    finally:
        conn.close()
    u1 = "https://remote.example/users/u1"
    assert _post_event(internal, _event("Update", actor={"iri": u1, "inbox": "https://remote.example/moved"}, object_iri=u1, recipient_public_id=None)).json() == blocked
    assert _follower_rows(internal) == before


@pytest.mark.parametrize(
    "changes",
    [
        {"type_": "Like"},
        {"type_": "Announce"},
        {"type_": "Create", "object_type": "Note", "object_iri": "https://remote.example/notes/1"},
        {"type_": "Flag"},
        {"type_": "Accept", "object_type": "Follow"},
        {"type_": "Undo", "object_type": "Like"},
        {"type_": "Undo", "object_type": None},
        {"type_": "Delete", "object_iri": "https://remote.example/notes/1"},
        {"type_": "Delete", "object_iri": None},
        {"type_": "Update", "object_iri": "https://remote.example/notes/1"},
        {"type_": "follow"},
    ],
)
def test_event_type_outside_the_v1_set_is_ignored(internal, changes):
    before = _follower_rows(internal)
    assert _post_event(internal, _event(**changes)).json() == {"decision": "ignored", "reply": None, "reason": "unsupported_type"}
    assert _follower_rows(internal) == before


def test_delete_and_update_of_a_remote_actor_apply_to_all_accounts(internal):
    u1 = "https://remote.example/users/u1"
    conn = dbm.connect_existing(internal.settings.db_file)
    try:
        admin_row, _made = federation_store.upsert_follower(conn, account_id=1, actor_iri=u1, inbox_iri=f"{u1}/inbox", shared_inbox_iri=None, salt=accounts_store.AccountStore(conn).id_salt(), now=float(NOW))
        conn.commit()
    finally:
        conn.close()

    update = _event("Update", actor={"iri": u1, "inbox": "https://remote.example/moved/u1", "shared_inbox": None}, object_iri=u1, object_type="Person", recipient_public_id=None)
    assert _post_event(internal, update).json() == {"decision": "accepted", "reply": None, "reason": None}
    assert sorted(row for row in _follower_rows(internal) if row[1] == u1) == [(1, u1, "https://remote.example/moved/u1", None), (internal.alice, u1, "https://remote.example/moved/u1", None)]

    delete = _event("Delete", actor={"iri": u1, "inbox": f"{u1}/inbox"}, object_iri=u1, object_type=None, recipient_public_id=None)
    for _ in range(2):
        assert _post_event(internal, delete).json() == {"decision": "accepted", "reply": None, "reason": None}
    rows = _follower_rows(internal)
    assert len(rows) == 2 and all(row[1] != u1 for row in rows)
    assert admin_row.public_id > 0


@pytest.mark.parametrize(
    "changes, reason",
    [
        ({"actor": {"iri": "http://remote.example/users/bob"}}, "invalid_actor"),
        ({"actor": {"inbox": "http://remote.example/users/bob/inbox"}}, "invalid_actor"),
        ({"actor": {"iri": "https://claire.example.com/ap/actors/216412345678901234"}}, "invalid_actor"),
        ({"actor": {"shared_inbox": "https://CLAIRE.example.com/ap/inbox"}}, "invalid_actor"),
        ({"actor": {"iri": "https://user:secret@remote.example/users/bob"}}, "invalid_actor"),
        ({"actor": {"iri": "acct:bob@remote.example"}}, "invalid_actor"),
        ({"actor": {"inbox": "https:///inbox"}}, "invalid_actor"),
        ({"object_iri": None}, "not_an_actor"),
        ({"object_iri": "https://other.example/ap/actors/116412345678901234"}, "not_an_actor"),
        ({"object_iri": f"{ALICE_IRI}/"}, "not_an_actor"),
        ({"object_iri": "https://claire.example.com/ap/actors/instance", "recipient_public_id": None}, "not_an_actor"),
        ({"object_iri": "https://claire.example.com/ap/actors/316412345678901234", "recipient_public_id": None}, "not_an_actor"),
        ({"object_iri": "https://claire.example.com/ap/actors/999999999999999999", "recipient_public_id": None}, "not_an_actor"),
        ({"recipient_public_id": "216412345678901234"}, "recipient_mismatch"),
    ],
)
def test_follow_that_policy_refuses_is_rejected_and_stores_nothing(internal, changes, reason):
    before = _follower_rows(internal)
    assert _post_event(internal, _event(**changes)).json() == {"decision": "rejected", "reply": None, "reason": reason}
    assert _follower_rows(internal) == before


def test_follow_through_the_shared_inbox_has_no_recipient(internal):
    assert _post_event(internal, _event(recipient_public_id=None)).json()["reply"] == "accept"
    assert (internal.alice, BOB_IRI, f"{BOB_IRI}/inbox", "https://remote.example/inbox") in _follower_rows(internal)


@pytest.mark.parametrize("state", [{"federation": "off"}, {"mode": "single"}])
def test_no_event_is_applied_when_federation_is_not_active(internal, state):
    before = _follower_rows(internal)
    internal.set_state(**state)
    for body in (_event(), _event("Undo", object_type="Follow", actor={"iri": "https://remote.example/users/u1"}), _event("Like")):
        assert _post_event(internal, body).json() == {"decision": "rejected", "reply": None, "reason": "federation_off"}
    assert _follower_rows(internal) == before


def test_undo_follow_applies_to_a_paused_account(internal):
    _post_event(internal, _event())
    conn = dbm.connect_existing(internal.settings.db_file)
    try:
        conn.execute("UPDATE accounts SET profile_public=0 WHERE id=?", (internal.alice,))
        conn.commit()
    finally:
        conn.close()
    assert _post_event(internal, _event()).json()["reason"] == "not_an_actor"
    assert _post_event(internal, _event("Undo", object_type="Follow")).json()["decision"] == "accepted"
    assert all(row[1] != BOB_IRI for row in _follower_rows(internal))


@pytest.mark.parametrize(
    "change",
    [
        {"extra": 1},
        {"verified_by": "none"},
        {"recipient_public_id": "0116412345678901234"},
        {"recipient_public_id": 116412345678901234},
        {"type": ""},
        {"event_id": "x" * 2049},
        {"actor": {"iri": BOB_IRI, "inbox": f"{BOB_IRI}/inbox"}},
        {"actor": {"iri": BOB_IRI, "inbox": f"{BOB_IRI}/inbox", "shared_inbox": None, "name": "Bob"}},
        {"object_iri": ""},
    ],
)
def test_inbox_event_outside_the_model_is_refused(internal, change):
    before = _follower_rows(internal)
    body = {**fixture("inbox_event_follow")["request"]["body"], **change}
    assert _post_event(internal, body).status_code == 400
    absent = dict(fixture("inbox_event_follow")["request"]["body"])
    del absent["object_type"]
    assert _post_event(internal, absent).status_code == 400
    assert _follower_rows(internal) == before


# --- Followers and domain blocks (design Section 6.2, unit FP2-2) ---


def test_follower_pages_have_inbox_addresses_only_and_refuse_a_bad_cursor(internal):
    page = internal.get(f"/actors/{ALICE_PUBLIC_ID}/followers").json()
    assert (page["total"], len(page["items"]), page["next_max_id"]) == (3, 3, None)
    assert all(set(item) == {"id", "actor_iri", "inbox_iri", "shared_inbox_iri"} for item in page["items"])
    oldest = page["items"][-1]["id"]
    assert [i["id"] for i in internal.get(f"/actors/{ALICE_PUBLIC_ID}/followers?min_id={oldest}&limit=1").json()["items"]] == [page["items"][1]["id"]]
    assert [i["id"] for i in internal.get(f"/actors/{ALICE_PUBLIC_ID}/followers?since_id={oldest}&limit=1").json()["items"]] == [page["items"][0]["id"]]
    for query in ("max_id=abc", "limit=0", "limit=81", f"max_id={oldest}&min_id={oldest}", "max_id=01"):
        response = internal.get(f"/actors/{ALICE_PUBLIC_ID}/followers?{query}")
        assert (response.status_code, response.json()) == (400, {"error": "invalid_request"}), query


def test_domain_blocks_have_an_entity_tag_that_config_repeats(internal):
    first = internal.get("/domain-blocks")
    etag = first.headers["etag"]
    assert etag == first.json()["etag"] == internal.get("/config").json()["domain_blocks_etag"]

    path = f"{INTERNAL_PREFIX}/domain-blocks"
    ts = str(NOW)
    signed = {TIMESTAMP_HEADER: ts, SIGNATURE_HEADER: sign_request(internal.key, "GET", path, ts, b"")}
    unchanged = internal.client.get(path, headers={**signed, "If-None-Match": etag})
    assert (unchanged.status_code, unchanged.content, unchanged.headers["etag"]) == (304, b"", etag)
    assert internal.client.get(path, headers={**signed, "If-None-Match": '"other"'}).status_code == 200

    conn = dbm.connect_existing(internal.settings.db_file)
    try:
        federation_store.add_domain_block(conn, "Worse.Example", created_by=1, now=float(NOW))
        conn.commit()
    finally:
        conn.close()
    changed = internal.client.get(path, headers={**signed, "If-None-Match": etag})
    assert changed.status_code == 200 and changed.json()["domains"] == ["blocked.example", "worse.example"]
    assert changed.json()["etag"] == internal.get("/config").json()["domain_blocks_etag"] != etag

    # The list does not depend on the federation switch. The sidecar can load it before the switch is on.
    internal.set_state(federation="off")
    assert internal.get("/domain-blocks").json() == changed.json()


# --- End to end: a real api process on the internal socket (unit FP2-2) ---


def test_e2e_follow_events_through_the_internal_socket(tmp_path):
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        port = probe.getsockname()[1]
    data_dir = tmp_path / "data"
    origin = f"http://127.0.0.1:{port}"
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
    conn = dbm.connect_existing(data_dir / "claire.db")
    try:
        store = accounts_store.AccountStore(conn)
        store.complete_setup(1, username="admin_kim", display_name="Admin Kim")
        store.set_setting("mode", "network", updated_by=1)
        store.set_federation_state("on", updated_by=1)
        _add_account(conn, ALICE_PUBLIC_ID, "alice")
    finally:
        conn.close()
    key = load_service_key(service_key_path(data_dir))
    from claire.api.federation_internal import federation_socket_path

    sock_path = federation_socket_path(data_dir)
    # The development origin uses http. Thus a remote address with http is permitted here.
    remote = "http://127.0.0.1:9"
    alice_iri = f"{origin}/ap/actors/{ALICE_PUBLIC_ID}"

    proc = subprocess.Popen([sys.executable, "-m", "claire.cli", "serve-api"], cwd=tmp_path, env=env, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
    try:
        deadline = time.monotonic() + 60
        while True:
            if proc.poll() is not None:
                raise AssertionError(f"serve-api exited early: {proc.returncode}\n{proc.stdout.read()}")
            try:
                if httpx.get(f"{origin}/health", timeout=2).status_code == 200 and sock_path.exists():
                    break
            except httpx.HTTPError:
                pass
            assert time.monotonic() < deadline, "serve-api did not start in time"
            time.sleep(0.2)

        with httpx.Client(transport=httpx.HTTPTransport(uds=str(sock_path)), base_url="http://internal") as client:

            def call(method: str, path: str, body: dict | None = None) -> httpx.Response:
                target = f"{INTERNAL_PREFIX}{path}"
                content = b"" if body is None else json.dumps(body).encode()
                ts = str(int(time.time()))
                return client.request(method, target, content=content, headers={TIMESTAMP_HEADER: ts, SIGNATURE_HEADER: sign_request(key, method, target, ts, content)})

            def event(type_: str, user: str, **changes) -> dict:
                actor = f"{remote}/users/{user}"
                return {"event_id": f"{remote}/activities/{secrets.token_hex(4)}", "type": type_, "object_type": None, "recipient_public_id": str(ALICE_PUBLIC_ID), "actor": {"iri": actor, "inbox": f"{actor}/inbox", "shared_inbox": None}, "object_iri": alice_iri, "verified_by": "http-signature", **changes}

            assert call("POST", "/inbox-events", event("Follow", "bob")).json() == {"decision": "accepted", "reply": "accept", "reason": None}
            assert call("POST", "/inbox-events", event("Follow", "bob")).json()["reply"] == "accept"
            assert call("POST", "/inbox-events", event("Follow", "dan")).json()["reply"] == "accept"
            page = call("GET", f"/actors/{ALICE_PUBLIC_ID}/followers?limit=1").json()
            assert (page["total"], [i["actor_iri"] for i in page["items"]]) == (2, [f"{remote}/users/dan"])
            rest = call("GET", f"/actors/{ALICE_PUBLIC_ID}/followers?limit=1&max_id={page['next_max_id']}").json()
            assert [i["inbox_iri"] for i in rest["items"]] == [f"{remote}/users/bob/inbox"]

            assert call("POST", "/inbox-events", event("Like", "bob")).json()["decision"] == "ignored"
            # An address on the origin of this instance is not a remote actor.
            assert call("POST", "/inbox-events", event("Follow", "x", actor={"iri": f"{origin}/users/x", "inbox": f"{origin}/users/x/inbox", "shared_inbox": None})).json()["reason"] == "invalid_actor"

            # A block from a different connection (the admin API of a later unit) has an effect on the next event.
            before = call("GET", "/config").json()["domain_blocks_etag"]
            conn = dbm.connect_existing(data_dir / "claire.db")
            try:
                federation_store.add_domain_block(conn, "127.0.0.1", created_by=1, now=time.time())
                conn.commit()
            finally:
                conn.close()
            blocks = call("GET", "/domain-blocks").json()
            assert blocks["domains"] == ["127.0.0.1"] and blocks["etag"] == call("GET", "/config").json()["domain_blocks_etag"] != before
            assert call("POST", "/inbox-events", event("Follow", "eve")).json() == {"decision": "rejected", "reply": None, "reason": "blocked_domain"}

            conn = dbm.connect_existing(data_dir / "claire.db")
            try:
                federation_store.remove_domain_block(conn, "127.0.0.1")
                conn.commit()
            finally:
                conn.close()
            assert call("POST", "/inbox-events", event("Undo", "bob", object_type="Follow")).json()["decision"] == "accepted"
            assert call("GET", f"/actors/{ALICE_PUBLIC_ID}/followers").json()["total"] == 1

        # The public listener has no inbox-events path.
        assert httpx.post(f"{origin}{INTERNAL_PREFIX}/inbox-events", json=event("Follow", "mallory"), timeout=5).status_code == 404
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=15)
        except subprocess.TimeoutExpired:
            proc.kill()
            proc.wait()


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
