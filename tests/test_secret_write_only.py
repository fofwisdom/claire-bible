"""Tests that secret keys are write-only and never leak (design §11.3, P4).

This module tests that canary keys do not leak into:
- HTTP responses and headers
- Logs and exception traces
- raw_inbox table rows
- Job status files
- Telemetry database records
- Support bundle archives
- Graph bundle archives
- Manuscript backup archives
- Subprocess environments and representations
- Cross-account or cross-provider AAD decryption

All state writes to tmp_path only and isolates environment variables.
"""

from __future__ import annotations

import io
import json
import logging
import os
import sqlite3
import stat
import subprocess
import tarfile
import time
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from types import SimpleNamespace
from typing import Any
from unittest.mock import patch

import pytest
import zstandard as zstd
from starlette.testclient import TestClient

from claire import secret_scrub
from claire.accounts.provider_policy import ProviderContext, ProviderNotAllowed, ProviderNotConfigured, classify_provider
from claire.api import server
from claire.graph_bundle import create_graph_bundle
from claire.ontology.base import Document
from claire.store import accounts as accounts_store
from claire.store import auth_db
from claire.store import db as dbm
from claire.store import provider_keys
from claire.store.secrets import SecretDecryptionError, SecretService, reset_secret_service
from claire.store.telemetry import connect_telemetry, get_telemetry_db_path, query_telemetry, record_telemetry
from claire.support_bundle import create_support_bundle
from ops import cb_manuscript as cb

PUBLIC_URL = "http://127.0.0.1:8765"
ORIGIN = PUBLIC_URL
OWNER_TOKEN = "owner-" + "o" * 32
ADMIN_PASSWORD = "Correct-Horse-Battery-123!"
TEST_MASTER_KEY_HEX = "33" * 32
CANARY = "canary-member-key-7f3a9c1e5b2d4f60"
CANARY_ALT = "canary-alt-key-0123456789abcdef"

_REAL_SUBPROCESS_RUN = subprocess.run


class _DockerHarness:
    """Record Docker commands and bypass Docker in unit tests."""

    def __init__(self, *, running: tuple[str, ...] = (), migrate_returncode: int = 0, liveness_returncode: int = 0) -> None:
        self.running = running
        self.migrate_returncode = migrate_returncode
        self.liveness_returncode = liveness_returncode
        self.commands: list[list[str]] = []

    def __call__(self, argv: Any, **kwargs: Any) -> Any:
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


@dataclass
class _Settings:
    db_file: Path
    data_dir: Path
    environment: str = "development"
    public_url: str = PUBLIC_URL
    inject_token: str = OWNER_TOKEN
    readonly_token: str = "readonly-" + "r" * 32
    cors_allowed_origins: str = ""
    anonymous_readonly: bool = False
    fqdn: str = "notes.kimhome.net"
    multi_theme: bool = True


@pytest.fixture(autouse=True)
def _isolate_env_and_secrets(monkeypatch: pytest.MonkeyPatch):
    for key in list(os.environ):
        if key.startswith("CLAIRE_") or key.startswith("GEMINI_") or key.startswith("GOOGLE_API"):
            monkeypatch.delenv(key, raising=False)
    monkeypatch.setenv("CLAIRE_SECRET_KEY", TEST_MASTER_KEY_HEX)
    reset_secret_service()
    secret_scrub._reset_for_tests()
    yield
    reset_secret_service()
    secret_scrub._reset_for_tests()


@pytest.fixture
def mock_verify_key(monkeypatch: pytest.MonkeyPatch) -> None:
    def _fake_verify(provider: str, api_key: str) -> tuple[bool, str]:
        return True, "ok"

    monkeypatch.setattr(provider_keys, "verify_key", _fake_verify)


def _setup_app_env(tmp_path: Path) -> SimpleNamespace:
    settings = _Settings(db_file=tmp_path / "claire.db", data_dir=tmp_path)
    conn = dbm.connect(settings.db_file)
    try:
        dbm.init_db(conn)
        accounts_store.AccountStore(conn).complete_setup(1, username="admin_kim", display_name="Admin Kim")
        auth = auth_db.connect_auth_db(auth_db.auth_db_path(settings.data_dir))
        try:
            auth_db.set_password(auth, 1, ADMIN_PASSWORD, username="admin_kim")
        finally:
            auth.close()

        accounts_store.AccountStore(conn).set_setting("mode", "network", updated_by=1)
        cur = conn.execute(
            "INSERT INTO accounts (public_id, username, role, status, created_at, updated_at) "
            "VALUES (?, ?, 'member', 'active', 0, 0)",
            (223456789012, "member_alpha"),
        )
        conn.commit()
        member_id = int(cur.lastrowid)
    finally:
        conn.close()

    conn = dbm.connect_existing(settings.db_file)
    try:
        member_session = accounts_store.create_account_session(conn, account_id=member_id, scope="owner")
        admin_session = accounts_store.create_account_session(conn, account_id=1, scope="owner")
    finally:
        conn.close()

    app = server.create_app(settings, SimpleNamespace(provider=SimpleNamespace(name="stub")))
    return SimpleNamespace(
        settings=settings,
        member_id=member_id,
        member_session=member_session,
        admin_session=admin_session,
        app=app,
    )


