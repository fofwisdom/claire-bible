"""Federation actor keys: ``auth.db`` migration, encrypted storage, and JWK export (Fedify sidecar design Sections 7 and 10.2, unit FP1-2)."""

from __future__ import annotations

import base64
import json
import os
import sqlite3

import pytest
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.asymmetric import ed25519, padding, rsa

from claire import cli
from claire.store import actor_keys as ak
from claire.store import auth_db as adb
from claire.store.secrets import SecretDecryptionError, SecretService


@pytest.fixture(autouse=True)
def _isolate_env(monkeypatch):
    for name in list(os.environ):
        if name.startswith(("CLAIRE_", "CB_")):
            monkeypatch.delenv(name, raising=False)


@pytest.fixture
def conn(tmp_path):
    c = adb.connect_auth_db(adb.auth_db_path(tmp_path))
    yield c
    c.close()


@pytest.fixture
def svc():
    return SecretService(master_key=b"k" * 32)


def _int(value: str) -> int:
    return int.from_bytes(base64.urlsafe_b64decode(value + "=" * (-len(value) % 4)), "big")


def _raw(value: str) -> bytes:
    return base64.urlsafe_b64decode(value + "=" * (-len(value) % 4))


def _downgrade_to_v4(path) -> None:
    raw = sqlite3.connect(path)
    raw.execute("DROP TABLE actor_keys")
    raw.execute("UPDATE meta SET value='4' WHERE key='schema_version'")
    raw.commit()
    raw.close()


def _schema(c: sqlite3.Connection) -> list[tuple]:
    return [tuple(r) for r in c.execute("SELECT type, name, sql FROM sqlite_master WHERE name NOT LIKE 'sqlite_%' ORDER BY name")]


# --- Migration (I/O) ---


def test_migration_from_v4_adds_actor_keys_and_is_idempotent(tmp_path):
    path = adb.auth_db_path(tmp_path)
    adb.connect_auth_db(path).close()
    _downgrade_to_v4(path)

    c = adb.connect_auth_db(path)
    try:
        assert c.execute("SELECT value FROM meta WHERE key='schema_version'").fetchone()[0] == "5"
        columns = [r[1] for r in c.execute("PRAGMA table_info(actor_keys)")]
        assert columns == ["account_id", "alg", "public_jwk", "private_ciphertext", "private_nonce", "key_version", "created_at"]
        before = _schema(c)
        adb.init_auth_db(c)
        adb.init_auth_db(c)
        assert _schema(c) == before
    finally:
        c.close()


def test_e2e_migrate_command_upgrades_v4_and_keeps_stored_keys(monkeypatch, tmp_path, svc):
    monkeypatch.setenv("CLAIRE_APP_ROOT", str(tmp_path))
    monkeypatch.setenv("CLAIRE_DB_PATH", str(tmp_path / "data" / "claire.db"))
    monkeypatch.setenv("CLAIRE_PROVIDER", "mock")
    from claire.config import get_settings

    get_settings.cache_clear()
    try:
        path = adb.auth_db_path(tmp_path / "data")
        adb.connect_auth_db(path).close()
        _downgrade_to_v4(path)
        assert cli.main(["migrate"]) == 0

        c = adb.connect_auth_db(path)
        assert ak.ensure_actor_keys(c, 7, svc) is True
        stored = ak.load_actor_keys(c, 7, svc)
        c.close()

        assert cli.main(["migrate"]) == 0
        c = adb.connect_auth_db(path)
        try:
            assert c.execute("SELECT value FROM meta WHERE key='schema_version'").fetchone()[0] == "5"
            assert ak.load_actor_keys(c, 7, svc) == stored
        finally:
            c.close()
    finally:
        get_settings.cache_clear()


def test_alg_check_and_primary_key(conn):
    row = (1, "rsa2048", "{}", b"c", b"n", 1, 0.0)
    insert = "INSERT INTO actor_keys VALUES (?, ?, ?, ?, ?, ?, ?)"
    conn.execute(insert, row)
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute(insert, row)
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute(insert, (1, "dsa", "{}", b"c", b"n", 1, 0.0))


# --- Encrypted storage (module interface) ---


def test_ensure_generates_both_keys_one_time(conn, svc):
    assert ak.load_actor_keys(conn, 5, svc) == []
    assert ak.ensure_actor_keys(conn, 5, svc, now=100.0) is True
    first = ak.load_actor_keys(conn, 5, svc)
    assert [p.alg for p in first] == ["rsa2048", "ed25519"]
    assert all(p.created_at == 100.0 for p in first)

    assert ak.ensure_actor_keys(conn, 5, svc, now=200.0) is False
    assert ak.load_actor_keys(conn, 5, svc) == first
    assert ak.load_public_jwks(conn, 5) == {p.alg: p.public_jwk for p in first}


def test_ensure_adds_only_the_absent_algorithm(conn, svc):
    ak.ensure_actor_keys(conn, 5, svc)
    rsa_before = ak.load_actor_keys(conn, 5, svc)[0]
    conn.execute("DELETE FROM actor_keys WHERE account_id=5 AND alg='ed25519'")
    conn.commit()
    assert ak.ensure_actor_keys(conn, 5, svc) is True
    after = ak.load_actor_keys(conn, 5, svc)
    assert after[0] == rsa_before and after[1].alg == "ed25519"


