"""Notifications and Web Push (PWA_NOTIFICATION_DESIGN.md).

- External library (``cryptography``): the message encryption gives the example of RFC 8291 Section 5; the VAPID token has a correct ES256 signature.
- I/O: the tables of ``client.db`` version 9 (grouping, queue, subscriptions, purge, migration from version 8) and the VAPID key pair in ``auth.db``.
- Module interfaces: the sender results, the routes of the account, the page ``/account/notifications``, and one event for each kind.

No test sends a request to a push service. All state is in ``tmp_path``.
"""

from __future__ import annotations

import asyncio
import json
import os
import sqlite3
import time
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import httpx
import pytest
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.hazmat.primitives.asymmetric.utils import encode_dss_signature
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from starlette.testclient import TestClient

from tests.kb_paths import step_up_headers

from claire import notifications
from claire.api.server import create_app
from claire.core import ids
from claire.core.config import Settings
from claire.notifications import events, sender, webpush
from claire.store import accounts as accounts_store
from claire.store import auth_db, client_db, kb_collab
from claire.store import db as dbm
from claire.store import notifications as store
from claire.store.knowledge_base import get_kb_manager
from claire.store.secrets import SecretService, reset_secret_service
from claire.store.token_store import get_token_store, reset_token_stores

PUBLIC_URL = "http://127.0.0.1:8765"
OWNER_TOKEN = "owner-" + ("n" * 32)
ACCOUNTS = {"admin": 1, "ann": 2, "ben": 3, "cho": 4}

# RFC 8291 Section 5 and Appendix A.
RFC_PLAINTEXT = b"When I grow up, I want to be a watermelon"
RFC_AS_PRIVATE = "yfWPiYE-n46HLnH0KqZOF1fJJU3MYrct3AELtAQ-oRw"
RFC_AS_PUBLIC = "BP4z9KsN6nGRTbVYI_c7VJSPQTBtkgcy27mlmlMoZIIgDll6e3vCYLocInmYWAmS6TlzAC8wEqKK6PBru3jl7A8"
RFC_UA_PRIVATE = "q1dXpw3UpT5VOmu_cf_v6ih07Aems3njxI-JWgLcM94"
RFC_UA_PUBLIC = "BCVxsr7N_eNgVRqvHtD0zTZsEc6-VV-JvLexhqUzORcxaOzi6-AYWXvTBHm4bjyPjs7Vd8pZGH6SRpkNtoIAiw4"
RFC_AUTH = "BTBZMqHH6r4Tts7J_aSIgg"
RFC_SALT = "DGv6ra1nlYgDCS1FRnbzlw"
RFC_BODY_START = "DGv6ra1nlYgDCS1FRnbzlwAAEABBBP4z9KsN6nGRTbVYI_c7VJSPQTBtkgcy27ml"
RFC_CEK = "oIhVW04MRdy2XN9CiKLxTg"
RFC_NONCE = "4h_95klXJ5E_qnoN"

ENDPOINT = "https://push.example.net/send/abc"


@pytest.fixture(autouse=True)
def _isolate(monkeypatch: pytest.MonkeyPatch):
    for key in list(os.environ):
        if key.startswith("CLAIRE_"):
            monkeypatch.delenv(key, raising=False)
    monkeypatch.setenv("CLAIRE_SECRET_KEY", "33" * 32)
    reset_secret_service()
    reset_token_stores()
    yield
    reset_secret_service()
    reset_token_stores()


# --- Web Push protocol (external library) ---------------------------------------------------------------------------


def test_encryption_gives_the_rfc_8291_example():
    body = webpush.encrypt_payload(RFC_PLAINTEXT, p256dh=RFC_UA_PUBLIC, auth=RFC_AUTH, salt=webpush.b64u_decode(RFC_SALT), sender_key=webpush.load_vapid_private_key(RFC_AS_PRIVATE))
    assert webpush.b64u_encode(body).startswith(RFC_BODY_START)
    assert webpush.vapid_public_key(RFC_AS_PRIVATE) == RFC_AS_PUBLIC
    # The receiver decrypts the record with the key and the nonce of the RFC (Appendix A): header 86 bytes, then one record with the delimiter 0x02.
    assert body[:16] == webpush.b64u_decode(RFC_SALT) and int.from_bytes(body[16:20], "big") == 4096 and body[20] == 65
    assert AESGCM(webpush.b64u_decode(RFC_CEK)).decrypt(webpush.b64u_decode(RFC_NONCE), body[86:], None) == RFC_PLAINTEXT + b"\x02"


def test_encryption_refuses_bad_keys_and_long_payloads():
    with pytest.raises(webpush.WebPushError):
        webpush.encrypt_payload(b"x" * (webpush.MAX_PLAINTEXT_BYTES + 1), p256dh=RFC_UA_PUBLIC, auth=RFC_AUTH)
    with pytest.raises(webpush.WebPushError):
        webpush.check_subscription_keys(webpush.b64u_encode(b"\x04" + b"\x00" * 64), RFC_AUTH)
    with pytest.raises(webpush.WebPushError):
        webpush.check_subscription_keys(RFC_UA_PUBLIC, webpush.b64u_encode(b"short"))
    assert len(webpush.encrypt_payload(b"x" * webpush.MAX_PLAINTEXT_BYTES, p256dh=RFC_UA_PUBLIC, auth=RFC_AUTH)) == 4096


