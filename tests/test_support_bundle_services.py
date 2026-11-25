"""The Support Bundle members of the subsystems after format 3 (support_bundle_services.py) and the records of the worker loops (loop_runtime.py).

The end-to-end test makes a bundle from a data directory with claire.db, client.db, auth.db, the loop records, the sidecar heartbeat, and the runtime files, and reads the archive. The unit tests cover the file I/O of loop_runtime and of the state record of the Web Push task.

The notification member (support_bundle_notifications.py) has its own end-to-end test: a data directory with notifications, devices, a queue, and the VAPID key pair row.
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

from claire.notifications import sender
from claire.ops import loop_runtime
from claire.store import auth_db, client_db
from claire.store import db as dbm
from claire.ops.support_bundle import create_support_bundle

TELEGRAM_TOKEN = "123456789:AAEexampleexampleexampleexample-_xyz"
FEED_URL = "https://private.example/feeds/secret-list.xml"
ID_SALT = "salt-value-that-must-not-leak-0123456789"
S3_SECRET = "s3-secret-value-0123456789"
GEMINI_KEY = "AIza-gemini-key-0123456789"
PUSH_PATH_A = "send/capability-of-device-a-0123456789"
PUSH_PATH_B = "wpush/v2/capability-of-device-b-0123456789"
PUSH_P256DH = "BCVxsr7N_eNgVRqvHtD0zTZsEc6-VV-JvLexhqUzORcxaOzi6-AYWXvTBHm4bjyPjs7Vd8pZGH6SRpkNtoIAiw4"
PUSH_AUTH = "BTBZMqHH6r4Tts7J_aSIgg"
ACTOR_NAME = "dana@other.example"


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
    assert server["federation_objects"] == {"total": 0}
    assert any(f["code"] == "SIDECAR_SIGNED_CHECK_FAILED" for f in server["heartbeat_findings"]) is False
    assert server["kf_outbox"]["groups"][0]["status"] == "pending"
    assert server["kf_outbox_errors"] == [{"error_code": "http_503", "recipient_domain": "b.example", "count": 1, "last_at": server["kf_outbox_errors"][0]["last_at"]}]
    assert server["kf_outbox_retries"]["pending_overdue_1h"] == 1
    known = server["kf_known_servers"][0]
    assert known["domain"] == "b.example" and known["error_count"] == 4 and "[URL]" in known["last_error"]

    client = json.loads(members["federation/client_db.json"])
    assert client["quick_check"] == "ok"
    assert client["client_meta"]["schema_version"] == str(client_db.CLIENT_SCHEMA_VERSION)
    assert client["federation_notes"] == {"total": 0, "active": 0, "deleted": 0, "published_in_period": 0}
    assert client["federation_object_reactions"]["by_kind"] == {}
    assert client["federation_quote_authorizations"] == {"by_state": {}}
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


# --- notifications/summary.json ---------------------------------------------------------------------------------------


def _bundle_members(settings: Any, now: float) -> dict[str, bytes]:
    return _members(create_support_bundle(settings, days=1, now_epoch=now).filepath)


def _seed_notifications(settings: StubSettings, now: float) -> None:
    """Account 1 (the admin of a new database): two unread notifications, one of them of an unknown kind; two devices (one fails, and none has a session in effect); one message that waits for 10 minutes; one kind without push; the VAPID row."""
    conn = dbm.connect_existing(settings.db_file)
    conn.execute("UPDATE accounts SET username='owner1', status='active' WHERE id=1")
    conn.execute("INSERT INTO kb_contributions (public_id, kb_id, document_id, origin, state, contributed_at, updated_at) VALUES (901, 0, 'doc-901', 'bulk', 'pending', ?, ?)", (now, now))
    conn.commit()
    conn.close()
    cconn = client_db.connect_client_db(client_db.client_db_path(settings.db_file))
    cconn.execute("INSERT INTO notifications (id, account_id, kind, params_json, target_path, group_key, count, created_at, updated_at) VALUES (1, 1, 'kf_contribution_pending', ?, '/moderation/kb/77?tab=queue', 'kb:5', 3, ?, ?)", (json.dumps({"actor": ACTOR_NAME, "kb": "Cities"}), now - 900, now - 700))
    cconn.execute("INSERT INTO notifications (id, account_id, kind, params_json, target_path, count, created_at, updated_at, read_at, pushed_at) VALUES (2, 1, 'ap_follower', '{}', '/account/federation', 1, ?, ?, ?, ?)", (now - 5 * 86400, now - 5 * 86400, now - 86400, now - 5 * 86400))
    cconn.execute("INSERT INTO notifications (id, account_id, kind, params_json, target_path, count, created_at, updated_at) VALUES (3, 1, 'kind_of_a_newer_version', '{}', '/', 1, ?, ?)", (now - 60, now - 60))
    for sub_id, endpoint, failures, error, success in ((1, f"https://fcm.googleapis.com/{PUSH_PATH_A}", 0, None, now - 3600), (2, f"https://updates.push.services.mozilla.com/{PUSH_PATH_B}", 3, "http_403", None)):
        cconn.execute(
            "INSERT INTO push_subscriptions (id, account_id, endpoint, p256dh, auth, label, session_ref, created_at, last_success_at, failure_count, last_error) VALUES (?, 1, ?, ?, ?, 'Chrome · Linux', 'ref-of-a-session-that-ended', ?, ?, ?, ?)",
            (sub_id, endpoint, PUSH_P256DH, PUSH_AUTH, now - 86400, success, failures, error),
        )
    cconn.execute("INSERT INTO push_queue (notification_id, subscription_id, attempts, next_attempt_at) VALUES (1, 2, 2, ?)", (now - 600,))
    cconn.execute("INSERT INTO notification_push_off (account_id, kind) VALUES (1, 'ap_follower')")
    cconn.commit()
    cconn.close()
    aconn = auth_db.connect_auth_db(auth_db.auth_db_path(settings.data_dir))
    aconn.execute("INSERT INTO server_key_pairs (name, public_key, private_ciphertext, private_nonce, key_version, created_at) VALUES ('webpush_vapid', 'public-key-text', ?, ?, 1, ?)", (b"vapid-cipher-bytes", b"vapid-nonce", now - 86400))
    aconn.commit()
    aconn.close()


def test_support_bundle_has_the_notification_member_with_findings_and_no_capability(tmp_path: Path, monkeypatch):
    now = time.time()
    settings = _seed(tmp_path, now)
    _seed_notifications(settings, now)
    monkeypatch.setattr(sender, "vapid_subject", lambda _settings: "http://claire.example")
    members = _bundle_members(settings, now)
    blob = b"".join(members.values()).decode("utf-8", errors="replace")
    # No endpoint path (the capability of a device), no subscription key, no key material, no parameter of a notification, no entry address.
    for secret in (PUSH_PATH_A, PUSH_PATH_B, PUSH_P256DH, PUSH_AUTH, "vapid-cipher-bytes", "public-key-text", ACTOR_NAME, "/moderation/kb/77", "ref-of-a-session-that-ended"):
        assert secret not in blob, secret

    summary = json.loads(members["notifications/summary.json"])
    assert summary["readiness"] == {"public_origin": True, "origin_scheme": "http", "vapid_pair": {"state": "present", "created_at": summary["readiness"]["vapid_pair"]["created_at"]}}
    assert summary["sender_task"] == {"state": "absent"}
    assert summary["notifications"] == {"total": 3, "unread": 2, "accounts_with_unread": 1, "accounts_with_unread_and_no_device": 0}
    kinds = {row["kind"]: row for row in summary["notifications_by_kind"]}
    pending = kinds["kf_contribution_pending"]
    assert (pending["channel"], pending["class"], pending["notifications"], pending["unread"], pending["events"], pending["made_in_period"], pending["unread_never_sent"]) == ("activitypub", "request", 1, 1, 3, 1, 1)
    assert (kinds["ap_follower"]["unread"], kinds["ap_follower"]["made_in_period"]) == (0, 0)
    assert kinds["kind_of_a_newer_version"]["known"] is False
    queue = summary["push_queue"]
    assert (queue["messages"], queue["due_now"], queue["retrying"], queue["max_attempts"], queue["by_kind"]) == (1, 1, 1, 2, [{"kind": "kf_contribution_pending", "messages": 1, "max_attempts": 2}])
    assert 590 < queue["oldest_due_age_seconds"] < 660
    subs = summary["push_subscriptions"]
    assert (subs["devices"], subs["accounts"], subs["failing"], subs["max_failures_in_sequence"], subs["without_session_in_effect"]) == (2, 1, 1, 3, 2)
    assert [(row["push_service"], row["devices"], row["failing"], row["never_sent"]) for row in subs["by_push_service"]] == [("fcm.googleapis.com", 1, 0, 0), ("updates.push.services.mozilla.com", 1, 1, 1)]
    assert subs["last_errors"] == [{"error": "http_403", "devices": 1}]
    assert summary["push_off"] == [{"kind": "ap_follower", "accounts": 1}]
    assert summary["scan"]["contribution"] == {"first_scan_at": None, "told": 0, "pending_contributions": 1, "pending_not_told": 1}
    [top] = summary["accounts_with_most_unread"]
    assert (top["unread"], top["devices"], top["queued_messages"]) == (2, 2, 1) and top["account"]
    codes = [finding["code"] for finding in summary["findings"]]
    assert codes == ["ORIGIN_NOT_HTTPS", "SENDER_TASK_NOT_RUNNING", "QUEUE_OVERDUE", "DEVICES_FAILING", "DEVICES_WITHOUT_SESSION", "UNKNOWN_KINDS", "SCAN_NEVER_RAN"]
    assert json.loads(members["manifest.json"])["notification_findings"] == codes

    # The liveness of the task is with the other services, and the row counts of tables with a secret word in the name are counts, not a mask.
    assert json.loads(members["diagnostics/services.json"])["api_tasks"] == {"web_push": {"state": "absent"}}
    auth_counts = json.loads(members["accounts/auth_db.json"])["row_counts"]
    assert auth_counts["server_key_pairs"] == 1 and auth_counts["provider_key_decryptions"] == 0
    client_counts = json.loads(members["federation/client_db.json"])["row_counts"]
    assert (client_counts["notifications"], client_counts["push_subscriptions"], client_counts["push_queue"]) == (3, 2, 1)
    warnings = json.loads(members["diagnostics/collector_warnings.json"])
    assert not [w for w in warnings if w["code"].startswith("NOTIFICATIONS")], warnings


def test_notification_member_without_a_public_origin_and_with_a_running_task(tmp_path: Path, monkeypatch):
    now = time.time()
    settings = _seed(tmp_path, now)
    _seed_notifications(settings, now)
    monkeypatch.setattr(sender, "vapid_subject", lambda _settings: None)
    recorder = sender.TaskRecorder(tmp_path, interval=5.0, now=now - 30)
    recorder.cycle_ok(sender.SendReport(sent=2, dropped=1, errors=["http_403"]), told_contributions=1, now=now - 20)
    members = _bundle_members(settings, now)
    summary = json.loads(members["notifications/summary.json"])
    task = summary["sender_task"]
    assert (task["state"], task["totals"], task["last_work"]["sent"]) == ("recent", {"sent": 2, "retried": 0, "dropped": 1, "removed_subscriptions": 0, "told_contributions": 1}, 2)
    assert task["last_send_errors"][0]["error"] == "http_403" and 15 < task["age_seconds"] < 30
    codes = [finding["code"] for finding in summary["findings"]]
    assert codes[0] == "NO_PUBLIC_ORIGIN" and "SENDER_TASK_NOT_RUNNING" not in codes and "ORIGIN_NOT_HTTPS" not in codes


def test_notification_member_reports_an_absent_or_old_client_db(tmp_path: Path):
    now = time.time()
    db_file = tmp_path / "claire.db"
    conn = dbm.connect(db_file)
    dbm.init_db(conn)
    conn.close()
    settings = StubSettings(db_file=db_file, data_dir=tmp_path)
    summary = json.loads(_bundle_members(settings, now)["notifications/summary.json"])
    assert summary["client_db"]["state"] == "absent" and summary["sender_task"] == {"state": "absent"} and summary["readiness"]["vapid_pair"]["state"] == "absent"
    assert not (tmp_path / "client.db").exists() and not (tmp_path / "auth" / "auth.db").exists()
    # A client.db of version 8 (before the notifications): the member says that the tables are absent, and the collector does not fail.
    cconn = client_db.connect_client_db(client_db.client_db_path(db_file))
    for table in ("notifications", "push_subscriptions", "push_queue", "notification_push_off", "notification_scan_seen"):
        cconn.execute(f"DROP TABLE {table}")
    cconn.commit()
    cconn.close()
    members = _bundle_members(settings, now + 1)
    summary = json.loads(members["notifications/summary.json"])
    assert summary["client_db"]["state"] == "no_notification_tables" and [f["code"] for f in summary["findings"]] == ["NOTIFICATION_TABLES_ABSENT"]
    assert not [w for w in json.loads(members["diagnostics/collector_warnings.json"]) if w["code"].startswith("NOTIFICATIONS")]


def test_web_push_task_state_file_is_written_with_a_limit_and_read_with_its_age(tmp_path: Path):
    assert sender.read_task_state(tmp_path, now=100.0) == {"state": "absent"}
    recorder = sender.TaskRecorder(tmp_path, interval=5.0, now=1000.0)
    path = sender.task_state_path(tmp_path)
    assert json.loads(path.read_text())["started_at"] == 1000.0
    # A cycle without work in the write interval does not write the file; a cycle with work, a failed cycle, and the end of the interval do.
    recorder.cycle_ok(sender.SendReport(), told_contributions=0, now=1005.0)
    assert json.loads(path.read_text())["cycles"] == 0
    recorder.cycle_ok(sender.SendReport(sent=1), told_contributions=0, now=1010.0)
    assert (json.loads(path.read_text())["cycles"], json.loads(path.read_text())["last_work_at"]) == (2, 1010.0)
    recorder.cycle_ok(sender.SendReport(), told_contributions=0, now=1010.0 + sender.STATE_WRITE_INTERVAL)
    assert json.loads(path.read_text())["cycles"] == 3
    recorder.cycle_failed("OperationalError", now=1100.0)
    state = sender.read_task_state(tmp_path, now=1110.0)
    assert (state["state"], state["last_cycle_ok"], state["last_error"], state["failed_cycles"], state["totals"]["sent"]) == ("recent", False, "OperationalError", 1, 1)
    assert sender.read_task_state(tmp_path, now=1100.0 + 2 * sender.STATE_WRITE_INTERVAL + 61)["state"] == "overdue"
    path.write_text("not json", encoding="utf-8")
    assert sender.read_task_state(tmp_path, now=1110.0)["state"] == "unreadable"


def test_account_and_server_bundles_include_safe_federation_diagnostics(tmp_path: Path):
    from claire.ops.account_support_bundle import create_account_support_bundle
    from claire.store import accounts as accounts_store

    now = time.time()
    db_file = tmp_path / "claire.db"
    conn = dbm.connect(db_file)
    dbm.init_db(conn)
    store = accounts_store.AccountStore(conn)
    store.complete_setup(1, username="admin_kim", display_name="Admin Kim")
    store.set_setting("mode", "network", updated_by=1)
    conn.execute("INSERT INTO accounts (id, public_id, username, role, status, display_name, profile_public, created_at, activated_at, updated_at) VALUES (7, 716412345678901234, 'fow', 'member', 'active', 'FoW', 1, ?, ?, ?)", (now - 1000, now - 1000, now - 1000))
    conn.execute("INSERT INTO accounts (id, public_id, username, role, status, display_name, profile_public, created_at, activated_at, updated_at) VALUES (8, 816412345678901234, 'other', 'member', 'active', 'Other', 1, ?, ?, ?)", (now - 1000, now - 1000, now - 1000))
    conn.execute("INSERT INTO knowledge_bases (id, seq, label, is_personal, is_public, created_at, updated_at, account_id, number) VALUES (70, 70, 'fow kb', 1, 1, ?, ?, 7, 516412345678901234)", (now - 500, now - 500))
    conn.execute("INSERT INTO federation_followers (public_id, account_id, actor_iri, inbox_iri, created_at, updated_at) VALUES (916412345678901234, 7, 'https://secret-remote.example/users/r1', 'https://secret-remote.example/users/r1/inbox', ?, ?)", (now - 300, now - 300))
    conn.execute("INSERT INTO federation_followers (public_id, account_id, actor_iri, inbox_iri, created_at, updated_at) VALUES (926412345678901234, 8, 'https://secret-other.example/users/r2', 'https://secret-other.example/users/r2/inbox', ?, ?)", (now - 300, now - 300))
    conn.execute("INSERT INTO federation_outbox (account_id, kind, status, error_code, created_at) VALUES (7, 'create', 'rejected', 'object_not_found', ?)", (now - 120,))
    conn.execute("INSERT INTO federation_outbox (account_id, kind, status, error_code, created_at) VALUES (8, 'create', 'rejected', 'other_account_err', ?)", (now - 120,))
    conn.commit()
    conn.close()

    cconn = client_db.connect_client_db(client_db.client_db_path(db_file))
    cconn.execute("INSERT INTO federation_following (account_id, request, actor_iri, state, created_at, updated_at) VALUES (7, '@bob@remote.example', 'https://secret-remote.example/users/bob', 'accepted', ?, ?)", (now - 200, now - 200))
    cconn.execute("INSERT INTO federation_client_outbox (account_id, kind, target_iri, status, error_code, created_at) VALUES (7, 'note', 'https://secret-remote.example/obj/1', 'rejected', 'delivery_failed', ?)", (now - 90,))
    cconn.execute("INSERT INTO federation_client_outbox (account_id, kind, target_iri, status, error_code, created_at) VALUES (8, 'note', 'https://secret-other.example/obj/2', 'rejected', 'other_client_err', ?)", (now - 90,))
    cconn.execute("INSERT INTO federation_notes (public_id, account_id, content_text, content_html, published_at, updated_at, quote_policy) VALUES (117660457792953794, 7, 'SECRET NOTE TEXT ALPHA', '<p>SECRET NOTE HTML ALPHA</p>', ?, ?, 'public')", (now - 60, now - 60))
    cconn.execute("INSERT INTO federation_notes (public_id, account_id, content_text, content_html, published_at, updated_at, quote_policy) VALUES (217660457792953794, 8, 'OTHER ACCOUNT NOTE TEXT', '<p>OTHER</p>', ?, ?, 'public')", (now - 60, now - 60))
    cconn.execute("INSERT INTO federation_object_reactions (object_public_id, account_id, kind, actor_iri, activity_iri, received_at) VALUES (117660457792953794, 7, 'announce', 'https://secret-remote.example/users/bob', 'https://secret-remote.example/act/1', ?)", (now - 30,))
    cconn.execute("INSERT INTO federation_object_reactions (object_public_id, account_id, kind, actor_iri, activity_iri, received_at) VALUES (217660457792953794, 8, 'like', 'https://secret-other.example/users/z', 'https://secret-other.example/act/2', ?)", (now - 30,))
    cconn.execute("INSERT INTO federation_quote_authorizations (account_id, object_public_id, quoting_object_iri, quoting_actor_iri, request_iri, state, created_at, updated_at) VALUES (7, 117660457792953794, 'https://secret-remote.example/q/1', 'https://secret-remote.example/users/bob', 'https://secret-remote.example/qr/1', 'pending', ?, ?)", (now - 20, now - 20))
    cconn.commit()
    cconn.close()

    run_dir = tmp_path / "run"
    run_dir.mkdir(parents=True, exist_ok=True)
    (run_dir / "federation_heartbeat.json").write_text(
        json.dumps(
            {
                "received_at": now - 15,
                "contract_version": "1.18",
                "sidecar_version": "0.1.0",
                "queue_depth": 0,
                "self_check": {"ok": True, "answer": None},
                "knowledge_self_check": {
                    "checked_at": now - 15,
                    "failed_paths": [],
                    "answers": [],
                    "signed": {
                        "draft_cavage": {"result": "ok", "answer": None},
                        "rfc9421": {"result": "failed", "answer": {"problem": "signature_invalid", "status": 404}},
                    },
                },
                "traffic": {
                    "window_seconds": 86400,
                    "inbound_verified": {"count": 2, "hosts": 1, "last_at": now - 20},
                    "inbound_rejected": {"count": 34, "last_at": now - 18},
                    "deliveries": {"succeeded": 1, "failed": {"dns": 0, "tls": 0, "timeout": 0, "http_4xx": 0, "http_5xx": 2, "other": 0}, "last_success_at": now - 25},
                },
            }
        ),
        encoding="utf-8",
    )

    settings: Any = StubSettings(db_file=db_file, data_dir=tmp_path)
    acct_info = create_account_support_bundle(settings, account_id=7, days=1, now_epoch=now)
    acct_members = _members(acct_info.filepath)
    acct_blob = b"\n".join(acct_members.values()).decode("utf-8", errors="replace")
    for forbidden in ("SECRET NOTE TEXT ALPHA", "SECRET NOTE HTML ALPHA", "OTHER ACCOUNT NOTE TEXT", "secret-remote.example", "secret-other.example", "other_account_err", "other_client_err", "217660457792953794"):
        assert forbidden not in acct_blob, forbidden

    fed = json.loads(acct_members["federation/summary.json"])
    acct_manifest = json.loads(acct_members["manifest.json"])
    assert fed["actor"]["is_actor"] is True and fed["actor"]["handle"] == "@fow@127.0.0.1:8765"
    assert fed["followers"] == {"total": 1}
    assert fed["notes"]["total"] == 1 and fed["notes"]["recent"][0]["public_id"] == "117660457792953794" and fed["notes"]["recent"][0]["announces"] == 1
    assert fed["reactions"]["by_kind"] == {"announce": 1}
    assert fed["quotes"]["by_state"] == {"pending": 1}
    assert fed["sidecar"]["signed_checks"]["rfc9421"] == {"result": "failed", "problem": "signature_invalid", "status": 404}
    assert fed["sidecar"]["traffic"]["inbound_rejected_count"] == 34 and fed["sidecar"]["traffic"]["deliveries_failed_total"] == 2
    expected_codes = {"SIDECAR_SIGNED_CHECK_FAILED", "INBOUND_SIGNATURES_REJECTED", "DELIVERIES_FAILED", "OUTBOX_TASKS_REJECTED", "CLIENT_OUTBOX_TASKS_REJECTED"}
    assert {f["code"] for f in fed["findings"]} == expected_codes
    assert acct_manifest["counts"]["federation_findings"] == len(expected_codes)
    assert acct_manifest["counts"]["federation_notes"] == 1

    srv_members = _members(create_support_bundle(settings, days=1, now_epoch=now).filepath)
    srv_manifest = json.loads(srv_members["manifest.json"])
    srv_client = json.loads(srv_members["federation/client_db.json"])
    assert set(srv_manifest["federation_findings"]) == {"SIDECAR_SIGNED_CHECK_FAILED", "INBOUND_SIGNATURES_REJECTED", "DELIVERIES_FAILED"}
    assert srv_client["federation_notes"]["total"] == 2
    assert srv_client["federation_object_reactions"]["by_kind"] == {"announce": 1, "like": 1}
    assert srv_client["federation_quote_authorizations"] == {"by_state": {"pending": 1}}
