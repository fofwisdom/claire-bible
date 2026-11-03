"""Tests for secret service and backup integrity.

Contracts covered:
- AES-256-GCM encryption/decryption with AAD binding and cross-decryption failure.
- Master key generation and loading with 0600 permissions and CLAIRE_SECRET_KEY resolution.
- Key derivation, opaque fingerprints, and canary leak prevention.
- Backup archive exclusion of master.key and live WAL online snapshot of auth.db.
- Restore preservation of existing master.key and rejection of archives containing master.key.
"""

from __future__ import annotations

import json
import logging
import os
import re
import stat
import subprocess
import tarfile
from contextlib import contextmanager
from pathlib import Path, PurePosixPath
from unittest.mock import patch

import pytest

from claire.ontology.base import Document
from claire.store import auth_db as adb
from claire.store import db as dbm
from claire.store.secrets import (
    DEFAULT_KEY_VERSION,
    SecretDecryptionError,
    SecretError,
    SecretService,
    get_secret_service,
    reset_secret_service,
)
from ops import cb_manuscript as cb

_REAL_SUBPROCESS_RUN = subprocess.run


class _DockerHarness:
    """Record Docker commands and bypass Docker in unit tests."""

    def __init__(self, *, running: tuple[str, ...] = (), migrate_returncode: int = 0, liveness_returncode: int = 0) -> None:
        self.running = running
        self.migrate_returncode = migrate_returncode
        self.liveness_returncode = liveness_returncode
        self.commands: list[list[str]] = []

    def __call__(self, argv, **kwargs):
        command = [str(part) for part in argv]
        if not command or command[0] != "docker":
            return _REAL_SUBPROCESS_RUN(command, **kwargs)
        self.commands.append(command)
        if command[:3] == ["docker", "ps", "-q"]:
            return subprocess.CompletedProcess(command, 0, stdout="", stderr="")
        if command[:2] == ["docker", "compose"] and "ps" in command and "-q" in command:
            stdout = "".join(f"{container}\n" for container in self.running)
            return subprocess.CompletedProcess(command, 0, stdout=stdout, stderr="")
        if command[-2:] == ["claire", "migrate"]:
            return subprocess.CompletedProcess(command, self.migrate_returncode, stdout="", stderr="")
        if command[-2:] == ["claire", "liveness"]:
            return subprocess.CompletedProcess(command, self.liveness_returncode, stdout="", stderr="")
        return subprocess.CompletedProcess(command, 0, stdout="", stderr="")


@contextmanager
def _docker(harness: _DockerHarness):
    with patch.object(cb.subprocess, "run", side_effect=harness):
        yield harness


def _setup_test_layout(root: Path) -> None:
    root.mkdir(parents=True, exist_ok=True)
    prod = "\n".join(
        (
            "CLAIRE_ENVIRONMENT=production",
            "CB_PROJECT_NAME=claire-bible",
            "CB_WAIT_TIMEOUT=45",
            "CB_API_BIND=127.0.0.1",
            "CB_API_PORT=8765",
            "CB_DATA_DIR=./data",
            "CB_VAULT_DIR=./vault",
            "CLAIRE_DB_PATH=data/claire.db",
            "CLAIRE_INJECT_TOKEN=" + ("s" * 32),
            "CLAIRE_PUBLIC_URL=https://claire.example.com/",
            "CLAIRE_CORS_ALLOWED_ORIGINS=",
            "TELEGRAM_BOT_TOKEN=",
            "",
        )
    )
    (root / ".env.example").write_text(prod, encoding="utf-8")
    (root / ".env").write_text(prod, encoding="utf-8")
    schema_source = root / "src" / "claire" / "store" / "db.py"
    schema_source.parent.mkdir(parents=True, exist_ok=True)
    schema_source.write_text(
        f"SCHEMA_VERSION = {dbm.SCHEMA_VERSION}\nSCHEMA_LINEAGE = {dbm.SCHEMA_LINEAGE!r}\n",
        encoding="utf-8",
    )
    (root / "docker-compose.yml").write_text("services:\n  api:\n    image: test\n", encoding="utf-8")


