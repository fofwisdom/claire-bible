"""AccountStore와 자격증명 귀속 (설계 §8.2–§8.4, §13.1)."""

from __future__ import annotations

import sqlite3
import threading
import time
from pathlib import Path

import pytest

from claire.accounts.username_policy import UsernameRejected
from claire.store import accounts as acc
from claire.store import db as dbm


@pytest.fixture()
def db_path(tmp_path: Path) -> Path:
    path = tmp_path / "claire.db"
    conn = dbm.connect(path)
    try:
        dbm.init_db(conn)
    finally:
        conn.close()
    return path


@pytest.fixture()
def conn(db_path: Path):
    c = dbm.connect(db_path)
    try:
        yield c
    finally:
        c.close()


def _add_member(conn: sqlite3.Connection, account_id: int, username: str | None, status: str = "active") -> None:
    now = time.time()
    conn.execute(
        "INSERT INTO accounts (id, public_id, username, role, status, created_at, updated_at) "
        "VALUES (?, ?, ?, 'member', ?, ?, ?)",
        (account_id, (1 << 57) + account_id, username, status, now, now),
    )
    conn.commit()


def test_admin_id_matches_store_db() -> None:
    assert acc.ADMIN_ACCOUNT_ID == dbm.ADMIN_ACCOUNT_ID == 1


def test_lookup(conn: sqlite3.Connection) -> None:
    store = acc.AccountStore(conn)
    admin = store.admin()
    assert admin == store.get(1)
    assert admin.role == "admin" and admin.status == "setup_required" and admin.username is None
    assert admin.profile_public is False
    with pytest.raises(acc.AccountNotFound):
        store.get(99)
    _add_member(conn, 2, "bob")
    assert [a.id for a in store.list_accounts()] == [1, 2]
    assert store.find_by_username("bob").id == 2
    assert store.find_by_username("bo") is None
    assert store.find_by_username("BOB").id == 2
    assert store.find_by_username("BOB").username == "bob"


def test_admin_missing_raises() -> None:
    c = sqlite3.connect(":memory:")
    c.row_factory = sqlite3.Row
    dbm.init_db(c)
    c.execute("DELETE FROM accounts")
    with pytest.raises(acc.AccountNotFound):
        acc.AccountStore(c).admin()


def test_settings_and_salt(conn: sqlite3.Connection) -> None:
    store = acc.AccountStore(conn)
    assert len(store.id_salt()) == 32
    assert store.get_setting("mode") is None
    store.set_setting("mode", "single", updated_by=1)
    store.set_setting("mode", "network", updated_by=1)
    assert store.get_setting("mode") == "network"
    conn.execute("DELETE FROM server_settings WHERE key='id_salt'")
    conn.commit()
    with pytest.raises(RuntimeError, match="id_salt is missing"):
        store.id_salt()


def test_complete_setup_transitions_once(conn: sqlite3.Connection) -> None:
    store = acc.AccountStore(conn)
    done = store.complete_setup(1, username="alice", display_name="Alice", now=1000.0)
    assert done.status == "active"
    assert done.username == "alice"
    assert done.display_name == "Alice"
    assert done.activated_at == 1000.0
    assert done.updated_at == 1000.0
    with pytest.raises(acc.AccountStateError):
        store.complete_setup(1, username="alice2", display_name="x")
    assert store.get(1).username == "alice"


def test_complete_setup_rejects_non_admin_and_wrong_state(conn: sqlite3.Connection) -> None:
    store = acc.AccountStore(conn)
    _add_member(conn, 2, None, status="setup_required")
    with pytest.raises(acc.AccountStateError):
        store.complete_setup(2, username="carol", display_name="")
    conn.execute("UPDATE accounts SET username='owner1', status='suspended' WHERE id=1")
    conn.commit()
    with pytest.raises(acc.AccountStateError):
        store.complete_setup(1, username="alice", display_name="")
    with pytest.raises(acc.AccountNotFound):
        store.complete_setup(42, username="alice", display_name="")


