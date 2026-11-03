"""Tests for account lifecycle, disk garbage collection, and username release (설계 §8.2, §8.6, §8.9, §15).

Tests:
1. Re-use of retired username before release is rejected.
2. Step-up authentication enforcement on admin endpoints (401/403).
3. Username release preview by default and apply=True release execution.
4. Clean account_id and public_id generation on re-registering released username.
5. End-to-end (E2E) account lifecycle: suspend, resume, delete, purge, release.
6. Test isolation: tmp_path and CLAIRE_* environment variables.
"""

from __future__ import annotations

import os
import sqlite3
import time
from dataclasses import dataclass
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
from starlette.testclient import TestClient

from claire.accounts.username_policy import UsernameRejected
from claire.api import security, server
from claire.api.principal import STEP_UP_WINDOW_SECONDS
from claire.store import accounts as accounts_store
from claire.store import auth_db
from claire.store import db as dbm
from claire.store.token_store import reset_token_stores

OWNER_TOKEN = "owner-" + "o" * 32
READONLY_TOKEN = "readonly-" + "r" * 32
PUBLIC_URL = "http://127.0.0.1:8765"
ORIGIN = PUBLIC_URL
ADMIN_USERNAME = "admin_kim"
ADMIN_PASSWORD = "correct-horse-battery"


@pytest.fixture(autouse=True)
def _isolate_env(monkeypatch):
    for key in list(os.environ):
        if key.startswith("CLAIRE_"):
            monkeypatch.delenv(key, raising=False)
    reset_token_stores()
    yield
    reset_token_stores()


@pytest.fixture(autouse=True)
def _patch_admin_route_policies(monkeypatch):
    new_policies = dict(security.ROUTE_POLICY)
    new_policies[("PATCH", "/api/admin/accounts")] = security._system("admin")
    new_policies[("GET", "/api/admin/usernames/retired")] = security._system("admin")
    new_policies[("HEAD", "/api/admin/usernames/retired")] = security._system("admin")
    new_policies[("POST", "/api/admin/usernames/release")] = security._system("admin")
    monkeypatch.setattr(security, "ROUTE_POLICY", new_policies)
    monkeypatch.setattr(security, "_EXACT_ROUTE_POLICY", {
        k: v for k, v in new_policies.items() if "{" not in k[1]
    })


@dataclass
class _Settings:
    db_file: Path
    data_dir: Path
    environment: str = "development"
    public_url: str = PUBLIC_URL
    inject_token: str = OWNER_TOKEN
    readonly_token: str = READONLY_TOKEN
    cors_allowed_origins: str = ""
    anonymous_readonly: bool = False


class _Client:
    def __init__(self, client: TestClient) -> None:
        self.client = client

    def request(
        self,
        method: str,
        path: str,
        *,
        session: str | None = None,
        bearer: str | None = None,
        **kwargs: Any,
    ):
        headers = dict(kwargs.pop("headers", {}) or {})
        if session is not None:
            headers["Cookie"] = f"claire_session={session}"
        if bearer is not None:
            headers["Authorization"] = f"Bearer {bearer}"
        if method not in {"GET", "HEAD"}:
            headers["Origin"] = ORIGIN
        self.client.cookies.clear()
        response = self.client.request(method, path, headers=headers, follow_redirects=False, **kwargs)
        self.client.cookies.clear()
        return response


@pytest.fixture
def settings(tmp_path: Path) -> _Settings:
    return _Settings(db_file=tmp_path / "claire.db", data_dir=tmp_path)


@pytest.fixture
def conn(settings: _Settings):
    c = dbm.connect(settings.db_file)
    try:
        dbm.init_db(c)
        yield c
    finally:
        c.close()


@pytest.fixture
def auth_conn(settings: _Settings):
    c = auth_db.connect_auth_db(auth_db.auth_db_path(settings.data_dir))
    try:
        yield c
    finally:
        c.close()


@pytest.fixture
def client(settings: _Settings):
    app = server.create_app(settings, SimpleNamespace(provider=SimpleNamespace(name="stub")))
    with TestClient(app, base_url=PUBLIC_URL) as tc:
        yield _Client(tc)


def _activate_admin(settings: _Settings) -> None:
    c = dbm.connect_existing(settings.db_file)
    a = auth_db.connect_auth_db(auth_db.auth_db_path(settings.data_dir))
    try:
        accounts_store.AccountStore(c).complete_setup(1, username=ADMIN_USERNAME, display_name="Admin")
        auth_db.set_password(a, 1, ADMIN_PASSWORD, username=ADMIN_USERNAME)
    finally:
        a.close()
        c.close()