def test_accounts_and_instance_actor_have_separate_keys(conn, svc):
    for account_id in (ak.INSTANCE_ACTOR_ACCOUNT_ID, 1, 2):
        ak.ensure_actor_keys(conn, account_id, svc)
    public = [json.dumps(ak.load_public_jwks(conn, a), sort_keys=True) for a in (0, 1, 2)]
    assert len(set(public)) == 3
    assert ak.delete_actor_keys(conn, 1) == 2
    assert ak.load_actor_keys(conn, 1, svc) == []
    assert len(ak.load_actor_keys(conn, 0, svc)) == 2 and len(ak.load_actor_keys(conn, 2, svc)) == 2


@pytest.mark.parametrize("account_id", [-1, True, "1", 1.0])
def test_invalid_account_id_is_refused(conn, svc, account_id):
    with pytest.raises(ValueError):
        ak.ensure_actor_keys(conn, account_id, svc)


def test_private_key_is_encrypted_at_rest_and_hidden_in_repr(conn, svc):
    ak.ensure_actor_keys(conn, 5, svc)
    for pair in ak.load_actor_keys(conn, 5, svc):
        secret = pair.private_jwk["d"]
        assert secret not in repr(pair)
        assert "d" not in pair.public_jwk
        for row in conn.execute("SELECT * FROM actor_keys WHERE account_id=5 AND alg=?", (pair.alg,)):
            for value in tuple(row):
                data = value if isinstance(value, bytes) else str(value).encode()
                assert secret.encode() not in data


def test_private_key_decrypts_only_with_its_account_and_algorithm(conn, svc):
    ak.ensure_actor_keys(conn, 1, svc)
    ak.ensure_actor_keys(conn, 2, svc)

    def swap(where_a: tuple[int, str], where_b: tuple[int, str]) -> None:
        select = "SELECT private_ciphertext, private_nonce FROM actor_keys WHERE account_id=? AND alg=?"
        update = "UPDATE actor_keys SET private_ciphertext=?, private_nonce=? WHERE account_id=? AND alg=?"
        a, b = conn.execute(select, where_a).fetchone(), conn.execute(select, where_b).fetchone()
        conn.execute(update, (*tuple(b), *where_a))
        conn.execute(update, (*tuple(a), *where_b))
        conn.commit()

    # The ciphertext of a different account, same algorithm.
    swap((1, "rsa2048"), (2, "rsa2048"))
    for account_id in (1, 2):
        with pytest.raises(SecretDecryptionError):
            ak.load_actor_keys(conn, account_id, svc)
    swap((1, "rsa2048"), (2, "rsa2048"))
    assert len(ak.load_actor_keys(conn, 1, svc)) == 2

    # The ciphertext of a different algorithm, same account.
    swap((1, "rsa2048"), (1, "ed25519"))
    with pytest.raises(SecretDecryptionError):
        ak.load_actor_keys(conn, 1, svc)
    assert len(ak.load_actor_keys(conn, 2, svc)) == 2


def test_different_master_key_does_not_decrypt(conn, svc):
    ak.ensure_actor_keys(conn, 1, svc)
    with pytest.raises(SecretDecryptionError):
        ak.load_actor_keys(conn, 1, SecretService(master_key=b"x" * 32))


# --- Key generation and JWK export (external library: cryptography) ---


def test_rsa_jwk_is_a_valid_2048_bit_key_pair():
    public, private = ak.generate_key_pair("rsa2048")
    assert set(public) == {"kty", "alg", "n", "e"} and public["kty"] == "RSA"
    assert set(private) == set(public) | {"d", "p", "q", "dp", "dq", "qi"}
    public_numbers = rsa.RSAPublicNumbers(_int(private["e"]), _int(private["n"]))
    key = rsa.RSAPrivateNumbers(_int(private["p"]), _int(private["q"]), _int(private["d"]), _int(private["dp"]), _int(private["dq"]), _int(private["qi"]), public_numbers).private_key()
    assert key.key_size == 2048 and public_numbers.e == 65537
    signature = key.sign(b"message", padding.PKCS1v15(), hashes.SHA256())
    rsa.RSAPublicNumbers(_int(public["e"]), _int(public["n"])).public_key().verify(signature, b"message", padding.PKCS1v15(), hashes.SHA256())


def test_ed25519_jwk_is_a_valid_key_pair():
    public, private = ak.generate_key_pair("ed25519")
    assert public == {"kty": "OKP", "crv": "Ed25519", "alg": "Ed25519", "x": public["x"]}
    assert set(private) == set(public) | {"d"}
    assert "=" not in private["d"] + public["x"]
    key = ed25519.Ed25519PrivateKey.from_private_bytes(_raw(private["d"]))
    ed25519.Ed25519PublicKey.from_public_bytes(_raw(public["x"])).verify(key.sign(b"message"), b"message")


def test_unknown_algorithm_is_refused():
    with pytest.raises(ValueError):
        ak.generate_key_pair("dsa")
