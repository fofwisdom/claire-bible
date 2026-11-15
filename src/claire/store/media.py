"""Media storage interface, opaque key derivation, capability tokens, and implementations.

Design references:
- docs/design/MULTI_ACCOUNT_NAMESPACE_AND_SINGLE_FQDN_DESIGN.md §5.4, §16.1 (D1), §17.1 (P7)
- Key derivation: HMAC(derived_key, account_id || sha256(content)) + ext (S16)
- Cache rules:
  - Public media: Cache-Control: public, max-age=31536000, immutable
  - Private media: 1-hour capability viewing token, Cache-Control: private, no-store
- Invalidation: theme privacy switch or doc purge increments visibility_epoch and invokes CDN invalidation hook
"""

from __future__ import annotations

import asyncio
import hashlib
import hmac
import mimetypes
import os
import re
import time
from abc import ABC, abstractmethod
from collections.abc import AsyncIterable, Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from urllib.parse import quote, urlencode

from .secrets import SecretService, get_secret_service

__all__ = [
    "DEFAULT_MEDIA_TOKEN_TTL",
    "FallbackMediaStore",
    "LocalMediaStore",
    "MediaHead",
    "MediaObject",
    "MediaStore",
    "MediaStoreError",
    "RangeParseError",
    "S3MediaStore",
    "create_media_capability_token",
    "derive_media_key",
    "get_media_store",
    "is_object_storage_key",
    "parse_byte_range",
    "register_cdn_invalidator",
    "trigger_cdn_invalidation",
    "verify_media_capability_token",
]

DEFAULT_MEDIA_TOKEN_TTL = 3600
_CDN_INVALIDATORS: list[Callable[[list[str]], Any]] = []


# FEDERATION_REMOTE_READING_DESIGN.md Section 9.1.1 rule 6 (RT5): an object storage key has only these characters, so that the signed path and the sent path are the same.
_OBJECT_KEY_RE = re.compile(r"[A-Za-z0-9/_.-]+")
S3_TIMEOUT_SECONDS = 30.0


class RangeParseError(ValueError):
    """Raised when an HTTP Range header cannot be parsed or is unsatisfiable."""


class MediaStoreError(Exception):
    """The media store is not available or refused the operation (Section 9.1.1 rule 7)."""


def is_object_storage_key(key: str) -> bool:
    """True when ``key`` is a legal object storage key: the characters ``[A-Za-z0-9/_.-]`` only, no empty segment, and no ``.`` or ``..`` segment."""
    if not isinstance(key, str) or not _OBJECT_KEY_RE.fullmatch(key):
        return False
    return all(part not in ("", ".", "..") for part in key.split("/"))


@dataclass(slots=True)
class MediaHead:
    """The metadata of a stored media object, without the body."""

    content_type: str
    content_length: int
    etag: str | None = None


@dataclass(slots=True)
class MediaObject:
    """Retrieved media object representation."""

    body: bytes
    content_type: str
    content_length: int
    content_range: str | None = None
    etag: str | None = None
    status_code: int = 200


def parse_byte_range(range_header: str | None, total_length: int) -> tuple[int, int] | None:
    """Parse HTTP Range header (RFC 7233 / 9110) 'bytes=start-end'.

    Returns (start, end) inclusive, or None if no Range header provided.
    Raises RangeParseError if unsatisfiable.
    """
    if not range_header or not range_header.strip():
        return None
    header = range_header.strip()
    if not header.startswith("bytes="):
        raise RangeParseError(f"Unsupported range unit: {header}")
    range_spec = header[len("bytes=") :].strip()
    # Support only single range for media streaming
    if "," in range_spec:
        range_spec = range_spec.split(",", 1)[0].strip()
    parts = range_spec.split("-", 1)
    if len(parts) != 2:
        raise RangeParseError(f"Invalid range specification: {range_spec}")
    start_str, end_str = parts[0].strip(), parts[1].strip()

    if not start_str and not end_str:
        raise RangeParseError("Both start and end are empty in byte range")

    if not start_str:
        # Suffix byte range: bytes=-500
        try:
            suffix_len = int(end_str)
        except ValueError as err:
            raise RangeParseError(f"Invalid suffix length: {end_str}") from err
        if suffix_len <= 0:
            raise RangeParseError("Suffix length must be positive")
        start = max(0, total_length - suffix_len)
        end = total_length - 1
    elif not end_str:
        # Prefix byte range: bytes=500-
        try:
            start = int(start_str)
        except ValueError as err:
            raise RangeParseError(f"Invalid start offset: {start_str}") from err
        end = total_length - 1
    else:
        try:
            start = int(start_str)
            end = int(end_str)
        except ValueError as err:
            raise RangeParseError(f"Invalid range offsets: {range_spec}") from err

    if start > end or start >= total_length or start < 0:
        raise RangeParseError(f"Range {start}-{end} unsatisfiable for length {total_length}")
    end = min(end, total_length - 1)
    return start, end


