"""Development test only: DrFed 0.1.0 in Docker behind a TLS gateway, as a model of a remote ActivityPub server.

DrFed (https://www.npmjs.com/package/@drfed/drfed) is a test server for ActivityPub implementations: it makes test servers and actors, makes objects, and records each delivery that arrives at an inbox (the activity type, the result of the signature check, and the JSON-LD body). The module writes a private certificate authority and a wildcard certificate to a run directory, starts ``tests/drfed/docker-compose.yml``, signs in with the login code that DrFed writes to its log, and makes one server with actors. All secrets are random for each start and stay in the run directory (mode 0700).

Automatic tests: ``tests/test_drfed_docker.py`` (set ``CB_DRFED_TEST=1``).
Manual work::

    python -m tests.drfed_stack up      # start the stack and show the connection data
    python -m tests.drfed_stack info    # show the connection data again
    python -m tests.drfed_stack down    # stop the stack and delete the run directory
"""

from __future__ import annotations

import argparse
import json
import os
import re
import secrets
import shutil
import socket
import ssl
import subprocess
import sys
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

import httpx

from tests.garage_stack import _write_certificates, _write_private, docker_available

REPO_ROOT = Path(__file__).resolve().parent.parent
COMPOSE_FILE = REPO_ROOT / "tests" / "drfed" / "docker-compose.yml"
DEFAULT_RUN_DIR = REPO_ROOT / ".dev" / "drfed-test"
DEFAULT_PROJECT = "claire-drfed-test"
ROOT_NAME = "drfed.test"
# The port of the gateway for the public origin of the Claire server under test (no TLS: the development rule of Claire is http with an IPv4 address). The gateway sends its requests to the sidecar of Claire (the service ``sidecar``).
CLAIRE_PORT = 8080
STATE_FILE = "stack.json"
# The first three numbers of the two Docker networks. CB_DRFED_EDGE_PREFIX and CB_DRFED_BACKEND_PREFIX select other networks when these are in use.
DEFAULT_EDGE_PREFIX = "172.31.251"
DEFAULT_BACKEND_PREFIX = "172.31.252"


class DrFedStackError(RuntimeError):
    pass


@dataclass
class DrFedActor:
    id: str
    iri: str
    handle: str
    inbox: str


@dataclass
class DrFedStack:
    """The connection data of one running stack."""

    run_dir: str
    project: str
    edge_prefix: str
    backend_prefix: str
    loopback_port: int
    email: str
    access_token: str = ""
    server_id: str = ""
    server_slug: str = ""
    actors: list[DrFedActor] = field(default_factory=list)
    root_name: str = ROOT_NAME

    @property
    def gateway_ip(self) -> str:
        return f"{self.edge_prefix}.2"

    @property
    def dns_ip(self) -> str:
        return f"{self.edge_prefix}.3"

    @property
    def root_origin(self) -> str:
        return f"https://{self.root_name}"

    @property
    def server_origin(self) -> str:
        return f"https://{self.server_slug}.{self.root_name}"

    @property
    def ca_file(self) -> str:
        return str(Path(self.run_dir) / "tls" / "ca.crt")

    @property
    def ca_bundle(self) -> str:
        """The public certificate authorities and the private one. Use it as ``SSL_CERT_FILE``."""
        return str(Path(self.run_dir) / "tls" / "ca-bundle.crt")

    def is_stack_name(self, host: str) -> bool:
        return host == self.root_name or host.endswith("." + self.root_name)

    @property
    def claire_origin(self) -> str:
        """The public origin for a Claire process in the development environment (``CLAIRE_PUBLIC_URL``)."""
        return f"http://{self.gateway_ip}:{CLAIRE_PORT}"

    def start_sidecar(self, claire_data_dir: Path) -> None:
        """Start the Fedify sidecar of a Claire process on the host. ``claire_data_dir`` is the data directory of that process: it has the Unix socket and the service key."""
        (Path(claire_data_dir) / "fedify").mkdir(exist_ok=True)
        _compose(self, "--profile", "claire", "up", "-d", "--build", "sidecar", timeout=1800, extra_env={"CB_DRFED_CLAIRE_DATA": str(claire_data_dir), "CB_DRFED_UID": str(os.getuid()), "CB_DRFED_GID": str(os.getgid())})

    def stop_sidecar(self) -> None:
        _compose(self, "--profile", "claire", "rm", "-f", "-s", "sidecar", check=False)

    def compose(self, *args: str, check: bool = True, timeout: float = 300) -> subprocess.CompletedProcess:
        return _compose(self, *args, check=check, timeout=timeout)

    def logs(self, service: str = "drfed") -> str:
        result = self.compose("logs", "--no-color", "--no-log-prefix", service, check=False)
        return result.stdout + result.stderr

    def probe(self, script: str) -> subprocess.CompletedProcess:
        """Run a shell script in a container that is only on the network ``edge`` and uses the DNS server of the stack."""
        return self.compose("--profile", "probe", "run", "--rm", "-T", "probe", script, check=False)

    def client(self, *, token: bool = True, timeout: float = 30.0) -> httpx.Client:
        """An HTTP client on the host for the stack: the names of the stack resolve to the gateway, and the certificate authority of the stack is trusted."""
        headers = {"authorization": f"Bearer {self.access_token}"} if token and self.access_token else {}
        return httpx.Client(transport=_GatewayTransport(self), headers=headers, timeout=timeout)

    def graphql(self, query: str, variables: dict[str, Any] | None = None, *, token: bool = True) -> dict[str, Any]:
        """Send one GraphQL request to the control surface of DrFed (the root origin). An answer with ``errors`` is an error."""
        with self.client(token=token) as client:
            response = client.post(f"{self.root_origin}/graphql", json={"query": query, "variables": variables or {}})
        try:
            body = response.json()
        except ValueError as exc:
            raise DrFedStackError(f"GraphQL answer is not JSON (HTTP {response.status_code}): {response.text[:300]}") from exc
        if body.get("errors"):
            raise DrFedStackError(f"GraphQL error: {json.dumps(body['errors'])[:600]}")
        return body["data"]


