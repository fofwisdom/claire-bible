"""일반 웹 fetcher — 명시적 fallback 체인.

  1) static   : httpx + lxml 정적 추출 (가장 싸고 빠름, 브라우저 불필요)
  2) discourse : 본문 빈약 + Discourse 토픽이면 `.json` API 로 본문 확보 (싸고 결정적)
  3) scrapling : Scrapling Fetcher (curl-cffi + browserforge 스텔스 헤더). 브라우저 불필요.
                 정적 UA 를 403 으로 막는 봇차단(예: openai.com) 우회용.
  4) cdp       : nodriver(Chrome DevTools Protocol 직접 제어)로 실제 렌더링. 최후수단 —
                 JS 로만 그려지는 SPA(해시 라우팅 등)는 static/scrapling 이 빈 껍데기만
                 받아오므로 진짜 브라우저 실행이 필요. Playwright/patchright 는 안 쓰고
                 시스템 Chromium 을 CDP 로 직접 제어(이미지에 apt chromium 패키지 하나만
                 추가하면 됨 — Playwright 자체 브라우저 번들보다 가벼움).

체인을 다 돌고도 본문이 MIN_CONTENT 미만이면 FetchError 로 *실패 처리* —
제목만 적재되는 빈약 스크랩을 막고 raw_inbox 에 error 로 남겨 replay-failed 로 재적재.
임계 300 은 측정 기준: 정상 페이지 최소 ~1300자, 실패 페이지 73~111자 → 깔끔히 분리.
"""

from __future__ import annotations

import re

from ...ontology.base import Document
from ..normalize import canonicalize_url, content_hash
from .base import FetchError

_UA = (
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0 Safari/537.36"
)

# 본문이 이 길이 미만이면 "빈약"으로 보고 다음 fallback 시도, 끝까지 미달이면 실패.
MIN_CONTENT = 300

# Cloudflare 등 봇차단 인터스티셜/삭제·404 안내 페이지의 전형적 문구.
# 본문 앞부분(_FAILURE_SCAN_CHARS)에서만 검사 — 정상 기사 중간에 우연히
# 섞인 단어까지 걸리지 않도록. 길이 기준(MIN_CONTENT)만으로는 이런 페이지도
# "본문 충분"으로 오인해 정상 콘텐츠처럼 채택/저장되는 문제(inbox 실사례:
# Cloudflare "Just a moment...", 삭제된 페이지의 "Page not found ... This
# page is not in the workspace ...")를 막기 위한 가드.
_FAILURE_SCAN_CHARS = 400
_FAILURE_RE = re.compile(
    r"just a moment|checking your browser|verify you are (?:a )?human|"
    r"attention required|complete the security check|"
    r"enable javascript and cookies|"
    r"page not found|not in the workspace|we could not find the page|"
    r"404 error|error 404|403 forbidden|401 unauthorized",
    re.IGNORECASE,
)


def _looks_like_failure_page(text: str | None) -> bool:
    """본문이 봇차단 인터스티셜/삭제·404 안내 페이지로 보이면 True."""
    if not text:
        return False
    return bool(_FAILURE_RE.search(text[:_FAILURE_SCAN_CHARS]))


def _content_score(text: str | None) -> int:
    """길이 비교용 점수 — 실패 페이지로 보이면 0(다음 fallback 이 더 나은 후보로 채택)."""
    if not text or _looks_like_failure_page(text):
        return 0
    return len(text)

# 본문 콘텐츠 이미지(다이어그램·차트·스크린샷·도식)만 후보로. 장식/추적/UI 잡동사니는 사전 제외.
# (최종 선별은 render_detail 의 LLM 큐레이션이 한 번 더 — 여기선 명백한 잡음만 거른다.)
_IMG_NOISE_RE = re.compile(
    r"(?:^|[/_\-.])(?:icon|logo|avatar|sprite|emoji|badge|pixel|spacer|favicon|"
    r"gravatar|profile|thumb|placeholder|loading|blank|1x1|button|btn|arrow|"
    r"share|social|ads?|advert|banner|tracking|beacon|analytics)(?:[/_\-.]|$)",
    re.IGNORECASE,
)
_MAX_IMAGES = 12        # 문서당 후보 이미지 상한(LLM 큐레이션 입력 폭 통제)
_IMG_MIN_DIM = 150      # width/height 속성이 명시돼 있고 이보다 작으면 장식/아이콘으로 보고 제외


