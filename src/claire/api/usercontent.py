"""Usercontent isolated sub-application and capability viewing token management.

This module implements the usercontent sub-application for rendering user-authored and LLM-generated HTML
in an isolated origin (설계 §5.1–§5.2, §15 S1, §16.4 Rev. 2.3).

Key security features:
1. Capability viewing tokens: Short-term (default 1-hour / 3600s) capability URLs signed with HMAC-SHA256
   bound to (account_id, theme_id, doc_id, visibility_epoch, exp) via SecretService. Invalidated immediately
   upon visibility changes or expiration.
2. Sanitization: All HTML in static render profiles is sanitized via nh3 (allowlist sanitizer),
   completely stripping user scripts and inline event handlers (onerror, onload, onclick, etc.).
3. Strict Content Security Policy (CSP):
   - Static profile: frame-ancestors {scheme}://{app_host}; sandbox allow-popups allow-popups-to-escape-sandbox
   - Deck profile: script-src {scheme}://{usercontent_host} 'nonce-{nonce}'; frame-ancestors {scheme}://{app_host};
4. Cookie boundary: No cookies are read or set; incoming Cookie headers are ignored and Set-Cookie is blocked.
5. Opaque sandbox Origin: null requests are allowed for subresources (fonts, styles, scripts).
"""

from __future__ import annotations

import hashlib
import html
import hmac
import re
import secrets
import sqlite3
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any

import nh3
from starlette.applications import Starlette
from starlette.responses import FileResponse, HTMLResponse, PlainTextResponse, Response
from starlette.routing import Route
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from ..store import db as dbm
from ..store.secrets import SecretService, get_secret_service

__all__ = [
    "DEFAULT_VIEWING_TOKEN_TTL",
    "create_usercontent_app",
    "create_viewing_token",
    "sanitize_usercontent_html",
    "verify_viewing_token",
]

DEFAULT_VIEWING_TOKEN_TTL = 3600
STATIC_DIR = Path(__file__).resolve().parent.parent / "static"
ALLOWED_STATIC_PREFIXES = ("vendor/", "css/", "fonts/", "icons/")
DISALLOWED_STATIC_PREFIXES = ("js/",)


def create_viewing_token(
    account_id: int,
    theme_id: int,
    doc_id: int,
    visibility_epoch: int,
    expires_in: int = DEFAULT_VIEWING_TOKEN_TTL,
    *,
    secret_service: SecretService | None = None,
    now: float | None = None,
) -> str:
    """Create a capability viewing token binding (account_id, theme_id, doc_id, visibility_epoch, exp)."""
    current_time = int(now if now is not None else time.time())
    exp = current_time + int(expires_in)
    svc = secret_service or get_secret_service()
    derived_key = svc.derive_hmac_key("viewing_token")
    message = f"{int(account_id)}:{int(theme_id)}:{int(doc_id)}:{int(visibility_epoch)}:{exp}"
    sig = hmac.new(derived_key, message.encode("utf-8"), hashlib.sha256).hexdigest()
    return f"{exp}.{sig}"


def verify_viewing_token(
    token: str,
    account_id: int,
    theme_id: int,
    doc_id: int,
    visibility_epoch: int,
    *,
    secret_service: SecretService | None = None,
    now: float | None = None,
) -> bool:
    """Verify a capability viewing token against binding (account_id, theme_id, doc_id, visibility_epoch)."""
    if not token or not isinstance(token, str):
        return False
    # If full URL parameter string was passed
    if "token=" in token:
        token = token.split("token=")[1].split("&")[0]
    sep = "." if "." in token else ("_" if "_" in token else (":" if ":" in token else None))
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
    derived_key = svc.derive_hmac_key("viewing_token")
    message = f"{int(account_id)}:{int(theme_id)}:{int(doc_id)}:{int(visibility_epoch)}:{exp}"
    expected_sig = hmac.new(derived_key, message.encode("utf-8"), hashlib.sha256).hexdigest()
    return hmac.compare_digest(sig, expected_sig)


def sanitize_usercontent_html(raw_html: str) -> str:
    """Sanitize externally generated HTML using nh3, removing all user scripts and event handlers."""
    if not raw_html:
        return ""
    custom_attrs = dict(nh3.ALLOWED_ATTRIBUTES)
    custom_attrs["*"] = {"class", "id", "title", "aria-label", "aria-hidden", "role"}
    return nh3.clean(raw_html, attributes=custom_attrs)


class _CookielessMiddleware:
    """ASGI middleware to strip Cookie headers and prevent Set-Cookie on usercontent host."""

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


