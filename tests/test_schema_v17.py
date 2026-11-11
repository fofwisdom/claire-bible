"""Schema v17: federation followers, tombstones, and domain blocks, with the store functions (Fedify sidecar design Section 10.1, unit FP2-1)."""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from claire import ids
from claire.store import db as dbm
from claire.store import federation as fed

NOW = 1_800_000_400.0
BOB = "https://remote.example/users/bob"
BOB_INBOX = "https://remote.example/users/bob/inbox"
SHARED_INBOX = "https://remote.example/inbox"


def _salt(conn: sqlite3.Connection) -> bytes:
    return bytes.fromhex(conn.execute("SELECT value FROM instance_settings WHERE key='id_salt'").fetchone()["value"])


def _add_account(conn: sqlite3.Connection, public_id: int, username: str) -> int:
    cur = conn.execute("INSERT INTO accounts (public_id, username, role, status, display_name, profile_public, created_at, activated_at, updated_at) VALUES (?, ?, 'member', 'active', '', 1, ?, ?, ?)", (public_id, username, NOW, NOW, NOW))
    return int(cur.lastrowid)


@pytest.fixture
def conn(tmp_path: Path):
    connection = dbm.connect(tmp_path / "claire.db")
    dbm.init_db(connection)
    yield connection
    connection.close()


def _follow(conn: sqlite3.Connection, account_id: int, actor: str = BOB, *, inbox: str = BOB_INBOX, shared: str | None = SHARED_INBOX, now: float = NOW) -> tuple[fed.Follower, bool]:
    return fed.upsert_follower(conn, account_id=account_id, actor_iri=actor, inbox_iri=inbox, shared_inbox_iri=shared, salt=_salt(conn), now=now)


