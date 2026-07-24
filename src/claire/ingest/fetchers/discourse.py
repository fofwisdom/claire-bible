"""Discourse 포럼 어댑터 — 토픽 `.json` API 로 본문을 정적으로 가져온다.

Discourse 는 본문을 JS 로 렌더링해 정적 HTML 스크랩이 제목만 건진다(예: pytorch.kr).
대신 토픽 URL 에 `.json` 을 붙이면 `post_stream.posts[].cooked`(렌더된 HTML)를 준다.
호스트 하드코딩 없이 *범용*으로: .json 응답에 `post_stream` 키가 있으면 Discourse 로 간주.
"""

from __future__ import annotations

import re
from urllib.parse import urlsplit, urlunsplit

# Discourse 토픽 경로: /t/<slug>/<id> (뒤에 /<post_number> 가 붙을 수 있음)
_TOPIC_RE = re.compile(r"^(/t/[^/]+/\d+)")


def _topic_json_url(url: str) -> str | None:
    """토픽 URL → `<...>/t/slug/id.json`. 토픽 형태가 아니면 None."""
    parts = urlsplit(url)
    m = _TOPIC_RE.match(parts.path)
    if not m:
        return None
    return urlunsplit((parts.scheme, parts.netloc, m.group(1) + ".json", "", ""))


def try_discourse(url: str) -> tuple[str | None, str, list[str]] | None:
    """Discourse 토픽이면 (title, text, links) 반환, 아니면 None.

    실패(네트워크/비-Discourse)는 None 으로 떨어뜨려 상위 fallback 체인이 잇게 한다.
    """
    json_url = _topic_json_url(url)
    if not json_url:
        return None

    from ..netpolicy import safe_httpx_get

    try:
        resp = safe_httpx_get(
            json_url, timeout=20, headers={"User-Agent": "Mozilla/5.0 (claire)"},
        )
        if resp.status_code >= 400:
            return None
        data = resp.json()
    except Exception:  # noqa: BLE001
        return None

    posts = (data.get("post_stream") or {}).get("posts")
    if not isinstance(posts, list) or not posts:
        return None  # Discourse 가 아니거나 본문 없음

    title = data.get("title") or data.get("fancy_title")
    bodies, links = [], []
    seen: set[str] = set()
    multi = len(posts) > 1  # 토론(여러 포스트)일 때만 작성자 prefix 가 의미 있음
    for p in posts:
        cooked = p.get("cooked") or ""
        if not cooked:
            continue
        text, hrefs = _strip_html(cooked)
        if text:
            who = p.get("username") or ""
            bodies.append(f"[{who}] {text}" if (multi and who) else text)
        for h in hrefs:
            if h not in seen:
                seen.add(h)
                links.append(h)

    full = _trim_boilerplate("\n\n".join(bodies).strip())
    if not full:
        return None
    return (title.strip() if title else None), full, links[:50]


# Discourse 이미지 lightbox 가 남기는 메타(예: "1536×1024 229 KB") 잔재.
_IMG_META_RE = re.compile(r"\d+\s*[×x]\s*\d+(?:\s+[\d.]+\s*[KMG]?B)?")

# 본문 끝에 붙는 사이트 푸터/추천위젯 마커. 후반부(>=50%)에서 가장 먼저 나오는
# 마커부터 잘라낸다. (PyTorchKR 등 커뮤니티 공통: 관련글 목록 + 정리 고지 + 가입 CTA)
_BOILERPLATE_MARKERS = (
    "더 읽어보기",
    "이 글은 GPT 모델",
    "이 글은 LLM",
    "원문도 함께 참고",
    "회원으로 가입",
    "좋아요 를 눌러",
    "좋아요를 눌러",
    "이 정리한 이 글이 유용",
)


def _trim_boilerplate(text: str) -> str:
    """본문 후반부에 등장하는 사이트 푸터/추천글 위젯을 잘라낸다.

    오탐(본문 중간의 우연한 일치)을 막기 위해 *문서 후반(50% 이후)* 에 나타난
    마커만 절단 지점으로 인정한다. 여러 마커 중 가장 앞선 위치에서 자른다.
    """
    if not text:
        return text
    half = len(text) // 2
    cut = len(text)
    for m in _BOILERPLATE_MARKERS:
        i = text.find(m)
        if i >= half and i < cut:
            cut = i
    return text[:cut].strip()


def _strip_html(html: str) -> tuple[str, list[str]]:
    """cooked HTML → (plain text, 외부 링크 목록).

    이미지/캡션/메타(.lightbox·.meta·.informations·img) 요소를 먼저 제거해
    "1536×1024 229 KB" 같은 썸네일 잡음이 본문에 섞이지 않게 한다.
    multi-root cooked 도 안전하게 파싱하려고 div 로 감싼다.
    """
    from lxml import html as lh

    try:
        tree = lh.fragment_fromstring(html, create_parent="div")
    except Exception:  # noqa: BLE001
        return _IMG_META_RE.sub(" ", re.sub(r"<[^>]+>", " ", html)).strip(), []

    links = [
        h.strip() for h in tree.xpath(".//a/@href")
        if (h or "").strip().startswith(("http://", "https://"))
    ]
    # 이미지/메타성 노드 제거(본문 텍스트는 보존)
    for bad in tree.xpath(
        ".//img | .//script | .//style | .//svg"
        " | .//*[contains(@class,'meta')]"
        " | .//*[contains(@class,'informations')]"
        " | .//*[contains(@class,'filename')]"
    ):
        parent = bad.getparent()
        if parent is not None:
            parent.remove(bad)

    text = " ".join(t.strip() for t in tree.xpath(".//text()") if t.strip())
    text = _IMG_META_RE.sub(" ", text)        # 잔재 메타 정규식 청소
    text = re.sub(r"\s+", " ", text).strip()  # 공백 정규화
    return text, links
