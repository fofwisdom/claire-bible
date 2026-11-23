"""Resource records of the host and of each Python service (``api``, ``telegram``, the worker loops).

After a host that did not respond gets a forced restart, the kernel data of ``/proc`` is new and the cause is gone. The Support Bundle had no record from before the restart. This module gives that record:

- ``logs/host-<service>.log``: one JSON line each 60 seconds, written with ``fsync``, thus the last lines before a stop of the host stay on the disk (rotation at 5 MB, 1 old file). A line has the kernel boot ID, the memory, swap, load and pressure (PSI) of the host, the memory of the container (cgroup v2), and the memory, threads and open files of the process. The lines ``start`` and ``stop`` mark the start and the correct stop of the process.
- ``collect_host`` reads these files for ``diagnostics/host.json`` of the server bundle: the state now, the peaks of the period, and each restart with the last samples before it.

The ``api`` service also adds a network probe of the public host of the server to each line: the name resolution and a TCP connection. A time in which the server was not reachable then shows as a sequence of failed probes, and not only as a time without lines in ``api.log``.

The ``api`` service also adds a probe of its own process to each line (``ApiProbe``): the delay of the event loop, the threads and the queue of the default executor (``asyncio.to_thread`` uses it), and one request ``GET /health`` to its own listener. When the event loop does not answer or the request fails, the probe appends the stack of each thread and of each asyncio task to ``logs/api-stall.log``. A time in which the web service does not answer while the process runs then has a cause in the bundle.

A container sees the memory of the host in ``/proc/meminfo``, but only its own cgroup and its own processes. Thus each service writes its own file. The Fedify sidecar is not a Python service and has no file.

A failure to read a value gives ``None``. A failure to write a record does not stop the service.
"""

from __future__ import annotations

import atexit
import faulthandler
import http.client
import json
import os
import shutil
import socket
import sys
import threading
import time
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

__all__ = ["ApiProbe", "collect_host", "host_log_path", "read_sample", "set_api_loop", "start_host_recorder"]

SAMPLE_INTERVAL_SECONDS = 60.0
_LOG_MAX_BYTES = 5 * 1024 * 1024
_SAMPLES_BEFORE_RESTART = 10
# A gap between two samples of the same boot that is longer than this is a time when the process did not run or could not run.
_STALL_SECONDS = 5 * SAMPLE_INTERVAL_SECONDS
_LOW_MEMORY_FRACTION = 0.05
_HIGH_PRESSURE_PERCENT = 25.0
_API_SERVICE = "api"
_PROBE_TIMEOUT_SECONDS = 5.0
_STALL_LOG_MAX_BYTES = 5 * 1024 * 1024
_TASK_STACK_FRAMES = 8
_MAX_TASKS_IN_DUMP = 200

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


def probe_target(public_url: str | None) -> tuple[str, int] | None:
    """The host and the port of the public address. ``None`` for an empty address and for a loopback or literal IP host (a development server): the probe then has no value."""
    parts = urlsplit(public_url or "")
    host = parts.hostname
    if not host or host == "localhost" or host.replace(".", "").isdigit() or ":" in host:
        return None
    return host, parts.port or (443 if parts.scheme == "https" else 80)


def probe_network(target: tuple[str, int]) -> dict[str, Any]:
    """Resolve the name and open one TCP connection. The result has the step that failed (``dns`` or ``connect``) and the time of each step."""
    host, port = target
    out: dict[str, Any] = {"host": host, "port": port, "ok": False, "failed_step": None, "error": None, "dns_seconds": None, "connect_seconds": None}
    started = time.monotonic()
    try:
        addresses = socket.getaddrinfo(host, port, type=socket.SOCK_STREAM)
    except OSError as exc:
        out.update(failed_step="dns", error=f"{type(exc).__name__}: {exc}", dns_seconds=time.monotonic() - started)
        return out
    out["dns_seconds"] = time.monotonic() - started
    started = time.monotonic()
    family, kind, protocol, _, address = addresses[0]
    try:
        with socket.socket(family, kind, protocol) as connection:
            connection.settimeout(_PROBE_TIMEOUT_SECONDS)
            connection.connect(address)
    except OSError as exc:
        out.update(failed_step="connect", error=f"{type(exc).__name__}: {exc}", connect_seconds=time.monotonic() - started)
        return out
    out.update(ok=True, connect_seconds=time.monotonic() - started)
    return out


