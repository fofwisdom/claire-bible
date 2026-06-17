"""일반 웹 fetcher — 명시적 fallback 체인.

  1) static   : httpx + lxml 정적 추출 (가장 싸고 빠름, 브라우저 불필요)
  2) discourse : 본문 빈약 + Discourse 토픽이면 `.json` API 로 본문 확보 (싸고 결정적)
  3) scrapling : Scrapling Fetcher (curl-cffi + browserforge 스텔스 헤더). 브라우저 불필요.
                 정적 UA 를 403 으로 막는 봇차단(예: openai.com) 우회용.
  4) stealth   : Scrapling StealthyFetcher (Playwright). 최후수단, 브라우저 설치 시에만.

체인을 다 돌고도 본문이 MIN_CONTENT 미만이면 FetchError 로 *실패 처리* —
제목만 적재되는 빈약 스크랩을 막고 raw_inbox 에 error 로 남겨 replay-failed 로 재적재.
임계 300 은 측정 기준: 정상 페이지 최소 ~1300자, 실패 페이지 73~111자 → 깔끔히 분리.
"""

from __future__ import annotations

from ...ontology.base import Document
from ..normalize import canonicalize_url, content_hash
from .base import FetchError

_UA = (
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0 Safari/537.36"
)

# 본문이 이 길이 미만이면 "빈약"으로 보고 다음 fallback 시도, 끝까지 미달이면 실패.
MIN_CONTENT = 300


def fetch_web(url: str) -> Document:
    via = "static"
    title, text, links, anchors, err = _fetch_static(url)

    # 2) Discourse JSON 에스컬레이션
    if len(text or "") < MIN_CONTENT:
        from .discourse import try_discourse

        d = try_discourse(url)
        if d is not None:
            d_title, d_text, d_links = d
            if len(d_text) > len(text or ""):
                title, text, links, via = d_title or title, d_text, d_links or links, "discourse"

    # 3) Scrapling Fetcher 에스컬레이션 — curl-cffi + browserforge 헤더 위장.
    #    브라우저 불필요. 정적 UA 를 막는 봇차단(예: openai.com 403)을 우회.
    if len(text or "") < MIN_CONTENT:
        c_title, c_text, c_links, c_anchors = _fetch_scrapling(url)
        if c_text and len(c_text) > len(text or ""):
            title, text, links, anchors, via = (
                c_title or title, c_text, c_links or links, c_anchors or anchors, "scrapling")

    # 4) Stealth(Playwright) 에스컬레이션 — 브라우저 설치 시에만 동작(없으면 무시)
    if len(text or "") < MIN_CONTENT:
        s_title, s_text = _fetch_stealth(url)
        if s_text and len(s_text) > len(text or ""):
            title, text, via = s_title or title, s_text, "stealth"

    # thin-guard: 체인 끝까지 빈약하면 실패 처리(raw_inbox error → replay-failed 대상)
    if not text or len(text) < MIN_CONTENT:
        raise FetchError(
            err or f"본문 빈약(len={len(text or '')}, via={via}): {url}"
        )

    # link_anchors: 1홉 자동확장 LLM 선별용 신호(url→앵커 텍스트). links 와 같은 상한.
    anchor_pairs = [{"url": u, "anchor": anchors.get(u, "")} for u in links[:50]]
    return Document(
        url=url,
        canonical_url=canonicalize_url(url),
        title=title,
        raw_text=text[:20000],
        source_type="web",
        content_hash=content_hash(title or "", text),
        meta={"links": links[:50], "link_anchors": anchor_pairs, "fetch_via": via},
    )


