"""Development test only: service and security scenarios of the object storage against a Garage server in Docker (FEDERATION_REMOTE_READING_DESIGN.md Sections 9.1.1 to 9.3, RD26, RT10).

The stack of ``tests/garage_stack.py`` is a model of object storage on an external host: Garage is on an internal Docker network, and a TLS gateway with a certificate of a private certificate authority is the only way in. The tests connect to the host name ``garage.storage.test`` at the address of the gateway on the Docker network (not the loopback address).

The tests start containers, thus they run only with ``CB_GARAGE_TEST=1`` and a Docker daemon. ``CB_GARAGE_IMAGE`` selects a different Garage image.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import os
import secrets
import socket
import ssl
import time
from types import SimpleNamespace

import httpx
import pytest
from starlette.testclient import TestClient

from claire import cli, media_proxy
from claire.api import server
from claire.api.media import create_media_app
from claire.store import client_db, federation_media, media_storage
from claire.store import media as media_module
from claire.store.media import FallbackMediaStore, MediaStoreError, S3MediaStore, get_media_store
from claire.store.secrets import reset_secret_service
from tests import garage_stack
from tests.test_e2e_media_delivery import _call_asgi
from tests.test_federation_console import PUBLIC_URL, _audit_rows, _Client, _prepare, _session
from tests.test_media_proxy import PNG, MediaServer, _item

pytestmark = pytest.mark.skipif(os.environ.get("CB_GARAGE_TEST") != "1", reason="set CB_GARAGE_TEST=1 to run the Garage tests in Docker")

STORAGE = "/api/admin/storage"
TEST = "/api/admin/storage/test"
MIB = 1024 * 1024


@pytest.fixture(scope="module")
def stack(tmp_path_factory):
    if not garage_stack.docker_available():
        pytest.skip("Docker is not available")
    running = garage_stack.start(tmp_path_factory.mktemp("garage") / "run", project=f"claire-garage-pytest-{secrets.token_hex(4)}")
    yield running
    garage_stack.stop(running.run_dir, running.project)


@pytest.fixture(autouse=True)
def _external_host(stack, monkeypatch):
    """Clean environment, and the name of the external host resolves to the address of the gateway on the Docker network. The certificate authority of the gateway is not trusted."""
    for key in list(os.environ):
        if key.startswith(("CLAIRE_", "SSL_CERT_")):
            monkeypatch.delenv(key, raising=False)
    monkeypatch.setenv("CLAIRE_SECRET_KEY", "66" * 32)
    real = socket.getaddrinfo

    def _resolve(host, *args, **kwargs):
        name = host.decode("ascii", "replace") if isinstance(host, bytes) else host
        return real(stack.gateway_ip if name == stack.host_name else host, *args, **kwargs)

    monkeypatch.setattr(socket, "getaddrinfo", _resolve)
    reset_secret_service()
    yield
    reset_secret_service()


@pytest.fixture
def trust(stack, monkeypatch):
    """The process trusts the private certificate authority (in a container: a file in ``certs/``)."""
    monkeypatch.setenv("SSL_CERT_FILE", stack.ca_bundle)


def _store(stack, key: str = "rw", **changes) -> S3MediaStore:
    access = stack.keys[key]
    values = {"endpoint_url": stack.endpoint, "bucket": stack.bucket, "access_key": access.key_id, "secret_key": access.secret, "region": stack.region, **changes}
    return S3MediaStore(**values)


def _signed(store: S3MediaStore, stack, method: str, key: str, *, body: bytes = b"", request_time: time.struct_time | None = None) -> tuple[str, dict[str, str]]:
    path = f"/{stack.bucket}/{key}"
    headers = store._sign_v4_request(method, path, {}, {"host": f"{stack.host_name}:{garage_stack.S3_PORT}"}, hashlib.sha256(body).hexdigest(), request_time=request_time)
    return stack.endpoint + path, headers


# --- Transport security -------------------------------------------------------


async def test_tls_is_necessary_and_the_certificate_must_be_trusted(stack, monkeypatch):
    # A certificate from an authority that the process does not know stops the connection before a request goes out.
    result = await media_storage.probe_store(_store(stack))
    assert result["ok"] is False and result["failed_operation"] == "put" and result["detail"] == "PUT failed: ConnectError"
    monkeypatch.setenv("SSL_CERT_FILE", stack.ca_bundle)
    assert (await media_storage.probe_store(_store(stack)))["ok"] is True
    # The certificate has the host name only. A connection to the address does not agree with the certificate.
    by_address = await media_storage.probe_store(_store(stack, endpoint_url=f"https://{stack.gateway_ip}:{garage_stack.S3_PORT}"))
    assert by_address["ok"] is False and by_address["detail"] == "PUT failed: ConnectError"
    # The gateway has no port without TLS, and the settings refuse http for an external host, also in development.
    plain = await media_storage.probe_store(_store(stack, endpoint_url=f"http://{stack.host_name}:{garage_stack.S3_PORT}"))
    assert plain["ok"] is False and plain["failed_operation"] == "put"
    with pytest.raises(media_storage.StorageSettingsError):
        media_storage.validate_update({"endpoint": f"http://{stack.host_name}:{garage_stack.S3_PORT}"}, allow_private_http=True)


def test_an_external_client_reaches_only_the_s3_port(stack):
    """A container on the network ``edge``: the S3 port of the gateway is open; the admin API, the RPC port, and the Garage container are not there."""
    ports = stack.probe(f"for p in 3900 3901 3903 80; do nc -z -w 3 {stack.host_name} $p; echo $p=$?; done").stdout.split()
    assert ports == ["3900=0", "3901=1", "3903=1", "80=1"]
    assert stack.probe("nc -z -w 3 garage 3903; echo rc=$?").stdout.strip() == "rc=1"
    assert stack.probe(f"wget -q -T 3 -O- http://{stack.host_name}:3900/; echo rc=$?").stdout.strip() == "rc=1"
    # The loopback port of the host is the only published port.
    published = stack.compose("ps", "--format", "json").stdout
    assert "0.0.0.0" not in published and "[::]" not in published and f"127.0.0.1:{stack.loopback_port}" in published


# --- Service ------------------------------------------------------------------


async def test_garage_defaults_and_object_operations(stack, trust):
    """RT10: path-style addressing, the region ``garage``, and the four operations."""
    store = _store(stack)
    assert (await media_storage.probe_store(store))["ok"] is True
    key = f"images/doc-{secrets.token_hex(4)}_0.v1.png"
    data = bytes(range(256)) * 4
    await store.put(key, data, "image/png")
    head = await store.head(key)
    assert head is not None and head.content_length == len(data) and head.content_type == "image/png" and head.etag
    part = await store.get(key, range_header="bytes=10-19")
    assert part.status_code == 206 and part.body == data[10:20] and part.content_range == f"bytes 10-19/{len(data)}" and part.content_type == "image/png"
    assert (await store.get(key)).body == data
    with pytest.raises(media_module.RangeParseError):
        await store.get(key, range_header=f"bytes={len(data) + 10}-")
    # A large object goes through the gateway without a size limit.
    large = os.urandom(12 * MIB)
    await store.put("images/large.bin", large)
    assert hashlib.sha256((await store.get("images/large.bin")).body).digest() == hashlib.sha256(large).digest()
    for name in (key, "images/large.bin"):
        assert await store.delete(name) is True and await store.head(name) is None
    assert await store.get("images/not-there.png") is None and await store.delete("images/not-there.png") is True
    # A different region does not agree with the signature scope of the server.
    wrong_region = await media_storage.probe_store(_store(stack, region="us-east-1"))
    assert wrong_region["ok"] is False and wrong_region["failed_operation"] == "put"


# --- Access control of the server ----------------------------------------------


async def test_access_keys_have_only_their_permissions(stack, trust):
    key = "images/permission.png"
    await _store(stack).put(key, b"media", "image/png")
    try:
        cases = {
            "wrong secret": _store(stack, secret_key="0" * 64),
            "read-only key": _store(stack, "ro"),
            "key without a bucket": _store(stack, "none"),
            "key of a different bucket": _store(stack, "other"),
            "different bucket": _store(stack, bucket=stack.other_bucket),
            "unknown key": _store(stack, access_key="GK" + "0" * 24),
        }
        for name, store in cases.items():
            result = await media_storage.probe_store(store)
            assert result["ok"] is False and result["failed_operation"] == "put" and result["detail"] == "PUT failed: HTTP 403", name
            assert not any(access.secret in json.dumps(result) for access in stack.keys.values()), name
        # The read-only key reads, and cannot write or delete.
        read_only = _store(stack, "ro")
        assert (await read_only.get(key)).body == b"media"
        with pytest.raises(MediaStoreError):
            await read_only.delete(key)
        # The keys without the permission cannot read.
        for name in ("none", "other"):
            with pytest.raises(MediaStoreError):
                await _store(stack, name).get(key)
        assert (await _store(stack).get(key)).body == b"media"
    finally:
        await _store(stack).delete(key)


async def test_requests_without_a_correct_signature_are_refused(stack, trust):
    store = _store(stack)
    key, other = "images/signed.png", "images/signed-other.png"
    await store.put(key, b"good", "image/png")
    await store.put(other, b"other", "image/png")
    try:
        async with httpx.AsyncClient(verify=ssl.create_default_context(cafile=stack.ca_file)) as client:
            # No anonymous access: an object, the bucket, the list of the bucket, and the service root.
            for path in (f"/{stack.bucket}/{key}", f"/{stack.bucket}", f"/{stack.bucket}?list-type=2", "/"):
                assert (await client.get(stack.endpoint + path)).status_code == 403, path
            url, headers = _signed(store, stack, "GET", key)
            assert (await client.get(url, headers=headers)).status_code == 200
            # The signature is for one method and one path.
            assert (await client.delete(url, headers=headers)).status_code == 403
            assert (await client.get(stack.endpoint + f"/{stack.bucket}/{other}", headers=headers)).status_code == 403
            # A body that is not the signed body is refused, and the object does not change.
            url, headers = _signed(store, stack, "PUT", key, body=b"good")
            assert (await client.put(url, headers=headers, content=b"evil")).status_code == 400
            assert (await store.get(key)).body == b"good"
            # A signature from two days before is refused.
            url, headers = _signed(store, stack, "GET", key, request_time=time.gmtime(time.time() - 2 * 86400))
            assert (await client.get(url, headers=headers)).status_code in (400, 403)
            # A presigned address gives one object for a limited time. A change of the path, the method, or the time is refused.
            presigned = store.presign(key, expires_in=60)
            assert (await client.get(presigned)).content == b"good"
            assert (await client.get(presigned.replace(key, other))).status_code == 403
            assert (await client.put(presigned, content=b"evil")).status_code == 403
            assert (await client.get(presigned.replace("X-Amz-Expires=60", "X-Amz-Expires=6000"))).status_code == 403
    finally:
        await store.delete(key)
        await store.delete(other)


# --- The application with the external storage ------------------------------------


@pytest.fixture
def env(tmp_path, stack):
    settings, _alpha, _beta = _prepare(tmp_path)
    settings.allow_private_networks = True
    settings.media_fqdn = "127.0.0.1:8767"
    app = server.create_app(settings, SimpleNamespace(provider=SimpleNamespace(name="stub")))
    with TestClient(app, base_url=PUBLIC_URL) as tc:
        yield SimpleNamespace(client=_Client(tc), settings=settings, tmp_path=tmp_path, admin=_session(settings, 1))


def _migrate(env, monkeypatch, capsys, *options: str) -> tuple[int, dict]:
    monkeypatch.setattr(cli, "get_settings", lambda: env.settings)
    args = cli.build_parser().parse_args(["media", "migrate", "--to", "object-storage", "--json", *options])
    code = args.func(args)
    return code, json.loads(capsys.readouterr().out)


def _restart_gateway(stack) -> None:
    stack.compose("start", "gateway")
    stack.gateway_ip, stack.loopback_port = garage_stack._inspect_gateway(garage_stack.Path(stack.run_dir), stack.project)
    for _ in range(50):
        try:
            with socket.create_connection((stack.gateway_ip, garage_stack.S3_PORT), timeout=1):
                return
        except OSError:
            time.sleep(0.2)
    pytest.fail("the gateway did not start again")


def test_e2e_admin_console_migration_media_host_and_outage(env, stack, monkeypatch, capsys, caplog):
    c, access = env.client, stack.keys["rw"]
    secrets_of_stack = [key.secret for key in stack.keys.values()]
    images = env.tmp_path / "images"
    images.mkdir()
    files = {"doc7_0.png": b"png" * 5000, "doc7_1.jpg": os.urandom(300_000), "doc7_2.webp": b"webp" * 10}
    for name, data in files.items():
        (images / name).write_bytes(data)

    # The settings refuse http for the external host. The admin saves the https address.
    body = {"mode": "s3", "endpoint": f"http://{stack.host_name}:{garage_stack.S3_PORT}", "bucket": stack.bucket, "region": stack.region, "access_key": access.key_id, "secret_key": access.secret}
    assert c.request("PUT", STORAGE, session=env.admin, json=body).status_code == 400
    saved = c.request("PUT", STORAGE, session=env.admin, json={**body, "endpoint": stack.endpoint})
    assert saved.status_code == 200 and saved.json()["storage"]["secret_key_set"] is True and access.secret not in saved.text

    # The certificate authority is not trusted: the test reports the failure, and the mode stays as it was saved.
    untrusted = c.request("POST", TEST, session=env.admin, json={}).json()["test"]
    assert untrusted["ok"] is False and untrusted["failed_operation"] == "put"
    monkeypatch.setenv("SSL_CERT_FILE", stack.ca_bundle)
    assert c.request("POST", TEST, session=env.admin, json={}).json()["test"]["ok"] is True

    # A wrong secret: the test fails, and no response, audit record, or log line has a secret.
    assert c.request("PUT", STORAGE, session=env.admin, json={"secret_key": "f" * 64}).status_code == 200
    refused = c.request("POST", TEST, session=env.admin, json={})
    assert refused.json()["test"]["ok"] is False and refused.json()["test"]["detail"] == "PUT failed: HTTP 403"
    assert c.request("PUT", STORAGE, session=env.admin, json={"secret_key": access.secret}).status_code == 200
    assert c.request("POST", TEST, session=env.admin, json={}).json()["test"]["ok"] is True
    visible = refused.text + c.request("GET", STORAGE, session=env.admin).text + c.request("GET", "/admin/storage", session=env.admin).text + json.dumps(_audit_rows(env.settings)) + caplog.text
    assert not any(secret in visible for secret in [*secrets_of_stack, "f" * 64])
    # The stored secret is not plain text in the database file.
    assert access.secret.encode() not in env.settings.db_file.read_bytes()

    # The migration command: dry run, apply, and a second run that deletes the local files.
    code, dry = _migrate(env, monkeypatch, capsys)
    assert code == 0 and dry["to_upload"] == 3
    code, run = _migrate(env, monkeypatch, capsys, "--apply")
    assert code == 0 and run["uploaded"] == 3 and run["errors"] == 0
    code, again = _migrate(env, monkeypatch, capsys, "--apply", "--delete-source")
    assert code == 0 and again["uploaded"] == 0 and again["already_stored"] == 3 and again["deleted_sources"] == 3 and not list(images.iterdir())

    # After the next start, the media host reads the bucket. A new local file (an ingest writes to the local directory) is served from the disk.
    store = get_media_store(env.settings)
    assert isinstance(store, FallbackMediaStore)
    media_app = create_media_app(env.settings, store=store)
    for name, data in files.items():
        served = asyncio.run(_call_asgi(media_app, f"/media/{name}"))
        assert served.status == 200 and served.body == data, name
    (images / "local_only.png").write_bytes(b"local")
    assert asyncio.run(_call_asgi(media_app, "/media/local_only.png")).body == b"local"
    assert asyncio.run(_call_asgi(media_app, "/media/not_there.png")).status == 404

    # The external host goes away: a local file is served, a file that is only in the bucket gives 503 with Retry-After, and the test of the admin fails in a short time.
    monkeypatch.setattr(media_module, "S3_TIMEOUT_SECONDS", 4.0)
    stack.compose("stop", "gateway")
    try:
        assert asyncio.run(_call_asgi(media_app, "/media/local_only.png")).body == b"local"
        started = time.monotonic()
        lost = asyncio.run(_call_asgi(media_app, "/media/doc7_0.png"))
        assert lost.status == 503 and lost.headers.get("retry-after") == "60" and time.monotonic() - started < 15
        down = c.request("POST", TEST, session=env.admin, json={}).json()["test"]
        assert down["ok"] is False and down["failed_operation"] == "put"
    finally:
        _restart_gateway(stack)
    # The host comes back: no restart of the application is necessary.
    assert asyncio.run(_call_asgi(media_app, "/media/doc7_0.png")).body == files["doc7_0.png"]
    assert c.request("POST", TEST, session=env.admin, json={}).json()["test"]["ok"] is True
    for name in files:
        asyncio.run(_store(stack).delete(f"images/{name}"))


def test_remote_media_cache_uses_the_bucket_and_stays_private(tmp_path, stack, trust):
    """The media proxy (FP4-7) stores a remote file in the bucket, reads it from there, and deletes it at the cleanup. The public media route does not give the cache object."""
    settings, _alpha, _beta = _prepare(tmp_path)
    settings.allow_private_networks = True
    remote = MediaServer({"/a.png": ("image/png", PNG)}).start()
    try:
        url = f"{remote.base}/a.png"
        hash_value = federation_media.url_hash(url)
        conn = client_db.connect_client_db(client_db.client_db_path(settings.db_file))
        try:
            _item(conn, "https://r.example/1", f'<p><img src="{url}"></p>')
            federation_media.register_urls(conn, [url], now=time.time())
            conn.commit()
        finally:
            conn.close()
        store = FallbackMediaStore(_store(stack), media_module.LocalMediaStore(tmp_path / "images"))
        proxy = media_proxy.MediaProxy(settings, store)
        first = asyncio.run(proxy.serve(hash_value))
        assert first.status == 200 and first.obj.body == PNG and remote.hits == ["/a.png"]
        key = federation_media.store_key(hash_value)
        in_bucket = asyncio.run(_store(stack).head(key))
        assert in_bucket is not None and in_bucket.content_length == len(PNG) and not (tmp_path / "images" / "remote").exists()
        # The second request reads the bucket, not the remote server.
        assert asyncio.run(proxy.serve(hash_value, range_header="bytes=0-7")).obj.body == PNG[:8] and remote.hits == ["/a.png"]
        # The public route of the media host does not give the object without a media token.
        media_app = create_media_app(settings, store=store)
        assert asyncio.run(_call_asgi(media_app, f"/media/{key}")).status == 404
        assert asyncio.run(_call_asgi(media_app, f"/media/remote/{hash_value}")).status in (401, 403, 404)
        # The cleanup deletes the record and the object.
        conn = client_db.connect_client_db(client_db.client_db_path(settings.db_file))
        try:
            conn.execute("DELETE FROM federation_timeline_items")
            conn.commit()
        finally:
            conn.close()
        assert asyncio.run(proxy.remove_orphans()) == 1 and asyncio.run(_store(stack).head(key)) is None
    finally:
        remote.stop()
