"""Fedify sidecar (unit FP1-5): the Deno tests of ``sidecar/fedify`` and one end-to-end run with a real Claire process.

The tests need the ``deno`` program and its dependency cache. They are skipped when ``deno`` is not installed.
"""

from __future__ import annotations

import json
import os
import secrets
import shutil
import signal
import socket
import subprocess
import sys
import time
from pathlib import Path

import httpx
import pytest

from claire.api.federation_internal import federation_socket_path, service_key_path
from claire.api.federation_routes import read_heartbeat
from claire.store import accounts as accounts_store
from claire.store import db as dbm

SIDECAR_DIR = Path(__file__).resolve().parents[1] / "sidecar" / "fedify"
DENO = shutil.which("deno")
pytestmark = pytest.mark.skipif(DENO is None, reason="deno is not installed")
ALICE_PUBLIC_ID = 116412345678901234


def _clean_env() -> dict[str, str]:
    return {k: v for k, v in os.environ.items() if not k.startswith(("CLAIRE_", "CB_"))}


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return int(s.getsockname()[1])


def test_sidecar_source_layout():
    assert not (SIDECAR_DIR / "spike").exists()
    for name in ("deno.json", "deno.lock", "Dockerfile", "src/main.ts", "src/sidecar.ts", "src/internal_api.ts"):
        assert (SIDECAR_DIR / name).is_file(), name
    # The Fedify minor version is pinned (design Section 13, FS11).
    imports = json.loads((SIDECAR_DIR / "deno.json").read_text())["imports"]
    assert imports["@fedify/fedify"].startswith("jsr:@fedify/fedify@~") and imports["@fedify/sqlite"].startswith("jsr:@fedify/sqlite@~")


def test_sidecar_deno_tests_pass():
    result = subprocess.run([DENO, "task", "test"], cwd=SIDECAR_DIR, env={**_clean_env(), "NO_COLOR": "1"}, capture_output=True, text=True, timeout=300)
    assert result.returncode == 0, result.stdout[-4000:] + result.stderr[-4000:]


def _stop(proc: subprocess.Popen) -> str:
    if proc.poll() is None:
        proc.send_signal(signal.SIGTERM)
        try:
            return proc.communicate(timeout=30)[0]
        except subprocess.TimeoutExpired:
            proc.kill()
    return proc.communicate()[0]


