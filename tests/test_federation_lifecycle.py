"""Lifecycle tasks and privacy triggers (Fedify sidecar design Sections 8.1 and 9.4, unit FP3-4)."""

from __future__ import annotations

import json
import os
import time
from dataclasses import dataclass
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
from starlette.testclient import TestClient

from claire.api import federation_routes, server
from claire.api.accounts_routes.admin import ADMIN_ACCOUNTS_API_PATH
from claire.api.federation_internal import INTERNAL_PREFIX, SIGNATURE_HEADER, TIMESTAMP_HEADER, create_internal_federation_app, ensure_service_key, load_service_key, service_key_path, sign_request
from claire.store import accounts as accounts_store
from claire.store import actor_keys
from claire.store import auth_db
from claire.store import db as dbm
from claire.store import federation as fed
from claire.store.secrets import get_secret_service, reset_secret_service
from claire.store.theme import ThemeManager

PUBLIC_URL = "http://127.0.0.1:8765"
ALPHA_PUBLIC_ID = 116412345678901234
BETA_PUBLIC_ID = 216412345678901234
BOB = "https://remote.example/users/bob"


@dataclass
class _Settings:
    db_file: Path
    data_dir: Path
    environment: str = "development"
    public_url: str = PUBLIC_URL
    inject_token: str = "owner-" + "o" * 32
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
    monkeypatch.setenv("CLAIRE_SECRET_KEY", "55" * 32)
    reset_secret_service()
    yield
    reset_secret_service()


def _connect(settings: _Settings):
    return dbm.connect_existing(settings.db_file)


def _tasks(settings: _Settings) -> list[tuple]:
    conn = _connect(settings)
    try:
        return [tuple(r) for r in conn.execute("SELECT account_id, kind, object_public_id, status, error_code FROM federation_outbox ORDER BY id")]
    finally:
        conn.close()


def _tombstones(settings: _Settings) -> dict[int, str]:
    conn = _connect(settings)
    try:
        return {int(r[0]): str(r[1]) for r in conn.execute("SELECT public_id, kind FROM federation_tombstones")}
    finally:
        conn.close()


@pytest.fixture
def env(tmp_path):
    """A network-mode instance with federation on. ``alpha`` is an actor with the public themes 11 and 12, three published documents, and one follower. ``beta`` is an actor with no follower."""
    settings = _Settings(db_file=tmp_path / "claire.db", data_dir=tmp_path)
    conn = dbm.connect(settings.db_file)
    try:
        dbm.init_db(conn)
        store = accounts_store.AccountStore(conn)
        store.complete_setup(1, username="admin_kim", display_name="Admin Kim")
        store.set_setting("mode", "network", updated_by=1)
        store.set_federation_state("on", updated_by=1)
        members = []
        for public_id, username in ((ALPHA_PUBLIC_ID, "member_alpha"), (BETA_PUBLIC_ID, "member_beta")):
            cur = conn.execute("INSERT INTO accounts (public_id, username, role, status, display_name, profile_public, created_at, activated_at, updated_at) VALUES (?, ?, 'member', 'active', ?, 1, 0, 0, 0)", (public_id, username, username))
            members.append(int(cur.lastrowid))
        alpha, beta = members
        for theme_id in (11, 12):
            conn.execute("INSERT INTO themes (id, seq, label, is_default, is_public, is_collaborator_accessible, created_at, updated_at, account_id, number) VALUES (?, ?, ?, 0, 1, 1, 1, 1, ?, ?)", (theme_id, theme_id, f"theme-{theme_id}", alpha, 316412345678901200 + theme_id))
        objects = {}
        for document_id, theme_id in (("doc-a", 11), ("doc-b", 11), ("doc-c", 12)):
            conn.execute("INSERT INTO documents (id, theme_id, title, detail, detail_format, hidden, fetched_at) VALUES (?, ?, ?, 'Body', 'md', 0, 1)", (document_id, theme_id, document_id))
            obj, _task, _made = fed.publish_document(conn, account_id=alpha, theme_id=theme_id, document_id=document_id, published_by_account_id=alpha, salt=store.id_salt(), now=1000.0)
            objects[document_id] = obj.public_id
        # The tasks of the publish calls are done. The tests look at the tasks that a trigger makes.
        conn.execute("UPDATE federation_outbox SET status='handed_off', handed_off_at=1001")
        fed.upsert_follower(conn, account_id=alpha, actor_iri=BOB, inbox_iri=f"{BOB}/inbox", shared_inbox_iri=None, salt=store.id_salt(), now=1000.0)
        conn.commit()
    finally:
        conn.close()
    return SimpleNamespace(settings=settings, alpha=alpha, beta=beta, objects=objects)


