"""Pure web metadata, favicon tags, UI chrome, and page bundle builders."""

from __future__ import annotations

from collections.abc import Callable
from datetime import datetime, timezone
import html as _html
import json as _json
import re
from typing import Any

__all__ = [
    "extract_knowledge_node_tags",
    "render_ga_tag",
    "render_open_graph_tags",
    "resolve_preview_image",
    "sanitize_preview_text",
]


def render_ga_tag(measurement_id: str, doc_id: str = "", kb_id: int | None = None, theme_label: str = "") -> str:
    """Build a Google Analytics 4 (GA4) snippet, or return empty text when invalid."""
    cleaned_id = str(measurement_id or "").strip()
    if not cleaned_id or not re.fullmatch(r"^[A-Za-z0-9_-]+$", cleaned_id):
        return ""
    clean_doc_id = str(doc_id or "").strip()
    if clean_doc_id:
        loc_expr = f"window.location.origin + '/p/{clean_doc_id}'"
    else:
        loc_expr = "window.location.origin + window.location.pathname"

    extra_params = ""
    if kb_id is not None:
        safe_label = _html.escape(theme_label, quote=True)
        extra_params = f',\n    kb_id: {kb_id},\n    theme_label: "{safe_label}"'

    return (
        "<!-- Google Analytics (GA4) -->\n"
        f'<script async src="https://www.googletagmanager.com/gtag/js?id={cleaned_id}"></script>\n'
        "<script>\n"
        "  window.dataLayer = window.dataLayer || [];\n"
        "  function gtag(){dataLayer.push(arguments);}\n"
        "  gtag('js', new Date());\n"
        f'  gtag("config", "{cleaned_id}", {{\n'
        f'    page_location: {loc_expr},\n'
        f'    cookie_domain: window.location.hostname,\n'
        f'    cookie_flags: "SameSite=Lax;Secure"{extra_params}\n'
        f'  }});\n'
        "</script>"
    )


def sanitize_preview_text(text: str | None, max_chars: int = 220) -> str:
    """Remove Markdown, AsciiDoc, LaTeX, and HTML marks and truncate to one line."""
    if not text:
        return ""
    s = _html.unescape(str(text))

    # 1. Remove fenced code blocks and delimited block markers
    s = re.sub(r"```[\s\S]*?```", " ", s)
    s = re.sub(r"(\+\+\+\+|----|\.\.\.\.|====|\|===)[\s\S]*?\1", " ", s)
    s = re.sub(r"^\s*\[(?:latexmath|stem|asciimath|source[^\]]*)\]\s*$", " ", s, flags=re.MULTILINE)

    # 2. Strip math delimiters
    s = re.sub(r"(?:stem|latexmath|asciimath):\[(.*?)\]", r"\1", s)
    s = re.sub(r"\$\$([\s\S]*?)\$\$", r"\1", s)
    s = re.sub(r"\\\[([\s\S]*?)\\\]", r"\1", s)
    s = re.sub(r"\$([^\$\n]+)\$", r"\1", s)
    s = re.sub(r"\\\((.*?)\\\)", r"\1", s)

    # 3. Keep alt text of links and images
    s = re.sub(r"!\[([^\]]*)\]\([^\)]+\)", r"\1", s)
    s = re.sub(r"\[([^\]]+)\]\([^\)]+\)", r"\1", s)
    s = re.sub(r"image::?[^\[]*\[(.*?)\]", r"\1", s)
    s = re.sub(r"(?:https?://\S+|link:\S+)\[(.*?)\]", r"\1", s)

    # 4. Remove headings, quotes, and admonition labels
    s = re.sub(r"^\s*#{1,6}\s+", " ", s, flags=re.MULTILINE)
    s = re.sub(r"^\s*={1,6}\s+", " ", s, flags=re.MULTILINE)
    s = re.sub(r"^\s*>\s+", " ", s, flags=re.MULTILINE)
    s = re.sub(r"\b(?:NOTE|TIP|IMPORTANT|WARNING|CAUTION):\s*", " ", s)

    # 5. Remove matched inline formatting marks
    s = re.sub(r"\*\*([^*]+)\*\*", r"\1", s)
    s = re.sub(r"\*([^*]+)\*", r"\1", s)
    s = re.sub(r"__([^_]+)__", r"\1", s)
    s = re.sub(r"(?<!\w)_([^_]+)_(?!\w)", r"\1", s)
    s = re.sub(r"~~([^~]+)~~", r"\1", s)
    s = re.sub(r"`([^`]+)`", r"\1", s)

    # 6. Strip HTML tags and script/style blocks
    s = re.sub(r"<script[\s\S]*?</script>", " ", s, flags=re.IGNORECASE)
    s = re.sub(r"<style[\s\S]*?</style>", " ", s, flags=re.IGNORECASE)
    s = re.sub(r"<[a-zA-Z/][^>]*>", " ", s)

    # 7. Collapse whitespace
    s = re.sub(r"\s+", " ", s).strip()

    # 8. Truncate at word boundary
    if len(s) > max_chars:
        cut = s[:max_chars]
        last_space = cut.rfind(" ")
        if last_space > int(max_chars * 0.75):
            cut = cut[:last_space]
        s = cut.rstrip(" .,!?;:") + "…"

    return s


