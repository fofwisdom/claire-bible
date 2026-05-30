"""URL 정규화 + content hash (dedup 기반)."""

from __future__ import annotations

import hashlib
import re
from urllib.parse import urlsplit, urlunsplit, parse_qsl, urlencode

# 추적용 쿼리 파라미터 — canonical_url 에서 제거
_TRACKING = {
    "utm_source", "utm_medium", "utm_campaign", "utm_term", "utm_content",
    "gclid", "fbclid", "ref", "ref_src", "ref_url", "s", "spm", "share",
    "igshid", "feature",
}


def canonicalize_url(url: str) -> str:
    """호스트 소문자화, fragment 제거, 추적 파라미터 제거, 끝 슬래시 정리."""
    if not url:
        return url
    try:
        parts = urlsplit(url.strip())
    except ValueError:
        return url.strip()
    scheme = (parts.scheme or "https").lower()
    netloc = parts.netloc.lower()
    if netloc.startswith("www."):
        netloc = netloc[4:]
    path = parts.path or ""
    if len(path) > 1 and path.endswith("/"):
        path = path.rstrip("/")
    q = [(k, v) for k, v in parse_qsl(parts.query, keep_blank_values=False)
         if k.lower() not in _TRACKING]
    query = urlencode(sorted(q))
    return urlunsplit((scheme, netloc, path, query, ""))


def content_hash(*parts: str) -> str:
    """본문(+제목 등)으로 안정적 해시. 공백 정규화 후 sha256."""
    norm = " ".join(re.sub(r"\s+", " ", (p or "").strip()) for p in parts)
    return hashlib.sha256(norm.encode("utf-8", "ignore")).hexdigest()