def _session(settings: _Settings, account_id: int = 1, *, authenticated_ago: float = 0.0, scope: str = "owner") -> str:
    c = dbm.connect_existing(settings.db_file)
    try:
        return accounts_store.create_account_session(
            c, account_id=account_id, scope=scope, now=time.time() - authenticated_ago,
        )
    finally:
        c.close()


def test_account_state_transitions(conn: sqlite3.Connection):
    store = accounts_store.AccountStore(conn)
    admin = store.complete_setup(1, username=ADMIN_USERNAME, display_name="Admin")

    with pytest.raises(accounts_store.AccountStateError, match="admin account cannot be suspended"):
        store.suspend(admin.id)
    with pytest.raises(accounts_store.AccountStateError, match="admin account cannot be deleted"):
        store.schedule_delete(admin.id)
    with pytest.raises(accounts_store.AccountStateError, match="admin account cannot be purged"):
        store.purge(admin.id)

    bob = store.create_member(username="bob_lee", display_name="Bob")
    assert bob.status == "active"

    suspended = store.suspend(bob.id)
    assert suspended.status == "suspended"
    with pytest.raises(accounts_store.AccountStateError):
        store.suspend(bob.id)

    resumed = store.resume(bob.id)
    assert resumed.status == "active"
    with pytest.raises(accounts_store.AccountStateError):
        store.resume(bob.id)

    deleting = store.schedule_delete(bob.id)
    assert deleting.status == "deleting"
    assert deleting.delete_after is not None
    assert deleting.delete_after > time.time() + 6 * 86400

    cancelled = store.cancel_delete(bob.id)
    assert cancelled.status == "active"
    assert cancelled.delete_after is None
    with pytest.raises(accounts_store.AccountStateError):
        store.cancel_delete(bob.id)

    store.suspend(bob.id)
    deleting2 = store.schedule_delete(bob.id)
    assert deleting2.status == "deleting"

    purged = store.purge(bob.id)
    assert purged.status == "purged"
    assert purged.delete_after is None


def test_credentials_cleared_on_suspension_and_deletion(conn: sqlite3.Connection):
    store = accounts_store.AccountStore(conn)
    store.complete_setup(1, username=ADMIN_USERNAME, display_name="Admin")
    member = store.create_member(username="token_tester", display_name="Tester")

    tok = accounts_store.create_account_session(conn, account_id=member.id)
    assert conn.execute("SELECT COUNT(*) FROM auth_sessions WHERE account_id=?", (member.id,)).fetchone()[0] == 1

    store.suspend(member.id)
    assert conn.execute("SELECT COUNT(*) FROM auth_sessions WHERE account_id=?", (member.id,)).fetchone()[0] == 0

    store.resume(member.id)
    tok2 = accounts_store.create_account_session(conn, account_id=member.id)
    assert conn.execute("SELECT COUNT(*) FROM auth_sessions WHERE account_id=?", (member.id,)).fetchone()[0] == 1

    store.schedule_delete(member.id)
    assert conn.execute("SELECT COUNT(*) FROM auth_sessions WHERE account_id=?", (member.id,)).fetchone()[0] == 0

    store.cancel_delete(member.id)
    tok3 = accounts_store.create_account_session(conn, account_id=member.id)
    assert conn.execute("SELECT COUNT(*) FROM auth_sessions WHERE account_id=?", (member.id,)).fetchone()[0] == 1

    store.purge(member.id)
    assert conn.execute("SELECT COUNT(*) FROM auth_sessions WHERE account_id=?", (member.id,)).fetchone()[0] == 0


