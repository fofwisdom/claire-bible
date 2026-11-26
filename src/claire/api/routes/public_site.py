"""Public crawling, vocabulary, favicon, manifest, service worker, and font routes."""

from __future__ import annotations

import asyncio
from datetime import datetime, timezone
import hashlib
from pathlib import Path
import re
import time
from typing import Any

from starlette.requests import Request
from starlette.responses import FileResponse, HTMLResponse, PlainTextResponse, Response
from starlette.routing import Route

from ...core.config import Settings
from ...core.ports import get_web_ui_renderer
from ...store import db as dbm, permalinks
from ...store.knowledge_base import KnowledgeBaseManager

_ICON_FILENAME_RE = re.compile(r"^[A-Za-z0-9_.-]+\.(?:png|svg|ico|json|xml|webmanifest)$")
_FONT_FILENAME_RE = re.compile(r"^[A-Za-z0-9_.-]+\.woff2$")
FONTS: tuple[str, ...] = (
    "NotoSansKR-Regular.woff2",
    "NotoSansKR-Bold.woff2",
    "NotoSerifKR-Regular.woff2",
    "NotoSerifKR-Bold.woff2",
    "D2Coding.woff2",
    "D2CodingBold.woff2",
)


def build_public_site_routes(
    *,
    settings: Settings,
    theme_mgr: KnowledgeBaseManager | None,
    sitemap_cache: dict[str, tuple[float, str, bytes]],
) -> list[Route]:
    """Build `/robots.txt`, `/sitemap.xml`, `/ns*`, and static asset routes."""

    async def ns_context_route(_request: Request) -> Response:
        """`GET /ns/v1`: JSON-LD context of the knowledge federation vocabulary."""
        from ...federation.knowledge import vocabulary

        return Response(
            vocabulary.context_json(),
            media_type=vocabulary.CONTEXT_MEDIA_TYPE,
            headers={"Access-Control-Allow-Origin": "*", "Cache-Control": "public, max-age=86400"},
        )

    async def ns_context_v2_route(_request: Request) -> Response:
        """`GET /ns/v2`: JSON-LD context of version 2 (DOCUMENT_RENDITIONS_AND_PUBLICATION_DESIGN.md Section 5.5): version 1 and the terms of renditions and +1."""
        from ...federation.knowledge import vocabulary

        return Response(
            vocabulary.context_json_v2(),
            media_type=vocabulary.CONTEXT_MEDIA_TYPE,
            headers={"Access-Control-Allow-Origin": "*", "Cache-Control": "public, max-age=86400"},
        )

    async def ns_vocabulary_route(_request: Request) -> Response:
        """`GET /ns`: vocabulary page with one anchor per term."""
        from ...federation.knowledge import vocabulary

        return HTMLResponse(vocabulary.vocabulary_page(), headers={"Cache-Control": "public, max-age=86400"})

    def _public_sitemap_targets() -> list[tuple[Path, int]]:
        if theme_mgr is not None:
            private_personal_accounts: set[int] = set()
            if settings.db_file.is_file():
                conn_mode = dbm.connect_existing(settings.db_file, readonly=True)
                try:
                    mode_row = conn_mode.execute("SELECT value FROM server_settings WHERE key='mode'").fetchone()
                    if mode_row is not None and str(mode_row[0]) == "network":
                        private_personal_accounts = {
                            int(r[0]) for r in conn_mode.execute("SELECT id FROM accounts WHERE profile_public=0 OR status!='active'").fetchall()
                        }
                except Exception:
                    private_personal_accounts = set()
                finally:
                    conn_mode.close()
            targets: list[tuple[Path, int]] = []
            for kb in theme_mgr.list_kbs():
                if getattr(kb, "is_personal", False) and getattr(kb, "account_id", None) in private_personal_accounts:
                    continue
                if getattr(kb, "is_public", True):
                    kb_settings = theme_mgr.get_settings_for_kb(kb.id, settings)
                    targets.append((kb_settings.db_file, kb.id))
            return targets

        if settings.db_file.is_file():
            conn_pub = dbm.connect_existing(settings.db_file, readonly=True)
            try:
                rows = conn_pub.execute("SELECT id FROM knowledge_bases WHERE is_public = 1 ORDER BY id").fetchall()
                return [(settings.db_file, int(r[0])) for r in rows]
            except Exception:
                return []
            finally:
                conn_pub.close()
        return [(settings.db_file, 0)]

    async def robots_txt_route(request: Request) -> Response:
        host_header = request.headers.get("host") or ""
        is_private = getattr(settings, "robots_policy", "public") == "disallow_all" or not _public_sitemap_targets()
        if is_private:
            return PlainTextResponse("User-agent: *\nDisallow: /\n", media_type="text/plain; charset=utf-8")

        proto = request.headers.get("x-forwarded-proto") or request.url.scheme or "https"
        authority = host_header or getattr(settings, "fqdn", "") or "localhost"
        sitemap_url = f"{proto}://{authority}/sitemap.xml"

        lines = [
            "User-agent: *",
            "Allow: /$",
            "Allow: /silvercord/",
            "Allow: /static/",
            "Allow: /images/",
            "Allow: /p/images/",
            "Allow: /fonts/",
            "Allow: /favicon.*",
            "Disallow: /search",
            "Disallow: /ingest",
            "Disallow: /reingest",
            "Disallow: /regenerate",
            "Disallow: /document/raw",
            "Disallow: /document/regenerate",
            "Disallow: /document/presentation/generate",
            "Disallow: /document/presentation/compose",
            "Disallow: /document/presentation/compile",
            "Disallow: /document/seen",
            "Disallow: /document/pin",
            "Disallow: /document/hide",
            "Disallow: /document/title",
            "Disallow: /document/purge",
            "Disallow: /document/matrix/purge",
            "Disallow: /purge",
            "Disallow: /synthesize",
            "Disallow: /research",
            "Disallow: /dedup/",
            "Disallow: /entity/",
            "Disallow: /resolution/",
            "Disallow: /oauth/",
            "Disallow: /.well-known/oauth*",
            "Disallow: /mcp",
            "Disallow: /support/bundle",
            "Disallow: /support/graph-bundle",
            "Disallow: /whoami",
            "Disallow: /stats",
            "Disallow: /limits",
            "Disallow: /ingest-limits",
            "Disallow: /providers",
            "Disallow: /themes",
            "Disallow: /syndication",
        ]

        if getattr(settings, "block_ai_crawlers", True):
            ai_agents = [
                "GPTBot",
                "ChatGPT-User",
                "ClaudeBot",
                "anthropic-ai",
                "CCBot",
                "Bytespider",
                "PerplexityBot",
                "Cohere-ai",
                "Diffbot",
                "FacebookBot",
            ]
            lines.append("")
            for agent in ai_agents:
                lines.append(f"User-agent: {agent}")
            lines.append("Disallow: /")

        lines.append("")
        lines.append(f"Sitemap: {sitemap_url}")
        content = "\n".join(lines) + "\n"
        return PlainTextResponse(content, media_type="text/plain; charset=utf-8")

    async def sitemap_xml_route(request: Request) -> Response:
        if not getattr(settings, "sitemap_enabled", True):
            return PlainTextResponse("Not Found", status_code=404)

        target_dbs = _public_sitemap_targets()
        if not target_dbs:
            return PlainTextResponse("Not Found", status_code=404)

        host_header = request.headers.get("host") or ""
        proto = request.headers.get("x-forwarded-proto") or request.url.scheme or "https"
        authority = host_header or getattr(settings, "fqdn", "") or "localhost"
        base_url = f"{proto}://{authority}".rstrip("/")

        cache_ttl = float(getattr(settings, "sitemap_cache_ttl", 900))
        now = time.time()
        cached = sitemap_cache.get(authority)
        if cached is not None:
            cached_at, cached_etag, cached_xml = cached
            if now - cached_at < cache_ttl:
                if request.headers.get("if-none-match") == cached_etag:
                    return Response(status_code=304, headers={"ETag": cached_etag})
                return Response(
                    cached_xml,
                    media_type="application/xml; charset=utf-8",
                    headers={
                        "ETag": cached_etag,
                        "Cache-Control": f"public, max-age={int(cache_ttl)}",
                    },
                )

        all_doc_entries: list[dict[str, Any]] = []
        for db_file, target_tid in target_dbs:
            if not db_file.is_file():
                continue
            try:
                conn = dbm.connect_existing(db_file)
                try:
                    entries = dbm.list_public_sitemap_documents(conn, kb_id=target_tid)
                    all_doc_entries.extend(entries)
                finally:
                    conn.close()
            except Exception:
                pass

        xml_lines = [
            '<?xml version="1.0" encoding="UTF-8"?>',
            '<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">',
            "  <url>",
            f"    <loc>{base_url}/</loc>",
            "    <changefreq>daily</changefreq>",
            "    <priority>1.0</priority>",
            "  </url>",
        ]

        seen_tokens: set[str] = set()
        for doc in all_doc_entries:
            tok = doc.get("token")
            if not tok or tok in seen_tokens:
                continue
            seen_tokens.add(tok)
            loc = permalinks.permalink_url(base_url, tok)
            lastmod = ""
            fetched_at = doc.get("fetched_at")
            published_at = doc.get("published_at")
            if fetched_at:
                try:
                    dt = datetime.fromtimestamp(float(fetched_at), tz=timezone.utc)
                    lastmod = dt.strftime("%Y-%m-%dT%H:%M:%SZ")
                except Exception:
                    pass
            elif published_at and isinstance(published_at, str) and len(published_at) >= 10:
                lastmod = published_at.strip()

            xml_lines.append("  <url>")
            xml_lines.append(f"    <loc>{loc}</loc>")
            if lastmod:
                xml_lines.append(f"    <lastmod>{lastmod}</lastmod>")
            xml_lines.append("    <changefreq>weekly</changefreq>")
            xml_lines.append("    <priority>0.8</priority>")
            xml_lines.append("  </url>")

        xml_lines.append("</urlset>")
        xml_bytes = "\n".join(xml_lines).encode("utf-8")
        etag = f'"{hashlib.md5(xml_bytes).hexdigest()}"'
        sitemap_cache[authority] = (now, etag, xml_bytes)

        if request.headers.get("if-none-match") == etag:
            return Response(status_code=304, headers={"ETag": etag})

        return Response(
            xml_bytes,
            media_type="application/xml; charset=utf-8",
            headers={
                "ETag": etag,
                "Cache-Control": f"public, max-age={int(cache_ttl)}",
            },
        )

    def _static_icon_route(filename: str, media_type: str) -> Any:
        async def _handler(_request: Request) -> Response:
            path = get_web_ui_renderer().static_dir / "icons" / filename
            if not path.is_file():
                return PlainTextResponse("Not Found", status_code=404)
            return FileResponse(path, media_type=media_type)

        return _handler

    async def service_worker_route(request: Request) -> Response:
        headers = {
            "Content-Type": "application/javascript; charset=utf-8",
            "Cache-Control": "no-cache, no-store, must-revalidate",
            "Service-Worker-Allowed": "/",
        }
        if request.method == "HEAD":
            return Response(status_code=200, headers=headers, media_type="application/javascript")
        content = await asyncio.to_thread((get_web_ui_renderer().static_dir / "js" / "sw.js").read_bytes)
        return Response(content=content, status_code=200, headers=headers, media_type="application/javascript")

    async def icon_file_route(request: Request) -> Response:
        rel = request.query_params.get("p", "") or request.query_params.get("name", "")
        if not _ICON_FILENAME_RE.fullmatch(rel):
            return PlainTextResponse("Not Found", status_code=404)
        path = get_web_ui_renderer().static_dir / "icons" / rel
        if not path.is_file():
            return PlainTextResponse("Not Found", status_code=404)
        media_type = None
        if rel.endswith(".png"):
            media_type = "image/png"
        elif rel.endswith(".svg"):
            media_type = "image/svg+xml"
        elif rel.endswith(".ico"):
            media_type = "image/x-icon"
        elif rel.endswith(".json") or rel.endswith(".webmanifest"):
            media_type = "application/manifest+json"
        elif rel.endswith(".xml"):
            media_type = "application/xml"
        return FileResponse(path, media_type=media_type)

    def _create_font_file_handler(name: str) -> Any:
        async def _font_route(_request: Request) -> Response:
            path = get_web_ui_renderer().static_dir / "fonts" / name
            if not path.is_file():
                return PlainTextResponse("Not Found", status_code=404)
            return FileResponse(
                path,
                media_type="font/woff2",
                headers={"Cache-Control": "public, max-age=31536000, immutable"},
            )

        return _font_route

    async def font_file_route(request: Request) -> Response:
        rel = request.query_params.get("p", "") or request.query_params.get("name", "")
        if not _FONT_FILENAME_RE.fullmatch(rel):
            return PlainTextResponse("Not Found", status_code=404)
        path = get_web_ui_renderer().static_dir / "fonts" / rel
        if not path.is_file():
            return PlainTextResponse("Not Found", status_code=404)
        return FileResponse(
            path,
            media_type="font/woff2",
            headers={"Cache-Control": "public, max-age=31536000, immutable"},
        )

    favicon_ico_route = _static_icon_route("favicon.ico", "image/x-icon")
    favicon_svg_route = _static_icon_route("favicon.svg", "image/svg+xml")
    apple_touch_route = _static_icon_route("apple-touch-icon.png", "image/png")
    manifest_route = _static_icon_route("manifest.json", "application/manifest+json")
    browserconfig_route = _static_icon_route("browserconfig.xml", "application/xml")

    return [
        Route("/robots.txt", robots_txt_route, methods=["GET"]),
        Route("/ns", ns_vocabulary_route, methods=["GET"]),
        Route("/ns/v1", ns_context_route, methods=["GET"]),
        Route("/ns/v2", ns_context_v2_route, methods=["GET"]),
        Route("/sitemap.xml", sitemap_xml_route, methods=["GET"]),
        Route("/favicon.ico", favicon_ico_route, methods=["GET"]),
        Route("/favicon.svg", favicon_svg_route, methods=["GET"]),
        Route("/apple-touch-icon.png", apple_touch_route, methods=["GET"]),
        Route("/apple-touch-icon-precomposed.png", apple_touch_route, methods=["GET"]),
        Route("/manifest.json", manifest_route, methods=["GET"]),
        Route("/site.webmanifest", manifest_route, methods=["GET"]),
        Route("/browserconfig.xml", browserconfig_route, methods=["GET"]),
        Route("/sw.js", service_worker_route, methods=["GET"]),
        Route("/icon", icon_file_route, methods=["GET"]),
        Route("/font", font_file_route, methods=["GET"]),
        *(Route(f"/fonts/{font_name}", _create_font_file_handler(font_name), methods=["GET"]) for font_name in FONTS),
    ]