class ApiProbe:
    """Probe of the ``api`` process from the recorder thread. ``loop`` is set when the application starts (``set_api_loop``)."""

    def __init__(self, data_dir: Path | str, address: tuple[str, int]) -> None:
        self.stall_log = Path(data_dir) / "logs" / "api-stall.log"
        # A listener on all addresses answers on the loopback address.
        self.host = "127.0.0.1" if address[0] in ("0.0.0.0", "") else address[0]
        self.port = int(address[1])
        self.loop: Any = None

    def _loop_state(self) -> dict[str, Any]:
        loop = self.loop
        if loop is None:
            return {"loop_attached": False, "loop_blocked": None, "loop_lag_seconds": None, "tasks": None, "executor_threads": None, "executor_queued": None}
        answered = threading.Event()
        started = time.monotonic()
        blocked: bool | None
        lag: float | None = None
        try:
            loop.call_soon_threadsafe(answered.set)
            blocked = not answered.wait(_PROBE_TIMEOUT_SECONDS)
            if not blocked:
                lag = time.monotonic() - started
        except RuntimeError:  # The loop is closed: the process stops.
            blocked = None
        executor = getattr(loop, "_default_executor", None)
        queue = getattr(executor, "_work_queue", None)
        try:
            import asyncio

            tasks: int | None = len(asyncio.all_tasks(loop))
        except RuntimeError:
            tasks = None
        return {
            "loop_attached": True,
            "loop_blocked": blocked,
            "loop_lag_seconds": lag,
            "tasks": tasks,
            "executor_threads": len(getattr(executor, "_threads", ())) if executor is not None else 0,
            "executor_max_threads": getattr(executor, "_max_workers", None),
            "executor_queued": queue.qsize() if queue is not None else 0,
        }

    def _request(self) -> dict[str, Any]:
        started = time.monotonic()
        connection = http.client.HTTPConnection(self.host, self.port, timeout=_PROBE_TIMEOUT_SECONDS)
        try:
            connection.request("GET", "/health")
            status: int | None = connection.getresponse().status
            error = None
        except OSError as exc:
            status, error = None, f"{type(exc).__name__}: {exc}"
        finally:
            connection.close()
        return {"ok": status is not None, "status": status, "seconds": time.monotonic() - started, "error": error}

    def sample(self) -> dict[str, Any]:
        out = self._loop_state()
        out["threads"] = threading.active_count()
        out["request"] = self._request()
        if out["loop_blocked"] or not out["request"]["ok"]:
            out["stack_dump"] = self._dump(out)
        return out

    def _dump(self, state: dict[str, Any]) -> bool:
        """Append the stack of each thread and of each asyncio task to ``logs/api-stall.log``. The stacks have file names, line numbers and function names, no values."""
        try:
            self.stall_log.parent.mkdir(parents=True, exist_ok=True)
            if self.stall_log.is_file() and self.stall_log.stat().st_size > _STALL_LOG_MAX_BYTES:
                os.replace(self.stall_log, self.stall_log.with_name(self.stall_log.name + ".1"))
            with open(self.stall_log, "a", encoding="utf-8") as stream:
                stamp = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
                stream.write(f"===== {stamp} api stall: {json.dumps(state, default=str)}\n")
                stream.flush()
                faulthandler.dump_traceback(file=stream, all_threads=True)
                stream.write(self._task_stacks())
                stream.flush()
                os.fsync(stream.fileno())
            return True
        except Exception:  # noqa: BLE001
            return False

    def _task_stacks(self) -> str:
        if self.loop is None:
            return ""
        lines: list[str] = []
        try:
            import asyncio

            tasks = list(asyncio.all_tasks(self.loop))
        except RuntimeError as exc:
            return f"asyncio tasks: not readable ({exc})\n"
        lines.append(f"asyncio tasks: {len(tasks)}")
        for task in tasks[:_MAX_TASKS_IN_DUMP]:
            try:
                frames = task.get_stack(limit=_TASK_STACK_FRAMES)
                where = " <- ".join(f"{frame.f_code.co_filename}:{frame.f_lineno} {frame.f_code.co_name}" for frame in reversed(frames))
                lines.append(f"  task {task.get_name()}: {where or 'no frame'}")
            except Exception as exc:  # noqa: BLE001
                lines.append(f"  task: not readable ({type(exc).__name__})")
        return "\n".join(lines) + "\n"


_api_probe: ApiProbe | None = None


def set_api_loop(loop: Any) -> None:
    """Give the running event loop to the probe of the ``api`` service. Without a started recorder with a probe, this does nothing."""
    if _api_probe is not None:
        _api_probe.loop = loop


class _Recorder:
    def __init__(self, data_dir: Path | str, service: str, interval: float, probe: tuple[str, int] | None = None, api_probe: ApiProbe | None = None) -> None:
        self.probe = probe
        self.api_probe = api_probe
        self.data_dir = Path(data_dir)
        self.service = service
        self.interval = interval
        self.path = host_log_path(data_dir, service)
        self._stop = threading.Event()
        self._lock = threading.Lock()
        self._failed = False

    def write(self, event: str) -> None:
        record = {"event": event, "service": self.service, **read_sample(self.data_dir)}
        if self.probe is not None and event != "stop":  # The probe can take seconds: it must not delay the stop.
            try:
                record["network"] = probe_network(self.probe)
            except Exception as exc:  # noqa: BLE001
                record["network"] = {"ok": None, "error": f"{type(exc).__name__}: {exc}"}
        if self.api_probe is not None and event == "sample":  # At the start the listener is not open; at the stop it is closed.
            try:
                record["api"] = self.api_probe.sample()
            except Exception as exc:  # noqa: BLE001
                record["api"] = {"error": f"{type(exc).__name__}: {exc}"}
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