def _new_tasks(settings: _Settings) -> list[tuple]:
    return [row for row in _tasks(settings) if row[3] != "handed_off"]


# --- Privacy triggers (design Section 9.4) ---


def test_theme_change_to_private_makes_delete_tasks_with_the_visibility_epoch(env, monkeypatch):
    manager = ThemeManager(env.settings)
    before = manager.get_theme(11).visibility_epoch

    # The tasks and the epoch are one transaction: a failure of the task insert leaves the theme public.
    with monkeypatch.context() as m:
        m.setattr(fed, "add_outbox_task", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("no task")))
        with pytest.raises(RuntimeError):
            manager.update_theme(11, is_public=False)
    conn = _connect(env.settings)
    try:
        assert tuple(conn.execute("SELECT is_public, visibility_epoch FROM themes WHERE id=11").fetchone()) == (1, before)
        assert conn.execute("SELECT COUNT(*) FROM federation_objects WHERE theme_id=11").fetchone()[0] == 2
    finally:
        conn.close()
    assert _new_tasks(env.settings) == [] and _tombstones(env.settings) == {}

    manager = ThemeManager(env.settings)
    manager.update_theme(11, is_public=False)
    conn = _connect(env.settings)
    try:
        assert tuple(conn.execute("SELECT is_public, visibility_epoch FROM themes WHERE id=11").fetchone()) == (0, before + 1)
        assert [r[0] for r in conn.execute("SELECT document_id FROM federation_objects")] == ["doc-c"]
    finally:
        conn.close()
    assert _new_tasks(env.settings) == [(env.alpha, "delete", env.objects["doc-a"], "pending", None), (env.alpha, "delete", env.objects["doc-b"], "pending", None)]
    assert _tombstones(env.settings) == {env.objects["doc-a"]: "object", env.objects["doc-b"]: "object"}

    # A change that does not make the theme private makes no task: a label change, and the change back to public.
    manager.update_theme(11, label="new label")
    manager.update_theme(11, is_public=True)
    manager.update_theme(12, description="still public")
    assert len(_new_tasks(env.settings)) == 2


@pytest.mark.parametrize("purge", [False, True])
def test_theme_deletion_makes_delete_tasks_and_tombstones(env, purge):
    ThemeManager(env.settings).delete_theme(12, purge=purge)
    assert _new_tasks(env.settings) == [(env.alpha, "delete", env.objects["doc-c"], "pending", None)]
    assert _tombstones(env.settings) == {env.objects["doc-c"]: "object"}
    conn = _connect(env.settings)
    try:
        assert conn.execute("SELECT COUNT(*) FROM themes WHERE id=12").fetchone()[0] == 0
        assert sorted(r[0] for r in conn.execute("SELECT document_id FROM federation_objects")) == ["doc-a", "doc-b"]
    finally:
        conn.close()


def test_document_purge_makes_a_delete_task_for_that_document_only(env):
    conn = dbm.connect(env.settings.db_file)
    try:
        dry = dbm.purge_document_cascade(conn, env.settings.data_dir, ["doc-a"], dry_run=True, theme_id=11)
        assert dry["dry_run"] is True and _new_tasks(env.settings) == []
        report = dbm.purge_document_cascade(conn, env.settings.data_dir, ["doc-a"], theme_id=11)
    finally:
        conn.close()
    assert report["federation_delete_tasks"] == 1
    assert _new_tasks(env.settings) == [(env.alpha, "delete", env.objects["doc-a"], "pending", None)]
    assert _tombstones(env.settings) == {env.objects["doc-a"]: "object"}


# --- Actor tasks (design Section 8.1) ---


