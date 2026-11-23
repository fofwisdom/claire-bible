"""Resource records of the host and of each Python service (``api``, ``telegram``, the worker loops).

After a host that did not respond gets a forced restart, the kernel data of ``/proc`` is new and the cause is gone. The Support Bundle had no record from before the restart. This module gives that record:

- ``logs/host-<service>.log``: one JSON line each 60 seconds, written with ``fsync``, thus the last lines before a stop of the host stay on the disk (rotation at 5 MB, 1 old file). A line has the kernel boot ID, the memory, swap, load and pressure (PSI) of the host, the memory of the container (cgroup v2), and the memory, threads and open files of the process. The lines ``start`` and ``stop`` mark the start and the correct stop of the process.
- ``collect_host`` reads these files for ``diagnostics/host.json`` of the server bundle: the state now, the peaks of the period, and each restart with the last samples before it.

A container sees the memory of the host in ``/proc/meminfo``, but only its own cgroup and its own processes. Thus each service writes its own file. The Fedify sidecar is not a Python service and has no file.

A failure to read a value gives ``None``. A failure to write a record does not stop the service.
"""

from __future__ import annotations

import atexit
import json
import os
import shutil
import sys
import threading
import time
from pathlib import Path
from typing import Any

__all__ = ["collect_host", "host_log_path", "read_sample", "start_host_recorder"]

SAMPLE_INTERVAL_SECONDS = 60.0
_LOG_MAX_BYTES = 5 * 1024 * 1024
_SAMPLES_BEFORE_RESTART = 10
# A gap between two samples of the same boot that is longer than this is a time when the process did not run or could not run.
_STALL_SECONDS = 5 * SAMPLE_INTERVAL_SECONDS
_LOW_MEMORY_FRACTION = 0.05
_HIGH_PRESSURE_PERCENT = 25.0
_API_SERVICE = "api"

_PROC = Path("/proc")
_CGROUP = Path("/sys/fs/cgroup")

_recorders: dict[str, "_Recorder"] = {}


def host_log_path(data_dir: Path | str, service: str) -> Path:
    return Path(data_dir) / "logs" / f"host-{service}.log"


def _text(path: Path) -> str | None:
    try:
        return path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return None


def _int(path: Path) -> int | None:
    text = _text(path)
    try:
        return int(text.strip()) if text is not None else None
    except ValueError:
        return None  # "max" of memory.max: no limit.


def _key_values(text: str | None, *, separator: str | None = None) -> dict[str, str]:
    out: dict[str, str] = {}
    for line in (text or "").splitlines():
        key, _, value = line.partition(separator) if separator else line.partition(" ")
        if value:
            out[key.strip()] = value.strip()
    return out


def _kb(values: dict[str, str], key: str) -> int | None:
    try:
        return int(values[key].split()[0]) * 1024
    except (KeyError, ValueError, IndexError):
        return None


def _pressure(resource: str) -> dict[str, float] | None:
    """``avg10`` of the lines ``some`` and ``full`` of ``/proc/pressure/<resource>``: the percent of the last 10 seconds in which tasks waited for the resource."""
    text = _text(_PROC / "pressure" / resource)
    if text is None:
        return None
    out: dict[str, float] = {}
    for line in text.splitlines():
        kind, _, rest = line.partition(" ")
        for field in rest.split():
            name, _, value = field.partition("=")
            if name == "avg10":
                try:
                    out[kind] = float(value)
                except ValueError:
                    pass
    return out or None


def read_sample(data_dir: Path | str, *, now: float | None = None) -> dict[str, Any]:
    """One sample of the host, the container, and this process."""
    meminfo = _key_values(_text(_PROC / "meminfo"), separator=":")
    load = (_text(_PROC / "loadavg") or "").split()
    uptime = (_text(_PROC / "uptime") or "").split()
    status = _key_values(_text(_PROC / "self" / "status"), separator=":")
    events = _key_values(_text(_CGROUP / "memory.events"))
    try:
        open_files: int | None = len(os.listdir(_PROC / "self" / "fd"))
    except OSError:
        open_files = None
    try:
        disk_free: int | None = shutil.disk_usage(data_dir).free
    except OSError:
        disk_free = None

    def _float(values: list[str], index: int) -> float | None:
        try:
            return float(values[index])
        except (IndexError, ValueError):
            return None

    def _count(values: dict[str, str], key: str) -> int | None:
        try:
            return int(values[key])
        except (KeyError, ValueError):
            return None

    boot_id = _text(_PROC / "sys" / "kernel" / "random" / "boot_id")
    return {
        "at": now if now is not None else time.time(),
        "boot_id": boot_id.strip() if boot_id else None,
        "host": {
            "uptime_seconds": _float(uptime, 0),
            "mem_total": _kb(meminfo, "MemTotal"),
            "mem_available": _kb(meminfo, "MemAvailable"),
            "swap_total": _kb(meminfo, "SwapTotal"),
            "swap_free": _kb(meminfo, "SwapFree"),
            "load_1": _float(load, 0),
            "load_5": _float(load, 1),
            "pressure": {name: _pressure(name) for name in ("memory", "io", "cpu")},
            "data_dir_free": disk_free,
        },
        "cgroup": {
            "memory_current": _int(_CGROUP / "memory.current"),
            "memory_max": _int(_CGROUP / "memory.max"),
            "swap_current": _int(_CGROUP / "memory.swap.current"),
            "oom": _count(events, "oom"),
            "oom_kill": _count(events, "oom_kill"),
        },
        "process": {
            "pid": os.getpid(),
            "rss": _kb(status, "VmRSS"),
            "threads": _count(status, "Threads"),
            "open_files": open_files,
        },
    }


