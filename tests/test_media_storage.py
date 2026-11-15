"""Object storage of the media store (FEDERATION_REMOTE_READING_DESIGN.md Sections 9.1.1 to 9.3, unit FP4-1): the S3 client against a local S3-compatible server (external interface), the storage settings in ``instance_settings`` (I/O), and the read fallback of the media host (module interface)."""

from __future__ import annotations

import hashlib
import shutil
import subprocess
from types import SimpleNamespace

import pytest

from claire.api.media import create_media_app
from claire.store import db as dbm
from claire.store import media_storage
from claire.store.media import FallbackMediaStore, LocalMediaStore, MediaStoreError, S3MediaStore, get_media_store
from claire.store.secrets import SecretService, reset_secret_service
from tests.s3_test_server import ACCESS_KEY, BUCKET, REGION, SECRET_KEY, LocalS3Server
from tests.test_e2e_media_delivery import _call_asgi


@pytest.fixture(autouse=True)
def _isolate(monkeypatch):
    import os

    for key in list(os.environ):
        if key.startswith(("CLAIRE_", "CB_")):
            monkeypatch.delenv(key, raising=False)
    monkeypatch.setenv("CLAIRE_SECRET_KEY", "55" * 32)
    reset_secret_service()
    yield
    reset_secret_service()


@pytest.fixture
def s3():
    server = LocalS3Server().start()
    yield server
    server.stop()


def _store(server: LocalS3Server, **overrides) -> S3MediaStore:
    values = {"endpoint_url": server.endpoint, "bucket": BUCKET, "access_key": ACCESS_KEY, "secret_key": SECRET_KEY, "region": REGION, **overrides}
    return S3MediaStore(**values)


def _settings(tmp_path):
    db_file = tmp_path / "claire.db"
    conn = dbm.connect(db_file)
    dbm.init_db(conn)
    conn.close()
    return SimpleNamespace(db_file=db_file, data_dir=tmp_path, secret_service=SecretService(master_key=b"k" * 32))


async def test_s3_store_does_the_four_operations_with_a_checked_signature(s3):
    store = _store(s3)
    data = bytes(range(256)) * 4
    assert await store.put("remote/0123abcd", data, "image/png") == "remote/0123abcd"
    obj = await store.get("remote/0123abcd")
    assert obj is not None and obj.body == data and obj.content_type == "image/png" and obj.status_code == 200
    part = await store.get("remote/0123abcd", range_header="bytes=10-19")
    assert part is not None and part.body == data[10:20] and part.status_code == 206 and part.content_range == f"bytes 10-19/{len(data)}"
    head = await store.head("remote/0123abcd")
    assert head is not None and head.content_length == len(data) and head.content_type == "image/png"
    assert await store.delete("remote/0123abcd") is True
    assert await store.get("remote/0123abcd") is None and await store.head("remote/0123abcd") is None
    # The server checked the signature of each request: a wrong secret is refused.
    with pytest.raises(MediaStoreError):
        await _store(s3, secret_key="wrong").put("remote/x", b"1")
    # RT5: a key that needs percent encoding is refused before a request goes out.
    sent = len(s3.requests)
    for key in ("images/a b.png", "images/한글.png", "a/../b", "images/x?y"):
        with pytest.raises(ValueError):
            await store.put(key, b"1")
    assert len(s3.requests) == sent


async def test_s3_store_reports_a_server_that_is_not_available():
    store = S3MediaStore(endpoint_url="http://127.0.0.1:9", bucket=BUCKET, access_key=ACCESS_KEY, secret_key=SECRET_KEY)
    with pytest.raises(MediaStoreError):
        await store.get("remote/x")


def test_storage_settings_keep_the_secret_encrypted_and_out_of_the_view(tmp_path):
    settings = _settings(tmp_path)
    conn = dbm.connect_existing(settings.db_file)
    try:
        assert media_storage.settings_view(conn)["mode"] == "local" and media_storage.active_config(settings) is None
        # The mode s3 needs all fields.
        with pytest.raises(media_storage.StorageSettingsError):
            media_storage.set_admin_settings(conn, media_storage.validate_update({"mode": "s3"}), secret_service=settings.secret_service, updated_by=1)
        update = media_storage.validate_update({"mode": "s3", "endpoint": "https://garage.example:3900/", "bucket": BUCKET, "region": REGION, "access_key": ACCESS_KEY, "secret_key": SECRET_KEY})
        media_storage.set_admin_settings(conn, update, secret_service=settings.secret_service, updated_by=1)
        view = media_storage.settings_view(conn)
        assert view["secret_key_set"] is True and view["endpoint"] == "https://garage.example:3900" and view["mode"] == "s3"
        raw = conn.execute("SELECT value FROM instance_settings WHERE key='media_storage'").fetchone()[0]
        assert SECRET_KEY not in raw and SECRET_KEY not in repr(view)
    finally:
        conn.close()
    config = media_storage.active_config(settings)
    assert config is not None and config.secret_key == SECRET_KEY and config.region == REGION
    assert isinstance(get_media_store(settings), FallbackMediaStore)
    # A secret that cannot be decrypted gives local storage, and the process starts.
    other = SimpleNamespace(db_file=settings.db_file, data_dir=tmp_path, secret_service=SecretService(master_key=b"z" * 32))
    assert media_storage.active_config(other) is None and isinstance(get_media_store(other), LocalMediaStore)