def create_usercontent_app(
    settings: Any,
    *,
    theme_manager: Any | None = None,
    secret_service: SecretService | None = None,
    roles: Any | None = None,
    now: Callable[[], float] | None = None,
) -> ASGIApp:
    """Create Starlette ASGI application for the usercontent host (설계 §5.1–§5.2)."""
    db_path_val = getattr(settings, "db_file", None) or getattr(settings, "CLAIRE_DB_PATH", "data/claire.db")
    db_path = Path(db_path_val) if db_path_val else None

    # Derive role hosts if not passed
    scheme = getattr(roles, "scheme", "http") if roles else "http"
    app_host = getattr(roles, "app", "127.0.0.1:8766") if roles else "127.0.0.1:8766"
    usercontent_host = getattr(roles, "usercontent", None) or getattr(roles, "app", "127.0.0.1:8767")
    app_origin = f"{scheme}://{app_host}"
    usercontent_origin = f"{scheme}://{usercontent_host}"

    def _resolve_document_and_theme(doc_id_val: int | str) -> tuple[dict[str, Any], dict[str, Any]] | None:
        if not db_path or not db_path.exists():
            return None
        conn = dbm.connect_existing(db_path, readonly=True)
        try:
            conn.row_factory = sqlite3.Row
            row = conn.execute("SELECT * FROM documents WHERE id = ?", (str(doc_id_val),)).fetchone()
            if row is None and isinstance(doc_id_val, int):
                row = conn.execute("SELECT * FROM documents WHERE id = ?", (doc_id_val,)).fetchone()
            if row is None:
                return None
            doc_dict = dict(row)
            theme_id = doc_dict.get("theme_id", 0)
            theme_row = conn.execute("SELECT * FROM themes WHERE id = ?", (theme_id,)).fetchone()
            if theme_row is not None:
                theme_dict = dict(theme_row)
            else:
                theme_dict = {"id": theme_id, "account_id": 1, "visibility_epoch": 1, "is_public": 1}
            return doc_dict, theme_dict
        finally:
            conn.close()

    async def static_asset_route(request: Any) -> Response:
        path = request.path_params.get("path", "").lstrip("/")
        if any(path.startswith(d) for d in DISALLOWED_STATIC_PREFIXES) or path == "js" or "/js/" in path:
            return PlainTextResponse("Not Found", status_code=404)
        if not any(path.startswith(prefix) for prefix in ALLOWED_STATIC_PREFIXES):
            return PlainTextResponse("Not Found", status_code=404)
        file_path = (STATIC_DIR / path).resolve()
        if not file_path.is_relative_to(STATIC_DIR) or not file_path.is_file():
            return PlainTextResponse("Not Found", status_code=404)

        return FileResponse(
            file_path,
            headers={
                "access-control-allow-origin": "*",
                "access-control-allow-methods": "GET, HEAD, OPTIONS",
                "x-content-type-options": "nosniff",
            },
        )

    async def render_doc_route(request: Any) -> Response:
        doc_id_raw = request.path_params.get("id")
        try:
            doc_id = int(doc_id_raw)
        except (ValueError, TypeError):
            return PlainTextResponse("Bad Request", status_code=400)

        resolved = _resolve_document_and_theme(doc_id)
        if resolved is None:
            return PlainTextResponse("Document not found", status_code=404)

        doc_dict, theme_dict = resolved
        account_id = theme_dict.get("account_id") or 1
        theme_id = theme_dict.get("id") or 0
        visibility_epoch = theme_dict.get("visibility_epoch") or 1
        is_theme_public = bool(theme_dict.get("is_public", 1))
        is_hidden = bool(doc_dict.get("hidden", 0))
        is_private = (not is_theme_public) or is_hidden

        if is_private:
            token = request.query_params.get("token") or request.query_params.get("s")
            if not token and "sig" in request.query_params and "exp" in request.query_params:
                token = f"{request.query_params['exp']}.{request.query_params['sig']}"
            if not token:
                return PlainTextResponse("Forbidden: viewing token required for private content", status_code=403)
            cur_now = now() if now is not None else None
            is_valid = verify_viewing_token(
                token=token,
                account_id=account_id,
                theme_id=theme_id,
                doc_id=doc_id,
                visibility_epoch=visibility_epoch,
                secret_service=secret_service,
                now=cur_now,
            )
            if not is_valid:
                return PlainTextResponse("Forbidden: invalid or expired viewing token", status_code=403)

        raw_content = doc_dict.get("detail_html") or doc_dict.get("detail") or doc_dict.get("raw_text") or ""
        if not raw_content:
            raw_content = f"<h1>{html.escape(doc_dict.get('title') or 'Document')}</h1>"

        cleaned_body = sanitize_usercontent_html(raw_content)
        title_str = html.escape(doc_dict.get("title") or "Document")
        doc_html = f"""<!DOCTYPE html>
<html lang="ko">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{title_str}</title>
<link rel="stylesheet" href="/static/css/reader.css">
</head>
<body class="cb-static-render">
<article class="cb-document-content">
{cleaned_body}
</article>
</body>
</html>"""

        csp = (
            "default-src 'none'; "
            "style-src 'self' 'unsafe-inline' https://fonts.googleapis.com; "
            "font-src 'self' data: https://fonts.gstatic.com; "
            "img-src 'self' data: https:; "
            f"frame-ancestors {app_origin}; "
            "sandbox allow-popups allow-popups-to-escape-sandbox"
        )
        headers = {
            "content-security-policy": csp,
            "x-content-type-options": "nosniff",
            "cache-control": "private, no-store",
            "access-control-allow-origin": "*",
        }
        return HTMLResponse(doc_html, headers=headers)

    async def presentation_route(request: Any) -> Response:
        doc_id_param = request.query_params.get("id", "").strip()
        doc_dict: dict[str, Any] | None = None
        theme_dict: dict[str, Any] | None = None
        doc_id: int | None = None

        if doc_id_param:
            try:
                doc_id = int(doc_id_param)
            except ValueError:
                doc_id = None
            if doc_id is not None:
                resolved = _resolve_document_and_theme(doc_id)
                if resolved is not None:
                    doc_dict, theme_dict = resolved

        if doc_dict is not None and theme_dict is not None and doc_id is not None:
            account_id = theme_dict.get("account_id") or 1
            theme_id = theme_dict.get("id") or 0
            visibility_epoch = theme_dict.get("visibility_epoch") or 1
            is_theme_public = bool(theme_dict.get("is_public", 1))
            is_hidden = bool(doc_dict.get("hidden", 0))
            is_private = (not is_theme_public) or is_hidden

            if is_private:
                token = request.query_params.get("token") or request.query_params.get("s")
                if not token and "sig" in request.query_params and "exp" in request.query_params:
                    token = f"{request.query_params['exp']}.{request.query_params['sig']}"
                if not token:
                    return PlainTextResponse("Forbidden: viewing token required for private presentation", status_code=403)
                cur_now = now() if now is not None else None
                is_valid = verify_viewing_token(
                    token=token,
                    account_id=account_id,
                    theme_id=theme_id,
                    doc_id=doc_id,
                    visibility_epoch=visibility_epoch,
                    secret_service=secret_service,
                    now=cur_now,
                )
                if not is_valid:
                    return PlainTextResponse("Forbidden: invalid or expired viewing token", status_code=403)

        nonce = secrets.token_urlsafe(16)
        slide_theme = request.query_params.get("slide_theme") or request.query_params.get("presentation_theme") or "night"

        # Check compiled presentation on disk
        data_dir_path = Path(getattr(settings, "data_dir", "data"))
        pres_file = data_dir_path / "presentations" / f"{doc_id_param}.html" if doc_id_param else None

        if pres_file and pres_file.is_file():
            content = pres_file.read_text(encoding="utf-8")
            deck_html = re.sub(r"<script(?![^>]*\bnonce=)", f'<script nonce="{nonce}"', content)
        else:
            title = html.escape(doc_dict.get("title") if doc_dict else "Presentation")
            deck_html = f"""<!DOCTYPE html>
<html lang="ko">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>{title}</title>
<link rel="stylesheet" href="/static/vendor/reveal.js/dist/reset.css">
<link rel="stylesheet" href="/static/vendor/reveal.js/dist/reveal.css">
<link rel="stylesheet" href="/static/vendor/reveal.js/dist/theme/{html.escape(slide_theme)}.css">
</head>
<body>
<div class="reveal">
  <div class="slides">
    <section>
      <h2>{title}</h2>
      <p>Presentation deck</p>
    </section>
  </div>
</div>
<script src="/static/vendor/reveal.js/dist/reveal.js"></script>
<script nonce="{nonce}">
Reveal.initialize({{ hash: true, slideNumber: true }});
</script>
</body>
</html>"""

        csp = (
            "default-src 'self'; "
            f"script-src {usercontent_origin} 'nonce-{nonce}'; "
            "style-src 'self' 'unsafe-inline' https://fonts.googleapis.com; "
            "font-src 'self' data: https://fonts.gstatic.com; "
            "img-src 'self' data: https:; "
            f"frame-ancestors {app_origin};"
        )
        headers = {
            "content-security-policy": csp,
            "x-content-type-options": "nosniff",
            "cache-control": "no-cache",
            "access-control-allow-origin": "*",
        }
        return HTMLResponse(deck_html, headers=headers)

    app = Starlette(
        routes=[
            Route("/static/{path:path}", static_asset_route, methods=["GET", "HEAD"]),
            Route("/render/doc/{id:int}", render_doc_route, methods=["GET", "HEAD"]),
            Route("/render/doc/{id}", render_doc_route, methods=["GET", "HEAD"]),
            Route("/p/presentation", presentation_route, methods=["GET", "HEAD"]),
        ],
    )
    return _CookielessMiddleware(app)