def fetch_web(url: str) -> Document:
    via = "static"
    title, text, links, anchors, err, effective_url, images = _fetch_static(url)

    # 2) Discourse JSON 에스컬레이션
    if _content_score(text) < MIN_CONTENT:
        from .discourse import try_discourse

        d = try_discourse(url)
        if d is not None:
            d_title, d_text, d_links = d
            if _content_score(d_text) > _content_score(text):
                title, text, links, via = d_title or title, d_text, d_links or links, "discourse"

    # 3) Scrapling Fetcher 에스컬레이션 — curl-cffi + browserforge 헤더 위장.
    #    브라우저 불필요. 정적 UA 를 막는 봇차단(예: openai.com 403)을 우회.
    if _content_score(text) < MIN_CONTENT:
        c_title, c_text, c_links, c_anchors, c_images = _fetch_scrapling(url)
        if c_text and _content_score(c_text) > _content_score(text):
            title, text, links, anchors, images, via = (
                c_title or title, c_text, c_links or links, c_anchors or anchors,
                c_images or images, "scrapling")

    # 4) CDP(nodriver) 에스컬레이션 — 실제 Chromium 렌더링. 최후수단(느림, 브라우저 필요).
    if _content_score(text) < MIN_CONTENT:
        d_title, d_text, d_links, d_anchors, d_images = _fetch_cdp(url)
        if d_text and _content_score(d_text) > _content_score(text):
            title, text, links, anchors, images, via = (
                d_title or title, d_text, d_links or links, d_anchors or anchors,
                d_images or images, "cdp")

    # thin-guard: 체인 끝까지 빈약하거나 인터스티셜/실패 페이지면 실패 처리
    # (raw_inbox error → replay-failed 대상). 봇차단/삭제 안내 페이지가 길이
    # 기준만으로 정상 콘텐츠로 오인되지 않도록 _content_score 로 함께 판정.
    if not text or _content_score(text) < MIN_CONTENT:
        reason = "인터스티셜/실패 페이지로 판단" if _looks_like_failure_page(text) else "본문 빈약"
        raise FetchError(
            err or f"{reason}(len={len(text or '')}, via={via}): {url}"
        )

    # canonical 은 서버 redirect 이후의 *실제 도달 URL* 기준(dedup 핵심).
    #   직접링크와 share/단축링크가 같은 페이지로 풀리면 같은 canonical 로 수렴 → 중복 방지.
    #   static 이 실패해 effective 를 못 얻으면 입력 url 로 폴백.
    effective = effective_url or url
    # link_anchors: 1홉 자동확장 LLM 선별용 신호(url→앵커 텍스트). links 와 같은 상한.
    anchor_pairs = [{"url": u, "anchor": anchors.get(u, "")} for u in links[:50]]
    return Document(
        url=url,
        canonical_url=canonicalize_url(effective),
        title=title,
        raw_text=text[:20000],
        source_type="web",
        content_hash=content_hash(title or "", text),
        # images: 본문 콘텐츠 이미지 후보(다이어그램·차트·스크린샷). render_detail 의 LLM
        # 큐레이션이 이해에 도움 되는 것만 골라 마크다운에 삽입한다(이미지/도식 보존).
        meta={"links": links[:50], "link_anchors": anchor_pairs, "fetch_via": via,
              "effective_url": effective, "images": images or []},
    )


def _fetch_static(
    url: str,
) -> tuple[str | None, str, list[str], dict[str, str], str | None, str | None, list[dict]]:
    """(title, text, links, anchors, error, effective_url, images). httpx + lxml.

    effective_url 은 httpx 의 follow_redirects 가 따라간 최종 URL(resp.url) — dedup 의
    canonical 기준. 실패하면 None. 실패해도 예외 대신 빈 결과를 돌려준다. images 는
    본문 이미지 후보(상대경로는 effective_url 기준으로 절대경로화).
    """
    from ..netpolicy import safe_httpx_get

    try:
        resp = safe_httpx_get(url, timeout=30, headers={"User-Agent": _UA})
        if resp.status_code >= 400:
            return None, "", [], {}, f"http {resp.status_code} for {url}", None, []
        title, text, links, anchors, perr, images = _extract_html(
            resp.text, base_url=str(resp.url))
        return title, text, links, anchors, perr, str(resp.url), images
    except Exception as e:  # noqa: BLE001
        return None, "", [], {}, f"fetch failed: {e}", None, []