def test_storage_settings_validation():
    for body in ({}, {"mode": "gcs"}, {"x": "1"}, {"endpoint": "ftp://a"}, {"endpoint": "http://garage.example:3900"}, {"endpoint": "https://u:p@a.example"}, {"bucket": "a/b"}, {"secret_key": ""}, {"secret_key": "a", "clear_secret_key": True}, {"region": "a\nb"}):
        with pytest.raises(media_storage.StorageSettingsError):
            media_storage.validate_update(body)
    # http is possible only for a private address in development.
    with pytest.raises(media_storage.StorageSettingsError):
        media_storage.validate_update({"endpoint": "http://127.0.0.1:3900"})
    assert media_storage.validate_update({"endpoint": "http://127.0.0.1:3900"}, allow_private_http=True)["endpoint"] == "http://127.0.0.1:3900"
    with pytest.raises(media_storage.StorageSettingsError):
        media_storage.validate_update({"endpoint": "http://garage.example:3900"}, allow_private_http=True)


def test_legacy_environment_is_imported_one_time(tmp_path, monkeypatch):
    """The variables of the old releases go into the admin settings when these were never saved. After that the variables have no effect."""
    settings = _settings(tmp_path)
    for name, value in (("ENDPOINT", "https://env.example"), ("BUCKET", "env-bucket"), ("ACCESS_KEY", "envkey"), ("SECRET_KEY", "envsecret")):
        monkeypatch.setenv(f"CLAIRE_MEDIA_S3_{name}", value)
    config = media_storage.active_config(settings)
    assert config is not None and (config.endpoint, config.bucket, config.secret_key, config.region) == ("https://env.example", "env-bucket", "envsecret", "us-east-1")
    assert b"envsecret" not in settings.db_file.read_bytes()
    # The admin changes the settings. The variables do not come back.
    conn = dbm.connect_existing(settings.db_file)
    try:
        media_storage.set_admin_settings(conn, {"mode": "local"}, secret_service=media_storage.secret_service_for(settings), updated_by=1)
        conn.commit()
        assert media_storage.settings_view(conn)["mode"] == "local"
    finally:
        conn.close()
    assert media_storage.active_config(settings) is None and isinstance(get_media_store(settings), LocalMediaStore)
    # Variables that are not complete change nothing.
    monkeypatch.delenv("CLAIRE_MEDIA_S3_BUCKET")
    assert media_storage.active_config(_settings(tmp_path / "other")) is None


async def test_media_host_reads_the_bucket_then_local_storage_and_answers_503(tmp_path, s3):
    """Section 9.1.1 rules 3 and 7: a key that is not in the bucket comes from local storage; when the object store is not available, a key that is not local gets 503."""
    images = tmp_path / "images"
    images.mkdir()
    (images / "doc1_0.png").write_bytes(b"local-image")
    store = FallbackMediaStore(_store(s3), LocalMediaStore(tmp_path / "media"))
    await store.put("images/doc2_0.png", b"bucket-image", "image/png")
    await store.put("remote/abc", b"remote-media", "image/png")
    app = create_media_app(SimpleNamespace(data_dir=tmp_path, db_file=tmp_path / "none.db"), store=store, secret_service=SecretService(master_key=b"k" * 32))
    assert (await _call_asgi(app, "/media/doc1_0.png")).body == b"local-image"
    assert (await _call_asgi(app, "/media/doc2_0.png")).body == b"bucket-image"
    assert (await _call_asgi(app, "/images/doc2_0.png")).body == b"bucket-image"
    # The cache of remote media is not reached through the public media route.
    assert (await _call_asgi(app, "/media/remote/abc")).status == 404 and (await _call_asgi(app, "/media/remote/abc/x")).status == 404
    assert (await _call_asgi(app, "/media/doc9_0.png")).status == 404
    s3.fail = 500
    assert (await _call_asgi(app, "/media/doc1_0.png")).body == b"local-image"
    assert (await _call_asgi(app, "/media/doc2_0.png")).status == 503