def derive_media_key(
    account_id: int,
    content: bytes,
    ext: str = "",
    *,
    secret_service: SecretService | None = None,
) -> str:
    """Derive an opaque object key for media storage (설계 §5.4, §15 S16).

    Algorithm: HMAC(derived_key, f"{account_id}:{sha256(content)}").hexdigest() + ext
    - USERNAME, theme number, doc_id are NOT in the key (preserves single-mode anonymity).
    - Prevents cross-account file guessing (account_id is mixed in, no cross-account dedup).
    """
    svc = secret_service or get_secret_service()
    derived_key = svc.derive_hmac_key("media_key")
    content_sha256 = hashlib.sha256(content).hexdigest()
    msg = f"{int(account_id)}:{content_sha256}".encode("utf-8")
    opaque_hash = hmac.new(derived_key, msg, hashlib.sha256).hexdigest()
    clean_ext = ext.strip()
    if clean_ext and not clean_ext.startswith("."):
        clean_ext = f".{clean_ext}"
    return f"{opaque_hash}{clean_ext}"


def create_media_capability_token(
    account_id: int,
    theme_id: int,
    visibility_epoch: int,
    key: str,
    expires_in: int = DEFAULT_MEDIA_TOKEN_TTL,
    *,
    secret_service: SecretService | None = None,
    now: float | None = None,
) -> str:
    """Create a 1-hour capability token for accessing private media (설계 §5.2, §5.4).

    Binds (account_id, theme_id, visibility_epoch, key, exp) via HMAC-SHA256.
    """
    current_time = int(now if now is not None else time.time())
    exp = current_time + int(expires_in)
    svc = secret_service or get_secret_service()
    derived_key = svc.derive_hmac_key("media_capability_token")
    message = f"{int(account_id)}:{int(theme_id)}:{int(visibility_epoch)}:{key}:{exp}"
    sig = hmac.new(derived_key, message.encode("utf-8"), hashlib.sha256).hexdigest()
    return f"{exp}.{sig}"


def verify_media_capability_token(
    token: str,
    account_id: int,
    theme_id: int,
    visibility_epoch: int,
    key: str,
    *,
    secret_service: SecretService | None = None,
    now: float | None = None,
) -> bool:
    """Verify a capability token against binding (account_id, theme_id, visibility_epoch, key)."""
    if not token or not isinstance(token, str):
        return False
    if "token=" in token:
        token = token.split("token=")[1].split("&")[0]
    sep = "." if "." in token else ("_" if "_" in token else None)
    if sep is None:
        return False
    parts = token.split(sep, 1)
    if len(parts) != 2:
        return False
    exp_str, sig = parts
    try:
        exp = int(exp_str)
    except (ValueError, TypeError):
        return False
    current_time = int(now if now is not None else time.time())
    if current_time > exp:
        return False
    svc = secret_service or get_secret_service()
    derived_key = svc.derive_hmac_key("media_capability_token")
    message = f"{int(account_id)}:{int(theme_id)}:{int(visibility_epoch)}:{key}:{exp}"
    expected_sig = hmac.new(derived_key, message.encode("utf-8"), hashlib.sha256).hexdigest()
    return hmac.compare_digest(sig, expected_sig)


