import time
import pytest
import httpx
from unittest.mock import AsyncMock

from claire.store.media import (
    DEFAULT_MEDIA_TOKEN_TTL,
    LocalMediaStore,
    MediaObject,
    MediaStore,
    RangeParseError,
    S3MediaStore,
    create_media_capability_token,
    derive_media_key,
    parse_byte_range,
    register_cdn_invalidator,
    trigger_cdn_invalidation,
    verify_media_capability_token,
)
from claire.store.secrets import SecretService


def test_byte_range_parser():
    assert parse_byte_range(None, 1000) is None
    assert parse_byte_range("", 1000) is None
    assert parse_byte_range("bytes=0-499", 1000) == (0, 499)
    assert parse_byte_range("bytes=500-999", 1000) == (500, 999)
    assert parse_byte_range("bytes=500-", 1000) == (500, 999)
    assert parse_byte_range("bytes=-200", 1000) == (800, 999)

    with pytest.raises(RangeParseError):
        parse_byte_range("items=0-10", 1000)
    with pytest.raises(RangeParseError):
        parse_byte_range("bytes=1000-2000", 1000)
    with pytest.raises(RangeParseError):
        parse_byte_range("bytes=500-200", 1000)
    with pytest.raises(RangeParseError):
        parse_byte_range("bytes=-0", 1000)


def test_opaque_media_key_derivation_and_s16_isolation():
    sec = SecretService(b"k" * 32)
    content = b"sample image binary content"

    key1 = derive_media_key(account_id=1, content=content, ext=".png", secret_service=sec)
    key2 = derive_media_key(account_id=2, content=content, ext=".png", secret_service=sec)

    assert key1.endswith(".png")
    assert key2.endswith(".png")
    # S16: No cross-account dedup; same content produces different keys across accounts
    assert key1 != key2
    # Verify no account_id, username, or doc_id leaked in plain text
    assert "account" not in key1
    assert "admin" not in key1


def test_media_capability_token_lifecycle_and_binding():
    sec = SecretService(b"k" * 32)
    account_id = 42
    theme_id = 100
    visibility_epoch = 1
    key = "opaque_media_key.png"
    now = 1700000000.0

    token = create_media_capability_token(
        account_id=account_id,
        theme_id=theme_id,
        visibility_epoch=visibility_epoch,
        key=key,
        expires_in=3600,
        secret_service=sec,
        now=now,
    )
    assert token.startswith(f"{int(now) + 3600}.")

    # Valid verification
    assert verify_media_capability_token(
        token=token,
        account_id=account_id,
        theme_id=theme_id,
        visibility_epoch=visibility_epoch,
        key=key,
        secret_service=sec,
        now=now + 1800,
    )

    # Expired token rejected
    assert not verify_media_capability_token(
        token=token,
        account_id=account_id,
        theme_id=theme_id,
        visibility_epoch=visibility_epoch,
        key=key,
        secret_service=sec,
        now=now + 3601,
    )

    # Mismatched account rejected
    assert not verify_media_capability_token(
        token=token,
        account_id=account_id + 1,
        theme_id=theme_id,
        visibility_epoch=visibility_epoch,
        key=key,
        secret_service=sec,
        now=now,
    )

    # Mismatched visibility epoch rejected
    assert not verify_media_capability_token(
        token=token,
        account_id=account_id,
        theme_id=theme_id,
        visibility_epoch=visibility_epoch + 1,
        key=key,
        secret_service=sec,
        now=now,
    )

    # Mismatched key rejected
    assert not verify_media_capability_token(
        token=token,
        account_id=account_id,
        theme_id=theme_id,
        visibility_epoch=visibility_epoch,
        key="different_key.png",
        secret_service=sec,
        now=now,
    )


@pytest.mark.asyncio
async def test_local_media_store_operations(tmp_path):
    store = LocalMediaStore(tmp_path / "media")
    data = b"Hello Claire Media Streaming Range Test 0123456789"
    key = "sub/test.txt"

    # Put
    res_key = await store.put(key, data, content_type="text/plain")
    assert res_key == key

    # Path traversal rejected
    with pytest.raises(ValueError, match="Path traversal"):
        await store.put("../evil.txt", b"x")

    # Get full
    obj = await store.get(key)
    assert obj is not None
    assert obj.body == data
    assert obj.status_code == 200
    assert obj.content_length == len(data)
    assert obj.content_range is None

    # Get range
    obj_range = await store.get(key, range_header="bytes=0-11")
    assert obj_range is not None
    assert obj_range.body == b"Hello Claire"
    assert obj_range.status_code == 206
    assert obj_range.content_length == 12
    assert obj_range.content_range == f"bytes 0-11/{len(data)}"

    # Delete
    del_ok = await store.delete(key)
    assert del_ok is True
    assert await store.get(key) is None


@pytest.mark.asyncio
async def test_s3_media_store_signing_and_mock_io(monkeypatch):
    store = S3MediaStore(
        endpoint_url="https://s3.example.com",
        bucket="mybucket",
        access_key="AKIAEXAMPLE",
        secret_key="SECRETEXAMPLE",
        region="us-east-1",
        public_url_base="https://media.example.com",
    )

    # Presign check
    presigned = store.presign("images/photo.jpg", expires_in=1800)
    assert presigned.startswith("https://media.example.com/images/photo.jpg?")
    assert "X-Amz-Signature=" in presigned
    assert "X-Amz-Expires=1800" in presigned

    # Mock httpx AsyncClient
    content = b"s3 media object data"

    class MockResponse:
        def __init__(self, status_code, content, headers=None):
            self.status_code = status_code
            self.content = content
            self.headers = headers or {}

        def raise_for_status(self):
            if self.status_code >= 400:
                raise httpx.HTTPStatusError("err", request=None, response=self)

    class MockAsyncClient:
        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc_val, exc_tb):
            pass

        async def put(self, url, headers=None, content=None):
            assert "authorization" in headers
            assert "AWS4-HMAC-SHA256" in headers["authorization"]
            return MockResponse(200, b"")

        async def get(self, url, headers=None):
            assert "authorization" in headers
            if "range" in headers:
                return MockResponse(206, content[:5], {"content-type": "image/png", "content-range": f"bytes 0-4/{len(content)}"})
            return MockResponse(200, content, {"content-type": "image/png"})

        async def delete(self, url, headers=None):
            return MockResponse(204, b"")

    monkeypatch.setattr(httpx, "AsyncClient", MockAsyncClient)

    # Put
    res = await store.put("photo.png", content, "image/png")
    assert res == "photo.png"

    # Get full
    obj = await store.get("photo.png")
    assert obj is not None
    assert obj.body == content
    assert obj.status_code == 200

    # Get range
    obj_range = await store.get("photo.png", range_header="bytes=0-4")
    assert obj_range is not None
    assert obj_range.body == content[:5]
    assert obj_range.status_code == 206

    # Delete
    assert await store.delete("photo.png") is True


def test_cdn_invalidator_trigger():
    called_keys = []

    def handler(keys):
        called_keys.extend(keys)

    register_cdn_invalidator(handler)
    trigger_cdn_invalidation(["key1.jpg", "key2.png"])
    assert "key1.jpg" in called_keys
    assert "key2.png" in called_keys