@pytest.fixture(autouse=True)
def _isolate_env(monkeypatch: pytest.MonkeyPatch):
    reset_secret_service()
    for key in list(os.environ):
        if key.startswith("CLAIRE_"):
            monkeypatch.delenv(key, raising=False)
    yield
    reset_secret_service()


def test_aes_gcm_aad_binding_and_cross_decryption_failure():
    """Verify AES-256-GCM encryption, decryption, and strict AAD binding."""
    key = os.urandom(32)
    service = SecretService(master_key=key)

    secret_text = "sk-provider-secret-key-12345"
    aad_account1 = "account:1|provider:gemini|key_id:k1"
    aad_account2 = "account:2|provider:gemini|key_id:k1"

    encrypted = service.encrypt(secret_text, aad_account1)
    assert encrypted.key_version == DEFAULT_KEY_VERSION
    assert len(encrypted.nonce) == 12
    assert encrypted.ciphertext != secret_text.encode("utf-8")

    decrypted = service.decrypt(encrypted.ciphertext, encrypted.nonce, aad_account1)
    assert decrypted.decode("utf-8") == secret_text

    with pytest.raises(SecretDecryptionError):
        service.decrypt(encrypted.ciphertext, encrypted.nonce, aad_account2)

    corrupted_ciphertext = bytearray(encrypted.ciphertext)
    corrupted_ciphertext[0] ^= 0xFF
    with pytest.raises(SecretDecryptionError):
        service.decrypt(bytes(corrupted_ciphertext), encrypted.nonce, aad_account1)

    corrupted_nonce = bytearray(encrypted.nonce)
    corrupted_nonce[0] ^= 0xFF
    with pytest.raises(SecretDecryptionError):
        service.decrypt(encrypted.ciphertext, bytes(corrupted_nonce), aad_account1)

    with pytest.raises(SecretDecryptionError):
        service.decrypt(encrypted.ciphertext, encrypted.nonce, aad_account1, key_version=999)


def test_master_key_generation_and_loading_permissions(tmp_path: Path):
    """Verify master key generation with 0600 permissions and CLAIRE_SECRET_KEY resolution."""
    key_file = tmp_path / "auth" / "master.key"
    assert not key_file.exists()

    service = SecretService(key_path=key_file)
    assert key_file.exists()
    assert stat.S_IMODE(key_file.stat().st_mode) == 0o600
    assert stat.S_IMODE(key_file.parent.stat().st_mode) == 0o700
    key_bytes = key_file.read_bytes()
    assert len(key_bytes) == 32

    reloaded_service = SecretService(key_path=key_file)
    assert reloaded_service._master_key == key_bytes

    env_hex = "a" * 64
    with patch.dict(os.environ, {"CLAIRE_SECRET_KEY": env_hex}):
        env_service = SecretService()
        assert env_service._master_key == bytes.fromhex(env_hex)

    env_pass = "some-arbitrary-passphrase"
    with patch.dict(os.environ, {"CLAIRE_SECRET_KEY": env_pass}):
        pass_service = SecretService()
        assert len(pass_service._master_key) == 32

    short_key_file = tmp_path / "short.key"
    short_key_file.write_bytes(b"too-short")
    with pytest.raises(SecretError):
        SecretService(key_path=short_key_file)