def register_cdn_invalidator(handler: Callable[[list[str]], Any]) -> None:
    """Register a callback for CDN cache invalidation."""
    if handler not in _CDN_INVALIDATORS:
        _CDN_INVALIDATORS.append(handler)


def trigger_cdn_invalidation(keys: list[str]) -> None:
    """Trigger all registered CDN invalidators for the given media keys."""
    if not keys:
        return
    for inv in list(_CDN_INVALIDATORS):
        try:
            res = inv(keys)
            if asyncio.iscoroutine(res):
                try:
                    loop = asyncio.get_running_loop()
                    loop.create_task(res)
                except RuntimeError:
                    asyncio.run(res)
        except Exception:
            pass


class MediaStore(ABC):
    """Abstract interface for media object storage (설계 §5.4)."""

    @abstractmethod
    async def put(self, key: str, data: bytes, content_type: str = "application/octet-stream") -> str:
        """Store media data under key. Returns key."""
        ...

    @abstractmethod
    async def get(self, key: str, range_header: str | None = None) -> MediaObject | None:
        """Retrieve media object under key, optionally handling HTTP byte ranges."""
        ...

    @abstractmethod
    async def delete(self, key: str) -> bool:
        """Delete media object under key. Returns True if deleted or did not exist."""
        ...

    async def head(self, key: str) -> MediaHead | None:
        """Return the metadata of the object under key, or ``None`` when there is no object. The default reads the object."""
        obj = await self.get(key)
        return None if obj is None else MediaHead(content_type=obj.content_type, content_length=obj.content_length, etag=obj.etag)

    @abstractmethod
    def presign(self, key: str, expires_in: int = 3600, method: str = "GET") -> str:
        """Return a presigned or capability-signed URL for direct access."""
        ...

    @abstractmethod
    async def invalidate(self, keys: list[str]) -> list[str]:
        """Invalidate cached entries across CDN/proxies. Returns invalidated keys."""
        ...


class LocalMediaStore(MediaStore):
    """Local filesystem-backed media storage implementation."""

    def __init__(
        self,
        base_dir: Path | str,
        *,
        secret_service: SecretService | None = None,
        base_url: str = "/media",
    ) -> None:
        self.base_dir = Path(base_dir).resolve()
        self.secret_service = secret_service
        self.base_url = base_url.rstrip("/")
        self.base_dir.mkdir(parents=True, exist_ok=True)

    def _safe_path(self, key: str) -> Path:
        clean_key = key.lstrip("/")
        if ".." in clean_key.split("/"):
            raise ValueError(f"Path traversal detected in key: {key}")
        target = (self.base_dir / clean_key).resolve()
        if not target.is_relative_to(self.base_dir):
            raise ValueError(f"Target outside base directory: {key}")
        return target

    async def put(self, key: str, data: bytes, content_type: str = "application/octet-stream") -> str:
        target = self._safe_path(key)
        target.parent.mkdir(parents=True, exist_ok=True)
        # Atomic write via temp file
        temp_path = target.with_suffix(f"{target.suffix}.tmp.{os.getpid()}")
        temp_path.write_bytes(data)
        temp_path.replace(target)
        return key

    async def get(self, key: str, range_header: str | None = None) -> MediaObject | None:
        target = self._safe_path(key)
        if not target.is_file():
            return None
        data = target.read_bytes()
        total_length = len(data)
        guessed_type, _ = mimetypes.guess_type(target.name)
        content_type = guessed_type or "application/octet-stream"
        etag = f'"{hashlib.sha256(data).hexdigest()[:16]}"'

        if range_header:
            start, end = parse_byte_range(range_header, total_length)
            sliced = data[start : end + 1]
            content_range = f"bytes {start}-{end}/{total_length}"
            return MediaObject(
                body=sliced,
                content_type=content_type,
                content_length=len(sliced),
                content_range=content_range,
                etag=etag,
                status_code=206,
            )

        return MediaObject(
            body=data,
            content_type=content_type,
            content_length=total_length,
            etag=etag,
            status_code=200,
        )

    async def delete(self, key: str) -> bool:
        target = self._safe_path(key)
        if target.is_file():
            target.unlink()
        return True

    async def head(self, key: str) -> MediaHead | None:
        target = self._safe_path(key)
        if not target.is_file():
            return None
        guessed_type, _ = mimetypes.guess_type(target.name)
        return MediaHead(content_type=guessed_type or "application/octet-stream", content_length=target.stat().st_size)

    def presign(self, key: str, expires_in: int = 3600, method: str = "GET") -> str:
        # Local presigning returns path relative to base_url
        clean_key = key.lstrip("/")
        return f"{self.base_url}/{clean_key}"

    async def invalidate(self, keys: list[str]) -> list[str]:
        trigger_cdn_invalidation(keys)
        return keys


