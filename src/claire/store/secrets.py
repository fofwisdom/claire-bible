"""Secret management and AES-256-GCM envelope encryption service."""

from __future__ import annotations

import hmac
import os
import secrets
import stat
from dataclasses import dataclass
from hashlib import sha256
from pathlib import Path
from typing import Final

from cryptography.hazmat.primitives.ciphers.aead import AESGCM

DEFAULT_KEY_VERSION: Final[int] = 1


class SecretError(Exception):
    """Base exception for secret management operations."""


class SecretDecryptionError(SecretError):
    """Raised when decryption fails due to ciphertext or AAD mismatch."""


@dataclass(frozen=True, slots=True)
class EncryptedSecret:
    ciphertext: bytes
    nonce: bytes
    key_version: int = DEFAULT_KEY_VERSION


class SecretService:
    """Provides AES-256-GCM encryption, HMAC derivation, and fingerprints."""

    def __init__(self, master_key: bytes | None = None, key_path: Path | None = None) -> None:
        if master_key is not None:
            if len(master_key) < 32:
                raise SecretError("Master key must be at least 32 bytes.")
            self._master_key = master_key[:32]
        else:
            self._master_key = self._resolve_master_key(key_path)

    @classmethod
    def _resolve_master_key(cls, key_path: Path | None = None) -> bytes:
        env_key = os.environ.get("CLAIRE_SECRET_KEY")
        if env_key:
            env_key_str = env_key.strip()
            if len(env_key_str) == 64:
                try:
                    return bytes.fromhex(env_key_str)
                except ValueError:
                    pass
            return sha256(env_key_str.encode("utf-8")).digest()

        path = key_path or Path("data/auth/master.key")
        if path.exists():
            try:
                os.chmod(path, 0o600)
            except OSError:
                pass
            raw = path.read_bytes()
            raw_str = raw.decode("utf-8", errors="ignore").strip()
            if len(raw_str) == 64:
                try:
                    return bytes.fromhex(raw_str)
                except ValueError:
                    pass
            if len(raw) < 32:
                raise SecretError(f"Master key file at {path} contains fewer than 32 bytes")
            return raw[:32]

        new_key = secrets.token_bytes(32)
        path.parent.mkdir(parents=True, exist_ok=True)
        try:
            os.chmod(path.parent, 0o700)
        except OSError:
            pass
        fd = os.open(str(path), os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with open(fd, "wb") as f:
            f.write(new_key)
        try:
            os.chmod(path, 0o600)
        except OSError:
            pass
        return new_key

    def derive_hmac_key(self, purpose: str) -> bytes:
        """Derive purpose-specific HMAC key using HMAC-SHA256."""
        return hmac.new(self._master_key, f"claire:{purpose}".encode("utf-8"), sha256).digest()

    def encrypt(self, plaintext: bytes | str, aad: bytes | str, key_version: int = DEFAULT_KEY_VERSION) -> EncryptedSecret:
        """Encrypt plaintext with AES-256-GCM binding AAD."""
        raw_plaintext = plaintext.encode("utf-8") if isinstance(plaintext, str) else plaintext
        raw_aad = aad.encode("utf-8") if isinstance(aad, str) else aad
        aesgcm = AESGCM(self._master_key)
        nonce = secrets.token_bytes(12)
        ciphertext = aesgcm.encrypt(nonce, raw_plaintext, raw_aad)
        return EncryptedSecret(ciphertext=ciphertext, nonce=nonce, key_version=key_version)

    def decrypt(self, ciphertext: bytes, nonce: bytes, aad: bytes | str, key_version: int = DEFAULT_KEY_VERSION) -> bytes:
        """Decrypt AES-256-GCM ciphertext verifying AAD."""
        if key_version != DEFAULT_KEY_VERSION:
            raise SecretDecryptionError(f"Unsupported key version: {key_version}")
        raw_aad = aad.encode("utf-8") if isinstance(aad, str) else aad
        aesgcm = AESGCM(self._master_key)
        try:
            return aesgcm.decrypt(nonce, ciphertext, raw_aad)
        except Exception as e:
            raise SecretDecryptionError("Decryption failed or AAD mismatch") from None

    def compute_fingerprint(self, secret: str | None = None, purpose: str = "key_fingerprint", *, value: str | None = None) -> str:
        """Compute an 8-char opaque fingerprint from derived HMAC (never revealing plaintext)."""
        secret_val = secret if secret is not None else value
        if secret_val is None:
            raise ValueError("secret must be provided")
        derived_key = self.derive_hmac_key(purpose)
        digest = hmac.new(derived_key, secret_val.encode("utf-8"), sha256).hexdigest()
        return digest[:8]

    def hmac_index(self, token: str | None = None, purpose: str = "", *, value: str | None = None) -> str:
        """Compute hex HMAC for indexed search."""
        token_val = token if token is not None else value
        if token_val is None:
            raise ValueError("token must be provided")
        derived_key = self.derive_hmac_key(purpose)
        return hmac.new(derived_key, token_val.encode("utf-8"), sha256).hexdigest()


_GLOBAL_SECRET_SERVICE: SecretService | None = None


def get_secret_service(key_path: Path | None = None, *, reload: bool = False) -> SecretService:
    global _GLOBAL_SECRET_SERVICE
    if _GLOBAL_SECRET_SERVICE is None or reload or key_path is not None:
        service = SecretService(key_path=key_path)
        if key_path is None and not reload:
            _GLOBAL_SECRET_SERVICE = service
        return service
    return _GLOBAL_SECRET_SERVICE


def reset_secret_service() -> None:
    global _GLOBAL_SECRET_SERVICE
    _GLOBAL_SECRET_SERVICE = None