def test_derived_hmac_and_fingerprints_and_canary_leak_prevention(caplog: pytest.LogCaptureFixture):
    """Verify purpose-derived HMAC keys, 8-char opaque fingerprints, and canary leak prevention."""
    service = SecretService(master_key=b"\x07" * 32)

    key_auth = service.derive_hmac_key("auth_index")
    key_tokens = service.derive_hmac_key("tokens")
    assert len(key_auth) == 32
    assert len(key_tokens) == 32
    assert key_auth != key_tokens

    canary = "CANARY_SECRET_VALUE_DO_NOT_LEAK_987654321"

    fingerprint = service.compute_fingerprint(canary)
    assert len(fingerprint) == 8
    assert re.fullmatch(r"[0-9a-f]{8}", fingerprint) is not None
    assert canary not in fingerprint

    same_fp = service.compute_fingerprint(secret=canary)
    assert same_fp == fingerprint

    idx = service.hmac_index(canary, "invitation_search")
    assert len(idx) == 64
    assert re.fullmatch(r"[0-9a-f]{64}", idx) is not None
    assert canary not in idx

    encrypted = service.encrypt(canary, "aad-test")
    with caplog.at_level(logging.DEBUG):
        with pytest.raises(SecretDecryptionError) as exc_info:
            service.decrypt(encrypted.ciphertext, encrypted.nonce, "wrong-aad")
        assert canary not in str(exc_info.value)
        assert canary not in caplog.text


def test_backup_archive_excludes_master_key_and_includes_auth_db(tmp_path: Path, capsys: pytest.CaptureFixture[str]):
    """E2E: Verify manuscript backup archive excludes master.key and safely snapshots live WAL auth.db."""
    _setup_test_layout(tmp_path)
    data_dir = tmp_path / "data"
    data_dir.mkdir(parents=True, exist_ok=True)
    vault_dir = tmp_path / "vault"
    vault_dir.mkdir(parents=True, exist_ok=True)
    (vault_dir / "note.txt").write_text("vault data", encoding="utf-8")

    claire_conn = dbm.connect(data_dir / "claire.db")
    dbm.init_db(claire_conn)
    dbm.insert_document(
        claire_conn,
        Document(id="doc-backup-test", source_type="text", raw_text="knowledge data", content_hash="hash-1"),
    )
    claire_conn.close()

    auth_path = data_dir / "auth" / "auth.db"
    auth_conn = adb.connect_auth_db(auth_path)
    auth_conn.execute("INSERT INTO meta(key, value) VALUES ('test_canary', 'auth_live_wal_row')")
    auth_conn.commit()

    master_key_path = data_dir / "auth" / "master.key"
    master_key_bytes = os.urandom(32)
    master_key_path.write_bytes(master_key_bytes)
    os.chmod(master_key_path, 0o600)

    docker_harness = _DockerHarness()
    with _docker(docker_harness):
        exit_code = cb.main(["backup", "--format", "archive"], root=tmp_path)
        assert exit_code == 0

    auth_conn.close()

    stdout = capsys.readouterr().out
    assert "data/auth/master.key is excluded from the backup" in stdout
    assert "Store the master key" in stdout

    backups = list((tmp_path / "backups").glob("cb-*.tar.gz"))
    assert len(backups) == 1
    archive_path = backups[0]

    with tarfile.open(archive_path, "r:gz") as archive:
        names = [PurePosixPath(m.name).as_posix().removeprefix("./") for m in archive.getmembers()]
        assert not any("master.key" in name for name in names)
        assert any("data/auth/auth.db" in name for name in names)
        assert any("data/claire.db" in name for name in names)

        manifest_file = archive.extractfile("manifest.json")
        assert manifest_file is not None
        manifest = json.loads(manifest_file.read().decode("utf-8"))
        entries = manifest.get("entries", [])
        entry_paths = [e["path"] for e in entries if isinstance(e, dict)]
        assert "data/auth/master.key" not in entry_paths
        assert not any(p.endswith("master.key") for p in entry_paths)
        assert "data/auth/auth.db" in entry_paths
        assert "data/claire.db" in entry_paths

        auth_db_member = next(m for m in archive.getmembers() if m.name.endswith("data/auth/auth.db"))
        extracted_auth = archive.extractfile(auth_db_member)
        assert extracted_auth is not None
        temp_auth = tmp_path / "extracted_auth.db"
        temp_auth.write_bytes(extracted_auth.read())

        conn = adb.sqlite3.connect(str(temp_auth))
        cursor = conn.cursor()
        mode = cursor.execute("PRAGMA journal_mode").fetchone()[0]
        assert mode.lower() == "delete"
        row = cursor.execute("SELECT value FROM meta WHERE key='test_canary'").fetchone()
        assert row is not None and row[0] == "auth_live_wal_row"
        conn.close()