async def test_migration_copies_with_equal_hashes_and_can_run_again(tmp_path, s3):
    (tmp_path / "images").mkdir()
    (tmp_path / "media" / "remote").mkdir(parents=True)
    files = {"images/doc1_0.png": b"one" * 100, "images/doc1_1.jpg": b"two" * 100, "remote/abcd": b"three" * 100}
    (tmp_path / "images" / "doc1_0.png").write_bytes(files["images/doc1_0.png"])
    (tmp_path / "images" / "doc1_1.jpg").write_bytes(files["images/doc1_1.jpg"])
    (tmp_path / "media" / "remote" / "abcd").write_bytes(files["remote/abcd"])
    (tmp_path / "images" / "bad name.png").write_bytes(b"x")
    store = _store(s3)
    dry = await media_storage.migrate_to_object_storage(tmp_path, store)
    assert dry["applied"] is False and dry["total"] == 4 and dry["to_upload"] == 3 and dry["invalid_keys"] == 1 and not s3.objects
    # A file with a key that is not legal stops the delete of the sources, and stays local.
    run = await media_storage.migrate_to_object_storage(tmp_path, store, apply=True, delete_source=True)
    assert run["uploaded"] == 3 and run["errors"] == 1 and run["deleted_sources"] == 0
    for key, data in files.items():
        assert hashlib.sha256(s3.objects[f"{BUCKET}/{key}"][0]).hexdigest() == hashlib.sha256(data).hexdigest()
    (tmp_path / "images" / "bad name.png").unlink()
    # A second run uploads nothing. A changed local file is uploaded again.
    (tmp_path / "images" / "doc1_1.jpg").write_bytes(b"changed")
    again = await media_storage.migrate_to_object_storage(tmp_path, store, apply=True, delete_source=True)
    assert again["already_stored"] == 2 and again["uploaded"] == 1 and again["errors"] == 0 and again["deleted_sources"] == 3
    assert s3.objects[f"{BUCKET}/images/doc1_1.jpg"][0] == b"changed" and not list((tmp_path / "images").iterdir())


async def test_probe_reports_the_first_operation_that_fails(s3):
    assert (await media_storage.probe_store(_store(s3)))["ok"] is True and not s3.objects
    result = await media_storage.probe_store(_store(s3, bucket="missing"))
    assert result["ok"] is False and result["failed_operation"] == "put"


@pytest.mark.skipif(shutil.which("garage") is None, reason="the Garage server is not installed")
async def test_garage_accepts_the_four_operations(tmp_path):
    """RT10: a local Garage server (the reference S3 server, RD26) with path-style addressing and the region ``garage``."""
    import socket
    import time

    def _port() -> int:
        with socket.socket() as sock:
            sock.bind(("127.0.0.1", 0))
            return sock.getsockname()[1]

    s3_port, rpc_port, admin_port = _port(), _port(), _port()
    config = tmp_path / "garage.toml"
    config.write_text(
        f'metadata_dir = "{tmp_path / "meta"}"\ndata_dir = "{tmp_path / "data"}"\ndb_engine = "sqlite"\nreplication_factor = 1\n'
        f'rpc_bind_addr = "127.0.0.1:{rpc_port}"\nrpc_public_addr = "127.0.0.1:{rpc_port}"\nrpc_secret = "{"ab" * 32}"\n'
        f'[s3_api]\ns3_region = "garage"\napi_bind_addr = "127.0.0.1:{s3_port}"\n[admin]\napi_bind_addr = "127.0.0.1:{admin_port}"\n'
    )

    def _garage(*args: str) -> str:
        return subprocess.run(["garage", "-c", str(config), *args], check=True, capture_output=True, text=True, timeout=60).stdout

    server = subprocess.Popen(["garage", "-c", str(config), "server"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    try:
        for _ in range(100):
            try:
                node_id = _garage("node", "id", "-q").strip().split("@")[0]
                break
            except subprocess.CalledProcessError:
                time.sleep(0.2)
        else:
            pytest.fail("the Garage server did not start")
        _garage("layout", "assign", "-z", "dc1", "-c", "1G", node_id)
        _garage("layout", "apply", "--version", "1")
        _garage("bucket", "create", BUCKET)
        key_info = _garage("key", "create", "claire-test")
        access_key = next(line.split()[-1] for line in key_info.splitlines() if line.lower().startswith("key id"))
        secret_key = next(line.split()[-1] for line in key_info.splitlines() if line.lower().startswith("secret key"))
        _garage("bucket", "allow", "--read", "--write", "--owner", BUCKET, "--key", "claire-test")
        store = S3MediaStore(endpoint_url=f"http://127.0.0.1:{s3_port}", bucket=BUCKET, access_key=access_key, secret_key=secret_key, region="garage")
        result = await media_storage.probe_store(store)
        assert result["ok"] is True, result
    finally:
        server.terminate()
        server.wait(timeout=30)