def test_display_name_change_of_an_actor_makes_an_update_actor_task(env):
    conn = _connect(env.settings)
    try:
        store = accounts_store.AccountStore(conn)
        store.update_display_name(env.alpha, "Alpha Kim")
        # The same name again is no change.
        store.update_display_name(env.alpha, "Alpha Kim")
        assert _new_tasks(env.settings) == [(env.alpha, "update_actor", None, "pending", None)]

        # A paused account, and an instance with federation off, send nothing.
        store.update_profile_public(env.beta, 0)
        store.update_display_name(env.beta, "Beta Kim")
        store.set_federation_state("off", updated_by=1)
        store.update_display_name(env.alpha, "Alpha Park")
        assert len(_new_tasks(env.settings)) == 1
    finally:
        conn.close()


class _Internal:
    """A signed client for the internal sub-application."""

    def __init__(self, settings: _Settings) -> None:
        ensure_service_key(service_key_path(settings.data_dir))
        self.key = load_service_key(service_key_path(settings.data_dir))
        self.now = time.time()
        routes = federation_routes.federation_internal_routes(settings, now=lambda: self.now)
        self.client = TestClient(create_internal_federation_app(settings, routes=routes, now=lambda: self.now))

    def call(self, method: str, path: str, body: Any = None):
        target = f"{INTERNAL_PREFIX}{path}"
        content = b"" if body is None else json.dumps(body).encode()
        ts = str(int(self.now))
        return self.client.request(method, target, content=content, headers={TIMESTAMP_HEADER: ts, SIGNATURE_HEADER: sign_request(self.key, method, target, ts, content)})


def _key_count(settings: _Settings, account_id: int) -> int:
    auth = auth_db.connect_auth_db(auth_db.auth_db_path(settings.data_dir))
    try:
        return int(auth.execute("SELECT COUNT(*) FROM actor_keys WHERE account_id=?", (account_id,)).fetchone()[0])
    finally:
        auth.close()


def test_purge_of_an_actor_gives_410_and_a_delete_actor_task_until_the_acknowledgement(env):
    internal = _Internal(env.settings)
    assert internal.call("GET", f"/actors/{ALPHA_PUBLIC_ID}").status_code == 200
    keys_before = internal.call("GET", f"/actors/{ALPHA_PUBLIC_ID}/keys").json()
    assert _key_count(env.settings, env.alpha) == 2
    conn = _connect(env.settings)
    try:
        store = accounts_store.AccountStore(conn)
        # A task that waits at the time of the purge gets no handoff.
        store.update_display_name(env.alpha, "Alpha Kim")
        store.purge(env.alpha, data_dir=env.settings.data_dir)
        assert fed.has_pending_delete_actor(conn, env.alpha)
    finally:
        conn.close()
    assert _new_tasks(env.settings) == [(env.alpha, "update_actor", None, "rejected", "account_purged"), (env.alpha, "delete_actor", None, "pending", None)]
    # The actor and each of its objects are tombstones. The object rows went with the themes.
    assert _tombstones(env.settings) == {ALPHA_PUBLIC_ID: "actor", **{number: "object" for number in env.objects.values()}}

    gone = internal.call("GET", f"/actors/{ALPHA_PUBLIC_ID}")
    assert gone.status_code == 410 and gone.json()["error"] == "gone"
    assert internal.call("GET", f"/objects/{env.objects['doc-a']}").status_code == 410
    assert internal.call("GET", "/actors/by-username/member_alpha").status_code == 404
    # While the task waits, the sidecar gets the task, the same keys, and the followers.
    tasks = internal.call("GET", "/outbox-tasks").json()["tasks"]
    assert [(t["kind"], t["actor_public_id"], t["object_public_id"]) for t in tasks] == [("delete_actor", str(ALPHA_PUBLIC_ID), None)]
    assert internal.call("GET", f"/actors/{ALPHA_PUBLIC_ID}/keys").json() == keys_before
    followers = internal.call("GET", f"/actors/{ALPHA_PUBLIC_ID}/followers").json()
    assert [item["actor_iri"] for item in followers["items"]] == [BOB]

    # The acknowledgement deletes the followers and the keys. The actor ID stays 410.
    assert internal.call("POST", f"/outbox-tasks/{tasks[0]['id']}/ack", {"result": "handed_off", "error_code": None}).json()["status"] == "handed_off"
    assert internal.call("GET", f"/actors/{ALPHA_PUBLIC_ID}/keys").status_code == 404
    assert internal.call("GET", f"/actors/{ALPHA_PUBLIC_ID}/followers").status_code == 404
    assert _key_count(env.settings, env.alpha) == 0
    conn = _connect(env.settings)
    try:
        assert fed.count_followers(conn, env.alpha) == 0
    finally:
        conn.close()
    assert internal.call("GET", f"/actors/{ALPHA_PUBLIC_ID}").status_code == 410
    # A different actor is not changed.
    assert internal.call("GET", f"/actors/{BETA_PUBLIC_ID}").status_code == 200


