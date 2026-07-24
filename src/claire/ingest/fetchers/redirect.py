"""Redirect 해석 — google share 등 단축/리다이렉트 URL의 최종 목적지 반환.

google share(share.google)는 HTTP 3xx 가 아니라 **JS 리다이렉트**라 httpx 의
follow_redirects 로는 안 풀린다(share.google 페이지에 머묾). 대신 응답 HTML 본문에
목적지 URL 이 평문으로 들어있어, 거기서 첫 외부(비-google) URL 을 타겟으로 뽑는다.

최종 URL 을 돌려주면 라우터가 다시 적절한 fetcher 로 라우팅한다.
"""

from __future__ import annotations

import re
from urllib.parse import urlsplit

from .base import FetchError

_UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36"

# 타겟이 될 수 없는 인프라/스토어/스키마 도메인 — share 페이지에 항상 섞여 들어옴.
_SKIP_HOSTS = (
    "google.com", "gstatic.com", "googleapis.com", "ggpht.com",
    "googlequicksearchbox", "play.google.com", "apps.apple.com",
    "schema.org", "w3.org", "share.google",
)


def _is_share_host(host: str) -> bool:
    h = host.lower()
    return "share.google" in h or h.startswith("share.")


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
    from ..netpolicy import safe_httpx_get, validate_outbound_url

    try:
        resp = safe_httpx_get(url, timeout=timeout, headers={"User-Agent": _UA})
    except Exception as e:  # noqa: BLE001
        raise FetchError(f"redirect resolve failed: {e}") from e

    final = str(resp.url)
    # HTTP redirect 로 풀려 share 호스트를 벗어났으면 그대로 사용.
    if not _is_share_host(urlsplit(final).netloc):
        return final
    # JS redirect: 본문에서 타겟 추출.
    target = _extract_target(resp.text or "")
    if target:
        validate_outbound_url(target)
    return target or final
