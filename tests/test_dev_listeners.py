"""개발 리스너 계산과 run_api 배선 (설계 §5.1 개발 환경 포트 분리 배선, O16).

모든 테스트는 ``tmp_path``만 쓰고 ``CLAIRE_*``·``CB_*`` 환경변수를 지운 뒤 실행한다(§18.2-6).
"""

from __future__ import annotations

import os
import secrets
import signal
import socket
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path
from types import SimpleNamespace

import pytest

from claire.api.dev_listeners import dev_listeners
from claire.config import Settings


@pytest.fixture(autouse=True)
def _isolate_env(monkeypatch):
    for name in list(os.environ):
        if name.startswith(("CLAIRE_", "CB_")):
            monkeypatch.delenv(name, raising=False)


def _settings(**overrides) -> SimpleNamespace:
    values = dict(
        environment="development",
        inject_host="127.0.0.1",
        inject_port=8766,
        usercontent_fqdn="",
        media_fqdn="",
    )
    values.update(overrides)
    return SimpleNamespace(**values)


def test_app_listener_only_without_role_hosts():
    assert dev_listeners(_settings()) == (("127.0.0.1", 8766),)


def test_development_adds_role_ports_on_inject_host():
    s = _settings(usercontent_fqdn="127.0.0.1:8767", media_fqdn="127.0.0.1:8768")
    assert dev_listeners(s) == (("127.0.0.1", 8766), ("127.0.0.1", 8767), ("127.0.0.1", 8768))


def test_media_only_is_added():
    assert dev_listeners(_settings(media_fqdn="127.0.0.1:8768")) == (
        ("127.0.0.1", 8766),
        ("127.0.0.1", 8768),
    )


def test_lan_ipv4_inject_host():
    s = _settings(inject_host="192.0.2.10", usercontent_fqdn="192.0.2.10:8767")
    assert dev_listeners(s) == (("192.0.2.10", 8766), ("192.0.2.10", 8767))


def test_container_wildcard_host_adds_nothing():
    # 컨테이너는 0.0.0.0에서 듣고 compose가 호스트 포트를 컨테이너 포트로 연결한다.
    s = _settings(inject_host="0.0.0.0", usercontent_fqdn="127.0.0.1:8767", media_fqdn="127.0.0.1:8768")
    assert dev_listeners(s) == (("0.0.0.0", 8766),)


@pytest.mark.parametrize(
    "role_host",
    [
        "192.0.2.10:8767",  # 다른 IPv4
        "localhost:8767",  # 이름(IPv4 아님)
        "127.0.0.1",  # 포트 없음
        "127.0.0.1:",  # 빈 포트
        "127.0.0.1:0",
        "127.0.0.1:65536",
        "127.0.0.1:87a7",
        "127.0.0.1:８７６７",  # 전각 숫자
        " 127.0.0.1:8767",
        "127.0.0.1:8767 ",
        "usercontent.example.com",
    ],
)
def test_role_host_not_matching_inject_host_adds_nothing(role_host):
    assert dev_listeners(_settings(usercontent_fqdn=role_host)) == (("127.0.0.1", 8766),)


def test_inject_host_must_match_literally():
    s = _settings(inject_host="localhost", usercontent_fqdn="127.0.0.1:8767")
    assert dev_listeners(s) == (("localhost", 8766),)


@pytest.mark.parametrize("environment", ["production", "", "Development", " development"])
def test_only_canonical_development_adds_listeners(environment):
    s = _settings(environment=environment, usercontent_fqdn="127.0.0.1:8767", media_fqdn="127.0.0.1:8768")
    assert dev_listeners(s) == (("127.0.0.1", 8766),)


def test_duplicate_ports_collapse():
    s = _settings(usercontent_fqdn="127.0.0.1:8766", media_fqdn="127.0.0.1:8766")
    assert dev_listeners(s) == (("127.0.0.1", 8766),)
    s = _settings(usercontent_fqdn="127.0.0.1:8767", media_fqdn="127.0.0.1:8767")
    assert dev_listeners(s) == (("127.0.0.1", 8766), ("127.0.0.1", 8767))


