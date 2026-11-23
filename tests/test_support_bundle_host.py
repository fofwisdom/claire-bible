"""The resource records of the host (host_runtime.py) and the Support Bundle member ``diagnostics/host.json``.

The unit test covers the file I/O of the recorder. The end-to-end test makes a server bundle from a data directory with the records of a host that restarted without a stop record, and reads the archive.
"""

from __future__ import annotations

import json
import tarfile
import time
from pathlib import Path

from compression import zstd

from claire.ops import host_runtime
from claire.ops.support_bundle import create_support_bundle
from claire.store import db as dbm
from tests.test_support_bundle_services import StubSettings

GIB = 1024**3


def _record(event: str, at: float, boot_id: str, *, mem_available: int = 20 * GIB, pressure: float = 0.0, rss: int = 300 * 1024**2) -> dict:
    return {
        "event": event,
        "service": "api",
        "at": at,
        "boot_id": boot_id,
        "host": {"uptime_seconds": 1000.0, "mem_total": 32 * GIB, "mem_available": mem_available, "swap_total": 8 * GIB, "swap_free": 8 * GIB, "load_1": 1.0, "load_5": 1.0, "pressure": {"memory": {"some": pressure, "full": pressure}, "io": None, "cpu": None}, "data_dir_free": 100 * GIB},
        "cgroup": {"memory_current": rss, "memory_max": None, "swap_current": 0, "oom": 0, "oom_kill": 0},
        "process": {"pid": 1, "rss": rss, "threads": 10, "open_files": 50},
    }