def test_complete_setup_rejects_retired_and_taken(conn: sqlite3.Connection) -> None:
    store = acc.AccountStore(conn)
    conn.execute(
        "INSERT INTO username_tombstones (username, account_id, retired_at) VALUES ('ghost', 9, 1.0)"
    )
    conn.execute(
        "INSERT INTO username_tombstones (username, account_id, retired_at, released_at, released_by) "
        "VALUES ('freed', 8, 1.0, 2.0, 1)"
    )
    conn.commit()
    _add_member(conn, 2, "bob")
    assert store.is_username_retired("ghost")
    assert not store.is_username_retired("freed")
    with pytest.raises(UsernameRejected) as e:
        store.complete_setup(1, username="ghost", display_name="")
    assert e.value.reason == "retired"
    with pytest.raises(UsernameRejected) as e:
        store.complete_setup(1, username="bob", display_name="")
    assert e.value.reason == "taken"
    assert store.get(1).status == "setup_required"
    # 정보 초기화된 이름은 다시 쓸 수 있다.
    assert store.complete_setup(1, username="freed", display_name="").username == "freed"


def test_complete_setup_concurrent_only_one_wins(db_path: Path) -> None:
    n = 6
    barrier = threading.Barrier(n)
    results: list[object] = [None] * n

    def worker(i: int) -> None:
        c = dbm.connect(db_path)
        try:
            barrier.wait()
            try:
                results[i] = acc.AccountStore(c).complete_setup(1, username=f"user{i}x", display_name="")
            except Exception as exc:  # noqa: BLE001 - 결과 분류용
                results[i] = exc
        finally:
            c.close()

    threads = [threading.Thread(target=worker, args=(i,)) for i in range(n)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    wins = [r for r in results if isinstance(r, acc.Account)]
    losses = [r for r in results if not isinstance(r, acc.Account)]
    assert len(wins) == 1
    assert all(isinstance(r, acc.AccountStateError) for r in losses), losses
    c = dbm.connect(db_path)
    try:
        assert acc.AccountStore(c).get(1).username == wins[0].username
    finally:
        c.close()


def test_update_display_name(conn: sqlite3.Connection) -> None:
    store = acc.AccountStore(conn)
    a = store.update_display_name(1, "Root Person", now=5.0)
    assert a.display_name == "Root Person" and a.updated_at == 5.0
    with pytest.raises(acc.AccountNotFound):
        store.update_display_name(77, "x")


def test_account_session_binding(conn: sqlite3.Connection) -> None:
    before = time.time()
    tok = acc.create_account_session(conn, account_id=1, scope="owner")
    b = acc.validate_session_binding(conn, tok)
    assert b is not None
    assert b.account_id == 1 and b.scope == "owner"
    assert b.authenticated_at is not None and b.authenticated_at >= before
    assert len(b.credential_id) == 16 and tok not in b.credential_id
    assert acc.validate_session_binding(conn, tok, scopes=("readonly",)) is None
    assert acc.validate_session_binding(conn, "short") is None
    assert acc.validate_session_binding(conn, "x" * 40) is None


def test_account_session_keeps_other_accounts_and_replaces_given(conn: sqlite3.Connection) -> None:
    _add_member(conn, 2, "bob")
    admin_tok = acc.create_account_session(conn, account_id=1)
    member_tok = acc.create_account_session(conn, account_id=2)
    assert acc.validate_session_binding(conn, admin_tok).account_id == 1
    assert acc.validate_session_binding(conn, member_tok).account_id == 2
    fresh = acc.create_account_session(conn, account_id=1, replace_token=admin_tok)
    assert acc.validate_session_binding(conn, admin_tok) is None
    assert acc.validate_session_binding(conn, fresh).account_id == 1
    assert acc.validate_session_binding(conn, member_tok).account_id == 2


def test_account_session_rejects_unknown_scope_and_account(conn: sqlite3.Connection) -> None:
    with pytest.raises(ValueError):
        acc.create_account_session(conn, account_id=1, scope="admin")
    with pytest.raises(acc.AccountNotFound):
        acc.create_account_session(conn, account_id=404)


def test_null_account_id_is_rejected(conn: sqlite3.Connection) -> None:
    tok = acc.create_account_session(conn, account_id=1)
    conn.execute("UPDATE auth_sessions SET account_id=NULL WHERE session_token=?", (tok,))
    conn.commit()
    assert acc.validate_session_binding(conn, tok) is None
    assert acc.mark_session_authenticated(conn, tok) is False

    access, _ = dbm.create_oauth_token(conn, "cli")
    b = acc.validate_oauth_binding(conn, access)
    assert b is not None and b.account_id == 1 and b.authenticated_at is None
    assert len(b.credential_id) == 16
    conn.execute("UPDATE oauth_tokens SET account_id=NULL WHERE token=?", (access,))
    conn.commit()
    assert acc.validate_oauth_binding(conn, access) is None


def test_mark_session_authenticated(conn: sqlite3.Connection) -> None:
    tok = dbm.create_session(conn)
    conn.execute("UPDATE auth_sessions SET authenticated_at=NULL")
    conn.commit()
    assert acc.validate_session_binding(conn, tok).authenticated_at is None
    assert acc.mark_session_authenticated(conn, tok, now=time.time()) is True
    assert acc.validate_session_binding(conn, tok).authenticated_at is not None
    assert acc.mark_session_authenticated(conn, "y" * 43) is False


def test_session_credential_id_survives_rotation(conn: sqlite3.Connection) -> None:
    tok = dbm.create_session(conn)
    before = acc.validate_session_binding(conn, tok).credential_id
    exchanged = dbm.exchange_session_token(conn, tok)
    assert exchanged is not None
    assert acc.validate_session_binding(conn, exchanged[1]).credential_id == before


def test_case_preserving_setup_member_and_case_insensitive_collision(conn: sqlite3.Connection) -> None:
    store = acc.AccountStore(conn)
    admin = store.complete_setup(1, username="FounderRoot", display_name="Founder Root", now=100.0)
    assert admin.username == "FounderRoot"
    assert store.find_by_username("founderroot").id == 1

    member = store.create_member(username="NASA", display_name="Space Agency", now=101.0)
    assert member.username == "NASA"
    assert store.find_by_username("nasa").username == "NASA"

    with pytest.raises(UsernameRejected) as exc:
        store.create_member(username="nasa", display_name="Duplicate", now=102.0)
    assert exc.value.reason == "taken"


def test_change_username_hold_expiry_early_release_and_federation_outbox(conn: sqlite3.Connection) -> None:
    store = acc.AccountStore(conn)
    store.complete_setup(1, username="Boss", display_name="Boss", now=100.0)
    store.set_setting("mode", "network", updated_by=1)

    alice = store.create_member(username="Alice", display_name="Alice", profile_public=True, now=101.0)
    bob = store.create_member(username="Bob", display_name="Bob", profile_public=False, now=101.0)
    tok = acc.create_account_session(conn, account_id=alice.id)

    unchanged = store.change_username(alice.id, "Alice", now=200.0)
    assert unchanged.changed is False

    case_only = store.change_username(alice.id, "ALICE", now=201.0)
    assert case_only.changed is True and case_only.case_only is True and case_only.account.username == "ALICE"
    assert case_only.federation_update_queued is True
    assert store.list_retired_usernames(now=201.0) == []

    renamed = store.change_username(alice.id, "AliceDev", now=300.0)
    assert renamed.changed is True and renamed.case_only is False and renamed.previous_username == "ALICE"
    assert renamed.account.username == "AliceDev"
    assert renamed.retired_until == pytest.approx(300.0 + acc.USERNAME_RENAME_HOLD_SECONDS)
    assert renamed.federation_update_queued is True

    # Outbox queued update_actor for public federated user.
    tasks = conn.execute("SELECT kind, object_public_id, status FROM federation_outbox WHERE account_id=? ORDER BY id", (alice.id,)).fetchall()
    assert [r[0] for r in tasks] == ["update_actor", "update_actor"]

    # An account with a private profile is an actor too: its rename queues update_actor.
    bob_renamed = store.change_username(bob.id, "Bobby", now=305.0)
    assert bob_renamed.federation_update_queued is True
    assert [r[0] for r in conn.execute("SELECT kind FROM federation_outbox WHERE account_id=?", (bob.id,))] == ["update_actor"]

    # Previous handle is held for 7 days.
    assert store.is_username_retired("Alice", now=300.0 + 3600.0) is True
    with pytest.raises(UsernameRejected) as exc:
        store.create_member(username="alice", display_name="Impostor", now=300.0 + 3600.0)
    assert exc.value.reason == "retired"

    # Early admin release unlocks handle while keeping active account and live session intact.
    preview = store.preview_release_username("Alice", now=400.0)
    assert preview["reason"] == "rename" and preview["residual_sessions"] == 0
    assert store.release_username("ALICE", released_by=1, now=401.0) is True
    assert acc.validate_session_binding(conn, tok) is not None
    assert store.get(alice.id).status == "active"
    assert store.create_member(username="alice", display_name="New Alice", now=402.0).username == "alice"

    # After 7 days, hold expires automatically without admin release.
    expiry = 305.0 + acc.USERNAME_RENAME_HOLD_SECONDS
    assert store.is_username_retired("bob", now=expiry) is False
    assert store.list_retired_usernames(now=expiry) == []
    assert store.create_member(username="Bob", display_name="New Bob", now=expiry).username == "Bob"