def _fetch_static(url: str) -> tuple[str | None, str, list[str], dict[str, str], str | None]:
    """(title, text, links, anchors, error). httpx + lxml. 실패해도 예외 대신 빈 결과."""
    import httpx

    try:
        with httpx.Client(follow_redirects=True, timeout=30,
                          headers={"User-Agent": _UA}) as client:
            resp = client.get(url)
        if resp.status_code >= 400:
            return None, "", [], {}, f"http {resp.status_code} for {url}"
        return _extract_html(resp.text)
    except Exception as e:  # noqa: BLE001
        return None, "", [], {}, f"fetch failed: {e}"


def _extract_html(html: str) -> tuple[str | None, str, list[str], dict[str, str], str | None]:
    from lxml import html as lh

    try:
        tree = lh.fromstring(html)
    except Exception as e:  # noqa: BLE001
        return None, "", [], {}, f"parse failed: {e}"

    # 외부 링크 수집(1홉 후보용) + 앵커 텍스트(LLM 선별 신호; url 당 첫 비어있지 않은 텍스트)
    links: list[str] = []
    anchors: dict[str, str] = {}
    seen_l: set[str] = set()
    for a in tree.xpath("//a[@href]"):
        h = (a.get("href") or "").strip()
        if h.startswith("http://") or h.startswith("https://"):
            if h not in seen_l:
                seen_l.add(h)
                links.append(h)
            if not anchors.get(h):
                txt = " ".join(a.text_content().split())[:160]
                if txt:
                    anchors[h] = txt

    # title: og:title > <title> > <h1>
    title = None
    og = tree.xpath("//meta[@property='og:title']/@content")
    if og:
        title = og[0].strip()
    if not title:
        t = tree.xpath("//title/text()")
        if t:
            title = t[0].strip()
    if not title:
        h1 = tree.xpath("//h1//text()")
        if h1:
            title = " ".join(x.strip() for x in h1 if x.strip())[:200]

    # 본문: script/style/nav/footer 제거 후 텍스트
    for bad in tree.xpath("//script | //style | //noscript | //nav | //footer | //header"):
        if bad.getparent() is not None:
            bad.getparent().remove(bad)
    text = " ".join(t.strip() for t in tree.xpath("//body//text()") if t.strip())
    if not text:
        text = " ".join(t.strip() for t in tree.xpath("//text()") if t.strip())
    return (title[:200] if title else None), text, links, anchors, None


def _fetch_scrapling(url: str) -> tuple[str | None, str, list[str], dict[str, str]]:
    """Scrapling Fetcher (curl-cffi + browserforge 스텔스 헤더). 브라우저 불필요.

    정적 httpx UA 를 403 으로 막는 봇차단(예: openai.com)을, 브라우저 지문에
    가까운 헤더/TLS 로 우회. raw HTML 은 _extract_html 로 동일하게 파싱 →
    title/본문/링크(1홉 후보)/앵커 추출 일관성 유지. 미설치/실패 시 (None, '', [], {}).
    """
    try:
        from scrapling.fetchers import Fetcher

        page = Fetcher.get(url, stealthy_headers=True, timeout=30)
        status = getattr(page, "status", 200)
        if status and status >= 400:
            return None, "", [], {}
        html = getattr(page, "html_content", "") or ""
        title, text, links, anchors, _ = _extract_html(str(html))
        return title, text, links, anchors
    except Exception:  # noqa: BLE001
        return None, "", [], {}


def _fetch_stealth(url: str) -> tuple[str | None, str]:
    """Scrapling StealthyFetcher (브라우저 필요). 미설치/실패 시 ('', '')."""
    try:
        from scrapling.fetchers import StealthyFetcher

        page = StealthyFetcher.fetch(url, timeout=45000, headless=True)
        status = getattr(page, "status", 200)
        if status and status >= 400:
            return None, ""
        title = None
        t = page.css_first("title::text")
        if t:
            title = (t if isinstance(t, str) else getattr(t, "text", "")).strip()
        body = getattr(page, "get_all_text", None)
        text = body() if callable(body) else (getattr(page, "text", "") or "")
        return title, str(text).strip()
    except Exception:  # noqa: BLE001
        return None, ""
