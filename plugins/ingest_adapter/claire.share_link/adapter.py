"""Redirect 해석 (project plugin claire.share_link) — google share 등 단축/리다이렉트 URL의 최종 목적지 반환.

google share(share.google)는 HTTP 3xx 가 아니라 **JS 리다이렉트**라 httpx 의 follow_redirects 로는 안 풀린다(share.google 페이지에 머묾). 대신 응답 HTML 본문에 목적지 URL 이 평문으로 들어있어, 거기서 첫 외부(비-google) URL 을 타겟으로 뽑는다.

최종 URL 을 돌려주면 라우터가 다시 적절한 fetcher 로 라우팅한다.
"""

from __future__ import annotations

import re
from urllib.parse import urlsplit

from typing import Any

from claire.plugin_sdk import BaseFetcher, Document, FetchError, PluginContext, fetch_web, reroute, url_host

_ctx: PluginContext | None = None


def register(ctx: PluginContext) -> None:
    global _ctx
    _ctx = ctx
    ctx.fetcher(ShareLinkFetcher, name="share_link", priority=100, hosts=("*",))

_UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36"

# 타겟이 될 수 없는 인프라/스토어/스키마 도메인 — share 페이지에 항상 섞여 들어옴.
_SKIP_HOSTS = (
    "google.com", "gstatic.com", "googleapis.com", "ggpht.com",
    "googlequicksearchbox", "play.google.com", "apps.apple.com",
    "schema.org", "w3.org", "share.google",
)


def _is_share_host(host: str) -> bool:
    """Exact hosts (INGEST_ADAPTER_PLUGIN_DESIGN.md PL-D12): share.google, a host that starts with "share.", and known short link hosts."""
    h = host.lower().split(":", 1)[0].rstrip(".")
    return h == "share.google" or h.startswith("share.") or h in ("naver.me", "link.naver.com", "bit.ly", "tinyurl.com", "t.co")


def _extract_target(html: str) -> str | None:
    """share 페이지 HTML 에서 실제 목적지 URL 추출(canonical/og:url 우선, 없으면 첫 외부)."""
    for pat in (r'<link[^>]+rel="canonical"[^>]+href="([^"]+)"',
                r'<meta[^>]+property="og:url"[^>]+content="([^"]+)"'):
        m = re.search(pat, html, re.IGNORECASE)
        if m:
            u = m.group(1)
            if u.startswith("http") and not any(s in u for s in _SKIP_HOSTS):
                return u
    for u in re.findall(r'https?://[^\s"\'<>\\]+', html):
        if not any(s in u for s in _SKIP_HOSTS):
            return u.rstrip('".,)\\')
    return None


def resolve_redirect(url: str, timeout: float = 15.0) -> str:
    assert _ctx is not None, "register(ctx) was not called"
    try:
        with _ctx.http_client(follow_redirects=True, timeout=timeout,
                          headers={"User-Agent": _UA}) as client:
            resp = client.get(url)
    except Exception as e:
        raise FetchError(f"redirect resolve failed: {e}") from e

    final = str(resp.url)
    parsed_final = urlsplit(final)
    if (parsed_final.hostname or "") == "link.naver.com" and "/bridge" in parsed_final.path:
        from urllib.parse import parse_qs, unquote
        qs = parse_qs(parsed_final.query)
        if "url" in qs and qs["url"]:
            return unquote(qs["url"][0])

    # HTTP redirect 로 풀려 share 호스트를 벗어났으면 그대로 사용.
    if not _is_share_host(urlsplit(final).netloc):
        return final
    # JS redirect: 본문에서 타겟 추출.
    target = _extract_target(resp.text or "")
    return target or final


class ShareLinkFetcher(BaseFetcher):
    """Takes share links; routes the destination again (``reroute``)."""

    @classmethod
    def can_handle(cls, url: str) -> bool:
        host = url_host(url) or ""
        return host == "share.google" or host.startswith("share.")

    @classmethod
    def fetch(cls, url: str, *, full_content: bool = False, **kwargs: Any) -> Document:
        final = resolve_redirect(url)
        if final and final != url:
            return reroute(final, full_content=full_content)
        # No destination: the web chain tries the page itself.
        return fetch_web(url, full_content=full_content)