def start_host_recorder(data_dir: Path | str, service: str, *, interval: float = SAMPLE_INTERVAL_SECONDS, public_url: str | None = None, api_address: tuple[str, int] | None = None) -> None:
    """Start the thread that writes ``logs/host-<service>.log``. A second call for the same service does nothing. The line ``stop`` is written when the process stops correctly. With ``public_url``, each line has a network probe of that host. With ``api_address`` (the listener of the ``api`` process), each line has the probe of the ``api`` process (``ApiProbe``)."""
    if service in _recorders:
        return
    global _api_probe
    api_probe = ApiProbe(data_dir, api_address) if api_address else None
    if api_probe is not None:
        _api_probe = api_probe
    recorder = _Recorder(data_dir, service, interval, probe_target(public_url), api_probe)
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
    outages: list[dict[str, Any]] = []
    open_outage: dict[str, Any] | None = None
    for record in records:
        ok = _get(record, "network", "ok")
        if ok is False:
            if open_outage is None:
                open_outage = {"from": record["at"], "to": record["at"], "failed_probes": 0, "failed_steps": [], "last_error": None}
                outages.append(open_outage)
            step = _get(record, "network", "failed_step")
            open_outage.update(to=record["at"], failed_probes=open_outage["failed_probes"] + 1, last_error=_get(record, "network", "error"))
            if step and step not in open_outage["failed_steps"]:
                open_outage["failed_steps"].append(step)
        elif ok is True or record.get("event") == "start":
            if open_outage is not None and ok is True:
                open_outage["recovered_at"] = record["at"]
            open_outage = None
    if outages:
        findings.append({"code": "HOST_NETWORK_UNREACHABLE", "message": f"The network probe of the public host failed in {len(outages)} sequences ({sum(item['failed_probes'] for item in outages)} probes) in the records of the service {service}. The process ran in that time."})
    api_stalls: list[dict[str, Any]] = []
    open_stall: dict[str, Any] | None = None
    for record in records:
        blocked = _get(record, "api", "loop_blocked")
        request_ok = _get(record, "api", "request", "ok")
        if blocked is True or request_ok is False:
            if open_stall is None:
                open_stall = {"from": record["at"], "to": record["at"], "records": 0, "loop_blocked": False, "request_failed": False, "last_request_error": None, "executor_queued_max": 0, "first_record": record}
                api_stalls.append(open_stall)
            queued = _get(record, "api", "executor_queued")
            open_stall.update(
                to=record["at"],
                records=open_stall["records"] + 1,
                loop_blocked=open_stall["loop_blocked"] or blocked is True,
                request_failed=open_stall["request_failed"] or request_ok is False,
                last_request_error=_get(record, "api", "request", "error") or open_stall["last_request_error"],
                executor_queued_max=max(open_stall["executor_queued_max"], queued if isinstance(queued, int) else 0),
            )
        elif request_ok is True or record.get("event") == "start":
            if open_stall is not None and request_ok is True:
                open_stall["recovered_at"] = record["at"]
            open_stall = None
    if any(item["loop_blocked"] for item in api_stalls):
        findings.append({"code": "API_EVENT_LOOP_BLOCKED", "message": f"The event loop of the api process did not answer in {_PROBE_TIMEOUT_SECONDS:.0f} seconds, in {sum(1 for item in api_stalls if item['loop_blocked'])} sequences. See logs/api-stall.log for the stacks."})
    if any(item["request_failed"] and not item["loop_blocked"] for item in api_stalls):
        findings.append({"code": "API_REQUEST_FAILED", "message": f"The request GET /health of the api process to itself failed while the event loop answered, in {sum(1 for item in api_stalls if item['request_failed'] and not item['loop_blocked'])} sequences. See logs/api-stall.log for the stacks."})
    queued_values = _numbers(records, "api", "executor_queued")
    if queued_values and max(queued_values) > 0:
        findings.append({"code": "API_EXECUTOR_QUEUE", "message": f"Calls waited for a thread of the default executor of the api process (highest queue: {max(queued_values):.0f})."})
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
            "api_loop_lag_max": _peak(max, "api", "loop_lag_seconds"),
            "api_request_seconds_max": _peak(max, "api", "request", "seconds"),
            "api_executor_queued_max": _peak(max, "api", "executor_queued"),
            "api_tasks_max": _peak(max, "api", "tasks"),
            "api_threads_max": _peak(max, "api", "threads"),
        },
        "restarts": restarts,
        "stalls": stalls,
        "network_outages": outages,
        "api_stalls": api_stalls,
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