def test_disk_gc_on_account_purge(conn: sqlite3.Connection, tmp_path: Path):
    store = accounts_store.AccountStore(conn)
    store.complete_setup(1, username=ADMIN_USERNAME, display_name="Admin")
    member = store.create_member(username="gc_target", display_name="GC Target")

    now = time.time()
    conn.execute(
        "INSERT INTO themes (id, seq, label, account_id, created_at, updated_at) VALUES (20, 20, 'theme_gc', ?, ?, ?)",
        (member.id, now, now),
    )
    conn.execute(
        "INSERT INTO documents (id, theme_id, title, url, fetched_at) VALUES ('doc_gc_orphan', 20, 'Orphan Doc', 'http://a.com', ?)",
        (now,),
    )
    conn.execute(
        "INSERT INTO documents (id, theme_id, title, url, fetched_at) VALUES ('doc_admin_keep', 0, 'Admin Doc', 'http://b.com', ?)",
        (now,),
    )

    art_dir = tmp_path / "raw" / "artifacts"
    art_dir.mkdir(parents=True, exist_ok=True)
    orphan_art = art_dir / "doc_gc_orphan.txt.zst"
    orphan_art.write_bytes(b"orphan artifact")
    admin_art = art_dir / "doc_admin_keep.txt.zst"
    admin_art.write_bytes(b"admin artifact")

    img_dir = tmp_path / "images"
    img_dir.mkdir(parents=True, exist_ok=True)
    orphan_img = img_dir / "doc_gc_orphan_1.png"
    orphan_img.write_bytes(b"image bytes")

    att_dir = tmp_path / "raw" / "attachments" / "doc_gc_orphan"
    att_dir.mkdir(parents=True, exist_ok=True)
    (att_dir / "file.pdf").write_bytes(b"pdf content")

    pres_dir = tmp_path / "presentations"
    pres_dir.mkdir(parents=True, exist_ok=True)
    (pres_dir / "doc_gc_orphan.html").write_text("html content", encoding="utf-8")

    files_dir = tmp_path / "raw" / "files"
    files_dir.mkdir(parents=True, exist_ok=True)
    orphan_raw = files_dir / "inbox_orphan.pdf"
    orphan_raw.write_bytes(b"orphan raw pdf")
    shared_raw = files_dir / "inbox_shared.pdf"
    shared_raw.write_bytes(b"shared raw pdf")

    conn.execute(
        "INSERT INTO raw_inbox (theme_id, file_ref, received_at) VALUES (20, ?, ?)",
        (str(orphan_raw), now),
    )
    conn.execute(
        "INSERT INTO raw_inbox (theme_id, file_ref, received_at) VALUES (20, ?, ?)",
        (str(shared_raw), now),
    )
    conn.execute(
        "INSERT INTO raw_inbox (theme_id, file_ref, received_at) VALUES (0, ?, ?)",
        (str(shared_raw), now),
    )
    conn.commit()

    store.purge(member.id, data_dir=tmp_path)

    assert not orphan_art.exists()
    assert not orphan_img.exists()
    assert not att_dir.exists()
    assert not (pres_dir / "doc_gc_orphan.html").exists()
    assert not orphan_raw.exists()
    assert admin_art.exists()
    assert shared_raw.exists()
    assert conn.execute("SELECT COUNT(*) FROM themes WHERE id=20").fetchone()[0] == 0
    assert conn.execute("SELECT COUNT(*) FROM documents WHERE theme_id=20").fetchone()[0] == 0


def test_retired_username_rejected_until_release(conn: sqlite3.Connection, auth_conn: sqlite3.Connection):
    store = accounts_store.AccountStore(conn)
    store.complete_setup(1, username=ADMIN_USERNAME, display_name="Admin")
    member = store.create_member(username="charlie_cat", display_name="Charlie", profile_public=True)

    store.purge(member.id)

    assert store.is_username_retired("charlie_cat") is True

    with pytest.raises(UsernameRejected) as exc_info:
        store.validate_username("charlie_cat")
    assert exc_info.value.reason == "retired"

    with pytest.raises(UsernameRejected) as exc_info2:
        store.create_member(username="charlie_cat", display_name="New Charlie")
    assert exc_info2.value.reason == "retired"

    preview = store.preview_release_username("charlie_cat", auth_conn=auth_conn)
    assert preview["username"] == "charlie_cat"
    assert preview["account_id"] == member.id
    assert preview["has_410_marker"] is True

    success = store.release_username("charlie_cat", released_by=1, auth_conn=auth_conn)
    assert success is True
    assert store.is_username_retired("charlie_cat") is False

    tombstone = conn.execute(
        "SELECT was_public, released_at, released_by FROM username_tombstones WHERE username='charlie_cat'",
    ).fetchone()
    assert tombstone["released_at"] is not None
    assert tombstone["released_by"] == 1
    assert tombstone["was_public"] == 0

    time.sleep(0.005)
    new_member = store.create_member(username="charlie_cat", display_name="Reborn Charlie")
    assert new_member.id != member.id
    assert new_member.public_id != member.public_id
    assert new_member.username == "charlie_cat"


def test_admin_step_up_authentication_enforcement(client: _Client, settings: _Settings):
    _activate_admin(settings)

    stale_session = _session(settings, account_id=1, authenticated_ago=STEP_UP_WINDOW_SECONDS + 60)
    fresh_session = _session(settings, account_id=1, authenticated_ago=10)

    for path, method, payload in [
        ("/api/admin/accounts", "PATCH", {"username": "foo", "action": "suspend"}),
        ("/api/admin/usernames/release", "POST", {"username": "foo", "apply": False}),
    ]:
        resp_stale = client.request(method, path, session=stale_session, json=payload)
        assert resp_stale.status_code == 401
        assert "reauth_required" in resp_stale.json()["error"]

        resp_bearer = client.request(method, path, bearer=OWNER_TOKEN, json=payload)
        assert resp_bearer.status_code == 403
        assert "step_up_requires_session" in resp_bearer.json()["error"]

        resp_anon = client.request(method, path, json=payload)
        assert resp_anon.status_code == 404

    resp_retired_anon = client.request("GET", "/api/admin/usernames/retired")
    assert resp_retired_anon.status_code == 404

    resp_retired_fresh = client.request("GET", "/api/admin/usernames/retired", session=fresh_session)
    assert resp_retired_fresh.status_code == 200
    assert "retired" in resp_retired_fresh.json()