def test_e2e_sidecar_resolves_a_handle_through_a_real_claire(tmp_path):
    claire_port, sidecar_port = _free_port(), _free_port()
    data_dir = tmp_path / "data"
    origin = f"http://127.0.0.1:{claire_port}"
    env = _clean_env()
    env.update(
        CLAIRE_APP_ROOT=str(tmp_path),
        CLAIRE_ENVIRONMENT="development",
        CLAIRE_PROVIDER="mock",
        CLAIRE_DB_PATH=str(data_dir / "claire.db"),
        CB_DATA_DIR=str(data_dir),
        CLAIRE_INJECT_HOST="127.0.0.1",
        CLAIRE_INJECT_PORT=str(claire_port),
        CLAIRE_FQDN=f"127.0.0.1:{claire_port}",
        CLAIRE_INJECT_TOKEN=secrets.token_urlsafe(32),
        PYTHONDONTWRITEBYTECODE="1",
    )
    migrate = subprocess.run([sys.executable, "-m", "claire.cli", "migrate"], cwd=tmp_path, env=env, capture_output=True, text=True)
    assert migrate.returncode == 0, migrate.stdout + migrate.stderr

    def seed(*, mode: str, federation: str) -> None:
        conn = dbm.connect_existing(data_dir / "claire.db")
        try:
            store = accounts_store.AccountStore(conn)
            store.set_setting("mode", mode, updated_by=1)
            store.set_federation_state(federation, updated_by=1)
        finally:
            conn.close()

    conn = dbm.connect_existing(data_dir / "claire.db")
    try:
        store = accounts_store.AccountStore(conn)
        store.complete_setup(1, username="admin_kim", display_name="Admin Kim")
        # The admin is a federation actor in network mode. Single mode must hide the admin USERNAME again (S9).
        store.update_profile_public(1, 1)
        admin_public_id = store.get(1).public_id
        conn.execute(
            "INSERT INTO accounts (public_id, username, role, status, display_name, profile_public, created_at, activated_at, updated_at) VALUES (?, 'alice', 'member', 'active', 'Alice Kim', 1, 0, 1800000100.5, 0)",
            (ALICE_PUBLIC_ID,),
        )
        conn.execute(
            "INSERT INTO accounts (public_id, username, role, status, display_name, profile_public, created_at, activated_at, updated_at) VALUES (216412345678901234, 'private_bob', 'member', 'active', 'Bob', 0, 0, 0, 0)"
        )
        conn.commit()
    finally:
        conn.close()
    seed(mode="network", federation="on")

    sock_path = federation_socket_path(data_dir)
    sidecar_db = data_dir / "fedify" / "fedify.db"
    sidecar_db.parent.mkdir(parents=True)
    claire = subprocess.Popen([sys.executable, "-m", "claire.cli", "serve-api"], cwd=tmp_path, env=env, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
    sidecar = None
    sidecar_out = ""
    try:
        deadline = time.monotonic() + 60
        while not sock_path.exists():
            assert claire.poll() is None, claire.stdout.read()
            assert time.monotonic() < deadline, "serve-api did not start in time"
            time.sleep(0.2)

        sidecar_env = {
            **_clean_env(),
            "NO_COLOR": "1",
            "CLAIRE_FEDIFY_SOCKET": str(sock_path),
            "CLAIRE_FEDIFY_SERVICE_KEY_FILE": str(service_key_path(data_dir)),
            "CLAIRE_FEDIFY_DB": str(sidecar_db),
            "CLAIRE_FEDIFY_HOST": "127.0.0.1",
            "CLAIRE_FEDIFY_PORT": str(sidecar_port),
        }
        command = [
            DENO, "run", "--frozen", "--cached-only",
            f"--allow-net=127.0.0.1,claire.internal:80,unix:{sock_path}",
            f"--allow-read={data_dir},{SIDECAR_DIR}",
            f"--allow-write={sidecar_db.parent},{sock_path}",
            "--allow-env=CLAIRE_FEDIFY_SOCKET,CLAIRE_FEDIFY_SERVICE_KEY_FILE,CLAIRE_FEDIFY_DB,CLAIRE_FEDIFY_HOST,CLAIRE_FEDIFY_PORT",
            "src/main.ts",
        ]  # fmt: skip
        sidecar = subprocess.Popen(command, cwd=SIDECAR_DIR, env=sidecar_env, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
        base = f"http://127.0.0.1:{sidecar_port}"
        finger_url = f"{base}/.well-known/webfinger"
        deadline = time.monotonic() + 120
        while True:
            assert sidecar.poll() is None, sidecar.stdout.read()
            try:
                httpx.get(f"{base}/", timeout=2)
                break
            except httpx.HTTPError:
                assert time.monotonic() < deadline, "the sidecar did not start in time"
                time.sleep(0.2)

        # A handle resolves to an actor document with the canonical origin of Claire, not the address of the sidecar.
        actor_url = f"{origin}/ap/actors/{ALICE_PUBLIC_ID}"
        finger = httpx.get(finger_url, params={"resource": f"acct:alice@127.0.0.1:{claire_port}"}, headers={"Accept": "application/jrd+json"}, timeout=10)
        assert finger.status_code == 200, finger.text
        links = {link["rel"]: link["href"] for link in finger.json()["links"] if "href" in link}
        assert links["self"] == actor_url
        assert links["http://webfinger.net/rel/profile-page"] == f"{origin}/@alice/"

        actor = httpx.get(f"{base}/ap/actors/{ALICE_PUBLIC_ID}", headers={"Accept": "application/activity+json"}, timeout=10)
        assert actor.status_code == 200, actor.text
        doc = actor.json()
        assert (doc["id"], doc["type"], doc["preferredUsername"], doc["name"]) == (actor_url, "Person", "alice", "Alice Kim")
        assert doc["inbox"] == f"{actor_url}/inbox"
        assert doc["publicKey"]["publicKeyPem"].startswith("-----BEGIN PUBLIC KEY-----")
        assert str(sidecar_port) not in actor.text + finger.text

        # The key of the actor document is the key that Claire keeps.
        import sqlite3

        auth = sqlite3.connect(data_dir / "auth" / "auth.db")
        try:
            owners = [tuple(r) for r in auth.execute("SELECT account_id, alg FROM actor_keys ORDER BY account_id, alg")]
        finally:
            auth.close()
        assert (2, "ed25519") in owners and (2, "rsa2048") in owners

        instance = httpx.get(f"{base}/ap/actors/instance", headers={"Accept": "application/activity+json"}, timeout=10)
        assert instance.status_code == 200 and instance.json()["type"] == "Application"
        nodeinfo = httpx.get(f"{base}/nodeinfo/2.1", timeout=10).json()
        assert nodeinfo["software"]["name"] == "claire-bible" and nodeinfo["usage"]["users"]["total"] == 2

        admin_finger = httpx.get(finger_url, params={"resource": f"acct:admin_kim@127.0.0.1:{claire_port}"}, timeout=10)
        assert admin_finger.status_code == 200 and "admin_kim" in admin_finger.text

        # An account that is not an actor, and a path of Claire.
        assert httpx.get(finger_url, params={"resource": f"acct:private_bob@127.0.0.1:{claire_port}"}, timeout=10).status_code == 404
        assert httpx.get(f"{base}/api/me", timeout=10).status_code == 404

        # The heartbeat got to Claire through the signed socket.
        deadline = time.monotonic() + 30
        while (record := read_heartbeat(data_dir)) is None:
            assert time.monotonic() < deadline, "no heartbeat"
            time.sleep(0.2)
        assert record["contract_version"] == "1.0" and record["queue_depth"] == 0
        # The sidecar database is a secret store (design Section 7 item 5).
        assert (sidecar_db.stat().st_mode & 0o777) == 0o600

        # Single mode shows no USERNAME (S9): after the config cache time, each path is 404.
        seed(mode="single", federation="off")
        deadline = time.monotonic() + 30
        # NodeInfo depends on the cached config only. An actor path can be 404 earlier, because Claire refuses the actor at once.
        while httpx.get(f"{base}/nodeinfo/2.1", timeout=10).status_code != 404:
            assert time.monotonic() < deadline, "the sidecar did not stop the actor"
            time.sleep(0.5)
        gone = httpx.get(finger_url, params={"resource": f"acct:alice@127.0.0.1:{claire_port}"}, timeout=10)
        assert gone.status_code == 404 and "alice" not in gone.text
        # S9: the admin USERNAME is not shown in single mode, on any federation path of the sidecar.
        single_answers = [
            httpx.get(finger_url, params={"resource": f"acct:admin_kim@127.0.0.1:{claire_port}"}, timeout=10),
            httpx.get(finger_url, params={"resource": f"{origin}/ap/actors/{admin_public_id}"}, timeout=10),
            httpx.get(f"{base}/ap/actors/{admin_public_id}", headers={"Accept": "application/activity+json"}, timeout=10),
            httpx.get(f"{base}/ap/actors/instance", headers={"Accept": "application/activity+json"}, timeout=10),
            httpx.get(f"{base}/.well-known/nodeinfo", timeout=10),
            httpx.get(f"{base}/nodeinfo/2.1", timeout=10),
        ]
        for answer in single_answers:
            assert answer.status_code == 404, answer.url
            assert "admin_kim" not in answer.text + repr(list(answer.headers.items())), answer.url
    except BaseException:
        if sidecar is not None:
            print("sidecar output:", _stop(sidecar)[-3000:])
        print("claire output:", _stop(claire)[-3000:])
        raise
    finally:
        if sidecar is not None:
            sidecar_out = _stop(sidecar)
        claire_out = _stop(claire)
    # No private key field is in the sidecar log.
    assert '"d"' not in sidecar_out and "private_jwk" not in sidecar_out, sidecar_out[-2000:]
    assert claire.returncode in (0, -signal.SIGTERM), claire_out[-2000:]


def test_e2e_self_check_passes_while_federation_is_off(tmp_path):
    """The admin can set the switch to ``on`` only after GF6 passes. Thus the sidecar self-check must pass while federation is off.

    The public origin of this instance is the address of the sidecar. This stands for a reverse proxy that sends the federation paths to the sidecar.
    """
    claire_port, sidecar_port = _free_port(), _free_port()
    data_dir = tmp_path / "data"
    env = _clean_env()
    env.update(
        CLAIRE_APP_ROOT=str(tmp_path),
        CLAIRE_ENVIRONMENT="development",
        CLAIRE_PROVIDER="mock",
        CLAIRE_DB_PATH=str(data_dir / "claire.db"),
        CB_DATA_DIR=str(data_dir),
        CLAIRE_INJECT_HOST="127.0.0.1",
        CLAIRE_INJECT_PORT=str(claire_port),
        CLAIRE_FQDN=f"127.0.0.1:{sidecar_port}",
        CLAIRE_INJECT_TOKEN=secrets.token_urlsafe(32),
        CLAIRE_SECRET_KEY="55" * 32,
        PYTHONDONTWRITEBYTECODE="1",
    )
    migrate = subprocess.run([sys.executable, "-m", "claire.cli", "migrate"], cwd=tmp_path, env=env, capture_output=True, text=True)
    assert migrate.returncode == 0, migrate.stdout + migrate.stderr
    conn = dbm.connect_existing(data_dir / "claire.db")
    try:
        store = accounts_store.AccountStore(conn)
        store.complete_setup(1, username="admin_kim", display_name="Admin Kim")
        store.set_setting("mode", "network", updated_by=1)
        assert store.federation_state() == "off"
    finally:
        conn.close()

    sock_path = federation_socket_path(data_dir)
    sidecar_db = data_dir / "fedify" / "fedify.db"
    sidecar_db.parent.mkdir(parents=True)
    claire = subprocess.Popen([sys.executable, "-m", "claire.cli", "serve-api"], cwd=tmp_path, env=env, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
    sidecar = None
    try:
        deadline = time.monotonic() + 60
        while not sock_path.exists():
            assert claire.poll() is None, claire.stdout.read()
            assert time.monotonic() < deadline, "serve-api did not start in time"
            time.sleep(0.2)
        sidecar_env = {
            **_clean_env(),
            "NO_COLOR": "1",
            "CLAIRE_FEDIFY_SOCKET": str(sock_path),
            "CLAIRE_FEDIFY_SERVICE_KEY_FILE": str(service_key_path(data_dir)),
            "CLAIRE_FEDIFY_DB": str(sidecar_db),
            "CLAIRE_FEDIFY_HOST": "127.0.0.1",
            "CLAIRE_FEDIFY_PORT": str(sidecar_port),
        }
        command = [
            DENO, "run", "--frozen", "--cached-only",
            f"--allow-net=127.0.0.1,claire.internal:80,unix:{sock_path}",
            f"--allow-read={data_dir},{SIDECAR_DIR}",
            f"--allow-write={sidecar_db.parent},{sock_path}",
            "--allow-env=CLAIRE_FEDIFY_SOCKET,CLAIRE_FEDIFY_SERVICE_KEY_FILE,CLAIRE_FEDIFY_DB,CLAIRE_FEDIFY_HOST,CLAIRE_FEDIFY_PORT",
            "src/main.ts",
        ]  # fmt: skip
        sidecar = subprocess.Popen(command, cwd=SIDECAR_DIR, env=sidecar_env, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)

        deadline = time.monotonic() + 120
        while (record := read_heartbeat(data_dir)) is None:
            assert sidecar.poll() is None, sidecar.stdout.read()
            assert time.monotonic() < deadline, "no heartbeat"
            time.sleep(0.2)
        assert record["self_check"]["ok"] is True, record

        # Federation is off: the sidecar still shows no federation data.
        finger = httpx.get(f"http://127.0.0.1:{sidecar_port}/.well-known/webfinger", params={"resource": f"acct:claire@127.0.0.1:{sidecar_port}"}, timeout=10)
        assert finger.status_code == 404 and finger.text == "Not Found"

        # The readiness checks that depend on the sidecar pass. GF7 fails here only because this test has no reverse proxy that sends the other paths to Claire.
        check = subprocess.run([sys.executable, "-m", "claire.cli", "instance", "check", "--federation", "--json"], cwd=tmp_path, env=env, capture_output=True, text=True, timeout=60)
        report = json.loads(check.stdout)
        items = {item["id"]: item["passed"] for item in report["data"]["items"]}
        assert items == {"GF1": True, "GF2": True, "GF3": True, "GF4": True, "GF5": True, "GF6": True, "GF7": False}, check.stdout
    finally:
        if sidecar is not None:
            _stop(sidecar)
        _stop(claire)