def test_vapid_token_has_a_valid_signature_and_claims():
    private_key = webpush.generate_vapid_private_key()
    request = webpush.build_request(endpoint=ENDPOINT, p256dh=RFC_UA_PUBLIC, auth=RFC_AUTH, payload=b"{}", vapid_private_key=private_key, subject="https://claire.example", ttl=60, urgency="high", topic="claire-7", now=1000.0)
    assert request.headers["Content-Encoding"] == "aes128gcm" and request.headers["TTL"] == "60" and request.headers["Urgency"] == "high" and request.headers["Topic"] == "claire-7"
    scheme, _, rest = request.headers["Authorization"].partition(" ")
    fields = dict(part.strip().split("=", 1) for part in rest.split(","))
    assert scheme == "vapid" and fields["k"] == webpush.vapid_public_key(private_key)
    header, claims, signature = fields["t"].split(".")
    assert json.loads(webpush.b64u_decode(header)) == {"alg": "ES256", "typ": "JWT"}
    assert json.loads(webpush.b64u_decode(claims)) == {"aud": "https://push.example.net", "exp": 1000 + 12 * 3600, "sub": "https://claire.example"}
    raw = webpush.b64u_decode(signature)
    public = ec.EllipticCurvePublicKey.from_encoded_point(ec.SECP256R1(), webpush.b64u_decode(fields["k"]))
    public.verify(encode_dss_signature(int.from_bytes(raw[:32], "big"), int.from_bytes(raw[32:], "big")), f"{header}.{claims}".encode("ascii"), ec.ECDSA(hashes.SHA256()))
    with pytest.raises(webpush.WebPushError):
        webpush.endpoint_audience("http://push.example.net/x")


# --- Store (I/O) -----------------------------------------------------------------------------------------------------


@pytest.fixture
def client_conn(tmp_path: Path):
    conn = client_db.connect_client_db(tmp_path / "client.db")
    yield conn
    conn.close()


def _subscribe(conn: sqlite3.Connection, account_id: int, endpoint: str = ENDPOINT, session_ref: str = "s1") -> store.PushSubscription:
    return store.upsert_subscription(conn, account_id=account_id, endpoint=endpoint, p256dh=RFC_UA_PUBLIC, auth=RFC_AUTH, label="Chrome · Linux", session_ref=session_ref, now=10.0)


def test_group_joins_events_until_the_notification_is_read(client_conn):
    first, created = store.add_notification(client_conn, account_id=2, kind="kb_join_request", params={"actor": "Ben", "kb": "Cities"}, target_path="/moderation/kb/9?tab=people", group_key="kb:7", now=1.0)
    again, created_again = store.add_notification(client_conn, account_id=2, kind="kb_join_request", params={"actor": "Cho", "kb": "Cities"}, target_path="/moderation/kb/9?tab=people", group_key="kb:7", events=2, now=2.0)
    other, _ = store.add_notification(client_conn, account_id=3, kind="kb_join_request", params={}, target_path="/x", group_key="kb:7", now=2.0)
    assert (created, created_again, first == again, other != first) == (True, False, True, True)
    row = store.get_notification(client_conn, first)
    assert row is not None
    assert (row.count, row.params["actor"], row.updated_at, row.created_at) == (3, "Cho", 2.0, 1.0)
    assert store.unread_count(client_conn, 2) == 1
    assert store.mark_read(client_conn, 2, ids=[first, other], now=3.0) == 1  # the row of account 3 is not a row of account 2
    assert store.unread_count(client_conn, 2) == 0 and store.unread_count(client_conn, 3) == 1
    fresh, created_fresh = store.add_notification(client_conn, account_id=2, kind="kb_join_request", params={}, target_path="/x", group_key="kb:7", now=4.0)
    assert created_fresh and fresh != first
    assert [n.id for n in store.list_notifications(client_conn, 2, limit=10)] == [fresh, first]


def test_queue_subscriptions_and_purge(client_conn):
    sub = _subscribe(client_conn, 2)
    moved = _subscribe(client_conn, 3, session_ref="s2")  # the same browser signs in as another account: the row moves
    assert moved.id == sub.id and moved.account_id == 3 and store.list_subscriptions(client_conn, 2) == []
    for index in range(store.MAX_SUBSCRIPTIONS - 1):
        _subscribe(client_conn, 3, endpoint=f"https://push.example.net/send/{index}")
    with pytest.raises(store.SubscriptionLimit):
        _subscribe(client_conn, 3, endpoint="https://push.example.net/send/over")
    notification_id, _ = store.add_notification(client_conn, account_id=3, kind="test", params={}, target_path="/account/notifications", now=5.0)
    assert store.enqueue_push(client_conn, notification_id=notification_id, account_id=3, not_before=5.0) == store.MAX_SUBSCRIPTIONS
    assert store.enqueue_push(client_conn, notification_id=notification_id, account_id=3, not_before=9.0) == 0  # a waiting row stays
    assert len(store.due_push_jobs(client_conn, now=5.0)) == store.MAX_SUBSCRIPTIONS and store.due_push_jobs(client_conn, now=4.0) == []
    store.mark_read(client_conn, 3, ids=None)
    assert store.due_push_jobs(client_conn, now=5.0) == []  # a read notification is not sent
    store.set_push_off(client_conn, 3, "ap_follower", True)
    assert store.push_off_kinds(client_conn, 3) == {"ap_follower"}
    client_db.purge_account(client_conn, 3)
    assert store.list_subscriptions(client_conn, 3) == [] and store.list_notifications(client_conn, 3, limit=5) == [] and store.push_off_kinds(client_conn, 3) == set()


def test_migration_from_version_8_keeps_data(tmp_path: Path):
    path = tmp_path / "client.db"
    conn = client_db.connect_client_db(path)
    for table in ("notifications", "push_subscriptions", "push_queue", "notification_push_off", "notification_scan_seen"):
        conn.execute(f"DROP TABLE {table}")
    conn.execute("UPDATE client_meta SET value='8' WHERE key='schema_version'")
    conn.execute("INSERT INTO federation_auto_replies (account_id, sender_iri, last_sent_at, count) VALUES (2, 'https://a.example/u', 1, 4)")
    conn.commit()
    conn.close()
    conn = client_db.connect_client_db(path)
    try:
        assert client_db.stored_client_version(conn) == client_db.CLIENT_SCHEMA_VERSION == 9
        assert conn.execute("SELECT count FROM federation_auto_replies").fetchone()[0] == 4
        assert _subscribe(conn, 2).id == 1
    finally:
        conn.close()