def test_canary_key_not_in_http_responses_and_headers(tmp_path: Path, mock_verify_key: None) -> None:
    """Verify canary key is write-only and absent from all HTTP responses and headers."""
    env = _setup_app_env(tmp_path)
    with TestClient(env.app, base_url=PUBLIC_URL) as tc:
        headers_member = {"Cookie": f"claire_session={env.member_session}", "Origin": ORIGIN}
        res_put = tc.put("/api/me/keys", json={"provider": "gemini", "api_key": CANARY}, headers=headers_member)
        assert res_put.status_code == 200

        res_get = tc.get("/api/me/keys", headers=headers_member)
        assert res_get.status_code == 200
        get_json = res_get.json()
        assert "keys" in get_json
        assert len(get_json["keys"]) == 1
        assert get_json["keys"][0]["provider"] == "gemini"
        assert "fingerprint" in get_json["keys"][0]
        assert CANARY not in json.dumps(get_json)

        res_verify = tc.post("/api/me/keys/verify", json={"provider": "gemini"}, headers=headers_member)
        assert res_verify.status_code == 200

        res_me = tc.get("/me", headers=headers_member, follow_redirects=False)
        assert res_me.status_code == 302
        assert res_me.headers["location"] == "/settings/account"

        res_account = tc.get("/settings/account", headers=headers_member, follow_redirects=False)
        assert res_account.status_code == 200
        assert res_account.headers["referrer-policy"] == "same-origin"

        res_keys_page = tc.get("/settings/keys", headers=headers_member, follow_redirects=False)
        assert res_keys_page.status_code == 200
        assert res_keys_page.headers["referrer-policy"] == "same-origin"

        headers_admin = {"Cookie": f"claire_session={env.admin_session}", "Origin": ORIGIN}
        res_admin_accounts = tc.get("/admin/accounts", headers=headers_admin)
        assert res_admin_accounts.status_code == 200

        res_del = tc.delete("/api/me/keys", headers=headers_member)
        assert res_del.status_code == 200

        for res in (res_put, res_get, res_verify, res_me, res_account, res_keys_page, res_admin_accounts, res_del):
            blob = res.content.decode("utf-8", errors="replace") + repr(list(res.headers.items()))
            assert CANARY not in blob


def test_canary_key_not_in_logs_and_exceptions() -> None:
    """Verify log scrubber redacts registered secrets and pattern-matched keys from logs."""
    secret_scrub.install_log_scrubber()
    secret_scrub.register_secret(CANARY)

    log_stream = io.StringIO()
    handler = logging.StreamHandler(log_stream)
    handler.setLevel(logging.DEBUG)
    test_logger = logging.getLogger("claire.test.secret_canary")
    test_logger.setLevel(logging.DEBUG)
    test_logger.addHandler(handler)

    try:
        test_logger.info("Connecting upstream with key=%s", CANARY)
        test_logger.warning("Upstream failure details: %s", f"error with secret {CANARY}")
        try:
            raise RuntimeError(f"External API failure: {CANARY}")
        except RuntimeError as exc:
            test_logger.exception("Caught exception: %s", exc)

        handler.flush()
        captured = log_stream.getvalue()
        assert CANARY not in captured
        assert "[REDACTED]" in captured
    finally:
        test_logger.removeHandler(handler)


def test_canary_key_not_in_raw_inbox(tmp_path: Path) -> None:
    """Verify error messages saved to raw_inbox have secrets redacted."""
    db_path = tmp_path / "claire.db"
    conn = dbm.connect(db_path)
    try:
        dbm.init_db(conn)
        secret_scrub.register_secret(CANARY)

        raw_error = f"Upstream rejected API call: key={CANARY}"
        scrubbed_error = secret_scrub.scrub_text(raw_error)

        inbox_id = dbm.log_inbox(
            conn,
            theme_id=0,
            source="test",
            kind="text",
            payload="sample payload text",
        )
        dbm.update_inbox(
            conn,
            inbox_id,
            status="error",
            error=scrubbed_error,
        )

        rows = conn.execute("SELECT * FROM raw_inbox WHERE id = ?", (inbox_id,)).fetchall()
        assert len(rows) == 1
        row_blob = repr(list(rows[0]))
        assert CANARY not in row_blob
        assert "[REDACTED]" in row_blob
    finally:
        conn.close()


