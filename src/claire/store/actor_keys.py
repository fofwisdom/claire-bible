"""Federation actor key pairs in ``auth.db`` table ``actor_keys`` (Fedify sidecar design Sections 7 and 10.2, unit FP1-2).

- Each actor has two key pairs: RSA 2048 for HTTP Signatures and Ed25519 for Object Integrity Proofs.
- The instance actor uses ``account_id`` 0. An account ID is 1 or more.
- The public key is stored as a JWK in plaintext. The private key is stored as a JWK that ``SecretService`` encrypts with the additional authenticated data ``actor_key|{account_id}|{alg}``. A ciphertext that is moved to a different account or algorithm does not decrypt.
- This module does not write a private key to a log. ``ActorKeyPair`` hides the private JWK in its ``repr``.
"""

from __future__ import annotations

import base64
import json
import sqlite3
import time
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, Final

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import ed25519, rsa

if TYPE_CHECKING:
    from .secrets import SecretService

__all__ = [
    "ACTOR_KEY_ALGS",
    "INSTANCE_ACTOR_ACCOUNT_ID",
    "ActorKeyPair",
    "delete_actor_keys",
    "ensure_actor_keys",
    "generate_key_pair",
    "load_actor_keys",
    "load_public_jwks",
]

INSTANCE_ACTOR_ACCOUNT_ID: Final[int] = 0
ACTOR_KEY_ALGS: Final[tuple[str, ...]] = ("rsa2048", "ed25519")


@dataclass(frozen=True, slots=True)
class ActorKeyPair:
    alg: str
    public_jwk: dict[str, str]
    private_jwk: dict[str, str] = field(repr=False)
    created_at: float = 0.0


def _b64u(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode("ascii")


def _b64u_int(value: int) -> str:
    return _b64u(value.to_bytes((value.bit_length() + 7) // 8, "big"))


def generate_key_pair(alg: str) -> tuple[dict[str, str], dict[str, str]]:
    """Generate one key pair. Return ``(public JWK, private JWK)``."""
    if alg == "rsa2048":
        numbers = rsa.generate_private_key(public_exponent=65537, key_size=2048).private_numbers()
        public = {"kty": "RSA", "alg": "RS256", "n": _b64u_int(numbers.public_numbers.n), "e": _b64u_int(numbers.public_numbers.e)}
        private = {**public, "d": _b64u_int(numbers.d), "p": _b64u_int(numbers.p), "q": _b64u_int(numbers.q), "dp": _b64u_int(numbers.dmp1), "dq": _b64u_int(numbers.dmq1), "qi": _b64u_int(numbers.iqmp)}
        return public, private
    if alg == "ed25519":
        key = ed25519.Ed25519PrivateKey.generate()
        public_raw = key.public_key().public_bytes(serialization.Encoding.Raw, serialization.PublicFormat.Raw)
        private_raw = key.private_bytes(serialization.Encoding.Raw, serialization.PrivateFormat.Raw, serialization.NoEncryption())
        public = {"kty": "OKP", "crv": "Ed25519", "alg": "Ed25519", "x": _b64u(public_raw)}
        return public, {**public, "d": _b64u(private_raw)}
    raise ValueError(f"unknown actor key algorithm: {alg}")


def _aad(account_id: int, alg: str) -> str:
    return f"actor_key|{int(account_id)}|{alg}"


def _check_account_id(account_id: int) -> int:
    if isinstance(account_id, bool) or not isinstance(account_id, int) or account_id < 0:
        raise ValueError("account_id must be 0 (instance actor) or a positive account ID")
    return account_id


def ensure_actor_keys(conn: sqlite3.Connection, account_id: int, secret_service: SecretService, *, now: float | None = None) -> bool:
    """Generate and store the key pairs that the actor does not have. Return ``True`` when this call stored a key.

    An existing key is not replaced.
    """
    _check_account_id(account_id)
    existing = {str(r[0]) for r in conn.execute("SELECT alg FROM actor_keys WHERE account_id=?", (account_id,))}
    missing = [alg for alg in ACTOR_KEY_ALGS if alg not in existing]
    if not missing:
        return False
    rows: list[tuple[Any, ...]] = []
    created_at = time.time() if now is None else float(now)
    for alg in missing:
        public, private = generate_key_pair(alg)
        sealed = secret_service.encrypt(json.dumps(private, separators=(",", ":")), _aad(account_id, alg))
        rows.append((account_id, alg, json.dumps(public, separators=(",", ":")), sealed.ciphertext, sealed.nonce, sealed.key_version, created_at))
    stored = 0
    with conn:
        for row in rows:
            # A concurrent caller can store the same key first. Its key stays.
            stored += conn.execute(
                "INSERT OR IGNORE INTO actor_keys(account_id, alg, public_jwk, private_ciphertext, private_nonce, key_version, created_at) VALUES (?, ?, ?, ?, ?, ?, ?)",
                row,
            ).rowcount
    return stored > 0


def load_public_jwks(conn: sqlite3.Connection, account_id: int) -> dict[str, dict[str, str]]:
    """Return the public JWK of each stored algorithm. This function does not decrypt."""
    rows = conn.execute("SELECT alg, public_jwk FROM actor_keys WHERE account_id=?", (_check_account_id(account_id),)).fetchall()
    return {str(alg): json.loads(public_jwk) for alg, public_jwk in rows}


def load_actor_keys(conn: sqlite3.Connection, account_id: int, secret_service: SecretService) -> list[ActorKeyPair]:
    """Return the decrypted key pairs of the actor in the order of ``ACTOR_KEY_ALGS``. The list is empty when the actor has no keys.

    Raises ``SecretDecryptionError`` when a ciphertext does not belong to its row.
    """
    rows = conn.execute(
        "SELECT alg, public_jwk, private_ciphertext, private_nonce, key_version, created_at FROM actor_keys WHERE account_id=?",
        (_check_account_id(account_id),),
    ).fetchall()
    pairs: list[ActorKeyPair] = []
    for alg, public_jwk, ciphertext, nonce, key_version, created_at in sorted(rows, key=lambda r: ACTOR_KEY_ALGS.index(str(r[0]))):
        plaintext = secret_service.decrypt(bytes(ciphertext), bytes(nonce), _aad(account_id, str(alg)), int(key_version))
        pairs.append(ActorKeyPair(alg=str(alg), public_jwk=json.loads(public_jwk), private_jwk=json.loads(plaintext), created_at=float(created_at)))
    return pairs


def delete_actor_keys(conn: sqlite3.Connection, account_id: int) -> int:
    """Delete all keys of the actor. Return the number of deleted rows."""
    with conn:
        return conn.execute("DELETE FROM actor_keys WHERE account_id=?", (_check_account_id(account_id),)).rowcount
