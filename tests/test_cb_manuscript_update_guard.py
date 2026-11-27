"""Tests for cb-manuscript update pre-restart active-ingest guard."""

from __future__ import annotations

import json
import subprocess
import sys
import time
from pathlib import Path
from unittest.mock import patch

import pytest

from ops import cb_manuscript as cb


@pytest.fixture(autouse=True)
def _clear_environment_selector(monkeypatch):
    monkeypatch.delenv("CLAIRE_ENVIRONMENT", raising=False)
    monkeypatch.delenv("CB_DATA_DIR", raising=False)
    monkeypatch.delenv("CB_PLUGINS_DIR", raising=False)


def _write_layout(root: Path, *, dev: bool = False) -> None:
    owner_token = "owner-" + ("x" * 32)
    (root / ".env.example").write_text(
        "\n".join(
            (
                "CLAIRE_ENVIRONMENT=production",
                "CB_PROJECT_NAME=claire-bible",
                "CB_WAIT_TIMEOUT=45",
                "CB_API_BIND=127.0.0.1",
                "CB_API_PORT=8765",
                "CLAIRE_FQDN=claire.example.com",
                "CLAIRE_CORS_ALLOWED_ORIGINS=",
                "CLAIRE_READONLY_TOKEN=",
                f"CLAIRE_INJECT_TOKEN={owner_token}",
                "GEMINI_API_KEY=",
                "TELEGRAM_BOT_TOKEN=",
                "",
            )
        ),
        encoding="utf-8",
    )
    (root / ".env").write_text((root / ".env.example").read_text(encoding="utf-8"), encoding="utf-8")
    (root / "docker-compose.yml").write_text("services: {}\n", encoding="utf-8")
    if dev:
        (root / ".env.dev.example").write_text(
            "\n".join(
                (
                    "CLAIRE_ENVIRONMENT=development",
                    "CB_PROJECT_NAME=claire-bible-dev",
                    "CB_WAIT_TIMEOUT=15",
                    "CB_API_BIND=127.0.0.1",
                    "CB_API_PORT=8766",
                    "CLAIRE_FQDN=127.0.0.1:8766",
                    "CLAIRE_CORS_ALLOWED_ORIGINS=",
                    "CLAIRE_READONLY_TOKEN=",
                    f"CLAIRE_INJECT_TOKEN={owner_token}",
                    "TELEGRAM_BOT_TOKEN=",
                    "",
                )
            ),
            encoding="utf-8",
        )
        (root / ".env.dev").write_text((root / ".env.dev.example").read_text(encoding="utf-8"), encoding="utf-8")
        (root / "docker-compose.dev.yml").write_text("services: {}\n", encoding="utf-8")


def _completed(argv: list[str] | tuple[str, ...], returncode: int = 0, *, stdout: str = "", stderr: str = "") -> subprocess.CompletedProcess[str]:
    return subprocess.CompletedProcess(argv, returncode, stdout, stderr)


def _fake_update_subprocess(argv, **_kwargs):
    if argv[:2] == ["docker", "compose"] and argv[-2:] == ["ps", "-q"]:
        return _completed(argv, stdout="running-bot-id\n")
    if argv[:3] == ["docker", "ps", "-q"]:
        return _completed(argv, stdout="")
    return _completed(argv)


def _commands(mock_run) -> list[list[str]]:
    return [call.args[0] for call in mock_run.call_args_list]


