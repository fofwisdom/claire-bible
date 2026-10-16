"""Favicon and web application metadata management for Claire Bible.

This module provides standard favicon tags and configuration for HTML documents.
"""

from __future__ import annotations

import re
from typing import Any

# Authoritative paths for favicon and web application assets.
FAVICON_SVG_PATH: str = "/favicon.svg"
FAVICON_ICO_PATH: str = "/favicon.ico"
ICON_PNG_192_PATH: str = "/icon?p=android-chrome-192x192.png"
ICON_PNG_512_PATH: str = "/icon?p=android-chrome-512x512.png"
APPLE_TOUCH_ICON_PATH: str = "/apple-touch-icon.png"
MANIFEST_PATH: str = "/manifest.json"
MASK_ICON_COLOR: str = "#00ffaa"
THEME_COLOR: str = "#0e1116"
APPLICATION_NAME: str = "Claire Bible"

FAVICON_PLACEHOLDER: str = "<!-- __FAVICON_TAGS__ -->"


def render_favicon_head_tags(*, base_url: str = "") -> str:
    """Render the standard favicon and application metadata HTML tags."""
    prefix = base_url.rstrip("/")
    return (
        f'<meta name="mobile-web-app-capable" content="yes"/>\n'
        f'<meta name="apple-mobile-web-app-capable" content="yes"/>\n'
        f'<meta name="apple-mobile-web-app-status-bar-style" content="black-translucent"/>\n'
        f'<meta name="apple-mobile-web-app-title" content="{APPLICATION_NAME}"/>\n'
        f'<meta name="application-name" content="{APPLICATION_NAME}"/>\n'
        f'<link rel="icon" type="image/svg+xml" href="{prefix}{FAVICON_SVG_PATH}"/>\n'
        f'<link rel="icon" type="image/png" sizes="192x192" href="{prefix}{ICON_PNG_192_PATH}"/>\n'
        f'<link rel="icon" type="image/png" sizes="512x512" href="{prefix}{ICON_PNG_512_PATH}"/>\n'
        f'<link rel="alternate icon" href="{prefix}{FAVICON_ICO_PATH}"/>\n'
        f'<link rel="apple-touch-icon" sizes="180x180" href="{prefix}{APPLE_TOUCH_ICON_PATH}"/>\n'
        f'<link rel="manifest" href="{prefix}{MANIFEST_PATH}"/>\n'
        f'<link rel="mask-icon" href="{prefix}{FAVICON_SVG_PATH}" color="{MASK_ICON_COLOR}"/>\n'
        f'<meta name="theme-color" content="{THEME_COLOR}"/>'
    )


FAVICON_TAGS_HTML: str = render_favicon_head_tags()


def inject_favicon_head_tags(html_content: str, *, base_url: str = "") -> str:
    """Inject standard favicon tags into an HTML string.

    If the placeholder exists, replace it.
    If no placeholder exists, insert the tags before the closing head tag.
    """
    tags = render_favicon_head_tags(base_url=base_url)
    if FAVICON_PLACEHOLDER in html_content:
        return html_content.replace(FAVICON_PLACEHOLDER, tags)

    match = re.search(r"</head>", html_content, re.IGNORECASE)
    if match:
        idx = match.start()
        return html_content[:idx] + tags + "\n" + html_content[idx:]

    return tags + "\n" + html_content


def get_scalar_favicon_config(*, base_url: str = "") -> dict[str, Any]:
    """Get the favicon configuration dictionary for the Scalar documentation tool."""
    prefix = base_url.rstrip("/")
    return {
        "favicon": f"{prefix}{FAVICON_SVG_PATH}",
    }


__all__ = [
    "APPLICATION_NAME",
    "APPLE_TOUCH_ICON_PATH",
    "FAVICON_ICO_PATH",
    "FAVICON_PLACEHOLDER",
    "FAVICON_SVG_PATH",
    "FAVICON_TAGS_HTML",
    "ICON_PNG_192_PATH",
    "ICON_PNG_512_PATH",
    "MANIFEST_PATH",
    "MASK_ICON_COLOR",
    "THEME_COLOR",
    "get_scalar_favicon_config",
    "inject_favicon_head_tags",
    "render_favicon_head_tags",
]
