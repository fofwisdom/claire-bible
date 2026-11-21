"""The Support Bundle members of the subsystems after format 3 (support_bundle_services.py) and the records of the worker loops (loop_runtime.py).

The end-to-end test makes a bundle from a data directory with claire.db, client.db, auth.db, the loop records, the sidecar heartbeat, and the runtime files, and reads the archive. The unit tests cover the file I/O of loop_runtime.
"""

from __future__ import annotations

import io
import json
import sys
import tarfile
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from compression import zstd

from claire.ops import loop_runtime
from claire.store import auth_db, client_db
from claire.store import db as dbm
from claire.ops.support_bundle import create_support_bundle

TELEGRAM_TOKEN = "123456789:AAEexampleexampleexampleexample-_xyz"
FEED_URL = "https://private.example/feeds/secret-list.xml"
ID_SALT = "salt-value-that-must-not-leak-0123456789"
S3_SECRET = "s3-secret-value-0123456789"
GEMINI_KEY = "AIza-gemini-key-0123456789"


@dataclass
class StubSettings:
    db_file: Path
    data_dir: Path
    environment: str = "development"
    public_url: str = "http://127.0.0.1:8765"
    telemetry_retention_days: int = 30
    inject_host: str = "127.0.0.1"
    inject_port: int = 8765
    provider: str = "mock"

    @property
    def effective_provider(self) -> str:
        return "mock"

    @property
    def plugins_dir(self) -> Path:
        return self.data_dir / "var-plugins"

    def model_dump(self) -> dict[str, Any]:
        return {"environment": self.environment}


def _seed(data_dir: Path, now: float) -> StubSettings:
    db_file = data_dir / "claire.db"
    conn = dbm.connect(db_file)
    dbm.init_db(conn)
    conn.execute("INSERT OR REPLACE INTO server_settings (key, value, updated_at, updated_by) VALUES ('id_salt', ?, ?, NULL)", (ID_SALT, now))
    conn.execute(
        "INSERT OR REPLACE INTO server_settings (key, value, updated_at, updated_by) VALUES ('media_storage', ?, ?, 1)",
        (json.dumps({"mode": "s3", "bucket": "claire", "access_key": "AKIAEXAMPLE", "secret": {"ciphertext": S3_SECRET}}), now),
    )
    conn.execute(
        "INSERT INTO kf_outbox (activity_iri, kind, signer, recipient, recipient_domain, status, attempts, error_code, next_attempt_at, created_at) VALUES (?,?,?,?,?,?,?,?,?,?)",
        ("https://a.example/act/1", "offer", "1", "https://b.example/actor", "b.example", "pending", 3, "http_503", now - 7200, now - 600),
    )
    conn.execute(
        "INSERT INTO kf_known_servers (domain, source, created_at, error_count, last_error, last_error_at) VALUES (?,?,?,?,?,?)",
        ("b.example", "admin", now - 86400, 4, "timeout reading https://b.example/kb/9/?token=abc", now - 60),
    )
    conn.execute(
        "INSERT INTO expand_queue (document_id, status, attempts, error, created_at, updated_at, kb_id) VALUES (?,?,?,?,?,?,?)",
        ("doc-1", "error", 2, "fetch failed: HTTP 404", now - 300, now - 300, 0),
    )
    conn.commit()
    conn.close()

    cconn = client_db.connect_client_db(client_db.client_db_path(db_file))
    cconn.execute(
        "INSERT INTO feed_subscriptions (account_id, url, title, last_polled_at, next_poll_at, last_status, last_error, failure_count, created_at, updated_at) VALUES (?,?,?,?,?,?,?,?,?,?)",
        (7, FEED_URL, "Private list", now - 100, now - 7200, 403, f"HTTP 403 for {FEED_URL}", 5, now - 86400, now - 100),
    )
    cconn.execute(
        "INSERT INTO federation_client_outbox (account_id, kind, status, error_code, created_at) VALUES (?,?,?,?,?)",
        (7, "follow", "rejected", "remote_gone", now - 50),
    )
    cconn.commit()
    cconn.close()

    aconn = auth_db.connect_auth_db(auth_db.auth_db_path(data_dir))
    aconn.execute(
        "INSERT INTO account_telegram_bots (account_id, key_id, ciphertext, nonce, key_version, fingerprint, bot_id, bot_username, status, last_error, status_at, created_at, updated_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
        (7, "k1", b"cipher-bytes", b"nonce-bytes", 1, "fp-0001", 42, "alice_bot", "error", f"getMe failed: https://api.telegram.org/bot{TELEGRAM_TOKEN}/getMe 401", now - 30, now - 1000, now - 30),
    )
    aconn.execute(
        "INSERT INTO account_backup_policies (account_id, schedule, keep, last_run_at, last_status, last_error, updated_at) VALUES (?,?,?,?,?,?,?)",
        (7, "daily", 3, now - 3600, "error", "disk full", now - 3600),
    )
    aconn.commit()
    aconn.close()

    run_dir = data_dir / "run"
    run_dir.mkdir(parents=True, exist_ok=True)
    (run_dir / "federation_heartbeat.json").write_text(json.dumps({"received_at": now - 20, "contract_version": "1.8", "sidecar_version": "0.9.1", "queue_depth": 2, "self_check": {}}), encoding="utf-8")
    recorder = loop_runtime.LoopRecorder(data_dir, "expand", interval=900)
    recorder.cycle_failed("provider timeout")
    old = loop_runtime.LoopRecorder(data_dir, "refresh", interval=3600)
    old.state["last_cycle_at"] = now - 5 * 3600
    old._write()
    (data_dir / "providers.json").write_text(json.dumps({"version": 1, "active_provider": "gemini", "providers": {"gemini": {"api_key": GEMINI_KEY, "model": "gemini-x"}}}), encoding="utf-8")
    (data_dir / "ingest_limits.json").write_text(json.dumps({"version": 1, "limits": {"expand_max": 5}}), encoding="utf-8")
    return StubSettings(db_file=db_file, data_dir=data_dir)