def test_vapid_key_is_made_one_time_and_encrypted(tmp_path: Path):
    service = SecretService(master_key=b"k" * 32)
    conn = auth_db.connect_auth_db(auth_db.auth_db_path(tmp_path))
    try:
        key = store.ensure_vapid_private_key(conn, service)
        assert store.ensure_vapid_private_key(conn, service) == key and len(webpush.b64u_decode(key)) == 32
        row = conn.execute("SELECT public_key, private_ciphertext FROM server_key_pairs WHERE name='webpush_vapid'").fetchone()
        assert row["public_key"] == webpush.vapid_public_key(key) and key.encode() not in bytes(row["private_ciphertext"])
    finally:
        conn.close()


# --- Server fixture --------------------------------------------------------------------------------------------------


async def _no_scheduler(_settings: Any) -> None:
    await asyncio.Event().wait()


@pytest.fixture
def world(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """A network-mode server: the admin and the active accounts ann, ben, cho. ann founds the shared knowledge base ``Cities`` (join policy ``approval``).

    The task of the ``api`` process that scans and sends does not run: each test calls ``scan_pending_contributions`` and ``send_due`` itself, so that no send happens at a time that the test does not control.
    """
    monkeypatch.setattr(sender, "run_scheduler", _no_scheduler)
    yield from _make_world(tmp_path)


@pytest.fixture
def live_world(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """The same server with the task of the ``api`` process. The requests to a push service go to ``posted`` and get the status 201."""
    posted: list[webpush.PushRequest] = []
    monkeypatch.setattr(sender, "_default_post", lambda request: posted.append(request) or 201)
    for made in _make_world(tmp_path):
        made.posted = posted
        yield made


def _make_world(tmp_path: Path):
    (tmp_path / "data").mkdir(parents=True)
    settings = Settings(
        CLAIRE_DB_PATH=str(tmp_path / "data" / "claire.db"),
        CLAIRE_VAULT_PATH=str(tmp_path / "vault"),
        CLAIRE_PROVIDER="mock",
        CLAIRE_ENVIRONMENT="development",
        CLAIRE_PUBLIC_URL=PUBLIC_URL,
        CLAIRE_INJECT_TOKEN=OWNER_TOKEN,
        CLAIRE_ANONYMOUS_READONLY=True,
        CLAIRE_MULTI_THEME=True,
    )
    conn = dbm.connect(settings.db_file)
    try:
        dbm.init_db(conn)
        now = time.time()
        for username, account_id in ACCOUNTS.items():
            conn.execute(
                "INSERT INTO accounts (id, public_id, username, role, status, display_name, profile_public, created_at, updated_at) VALUES (?, ?, ?, ?, 'active', ?, 1, ?, ?) "
                "ON CONFLICT(id) DO UPDATE SET username=excluded.username, role=excluded.role, status=excluded.status, display_name=excluded.display_name",
                (account_id, (1 << 57) + account_id, username, "admin" if account_id == 1 else "member", username.capitalize(), now, now),
            )
        accounts_store.AccountStore(conn).set_setting("mode", "network", updated_by=1)
        conn.commit()
    finally:
        conn.close()
    tokens = get_token_store(data_dir=settings.data_dir)
    bearer = {name: {"Authorization": f"Bearer {tokens.create_api_token(name=name, scope='owner', account_id=account_id)['token']}"} for name, account_id in ACCOUNTS.items()}
    public = {name: ids.format_public_id((1 << 57) + account_id) for name, account_id in ACCOUNTS.items()}
    with TestClient(create_app(settings=settings), base_url=PUBLIC_URL, follow_redirects=False) as client:
        made = client.post("/kb", json={"label": "Cities", "knowledge_type": "real", "seed_entities": ["Seoul", "Busan"]}, headers=bearer["ann"])
        assert made.status_code == 201, made.text
        kb = made.json()["knowledge_base"]

        def session(name: str, method: str, path: str, **kwargs: Any):
            """One request with a new cookie session of ``name`` that has a recent sign-in."""
            headers = step_up_headers(settings, account_id=ACCOUNTS[name])
            client.cookies.clear()
            response = client.request(method, path, headers=headers, **kwargs)
            client.cookies.clear()
            return response

        yield SimpleNamespace(settings=settings, db_file=settings.db_file, client=client, bearer=bearer, public=public, kb=kb, base=f"/kb/{kb['number']}", session=session)


def _rows(world, name: str) -> list[store.Notification]:
    conn = client_db.connect_client_existing(client_db.client_db_path(world.db_file), readonly=True)
    try:
        return store.list_notifications(conn, ACCOUNTS[name], limit=50)
    finally:
        conn.close()


def _kinds(world, name: str) -> list[tuple[str, str, int]]:
    return [(n.kind, n.target_path, n.count) for n in _rows(world, name)]


def _client(world) -> sqlite3.Connection:
    return client_db.connect_client_existing(client_db.client_db_path(world.db_file))


def _session_of(world, name: str) -> tuple[dict[str, str], str]:
    """Session headers of ``name`` and the credential identifier of that session."""
    headers = step_up_headers(world.settings, account_id=ACCOUNTS[name])
    token = headers["Cookie"].split("=", 1)[1]
    conn = dbm.connect_existing(world.db_file, readonly=True)
    try:
        nonce = conn.execute("SELECT nonce FROM auth_sessions WHERE session_token=?", (token,)).fetchone()[0]
    finally:
        conn.close()
    return headers, accounts_store._credential_id(str(nonce))


def _request(world, headers: dict[str, str], method: str, path: str, **kwargs: Any):
    world.client.cookies.clear()
    response = world.client.request(method, path, headers=headers, **kwargs)
    world.client.cookies.clear()
    return response


# --- Emit and text ---------------------------------------------------------------------------------------------------


def test_emit_skips_the_actor_and_accounts_that_are_not_active_and_never_raises(world):
    conn = dbm.connect_existing(world.db_file)
    conn.execute("UPDATE accounts SET status='suspended' WHERE id=4")
    conn.commit()
    conn.close()
    made = notifications.emit(world.db_file, kind="mutual_requested", account_ids=[2, 3, 4, None], actor_account_id=2, target="/settings/kb?tab=mem", params={"actor": "Ann"})
    assert len(made) == 1 and _kinds(world, "ben") == [("mutual_requested", "/settings/kb?tab=mem", 1)] and _kinds(world, "ann") == [] and _kinds(world, "cho") == []
    assert notifications.emit(world.db_file, kind="no_such_kind", account_ids=[3], target="/x") == []
    assert notifications.emit(world.db_file, kind="test", account_ids=[3], target="https://evil.example/") == []
    assert notifications.emit(world.db_file, kind="kb_role_given", account_ids=[3], target="/x", params={"variant": "owner"}) == []


def test_each_kind_has_text_in_each_locale_and_for_a_count():
    for kind in notifications.KINDS.values():
        for variant in kind.variants or ("",):
            for count in (1, 3):
                row = store.Notification(1, 1, kind.name, {"variant": variant, "actor": "A", "kb": "K", "agent": "G", "client": "C"}, "/", None, count, 0.0, 0.0, None, None)
                for locale in ("en", "ko"):
                    title, body = notifications.render(row, locale)
                    assert title and body and "notify." not in title + body and "{" not in title + body, (kind.name, variant, count, locale)
    unknown = store.Notification(1, 1, "kind_of_a_newer_version", {}, "/", None, 1, 0.0, 0.0, None, None)
    assert notifications.render(unknown, "en") == ("Notification", "Open Claire to see the item.")
    long_text = str(notifications._clean_params({"actor": "x" * 500 + "\u0000"})["actor"])
    assert len(long_text) == notifications.PARAM_MAX_LENGTH and long_text.endswith("…")


# --- Events (one for each kind) --------------------------------------------------------------------------------------


def test_platform_events_through_the_routes(world):
    client, kb_id, number = world.client, world.kb["id"], world.kb["number"]
    # Join request: the people managers of the knowledge base (the moderator ann and the admin), not the requester.
    assert client.post(f"{world.base}/membership", headers=world.bearer["ben"]).status_code == 201
    assert client.post(f"{world.base}/membership", headers=world.bearer["cho"]).status_code == 201
    expected = [("kb_join_request", f"/moderation/kb/{number}?tab=people", 2)]
    assert _kinds(world, "ann") == expected and _kinds(world, "admin") == expected and _kinds(world, "ben") == []
    assert notifications.render(_rows(world, "ann")[0], "en")[1] == "2 join requests wait in Cities."
    # Approval: the requester.
    assert client.patch(f"{world.base}/members/{world.public['ben']}", json={"action": "approve"}, headers=world.bearer["ann"]).status_code == 200
    assert _kinds(world, "ben") == [("kb_join_approved", "/settings/kb?tab=mem", 1)]
    # Role collaborator (a recent sign-in), then moderator by the admin.
    assert world.session("ann", "PATCH", f"{world.base}/members/{world.public['ben']}", json={"role": "collaborator"}).status_code == 200
    assert world.session("admin", "PUT", f"/api/admin/kb/{number}/moderators/{world.public['ben']}").status_code == 200
    roles = [(n.kind, n.params["variant"]) for n in _rows(world, "ben") if n.kind == "kb_role_given"]
    assert sorted(roles) == [("kb_role_given", "collaborator"), ("kb_role_given", "moderator")]
    # Mutual collaboration: the request goes to the other account, the acceptance to the requester.
    assert world.session("ann", "POST", "/api/mutual-collaborations", json={"account": world.public["cho"]}).status_code == 201
    assert ("mutual_requested", "/settings/kb?tab=mem", 1) in _kinds(world, "cho")
    assert world.session("cho", "PUT", f"/api/mutual-collaborations/{world.public['ann']}").status_code == 200
    assert ("mutual_accepted", "/settings/kb?tab=mem", 1) in _kinds(world, "ann")
    assert all(n.kind != "mutual_accepted" for n in _rows(world, "cho"))
    assert kb_id


def test_contribution_scan_and_decision_events(world):
    kb_id, number = world.kb["id"], world.kb["number"]

    def add(public_id: int, contributor: int | None, credit: str | None = None) -> None:
        conn = dbm.connect_existing(world.db_file)
        try:
            kb_collab.ensure_remote_columns(conn)
            conn.execute(
                "INSERT INTO kb_contributions (public_id, kb_id, document_id, origin, contributor_account_id, state, contributed_at, updated_at, remote_credit_iri) VALUES (?, ?, ?, 'bulk', ?, 'pending', 1, 1, ?)",
                (public_id, kb_id, f"doc-{public_id}", contributor, credit),
            )
            conn.commit()
        finally:
            conn.close()

    add(101, ACCOUNTS["ben"])
    # The first scan records what waits and tells about nothing (an update of the server makes no notifications for old items).
    assert events.scan_pending_contributions(world.db_file) == 0 and _kinds(world, "ann") == []
    add(102, ACCOUNTS["ben"])
    add(103, ACCOUNTS["cho"])
    add(104, None, "https://other.example/users/dana")
    assert events.scan_pending_contributions(world.db_file) == 3
    queue = f"/moderation/kb/{number}?tab=queue"
    assert sorted(_kinds(world, "ann")) == [("kb_contribution_pending", queue, 2), ("kf_contribution_pending", queue, 1)]
    assert sorted(_kinds(world, "admin")) == sorted(_kinds(world, "ann")) and _kinds(world, "ben") == []
    remote = next(n for n in _rows(world, "ann") if n.kind == "kf_contribution_pending")
    assert notifications.render(remote, "en")[1] == "dana@other.example offered a contribution to Cities."
    assert events.scan_pending_contributions(world.db_file) == 0 and len(_rows(world, "ann")) == 2  # a second scan tells about nothing
    # Decisions: the contributor gets the answer; an account of another server has no account here.
    events.kb_contribution_decided(world.db_file, kb_id=kb_id, contributor_account_id=ACCOUNTS["ben"], decision="approved", actor_account_id=ACCOUNTS["ann"])
    events.kb_contribution_decided(world.db_file, kb_id=kb_id, contributor_account_id=ACCOUNTS["ben"], decision="approved", actor_account_id=ACCOUNTS["ann"])
    events.kb_contribution_decided(world.db_file, kb_id=kb_id, contributor_account_id=ACCOUNTS["cho"], decision="rejected", actor_account_id=ACCOUNTS["ann"])
    events.kb_contribution_decided(world.db_file, kb_id=kb_id, contributor_account_id=None, decision="removed", actor_account_id=ACCOUNTS["ann"])
    assert _kinds(world, "ben") == [("kb_contribution_decided", "/settings/kb?tab=subs", 2)] and _kinds(world, "cho") == [("kb_contribution_decided", "/settings/kb?tab=subs", 1)]
    assert notifications.render(_rows(world, "ben")[0], "ko")[1] == "Cities에 보낸 기여 2건이 승인되었습니다."


def test_agent_and_activitypub_events(world):
    personal = int(get_kb_manager(world.settings).define_kb("Ben notes", account_id=ACCOUNTS["ben"], is_personal=True).id)
    events.agent_source_staged(world.db_file, kb_id=personal)
    events.agent_source_staged(world.db_file, kb_id=personal)
    events.agent_extraction_done(world.db_file, kb_id=personal, document_id="doc 1", agent="claude-code")
    events.agent_write_grant(world.db_file, account_id=ACCOUNTS["ben"], client="Claude Code")
    events.ap_follower(world.db_file, account_id=ACCOUNTS["ben"], actor_iri="https://social.example/users/alice")
    events.ap_follow_answered(world.db_file, account_id=ACCOUNTS["ben"], actor_iri="https://social.example/users/alice", accepted=False)
    events.ap_mention(world.db_file, account_ids=[ACCOUNTS["ben"], ACCOUNTS["cho"]], actor_iri="https://social.example/@bob")
    events.ap_quote_request(world.db_file, account_id=ACCOUNTS["ben"], actor_iri="https://social.example/users/alice")
    events.ap_quote_answered(world.db_file, account_id=ACCOUNTS["ben"], actor_iri="https://social.example/users/alice", approved=True)
    events.ap_private_message(world.db_file, account_ids=[ACCOUNTS["ben"]], actor_iri="https://social.example/users/9001")
    events.kf_offer_answered(world.db_file, account_id=ACCOUNTS["ben"], kb_iri="https://other.example/kb/55", accepted=True)
    events.kf_grant(world.db_file, account_id=ACCOUNTS["ben"], kb_iri="https://other.example/kb/55", given=True)
    events.kf_grant(world.db_file, account_id=None, kb_iri="https://other.example/kb/55", given=False)  # no account: no notification, no exception
    got = {n.kind: n for n in _rows(world, "ben")}
    assert set(got) == {"agent_source_staged", "agent_extraction_done", "agent_write_grant", "ap_follower", "ap_follow_answered", "ap_mention", "ap_quote_request", "ap_quote_answered", "ap_private_message", "kf_offer_answered", "kf_grant"}
    assert (got["agent_source_staged"].count, got["agent_source_staged"].target_path) == (2, "/account/mcp")
    # The entry address of an extracted document is its permalink.
    extraction_target = got["agent_extraction_done"].target_path
    assert extraction_target.startswith("/silvercord/") and len(extraction_target) == len("/silvercord/") + 16
    assert got["ap_follower"].params["actor"] == "alice@social.example" and got["ap_follower"].target_path == "/account/federation"
    assert got["ap_follow_answered"].params["variant"] == "rejected" and got["ap_mention"].params["actor"] == "bob@social.example"
    assert got["ap_private_message"].params["actor"] == "social.example"  # a numeric path part is not a name
    assert got["ap_quote_request"].target_path == "/syndication?stage=collect" and got["kf_grant"].params == {"kb": "other.example", "variant": "given"}
    assert [n.kind for n in _rows(world, "cho")] == ["ap_mention"]
    assert set(got) | {"kb_join_request", "kb_join_approved", "kb_contribution_pending", "kb_contribution_decided", "kb_role_given", "mutual_requested", "mutual_accepted", "kf_contribution_pending", "test"} == set(notifications.KINDS)


def test_after_commit_holds_events_and_drops_them_on_failure(world):
    with events.after_commit():
        events.ap_follower(world.db_file, account_id=ACCOUNTS["ben"], actor_iri="https://social.example/users/alice")
        assert _rows(world, "ben") == []
    assert [n.kind for n in _rows(world, "ben")] == ["ap_follower"]
    with pytest.raises(RuntimeError):
        with events.after_commit():
            events.ap_mention(world.db_file, account_ids=[ACCOUNTS["cho"]], actor_iri="https://social.example/users/alice")
            raise RuntimeError("the transaction failed")
    assert _rows(world, "cho") == []
    events.ap_mention(world.db_file, account_ids=[ACCOUNTS["cho"]], actor_iri="https://social.example/users/alice")  # the block is closed: the event runs at once
    assert [n.kind for n in _rows(world, "cho")] == ["ap_mention"]


# --- Sender ----------------------------------------------------------------------------------------------------------


def _one_subscription(world, name: str, endpoint: str = ENDPOINT) -> str:
    headers, ref = _session_of(world, name)
    conn = _client(world)
    try:
        _subscribe(conn, ACCOUNTS[name], endpoint=endpoint, session_ref=ref)
        conn.commit()
    finally:
        conn.close()
    return ref


def _state(world) -> tuple[int, int]:
    conn = _client(world)
    try:
        return int(conn.execute("SELECT COUNT(*) FROM push_subscriptions").fetchone()[0]), int(conn.execute("SELECT COUNT(*) FROM push_queue").fetchone()[0])
    finally:
        conn.close()


def test_sender_sends_an_encrypted_message_with_the_text_of_the_account_locale(world):
    _one_subscription(world, "ben")
    conn = dbm.connect_existing(world.db_file)
    conn.execute("UPDATE accounts SET locale='ko' WHERE id=?", (ACCOUNTS["ben"],))
    conn.commit()
    conn.close()
    events.ap_quote_request(world.db_file, account_id=ACCOUNTS["ben"], actor_iri="https://social.example/users/alice")
    sent: list[webpush.PushRequest] = []

    def post(request: webpush.PushRequest) -> int:
        sent.append(request)
        return 201

    report = sender.send_due(world.settings, post=post)
    assert (report.sent, report.dropped, report.removed_subscriptions) == (1, 0, 0) and _state(world) == (1, 0)
    request = sent[0]
    assert request.endpoint == ENDPOINT and request.headers["Urgency"] == "high" and request.headers["TTL"] == "86400" and request.headers["Topic"].startswith("claire-")
    claims = json.loads(webpush.b64u_decode(request.headers["Authorization"].split("t=", 1)[1].split(",", 1)[0].split(".")[1]))
    assert claims["aud"] == "https://push.example.net" and claims["sub"] == PUBLIC_URL
    # The browser of the RFC example decrypts the body: the payload has the Korean text and the entry address.
    ua_private = webpush.load_vapid_private_key(RFC_UA_PRIVATE)
    as_public = ec.EllipticCurvePublicKey.from_encoded_point(ec.SECP256R1(), request.body[21:86])
    ikm = webpush._hkdf(webpush.b64u_decode(RFC_AUTH), ua_private.exchange(ec.ECDH(), as_public), b"WebPush: info\x00" + webpush.b64u_decode(RFC_UA_PUBLIC) + request.body[21:86], 32)
    key, nonce = webpush._hkdf(request.body[:16], ikm, b"Content-Encoding: aes128gcm\x00", 16), webpush._hkdf(request.body[:16], ikm, b"Content-Encoding: nonce\x00", 12)
    payload = json.loads(AESGCM(key).decrypt(nonce, request.body[86:], None)[:-1])
    assert payload["title"] == "인용 요청" and payload["url"] == "/syndication?stage=collect" and payload["unread"] == 1 and payload["kind"] == "ap_quote_request" and "alice@social.example" in payload["body"]
    assert sender.send_due(world.settings, post=post).total == 0  # the queue is empty
    # The 60 s rule: a second event of the same group waits until 60 s after the last send.
    events.ap_quote_request(world.db_file, account_id=ACCOUNTS["ben"], actor_iri="https://social.example/users/carol")
    assert sender.send_due(world.settings, post=post).total == 0
    assert sender.send_due(world.settings, post=post, now=time.time() + 61).sent == 1


def test_sender_results_gone_retry_failure_and_session_end(world):
    _one_subscription(world, "ben")
    events.ap_follower(world.db_file, account_id=ACCOUNTS["ben"], actor_iri="https://social.example/users/alice")
    now = time.time()
    statuses = iter([503, 429, 500, 503])
    for step, wait in enumerate((0.0, 31.0, 160.0, 800.0)):
        report = sender.send_due(world.settings, post=lambda _r: next(statuses), now=now + wait)
        assert (report.retried, report.dropped) == ((1, 0) if step < 3 else (0, 1)), step
    assert _state(world) == (1, 0)  # the message was given up after four attempts; the subscription stays with one failure
    # 410: the push service has no such subscription.
    events.ap_follow_answered(world.db_file, account_id=ACCOUNTS["ben"], actor_iri="https://social.example/users/alice", accepted=True)
    assert sender.send_due(world.settings, post=lambda _r: 410).removed_subscriptions == 1 and _state(world) == (0, 0)
    # A network error is a retry.
    _one_subscription(world, "ben")
    events.ap_mention(world.db_file, account_ids=[ACCOUNTS["ben"]], actor_iri="https://social.example/users/alice")

    def offline(_request: webpush.PushRequest) -> int:
        raise httpx.ConnectError("no route")

    assert sender.send_due(world.settings, post=offline).retried == 1
    # The session ends (sign-out): the subscription goes before a send, and nothing is sent.
    conn = dbm.connect_existing(world.db_file)
    conn.execute("DELETE FROM auth_sessions WHERE account_id=?", (ACCOUNTS["ben"],))
    conn.commit()
    conn.close()
    called: list[int] = []
    report = sender.send_due(world.settings, post=lambda _r: called.append(1) or 201, now=time.time() + 40)
    assert report.removed_subscriptions == 1 and called == [] and _state(world) == (0, 0)


def test_sender_refuses_an_endpoint_that_is_not_public(world):
    _one_subscription(world, "ben", endpoint="https://127.0.0.1/push")
    events.ap_follower(world.db_file, account_id=ACCOUNTS["ben"], actor_iri="https://social.example/users/alice")
    report = sender.send_due(world.settings)  # the default sender: the address check refuses the request before a connection
    assert report.removed_subscriptions == 1 and _state(world) == (0, 0)


def test_sender_sends_nothing_without_a_public_origin(world, monkeypatch: pytest.MonkeyPatch):
    _one_subscription(world, "ben")
    events.ap_follower(world.db_file, account_id=ACCOUNTS["ben"], actor_iri="https://social.example/users/alice")
    monkeypatch.setattr(sender, "vapid_subject", lambda _settings: None)
    report = sender.send_due(world.settings, post=lambda _r: 201)
    assert (report.sent, report.dropped, report.errors) == (0, 1, ["no_public_origin"]) and _state(world) == (1, 0)


def test_a_kind_that_is_off_is_recorded_and_not_queued(world):
    _one_subscription(world, "ben")
    conn = _client(world)
    store.set_push_off(conn, ACCOUNTS["ben"], "ap_follower", True)
    conn.commit()
    conn.close()
    events.ap_follower(world.db_file, account_id=ACCOUNTS["ben"], actor_iri="https://social.example/users/alice")
    assert [n.kind for n in _rows(world, "ben")] == ["ap_follower"] and _state(world) == (1, 0)


def test_task_of_the_api_process_sends_after_an_emit(live_world):
    world = live_world
    headers, _ref = _session_of(world, "ben")
    body = {"endpoint": ENDPOINT, "keys": {"p256dh": RFC_UA_PUBLIC, "auth": RFC_AUTH}}
    assert _request(world, headers, "POST", "/api/me/push/subscriptions", json=body).status_code == 201
    assert _request(world, headers, "POST", "/api/me/push/test", json={}).status_code == 201
    deadline = time.time() + 10
    while not world.posted and time.time() < deadline:
        time.sleep(0.05)
    # The emit wakes the task: the message goes out without a wait for the interval.
    assert len(world.posted) == 1 and world.posted[0].endpoint == ENDPOINT and time.time() < deadline - 5
    assert _state(world) == (1, 0)


# --- Routes ----------------------------------------------------------------------------------------------------------


def test_notification_routes_list_read_and_test(world):
    headers, _ref = _session_of(world, "ben")
    assert _request(world, headers, "POST", "/api/me/push/test", json={}).status_code == 201
    events.ap_follower(world.db_file, account_id=ACCOUNTS["ben"], actor_iri="https://social.example/users/alice")
    listed = _request(world, headers, "GET", "/api/me/notifications?limit=10").json()
    assert listed["unread"] == 2 and [i["kind"] for i in listed["items"]] == ["ap_follower", "test"]
    assert listed["items"][0] == {**listed["items"][0], "title": "New follower", "body": "alice@social.example follows you.", "url": "/account/federation", "channel": "activitypub", "class": "notice", "read": False, "count": 1}
    read = _request(world, headers, "POST", "/api/me/notifications/read", json={"ids": [listed["items"][0]["id"]]})
    assert read.status_code == 200 and read.json()["unread"] == 1
    assert _request(world, headers, "POST", "/api/me/notifications/read", json={"all": True}).json()["unread"] == 0
    assert _request(world, headers, "POST", "/api/me/notifications/read", json={"ids": []}).status_code == 400
    assert _request(world, headers, "GET", "/api/me/notifications?limit=500").status_code == 400
    # Another account does not see the rows; a caller without an account gets no answer of the route.
    assert _request(world, _session_of(world, "cho")[0], "GET", "/api/me/notifications").json() == {"items": [], "unread": 0}
    assert _request(world, {}, "GET", "/api/me/notifications").status_code in (401, 403, 404)


def test_push_routes_subscribe_kinds_and_remove(world):
    headers, ref = _session_of(world, "ben")
    state = _request(world, headers, "GET", "/api/me/push").json()
    assert state["ready"] is True and state["subscriptions"] == [] and len(webpush.b64u_decode(state["public_key"])) == 65
    assert {k["kind"] for k in state["kinds"]} == set(notifications.KINDS) - {"test"} and all(k["push"] for k in state["kinds"])
    body = {"endpoint": ENDPOINT, "keys": {"p256dh": RFC_UA_PUBLIC, "auth": RFC_AUTH}}
    made = _request(world, headers, "POST", "/api/me/push/subscriptions", json=body)
    assert made.status_code == 201 and "endpoint" not in made.text
    assert _request(world, headers, "POST", "/api/me/push/subscriptions", json=body).status_code == 201  # the same browser again: one row
    conn = _client(world)
    try:
        subs = store.list_subscriptions(conn, ACCOUNTS["ben"])
    finally:
        conn.close()
    assert len(subs) == 1 and subs[0].session_ref == ref and subs[0].endpoint == ENDPOINT
    # Endpoints that cannot be a push service, and keys that cannot encrypt.
    for bad in ("http://push.example.net/x", "https://10.0.0.5/x", "https://user:pw@push.example.net/x", "https:///x", ""):
        assert _request(world, headers, "POST", "/api/me/push/subscriptions", json={**body, "endpoint": bad}).status_code == 400, bad
    assert _request(world, headers, "POST", "/api/me/push/subscriptions", json={"endpoint": ENDPOINT, "keys": {"p256dh": "AAAA", "auth": RFC_AUTH}}).status_code == 400
    # A credential that is not a browser session cannot subscribe: the subscription is bound to a session.
    assert world.client.post("/api/me/push/subscriptions", json=body, headers=world.bearer["ben"]).status_code == 403
    # Kinds.
    assert _request(world, headers, "PUT", "/api/me/push/kinds/ap_follower", json={"push": False}).json() == {"kind": "ap_follower", "push": False}
    assert _request(world, headers, "PUT", "/api/me/push/kinds/test", json={"push": False}).status_code == 404
    assert _request(world, headers, "PUT", "/api/me/push/kinds/ap_follower", json={"push": "no"}).status_code == 400
    state = _request(world, headers, "GET", "/api/me/push").json()
    assert [k["push"] for k in state["kinds"] if k["kind"] == "ap_follower"] == [False] and state["subscriptions"][0]["this_session"] is True
    # Another account cannot remove the device; the owner can.
    other = _session_of(world, "cho")[0]
    assert _request(world, other, "DELETE", "/api/me/push/subscriptions", json={"id": subs[0].id}).json()["deleted"] is False
    assert _request(world, headers, "DELETE", "/api/me/push/subscriptions", json={"endpoint": ENDPOINT}).json()["deleted"] is True
    assert _state(world) == (0, 0)


def test_account_support_bundle_has_the_notifications_of_the_account_without_capabilities(world):
    """End to end through the routes: the account makes its bundle and reads the member ``notifications/summary.json``."""
    import io
    import tarfile

    from compression import zstd

    ben_headers, _ref = _session_of(world, "ben")
    endpoint = "https://fcm.googleapis.com/fcm/send/capability-of-the-device-of-ben"
    assert _request(world, ben_headers, "POST", "/api/me/push/subscriptions", json={"endpoint": endpoint, "keys": {"p256dh": RFC_UA_PUBLIC, "auth": RFC_AUTH}}).status_code == 201
    assert _request(world, ben_headers, "PUT", "/api/me/push/kinds/ap_follower", json={"push": False}).status_code == 200
    _one_subscription(world, "cho", endpoint="https://web.push.apple.com/capability-of-the-device-of-cho")
    events.ap_quote_request(world.db_file, account_id=ACCOUNTS["ben"], actor_iri="https://social.example/users/alice")
    events.ap_quote_request(world.db_file, account_id=ACCOUNTS["ben"], actor_iri="https://social.example/users/carol")
    events.ap_follower(world.db_file, account_id=ACCOUNTS["ben"], actor_iri="https://social.example/users/alice")
    events.ap_mention(world.db_file, account_ids=[ACCOUNTS["cho"]], actor_iri="https://social.example/users/zed")

    made = _request(world, ben_headers, "POST", "/api/support/bundles", json={"scope": "account", "days": 1})
    assert made.status_code == 200, made.text
    archive = _request(world, {}, "GET", "/support/bundle/" + made.json()["bundle"]["link"].split("/support/bundle/", 1)[1])
    assert archive.status_code == 200
    files: dict[str, bytes] = {}
    with zstd.ZstdFile(io.BytesIO(archive.content), "rb") as reader, tarfile.open(fileobj=reader, mode="r|") as tar:
        for member in tar:
            data = tar.extractfile(member)
            if data is not None:
                files[member.name.split("/", 1)[1]] = data.read()
    blob = b"".join(files.values()).decode("utf-8")
    # No capability of a device, no key, no name from a notification, and nothing of another account.
    for secret in ("capability-of-the-device", RFC_UA_PUBLIC, RFC_AUTH, "alice@social.example", "zed@social.example", "web.push.apple.com", "ap_mention"):
        assert secret not in blob, secret
    summary = json.loads(files["notifications/summary.json"])
    assert summary["server_can_send"] is True and summary["server_sender_task"] == "absent"  # the fixture stops the task of the api process
    [device] = summary["devices"]
    assert (device["push_service"], device["session"], device["failures_in_sequence"], device["last_error"], device["last_success_at"]) == ("fcm.googleapis.com", "in_effect", 0, None, None)
    assert summary["kinds_without_push"] == ["ap_follower"]
    kinds = {row["kind"]: (row["notifications"], row["unread"], row["events"], row["unread_never_sent"]) for row in summary["notifications_by_kind"]}
    assert kinds == {"ap_follower": (1, 1, 1, 1), "ap_quote_request": (1, 1, 2, 1)}
    assert (summary["push_queue"]["messages"], summary["push_queue"]["due_now"]) == (1, 1)  # ap_follower has no push for this account
    assert [(row["kind"], row["events"], row["read_at"], row["sent_at"]) for row in summary["recent"]] == [("ap_follower", 1, None, None), ("ap_quote_request", 2, None, None)]
    assert [finding["code"] for finding in summary["findings"]] == ["SERVER_SENDER_NOT_RUNNING"]
    manifest = json.loads(files["manifest.json"])
    assert (manifest["counts"]["notification_devices"], manifest["counts"]["notification_findings"]) == (1, 1)

    # The session of the device ends: the bundle of the account says so.
    conn = dbm.connect_existing(world.db_file)
    conn.execute("DELETE FROM auth_sessions WHERE account_id=?", (ACCOUNTS["ben"],))
    conn.commit()
    conn.close()
    from claire.ops.support_bundle_notifications import collect_account_notifications

    later = collect_account_notifications(world.settings, account_id=ACCOUNTS["ben"], now=time.time(), since=time.time() - 86400)
    assert later["devices"][0]["session"] == "ended" and [f["code"] for f in later["findings"]] == ["SERVER_SENDER_NOT_RUNNING", "DEVICE_SESSION_ENDED"]
    # An account without a device and with unread notifications.
    conn = _client(world)
    conn.execute("DELETE FROM push_subscriptions WHERE account_id=?", (ACCOUNTS["cho"],))
    conn.commit()
    conn.close()
    cho = collect_account_notifications(world.settings, account_id=ACCOUNTS["cho"], now=time.time(), since=time.time() - 86400)
    assert cho["devices"] == [] and [f["code"] for f in cho["findings"]] == ["SERVER_SENDER_NOT_RUNNING", "NO_DEVICE"]


def test_settings_page_and_tab_query_pages_render(world):
    headers, _ref = _session_of(world, "ben")
    page = _request(world, headers, "GET", "/account/notifications")
    assert page.status_code == 200
    html = page.text
    assert 'class="console-shell"' in html and "data-notify-device" in html and "/static/js/console_notifications.js" in html and 'id="notifybtn"' in html
    for kind in notifications.KINDS.values():
        assert (f'data-notify-kind="{kind.name}"' in html) == (kind.channel in notifications.CHANNELS)
    assert _request(world, headers, "GET", "/settings/kb?tab=subs").status_code == 200
    assert _request(world, _session_of(world, "ann")[0], "GET", f"/moderation/kb/{world.kb['number']}?tab=people").status_code == 200
    worker = world.client.get("/sw.js").text
    assert "addEventListener('push'" in worker and "addEventListener('notificationclick'" in worker and "showNotification" in worker