def _make_v16_database(path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    with monkeypatch.context() as m:
        m.setattr(dbm, "SCHEMA_VERSION", 16)
        m.setattr(dbm, "_migrate_v17_federation", lambda conn: None)
        conn = dbm.connect(path)
        try:
            dbm.init_db(conn)
            assert dbm.stored_schema_version(conn) == 16
            _add_account(conn, 116412345678901234, "alice")
            conn.commit()
        finally:
            conn.close()


def _tables(conn: sqlite3.Connection) -> set[str]:
    return {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}


def test_v16_database_migrates_and_the_migration_is_idempotent(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    path = tmp_path / "claire.db"
    _make_v16_database(path, monkeypatch)
    conn = dbm.connect(path)
    try:
        assert not {"federation_followers", "federation_tombstones", "federation_domain_blocks"} & _tables(conn)
        dbm.init_db(conn)
        assert dbm.SCHEMA_VERSION == 17 and dbm.require_current_schema(conn) == 17
        assert {"federation_followers", "federation_tombstones", "federation_domain_blocks"} <= _tables(conn)
        # The tables of phase FP3 are not in this version.
        assert not {"federation_objects", "federation_outbox"} & _tables(conn)
        assert conn.execute("SELECT username FROM accounts WHERE public_id=116412345678901234").fetchone()[0] == "alice"
        account_id = conn.execute("SELECT id FROM accounts WHERE username='alice'").fetchone()[0]
        _follow(conn, account_id)
        fed.add_tombstone(conn, 5, "actor", gone_at=NOW)
        fed.add_domain_block(conn, "bad.example", created_by=dbm.ADMIN_ACCOUNT_ID, now=NOW)
        conn.commit()
        first = list(conn.iterdump())
        dbm.init_db(conn)
        dbm.init_db(conn)
        assert list(conn.iterdump()) == first
    finally:
        conn.close()


def test_follower_key_is_the_account_and_the_actor(conn: sqlite3.Connection) -> None:
    alice, carol = _add_account(conn, 11, "alice"), _add_account(conn, 12, "carol")
    assert [r["name"] for r in conn.execute("PRAGMA table_info(federation_followers)") if r["pk"]] == ["account_id", "actor_iri"]

    first, made = _follow(conn, alice)
    assert made and first.public_id >= ids.MIN_CURRENT_NUMBER and first.created_at == NOW
    again, made_again = _follow(conn, alice, now=NOW + 50)
    assert not made_again and again == first

    # New inbox addresses replace the old ones. The row and its number stay.
    moved, made_moved = _follow(conn, alice, inbox="https://remote.example/new-inbox", shared=None, now=NOW + 60)
    assert not made_moved
    assert (moved.public_id, moved.created_at, moved.inbox_iri, moved.shared_inbox_iri, moved.updated_at) == (first.public_id, NOW, "https://remote.example/new-inbox", None, NOW + 60)
    assert fed.count_followers(conn, alice) == 1

    # The same remote actor is a separate follower of a second account.
    other, made_other = _follow(conn, carol)
    assert made_other and other.public_id != first.public_id
    assert (fed.count_followers(conn, alice), fed.count_followers(conn, carol)) == (1, 1)

    with pytest.raises(sqlite3.IntegrityError):
        conn.execute("INSERT INTO federation_followers (public_id, account_id, actor_iri, inbox_iri, created_at, updated_at) VALUES (1, ?, ?, 'x', 0, 0)", (alice, BOB))


def test_follower_removal_update_and_account_cascade(conn: sqlite3.Connection) -> None:
    alice, carol = _add_account(conn, 11, "alice"), _add_account(conn, 12, "carol")
    _follow(conn, alice)
    _follow(conn, carol)
    _follow(conn, alice, "https://other.example/users/dan", inbox="https://other.example/users/dan/inbox", shared=None)

    assert fed.update_follower_inboxes(conn, BOB, inbox_iri="https://remote.example/i2", shared_inbox_iri=None, now=NOW + 5) == 2
    assert fed.update_follower_inboxes(conn, BOB, inbox_iri="https://remote.example/i2", shared_inbox_iri=None, now=NOW + 9) == 0
    assert fed.get_follower(conn, carol, BOB).updated_at == NOW + 5

    assert fed.remove_follower(conn, alice, BOB) is True
    assert fed.remove_follower(conn, alice, BOB) is False
    assert fed.count_followers(conn, alice) == 1
    assert fed.remove_followers_of_actor(conn, BOB) == 1
    assert fed.count_followers(conn, carol) == 0

    conn.execute("DELETE FROM accounts WHERE id=?", (alice,))
    assert conn.execute("SELECT COUNT(*) FROM federation_followers").fetchone()[0] == 0


def test_follower_pages_use_the_collection_cursors(conn: sqlite3.Connection) -> None:
    alice = _add_account(conn, 11, "alice")
    numbers = [_follow(conn, alice, f"https://remote.example/users/u{i}", now=NOW + i)[0].public_id for i in range(5)]
    assert numbers == sorted(numbers)

    def page(**cursor: int) -> list[int]:
        return [f.public_id for f in fed.list_followers(conn, alice, limit=2, **cursor)]

    assert page() == [numbers[4], numbers[3]]
    assert page(max_id=numbers[3]) == [numbers[2], numbers[1]]
    assert page(since_id=numbers[0]) == [numbers[4], numbers[3]]
    assert page(min_id=numbers[0]) == [numbers[2], numbers[1]]
    assert page(max_id=numbers[0]) == []


def test_tombstone_keeps_its_first_time_and_has_no_account_reference(conn: sqlite3.Connection) -> None:
    assert fed.get_tombstone(conn, 77) is None
    assert fed.add_tombstone(conn, 77, "actor", gone_at=NOW) is True
    assert fed.add_tombstone(conn, 77, "actor", gone_at=NOW + 10) is False
    assert fed.get_tombstone(conn, 77) == fed.Tombstone(77, "actor", NOW)
    assert {r["name"] for r in conn.execute("PRAGMA table_info(federation_tombstones)")} == {"public_id", "kind", "gone_at"}
    assert conn.execute("PRAGMA foreign_key_list(federation_tombstones)").fetchall() == []
    with pytest.raises(sqlite3.IntegrityError):
        fed.add_tombstone(conn, 78, "theme", gone_at=NOW)  # type: ignore[arg-type]


def test_domain_blocks_are_normalized_and_apply_to_subdomains(conn: sqlite3.Connection) -> None:
    block, made = fed.add_domain_block(conn, "  Bad.Example. ", created_by=dbm.ADMIN_ACCOUNT_ID, note="spam", now=NOW)
    assert made and block == fed.DomainBlock("bad.example", NOW, dbm.ADMIN_ACCOUNT_ID, "spam")
    assert fed.add_domain_block(conn, "BAD.example", created_by=dbm.ADMIN_ACCOUNT_ID, note="other", now=NOW + 1) == (block, False)
    assert fed.add_domain_block(conn, "bücher.example", created_by=dbm.ADMIN_ACCOUNT_ID, now=NOW)[0].domain == "xn--bcher-kva.example"
    fed.add_domain_block(conn, "192.0.2.7", created_by=dbm.ADMIN_ACCOUNT_ID, now=NOW)
    assert [b.domain for b in fed.list_domain_blocks(conn)] == ["192.0.2.7", "bad.example", "xn--bcher-kva.example"]

    assert fed.blocking_domain(conn, fed.iri_host("https://bad.example/users/x")) == "bad.example"
    assert fed.blocking_domain(conn, fed.iri_host("https://Deep.Sub.BAD.example:8443/users/x")) == "bad.example"
    assert fed.blocking_domain(conn, fed.iri_host("https://notbad.example/users/x")) is None
    assert fed.blocking_domain(conn, fed.iri_host("https://example/users/x")) is None
    assert fed.blocking_domain(conn, fed.iri_host("http://192.0.2.7:8766/users/x")) == "192.0.2.7"
    assert fed.blocking_domain(conn, "2.7") is None

    assert fed.remove_domain_block(conn, "Bad.Example") is True
    assert fed.remove_domain_block(conn, "bad.example") is False
    assert fed.blocking_domain(conn, "bad.example") is None


@pytest.mark.parametrize("raw", ["", " ", "https://bad.example", "bad.example/path", "bad.example:443", "bad..example", "-bad.example", "bad example", "a" * 64 + ".example", "user@bad.example"])
def test_invalid_domain_is_refused(raw: str) -> None:
    with pytest.raises(ValueError):
        fed.normalize_domain(raw)


@pytest.mark.parametrize("iri", ["", "bad.example/users/x", "ftp://bad.example/x", "https:///x", "mailto:bob@bad.example", "https://bad..example/x", "https://[::1"])
def test_iri_without_a_valid_host_has_no_host(iri: str) -> None:
    assert fed.iri_host(iri) is None


def test_domain_blocks_etag_changes_with_the_list_only() -> None:
    tag = fed.domain_blocks_etag(["b.example", "a.example"])
    assert tag == fed.domain_blocks_etag(["a.example", "b.example"])
    assert tag != fed.domain_blocks_etag(["a.example"]) != fed.domain_blocks_etag([])
    assert tag.startswith('"') and tag.endswith('"')