def _members(path: Path) -> dict[str, bytes]:
    out: dict[str, bytes] = {}
    with open(path, "rb") as handle, zstd.ZstdFile(handle, "rb") as reader, tarfile.open(fileobj=reader, mode="r|") as tar:
        for member in tar:
            data = tar.extractfile(member)
            if data is not None:
                out[member.name.split("/", 1)[1]] = data.read()
    return out


def test_support_bundle_has_the_subsystem_members_without_secrets(tmp_path: Path):
    now = time.time()
    settings = _seed(tmp_path, now)
    info = create_support_bundle(settings, days=1, now_epoch=now)
    members = _members(info.filepath)
    blob = b"".join(members.values()).decode("utf-8", errors="replace")

    for secret in (TELEGRAM_TOKEN, FEED_URL, ID_SALT, S3_SECRET, GEMINI_KEY, "AKIAEXAMPLE", "cipher-bytes", "fp-0001", "token=abc"):
        assert secret not in blob, secret

    services = json.loads(members["diagnostics/services.json"])
    plugins = json.loads(members["diagnostics/plugins.json"])
    assert {"core_commit", "plugins", "process_reports"} <= set(plugins)
    assert services["worker_loops"]["expand"]["state"] == "recent"
    assert services["worker_loops"]["expand"]["last_error"] == "provider timeout"
    assert services["worker_loops"]["refresh"]["state"] == "overdue"
    assert services["worker_loops"]["recover"] == {"state": "absent"}
    assert services["fedify_sidecar"]["heartbeat"]["sidecar_version"] == "0.9.1"
    assert 0 < services["fedify_sidecar"]["heartbeat_age_seconds"] < 60

    runtime = json.loads(members["diagnostics/runtime_config.json"])
    assert runtime["providers_json"]["providers"]["gemini"]["model"] == "gemini-x"
    assert runtime["ingest_limits_json"]["limits"]["expand_max"] == 5
    assert runtime["server_settings"]["media_storage"]["value"]["bucket"] == "claire"
    assert runtime["server_settings"]["id_salt"]["value"] == "***REDACTED***"

    server = json.loads(members["federation/server_queues.json"])
    assert server["kf_outbox"]["groups"][0]["status"] == "pending"
    assert server["kf_outbox_errors"] == [{"error_code": "http_503", "recipient_domain": "b.example", "count": 1, "last_at": server["kf_outbox_errors"][0]["last_at"]}]
    assert server["kf_outbox_retries"]["pending_overdue_1h"] == 1
    known = server["kf_known_servers"][0]
    assert known["domain"] == "b.example" and known["error_count"] == 4 and "[URL]" in known["last_error"]

    client = json.loads(members["federation/client_db.json"])
    assert client["quick_check"] == "ok"
    assert client["client_meta"]["schema_version"] == str(client_db.CLIENT_SCHEMA_VERSION)
    assert client["feeds"]["failing"] == 1 and client["feeds"]["poll_overdue_1h"] == 1
    assert client["feeds"]["by_last_status"][0]["last_status"] == 403
    assert client["feeds"]["error_samples"][0]["error"] == "HTTP 403 for [URL]"
    assert client["federation_client_outbox_errors"][0]["error_code"] == "remote_gone"

    auth = json.loads(members["accounts/auth_db.json"])
    assert auth["quick_check"] == "ok"
    bot = auth["telegram_bots"][0]
    assert (bot["account_id"], bot["bot_username"], bot["status"]) == (7, "alice_bot", "error")
    assert "ciphertext" not in bot and "fingerprint" not in bot
    assert auth["backup_policies"][0]["last_error"] == "disk full"

    workers = json.loads(members["pipeline/worker_queues.json"])
    expand = workers[str(settings.db_file)]["expand_queue"]
    assert expand["groups"][0]["status"] == "error" and expand["max_attempts"] == 2
    assert expand["admin_error_samples"][0]["error"] == "fetch failed: HTTP 404"

    shared = json.loads(members["pipeline/shared_kb.json"])
    assert "kb_contributions" in shared

    warnings = json.loads(members["diagnostics/collector_warnings.json"])
    assert not [w for w in warnings if w["code"].endswith(("_COLLECTOR_FAILED", "_UNREADABLE"))], warnings