def test_reads_real_settings_from_env(monkeypatch, tmp_path):
    monkeypatch.setenv("CLAIRE_ENVIRONMENT", "development")
    monkeypatch.setenv("CLAIRE_INJECT_HOST", "127.0.0.1")
    monkeypatch.setenv("CLAIRE_INJECT_PORT", "8766")
    monkeypatch.setenv("CLAIRE_USERCONTENT_FQDN", "127.0.0.1:8767")
    monkeypatch.setenv("CLAIRE_MEDIA_FQDN", "")
    monkeypatch.setenv("CLAIRE_DB_PATH", str(tmp_path / "claire.db"))
    s = Settings(_env_file=None)
    assert dev_listeners(s) == (("127.0.0.1", 8766), ("127.0.0.1", 8767))


# --- run_api 배선: 실제 프로세스로 띄워 두 포트를 확인하고 SIGTERM으로 끝낸다 ---


def _free_ports(n: int) -> list[int]:
    socks = [socket.socket() for _ in range(n)]
    try:
        for s in socks:
            s.bind(("127.0.0.1", 0))
        return [s.getsockname()[1] for s in socks]
    finally:
        for s in socks:
            s.close()


def _status(url: str) -> int:
    try:
        with urllib.request.urlopen(url, timeout=2) as resp:
            return resp.status
    except urllib.error.HTTPError as exc:
        return exc.code


def _wait_until_up(url: str, proc: subprocess.Popen, deadline: float) -> None:
    while time.monotonic() < deadline:
        if proc.poll() is not None:
            raise AssertionError(f"serve-api exited early: {proc.returncode}\n{proc.stdout.read()}")
        try:
            _status(url)
            return
        except OSError:
            time.sleep(0.2)
    raise AssertionError("serve-api did not start in time")


def _port_open(port: int) -> bool:
    with socket.socket() as s:
        s.settimeout(0.5)
        return s.connect_ex(("127.0.0.1", port)) == 0


def test_run_api_serves_role_port_in_same_process(tmp_path):
    app_port, uc_port = _free_ports(2)
    env = {k: v for k, v in os.environ.items() if not k.startswith(("CLAIRE_", "CB_"))}
    env.update(
        # 저장소의 .env를 읽지 않고 상대 경로도 tmp_path에서 풀리게 한다.
        CLAIRE_APP_ROOT=str(tmp_path),
        CLAIRE_ENVIRONMENT="development",
        CLAIRE_PROVIDER="mock",
        CLAIRE_DB_PATH=str(tmp_path / "data" / "claire.db"),
        CB_DATA_DIR=str(tmp_path / "data"),
        CLAIRE_INJECT_HOST="127.0.0.1",
        CLAIRE_INJECT_PORT=str(app_port),
        CLAIRE_FQDN=f"127.0.0.1:{app_port}",
        CLAIRE_USERCONTENT_FQDN=f"127.0.0.1:{uc_port}",
        CLAIRE_INJECT_TOKEN=secrets.token_urlsafe(32),
        PYTHONDONTWRITEBYTECODE="1",
    )
    proc = subprocess.Popen(
        [sys.executable, "-m", "claire.cli", "serve-api"],
        cwd=tmp_path,
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
    )
    try:
        _wait_until_up(f"http://127.0.0.1:{app_port}/health", proc, time.monotonic() + 60)
        assert _status(f"http://127.0.0.1:{app_port}/health") == 200
        assert _status(f"http://127.0.0.1:{uc_port}/health") == 404
        assert _status(f"http://127.0.0.1:{uc_port}/any/path") == 404

        proc.send_signal(signal.SIGTERM)
        out, _ = proc.communicate(timeout=30)
    finally:
        if proc.poll() is None:
            proc.kill()
            proc.communicate()
    # uvicorn은 정상 종료 뒤 받은 신호를 다시 일으킨다(uvicorn.run과 같다).
    assert proc.returncode in (0, -signal.SIGTERM), out
    assert f"listen=127.0.0.1:{app_port}, 127.0.0.1:{uc_port}" in out
    assert "Application shutdown complete" in out
    assert not _port_open(app_port)
    assert not _port_open(uc_port)