def _extract_html(
    html: str, base_url: str | None = None,
) -> tuple[str | None, str, list[str], dict[str, str], str | None, list[dict]]:
    from lxml import html as lh

    try:
        tree = lh.fromstring(html)
    except Exception as e:  # noqa: BLE001
        return None, "", [], {}, f"parse failed: {e}", []

    # 상대경로(href/src)를 절대경로로 — 링크·이미지 url 을 그대로 쓸 수 있게(이미지 보존).
    if base_url:
        try:
            tree.make_links_absolute(base_url)
        except Exception:  # noqa: BLE001
            pass

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

    # og:image(대표 이미지)는 본문 제거 전에 — 보통 <head> 에 있어 본문 정리와 무관.
    og_image = None
    ogi = tree.xpath("//meta[@property='og:image']/@content")
    if ogi and (ogi[0] or "").strip().startswith(("http://", "https://")):
        og_image = ogi[0].strip()

    # 본문: script/style/nav/footer 제거 후 텍스트. 이미지도 본문 영역에서만 모은다
    # (nav/header/footer 의 로고·아이콘은 이 제거로 함께 빠진다).
    for bad in tree.xpath("//script | //style | //noscript | //nav | //footer | //header"):
        if bad.getparent() is not None:
            bad.getparent().remove(bad)
    images = _collect_images(tree, og_image)
    text = " ".join(t.strip() for t in tree.xpath("//body//text()") if t.strip())
    if not text:
        text = " ".join(t.strip() for t in tree.xpath("//text()") if t.strip())
    return (title[:200] if title else None), text, links, anchors, None, images


def _collect_images(tree, og_image: str | None) -> list[dict]:  # noqa: ANN001
    """본문 콘텐츠 이미지 후보를 휴리스틱으로 선별 — [{url, alt, caption}].

    명백한 장식/추적/UI 이미지(로고·아이콘·아바타·광고·1x1 픽셀·sprite)는 여기서 거르고,
    최종 '이해에 도움 되는가'는 render_detail 의 LLM 큐레이션이 한 번 더 판단한다.
    상대경로는 _extract_html 에서 이미 절대경로화됨. og:image 는 대표 이미지로 합류시킨다.
    """
    out: list[dict] = []
    seen: set[str] = set()

    def _consider(url: str, alt: str, caption: str, *, w=None, h=None,  # noqa: ANN001
                  cls: str = "") -> None:
        url = (url or "").strip()
        if not url or not url.startswith(("http://", "https://")):
            return  # data: URI·빈 src 제외(대개 인라인 아이콘)
        base = url.split("?", 1)[0]
        if base in seen:
            return
        # 치수 명시돼 있고 작으면(아이콘/썸네일) 제외
        for d in (w, h):
            try:
                if d is not None and int(str(d).rstrip("px")) < _IMG_MIN_DIM:
                    return
            except (ValueError, TypeError):
                pass
        if _IMG_NOISE_RE.search(base) or _IMG_NOISE_RE.search(cls):
            return
        seen.add(base)
        out.append({"url": url, "alt": (alt or "").strip()[:200],
                    "caption": (caption or "").strip()[:300]})

    for img in tree.xpath("//img"):
        if len(out) >= _MAX_IMAGES:
            break
        # lazy-load 패턴(data-src 등)도 본다 — 많은 사이트가 src 에 placeholder 를 둔다.
        src = (img.get("src") or img.get("data-src") or img.get("data-original")
               or img.get("data-lazy-src") or "")
        # <figure><figcaption> 캡션 — 콘텐츠 이미지의 강한 신호이자 LLM 배치 단서.
        caption = ""
        fig = img.xpath("ancestor::figure[1]")
        if fig:
            fc = fig[0].xpath(".//figcaption")
            if fc:
                caption = " ".join(fc[0].text_content().split())
        _consider(src, img.get("alt", ""), caption,
                  w=img.get("width"), h=img.get("height"),
                  cls=f"{img.get('class', '')} {img.get('id', '')}")

    if og_image:
        _consider(og_image, "대표 이미지", "")
    return out[:_MAX_IMAGES]


