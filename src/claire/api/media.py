"""Media sub-application for isolated media delivery.

Design references:
- docs/design/MULTI_ACCOUNT_NAMESPACE_AND_SINGLE_FQDN_DESIGN.md §5.1, §5.2, §5.4, §15 S16, §16.4
- Cookieless: incoming Cookie headers are ignored, Set-Cookie is never sent
- Origin: null allowed (for subresources requested by opaque-origin usercontent deck sandbox)
- Range requests: HTTP Range (RFC 7233 / 9110) support for media seeking (206 Partial Content)
- Cache rules:
  - Public media: Cache-Control: public, max-age=31536000, immutable
  - Private media: 1-hour capability token required, Cache-Control: private, no-store
- Unknown paths: 404 (e.g. app routes, admin routes, static js)
"""

from __future__ import annotations

import hashlib
import mimetypes
import os
import re
from collections.abc import Callable
from pathlib import Path
import logging
from typing import Any

from starlette.applications import Starlette
from starlette.responses import PlainTextResponse, Response
from starlette.routing import Route
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from ..store import db as dbm
from ..store.media import (
    DEFAULT_MEDIA_TOKEN_TTL,
    LocalMediaStore,
    MediaObject,
    MediaStore,
    MediaStoreError,
    RangeParseError,
    create_media_capability_token,
    get_media_store,
    parse_byte_range,
    verify_media_capability_token,
)
from ..store.secrets import SecretService, get_secret_service

__all__ = [
    "create_media_app",
]

log = logging.getLogger(__name__)

_PRIVATE_KEY_PREFIXES = ("remote/", "claire-probe/")

_LEGACY_IMAGE_RE = re.compile(r"^images/[A-Za-z0-9_.-]+\.(?:jpg|jpeg|png|webp|gif|svg|mp4|webm|mp3|ogg|bin)$")


class _MediaCookielessMiddleware:
    """ASGI middleware to strip Cookie headers and prevent Set-Cookie on media host."""

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] == "http":
            headers = [
                (k, v)
                for k, v in scope.get("headers", [])
                if k.lower() != b"cookie"
            ]
            clean_scope = {**scope, "headers": headers}

            async def safe_send(message: Message) -> None:
                if message["type"] == "http.response.start":
                    resp_headers = [
                        (k, v)
                        for k, v in message.get("headers", [])
                        if k.lower() != b"set-cookie"
                    ]
                    message = {**message, "headers": resp_headers}
                await send(message)

            await self.app(clean_scope, receive, safe_send)
        else:
            await self.app(scope, receive, send)