def test_support_bundle_reports_absent_subsystem_databases(tmp_path: Path):
    now = time.time()
    db_file = tmp_path / "claire.db"
    conn = dbm.connect(db_file)
    dbm.init_db(conn)
    conn.close()
    info = create_support_bundle(StubSettings(db_file=db_file, data_dir=tmp_path), days=1, now_epoch=now)
    members = _members(info.filepath)
    assert json.loads(members["federation/client_db.json"])["state"] == "absent"
    assert json.loads(members["accounts/auth_db.json"])["state"] == "absent"
    services = json.loads(members["diagnostics/services.json"])
    assert services["fedify_sidecar"]["heartbeat"] is None
    assert {loop["state"] for loop in services["worker_loops"].values()} == {"absent"}
    # The collectors open the databases read-only: they do not make client.db or auth.db.
    assert not (tmp_path / "client.db").exists() and not (tmp_path / "auth" / "auth.db").exists()


def test_loop_runtime_writes_the_log_and_the_state(tmp_path: Path, monkeypatch):
    out = io.StringIO()
    monkeypatch.setattr(sys, "stdout", out)
    monkeypatch.setattr(sys, "stderr", io.StringIO())
    recorder = loop_runtime.start_loop(tmp_path, "recover", interval=600)
    try:
        print("[recover] 2 tried, 1 recovered", flush=True)
        sys.stdout.write("partial ")
        sys.stdout.write("line\n")
        recorder.cycle_ok({"tried": 2, "recovered": 1})
    finally:
        logger = loop_runtime.logging.getLogger("claire.loop.recover")
        for handler in list(logger.handlers):
            logger.removeHandler(handler)
            handler.close()
    assert "[recover] 2 tried, 1 recovered" in out.getvalue()
    lines = (tmp_path / "logs" / "recover-loop.log").read_text(encoding="utf-8").splitlines()
    assert lines[0].endswith("Z [recover] 2 tried, 1 recovered") and lines[1].endswith("Z partial line")
    state = json.loads(loop_runtime.loop_state_path(tmp_path, "recover").read_text(encoding="utf-8"))
    assert (state["loop"], state["cycles"], state["last_cycle_ok"], state["last_summary"]) == ("recover", 1, True, {"tried": 2, "recovered": 1})
    read = loop_runtime.read_loop_states(tmp_path, now=time.time())
    assert read["recover"]["state"] == "recent" and read["expand"] == {"state": "absent"}