def _write_json(path: Path, data: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


def _active_job_payload(*, job_id: str | None = "j_11223344", active: object = True, title: str = "NAVER D2 Keynote", payload: str = "https://tv.naver.com/v/12345", stage: str = "extract", msg: str = "STT chunk 2/7 transcribing", started_at: float | None = None, updated_at: float | None = None) -> dict[str, object]:
    now = time.time()
    entry: dict[str, object] = {
        "active": active,
        "kind": "ingest",
        "payload": payload,
        "title": title,
        "stage": stage,
        "msg": msg,
        "started_at": now - 300 if started_at is None else started_at,
        "updated_at": now - 10 if updated_at is None else updated_at,
    }
    if job_id is not None:
        entry["job_id"] = job_id
    return entry


def test_list_active_ingests_reads_active_jobs_and_legacy_file_with_dedup(tmp_path):
    now = 1_700_000_000.0
    data_dir = tmp_path / "data"
    jobs_dir = data_dir / "active_jobs"
    jobs_dir.mkdir(parents=True)

    # Active job present in both active_jobs and active_ingest.json (must deduplicate by job_id, keeping newer updated_at)
    _write_json(jobs_dir / "j_dup.json", _active_job_payload(job_id="j_dup", title="Video A", stage="extract", msg="older", started_at=now - 500, updated_at=now - 50))
    _write_json(data_dir / "active_ingest.json", _active_job_payload(job_id="j_dup", title="Video A", stage="resolve", msg="newer", started_at=now - 500, updated_at=now - 5))

    # Second distinct active job
    _write_json(jobs_dir / "j_second.json", _active_job_payload(job_id="j_second", title="PDF B", stage="fetch", msg="downloading", started_at=now - 120, updated_at=now - 10))

    # Ignored entries: stale (>1800s), active=False, active="true", malformed JSON, non-dict JSON
    _write_json(jobs_dir / "j_stale.json", _active_job_payload(job_id="j_stale", title="Stale", started_at=now - 3000, updated_at=now - 1801))
    _write_json(jobs_dir / "j_done.json", _active_job_payload(job_id="j_done", active=False, title="Done", started_at=now - 200, updated_at=now - 1))
    _write_json(jobs_dir / "j_str_active.json", _active_job_payload(job_id="j_str_active", active="true", title="BadActive", started_at=now - 50, updated_at=now - 5))
    (jobs_dir / "j_broken.json").write_text("{not-valid-json", encoding="utf-8")
    (jobs_dir / "j_list.json").write_text("[1, 2, 3]", encoding="utf-8")

    found = cb.list_active_ingests(data_dir, now=now)
    assert [job["job_id"] for job in found] == ["j_dup", "j_second"]
    assert found[0]["stage"] == "resolve"
    assert found[0]["msg"] == "newer"


def test_list_active_ingests_handles_legacy_single_file_and_started_at_fallback(tmp_path):
    now = 1_700_000_000.0
    data_dir = tmp_path / "data"

    # Legacy active_ingest.json without job_id and without updated_at (falls back to started_at)
    entry = _active_job_payload(job_id=None, title="Legacy PDF", started_at=now - 1800, updated_at=0)
    _write_json(data_dir / "active_ingest.json", entry)

    found = cb.list_active_ingests(data_dir, now=now)
    assert len(found) == 1
    assert found[0]["title"] == "Legacy PDF"

    # Stale by 1 second past 1800s
    assert cb.list_active_ingests(data_dir, now=now + 1) == []


def test_list_active_ingests_ignores_stale_active_ingest_copy_when_active_job_finished(tmp_path):
    now = 1_700_000_000.0
    data_dir = tmp_path / "data"
    jobs_dir = data_dir / "active_jobs"

    _write_json(jobs_dir / "j_race.json", _active_job_payload(job_id="j_race", active=False, started_at=now - 400, updated_at=now - 1))
    _write_json(data_dir / "active_ingest.json", _active_job_payload(job_id="j_race", active=True, started_at=now - 400, updated_at=now - 5))

    assert cb.list_active_ingests(data_dir, now=now) == []


def test_update_noninteractive_aborts_safely_before_stop_when_active_ingest_exists(tmp_path, capsys):
    _write_layout(tmp_path)
    _write_json(tmp_path / "data" / "active_jobs" / "j_stt.json", _active_job_payload(job_id="j_stt", title="NAVER D2 Video", payload="https://tv.naver.com/v/999", stage="extract", msg="STT chunk 3/7"))

    with patch.object(cb, "_is_interactive_stdin", return_value=False), patch.object(cb.subprocess, "run", side_effect=_fake_update_subprocess) as run:
        rc = cb.main(["update", "--no-fetch"], root=tmp_path)

    assert rc == 2
    out, err = capsys.readouterr()
    assert "NAVER D2 Video" in out
    assert "stage=extract" in out
    assert "STT chunk 3/7" in out
    assert "elapsed=" in out
    assert "--wait-ingest" in err
    assert "--force-restart" in err

    commands = _commands(run)
    assert any(cmd[-1:] == ["build"] for cmd in commands)
    assert not any(cmd[-1:] == ["stop"] for cmd in commands)
    assert not any(cmd[-2:] == ["claire", "migrate"] for cmd in commands)
    assert not any("up" in cmd for cmd in commands)


def test_update_yes_without_policy_flag_still_aborts_safely_on_active_ingest(tmp_path, capsys):
    _write_layout(tmp_path)
    _write_json(tmp_path / "data" / "active_jobs" / "j_stt.json", _active_job_payload(job_id="j_stt", title="Long PDF"))

    with patch.object(cb, "_is_interactive_stdin", return_value=True), patch.object(cb.subprocess, "run", side_effect=_fake_update_subprocess) as run:
        rc = cb.main(["update", "--no-fetch", "--yes"], root=tmp_path)

    assert rc == 2
    err = capsys.readouterr().err
    assert "--wait-ingest" in err
    assert "--force-restart" in err
    commands = _commands(run)
    assert not any(cmd[-1:] == ["stop"] for cmd in commands)


def test_update_force_restart_proceeds_despite_active_ingest(tmp_path, capsys):
    _write_layout(tmp_path)
    _write_json(tmp_path / "data" / "active_jobs" / "j_stt.json", _active_job_payload(job_id="j_stt", title="NAVER D2 Video"))

    with patch.object(cb, "_is_interactive_stdin", return_value=False), patch.object(cb.subprocess, "run", side_effect=_fake_update_subprocess) as run:
        rc = cb.main(["update", "--no-fetch", "--force-restart"], root=tmp_path)

    assert rc == 0
    out = capsys.readouterr().out
    assert "NAVER D2 Video" in out
    assert "--force-restart" in out
    commands = _commands(run)
    build_idx = next(i for i, cmd in enumerate(commands) if cmd[-1:] == ["build"])
    stop_idx = next(i for i, cmd in enumerate(commands) if cmd[-1:] == ["stop"])
    up_idx = next(i for i, cmd in enumerate(commands) if cmd[:2] == ["docker", "compose"] and "up" in cmd[-7:])
    assert build_idx < stop_idx < up_idx


def test_update_wait_ingest_polls_until_active_file_clears_then_proceeds(tmp_path, capsys):
    _write_layout(tmp_path)
    job_file = tmp_path / "data" / "active_jobs" / "j_stt.json"
    _write_json(job_file, _active_job_payload(job_id="j_stt", title="NAVER D2 Video", stage="extract", msg="STT chunk 1/2"))

    clock = {"mono": 100.0}
    polls = {"count": 0}

    def fake_monotonic() -> float:
        return clock["mono"]

    def fake_sleep(seconds: float) -> None:
        clock["mono"] += seconds
        polls["count"] += 1
        if polls["count"] == 1:
            _write_json(job_file, _active_job_payload(job_id="j_stt", title="NAVER D2 Video", stage="detail", msg="Generating detail"))
        elif polls["count"] >= 2:
            _write_json(job_file, _active_job_payload(job_id="j_stt", active=False, title="NAVER D2 Video", stage="done", msg="Completed"))

    with (
        patch.object(cb, "DEFAULT_WAIT_INGEST_POLL_INTERVAL", 2.0),
        patch.object(cb.time, "sleep", side_effect=fake_sleep),
        patch.object(cb.time, "monotonic", side_effect=fake_monotonic),
        patch.object(cb.subprocess, "run", side_effect=_fake_update_subprocess) as run,
    ):
        rc = cb.main(["update", "--no-fetch", "--wait-ingest", "--wait-ingest-timeout", "30"], root=tmp_path)

    assert rc == 0
    assert polls["count"] == 2
    out = capsys.readouterr().out
    assert "Waiting for active ingest(s) to finish" in out
    assert "stage=detail" in out
    assert "All active ingests finished" in out
    commands = _commands(run)
    assert any(cmd[-1:] == ["stop"] for cmd in commands)
    assert any(cmd[:2] == ["docker", "compose"] and "up" in cmd[-7:] for cmd in commands)


def test_update_wait_ingest_timeout_aborts_cleanly_without_stopping_services(tmp_path, capsys):
    _write_layout(tmp_path)
    _write_json(tmp_path / "data" / "active_jobs" / "j_stt.json", _active_job_payload(job_id="j_stt", title="Never Ending STT"))

    clock = {"mono": 100.0}

    def fake_monotonic() -> float:
        return clock["mono"]

    def fake_sleep(seconds: float) -> None:
        clock["mono"] += seconds

    with (
        patch.object(cb, "DEFAULT_WAIT_INGEST_POLL_INTERVAL", 5.0),
        patch.object(cb.time, "sleep", side_effect=fake_sleep),
        patch.object(cb.time, "monotonic", side_effect=fake_monotonic),
        patch.object(cb.subprocess, "run", side_effect=_fake_update_subprocess) as run,
    ):
        rc = cb.main(["update", "--no-fetch", "--wait-ingest", "--wait-ingest-timeout", "10"], root=tmp_path)

    assert rc == 2
    err = capsys.readouterr().err
    assert "Timed out after 10s" in err
    assert "--force-restart" in err
    commands = _commands(run)
    assert any(cmd[-1:] == ["build"] for cmd in commands)
    assert not any(cmd[-1:] == ["stop"] for cmd in commands)


@pytest.mark.parametrize("answer, expected_rc, expect_stop", [("", 2, False), ("a", 2, False), ("f", 0, True)])
def test_update_interactive_tty_prompt_abort_and_force(tmp_path, answer, expected_rc, expect_stop):
    _write_layout(tmp_path)
    _write_json(tmp_path / "data" / "active_jobs" / "j_stt.json", _active_job_payload(job_id="j_stt", title="Interactive Job"))

    with (
        patch.object(cb, "_is_interactive_stdin", return_value=True),
        patch("builtins.input", return_value=answer),
        patch.object(cb.subprocess, "run", side_effect=_fake_update_subprocess) as run,
    ):
        rc = cb.main(["update", "--no-fetch"], root=tmp_path)

    assert rc == expected_rc
    commands = _commands(run)
    assert any(cmd[-1:] == ["stop"] for cmd in commands) is expect_stop


def test_update_interactive_tty_prompt_wait_clears_and_proceeds(tmp_path):
    _write_layout(tmp_path)
    job_file = tmp_path / "data" / "active_jobs" / "j_stt.json"
    _write_json(job_file, _active_job_payload(job_id="j_stt", title="Interactive Wait Job"))

    clock = {"mono": 50.0}

    def fake_sleep(seconds: float) -> None:
        clock["mono"] += seconds
        job_file.unlink()

    with (
        patch.object(cb, "_is_interactive_stdin", return_value=True),
        patch("builtins.input", side_effect=["invalid", "w"]),
        patch.object(cb.time, "sleep", side_effect=fake_sleep),
        patch.object(cb.time, "monotonic", side_effect=lambda: clock["mono"]),
        patch.object(cb.subprocess, "run", side_effect=_fake_update_subprocess) as run,
    ):
        rc = cb.main(["update", "--no-fetch"], root=tmp_path)

    assert rc == 0
    assert any(cmd[-1:] == ["stop"] for cmd in _commands(run))


def test_update_rejects_mutually_exclusive_flags_and_nonpositive_timeout(tmp_path):
    _write_layout(tmp_path)

    with patch.object(cb.subprocess, "run") as run:
        assert cb.main(["update", "--no-fetch", "--wait-ingest", "--force-restart"], root=tmp_path) == 2
        assert cb.main(["update", "--no-fetch", "--wait-ingest", "--wait-ingest-timeout", "0"], root=tmp_path) == 2

    run.assert_not_called()


def test_update_reexec_preserves_ingest_guard_flags(tmp_path, monkeypatch):
    _write_layout(tmp_path)
    monkeypatch.delenv("CLAIRE_BUILD_COMMIT", raising=False)
    pulled = []

    def fake(argv, **_kwargs):
        if argv[:2] == ["git", "status"]:
            return _completed(argv, stdout="")
        if argv[:3] == ["git", "rev-parse", "--abbrev-ref"]:
            return _completed(argv, stdout="origin/main\n")
        if argv == ["git", "pull", "--ff-only"]:
            pulled.append(True)
        if argv == ["git", "rev-parse", "HEAD"]:
            return _completed(argv, stdout=("b" * 40 if pulled else "a" * 40) + "\n")
        return _completed(argv)

    class Reexec(Exception):
        pass

    calls = []

    def fake_execve(path, argv, env):
        calls.append((path, argv, env))
        raise Reexec

    monkeypatch.setattr(cb.os, "execve", fake_execve)
    with patch.object(cb.subprocess, "run", side_effect=fake):
        with pytest.raises(Reexec):
            cb.main(["update", "--wait-ingest", "--wait-ingest-timeout", "120", "--yes"], root=tmp_path)

    (path, argv, _env), = calls
    assert path == sys.executable
    assert argv == [sys.executable, str(Path(cb.__file__).resolve()), "update", "--no-fetch", "--wait-ingest", "--wait-ingest-timeout", "120", "--yes"]


def test_update_guard_checks_dev_and_custom_cb_data_dir(tmp_path, monkeypatch, capsys):
    _write_layout(tmp_path, dev=True)
    _write_json(tmp_path / ".dev" / "data" / "active_jobs" / "j_dev.json", _active_job_payload(job_id="j_dev", title="", payload="https://example.com/report.pdf", stage="extract", msg="parsing PDF"))

    with patch.object(cb, "_is_interactive_stdin", return_value=False), patch.object(cb.subprocess, "run", side_effect=_fake_update_subprocess) as run:
        assert cb.main(["dev", "update", "--no-fetch"], root=tmp_path) == 2

    out = capsys.readouterr().out
    assert "https://example.com/report.pdf" in out
    assert not any(cmd[-1:] == ["stop"] for cmd in _commands(run))

    custom_data = tmp_path / "custom-data"
    _write_json(custom_data / "active_ingest.json", _active_job_payload(job_id="j_custom", title="Custom Dir Ingest"))
    monkeypatch.setenv("CB_DATA_DIR", str(custom_data))

    with patch.object(cb, "_is_interactive_stdin", return_value=False), patch.object(cb.subprocess, "run", side_effect=_fake_update_subprocess) as run:
        assert cb.main(["update", "--no-fetch"], root=tmp_path) == 2

    assert "Custom Dir Ingest" in capsys.readouterr().out
    assert not any(cmd[-1:] == ["stop"] for cmd in _commands(run))


def test_cli_e2e_update_guard_via_system_python3_with_fake_docker(tmp_path):
    """CLI E2E test invoking ops/cb_manuscript.py directly with host system python3 (stdlib only)."""
    _write_layout(tmp_path)
    _write_json(tmp_path / "data" / "active_jobs" / "j_e2e.json", _active_job_payload(job_id="j_e2e", title="E2E Video STT", stage="extract", msg="STT chunk 4/8"))

    # Create a fake docker executable that logs invocations and succeeds.
    fake_bin = tmp_path / "fake_bin"
    fake_bin.mkdir()
    docker_log = tmp_path / "docker.log"
    docker_script = fake_bin / "docker"
    docker_script.write_text(
        f"#!/usr/bin/env bash\nprintf '%s\\n' \"$*\" >> {json.dumps(str(docker_log))}\nexit 0\n",
        encoding="utf-8",
    )
    docker_script.chmod(0o755)

    # Copy ops/cb_manuscript.py into tmp_path so Path(__file__).resolve().parents[1] points at tmp_path.
    ops_dir = tmp_path / "ops"
    ops_dir.mkdir()
    script_copy = ops_dir / "cb_manuscript.py"
    script_copy.write_text(Path(cb.__file__).read_text(encoding="utf-8"), encoding="utf-8")

    env = {"PATH": f"{fake_bin}:/usr/bin:/bin", "HOME": str(tmp_path)}
    proc = subprocess.run(
        ["/usr/bin/python3", str(script_copy), "update", "--no-fetch"],
        cwd=str(tmp_path),
        env=env,
        stdin=subprocess.DEVNULL,
        capture_output=True,
        text=True,
        check=False,
    )
    assert proc.returncode == 2
    assert "E2E Video STT" in proc.stdout
    assert "stage=extract" in proc.stdout
    assert "--wait-ingest" in proc.stderr
    assert "--force-restart" in proc.stderr
    logged = docker_log.read_text(encoding="utf-8").splitlines()
    assert any(line.endswith(" build") for line in logged)
    assert not any(line.endswith(" stop") for line in logged)
    assert not any(" up " in line for line in logged)

    # Now re-run with --force-restart and verify it completes end-to-end.
    docker_log.write_text("", encoding="utf-8")
    proc_force = subprocess.run(
        ["/usr/bin/python3", str(script_copy), "update", "--no-fetch", "--force-restart"],
        cwd=str(tmp_path),
        env=env,
        stdin=subprocess.DEVNULL,
        capture_output=True,
        text=True,
        check=False,
    )
    assert proc_force.returncode == 0, proc_force.stderr
    logged_force = docker_log.read_text(encoding="utf-8").splitlines()
    assert any(line.endswith(" build") for line in logged_force)
    assert any(" up " in line for line in logged_force)