class S3MediaStore(MediaStore):
    """S3-compatible object storage implementation using standard SigV4.

    Compatible with AWS S3, Cloudflare R2, MinIO, and Google Cloud Storage S3 API.
    Does not require external heavy SDKs; uses standard hashlib and httpx.
    """

    def __init__(
        self,
        endpoint_url: str,
        bucket: str,
        access_key: str,
        secret_key: str,
        region: str = "us-east-1",
        *,
        secret_service: SecretService | None = None,
        public_url_base: str | None = None,
    ) -> None:
        self.endpoint_url = endpoint_url.rstrip("/")
        self.bucket = bucket.strip()
        self.access_key = access_key
        self.secret_key = secret_key
        self.region = region
        self.secret_service = secret_service
        self.public_url_base = public_url_base.rstrip("/") if public_url_base else None

    def _sign_v4_request(
        self,
        method: str,
        path: str,
        query_params: dict[str, str],
        headers: dict[str, str],
        payload_hash: str,
        request_time: time.struct_time | None = None,
    ) -> dict[str, str]:
        """Generate AWS SigV4 authorization headers."""
        t = request_time or time.gmtime()
        amz_date = time.strftime("%Y%m%dT%H%M%SZ", t)
        date_stamp = time.strftime("%Y%m%d", t)

        headers = {k.lower(): v for k, v in headers.items()}
        headers["x-amz-date"] = amz_date
        headers["x-amz-content-sha256"] = payload_hash

        canonical_uri = path
        canonical_querystr = "&".join(f"{k}={v}" for k, v in sorted(query_params.items()))
        signed_headers = ";".join(sorted(headers.keys()))
        canonical_headers = "".join(f"{k}:{headers[k]}\n" for k in sorted(headers.keys()))

        canonical_request = f"{method}\n{canonical_uri}\n{canonical_querystr}\n{canonical_headers}\n{signed_headers}\n{payload_hash}"
        algorithm = "AWS4-HMAC-SHA256"
        credential_scope = f"{date_stamp}/{self.region}/s3/aws4_request"
        string_to_sign = f"{algorithm}\n{amz_date}\n{credential_scope}\n{hashlib.sha256(canonical_request.encode('utf-8')).hexdigest()}"

        def _sign(key: bytes, msg: str) -> bytes:
            return hmac.new(key, msg.encode("utf-8"), hashlib.sha256).digest()

        k_date = _sign(f"AWS4{self.secret_key}".encode("utf-8"), date_stamp)
        k_region = _sign(k_date, self.region)
        k_service = _sign(k_region, "s3")
        k_signing = _sign(k_service, "aws4_request")
        signature = hmac.new(k_signing, string_to_sign.encode("utf-8"), hashlib.sha256).hexdigest()

        auth_header = (
            f"{algorithm} Credential={self.access_key}/{credential_scope}, "
            f"SignedHeaders={signed_headers}, Signature={signature}"
        )
        headers["authorization"] = auth_header
        return headers

    def _object_url(self, key: str) -> str:
        clean_key = key.lstrip("/")
        return f"{self.endpoint_url}/{self.bucket}/{clean_key}"

    def _clean_key(self, key: str) -> str:
        """The key without a leading slash. A key that needs percent encoding is refused (Section 9.1.1 rule 6), because the signed path is the raw path."""
        clean_key = key.lstrip("/")
        if not is_object_storage_key(clean_key):
            raise ValueError(f"Not a legal object storage key: {key!r}")
        return clean_key

    async def _send(self, method: str, key: str, *, extra_headers: dict[str, str] | None = None, content: bytes | None = None) -> Any:
        """Send one signed request for the object of ``key``. A network failure is a ``MediaStoreError``."""
        import httpx

        clean_key = self._clean_key(key)
        headers = {"host": httpx.URL(self.endpoint_url).netloc.decode("ascii"), **(extra_headers or {})}
        payload_hash = hashlib.sha256(content or b"").hexdigest()
        signed_headers = self._sign_v4_request(method, f"/{self.bucket}/{clean_key}", {}, headers, payload_hash)
        url = self._object_url(clean_key)
        try:
            async with httpx.AsyncClient(timeout=S3_TIMEOUT_SECONDS) as client:
                if method == "PUT":
                    return await client.put(url, headers=signed_headers, content=content)
                if method == "GET":
                    return await client.get(url, headers=signed_headers)
                if method == "HEAD":
                    return await client.head(url, headers=signed_headers)
                return await client.delete(url, headers=signed_headers)
        except httpx.HTTPError as exc:
            raise MediaStoreError(f"{method} failed: {type(exc).__name__}") from exc

    @staticmethod
    def _refuse(method: str, resp: Any) -> MediaStoreError:
        return MediaStoreError(f"{method} failed: HTTP {resp.status_code}")

    async def put(self, key: str, data: bytes, content_type: str = "application/octet-stream") -> str:
        resp = await self._send("PUT", key, extra_headers={"content-type": content_type}, content=data)
        if resp.status_code >= 300:
            raise self._refuse("PUT", resp)
        return key

    async def get(self, key: str, range_header: str | None = None) -> MediaObject | None:
        resp = await self._send("GET", key, extra_headers={"range": range_header} if range_header else None)
        if resp.status_code == 404:
            return None
        if resp.status_code == 416:
            raise RangeParseError("Range unsatisfiable from S3 backend")
        if resp.status_code >= 300:
            raise self._refuse("GET", resp)
        body = resp.content
        return MediaObject(
            body=body,
            content_type=resp.headers.get("content-type", "application/octet-stream"),
            content_length=len(body),
            content_range=resp.headers.get("content-range"),
            etag=resp.headers.get("etag"),
            status_code=resp.status_code,
        )

    async def head(self, key: str) -> MediaHead | None:
        resp = await self._send("HEAD", key)
        if resp.status_code == 404:
            return None
        if resp.status_code >= 300:
            raise self._refuse("HEAD", resp)
        length = resp.headers.get("content-length", "")
        return MediaHead(content_type=resp.headers.get("content-type", "application/octet-stream"), content_length=int(length) if str(length).isdigit() else 0, etag=resp.headers.get("etag"))

    async def delete(self, key: str) -> bool:
        resp = await self._send("DELETE", key)
        if resp.status_code in (200, 204, 404):
            return True
        raise self._refuse("DELETE", resp)

    def presign(self, key: str, expires_in: int = 3600, method: str = "GET") -> str:
        """Generate a presigned GET URL with SigV4 query authentication."""
        clean_key = key.lstrip("/")
        t = time.gmtime()
        amz_date = time.strftime("%Y%m%dT%H%M%SZ", t)
        date_stamp = time.strftime("%Y%m%d", t)
        credential_scope = f"{date_stamp}/{self.region}/s3/aws4_request"
        algorithm = "AWS4-HMAC-SHA256"

        host = urlencode({"host": self.endpoint_url.split("://")[-1]}).split("=")[-1]
        qp = {
            "X-Amz-Algorithm": algorithm,
            "X-Amz-Credential": f"{self.access_key}/{credential_scope}",
            "X-Amz-Date": amz_date,
            "X-Amz-Expires": str(expires_in),
            "X-Amz-SignedHeaders": "host",
        }
        canonical_uri = f"/{self.bucket}/{clean_key}"
        # SigV4 signs the percent-encoded query (the credential has slashes).
        canonical_querystr = "&".join(f"{k}={quote(qp[k], safe='-_.~')}" for k in sorted(qp.keys()))
        canonical_headers = f"host:{self.endpoint_url.split('://')[-1]}\n"
        canonical_request = f"{method}\n{canonical_uri}\n{canonical_querystr}\n{canonical_headers}\nhost\nUNSIGNED-PAYLOAD"

        string_to_sign = f"{algorithm}\n{amz_date}\n{credential_scope}\n{hashlib.sha256(canonical_request.encode('utf-8')).hexdigest()}"

        def _sign(k: bytes, msg: str) -> bytes:
            return hmac.new(k, msg.encode("utf-8"), hashlib.sha256).digest()

        k_date = _sign(f"AWS4{self.secret_key}".encode("utf-8"), date_stamp)
        k_region = _sign(k_date, self.region)
        k_service = _sign(k_region, "s3")
        k_signing = _sign(k_service, "aws4_request")
        signature = hmac.new(k_signing, string_to_sign.encode("utf-8"), hashlib.sha256).hexdigest()

        qp["X-Amz-Signature"] = signature
        base = self.public_url_base or f"{self.endpoint_url}/{self.bucket}"
        query_string = "&".join(f"{k}={quote(qp[k], safe='-_.~')}" for k in sorted(qp.keys()))
        return f"{base}/{clean_key}?{query_string}"

    async def invalidate(self, keys: list[str]) -> list[str]:
        trigger_cdn_invalidation(keys)
        return keys