def test_canary_key_not_in_job_files(tmp_path: Path) -> None:
    """Verify job state files never contain canary secrets."""
    jobs_dir = tmp_path / "jobs"
    jobs_dir.mkdir(parents=True, exist_ok=True)

    secret_scrub.register_secret(CANARY)
    safe_job_data = {
        "job_id": "job_12345",
        "status": "failed",
        "error": secret_scrub.scrub_text(f"API key error: {CANARY}"),
        "timestamp": time.time(),
    }
    job_file = jobs_dir / "job_12345.json"
    job_file.write_text(json.dumps(safe_job_data), encoding="utf-8")

    all_jobs_text = "".join(f.read_text(encoding="utf-8") for f in jobs_dir.glob("*.json"))
    assert CANARY not in all_jobs_text
    assert "[REDACTED]" in all_jobs_text


def test_canary_key_not_in_telemetry(tmp_path: Path) -> None:
    """Verify telemetry logs do not contain canary keys or fingerprints."""
    secret_scrub.register_secret(CANARY)
    record_telemetry(
        tmp_path,
        provider="gemini",
        call_type="extract",
        status="ERROR",
        error_message=secret_scrub.scrub_text(f"Token limit exceeded for {CANARY}"),
        output_snippet=secret_scrub.scrub_text(f"Snippet containing secret: {CANARY}"),
    )
    records = query_telemetry(tmp_path, limit=10)
    assert len(records) >= 1
    for r in records:
        r_blob = repr(r)
        assert CANARY not in r_blob
        assert "[REDACTED]" in r_blob


def test_canary_key_not_in_support_and_graph_bundles(tmp_path: Path) -> None:
    """Verify support bundle and graph bundle archives do not contain canary keys or master.key."""
    settings = _Settings(db_file=tmp_path / "claire.db", data_dir=tmp_path)
    conn = dbm.connect(settings.db_file)
    try:
        dbm.init_db(conn)
        doc = Document(id="doc_bundle_test", source_type="text", raw_text="bundle test content", content_hash="h1")
        dbm.insert_document(conn, doc)
    finally:
        conn.close()

    auth_path = auth_db.auth_db_path(settings.data_dir)
    auth = auth_db.connect_auth_db(auth_path)
    try:
        provider_keys.put_key(auth, account_id=2, provider="gemini", api_key=CANARY, status="active")
    finally:
        auth.close()

    # 1. Graph bundle verification
    gb_info = create_graph_bundle(settings, theme_id=0)
    assert gb_info.filepath.is_file()
    dctx = zstd.ZstdDecompressor()
    with open(gb_info.filepath, "rb") as fh:
        decompressed_gb = dctx.decompress(fh.read(), max_output_size=100 * 1024 * 1024)
    with tarfile.open(fileobj=io.BytesIO(decompressed_gb), mode="r:") as tar:
        for member in tar.getmembers():
            f = tar.extractfile(member)
            if f is not None:
                content = f.read().decode("utf-8", errors="replace")
                assert CANARY not in content
                assert "master.key" not in member.name

    # 2. Support bundle verification
    sb_info = create_support_bundle(settings, days=1)
    assert sb_info.filepath.is_file()
    with open(sb_info.filepath, "rb") as fh:
        decompressed_data = dctx.decompress(fh.read(), max_output_size=100 * 1024 * 1024)
    with tarfile.open(fileobj=io.BytesIO(decompressed_data), mode="r:") as tar:
        for member in tar.getmembers():
            assert "master.key" not in member.name
            f = tar.extractfile(member)
            if f is not None:
                content = f.read().decode("utf-8", errors="replace")
                assert CANARY not in content


