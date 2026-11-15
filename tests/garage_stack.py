"""Development test only: a Garage server in Docker behind a TLS gateway, as a model of object storage on an external host.

The module writes the Garage configuration, a private certificate authority, and the server certificate to a run directory, starts ``tests/garage/docker-compose.yml``, and makes the buckets and the access keys. All secrets are random for each start and stay in the run directory (mode 0700).

Automatic tests: ``tests/test_garage_docker.py`` (set ``CB_GARAGE_TEST=1``).
Manual work::

    python -m tests.garage_stack up      # start the stack and show the connection data
    python -m tests.garage_stack info    # show the connection data again
    python -m tests.garage_stack down    # stop the stack and delete the run directory
"""

from __future__ import annotations

import argparse
import datetime
import ipaddress
import json
import os
import secrets
import shutil
import subprocess
import sys
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
COMPOSE_FILE = REPO_ROOT / "tests" / "garage" / "docker-compose.yml"
DEFAULT_RUN_DIR = REPO_ROOT / ".dev" / "garage-test"
DEFAULT_PROJECT = "claire-garage-test"
HOST_NAME = "garage.storage.test"
S3_PORT = 3900
REGION = "garage"
BUCKET = "claire-media"
OTHER_BUCKET = "other-tenant"
STATE_FILE = "stack.json"


class GarageStackError(RuntimeError):
    pass


@dataclass
class AccessKey:
    key_id: str
    secret: str


@dataclass
class GarageStack:
    """The connection data of one running stack. ``keys`` has: ``rw`` (read and write on the media bucket), ``ro`` (read only), ``none`` (no bucket), ``other`` (owner of a different bucket)."""

    run_dir: str
    project: str
    gateway_ip: str
    loopback_port: int
    keys: dict[str, AccessKey] = field(default_factory=dict)
    host_name: str = HOST_NAME
    region: str = REGION
    bucket: str = BUCKET
    other_bucket: str = OTHER_BUCKET

    @property
    def endpoint(self) -> str:
        """The address of the external host. The name resolves only on the network ``edge``; a test on the host maps it to ``gateway_ip``."""
        return f"https://{self.host_name}:{S3_PORT}"

    @property
    def loopback_endpoint(self) -> str:
        return f"https://localhost:{self.loopback_port}"

    @property
    def ca_file(self) -> str:
        """The private certificate authority only."""
        return str(Path(self.run_dir) / "tls" / "ca.crt")

    @property
    def ca_bundle(self) -> str:
        """The public certificate authorities and the private one. Use it as ``SSL_CERT_FILE``."""
        return str(Path(self.run_dir) / "tls" / "ca-bundle.crt")

    def compose(self, *args: str, check: bool = True, timeout: float = 180) -> subprocess.CompletedProcess:
        return _compose(Path(self.run_dir), self.project, *args, check=check, timeout=timeout)

    def garage(self, *args: str) -> str:
        """Run one command of the Garage CLI in the server container."""
        return self.compose("exec", "-T", "garage", "/garage", "-c", "/etc/garage.toml", *args).stdout

    def probe(self, script: str) -> subprocess.CompletedProcess:
        """Run a shell script in a container that is only on the network ``edge``."""
        return self.compose("--profile", "probe", "run", "--rm", "-T", "probe", script, check=False)


def docker_available() -> bool:
    docker = shutil.which("docker")
    if docker is None:
        return False
    return subprocess.run([docker, "info"], capture_output=True, timeout=30).returncode == 0


def _compose(run_dir: Path, project: str, *args: str, check: bool = True, timeout: float = 180) -> subprocess.CompletedProcess:
    env = {**os.environ, "CB_GARAGE_RUN_DIR": str(run_dir), "CB_GARAGE_PROJECT": project}
    result = subprocess.run(["docker", "compose", "-f", str(COMPOSE_FILE), "-p", project, *args], env=env, capture_output=True, text=True, timeout=timeout)
    if check and result.returncode != 0:
        raise GarageStackError(f"docker compose {' '.join(args[:3])} failed: {result.stderr[-2000:]}")
    return result


def _write_private(path: Path, data: bytes) -> None:
    path.write_bytes(data)
    path.chmod(0o600)