class FallbackMediaStore(MediaStore):
    """Object storage for writes, with a read fallback to local storage (FEDERATION_REMOTE_READING_DESIGN.md Section 9.1.1 rule 3).

    A read looks in ``primary``. A key that is not there, or a primary store that is not available, is read from ``fallback``. When the primary store is not available and the key is not in ``fallback``, the ``MediaStoreError`` of the primary store goes to the caller (rule 7).
    """

    def __init__(self, primary: MediaStore, fallback: MediaStore) -> None:
        self.primary = primary
        self.fallback = fallback

    async def put(self, key: str, data: bytes, content_type: str = "application/octet-stream") -> str:
        return await self.primary.put(key, data, content_type)

    async def get(self, key: str, range_header: str | None = None) -> MediaObject | None:
        try:
            obj = await self.primary.get(key, range_header=range_header)
        except (MediaStoreError, ValueError) as exc:
            if isinstance(exc, RangeParseError):
                raise
            obj = await self.fallback.get(key, range_header=range_header)
            if obj is None and isinstance(exc, MediaStoreError):
                raise
            return obj
        return obj if obj is not None else await self.fallback.get(key, range_header=range_header)

    async def head(self, key: str) -> MediaHead | None:
        try:
            found = await self.primary.head(key)
        except (MediaStoreError, ValueError) as exc:
            found = await self.fallback.head(key)
            if found is None and isinstance(exc, MediaStoreError):
                raise
            return found
        return found if found is not None else await self.fallback.head(key)

    async def delete(self, key: str) -> bool:
        await self.fallback.delete(key)
        return await self.primary.delete(key)

    def presign(self, key: str, expires_in: int = 3600, method: str = "GET") -> str:
        return self.primary.presign(key, expires_in=expires_in, method=method)

    async def invalidate(self, keys: list[str]) -> list[str]:
        return await self.primary.invalidate(keys)


def get_media_store(settings: Any | None = None) -> MediaStore:
    """The media store of this process (FEDERATION_REMOTE_READING_DESIGN.md Sections 9.1.1 and 9.2).

    Local storage under ``data/media`` is the default. With object storage (the admin settings with the mode ``s3``), the store writes to the bucket and reads fall back to local storage. A setting that cannot be read gives local storage: the process starts also then (rule 7).
    """
    from . import media_storage

    sec_svc = getattr(settings, "secret_service", None) if settings else None
    data_dir = getattr(settings, "data_dir", None) or Path("data")
    local = LocalMediaStore(Path(data_dir) / "media", secret_service=sec_svc)
    config = media_storage.active_config(settings)
    if config is None:
        return local
    return FallbackMediaStore(media_storage.build_s3_store(config, secret_service=sec_svc), local)