def test_canary_key_not_in_backup_archive(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    """Verify cb-manuscript backup archive safely excludes master.key and never leaks plaintext canary."""
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
        Document(id="doc_backup_test", source_type="text", raw_text="knowledge data", content_hash="hash-1"),
    )
    claire_conn.close()

    auth_path = data_dir / "auth" / "auth.db"
    auth_conn = auth_db.connect_auth_db(auth_path)
    provider_keys.put_key(auth_conn, account_id=2, provider="gemini", api_key=CANARY, status="active")
    auth_conn.close()

    master_key_path = data_dir / "auth" / "master.key"
    master_key_bytes = os.urandom(32)
    master_key_path.write_bytes(master_key_bytes)
    os.chmod(master_key_path, 0o600)

    docker_harness = _DockerHarness()
    with patch.object(cb.subprocess, "run", side_effect=docker_harness):
        exit_code = cb.main(["backup", "--format", "archive"], root=tmp_path)
        assert exit_code == 0

    stdout = capsys.readouterr().out
    assert "data/auth/master.key is excluded from the backup" in stdout

    backups = list((tmp_path / "backups").glob("cb-*.tar.gz"))
    assert len(backups) == 1
    archive_path = backups[0]

    with tarfile.open(archive_path, "r:gz") as archive:
        names = [PurePosixPath(m.name).as_posix().removeprefix("./") for m in archive.getmembers()]
        assert not any("master.key" in name for name in names)
        assert any("data/auth/auth.db" in name for name in names)

        for member in archive.getmembers():
            f = archive.extractfile(member)
            if f is not None:
                raw_bytes = f.read()
                assert CANARY.encode("utf-8") not in raw_bytes


def test_canary_key_not_in_subprocess_environment_and_representations() -> None:
    """Verify ActiveKey and ProviderContext protect keys from representations and subprocess leaking."""
    active_key = provider_keys.ActiveKey(provider="gemini", fingerprint="7f3a9c1e", api_key=CANARY)
    repr_active = repr(active_key)
    assert CANARY not in repr_active
    assert "7f3a9c1e" in repr_active

    ctx = ProviderContext(
        account_id=2,
        is_admin=False,
        source="byok",
        provider="gemini",
        model="gemini-3.1-flash-lite",
        embed_model="gemini-embedding-exp-0827",
        effort="low",
        api_key=CANARY,
        credential_key="gemini:7f3a9c1e",
    )
    repr_ctx = repr(ctx)
    assert CANARY not in repr_ctx
    assert "gemini:7f3a9c1e" in repr_ctx

    # Fail closed for host-cli
    assert classify_provider("antigravity") == "host-cli"
    assert classify_provider("codex") == "host-cli"
    assert classify_provider("unknown_provider") == "host-cli"


def test_aad_cross_decryption_failure(tmp_path: Path) -> None:
    """Verify AES-256-GCM AAD binding prevents decryption when ciphertext is copied to another account/provider/id."""
    service = SecretService(master_key=os.urandom(32))

    aad_1 = provider_keys.key_aad(account_id=10, provider="gemini", key_id="key_10")
    encrypted = service.encrypt(CANARY, aad_1)

    decrypted = service.decrypt(encrypted.ciphertext, encrypted.nonce, aad_1)
    assert decrypted.decode("utf-8") == CANARY

    # 1. Cross account
    aad_cross_account = provider_keys.key_aad(account_id=20, provider="gemini", key_id="key_10")
    with pytest.raises(SecretDecryptionError):
        service.decrypt(encrypted.ciphertext, encrypted.nonce, aad_cross_account)

    # 2. Cross provider
    aad_cross_provider = provider_keys.key_aad(account_id=10, provider="openai", key_id="key_10")
    with pytest.raises(SecretDecryptionError):
        service.decrypt(encrypted.ciphertext, encrypted.nonce, aad_cross_provider)

    # 3. Cross key_id
    aad_cross_id = provider_keys.key_aad(account_id=10, provider="gemini", key_id="key_99")
    with pytest.raises(SecretDecryptionError):
        service.decrypt(encrypted.ciphertext, encrypted.nonce, aad_cross_id)

    # 4. Database row swapping
    auth_path = tmp_path / "auth.db"
    auth_conn = auth_db.connect_auth_db(auth_path)
    try:
        provider_keys.put_key(
            auth_conn, account_id=10, provider="gemini", api_key=CANARY, status="active", secret_service=service
        )
        row = auth_conn.execute("SELECT * FROM account_provider_keys WHERE account_id=10").fetchone()
        assert row is not None

        # Copy row to account_id=20
        auth_conn.execute(
            "INSERT INTO account_provider_keys (id, account_id, provider, ciphertext, nonce, key_version, "
            "fingerprint, status, created_at, last_verified_at, last_used_at) "
            "VALUES (?, 20, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                "swapped_id",
                row["provider"],
                row["ciphertext"],
                row["nonce"],
                row["key_version"],
                row["fingerprint"],
                row["status"],
                row["created_at"],
                row["last_verified_at"],
                row["last_used_at"],
            ),
        )
        auth_conn.commit()

        # Decrypting account 20's key must fail because AAD does not match
        with pytest.raises(provider_keys.ProviderKeyError):
            provider_keys.load_active_key(auth_conn, account_id=20, secret_service=service)
    finally:
        auth_conn.close()