def _write_certificates(tls_dir: Path) -> None:
    """Make a private certificate authority and a server certificate for the host name and the loopback addresses. The address of the gateway on the Docker network is not in the certificate."""
    from cryptography import x509
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.asymmetric import ec
    from cryptography.x509.oid import ExtendedKeyUsageOID, NameOID

    now = datetime.datetime.now(datetime.timezone.utc)
    start, end = now - datetime.timedelta(minutes=5), now + datetime.timedelta(days=30)
    ca_key = ec.generate_private_key(ec.SECP256R1())
    ca_name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "Claire Garage Test CA")])
    ca_cert = (
        x509.CertificateBuilder().subject_name(ca_name).issuer_name(ca_name).public_key(ca_key.public_key()).serial_number(x509.random_serial_number()).not_valid_before(start).not_valid_after(end)
        .add_extension(x509.BasicConstraints(ca=True, path_length=0), critical=True)
        .add_extension(x509.KeyUsage(digital_signature=True, key_cert_sign=True, crl_sign=True, content_commitment=False, key_encipherment=False, data_encipherment=False, key_agreement=False, encipher_only=False, decipher_only=False), critical=True)
        .add_extension(x509.SubjectKeyIdentifier.from_public_key(ca_key.public_key()), critical=False)
        .sign(ca_key, hashes.SHA256())
    )
    server_key = ec.generate_private_key(ec.SECP256R1())
    names = [x509.DNSName(HOST_NAME), x509.DNSName("localhost"), x509.IPAddress(ipaddress.ip_address("127.0.0.1"))]
    server_cert = (
        x509.CertificateBuilder().subject_name(x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, HOST_NAME)])).issuer_name(ca_name).public_key(server_key.public_key()).serial_number(x509.random_serial_number()).not_valid_before(start).not_valid_after(end)
        .add_extension(x509.BasicConstraints(ca=False, path_length=None), critical=True)
        .add_extension(x509.SubjectAlternativeName(names), critical=False)
        .add_extension(x509.ExtendedKeyUsage([ExtendedKeyUsageOID.SERVER_AUTH]), critical=False)
        .add_extension(x509.AuthorityKeyIdentifier.from_issuer_public_key(ca_key.public_key()), critical=False)
        .sign(ca_key, hashes.SHA256())
    )
    tls_dir.mkdir(mode=0o700)
    ca_pem = ca_cert.public_bytes(serialization.Encoding.PEM)
    (tls_dir / "ca.crt").write_bytes(ca_pem)
    (tls_dir / "server.crt").write_bytes(server_cert.public_bytes(serialization.Encoding.PEM))
    _write_private(tls_dir / "server.key", server_key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption()))
    # The key of the certificate authority is not written: no other certificate can come from this authority.
    import certifi

    (tls_dir / "ca-bundle.crt").write_bytes(Path(certifi.where()).read_bytes() + b"\n" + ca_pem)


def _write_garage_config(run_dir: Path) -> None:
    text = (
        'metadata_dir = "/var/lib/garage/meta"\ndata_dir = "/var/lib/garage/data"\ndb_engine = "sqlite"\nreplication_factor = 1\n'
        f'rpc_bind_addr = "[::]:3901"\nrpc_public_addr = "127.0.0.1:3901"\nrpc_secret = "{secrets.token_hex(32)}"\n'
        f'[s3_api]\ns3_region = "{REGION}"\napi_bind_addr = "[::]:{S3_PORT}"\n'
        f'[admin]\napi_bind_addr = "[::]:3903"\nadmin_token = "{secrets.token_urlsafe(32)}"\n'
    )
    _write_private(run_dir / "garage.toml", text.encode())


def _new_key() -> AccessKey:
    return AccessKey(key_id="GK" + secrets.token_hex(12), secret=secrets.token_hex(32))


def _inspect_gateway(run_dir: Path, project: str) -> tuple[str, int]:
    container = _compose(run_dir, project, "ps", "-q", "gateway").stdout.strip()
    if not container:
        raise GarageStackError("the gateway container is not there")
    data = json.loads(subprocess.run(["docker", "inspect", container], check=True, capture_output=True, text=True, timeout=30).stdout)[0]
    address = next(net["IPAddress"] for name, net in data["NetworkSettings"]["Networks"].items() if name.endswith("_edge"))
    port = int(data["NetworkSettings"]["Ports"][f"{S3_PORT}/tcp"][0]["HostPort"])
    return address, port


