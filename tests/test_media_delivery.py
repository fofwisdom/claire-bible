import time
import pytest
from types import SimpleNamespace
from starlette.testclient import TestClient

from claire.api.media import create_media_app
from claire.store.media import (
    LocalMediaStore,
    create_media_capability_token,
)
from claire.store.secrets import SecretService


@pytest.fixture
def media_env(tmp_path):
    sec = SecretService(b"s" * 32)
    media_dir = tmp_path / "media"
    media_dir.mkdir(parents=True)
    images_dir = tmp_path / "images"
    images_dir.mkdir(parents=True)

    store = LocalMediaStore(media_dir, secret_service=sec)
    settings = SimpleNamespace(data_dir=tmp_path, secret_service=sec)
    roles = SimpleNamespace(scheme="http", app="127.0.0.1:8766", media="127.0.0.1:8768")

    app = create_media_app(settings, store=store, secret_service=sec, roles=roles)
    client = TestClient(app, base_url="http://127.0.0.1:8768")
    return SimpleNamespace(
        client=client,
        store=store,
        sec=sec,
        media_dir=media_dir,
        images_dir=images_dir,
    )


@pytest.mark.asyncio
async def test_public_media_caching_and_cookieless(media_env):
    key = "sample.png"
    await media_env.store.put(key, b"\x89PNG\r\n\x1a\nfakeimagecontent", "image/png")

    resp = media_env.client.get(
        f"/media/{key}",
        headers={"Cookie": "claire_session=secret", "Origin": "null"},
    )
    assert resp.status_code == 200
    assert resp.content == b"\x89PNG\r\n\x1a\nfakeimagecontent"
    # Public caching rules: immutable, max-age=31536000
    assert "public" in resp.headers["cache-control"]
    assert "max-age=31536000" in resp.headers["cache-control"]
    assert "immutable" in resp.headers["cache-control"]
    # Cookieless: no Set-Cookie
    assert "set-cookie" not in resp.headers
    assert resp.headers["access-control-allow-origin"] == "*"
    assert resp.headers["accept-ranges"] == "bytes"


@pytest.mark.asyncio
async def test_private_media_capability_token_and_epoch_invalidation(media_env):
    key = "private_doc.pdf"
    content = b"%PDF-1.4 private sensitive document bytes"
    await media_env.store.put(key, content, "application/pdf")

    account_id = 7
    theme_id = 15
    visibility_epoch = 1
    now = time.time()

    # 1. Access without token is forbidden (403)
    resp_no_token = media_env.client.get(f"/media/{key}?account_id={account_id}&theme_id={theme_id}")
    assert resp_no_token.status_code == 403

    # 2. Access with valid capability token succeeds
    token = create_media_capability_token(
        account_id=account_id,
        theme_id=theme_id,
        visibility_epoch=visibility_epoch,
        key=key,
        secret_service=media_env.sec,
        now=now,
    )
    resp_valid = media_env.client.get(
        f"/media/{key}?account_id={account_id}&theme_id={theme_id}&visibility_epoch={visibility_epoch}&token={token}"
    )
    assert resp_valid.status_code == 200
    assert resp_valid.content == content
    # Private cache rules: private, no-store
    assert resp_valid.headers["cache-control"] == "private, no-store"

    # 3. Visibility epoch increment immediately invalidates the token
    bumped_epoch = visibility_epoch + 1
    resp_invalidated = media_env.client.get(
        f"/media/{key}?account_id={account_id}&theme_id={theme_id}&visibility_epoch={bumped_epoch}&token={token}"
    )
    assert resp_invalidated.status_code == 403


@pytest.mark.asyncio
async def test_media_streaming_range_requests(media_env):
    key = "stream_video.mp4"
    video_bytes = b"0123456789ABCDEFGHIJKLMNOPQRSTUVWXYZ"
    await media_env.store.put(key, video_bytes, "video/mp4")

    # Range request for bytes 0-9
    resp_range = media_env.client.get(
        f"/media/{key}",
        headers={"Range": "bytes=0-9"},
    )
    assert resp_range.status_code == 206
    assert resp_range.content == b"0123456789"
    assert resp_range.headers["content-range"] == f"bytes 0-9/{len(video_bytes)}"
    assert resp_range.headers["content-length"] == "10"
    assert resp_range.headers["accept-ranges"] == "bytes"

    # Suffix range request for last 10 bytes
    resp_suffix = media_env.client.get(
        f"/media/{key}",
        headers={"Range": "bytes=-10"},
    )
    assert resp_suffix.status_code == 206
    assert resp_suffix.content == video_bytes[-10:]
    assert resp_suffix.headers["content-range"] == f"bytes {len(video_bytes)-10}-{len(video_bytes)-1}/{len(video_bytes)}"

    # Unsatisfiable range returns 416
    resp_416 = media_env.client.get(
        f"/media/{key}",
        headers={"Range": "bytes=100-200"},
    )
    assert resp_416.status_code == 416


def test_media_app_unknown_paths_return_404(media_env):
    # App and admin paths on media host must return 404
    for p in ("/", "/health", "/admin/accounts", "/static/js/app.js", "/unknown"):
        resp = media_env.client.get(p)
        assert resp.status_code == 404
        assert "set-cookie" not in resp.headers


def test_legacy_images_fallback(media_env):
    # Fallback to data/images/
    legacy_file = media_env.images_dir / "legacy_test.png"
    legacy_file.write_bytes(b"\x89PNG\r\nlegacycontent")

    resp = media_env.client.get("/images/legacy_test.png")
    assert resp.status_code == 200
    assert resp.content == b"\x89PNG\r\nlegacycontent"

    resp_p = media_env.client.get("/p/images/legacy_test.png")
    assert resp_p.status_code == 200
    assert resp_p.content == b"\x89PNG\r\nlegacycontent"
