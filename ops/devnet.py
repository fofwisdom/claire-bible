"""``./cb-manuscript dev devnet``: the development network of Claire servers (docs/design/CONTRIBUTOR_DEVELOPMENT_ENVIRONMENT_DESIGN.md Sections 6 and 8.4).

The network has a core project (``claire-devnet``: the private certificate authority, the DNS of the names below ``claire.test``, and the TLS gateway) and one project for each server (``claire-devnet-a``, ``claire-devnet-b``: the services of ``docker-compose.yml`` with the overlay ``ops/dev/devnet/compose.server.yml``). The run folder ``.dev/devnet`` has the certificates, the env file and the data folder of each server, and the state file with the admin credentials (mode 0600). No secret is in the repository; each new network has new secrets.

Commands: ``up [--servers a|a,b] [--bot]``, ``check``, ``test [pytest arguments]``, ``info``, ``down``. Only the standard library: the host needs Python 3 and Docker with Compose.
"""

from __future__ import annotations

import argparse
import json
import os
import secrets
import shutil
import subprocess
import sys
from pathlib import Path

SERVERS = ("a", "b")
# The address prefix of the network claire-devnet-edge: the gateway is .2 and the DNS is .3.
EDGE_PREFIX = "172.31.250"
IMAGE_TAG = "devnet"
TOOLBOX_IMAGE = "claire-devnet-toolbox:devnet"
# The port of Claire in the container (CLAIRE_INJECT_PORT). No host port is open: the gateway is the only way in.
API_PORT = "8766"


class DevnetError(RuntimeError):
    pass


def _run(command: list[str], *, env: dict[str, str] | None = None, capture: bool = False, check: bool = True) -> subprocess.CompletedProcess:
    done = subprocess.run(command, env=env, text=True, capture_output=capture)
    if check and done.returncode != 0:
        detail = (done.stderr or done.stdout or "").strip()[-2000:] if capture else ""
        raise DevnetError(f"{' '.join(command[:6])} ... exited with {done.returncode}. {detail}")
    return done


