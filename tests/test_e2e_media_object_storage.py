"""End-to-end test of the object storage of the media store (FEDERATION_REMOTE_READING_DESIGN.md Sections 9.1.1 to 9.3, unit FP4-1).

A real app and a local S3-compatible server: the admin saves the object storage settings in ``/admin/storage``, the test route reports success, the migration command moves all local files with equal hashes and can run again, and the media host then reads the bucket.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
from types import SimpleNamespace

import pytest
from starlette.testclient import TestClient

from claire import cli
from claire.api import server
from claire.api.media import create_media_app
from claire.store.media import FallbackMediaStore, get_media_store
from tests.s3_test_server import ACCESS_KEY, BUCKET, REGION, SECRET_KEY, LocalS3Server
from tests.test_e2e_media_delivery import _call_asgi
from tests.test_federation_console import PUBLIC_URL, _audit_rows, _Client, _isolate, _prepare, _session  # noqa: F401  (_isolate is an autouse fixture)

STORAGE = "/api/admin/storage"
TEST = "/api/admin/storage/test"


@pytest.fixture
def s3():
    srv = LocalS3Server().start()
    yield srv
    srv.stop()


@pytest.fixture
def env(tmp_path):
    settings, alpha, _beta = _prepare(tmp_path)
    settings.allow_private_networks = True
    settings.media_fqdn = "127.0.0.1:8767"
    app = server.create_app(settings, SimpleNamespace(provider=SimpleNamespace(name="stub")))
    with TestClient(app, base_url=PUBLIC_URL) as tc:
        yield SimpleNamespace(client=_Client(tc), settings=settings, tmp_path=tmp_path, admin=_session(settings, 1), stale_admin=_session(settings, 1, authenticated_ago=16 * 60.0), member=_session(settings, alpha))


def _migrate(env, monkeypatch, capsys, *options: str) -> tuple[int, dict]:
    monkeypatch.setattr(cli, "get_settings", lambda: env.settings)
    args = cli.build_parser().parse_args(["media", "migrate", "--to", "object-storage", "--json", *options])
    code = args.func(args)
    return code, json.loads(capsys.readouterr().out)


def test_e2e_admin_sets_object_storage_tests_it_and_migrates(env, s3, monkeypatch, capsys):
    c = env.client
    images = env.tmp_path / "images"
    images.mkdir()
    files = {"doc1_0.png": b"png" * 500, "doc1_1.jpg": b"jpg" * 700}
    for name, data in files.items():
        (images / name).write_bytes(data)

    # Local storage is the default. A member does not see the admin routes.
    view = c.request("GET", STORAGE, session=env.admin).json()["storage"]
    assert view["mode"] == "local" and view["effective_mode"] == "local" and view["secret_key_set"] is False and view["last_test"] is None
    assert c.request("GET", STORAGE, session=env.member).status_code == 404
    assert c.request("PUT", STORAGE, session=env.member, json={"mode": "local"}).status_code == 404
    # A test without settings, and a change without a recent authentication, are refused.
    assert c.request("POST", TEST, session=env.admin, json={}).json()["error"] == "storage_not_configured"
    assert c.request("PUT", STORAGE, session=env.stale_admin, json={"region": REGION}).status_code == 401
    assert c.request("PUT", STORAGE, session=env.admin, json={"mode": "s3"}).status_code == 400

    # The admin saves the settings. The secret is never in a response, and the audit record has no secret.
    body = {"mode": "s3", "endpoint": s3.endpoint, "bucket": BUCKET, "region": REGION, "access_key": ACCESS_KEY, "secret_key": SECRET_KEY}
    res = c.request("PUT", STORAGE, session=env.admin, json=body)
    assert res.status_code == 200, res.text
    assert res.json()["restart_required"] is True and res.json()["storage"]["secret_key_set"] is True and SECRET_KEY not in res.text
    assert SECRET_KEY not in c.request("GET", STORAGE, session=env.admin).text
    audit = [row for row in _audit_rows(env.settings) if row[1] == "storage.update"]
    assert audit and SECRET_KEY not in json.dumps(audit) and audit[-1][2]["mode"] == "s3"

    # The test writes, reads, heads, and deletes one probe object, and the console shows the state.
    test = c.request("POST", TEST, session=env.admin, json={}).json()["test"]
    assert test["ok"] is True and test["failed_operation"] is None and not s3.objects
    assert {method for method, _path in s3.requests} == {"PUT", "GET", "HEAD", "DELETE"}
    page = c.request("GET", "/admin/storage", session=env.admin)
    assert page.status_code == 200 and 'id="admin-storage-form"' in page.text and 'data-storage-test-state="ok"' in page.text and SECRET_KEY not in page.text
    assert 'href="/admin/storage"' in page.text and 'data-storage-migration="none"' in page.text
    # A failed test reports the first operation that fails.
    s3.fail = 500
    failed = c.request("POST", TEST, session=env.admin, json={}).json()["test"]
    assert failed["ok"] is False and failed["failed_operation"] == "put"
    s3.fail = None

    # The migration: a dry run changes nothing; a run with --apply copies all files with equal hashes; a second run copies nothing.
    code, dry = _migrate(env, monkeypatch, capsys)
    assert code == 0 and dry["to_upload"] == 2 and not s3.objects
    code, run = _migrate(env, monkeypatch, capsys, "--apply")
    assert code == 0 and run["uploaded"] == 2 and run["errors"] == 0
    for name, data in files.items():
        assert hashlib.sha256(s3.objects[f"{BUCKET}/images/{name}"][0]).hexdigest() == hashlib.sha256(data).hexdigest()
    code, again = _migrate(env, monkeypatch, capsys, "--apply", "--delete-source")
    assert code == 0 and again["uploaded"] == 0 and again["already_stored"] == 2 and again["deleted_sources"] == 2 and not list(images.iterdir())
    assert 'data-storage-migration="present"' in c.request("GET", "/admin/storage", session=env.admin).text

    # After the next start, the store of the process is the bucket with the local fallback, and the media host serves the moved images.
    store = get_media_store(env.settings)
    assert isinstance(store, FallbackMediaStore)
    media_app = create_media_app(env.settings, store=store)
    served = asyncio.run(_call_asgi(media_app, "/media/doc1_0.png"))
    assert served.status == 200 and served.body == files["doc1_0.png"]


def test_environment_variables_lock_the_settings(env, s3, monkeypatch):
    for name, value in (("ENDPOINT", s3.endpoint), ("BUCKET", BUCKET), ("ACCESS_KEY", ACCESS_KEY), ("SECRET_KEY", SECRET_KEY), ("REGION", REGION)):
        monkeypatch.setenv(f"CLAIRE_MEDIA_S3_{name}", value)
    c = env.client
    view = c.request("GET", STORAGE, session=env.admin).json()["storage"]
    assert view["env_locked"] is True and view["effective_mode"] == "s3" and SECRET_KEY not in json.dumps(view)
    assert c.request("PUT", STORAGE, session=env.admin, json={"mode": "local"}).json()["error"] == "env_locked"
    assert c.request("POST", TEST, session=env.admin, json={}).json()["test"]["ok"] is True
    page = c.request("GET", "/admin/storage", session=env.admin).text
    assert "data-storage-env-locked" in page and SECRET_KEY not in page


def test_delete_source_needs_a_media_host(env, monkeypatch, capsys):
    env.settings.media_fqdn = ""
    monkeypatch.setattr(cli, "get_settings", lambda: env.settings)
    args = cli.build_parser().parse_args(["media", "migrate", "--to", "object-storage", "--apply", "--delete-source"])
    assert args.func(args) == 2 and "media host" in capsys.readouterr().err