def test_purge_without_followers_or_with_the_skip_option_makes_no_task(env):
    conn = _connect(env.settings)
    try:
        store = accounts_store.AccountStore(conn)
        store.purge(env.beta, data_dir=env.settings.data_dir)
        store.purge(env.alpha, data_dir=env.settings.data_dir, skip_federated_delete=True)
        assert fed.count_followers(conn, env.alpha) == 0
        assert not fed.has_pending_delete_actor(conn, env.alpha)
    finally:
        conn.close()
    assert _new_tasks(env.settings) == []
    assert _tombstones(env.settings)[ALPHA_PUBLIC_ID] == "actor" and _tombstones(env.settings)[BETA_PUBLIC_ID] == "actor"


def test_purge_of_an_account_that_was_never_an_actor_leaves_no_tombstone(env):
    conn = _connect(env.settings)
    try:
        store = accounts_store.AccountStore(conn)
        store.update_profile_public(env.beta, 0)
        store.purge(env.beta, data_dir=env.settings.data_dir)
    finally:
        conn.close()
    assert BETA_PUBLIC_ID not in _tombstones(env.settings)
    assert _Internal(env.settings).call("GET", f"/actors/{BETA_PUBLIC_ID}").status_code == 404


def test_a_task_of_an_account_row_that_is_gone_is_rejected(env):
    conn = _connect(env.settings)
    try:
        accounts_store.AccountStore(conn).purge(env.alpha, data_dir=env.settings.data_dir)
        # The release of the USERNAME deletes the account row.
        conn.execute("DELETE FROM accounts WHERE id=?", (env.alpha,))
        conn.commit()
    finally:
        conn.close()
    assert _Internal(env.settings).call("GET", "/outbox-tasks").json() == {"tasks": []}
    assert _new_tasks(env.settings) == [(env.alpha, "delete_actor", None, "rejected", "account_missing")]


# --- Admin purge route: the purge waits while the sidecar is not available ---


def _admin_client(env):
    conn = _connect(env.settings)
    try:
        session = accounts_store.create_account_session(conn, account_id=1, scope="owner", now=time.time())
    finally:
        conn.close()
    app = server.create_app(env.settings, SimpleNamespace(provider=SimpleNamespace(name="stub")))
    client = TestClient(app, base_url=PUBLIC_URL)

    def purge(account_id: int, **extra: Any):
        client.cookies.clear()
        return client.patch(ADMIN_ACCOUNTS_API_PATH, json={"account_id": account_id, "action": "purge", **extra}, headers={"Cookie": f"claire_session={session}", "Origin": PUBLIC_URL})

    return purge


def _audit_actions(settings: _Settings) -> list[str]:
    auth = auth_db.connect_auth_db(auth_db.auth_db_path(settings.data_dir))
    try:
        return [str(r[0]) for r in auth.execute("SELECT action FROM admin_audit ORDER BY id")]
    finally:
        auth.close()


def _write_heartbeat(settings: _Settings, received_at: float) -> None:
    path = settings.data_dir / federation_routes.HEARTBEAT_FILE
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"received_at": received_at, "contract_version": federation_routes.CONTRACT_VERSION, "sidecar_version": "0.1.0", "queue_depth": 0, "self_check": {"ok": True, "checked_at": received_at}}))