def resolve_preview_image(doc: dict, base_url: str = "") -> tuple[str, str]:
    """Return the preview image URL and twitter:card type."""
    base = base_url.rstrip("/") if base_url else ""
    meta = (doc or {}).get("meta") or {}

    candidates: list[str] = []

    for k in ("og_image", "image", "thumbnail"):
        v = (doc or {}).get(k) or meta.get(k)
        if v and isinstance(v, str) and v.strip():
            candidates.append(v.strip())

    images = meta.get("images") or []
    if isinstance(images, list):
        for im in images:
            if isinstance(im, dict):
                loc = str(im.get("local") or "").strip()
                if loc:
                    rel = loc.lstrip("/")
                    candidates.append(f"/image?p={rel}")
                u = str(im.get("url") or "").strip()
                if u:
                    candidates.append(u)

    detail = str((doc or {}).get("detail") or "")
    if detail:
        m_md = re.search(r"!\[.*?\]\((https?://[^\s\)]+)\)", detail)
        if m_md:
            candidates.append(m_md.group(1).strip())
        m_adoc = re.search(r"image::?(https?://[^\s\[]+)\[", detail)
        if m_adoc:
            candidates.append(m_adoc.group(1).strip())

    for c in candidates:
        if c.startswith(("http://", "https://")):
            return c, "summary_large_image"
        if c.startswith("/"):
            return f"{base}{c}" if base else c, "summary_large_image"

    icon_path = "/icon?p=android-chrome-512x512.png"
    return f"{base}{icon_path}" if base else icon_path, "summary"


def extract_knowledge_node_tags(doc: dict) -> list[str]:
    """Extract unique tag names from document knowledge nodes."""
    nodes = (doc or {}).get("nodes") or []
    tags: list[str] = []
    seen: set[str] = set()

    for n in nodes:
        if isinstance(n, dict):
            name = str(n.get("label") or n.get("name") or "").strip()
        elif isinstance(n, str):
            name = n.strip()
        else:
            continue
        if name and name not in seen:
            seen.add(name)
            tags.append(name)
    return tags