class _GatewayTransport(httpx.HTTPTransport):
    """Connect each name of the stack to the gateway on the Docker network. The Host header and the TLS server name stay the name of the request, as with a real wildcard DNS record."""

    def __init__(self, stack: DrFedStack) -> None:
        super().__init__(verify=ssl.create_default_context(cafile=stack.ca_file))
        self._stack = stack

    def handle_request(self, request: httpx.Request) -> httpx.Response:
        host = request.url.host
        if self._stack.is_stack_name(host):
            request.extensions = {**request.extensions, "sni_hostname": host}
            request.headers["host"] = host
            request.url = request.url.copy_with(host=self._stack.gateway_ip)
        return super().handle_request(request)


def _compose(stack: DrFedStack, *args: str, check: bool = True, timeout: float = 300, extra_env: dict[str, str] | None = None) -> subprocess.CompletedProcess:
    env = {**(extra_env or {}), **os.environ, "CB_DRFED_RUN_DIR": stack.run_dir, "CB_DRFED_PROJECT": stack.project, "CB_DRFED_EMAIL": stack.email, "CB_DRFED_ROOT_NAME": stack.root_name, "CB_DRFED_EDGE_PREFIX": stack.edge_prefix, "CB_DRFED_BACKEND_PREFIX": stack.backend_prefix}
    result = subprocess.run(["docker", "compose", "-f", str(COMPOSE_FILE), "-p", stack.project, *args], env=env, capture_output=True, text=True, timeout=timeout)
    if check and result.returncode != 0:
        raise DrFedStackError(f"docker compose {' '.join(args[:3])} failed: {result.stderr[-2000:]}")
    return result


def _loopback_port(stack: DrFedStack) -> int:
    out = stack.compose("port", "gateway", "443").stdout.strip()
    return int(out.rsplit(":", 1)[1])


def _wait_for_server(stack: DrFedStack) -> None:
    for _ in range(240):
        try:
            with socket.create_connection((stack.gateway_ip, 443), timeout=1):
                pass
            with stack.client(token=False, timeout=5) as client:
                if client.post(f"{stack.root_origin}/graphql", json={"query": "{ __typename }"}).status_code == 200:
                    return
        except (OSError, httpx.HTTPError):
            pass
        time.sleep(0.5)
    raise DrFedStackError(f"the DrFed server did not start: {stack.logs()[-1500:]}")


def sign_in(stack: DrFedStack) -> str:
    """Sign in with the account of the stack: ask for a login mail, read the login link in the log of DrFed (``--mail-log-content``), and complete the challenge. Return the access token."""
    challenge = stack.graphql("mutation($email: Email!, $url: URITemplate!) { loginByEmail(email: $email, verifyUrl: $url) { challengeId } }", {"email": stack.email, "url": f"{stack.root_origin}/sign/in/{{challengeId}}?code={{code}}"}, token=False)["loginByEmail"]["challengeId"]
    pattern = re.compile(re.escape(f"{stack.root_origin}/sign/in/{challenge}?code=") + r"([^\s\"'<>&\\]+)")
    for _ in range(60):
        found = pattern.search(stack.logs())
        if found:
            session = stack.graphql("mutation($id: UUID!, $code: String!) { completeLoginChallenge(challengeId: $id, code: $code) { accessToken } }", {"id": challenge, "code": found.group(1)}, token=False)["completeLoginChallenge"]
            if not session or not session.get("accessToken"):
                raise DrFedStackError("DrFed refused the login code")
            return str(session["accessToken"])
        time.sleep(0.5)
    raise DrFedStackError("the login link was not in the log of DrFed")