def test_admin_purge_waits_for_the_sidecar_and_the_skip_option_is_audited(env):
    purge = _admin_client(env)
    svc = get_secret_service(env.settings.data_dir / "auth" / "master.key")
    auth = auth_db.connect_auth_db(auth_db.auth_db_path(env.settings.data_dir))
    try:
        actor_keys.ensure_actor_keys(auth, env.alpha, svc, now=time.time())
    finally:
        auth.close()

    # No heartbeat: the purge of an actor with a follower waits, and the account keeps its status.
    waiting = purge(env.alpha)
    assert waiting.status_code == 409 and waiting.json()["error"] == "federation_sidecar_unavailable", waiting.text
    _write_heartbeat(env.settings, time.time() - 3600)
    assert purge(env.alpha).status_code == 409
    conn = _connect(env.settings)
    try:
        assert accounts_store.AccountStore(conn).get(env.alpha).status == "active"
    finally:
        conn.close()
    assert _new_tasks(env.settings) == [] and "account.purge" not in _audit_actions(env.settings)

    # An actor without a follower needs no sidecar.
    assert purge(env.beta).status_code == 200
    assert _new_tasks(env.settings) == []

    # The explicit option: no task, the followers and the keys go now, and the choice is in the audit log.
    skipped = purge(env.alpha, skip_federated_delete=True)
    assert skipped.status_code == 200, skipped.text
    assert _new_tasks(env.settings) == []
    assert _key_count(env.settings, env.alpha) == 0
    assert _audit_actions(env.settings)[-2:] == ["federation.purge_skip_delete", "account.purge"]
    assert _tombstones(env.settings)[ALPHA_PUBLIC_ID] == "actor"


def test_admin_purge_with_a_recent_heartbeat_makes_the_delete_actor_task_and_keeps_the_keys(env):
    purge = _admin_client(env)
    svc = get_secret_service(env.settings.data_dir / "auth" / "master.key")
    auth = auth_db.connect_auth_db(auth_db.auth_db_path(env.settings.data_dir))
    try:
        actor_keys.ensure_actor_keys(auth, env.alpha, svc, now=time.time())
    finally:
        auth.close()
    _write_heartbeat(env.settings, time.time())
    assert purge(env.alpha).status_code == 200
    assert _new_tasks(env.settings) == [(env.alpha, "delete_actor", None, "pending", None)]
    assert _key_count(env.settings, env.alpha) == 2
    assert "federation.purge_skip_delete" not in _audit_actions(env.settings)


# --- Recovery for a lost sidecar database (design Section 9.4) ---


def test_requeue_sets_recent_handed_off_tasks_to_pending(env):
    conn = _connect(env.settings)
    try:
        conn.execute("UPDATE federation_outbox SET handed_off_at=500 WHERE object_public_id=?", (env.objects["doc-a"],))
        rejected = fed.add_outbox_task(conn, account_id=env.alpha, kind="update", object_public_id=env.objects["doc-b"], now=1002.0)
        fed.ack_outbox_task(conn, rejected.id, result="rejected", error_code="object_not_found", now=1003.0)
        assert fed.requeue_handed_off_tasks(conn, since=1000.0) == 2
        conn.commit()
    finally:
        conn.close()
    assert sorted((row[2], row[3]) for row in _tasks(env.settings)) == sorted([(env.objects["doc-a"], "handed_off"), (env.objects["doc-b"], "pending"), (env.objects["doc-c"], "pending"), (env.objects["doc-b"], "rejected")])


def test_cli_requeue_counts_without_apply_and_changes_with_apply(env, monkeypatch, capsys):
    from claire import cli
    from claire.config import get_settings

    conn = _connect(env.settings)
    try:
        conn.execute("UPDATE federation_outbox SET handed_off_at=?", (time.time() - 3600,))
        conn.execute("UPDATE federation_outbox SET handed_off_at=? WHERE object_public_id=?", (time.time() - 100 * 3600, env.objects["doc-a"]))
        conn.commit()
    finally:
        conn.close()
    monkeypatch.setenv("CLAIRE_DB_PATH", str(env.settings.db_file))
    monkeypatch.setenv("CB_DATA_DIR", str(env.settings.data_dir))
    get_settings.cache_clear() if hasattr(get_settings, "cache_clear") else None

    assert cli.main(["federation", "requeue", "--json"]) == 0
    assert json.loads(capsys.readouterr().out)["data"] == {"applied": False, "max_age_hours": 72.0, "tasks": 2}
    assert _new_tasks(env.settings) == []
    assert cli.main(["federation", "requeue", "--apply", "--json"]) == 0
    assert json.loads(capsys.readouterr().out)["data"] == {"applied": True, "max_age_hours": 72.0, "tasks": 2}
    assert sorted((row[2], row[3]) for row in _tasks(env.settings)) == sorted([(env.objects["doc-a"], "handed_off"), (env.objects["doc-b"], "pending"), (env.objects["doc-c"], "pending")])
    assert cli.main(["federation", "requeue", "--max-age-hours", "0"]) == 2
