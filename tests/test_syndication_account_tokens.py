"""Unit tests for syndication account token encryption and theme isolation."""

from __future__ import annotations

import sqlite3
import pytest

from claire.store import db as dbm
from claire.store.secrets import SecretDecryptionError, SecretService


@pytest.fixture
def memory_db() -> sqlite3.Connection:
    """Create an in-memory SQLite database with initialized schema."""
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    dbm.init_db(conn)
    return conn


@pytest.fixture
def secret_service() -> SecretService:
    """Create a SecretService with a deterministic test master key."""
    master_key = b"01234567890123456789012345678901"
    return SecretService(master_key=master_key)


def test_syndication_account_token_encryption_and_decryption(memory_db: sqlite3.Connection, secret_service: SecretService) -> None:
    """Verify that syndication account tokens encrypt and decrypt with AAD binding."""
    theme_id = 1
    raw_token = "secret-token-alpha-12345"

    account_id = dbm.create_syndication_account(
        memory_db,
        theme_id=theme_id,
        protocol="activitypub",
        server_url="https://mastodon.social",
        handle="@testuser@mastodon.social",
        auth_kind="oauth2",
        scopes="read write",
        token_str=raw_token,
        secret_service=secret_service,
    )

    row = dbm.get_syndication_account(memory_db, account_id, theme_id=theme_id)
    assert row is not None
    assert row["token_enc"] is not None
    assert row["token_enc"] != raw_token.encode("utf-8")
    assert row["token_nonce"] is not None
    assert len(row["token_nonce"]) == 12
    assert row["token_key_version"] == 1

    decrypted = dbm.load_syndication_account_token(
        memory_db, account_id, theme_id=theme_id, secret_service=secret_service
    )
    assert decrypted == raw_token

    updated_token = "updated-secret-token-67890"
    dbm.save_syndication_account_token(
        memory_db, account_id, theme_id, updated_token, secret_service=secret_service
    )

    decrypted_updated = dbm.load_syndication_account_token(
        memory_db, account_id, theme_id=theme_id, secret_service=secret_service
    )
    assert decrypted_updated == updated_token


def test_cross_theme_tampering_raises_secret_decryption_error(memory_db: sqlite3.Connection, secret_service: SecretService) -> None:
    """Verify that changing theme_id causes AAD mismatch and raises SecretDecryptionError."""
    source_theme_id = 1
    target_theme_id = 2
    raw_token = "super-secret-token-bound-to-theme-1"

    account_id = dbm.create_syndication_account(
        memory_db,
        theme_id=source_theme_id,
        protocol="bluesky",
        server_url="https://bsky.social",
        handle="user.bsky.social",
        auth_kind="password",
        scopes="atproto",
        token_str=raw_token,
        secret_service=secret_service,
    )

    row = dbm.get_syndication_account(memory_db, account_id, theme_id=source_theme_id)
    assert row is not None
    ciphertext = row["token_enc"]
    nonce = row["token_nonce"]
    key_version = row["token_key_version"]

    with pytest.raises(SecretDecryptionError):
        secret_service.decrypt(
            ciphertext,
            nonce,
            aad=f"{target_theme_id}|{account_id}",
            key_version=key_version,
        )

    memory_db.execute(
        "UPDATE syndication_accounts SET theme_id = ? WHERE id = ?",
        (target_theme_id, account_id),
    )
    memory_db.commit()

    with pytest.raises(SecretDecryptionError):
        dbm.load_syndication_account_token(
            memory_db, account_id, theme_id=target_theme_id, secret_service=secret_service
        )


def test_cross_theme_isolation_get_and_list_syndication_account(memory_db: sqlite3.Connection, secret_service: SecretService) -> None:
    """Verify cross-theme isolation in get_syndication_account and list_syndication_accounts."""
    theme1_id = 10
    theme2_id = 20

    aid1 = dbm.create_syndication_account(
        memory_db,
        theme_id=theme1_id,
        protocol="activitypub",
        server_url="https://social.example.org",
        handle="@t1@social.example.org",
        auth_kind="token",
        scopes="all",
        token_str="tok1",
        secret_service=secret_service,
    )

    aid2 = dbm.create_syndication_account(
        memory_db,
        theme_id=theme2_id,
        protocol="activitypub",
        server_url="https://other.example.org",
        handle="@t2@other.example.org",
        auth_kind="token",
        scopes="all",
        token_str="tok2",
        secret_service=secret_service,
    )

    assert dbm.get_syndication_account(memory_db, aid1, theme_id=theme1_id) is not None
    assert dbm.get_syndication_account(memory_db, aid1, theme_id=theme2_id) is None
    assert dbm.get_syndication_account(memory_db, aid2, theme_id=theme2_id) is not None
    assert dbm.get_syndication_account(memory_db, aid2, theme_id=theme1_id) is None

    with pytest.raises(ValueError, match="not found for theme"):
        dbm.load_syndication_account_token(
            memory_db, aid1, theme_id=theme2_id, secret_service=secret_service
        )

    list_t1 = dbm.list_syndication_accounts(memory_db, theme_id=theme1_id)
    assert len(list_t1) == 1
    assert list_t1[0]["id"] == aid1

    list_t2 = dbm.list_syndication_accounts(memory_db, theme_id=theme2_id)
    assert len(list_t2) == 1
    assert list_t2[0]["id"] == aid2

    list_all = dbm.list_syndication_accounts(memory_db)
    assert len(list_all) >= 2


def test_syndication_dispatch_dispatched_by_account_id(memory_db: sqlite3.Connection) -> None:
    """Verify storing, retrieving, and filtering dispatched_by_account_id in syndication dispatches."""
    d1 = dbm.create_syndication_dispatch(
        memory_db,
        theme_id=1,
        target_theme_id=2,
        document_id="doc_1",
        dispatched_by_account_id=101,
        mode="cross_theme",
        status="pending",
    )

    d2 = dbm.create_syndication_dispatch(
        memory_db,
        theme_id=1,
        target_theme_id=2,
        document_id="doc_2",
        dispatched_by_account_id=202,
        mode="cross_theme",
        status="done",
    )

    d3 = dbm.create_syndication_dispatch(
        memory_db,
        theme_id=1,
        target_theme_id=2,
        document_id="doc_3",
        dispatched_by_account_id=None,
        mode="cross_theme",
        status="done",
    )

    r1 = dbm.get_syndication_dispatch(memory_db, d1)
    assert r1 is not None
    assert r1["dispatched_by_account_id"] == 101

    r2 = dbm.get_syndication_dispatch(memory_db, d2)
    assert r2 is not None
    assert r2["dispatched_by_account_id"] == 202

    r3 = dbm.get_syndication_dispatch(memory_db, d3)
    assert r3 is not None
    assert r3["dispatched_by_account_id"] is None

    filtered_101 = dbm.list_syndication_dispatches(memory_db, dispatched_by_account_id=101)
    assert len(filtered_101) == 1
    assert filtered_101[0]["id"] == d1

    filtered_202 = dbm.list_syndication_dispatches(memory_db, dispatched_by_account_id=202)
    assert len(filtered_202) == 1
    assert filtered_202[0]["id"] == d2