class Devnet:
    def __init__(self, root: Path) -> None:
        self.root = root
        self.ops = root / "ops" / "dev" / "devnet"
        self.run_dir = root / ".dev" / "devnet"
        self.state = self.run_dir / "state.json"
        self.ca = self.run_dir / "tls" / "ca" / "ca.crt"

    # Environment of the compose files.

    def _base_env(self) -> dict[str, str]:
        env = dict(os.environ)
        env.update(CB_DEVNET_RUN_DIR=str(self.run_dir), CB_DEVNET_EDGE_PREFIX=EDGE_PREFIX, CB_DEVNET_IMAGE=f"claire-bible:{IMAGE_TAG}", CB_UID=str(os.getuid()), CB_GID=str(os.getgid()))
        return env

    def _server_env(self, server: str) -> dict[str, str]:
        env = self._base_env()
        folder = self.run_dir / server
        env.update(
            CB_DEVNET_SERVER=server,
            CB_PROJECT_NAME=f"claire-devnet-{server}",
            CB_ENV_FILE=str(self.run_dir / f"{server}.env"),
            CB_DATA_DIR=str(folder / "data"),
            CB_PLUGINS_DIR=str(folder / "plugins"),
            CB_BIN_DIR=str(self.run_dir / "empty-bin"),
            CB_CERTS_DIR=str(self.run_dir / "tls" / "ca"),
            CB_IMAGE_TAG=IMAGE_TAG,
            CLAIRE_ENVIRONMENT="development",
            CB_API_BIND="127.0.0.1",
            CB_API_PORT=API_PORT,
            CB_EDGE_APP_HOST=f"{server}.claire.test",
        )
        return env

    def _core(self) -> list[str]:
        return ["docker", "compose", "-f", str(self.ops / "compose.core.yml")]

    def _server(self, server: str) -> list[str]:
        return ["docker", "compose", "-f", str(self.root / "docker-compose.yml"), "-f", str(self.ops / "compose.server.yml")]

    def _running_servers(self) -> list[str]:
        found = []
        for server in SERVERS:
            done = _run(self._server(server) + ["ps", "-q", "api"], env=self._server_env(server), capture=True, check=False)
            if done.returncode == 0 and done.stdout.strip():
                found.append(server)
        return found

    # Commands.

    def up(self, servers: list[str], *, bot: bool) -> int:
        if shutil.which("docker") is None:
            raise DevnetError("docker is not installed")
        (self.run_dir / "tls").mkdir(parents=True, exist_ok=True)
        (self.run_dir / "empty-bin").mkdir(exist_ok=True)
        os.chmod(self.run_dir, 0o700)
        print("devnet: build the images (tag devnet)")
        _run(self._server(servers[0]) + ["build", "api", "fedify", "edge"], env=self._server_env(servers[0]))
        _run(["docker", "build", "-q", "-t", TOOLBOX_IMAGE, "--build-arg", f"CLAIRE_IMAGE=claire-bible:{IMAGE_TAG}", "-f", str(self.ops / "toolbox.Dockerfile"), str(self.ops)], capture=True)
        print("devnet: start the core project (certificates, DNS, gateway)")
        _run(self._core() + ["up", "-d", "--build", "--wait"], env=self._base_env())
        for server in servers:
            self._up_server(server, bot=bot)
        for server in servers:
            self._bootstrap(server)
        self.info()
        return 0

    def _up_server(self, server: str, *, bot: bool) -> None:
        folder = self.run_dir / server
        for path in (folder / "data" / "run", folder / "data" / "fedify", folder / "plugins"):
            path.mkdir(parents=True, exist_ok=True)
        # A new owner token at each start (Section 6.6).
        values = {
            "CLAIRE_FQDN": f"{server}.claire.test",
            "CLAIRE_PROVIDER": "mock",
            "CLAIRE_ALLOW_PRIVATE_NETWORKS": "1",
            "CLAIRE_TRUSTED_PROXIES": f"{EDGE_PREFIX}.2",
            "CLAIRE_INJECT_TOKEN": secrets.token_urlsafe(32),
        }
        env_file = self.run_dir / f"{server}.env"
        fd = os.open(env_file, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write("".join(f"{key}={value}\n" for key, value in values.items()))
        env = self._server_env(server)
        print(f"devnet: server {server}: migrate")
        _run(self._server(server) + ["run", "--rm", "--no-deps", "api", "claire", "migrate"], env=env, capture=True)
        print(f"devnet: server {server}: start")
        profiles = ["--profile", "bot"] if bot else []
        _run(self._server(server) + profiles + ["up", "-d", "--wait"], env=env)

    def _bootstrap(self, server: str) -> None:
        state = json.loads(self.state.read_text(encoding="utf-8")) if self.state.exists() else {}
        if server in state:
            return
        auth = _run(self._server(server) + ["exec", "-T", "-u", str(os.getuid()), "api", "claire", "auth", "--scope", "owner", "--json"], env=self._server_env(server), capture=True)
        link = json.loads(auth.stdout)["data"]["url"]
        print(f"devnet: server {server}: first setup through the gateway")
        self._toolbox(["python", "/devnet-ops/bootstrap.py", "--server", server, "--link", link, "--state", "/devnet/state.json"], state_writable=True)

    def _toolbox(self, command: list[str], *, state_writable: bool = False, workdir: str | None = None) -> int:
        run = [
            "docker", "run", "--rm", "--network", "claire-devnet-edge", "--dns", f"{EDGE_PREFIX}.3",
            "--memory", "4g", "--memory-swap", "4g",
            "-e", f"CB_UID={os.getuid()}", "-e", f"CB_GID={os.getgid()}", "-e", "CB_DEVNET_TEST=1", "-e", "CB_DEVNET_STATE=/devnet/state.json", "-e", "TMPDIR=/tmp",
            "-v", f"{self.run_dir / 'tls' / 'ca'}:/extra-certs:ro",
            "-v", f"{self.ops}:/devnet-ops:ro",
        ]
        if state_writable:
            run += ["-v", f"{self.run_dir}:/devnet"]
        else:
            run += ["-v", f"{self.state}:/devnet/state.json:ro", "-v", f"{self.root}:/src:ro"]
        if workdir:
            run += ["-w", workdir]
        run += [TOOLBOX_IMAGE] + command
        return subprocess.run(run).returncode

    def check(self) -> int:
        failed = 0
        for server in self._running_servers():
            done = _run(self._server(server) + ["exec", "-T", "-u", str(os.getuid()), "api", "claire", "server", "check", "--federation", "--json"], env=self._server_env(server), capture=True, check=False)
            data = json.loads(done.stdout[done.stdout.index("{"):])["data"]
            print(f"server {server} ({server}.claire.test)")
            for item in data["items"]:
                status = "n/a " if not item["applies"] else ("PASS" if item["passed"] else "FAIL")
                print(f"  {item['id']:<5} {item['level']:<8} {status} {item['code']}")
                if item["applies"] and not item["passed"] and item["level"] == "required":
                    failed += 1
        return 1 if failed else 0

    def test(self, pytest_args: list[str]) -> int:
        if not self.state.exists():
            raise DevnetError("no state file: run ./cb-manuscript dev devnet up first")
        args = pytest_args or ["tests/test_devnet.py"]
        return self._toolbox(["python", "-m", "pytest", "-p", "no:cacheprovider", "-m", "devnet", *args], workdir="/src")

    def info(self) -> int:
        running = self._running_servers()
        print("devnet: development network")
        print(f"  servers running : {', '.join(running) or 'none'}")
        for server in running:
            print(f"  server {server}        : https://{server}.claire.test/ (through the gateway on the network claire-devnet-edge)")
        print(f"  authority       : {self.ca}")
        print(f"  state file      : {self.state} (admin credentials, mode 0600)")
        print("  access from a browser of the host: not implemented (open question DQ2)")
        return 0

    def down(self) -> int:
        for server in SERVERS:
            env = self._server_env(server)
            _run(self._server(server) + ["--profile", "bot", "down", "-v", "--remove-orphans"], env=env, check=False)
        _run(self._core() + ["down", "-v", "--remove-orphans"], env=self._base_env(), check=False)
        if self.run_dir.exists():
            shutil.rmtree(self.run_dir)
        print("devnet: the containers, the networks, the volumes, and .dev/devnet are removed")
        return 0


def main(root: Path, argv: list[str]) -> int:
    parser = argparse.ArgumentParser(prog="cb-manuscript dev devnet", description="The development network of Claire servers.")
    sub = parser.add_subparsers(dest="action", required=True)
    up = sub.add_parser("up", help="Start the network and the servers, and do the first setup of each server")
    up.add_argument("--servers", required=True, help="a or a,b")
    up.add_argument("--bot", action="store_true", help="Start the Telegram bot runner too")
    sub.add_parser("check", help="Readiness report of each server (claire server check --federation)")
    test = sub.add_parser("test", help="Run the tests with the marker devnet in the toolbox")
    test.add_argument("pytest_args", nargs=argparse.REMAINDER)
    sub.add_parser("info", help="Names, the authority file, and the state file")
    sub.add_parser("down", help="Remove the network, the servers, their data, and .dev/devnet")
    args = parser.parse_args(argv)
    devnet = Devnet(root)
    try:
        if args.action == "up":
            servers = [s for s in args.servers.split(",") if s]
            if not servers or any(s not in SERVERS for s in servers) or servers[0] != "a":
                parser.error("--servers must be a or a,b")
            return devnet.up(servers, bot=args.bot)
        if args.action == "check":
            return devnet.check()
        if args.action == "test":
            return devnet.test(args.pytest_args)
        if args.action == "info":
            return devnet.info()
        return devnet.down()
    except DevnetError as exc:
        print(f"devnet: {exc}", file=sys.stderr)
        return 1
