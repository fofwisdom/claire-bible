"""Runtime records of the worker loops (``refresh-loop``, ``recover-loop``, ``expand-loop``).

Each loop runs in its own Compose service and writes only to its standard output. The Support Bundle could not see these services. This module gives two records in the data directory:

- ``logs/<name>-loop.log``: a copy of each output line with the UTC time (rotation at 10 MB, 3 old files). The Support Bundle collects each ``*.log`` file of ``logs/``.
- ``run/loop_<name>.json``: the start time, the time and the result of the last cycle, and the interval. The Support Bundle reads these files and calculates their age (``diagnostics/services.json``).

A failure to write a record does not stop the loop.
"""

from __future__ import annotations

import io
import json
import logging
import os
import sys
import tempfile
import time
from logging.handlers import RotatingFileHandler
from pathlib import Path
from typing import Any, TextIO

__all__ = ["LoopRecorder", "loop_state_path", "read_loop_states", "start_loop"]

LOOP_NAMES = ("refresh", "recover", "expand")
_LOG_MAX_BYTES = 10 * 1024 * 1024
_LOG_BACKUPS = 3
_ERROR_MAX_CHARS = 500


def loop_state_path(data_dir: Path | str, name: str) -> Path:
    return Path(data_dir) / "run" / f"loop_{name}.json"


def loop_log_path(data_dir: Path | str, name: str) -> Path:
    return Path(data_dir) / "logs" / f"{name}-loop.log"


class _LineTee(io.TextIOBase):
    """Write to the original stream, and give each complete line to the logger."""

    def __init__(self, stream: TextIO, logger: logging.Logger) -> None:
        self._stream = stream
        self._logger = logger
        self._pending = ""

    def write(self, text: str) -> int:  # type: ignore[override]
        self._stream.write(text)
        self._pending += text
        while "\n" in self._pending:
            line, self._pending = self._pending.split("\n", 1)
            if line.strip():
                try:
                    self._logger.info(line)
                except Exception:  # noqa: BLE001
                    pass
        return len(text)

    def flush(self) -> None:
        self._stream.flush()

    def fileno(self) -> int:
        return self._stream.fileno()

    def isatty(self) -> bool:
        return self._stream.isatty()

    @property
    def encoding(self) -> str:  # type: ignore[override]
        return getattr(self._stream, "encoding", "utf-8")


def _start_log(data_dir: Path | str, name: str) -> None:
    path = loop_log_path(data_dir, name)
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        handler = RotatingFileHandler(str(path), maxBytes=_LOG_MAX_BYTES, backupCount=_LOG_BACKUPS, encoding="utf-8")
    except OSError as exc:
        print(f"[{name}] Cannot write the loop log {path}: {type(exc).__name__}: {exc}", file=sys.stderr, flush=True)
        return
    formatter = logging.Formatter("%(asctime)sZ %(message)s", "%Y-%m-%dT%H:%M:%S")
    formatter.converter = time.gmtime
    handler.setFormatter(formatter)
    logger = logging.getLogger(f"claire.loop.{name}")
    logger.setLevel(logging.INFO)
    logger.propagate = False
    for old in list(logger.handlers):
        logger.removeHandler(old)
        old.close()
    logger.addHandler(handler)
    if not isinstance(sys.stdout, _LineTee):
        sys.stdout = _LineTee(sys.stdout, logger)
    if not isinstance(sys.stderr, _LineTee):
        sys.stderr = _LineTee(sys.stderr, logger)


class LoopRecorder:
    """Write ``run/loop_<name>.json`` after each cycle."""

    def __init__(self, data_dir: Path | str, name: str, *, interval: float) -> None:
        self.path = loop_state_path(data_dir, name)
        self.state: dict[str, Any] = {
            "loop": name,
            "pid": os.getpid(),
            "interval_seconds": float(interval),
            "started_at": time.time(),
            "last_cycle_at": None,
            "last_cycle_ok": None,
            "last_error": None,
            "last_error_at": None,
            "last_summary": {},
            "cycles": 0,
            "failed_cycles": 0,
        }
        self._write()

    def cycle_ok(self, summary: dict[str, Any]) -> None:
        now = time.time()
        self.state.update(last_cycle_at=now, last_cycle_ok=True, last_summary=summary, cycles=self.state["cycles"] + 1)
        self._write()

    def cycle_failed(self, error: str) -> None:
        now = time.time()
        self.state.update(
            last_cycle_at=now,
            last_cycle_ok=False,
            last_error=str(error)[:_ERROR_MAX_CHARS],
            last_error_at=now,
            cycles=self.state["cycles"] + 1,
            failed_cycles=self.state["failed_cycles"] + 1,
        )
        self._write()

    def _write(self) -> None:
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            descriptor, tmp_name = tempfile.mkstemp(prefix=f".{self.path.name}-", dir=self.path.parent)
            try:
                with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
                    json.dump(self.state, stream, ensure_ascii=False)
                os.replace(tmp_name, self.path)
            except BaseException:
                Path(tmp_name).unlink(missing_ok=True)
                raise
        except OSError as exc:
            print(f"[{self.state['loop']}] Cannot write the loop state {self.path}: {type(exc).__name__}: {exc}", file=sys.stderr, flush=True)


def start_loop(data_dir: Path | str, name: str, *, interval: float) -> LoopRecorder:
    """Start the log copy and the state record of one worker loop."""
    _start_log(data_dir, name)
    return LoopRecorder(data_dir, name, interval=interval)


def read_loop_states(data_dir: Path | str, *, now: float) -> dict[str, Any]:
    """The state of each worker loop with its age. A loop without a file is ``absent``: its service did not start with this version, or it does not run."""
    out: dict[str, Any] = {}
    for name in LOOP_NAMES:
        path = loop_state_path(data_dir, name)
        if not path.is_file():
            out[name] = {"state": "absent"}
            continue
        try:
            record = json.loads(path.read_text(encoding="utf-8"))
            if not isinstance(record, dict):
                raise ValueError("not an object")
        except (OSError, ValueError) as exc:
            out[name] = {"state": "unreadable", "error": f"{type(exc).__name__}: {exc}"}
            continue
        last = record.get("last_cycle_at") or record.get("started_at")
        interval = float(record.get("interval_seconds") or 0)
        age = now - float(last) if isinstance(last, (int, float)) else None
        # A loop sleeps max(60, interval) after each cycle. A cycle can take long, thus "overdue" uses two intervals and 10 minutes.
        overdue = age is not None and age > 2 * max(60.0, interval) + 600
        out[name] = {"state": "overdue" if overdue else "recent", "age_seconds": age, **record}
    return out