class _Recorder:
    def __init__(self, data_dir: Path | str, service: str, interval: float) -> None:
        self.data_dir = Path(data_dir)
        self.service = service
        self.interval = interval
        self.path = host_log_path(data_dir, service)
        self._stop = threading.Event()
        self._lock = threading.Lock()
        self._failed = False

    def write(self, event: str) -> None:
        record = {"event": event, "service": self.service, **read_sample(self.data_dir)}
        line = json.dumps(record, ensure_ascii=False, separators=(",", ":")) + "\n"
        with self._lock:
            try:
                if event == "start":
                    self.path.parent.mkdir(parents=True, exist_ok=True)
                elif not self.path.parent.is_dir():
                    return  # The data directory is gone (a test directory): do not make it again.
                if self.path.is_file() and self.path.stat().st_size > _LOG_MAX_BYTES:
                    os.replace(self.path, self.path.with_name(self.path.name + ".1"))
                with open(self.path, "a", encoding="utf-8") as stream:
                    stream.write(line)
                    stream.flush()
                    os.fsync(stream.fileno())
                self._failed = False
            except OSError as exc:
                if not self._failed:  # One message for each sequence of failures.
                    print(f"[{self.service}] Cannot write the host record {self.path}: {type(exc).__name__}: {exc}", file=sys.stderr, flush=True)
                self._failed = True

    def run(self) -> None:
        self.write("start")
        while not self._stop.wait(self.interval):
            self.write("sample")

    def stop(self) -> None:
        if not self._stop.is_set():
            self._stop.set()
            self.write("stop")


def start_host_recorder(data_dir: Path | str, service: str, *, interval: float = SAMPLE_INTERVAL_SECONDS) -> None:
    """Start the thread that writes ``logs/host-<service>.log``. A second call for the same service does nothing. The line ``stop`` is written when the process stops correctly."""
    if service in _recorders:
        return
    recorder = _Recorder(data_dir, service, interval)
    _recorders[service] = recorder
    threading.Thread(target=recorder.run, name=f"host-recorder-{service}", daemon=True).start()
    atexit.register(recorder.stop)


# --- Support Bundle ---------------------------------------------------------------------------------


def _read_records(path: Path, since: float) -> list[dict[str, Any]]:
    """The records of the period from the old file and the current file. The last record before the period stays: it is the state before the first record of the period."""
    records: list[dict[str, Any]] = []
    for source in (path.with_name(path.name + ".1"), path):
        text = _text(source)
        for line in (text or "").splitlines():
            try:
                record = json.loads(line)
            except ValueError:
                continue  # A line that a stop of the host cut.
            if isinstance(record, dict) and isinstance(record.get("at"), (int, float)):
                records.append(record)
    records.sort(key=lambda record: record["at"])
    first = next((index for index, record in enumerate(records) if record["at"] >= since), len(records))
    return records[max(0, first - 1):]


def _get(record: dict[str, Any], *keys: str) -> Any:
    value: Any = record
    for key in keys:
        value = value.get(key) if isinstance(value, dict) else None
    return value


def _numbers(records: list[dict[str, Any]], *keys: str) -> list[float]:
    return [value for value in (_get(record, *keys) for record in records) if isinstance(value, (int, float))]