def create_server(stack: DrFedStack, slug: str) -> str:
    data = stack.graphql("mutation($slug: String!) { createInstance(slug: $slug) { __typename ... on Instance { id host } ... on CreateInstanceError { type message } } }", {"slug": slug})["createInstance"]
    if data["__typename"] != "Instance":
        raise DrFedStackError(f"DrFed did not make the server: {data}")
    return str(data["id"])


def generate_actors(stack: DrFedStack, server_id: str, size: int) -> list[DrFedActor]:
    data = stack.graphql("mutation($instance: ID!, $size: Int!) { generateActors(instance: $instance, size: $size) { __typename ... on CreateActorsSuccess { actors { id iri handle inboxUrl } } ... on CreateActorsError { type message } } }", {"instance": instance_id, "size": size})["generateActors"]
    if data["__typename"] != "CreateActorsSuccess":
        raise DrFedStackError(f"DrFed did not make the actors: {data}")
    return [DrFedActor(id=a["id"], iri=a["iri"], handle=a["handle"] or "", inbox=a["inboxUrl"]) for a in data["actors"]]


def start(run_dir: Path = DEFAULT_RUN_DIR, project: str = DEFAULT_PROJECT, *, actors: int = 2) -> DrFedStack:
    """Start a new stack. An old stack of the same project and its run directory are removed first."""
    run_dir = Path(run_dir).resolve()
    stack = DrFedStack(run_dir=str(run_dir), project=project, edge_prefix=os.environ.get("CB_DRFED_EDGE_PREFIX", DEFAULT_EDGE_PREFIX), backend_prefix=os.environ.get("CB_DRFED_BACKEND_PREFIX", DEFAULT_BACKEND_PREFIX), loopback_port=0, email=f"tester-{secrets.token_hex(4)}@{ROOT_NAME}")
    if run_dir.exists():
        stop(run_dir, project)
    run_dir.mkdir(parents=True, mode=0o700)
    try:
        _write_certificates(run_dir / "tls", dns_names=(ROOT_NAME, f"*.{ROOT_NAME}", "localhost"), ca_common_name="Claire DrFed Test CA")
        stack.compose("up", "-d", "--build", "drfed", "gateway", "dns", timeout=1200)
        _wait_for_server(stack)
        stack.loopback_port = _loopback_port(stack)
        stack.access_token = sign_in(stack)
        stack.server_slug = f"claire-{secrets.token_hex(3)}"
        stack.server_id = create_server(stack, stack.server_slug)
        stack.actors = generate_actors(stack, stack.server_id, actors)
        _write_private(run_dir / STATE_FILE, json.dumps(asdict(stack)).encode())
        return stack
    except BaseException:
        stop(run_dir, project)
        raise


def load(run_dir: Path = DEFAULT_RUN_DIR) -> DrFedStack:
    data = json.loads((Path(run_dir).resolve() / STATE_FILE).read_text())
    data["actors"] = [DrFedActor(**actor) for actor in data["actors"]]
    return DrFedStack(**data)


def stop(run_dir: Path = DEFAULT_RUN_DIR, project: str = DEFAULT_PROJECT) -> None:
    """Stop the stack, remove its volumes and networks, and delete the run directory."""
    run_dir = Path(run_dir).resolve()
    stack = DrFedStack(run_dir=str(run_dir), project=project, edge_prefix=os.environ.get("CB_DRFED_EDGE_PREFIX", DEFAULT_EDGE_PREFIX), backend_prefix=os.environ.get("CB_DRFED_BACKEND_PREFIX", DEFAULT_BACKEND_PREFIX), loopback_port=0, email="none@drfed.test")
    stack.compose("--profile", "probe", "--profile", "claire", "down", "-v", "--remove-orphans", check=False)
    shutil.rmtree(run_dir, ignore_errors=True)


def _info(stack: DrFedStack) -> str:
    return "\n".join(
        [
            "DrFed test stack (development test only).",
            f"Root origin: {stack.root_origin} (control surface and web UI)",
            f"Server: {stack.server_origin}",
            *(f"Actor: {actor.handle} {actor.iri}" for actor in stack.actors),
            f"Gateway on the Docker network: {stack.gateway_ip}:443. DNS server for the names: {stack.dns_ip}",
            f"Gateway on the host: 127.0.0.1:{stack.loopback_port} (send the Host header of the root origin or of a server)",
            f"Certificate authority: {stack.ca_file}",
            f"Account: {stack.email}",
            f"Access token (GraphQL, header Authorization: Bearer): {stack.access_token}",
        ]
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m tests.drfed_stack", description="DrFed test stack in Docker (development test only).")
    parser.add_argument("action", choices=["up", "info", "down"])
    args = parser.parse_args(argv)
    if not docker_available():
        print("Error: Docker is not available.", file=sys.stderr)
        return 1
    try:
        if args.action == "up":
            print(_info(start()))
        elif args.action == "info":
            print(_info(load()))
        else:
            stop()
            print("The DrFed test stack is stopped.")
    except (DrFedStackError, FileNotFoundError) as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
