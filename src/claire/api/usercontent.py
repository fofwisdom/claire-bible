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
import json
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

from ..render.media_transform import transform_media_urls_in_html
from ..store import db as dbm
from ..store.secrets import SecretService, get_secret_service

__all__ = [
    "DEFAULT_VIEWING_TOKEN_TTL",
    "create_usercontent_app",
    "create_remote_token",
    "create_viewing_token",
    "remote_item_response",
    "remote_item_text",
    "sanitize_remote_html",
    "sanitize_usercontent_html",
    "verify_remote_token",
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


# --- Remote items of the federation client (FEDERATION_REMOTE_READING_DESIGN.md Section 8, unit FP4-5) ---

# Elements that load a resource. The media proxy (Section 9, unit FP4-7) is the only way for remote media; until it exists the render has no media element.
_REMOTE_RESOURCE_TAGS = frozenset({"img", "picture", "source", "audio", "video", "track", "map", "area"})


def _remote_token_message(account_id: int, item_id: int, exp: int) -> bytes:
    return f"remote:{int(account_id)}:{int(item_id)}:{exp}".encode("utf-8")


def create_remote_token(account_id: int, item_id: int, expires_in: int = DEFAULT_VIEWING_TOKEN_TTL, *, secret_service: SecretService | None = None, now: float | None = None) -> str:
    """Make a viewing token of the kind ``remote``: it binds ``(account_id, item_id, exp)`` of one timeline item (Section 8 rule 2). A token of a document is not valid for an item, and the reverse."""
    exp = int(now if now is not None else time.time()) + int(expires_in)
    key = (secret_service or get_secret_service()).derive_hmac_key("viewing_token")
    return f"{exp}.{hmac.new(key, _remote_token_message(account_id, item_id, exp), hashlib.sha256).hexdigest()}"


def verify_remote_token(token: str, account_id: int, item_id: int, *, secret_service: SecretService | None = None, now: float | None = None) -> bool:
    if not isinstance(token, str) or token.count(".") != 1:
        return False
    exp_text, signature = token.split(".")
    if not (exp_text.isascii() and exp_text.isdigit() and len(exp_text) <= 12):
        return False
    exp = int(exp_text)
    if int(now if now is not None else time.time()) > exp:
        return False
    key = (secret_service or get_secret_service()).derive_hmac_key("viewing_token")
    return hmac.compare_digest(signature, hmac.new(key, _remote_token_message(account_id, item_id, exp), hashlib.sha256).hexdigest())


def sanitize_remote_html(raw_html: str) -> str:
    """Sanitize the HTML of a remote server (Section 8 rule 3): the allowlist of ``sanitize_usercontent_html`` without the elements that load a resource. Each link opens a new tab and gives the remote site no referrer."""
    if not raw_html:
        return ""
    attributes = {tag: set(names) for tag, names in nh3.ALLOWED_ATTRIBUTES.items() if tag not in _REMOTE_RESOURCE_TAGS}
    # ``class`` only: an ``id`` or a ``style`` of a remote server has no use in the frame.
    attributes["*"] = {"class", "title", "lang", "dir"}
    return nh3.clean(
        raw_html,
        tags=set(nh3.ALLOWED_TAGS) - _REMOTE_RESOURCE_TAGS,
        attributes=attributes,
        url_schemes={"http", "https", "mailto"},
        link_rel="nofollow noopener noreferrer",
        set_tag_attribute_values={"a": {"target": "_blank"}},
    )


def remote_item_text(raw_html: str, limit: int = 600) -> str:
    """The plain text of remote HTML for the timeline list (Section 8 rule 1): the list has no remote HTML."""
    text = nh3.clean(re.sub(r"<(?:br\s*/?|/p|/div|/li|/h[1-6]|/blockquote)>", " ", raw_html or "", flags=re.IGNORECASE), tags=set())
    text = re.sub(r"\s+", " ", html.unescape(text)).strip()
    return text if len(text) <= limit else text[: limit - 1].rstrip() + "…"


def _attachment_list(attachments: list[dict[str, Any]]) -> str:
    """The attachments as links to the remote server. The viewer contacts the remote server only by a click (RS3)."""
    rows = []
    for attachment in attachments:
        url = str(attachment.get("url") or "")
        if not url.startswith(("https://", "http://")):
            continue
        label = str(attachment.get("name") or attachment.get("media_type") or url)
        kind = str(attachment.get("media_type") or "")
        rows.append(f'<li><a href="{html.escape(url, quote=True)}" target="_blank" rel="nofollow noopener noreferrer">{html.escape(label)}</a>{" <small>" + html.escape(kind) + "</small>" if kind and kind != label else ""}</li>')
    return f'<ul class="cb-remote-attachments">{"".join(rows)}</ul>' if rows else ""


# One fixed light surface: the frame has an opaque origin and cannot read the theme of the page around it.
_REMOTE_STYLE = (
    "body{margin:0;padding:12px 14px;font:15px/1.65 system-ui,-apple-system,'Segoe UI',sans-serif;color:#1f2328;background:#fff;overflow-wrap:anywhere}"
    "a{color:#0969da}blockquote{margin:8px 0;padding-left:12px;border-left:3px solid #d0d7de;color:#57606a}pre{overflow:auto;padding:8px;background:#f6f8fa;border-radius:6px}"
    ".cb-remote-attachments{margin:12px 0 0;padding:8px 0 0 18px;border-top:1px solid #d0d7de;font-size:13px}.cb-remote-attachments small{color:#57606a}"
)


def remote_item_response(*, content_html: str, attachments: list[dict[str, Any]], language: str | None, frame_ancestors: str, extra_headers: dict[str, str] | None = None) -> Response:
    """The body of one timeline item as a page for a sandbox ``iframe`` (Section 8 rules 2 and 3).

    The policy permits no script, no remote resource, and no form. ``sandbox`` gives the page an opaque origin; a link opens a new tab. ``frame_ancestors`` is the app origin, or ``'self'`` for the fallback on the app origin (rule 5).
    """
    lang = html.escape(language or "", quote=True)
    page = (
        f'<!DOCTYPE html>\n<html lang="{lang}">\n<head>\n<meta charset="utf-8">\n<meta name="viewport" content="width=device-width, initial-scale=1">\n<meta name="referrer" content="no-referrer">\n'
        f"<title>Remote post</title>\n<style>{_REMOTE_STYLE}</style>\n</head>\n<body>\n<article>{sanitize_remote_html(content_html)}</article>\n{_attachment_list(attachments)}\n</body>\n</html>"
    )
    csp = f"default-src 'none'; style-src 'unsafe-inline'; base-uri 'none'; form-action 'none'; frame-ancestors {frame_ancestors}; sandbox allow-popups allow-popups-to-escape-sandbox"
    return HTMLResponse(page, headers={"content-security-policy": csp, "x-content-type-options": "nosniff", "referrer-policy": "no-referrer", "cache-control": "private, no-store", **(extra_headers or {})})


def load_remote_item(db_path: Path | None, item_id: int) -> dict[str, Any] | None:
    """Read the columns that the render needs from ``client.db`` next to ``claire.db``, or ``None``."""
    from ..store import client_db

    if db_path is None:
        return None
    client_path = client_db.client_db_path(db_path)
    if not client_path.exists():
        return None
    conn = client_db.connect_client_existing(client_path, readonly=True)
    try:
        row = conn.execute("SELECT account_id, content_html, attachments_json, language FROM federation_timeline_items WHERE id=?", (item_id,)).fetchone()
    finally:
        conn.close()
    if row is None:
        return None
    return {"account_id": int(row[0]), "content_html": str(row[1]), "attachments": json.loads(row[2]), "language": row[3]}


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

        raw_content = transform_media_urls_in_html(
            raw_content,
            is_public=not is_private,
            account_id=account_id,
            theme_id=theme_id,
            visibility_epoch=visibility_epoch,
            roles=roles,
            secret_service=secret_service,
        )
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

        media_host = getattr(roles, "media", None) if roles else None
        media_part = f" {scheme}://{media_host}" if media_host else ""
        csp = (
            "default-src 'none'; "
            "style-src 'self' 'unsafe-inline' https://fonts.googleapis.com; "
            "font-src 'self' data: https://fonts.gstatic.com; "
            f"img-src 'self' data: https:{media_part}; "
            f"media-src 'self' data: https:{media_part}; "
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

    async def render_remote_route(request: Any) -> Response:
        """``GET /render/remote/{item_id}?t={token}`` (FEDERATION_REMOTE_READING_DESIGN.md Section 8 rule 2). A token that is absent, expired, or of another item or account gets the same 404 as an item that does not exist."""
        item_id = int(request.path_params["id"])
        item = load_remote_item(db_path, item_id)
        token = request.query_params.get("t", "")
        if item is None or not verify_remote_token(token, item["account_id"], item_id, secret_service=secret_service, now=now() if now is not None else None):
            return PlainTextResponse("Not Found", status_code=404)
        return remote_item_response(content_html=item["content_html"], attachments=item["attachments"], language=item["language"], frame_ancestors=app_origin)

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
            content = transform_media_urls_in_html(
                content,
                is_public=not is_private,
                account_id=account_id if doc_dict else 1,
                theme_id=theme_id if doc_dict else 0,
                visibility_epoch=visibility_epoch if doc_dict else 1,
                roles=roles,
                secret_service=secret_service,
            )
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

        media_host = getattr(roles, "media", None) if roles else None
        media_part = f" {scheme}://{media_host}" if media_host else ""
        csp = (
            "default-src 'self'; "
            f"script-src {usercontent_origin} 'nonce-{nonce}'; "
            "style-src 'self' 'unsafe-inline' https://fonts.googleapis.com; "
            "font-src 'self' data: https://fonts.gstatic.com; "
            f"img-src 'self' data: https:{media_part}; "
            f"media-src 'self' data: https:{media_part}; "
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
            Route("/render/remote/{id:int}", render_remote_route, methods=["GET", "HEAD"]),
            Route("/p/presentation", presentation_route, methods=["GET", "HEAD"]),
        ],
    )
    return _CookielessMiddleware(app)