def create_media_app(
    settings: Any,
    *,
    store: MediaStore | None = None,
    secret_service: SecretService | None = None,
    roles: Any | None = None,
    now: Callable[[], float] | None = None,
) -> ASGIApp:
    """Create Starlette ASGI application for the media host (설계 §5.1, §5.4)."""
    media_store = store or get_media_store(settings)
    sec_svc = secret_service or getattr(settings, "secret_service", None) or get_secret_service()
    data_dir = Path(getattr(settings, "data_dir", None) or Path("data"))
    db_path_val = getattr(settings, "db_file", None) or getattr(settings, "CLAIRE_DB_PATH", "data/claire.db")
    db_path = Path(db_path_val) if db_path_val else None

    def _get_current_epoch(kb_id: int) -> int:
        if not db_path or not db_path.exists():
            return 1
        try:
            conn = dbm.connect_existing(db_path, readonly=True)
            try:
                row = conn.execute("SELECT visibility_epoch FROM knowledge_bases WHERE id = ?", (kb_id,)).fetchone()
                return int(row[0]) if row and row[0] is not None else 1
            finally:
                conn.close()
        except Exception:
            return 1

    async def media_object_route(request: Any) -> Response:
        key = request.path_params.get("key", "").lstrip("/")
        # The cache of remote media (``remote/``) is reached only through the proxy route with its token, and the probe objects of the storage test are not media.
        if not key or ".." in key.split("/") or key.startswith(_PRIVATE_KEY_PREFIXES):
            return PlainTextResponse("Not Found", status_code=404)

        # Token checking for capability access
        token = request.query_params.get("token") or request.query_params.get("s")
        if not token and "sig" in request.query_params and "exp" in request.query_params:
            token = f"{request.query_params['exp']}.{request.query_params['sig']}"

        is_private_req = "account_id" in request.query_params or "kb_id" in request.query_params or (token is not None)

        if is_private_req:
            if not token:
                return PlainTextResponse("Forbidden: viewing token required for private media", status_code=403)
            try:
                account_id = int(request.query_params.get("account_id", 1))
                kb_id = int(request.query_params.get("kb_id", 0))
                req_epoch = int(request.query_params.get("visibility_epoch", 1))
            except (ValueError, TypeError):
                return PlainTextResponse("Bad Request", status_code=400)

            # Check if requested epoch matches current theme epoch in DB
            cur_epoch = _get_current_epoch(kb_id)
            if req_epoch != cur_epoch:
                return PlainTextResponse("Forbidden: invalid or expired viewing token", status_code=403)

            cur_now = now() if now is not None else None
            is_valid = verify_media_capability_token(
                token=token,
                account_id=account_id,
                kb_id=kb_id,
                visibility_epoch=cur_epoch,
                key=key,
                secret_service=sec_svc,
                now=cur_now,
            )
            if not is_valid:
                return PlainTextResponse("Forbidden: invalid or expired viewing token", status_code=403)

        range_header = request.headers.get("range")
        # FEDERATION_REMOTE_READING_DESIGN.md Section 9.1.1 rule 7: when the object store is not available, a key that is not in local storage gets 503.
        store_error: MediaStoreError | None = None
        try:
            obj = await media_store.get(key, range_header=range_header)
        except RangeParseError:
            return PlainTextResponse("Requested Range Not Satisfiable", status_code=416)
        except MediaStoreError as exc:
            obj, store_error = None, exc
        except ValueError:
            obj = None

        if obj is None:
            # Fallback to local data/images if key exists there
            fallback_path = (data_dir / "images" / key).resolve()
            if fallback_path.is_file() and fallback_path.is_relative_to(data_dir / "images"):
                data = fallback_path.read_bytes()
                total_len = len(data)
                guessed, _ = mimetypes.guess_type(fallback_path.name)
                ctype = guessed or "application/octet-stream"
                etag_val = f'"{hashlib.sha256(data).hexdigest()[:16]}"'
                if range_header:
                    try:
                        start, end = parse_byte_range(range_header, total_len)
                        sliced = data[start : end + 1]
                        crange = f"bytes {start}-{end}/{total_len}"
                        obj = MediaObject(
                            body=sliced,
                            content_type=ctype,
                            content_length=len(sliced),
                            content_range=crange,
                            etag=etag_val,
                            status_code=206,
                        )
                    except RangeParseError:
                        return PlainTextResponse("Requested Range Not Satisfiable", status_code=416)
                else:
                    obj = MediaObject(
                        body=data,
                        content_type=ctype,
                        content_length=total_len,
                        etag=etag_val,
                        status_code=200,
                    )
            else:
                # A legacy image that the migration moved into the bucket has the object key ``images/{name}`` (Section 9.1.1 rule 6).
                legacy_key = f"images/{key}"
                if store_error is None and _LEGACY_IMAGE_RE.fullmatch(legacy_key):
                    try:
                        obj = await media_store.get(legacy_key, range_header=range_header)
                    except RangeParseError:
                        return PlainTextResponse("Requested Range Not Satisfiable", status_code=416)
                    except MediaStoreError as exc:
                        store_error = exc
                    except ValueError:
                        obj = None
                if store_error is not None:
                    log.error("The media store is not available for key %s: %s", key, store_error)
                    return PlainTextResponse("Service Unavailable", status_code=503, headers={"retry-after": "60"})
                if obj is None:
                    return PlainTextResponse("Not Found", status_code=404)

        cache_control = "private, no-store" if is_private_req else "public, max-age=31536000, immutable"
        headers = {
            "content-type": obj.content_type,
            "content-length": str(obj.content_length),
            "accept-ranges": "bytes",
            "cache-control": cache_control,
            "access-control-allow-origin": "*",
            "x-content-type-options": "nosniff",
        }
        if obj.content_range:
            headers["content-range"] = obj.content_range
        if obj.etag:
            headers["etag"] = obj.etag

        return Response(content=obj.body, status_code=obj.status_code, headers=headers)

    # Remote media of the federation client (FEDERATION_REMOTE_READING_DESIGN.md Section 9.1, unit FP4-7).
    from ..federation.media_proxy import MediaProxy, proxy_response, verify_media_token

    remote_proxy = MediaProxy(settings, media_store, now=now)

    async def remote_media_route(request: Any) -> Response:
        """``GET /media/remote/{url_hash}?t={token}``. A token that is absent, expired, or of another URL gets the same 404 as a URL without an entry."""
        url_hash = request.path_params.get("url_hash", "")
        token = request.query_params.get("t", "")
        if verify_media_token(token, url_hash, secret_service=sec_svc, now=now() if now is not None else None) is None:
            return PlainTextResponse("Not Found", status_code=404)
        return proxy_response(await remote_proxy.serve(url_hash, request.headers.get("range")))

    async def legacy_images_route(request: Any) -> Response:
        filename = request.path_params.get("filename", "").strip().lstrip("/")
        if filename.startswith("images/"):
            filename = filename[len("images/") :]
        rel = f"images/{filename}"
        if not _LEGACY_IMAGE_RE.fullmatch(rel):
            return PlainTextResponse("Not Found", status_code=404)

        # Route legacy image request through media_object_route
        request.path_params["key"] = filename
        return await media_object_route(request)

    async def not_found(request: Any) -> Response:
        return PlainTextResponse("Not Found", status_code=404)

    routes = [
        Route("/media/remote/{url_hash}", remote_media_route, methods=["GET", "HEAD"]),
        Route("/media/{key:path}", media_object_route, methods=["GET", "HEAD"]),
        Route("/images/{filename:path}", legacy_images_route, methods=["GET", "HEAD"]),
        Route("/p/images/{filename:path}", legacy_images_route, methods=["GET", "HEAD"]),
        Route("/{path:path}", not_found, methods=["GET", "POST", "PUT", "DELETE", "HEAD", "OPTIONS"]),
    ]

    raw_app = Starlette(routes=routes)
    return _MediaCookielessMiddleware(raw_app)