def _fetch_scrapling(url: str) -> tuple[str | None, str, list[str], dict[str, str], list[dict]]:
    """Scrapling Fetcher (curl-cffi + browserforge 스텔스 헤더). 브라우저 불필요.

    정적 httpx UA 를 403 으로 막는 봇차단(예: openai.com)을, 브라우저 지문에
    가까운 헤더/TLS 로 우회. raw HTML 은 _extract_html 로 동일하게 파싱 →
    title/본문/링크(1홉 후보)/앵커/이미지 추출 일관성 유지. 미설치/실패 시 빈 결과.
    """
    try:
        from ..netpolicy import validate_outbound_url
        from scrapling.fetchers import Fetcher

        validate_outbound_url(url)
        page = Fetcher.get(
            url,
            stealthy_headers=True,
            timeout=30,
            follow_redirects="safe",
            max_redirects=5,
        )
        status = getattr(page, "status", 200)
        if status and status >= 400:
            return None, "", [], {}, []
        html = getattr(page, "html_content", "") or ""
        title, text, links, anchors, _, images = _extract_html(str(html), base_url=url)
        return title, text, links, anchors, images
    except Exception:  # noqa: BLE001
        return None, "", [], {}, []


def _validate_browser_request_url(url: str) -> None:
    """CDP가 실제 네트워크로 내보내는 URL만 공통 outbound 정책으로 검증한다."""
    from urllib.parse import urlsplit

    # 문서 내부에서 만들어지는 로컬 리소스는 네트워크 연결을 만들지 않는다.
    if urlsplit(url).scheme.lower() in {"about", "blob", "data"}:
        return
    from ..netpolicy import validate_outbound_url

    validate_outbound_url(url)


def _fetch_cdp(url: str) -> tuple[str | None, str, list[str], dict[str, str], list[dict]]:
    """nodriver 로 시스템 Chromium 을 CDP 직접 제어해 실제 렌더링(브라우저 필요).

    JS SPA(해시 라우팅 등, 예: uniclawbench.github.io)는 static/scrapling(curl-cffi, 무JS)
    으로는 빈 셸만 받아온다 — 진짜 브라우저 실행이 필요한 최후수단. Playwright/patchright
    없이 nodriver(순수 CDP 클라이언트)로 apt 설치된 chromium 바이너리를 직접 제어한다.
    미설치/실패 시 빈 결과(체인의 다음 단계 없음 → thin-guard 가 최종 실패 처리).
    """
    try:
        import asyncio

        import nodriver as uc
        from ..netpolicy import validate_outbound_url

        validate_outbound_url(url)

        async def _run() -> str:
            browser = await uc.start(
                headless=True,
                browser_args=["--no-sandbox", "--disable-gpu", "--disable-dev-shm-usage"],
            )
            try:
                # 최초 navigation 전에 Fetch domain을 켜 redirect·JS navigation·하위
                # 리소스 각각을 검사한다. private 요청은 브라우저가 보내기 전에 중단한다.
                page = browser.tabs[0]

                async def _guard_request(event, connection) -> None:
                    try:
                        await asyncio.to_thread(
                            _validate_browser_request_url, event.request.url,
                        )
                    except Exception:  # noqa: BLE001
                        await connection.send(uc.cdp.fetch.fail_request(
                            event.request_id,
                            uc.cdp.network.ErrorReason.BLOCKED_BY_CLIENT,
                        ))
                        return
                    await connection.send(
                        uc.cdp.fetch.continue_request(event.request_id))

                page.add_handler(uc.cdp.fetch.RequestPaused, _guard_request)
                await page.send(uc.cdp.fetch.enable())
                # Tab.get()은 attach를 다시 수행해 Fetch domain/session을 바꾸므로,
                # 현재 CDP session을 유지한 채 Page.navigate를 직접 보낸다.
                await page.send(uc.cdp.page.navigate(url))
                await page.sleep(2.5)  # JS 렌더링 대기(SPA 초기 로드)
                return await page.get_content()
            finally:
                browser.stop()

        html = asyncio.run(_run())
        if not html:
            return None, "", [], {}, []
        title, text, links, anchors, _, images = _extract_html(str(html), base_url=url)
        return title, text, links, anchors, images
    except Exception:  # noqa: BLE001
        return None, "", [], {}, []