def _service_report(service: str, records: list[dict[str, Any]], findings: list[dict[str, Any]]) -> dict[str, Any]:
    restarts: list[dict[str, Any]] = []
    stalls: list[dict[str, Any]] = []
    for index in range(1, len(records)):
        previous, record = records[index - 1], records[index]
        gap = record["at"] - previous["at"]
        boot_changed = bool(record.get("boot_id")) and bool(previous.get("boot_id")) and record["boot_id"] != previous["boot_id"]
        if record.get("event") == "start" or boot_changed:
            before = records[max(0, index - _SAMPLES_BEFORE_RESTART):index]
            mem_total = _get(previous, "host", "mem_total")
            mem_available = _get(previous, "host", "mem_available")
            restart = {
                "started_at": record["at"],
                "last_record_before_at": previous["at"],
                "gap_seconds": gap,
                "host_restarted": boot_changed,
                "stop_recorded": previous.get("event") == "stop",
                "records_before": before,
            }
            restarts.append(restart)
            if boot_changed and previous.get("event") != "stop" and service == _API_SERVICE:
                findings.append({"code": "HOST_RESTART_WITHOUT_STOP", "message": f"The host restarted and the service {service} has no stop record. Last record at {previous['at']:.0f}, next start at {record['at']:.0f}."})
            if previous.get("event") != "stop" and isinstance(mem_total, (int, float)) and isinstance(mem_available, (int, float)) and mem_total > 0 and mem_available / mem_total < _LOW_MEMORY_FRACTION:
                findings.append({"code": "HOST_MEMORY_LOW_BEFORE_RESTART", "message": f"The last record of the service {service} before a restart has {mem_available / mem_total:.1%} of the host memory available."})
            before_kill, after_kill = _get(previous, "cgroup", "oom_kill"), _get(record, "cgroup", "oom_kill")
            if not boot_changed and isinstance(before_kill, int) and isinstance(after_kill, int) and after_kill > before_kill:
                findings.append({"code": "CGROUP_OOM_KILL", "message": f"The kernel stopped a process of the container of the service {service} because of the memory limit (oom_kill {before_kill} -> {after_kill})."})
        elif gap > _STALL_SECONDS:
            stalls.append({"from": previous["at"], "to": record["at"], "gap_seconds": gap})
    if stalls:
        findings.append({"code": "HOST_RECORD_STALLED", "message": f"The service {service} wrote no record for more than {_STALL_SECONDS:.0f} seconds, {len(stalls)} times. The process or the host did not run in that time."})
    pressure = _numbers(records, "host", "pressure", "memory", "full")
    if pressure and max(pressure) >= _HIGH_PRESSURE_PERCENT:
        findings.append({"code": "HOST_MEMORY_PRESSURE", "message": f"The memory pressure (full, avg10) of the host was {max(pressure):.1f}% in a record of the service {service}."})

    def _peak(function: Any, *keys: str) -> float | None:
        values = _numbers(records, *keys)
        return function(values) if values else None

    return {
        "records": len(records),
        "first_at": records[0]["at"] if records else None,
        "last_at": records[-1]["at"] if records else None,
        "last_record": records[-1] if records else None,
        "peaks": {
            "host_mem_available_min": _peak(min, "host", "mem_available"),
            "host_swap_free_min": _peak(min, "host", "swap_free"),
            "host_load_1_max": _peak(max, "host", "load_1"),
            "host_pressure_memory_full_max": _peak(max, "host", "pressure", "memory", "full"),
            "host_pressure_io_full_max": _peak(max, "host", "pressure", "io", "full"),
            "host_data_dir_free_min": _peak(min, "host", "data_dir_free"),
            "cgroup_memory_current_max": _peak(max, "cgroup", "memory_current"),
            "process_rss_max": _peak(max, "process", "rss"),
            "process_open_files_max": _peak(max, "process", "open_files"),
        },
        "restarts": restarts,
        "stalls": stalls,
    }


def collect_host(settings: Any, *, now: float, since: float) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    """``diagnostics/host.json``: the state now, and for each service the peaks of the period and each restart with the last records before it."""
    data_dir = Path(settings.data_dir)
    warnings: list[dict[str, Any]] = []
    findings: list[dict[str, Any]] = []
    services: dict[str, Any] = {}
    logs_dir = data_dir / "logs"
    for path in sorted(logs_dir.glob("host-*.log")) if logs_dir.is_dir() else []:
        service = path.name[len("host-"):-len(".log")]
        try:
            services[service] = _service_report(service, _read_records(path, since), findings)
        except Exception as exc:  # noqa: BLE001
            services[service] = {"state": "error", "error": f"{type(exc).__name__}: {exc}"}
            warnings.append({"code": "HOST_COLLECTOR_FAILED", "message": f"{service}: {type(exc).__name__}: {exc}"})
    report = {
        "sample_interval_seconds": SAMPLE_INTERVAL_SECONDS,
        "now": read_sample(data_dir, now=now),
        "findings": findings,
        "services": services,
    }
    return report, warnings