def render_open_graph_tags(doc: dict, base_url: str = "", share_token: str = "", settings: Any = None, *, is_media: bool = False, is_media_source: Callable[[str], bool] | None = None) -> str:
    """Build Open Graph, Twitter Cards, and Schema.org JSON-LD metadata tags."""
    base = base_url.rstrip("/") if base_url else ""

    raw_title = str((doc or {}).get("title") or "공유 문서").strip()
    title = sanitize_preview_text(raw_title, max_chars=100) or "공유 문서"

    raw_desc = (doc or {}).get("summary") or (doc or {}).get("detail") or ""
    desc = sanitize_preview_text(raw_desc, max_chars=220)
    if not desc:
        desc = "Claire Bible에서 공유된 지식 문서입니다."

    image_url, card_type = resolve_preview_image(doc, base_url=base)

    author = str((doc or {}).get("author") or "").strip()
    src_type = str((doc or {}).get("source_type") or "").strip().lower()
    media_source = is_media or (is_media_source(src_type) if is_media_source is not None else False)

    if media_source or author.lower() in ("orbrium", "youtube", "채널", "channel"):
        author = ""
    published_at = str((doc or {}).get("published_at") or "").strip()

    if share_token:
        page_url = f"{base}/silvercord/{share_token}" if base else f"/silvercord/{share_token}"
    else:
        page_url = f"{base}/p" if base else "/p"

    esc_title = _html.escape(title, quote=True)
    esc_desc = _html.escape(desc, quote=True)
    esc_image = _html.escape(image_url, quote=True)
    esc_url = _html.escape(page_url, quote=True)
    esc_author = _html.escape(author, quote=True) if author else ""
    esc_pub = _html.escape(published_at, quote=True) if published_at else ""

    tags = [
        "<!-- Open Graph / Web Preview (Telegram, Mastodon, KakaoTalk, etc.) -->",
        f'<meta name="description" content="{esc_desc}"/>',
        '<meta property="og:site_name" content="Claire Bible"/>',
        '<meta property="og:type" content="article"/>',
        f'<meta property="og:title" content="{esc_title}"/>',
        f'<meta property="og:description" content="{esc_desc}"/>',
        f'<meta property="og:url" content="{esc_url}"/>',
        f'<meta property="og:image" content="{esc_image}"/>',
        f'<meta property="og:image:alt" content="{esc_title}"/>',
    ]

    if esc_author:
        tags.append(f'<meta property="article:author" content="{esc_author}"/>')
        tags.append(f'<meta name="author" content="{esc_author}"/>')
    if esc_pub:
        tags.append(f'<meta property="article:published_time" content="{esc_pub}"/>')

    node_tags = extract_knowledge_node_tags(doc)
    if node_tags:
        esc_keywords = _html.escape(", ".join(node_tags), quote=True)
        tags.append(f'<meta name="keywords" content="{esc_keywords}"/>')
        for tag_name in node_tags:
            esc_t = _html.escape(tag_name, quote=True)
            tags.append(f'<meta property="article:tag" content="{esc_t}"/>')

    tags.extend([
        "<!-- Twitter Cards (X, Slack, Discord, etc.) -->",
        f'<meta name="twitter:card" content="{card_type}"/>',
        f'<meta name="twitter:title" content="{esc_title}"/>',
        f'<meta name="twitter:description" content="{esc_desc}"/>',
        f'<meta name="twitter:image" content="{esc_image}"/>',
        f'<link rel="canonical" href="{esc_url}"/>',
        "<!-- Search Engine Optimization & Crawling Directives -->",
        '<meta name="robots" content="index, follow, max-snippet:-1, max-image-preview:large, max-video-preview:-1"/>',
    ])

    site_name = getattr(settings, "site_name", "Claire Bible") if settings else "Claire Bible"
    json_ld: dict[str, Any] = {
        "@context": "https://schema.org",
        "@type": "TechArticle",
        "headline": title,
        "description": desc,
        "mainEntityOfPage": {
            "@type": "WebPage",
            "@id": esc_url,
        },
        "publisher": {
            "@type": "Organization",
            "name": site_name,
        },
    }
    if author:
        json_ld["author"] = {"@type": "Person", "name": author}
    if published_at:
        json_ld["datePublished"] = published_at
    fetched_at = (doc or {}).get("fetched_at")
    if fetched_at:
        try:
            dt = datetime.fromtimestamp(float(fetched_at), tz=timezone.utc)
            json_ld["dateModified"] = dt.strftime("%Y-%m-%dT%H:%M:%SZ")
        except Exception:
            pass
    if node_tags:
        json_ld["keywords"] = node_tags
    json_ld_str = _json.dumps(json_ld, ensure_ascii=False).replace("<", "\\u003c").replace(">", "\\u003e")
    tags.append(f'<script type="application/ld+json">{json_ld_str}</script>')

    return "\n".join(tags)