def start(run_dir: Path = DEFAULT_RUN_DIR, project: str = DEFAULT_PROJECT) -> GarageStack:
    """Start a new stack. An old stack of the same project and its run directory are removed first."""
    run_dir = Path(run_dir).resolve()
    if run_dir.exists():
        stop(run_dir, project)
    run_dir.mkdir(parents=True, mode=0o700)
    try:
        _write_certificates(run_dir / "tls")
        _write_garage_config(run_dir)
        _compose(run_dir, project, "up", "-d", "--wait", "garage", "gateway", timeout=600)
        stack = GarageStack(run_dir=str(run_dir), project=project, gateway_ip="", loopback_port=0)
        for _ in range(150):
            node = stack.compose("exec", "-T", "garage", "/garage", "-c", "/etc/garage.toml", "node", "id", "-q", check=False)
            if node.returncode == 0 and node.stdout.strip():
                break
            time.sleep(0.2)
        else:
            raise GarageStackError(f"the Garage server did not start: {node.stderr[-500:]}")
        stack.garage("layout", "assign", "-z", "dc1", "-c", "1G", node.stdout.strip().split("@")[0])
        stack.garage("layout", "apply", "--version", "1")
        stack.keys = {name: _new_key() for name in ("rw", "ro", "none", "other")}
        for name, key in stack.keys.items():
            stack.garage("key", "import", "--yes", "-n", f"claire-{name}", key.key_id, key.secret)
        stack.garage("bucket", "create", BUCKET)
        stack.garage("bucket", "create", OTHER_BUCKET)
        stack.garage("bucket", "allow", "--read", "--write", BUCKET, "--key", stack.keys["rw"].key_id)
        stack.garage("bucket", "allow", "--read", BUCKET, "--key", stack.keys["ro"].key_id)
        stack.garage("bucket", "allow", "--read", "--write", "--owner", OTHER_BUCKET, "--key", stack.keys["other"].key_id)
        stack.gateway_ip, stack.loopback_port = _inspect_gateway(run_dir, project)
        _write_private(run_dir / STATE_FILE, json.dumps(asdict(stack)).encode())
        return stack
    except BaseException:
        stop(run_dir, project)
        raise


def load(run_dir: Path = DEFAULT_RUN_DIR) -> GarageStack:
    data = json.loads((Path(run_dir).resolve() / STATE_FILE).read_text())
    data["keys"] = {name: AccessKey(**key) for name, key in data["keys"].items()}
    return GarageStack(**data)


def stop(run_dir: Path = DEFAULT_RUN_DIR, project: str = DEFAULT_PROJECT) -> None:
    """Stop the stack, remove its volumes and networks, and delete the run directory."""
    run_dir = Path(run_dir).resolve()
    _compose(run_dir, project, "--profile", "probe", "down", "-v", "--remove-orphans", check=False)
    shutil.rmtree(run_dir, ignore_errors=True)


def _info(stack: GarageStack) -> str:
    key = stack.keys["rw"]
    return "\n".join(
        [
            "Garage test stack (development test only).",
            f"Endpoint on the host: {stack.loopback_endpoint}",
            f"Endpoint on the Docker network: {stack.endpoint} (address {stack.gateway_ip})",
            f"Region: {stack.region}",
            f"Bucket: {stack.bucket}",
            f"Certificate authority: {stack.ca_file}",
            "",
            "Environment for a development process on the host:",
            f"export SSL_CERT_FILE={stack.ca_bundle}",
            f"export CLAIRE_MEDIA_S3_ENDPOINT={stack.loopback_endpoint}",
            f"export CLAIRE_MEDIA_S3_BUCKET={stack.bucket}",
            f"export CLAIRE_MEDIA_S3_REGION={stack.region}",
            f"export CLAIRE_MEDIA_S3_ACCESS_KEY={key.key_id}",
            f"export CLAIRE_MEDIA_S3_SECRET_KEY={key.secret}",
        ]
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m tests.garage_stack", description="Garage test stack in Docker (development test only).")
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
            print("The Garage test stack is stopped.")
    except (GarageStackError, FileNotFoundError) as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