def test_e2e_account_lifecycle_and_username_release(client: _Client, settings: _Settings, conn: sqlite3.Connection, auth_conn: sqlite3.Connection):
    """End-to-End lifecycle test via HTTP endpoints."""
    _activate_admin(settings)
    admin_session = _session(settings, account_id=1, authenticated_ago=10)

    store = accounts_store.AccountStore(conn)
    alice = store.create_member(username="alice_e2e", display_name="Alice E2E", profile_public=True)

    resp_list = client.request("GET", "/api/admin/accounts", session=admin_session)
    assert resp_list.status_code == 200
    accounts = resp_list.json()["accounts"]
    alice_entry = next(a for a in accounts if a["username"] == "alice_e2e")
    assert alice_entry["status"] == "active"

    resp_suspend = client.request(
        "PATCH",
        "/api/admin/accounts",
        session=admin_session,
        json={"username": "alice_e2e", "action": "suspend"},
    )
    assert resp_suspend.status_code == 200
    assert resp_suspend.json()["account"]["status"] == "suspended"

    resp_resume = client.request(
        "PATCH",
        "/api/admin/accounts",
        session=admin_session,
        json={"account_id": alice.id, "action": "resume"},
    )
    assert resp_resume.status_code == 200
    assert resp_resume.json()["account"]["status"] == "active"

    resp_del = client.request(
        "PATCH",
        "/api/admin/accounts",
        session=admin_session,
        json={"username": "alice_e2e", "action": "delete"},
    )
    assert resp_del.status_code == 200
    assert resp_del.json()["account"]["status"] == "deleting"
    assert resp_del.json()["account"]["delete_after"] is not None

    resp_cancel = client.request(
        "PATCH",
        "/api/admin/accounts",
        session=admin_session,
        json={"username": "alice_e2e", "action": "cancel_delete"},
    )
    assert resp_cancel.status_code == 200
    assert resp_cancel.json()["account"]["status"] == "active"

    resp_reset = client.request(
        "PATCH",
        "/api/admin/accounts",
        session=admin_session,
        json={"username": "alice_e2e", "action": "reset_password_link"},
    )
    assert resp_reset.status_code == 200
    assert resp_reset.json()["ok"] is True
    assert "reset_link" in resp_reset.json()

    resp_purge = client.request(
        "PATCH",
        "/api/admin/accounts",
        session=admin_session,
        json={"username": "alice_e2e", "action": "purge"},
    )
    assert resp_purge.status_code == 200
    assert resp_purge.json()["account"]["status"] == "purged"

    resp_retired = client.request("GET", "/api/admin/usernames/retired", session=admin_session)
    assert resp_retired.status_code == 200
    retired_list = resp_retired.json()["retired"]
    assert any(r["username"] == "alice_e2e" for r in retired_list)

    with pytest.raises(UsernameRejected) as exc_info:
        store.create_member(username="alice_e2e", display_name="Alice Duplicate")
    assert exc_info.value.reason == "retired"

    resp_preview = client.request(
        "POST",
        "/api/admin/usernames/release",
        session=admin_session,
        json={"username": "alice_e2e", "apply": False},
    )
    assert resp_preview.status_code == 200
    preview_data = resp_preview.json()
    assert preview_data["apply"] is False
    assert preview_data["username"] == "alice_e2e"
    assert preview_data["has_410_marker"] is True

    resp_retired_still = client.request("GET", "/api/admin/usernames/retired", session=admin_session)
    assert any(r["username"] == "alice_e2e" for r in resp_retired_still.json()["retired"])

    resp_release = client.request(
        "POST",
        "/api/admin/usernames/release",
        session=admin_session,
        json={"username": "alice_e2e", "apply": True},
    )
    assert resp_release.status_code == 200
    assert resp_release.json()["apply"] is True
    assert resp_release.json()["released"] is True

    resp_retired_after = client.request("GET", "/api/admin/usernames/retired", session=admin_session)
    assert not any(r["username"] == "alice_e2e" for r in resp_retired_after.json()["retired"])

    time.sleep(0.005)
    new_alice = store.create_member(username="alice_e2e", display_name="Alice Reborn")
    assert new_alice.id != alice.id
    assert new_alice.public_id != alice.public_id
    assert new_alice.username == "alice_e2e"
    assert new_alice.status == "active"