def _write(path: Path, records: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    # The last line is a line that the stop of the host cut.
    path.write_text("".join(json.dumps(record) + "\n" for record in records) + '{"event":"sample","at":', encoding="utf-8")


def test_recorder_writes_start_sample_and_stop_lines(tmp_path: Path):
    recorder = host_runtime._Recorder(tmp_path, "api", interval=0.01)
    recorder.write("start")
    recorder.write("sample")
    recorder.stop()
    recorder.stop()
    lines = [json.loads(line) for line in host_runtime.host_log_path(tmp_path, "api").read_text(encoding="utf-8").splitlines()]
    assert [line["event"] for line in lines] == ["start", "sample", "stop"]
    assert lines[0]["service"] == "api" and lines[0]["process"]["pid"] > 0 and lines[0]["host"]["data_dir_free"] > 0
    assert set(lines[0]) == {"event", "service", "at", "boot_id", "host", "cgroup", "process"}


def test_recorder_moves_a_full_file_to_the_old_file(tmp_path: Path, monkeypatch):
    monkeypatch.setattr(host_runtime, "_LOG_MAX_BYTES", 10)
    recorder = host_runtime._Recorder(tmp_path, "api", interval=60)
    recorder.write("start")
    recorder.write("sample")
    path = host_runtime.host_log_path(tmp_path, "api")
    assert json.loads(path.with_name("host-api.log.1").read_text(encoding="utf-8"))["event"] == "start"
    assert json.loads(path.read_text(encoding="utf-8"))["event"] == "sample"
    records = host_runtime._read_records(path, since=0)
    assert [record["event"] for record in records] == ["start", "sample"]


def test_network_probe_reports_the_step_that_failed():
    import socket

    assert host_runtime.probe_target("http://127.0.0.1:8765") is None and host_runtime.probe_target("") is None
    assert host_runtime.probe_target("https://claire.example/x") == ("claire.example", 443)
    with socket.socket() as listener:
        listener.bind(("127.0.0.1", 0))
        listener.listen(1)
        port = listener.getsockname()[1]
        ok = host_runtime.probe_network(("localhost", port))
    assert ok["ok"] is True and ok["failed_step"] is None and ok["connect_seconds"] >= 0
    closed = host_runtime.probe_network(("localhost", port))
    assert (closed["ok"], closed["failed_step"]) == (False, "connect") and "ConnectionRefusedError" in closed["error"]
    unknown = host_runtime.probe_network(("no-such-host.invalid", 443))
    assert (unknown["ok"], unknown["failed_step"]) == (False, "dns")


def test_api_probe_reports_a_blocked_event_loop_and_writes_the_stacks(tmp_path: Path, monkeypatch):
    import asyncio
    import socket
    import threading

    monkeypatch.setattr(host_runtime, "_PROBE_TIMEOUT_SECONDS", 0.3)
    with socket.socket() as unused:
        unused.bind(("127.0.0.1", 0))
        port = unused.getsockname()[1]
    probe = host_runtime.ApiProbe(tmp_path, ("0.0.0.0", port))
    assert probe.host == "127.0.0.1"
    loop = asyncio.new_event_loop()
    thread = threading.Thread(target=loop.run_forever, daemon=True)
    thread.start()
    try:
        probe.loop = loop
        state = probe._loop_state()
        assert state["loop_blocked"] is False and state["loop_lag_seconds"] < 0.3 and state["executor_queued"] == 0

        def block_the_event_loop() -> None:
            time.sleep(1.0)

        loop.call_soon_threadsafe(block_the_event_loop)
        time.sleep(0.1)
        sample = probe.sample()
        assert sample["loop_blocked"] is True and sample["request"]["ok"] is False and sample["stack_dump"] is True
    finally:
        loop.call_soon_threadsafe(loop.stop)
        thread.join(timeout=5)
        loop.close()
    dump = probe.stall_log.read_text(encoding="utf-8")
    assert "api stall" in dump and "block_the_event_loop" in dump and "ConnectionRefusedError" in dump


def test_server_bundle_shows_a_host_restart_without_a_stop_record(tmp_path: Path):
    now = time.time()
    db_file = tmp_path / "claire.db"
    conn = dbm.connect(db_file)
    dbm.init_db(conn)
    conn.close()
    crash = now - 6 * 3600
    records = [_record("start", crash - 600, "boot-a")]
    # The available memory of the host goes down and the pressure goes up before the last record.
    records += [_record("sample", crash - 540 + 60 * step, "boot-a", mem_available=(8 - step) * GIB // 8, pressure=10.0 * step, rss=(1 + step) * GIB) for step in range(8)]
    records += [_record("start", now - 300, "boot-b"), _record("sample", now - 240, "boot-b")]
    # Before the stop of the host: the event loop of the api process does not answer in the last two records.
    for record in records[7:9]:
        record["api"] = {"loop_attached": True, "loop_blocked": True, "loop_lag_seconds": None, "tasks": 40, "executor_threads": 7, "executor_max_threads": 7, "executor_queued": 12, "threads": 20, "request": {"ok": False, "status": None, "seconds": 5.0, "error": "TimeoutError: timed out"}, "stack_dump": True}
    (tmp_path / "logs").mkdir(parents=True, exist_ok=True)
    (tmp_path / "logs" / "api-stall.log").write_text("===== 2026-11-23T06:05:00Z api stall: {}\nThread 0x01 (most recent call first):\n", encoding="utf-8")
    # After the restart: two probes fail, then the network comes back.
    for offset, ok in ((-180, False), (-120, False), (-60, True)):
        sample = _record("sample", now + offset, "boot-b")
        sample["network"] = {"host": "claire.example", "port": 443, "ok": ok, "failed_step": None if ok else "dns", "error": None if ok else "gaierror: [Errno -3] Temporary failure in name resolution", "dns_seconds": 0.1, "connect_seconds": None}
        records.append(sample)
    _write(host_runtime.host_log_path(tmp_path, "api"), records)
    # A correct stop and start of a worker loop on the same boot gives no finding.
    _write(host_runtime.host_log_path(tmp_path, "expand-loop"), [_record("start", now - 900, "boot-b"), _record("stop", now - 600, "boot-b"), _record("start", now - 500, "boot-b")])

    info = create_support_bundle(StubSettings(db_file=db_file, data_dir=tmp_path), days=1, now_epoch=now)
    members: dict[str, bytes] = {}
    with open(info.filepath, "rb") as handle, zstd.ZstdFile(handle, "rb") as reader, tarfile.open(fileobj=reader, mode="r|") as tar:
        for member in tar:
            data = tar.extractfile(member)
            if data is not None:
                members[member.name.split("/", 1)[1]] = data.read()

    host = json.loads(members["diagnostics/host.json"])
    codes = [finding["code"] for finding in host["findings"]]
    assert codes == ["HOST_RESTART_WITHOUT_STOP", "HOST_MEMORY_LOW_BEFORE_RESTART", "HOST_NETWORK_UNREACHABLE", "API_EVENT_LOOP_BLOCKED", "API_EXECUTOR_QUEUE", "HOST_MEMORY_PRESSURE"]
    (stall,) = host["services"]["api"]["api_stalls"]
    assert (stall["from"], stall["to"], stall["records"], stall["loop_blocked"], stall["executor_queued_max"]) == (crash - 180, crash - 120, 2, True, 12)
    assert "recovered_at" not in stall and b"api stall" in members["logs/api-stall.log"]
    (outage,) = host["services"]["api"]["network_outages"]
    assert (outage["from"], outage["to"], outage["recovered_at"], outage["failed_probes"], outage["failed_steps"]) == (now - 180, now - 120, now - 60, 2, ["dns"])
    assert json.loads(members["manifest.json"])["host_findings"] == codes
    api = host["services"]["api"]
    assert api["records"] == len(records)
    (restart,) = api["restarts"]
    assert restart["host_restarted"] is True and restart["stop_recorded"] is False
    assert restart["last_record_before_at"] == crash - 120 and restart["gap_seconds"] > 5 * 3600
    assert len(restart["records_before"]) == 9 and restart["records_before"][-1]["process"]["rss"] == 8 * GIB
    assert api["peaks"]["process_rss_max"] == 8 * GIB and api["peaks"]["host_pressure_memory_full_max"] == 70.0
    (loop_restart,) = host["services"]["expand-loop"]["restarts"]
    assert loop_restart["host_restarted"] is False and loop_restart["stop_recorded"] is True
    assert host["now"]["process"]["pid"] > 0
    # The bundle also has the record files as logs.
    assert b'"boot-a"' in members["logs/host-api.log"]