def test_restore_preserves_master_key_and_rejects_archive_with_master_key(tmp_path: Path):
    """E2E: Verify restore preserves existing master.key and rejects backup containing master.key."""
    _setup_test_layout(tmp_path)
    data_dir = tmp_path / "data"
    data_dir.mkdir(parents=True, exist_ok=True)
    vault_dir = tmp_path / "vault"
    vault_dir.mkdir(parents=True, exist_ok=True)
    (vault_dir / "note.txt").write_text("vault data", encoding="utf-8")

    claire_conn = dbm.connect(data_dir / "claire.db")
    dbm.init_db(claire_conn)
    claire_conn.close()

    auth_path = data_dir / "auth" / "auth.db"
    auth_conn = adb.connect_auth_db(auth_path)
    auth_conn.close()

    master_key_path = data_dir / "auth" / "master.key"
    original_master_key = b"ORIGINAL_MASTER_KEY_32_BYTES_XX"
    master_key_path.write_bytes(original_master_key)
    os.chmod(master_key_path, 0o600)

    docker_harness = _DockerHarness()
    with _docker(docker_harness):
        assert cb.main(["backup", "--format", "archive"], root=tmp_path) == 0

    backups = list((tmp_path / "backups").glob("cb-*.tar.gz"))
    archive_path = backups[0]

    with _docker(docker_harness):
        exit_code = cb.main(["restore", str(archive_path), "--yes"], root=tmp_path)
        assert exit_code == 0

    assert master_key_path.exists()
    assert master_key_path.read_bytes() == original_master_key
    assert stat.S_IMODE(master_key_path.stat().st_mode) == 0o600

    malicious_dir = tmp_path / "malicious_backup"
    malicious_payload = malicious_dir / "payload"
    malicious_payload.mkdir(parents=True, exist_ok=True)
    (malicious_payload / "data" / "auth").mkdir(parents=True, exist_ok=True)
    (malicious_payload / "data" / "auth" / "master.key").write_bytes(b"ATTACKER_FAKE_KEY_32_BYTES_ZZZZ")
    cb.shutil.copy2(data_dir / "claire.db", malicious_payload / "data" / "claire.db")
    cb.shutil.copy2(auth_path, malicious_payload / "data" / "auth" / "auth.db")

    entries = cb._scan_payload(malicious_payload)
    files = [e for e in entries if e["type"] == "file"]
    db_report = cb._validate_sqlite_database(malicious_payload / "data" / "claire.db")
    db_report["path"] = "data/claire.db"
    manifest = {
        "format_version": cb.BACKUP_FORMAT_VERSION,
        "id": "cb-20261103-120000",
        "kind": "manual",
        "created_at": cb._utc_now().isoformat(),
        "profile": "production",
        "project": "claire-bible",
        "cb_manuscript_version": cb.VERSION,
        "source_revision": "test",
        "components": ["data"],
        "database": db_report,
        "entries": entries,
        "totals": {"files": len(files), "bytes": sum(int(e["size"]) for e in files)},
    }
    (malicious_dir / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")

    malicious_archive = tmp_path / "malicious.tar.gz"
    with tarfile.open(malicious_archive, "w:gz") as tar:
        tar.add(malicious_dir / "manifest.json", arcname="manifest.json")
        tar.add(malicious_payload, arcname="payload")

    runtime = cb.load_runtime(cb.Layout(tmp_path))
    with _docker(docker_harness):
        assert cb.main(["restore", str(malicious_archive), "--yes"], root=tmp_path) != 0
        with pytest.raises(cb.ManuscriptError, match="master.key"):
            cb.command_restore(runtime, raw_source=str(malicious_archive), raw_components=None, confirmed=True)

    assert master_key_path.read_bytes() == original_master_key
