"""Render-stage media URL transformation.

Design references:
- docs/design/MULTI_ACCOUNT_NAMESPACE_AND_SINGLE_FQDN_DESIGN.md §5.4, §16.4 (Rev. 2.3)
- Media references are never stored as absolute URLs in detail_html or presentation decks.
- Detail HTML is precompiled; media references are transformed dynamically at render time
  via media_url(ref, viewer).
- Rewrites both relative images/... and legacy /images/... or /image?p=... to media host
  (or signed capability URLs for private content).
"""

from __future__ import annotations

import re
from typing import Any
from urllib.parse import parse_qs, urlparse

from ..store.media import create_media_capability_token
from ..store.secrets import SecretService

__all__ = [
    "extract_media_key",
    "media_url",
    "transform_media_urls_in_html",
]

_IMG_SRC_RE = re.compile(
    r"""(?P<tag><(?:img|video|audio|source)\b[^>]*?\bsrc=["'])(?P<url>[^"']+)(?P<end>["'])""",
    re.IGNORECASE,
)
_BG_IMAGE_RE = re.compile(
    r"""(?P<prefix>\bdata-background-image=["'])(?P<url>[^"']+)(?P<end>["'])""",
    re.IGNORECASE,
)
_CSS_URL_RE = re.compile(
    r"""(?P<prefix>\burl\(\s*["']?)(?P<url>[^"')\s]+)(?P<end>["']?\s*\))""",
    re.IGNORECASE,
)


def extract_media_key(ref: str) -> str | None:
    """Extract clean relative media key from various URL representations.

    Returns the clean key (e.g. 'doc1_0.png') or None if not an internal media reference.
    """
    if not ref or ref.startswith("data:") or "://" in ref:
        return None
    parsed = urlparse(ref)
    path = parsed.path.lstrip("/")
    # Handle /image?p=images/foo.png
    if path == "image" and parsed.query:
        qs = parse_qs(parsed.query)
        p_vals = qs.get("p", [])
        if p_vals:
            path = p_vals[0].lstrip("/")

    if path.startswith("p/images/"):
        path = path[len("p/images/") :]
    elif path.startswith("images/"):
        path = path[len("images/") :]
    elif path.startswith("media/"):
        path = path[len("media/") :]

    # Reject path traversal
    if ".." in path.split("/"):
        return None
    return path if path else None


def media_url(
    ref: str,
    viewer: Any | None = None,
    *,
    is_public: bool = True,
    account_id: int = 1,
    theme_id: int = 0,
    visibility_epoch: int = 1,
    roles: Any | None = None,
    secret_service: SecretService | None = None,
    expires_in: int = 3600,
) -> str:
    """Transform media reference to target media URL (설계 §5.4).

    - If media host is configured (roles.media or CLAIRE_MEDIA_FQDN), returns URL on media host.
    - Otherwise returns app host legacy path (/image?p=images/...).
    - For private media (is_public=False), attaches 1-hour capability viewing token.
    """
    clean_key = extract_media_key(ref)
    if clean_key is None:
        return ref

    # Check if media role is configured
    media_host = getattr(roles, "media", None) if roles else None
    scheme = getattr(roles, "scheme", "http") if roles else "http"

    if media_host:
        base = f"{scheme}://{media_host}/media/{clean_key}"
    else:
        base = f"/image?p=images/{clean_key}"

    if not is_public:
        token = create_media_capability_token(
            account_id=account_id,
            theme_id=theme_id,
            visibility_epoch=visibility_epoch,
            key=clean_key,
            expires_in=expires_in,
            secret_service=secret_service,
        )
        sep = "&" if "?" in base else "?"
        return f"{base}{sep}account_id={account_id}&theme_id={theme_id}&visibility_epoch={visibility_epoch}&token={token}"

    return base


def transform_media_urls_in_html(
    html_content: str,
    viewer: Any | None = None,
    *,
    is_public: bool = True,
    account_id: int = 1,
    theme_id: int = 0,
    visibility_epoch: int = 1,
    roles: Any | None = None,
    secret_service: SecretService | None = None,
    expires_in: int = 3600,
) -> str:
    """Rewrite media references in HTML at render time (설계 §5.4)."""
    if not html_content:
        return ""

    def _replace_url(u: str) -> str:
        key = extract_media_key(u)
        if key is None:
            return u
        return media_url(
            key,
            viewer,
            is_public=is_public,
            account_id=account_id,
            theme_id=theme_id,
            visibility_epoch=visibility_epoch,
            roles=roles,
            secret_service=secret_service,
            expires_in=expires_in,
        )

    def _img_sub(match: re.Match[str]) -> str:
        tag = match.group("tag")
        u = match.group("url")
        end = match.group("end")
        return f"{tag}{_replace_url(u)}{end}"

    def _bg_sub(match: re.Match[str]) -> str:
        prefix = match.group("prefix")
        u = match.group("url")
        end = match.group("end")
        return f"{prefix}{_replace_url(u)}{end}"

    def _css_sub(match: re.Match[str]) -> str:
        prefix = match.group("prefix")
        u = match.group("url")
        end = match.group("end")
        return f"{prefix}{_replace_url(u)}{end}"

    out = _IMG_SRC_RE.sub(_img_sub, html_content)
    out = _BG_IMAGE_RE.sub(_bg_sub, out)
    out = _CSS_URL_RE.sub(_css_sub, out)
    return out
