"""일반 웹 fetcher — 명시적 fallback 체인.

  1) static   : httpx + lxml 정적 추출 (가장 싸고 빠름, 브라우저 불필요)
  2) law      : 국가법령정보센터(law.go.kr) iframe/AJAX 2중 구조 역추적 및 조문 정적 확보
  3) discourse : 본문 빈약 + Discourse 토픽이면 `.json` API 로 본문 확보 (싸고 결정적)
  4) scrapling : Scrapling Fetcher (curl-cffi + browserforge 스텔스 헤더). 브라우저 불필요.
                 정적 UA 를 403 으로 막는 봇차단(예: openai.com) 우회용.
  5) cdp       : Scrapling DynamicFetcher로 시스템 Chromium을 제어해 실제 렌더링.
                 JS로만 그려지는 SPA(해시 라우팅 등)는 static/scrapling 정적 경로가
                 빈 셸만 받아오므로 진짜 브라우저 실행이 필요. Python 패키지와
                 브라우저 바이너리를 분리하여 이미지에는 시스템 Chromium만 설치한다.

체인을 다 돌고도 본문이 MIN_CONTENT 미만이면 FetchError 로 *실패 처리* —
제목만 적재되는 빈약 스크랩을 막고 raw_inbox 에 error 로 남겨 replay-failed 로 재적재.
임계 300 은 측정 기준: 정상 페이지 최소 ~1300자, 실패 페이지 73~111자 → 깔끔히 분리.
"""

from __future__ import annotations

import re
import shutil
from pathlib import Path

from ...config import get_settings
from ...extract.table_budget import (
    slice_document_text,
    slice_text_with_table_exemption,
    slice_text_with_table_exemption_info,
)
from ...ontology.base import Document
from ..normalize import canonicalize_url, content_hash
from .base import FetchError
from .guard import validate_web_content
from .http_policy import BROWSER_USER_AGENT

_UA = BROWSER_USER_AGENT

# 본문이 이 길이 미만이면 "빈약"으로 보고 다음 fallback 시도, 끝까지 미달이면 실패.
MIN_CONTENT = 300


def _is_usable(title: str | None, text: str | None) -> tuple[bool, str | None]:
    """본문이 최소 길이를 만족하고 차단/저품질 가드를 통과하는지 확인."""
    if not text or len(text) < MIN_CONTENT:
        return False, f"본문 빈약(len={len(text or '')})"
    return validate_web_content(title, text)


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


class FetchStaticResult(tuple):
    """8개 튜플(title, text, links, anchors, error, effective_url, images, is_pdf) 호환 객체."""

    def __new__(cls, title, text, links, anchors, error, effective_url, images, is_pdf, biblio=None, parser_info=None):
        inst = super().__new__(cls, (title, text, links, anchors, error, effective_url, images, is_pdf))
        inst.biblio = biblio or {}
        inst.parser_info = parser_info or {}
        return inst


class FetchScraplingResult(tuple):
    """6개 튜플(title, text, links, anchors, images, is_pdf) 호환 객체."""

    def __new__(cls, title, text, links, anchors, images, is_pdf, biblio=None, parser_info=None):
        inst = super().__new__(cls, (title, text, links, anchors, images, is_pdf))
        inst.biblio = biblio or {}
        inst.parser_info = parser_info or {}
        return inst


def fetch_web(url: str, *, full_content: bool = False) -> Document:
    via = "static"
    res = _fetch_static(url)
    title, text, links, anchors, err, effective_url, images = res[:7]
    is_pdf = bool(res[7]) if len(res) > 7 else False
    biblio: dict[str, Any] = getattr(res, "biblio", None) or (res[8] if len(res) > 8 and isinstance(res[8], dict) else {})
    parser_info: dict[str, Any] = getattr(res, "parser_info", {}) or {}
    usable, guard_err = _is_usable(title, text)

    # 2) law.go.kr 에스컬레이션 — 국가법령정보센터 iframe / ajax 구조 해소
    if not usable:
        from .law import try_law_kr

        l = try_law_kr(url)
        if l is not None:
            l_title, l_text, l_links, l_anchors, l_images = l
            l_usable, l_guard_err = _is_usable(l_title or title, l_text)
            if l_usable:
                title, text, links, anchors, images, via = (
                    l_title or title, l_text, l_links or links, l_anchors or anchors,
                    l_images or images, "law"
                )
                usable, guard_err, is_pdf = True, None, False
            elif len(l_text) > len(text or ""):
                title, text, links, anchors, images, via = (
                    l_title or title, l_text, l_links or links, l_anchors or anchors,
                    l_images or images, "law"
                )
                usable, guard_err, is_pdf = l_usable, l_guard_err, False

    # 3) Discourse JSON 에스컬레이션
    if not usable:
        from .discourse import try_discourse

        d = try_discourse(url)
        if d is not None:
            d_title, d_text, d_links = d
            d_usable, d_guard_err = _is_usable(d_title or title, d_text)
            if d_usable:
                title, text, links, via = d_title or title, d_text, d_links or links, "discourse"
                usable, guard_err, is_pdf = True, None, False
            elif len(d_text) > len(text or ""):
                title, text, links, via = d_title or title, d_text, d_links or links, "discourse"
                usable, guard_err, is_pdf = d_usable, d_guard_err, False

    # 4) Scrapling Fetcher 에스컬레이션 — curl-cffi + browserforge 헤더 위장.
    #    브라우저 불필요. 정적 UA 를 막는 봇차단(예: openai.com 403)을 우회.
    if not usable:
        c_res = _fetch_scrapling(url)
        c_title, c_text, c_links, c_anchors, c_images = c_res[:5]
        c_is_pdf = bool(c_res[5]) if len(c_res) > 5 else False
        c_biblio = getattr(c_res, "biblio", None) or (c_res[6] if len(c_res) > 6 and isinstance(c_res[6], dict) else {})
        c_parser_info = getattr(c_res, "parser_info", None) or (c_res[7] if len(c_res) > 7 and isinstance(c_res[7], dict) else {})
        c_usable, c_guard_err = _is_usable(c_title or title, c_text)
        if c_usable:
            title, text, links, anchors, images, via = (
                c_title or title, c_text, c_links or links, c_anchors or anchors,
                c_images or images, "scrapling")
            usable, guard_err, is_pdf, biblio = True, None, c_is_pdf, c_biblio
            if c_parser_info:
                parser_info = c_parser_info
        elif c_text and len(c_text) > len(text or ""):
            title, text, links, anchors, images, via = (
                c_title or title, c_text, c_links or links, c_anchors or anchors,
                c_images or images, "scrapling")
            usable, guard_err, is_pdf, biblio = c_usable, c_guard_err, c_is_pdf, c_biblio
            if c_parser_info:
                parser_info = c_parser_info

    # 4) CDP(nodriver) 에스컬레이션 — 실제 Chromium 렌더링. 최후수단(느림, 브라우저 필요).
    if not usable:
        d_title, d_text, d_links, d_anchors, d_images = _fetch_cdp(url)
        d_usable, d_guard_err = _is_usable(d_title or title, d_text)
        if d_usable:
            title, text, links, anchors, images, via = (
                d_title or title, d_text, d_links or links, d_anchors or anchors,
                d_images or images, "cdp")
            usable, guard_err, is_pdf = True, None, False
        elif d_text and len(d_text) > len(text or ""):
            title, text, links, anchors, images, via = (
                d_title or title, d_text, d_links or links, d_anchors or anchors,
                d_images or images, "cdp")
            usable, guard_err, is_pdf = d_usable, d_guard_err, False

    # thin-guard & content-guard: 체인 끝까지 미달/차단이면 실패 처리(raw_inbox error → replay-failed 대상)
    if not usable:
        fail_reason = guard_err or err or f"본문 빈약(len={len(text or '')})"
        raise FetchError(
            f"{fail_reason} (via={via}): {url}"
        )

    # canonical 은 서버 redirect 이후의 *실제 도달 URL* 기준(dedup 핵심).
    #   직접링크와 share/단축링크가 같은 페이지로 풀리면 같은 canonical 로 수렴 → 중복 방지.
    #   static 이 실패해 effective 를 못 얻으면 입력 url 로 폴백.
    effective = effective_url or url

    # link_anchors: 1홉 자동확장 LLM 선별용 신호(url→앵커 텍스트). links 와 같은 상한.
    anchor_pairs = [{"url": u, "anchor": anchors.get(u, "")} for u in links[:50]]
    is_pdf = bool(
        is_pdf
        or url.lower().split("?", 1)[0].endswith(".pdf")
        or (effective_url and effective_url.lower().split("?", 1)[0].endswith(".pdf"))
        or via == "pdf"
    )
    settings = get_settings()
    budget = 0 if full_content else (settings.pdf_max_extract_chars if is_pdf else settings.raw_char_budget)
    appendix_truncated = False
    references_truncated = False
    if is_pdf:
        from .pdf import slice_pdf_text

        exclude_app = False if full_content else settings.pdf_exclude_appendix
        exclude_ref = False if full_content else settings.pdf_exclude_references
        raw_text, is_truncated, appendix_truncated, references_truncated, orig_chars, raw_chars = slice_pdf_text(
            text or "",
            budget,
            strategy=settings.slicing_strategy,
            exclude_appendix=exclude_app,
            exclude_references=exclude_ref,
        )
    else:
        raw_text, is_truncated, orig_chars, raw_chars = slice_document_text(
            text or "", budget, strategy=settings.slicing_strategy
        )
    meta: dict[str, Any] = {
        "links": links[:50],
        "link_anchors": anchor_pairs,
        "fetch_via": via,
        "effective_url": effective,
        "images": images or [],
        "raw_truncated": is_truncated,
        "appendix_truncated": appendix_truncated,
        "references_truncated": references_truncated,
        "orig_chars": orig_chars,
        "raw_chars": raw_chars,
    }
    if is_pdf and parser_info:
        meta.update(parser_info)
    if biblio:
        meta["biblio"] = biblio
    return Document(
        url=url,
        canonical_url=canonicalize_url(effective),
        title=title,
        author=biblio.get("author") if biblio else None,
        published_at=biblio.get("published_at") if biblio else None,
        raw_text=raw_text,
        source_type="pdf" if is_pdf else "web",
        content_hash=content_hash(title or "", text),
        # images: 본문 콘텐츠 이미지 후보(다이어그램·차트·스크린샷). render_detail 의 LLM
        # 큐레이션이 이해에 도움 되는 것만 골라 마크다운에 삽입한다(이미지/도식 보존).
        meta=meta,
    )


def _fetch_static(
    url: str,
) -> tuple[str | None, str, list[str], dict[str, str], str | None, str | None, list[dict], bool]:
    """(title, text, links, anchors, error, effective_url, images, is_pdf). httpx + lxml.

    effective_url 은 httpx 의 follow_redirects 가 따라간 최종 URL(resp.url) — dedup 의
    canonical 기준. 실패하면 None. 실패해도 예외 대신 빈 결과를 돌려준다. images 는
    본문 이미지 후보(상대경로는 effective_url 기준으로 절대경로화).
    """
    import httpx

    try:
        with httpx.Client(follow_redirects=True, timeout=30,
                          headers={"User-Agent": _UA}) as client:
            resp = client.get(url)
        if resp.status_code >= 400:
            return FetchStaticResult(None, "", [], {}, f"http {resp.status_code} for {url}", None, [], False, {})

        ctype = resp.headers.get("content-type", "").lower()
        cdisp = resp.headers.get("content-disposition", "").lower()
        if (
            "application/pdf" in ctype
            or "application/x-pdf" in ctype
            or "application/vnd.pdf" in ctype
            or "application/acrobat" in ctype
            or "text/pdf" in ctype
            or ".pdf" in cdisp
            or str(resp.url).lower().split("?", 1)[0].endswith(".pdf")
            or resp.content.startswith(b"%PDF-")
        ):
            from .pdf import extract_pdf_bytes

            fallback = str(resp.url).split("/")[-1].split("?")[0]
            pdf_res = extract_pdf_bytes(
                resp.content, url=str(resp.url), fallback_title=fallback
            )
            title, text, links, anchors, perr, images = pdf_res[:6]
            biblio = getattr(pdf_res, "biblio", None) or (pdf_res[6] if len(pdf_res) > 6 and isinstance(pdf_res[6], dict) else {})
            parser_info = {
                "pdf_parser_requested": getattr(pdf_res, "parser_requested", "pypdf"),
                "pdf_parser_used": getattr(pdf_res, "parser_used", "pypdf"),
                "pdf_parser_fallback": bool(getattr(pdf_res, "parser_fallback", False)),
            }
            if getattr(pdf_res, "parser_fallback_reason", None):
                parser_info["pdf_parser_fallback_reason"] = getattr(pdf_res, "parser_fallback_reason")
            return FetchStaticResult(title, text, links, anchors, perr, str(resp.url), images, True, biblio, parser_info=parser_info)

        title, text, links, anchors, perr, images = _extract_html(
            resp.text, base_url=str(resp.url))
        return FetchStaticResult(title, text, links, anchors, perr, str(resp.url), images, False, {})
    except Exception as e:  # noqa: BLE001
        return FetchStaticResult(None, "", [], {}, f"fetch failed: {e}", None, [], False, {})


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

    # 테이블 요소는 데이터 누락 및 왜곡을 방지하기 위해 마크다운 테이블로 사전 변환하여 보존
    _format_html_tables_to_markdown(tree)

    body_nodes = tree.xpath("//body")
    root_node = body_nodes[0] if body_nodes else tree
    text_pieces = list(root_node.itertext())
    text = "\n\n".join(
        chunk.strip()
        for chunk in "\n".join(t for t in text_pieces if t).split("\n\n")
        if chunk.strip()
    )
    if not text:
        text = " ".join(t.strip() for t in tree.xpath("//text()") if t.strip())
    return (title[:200] if title else None), text, links, anchors, None, images


def _format_html_tables_to_markdown(tree) -> None:
    """HTML <table> 태그들을 마크다운 테이블 텍스트로 변환하여 구조와 데이터를 온전히 보존.

    테이블 내 미디어 제거 허용 정책에 따라, 표 셀 내부의 이미지·아이콘·동영상 등
    미디어 태그는 제거하고 순수 데이터와 텍스트만 보존한다.
    """
    for tbl in list(tree.xpath("//table")):
        # 테이블 내부 미디어 요소(img, svg, video, audio, iframe, canvas, picture 등) 제거 (tail 텍스트 보존)
        for media in list(tbl.xpath(".//img | .//svg | .//video | .//audio | .//iframe | .//canvas | .//picture")):
            parent = media.getparent()
            if parent is not None:
                if media.tail:
                    prev = media.getprevious()
                    if prev is not None:
                        prev.tail = (prev.tail or "") + media.tail
                    else:
                        parent.text = (parent.text or "") + media.tail
                parent.remove(media)

        rows: list[list[str]] = []
        caption_txt = ""
        caps = tbl.xpath(".//caption")
        if caps:
            caption_txt = " ".join(caps[0].text_content().split())

        for tr in tbl.xpath(".//tr"):
            cells = tr.xpath("./th | ./td")
            if not cells:
                continue
            row = [" ".join(c.text_content().split()).replace("|", "\\|") for c in cells]
            if any(row):
                rows.append(row)

        if not rows:
            if tbl.getparent() is not None:
                tbl.getparent().remove(tbl)
            continue

        max_cols = max(len(r) for r in rows)
        if max_cols == 0:
            if tbl.getparent() is not None:
                tbl.getparent().remove(tbl)
            continue

        norm_rows = [r + [""] * (max_cols - len(r)) for r in rows]
        md_lines: list[str] = []
        if caption_txt:
            md_lines.append(f"**[Table: {caption_txt}]**")

        header = norm_rows[0]
        md_lines.append("| " + " | ".join(header) + " |")
        md_lines.append("| " + " | ".join(["---"] * max_cols) + " |")
        for r in norm_rows[1:]:
            md_lines.append("| " + " | ".join(r) + " |")

        md_table_text = "\n\n" + "\n".join(md_lines) + "\n\n"

        parent = tbl.getparent()
        if parent is not None:
            prev = tbl.getprevious()
            if prev is not None:
                prev.tail = (prev.tail or "") + md_table_text
            else:
                parent.text = (parent.text or "") + md_table_text
            parent.remove(tbl)


def _collect_images(tree, og_image: str | None) -> list[dict]:
    """본문 콘텐츠 이미지 후보를 휴리스틱으로 선별 — [{url, alt, caption}].

    명백한 장식/추적/UI 이미지(로고·아이콘·아바타·광고·1x1 픽셀·sprite) 및
    테이블(표) 내부 미디어는 여기서 거르고, 최종 '이해에 도움 되는가'는
    render_detail 의 LLM 큐레이션이 한 번 더 판단한다.
    상대경로는 _extract_html 에서 이미 절대경로화됨. og:image 는 대표 이미지로 합류시킨다.
    """
    out: list[dict] = []
    seen: set[str] = set()

    def _consider(url: str, alt: str, caption: str, *, w=None, h=None,
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

    # 테이블 내부 이미지는 본문 주요 콘텐츠 이미지 후보에서 제외
    for img in tree.xpath("//img[not(ancestor::table)]"):
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


def _fetch_scrapling(
    url: str,
) -> tuple[str | None, str, list[str], dict[str, str], list[dict], bool]:
    """Scrapling Fetcher (curl-cffi + browserforge 스텔스 헤더). 브라우저 불필요.

    정적 httpx UA 를 403 으로 막는 봇차단(예: openai.com)을, 브라우저 지문에
    가까운 헤더/TLS 로 우회. raw HTML 은 _extract_html 로 동일하게 파싱 →
    title/본문/링크(1홉 후보)/앵커/이미지 추출 일관성 유지. 미설치/실패 시 빈 결과.
    """
    try:
        from scrapling.fetchers import Fetcher

        page = Fetcher.get(url, stealthy_headers=True, timeout=30)
        status = getattr(page, "status", 200)
        if status and status >= 400:
            return None, "", [], {}, [], False

        body = getattr(page, "body", None)
        ctype = str(
            getattr(page, "content_type", "")
            or (getattr(page, "headers", {}) or {}).get("content-type", "")
        ).lower()
        cdisp = str(
            (getattr(page, "headers", {}) or {}).get("content-disposition", "")
        ).lower()
        if (
            "application/pdf" in ctype
            or "application/x-pdf" in ctype
            or "application/vnd.pdf" in ctype
            or "application/acrobat" in ctype
            or "text/pdf" in ctype
            or ".pdf" in cdisp
            or (body and isinstance(body, bytes) and body.startswith(b"%PDF-"))
            or url.lower().split("?", 1)[0].endswith(".pdf")
        ):
            from .pdf import extract_pdf_bytes

            raw_bytes = body if isinstance(body, bytes) else str(body).encode("latin1", errors="ignore")
            pdf_res = extract_pdf_bytes(
                raw_bytes, url=url, fallback_title=url.split("/")[-1].split("?")[0]
            )
            title, text, links, anchors, _, images = pdf_res[:6]
            biblio = getattr(pdf_res, "biblio", None) or (pdf_res[6] if len(pdf_res) > 6 and isinstance(pdf_res[6], dict) else {})
            parser_info = {
                "pdf_parser_requested": getattr(pdf_res, "parser_requested", "pypdf"),
                "pdf_parser_used": getattr(pdf_res, "parser_used", "pypdf"),
                "pdf_parser_fallback": bool(getattr(pdf_res, "parser_fallback", False)),
            }
            if getattr(pdf_res, "parser_fallback_reason", None):
                parser_info["pdf_parser_fallback_reason"] = getattr(pdf_res, "parser_fallback_reason")
            return FetchScraplingResult(title, text, links, anchors, images, True, biblio, parser_info=parser_info)

        html = getattr(page, "html_content", "") or ""
        title, text, links, anchors, _, images = _extract_html(str(html), base_url=url)
        return FetchScraplingResult(title, text, links, anchors, images, False, {})
    except Exception:  # noqa: BLE001
        return FetchScraplingResult(None, "", [], {}, [], False, {})


def render_html_cdp(
    url: str,
    *,
    wait_seconds: float = 2.5,
    click_tab_label: str | None = None,
    interaction_timeout_seconds: float = 12.0,
    post_click_wait_seconds: float = 1.5,
) -> str:
    """Scrapling과 시스템 Chromium으로 렌더링된 최종 HTML을 반환한다.

    일반 웹 fallback과 사이트별 구조 탐색이 같은 브라우저 경계를 공유하도록 HTML 획득만
    담당한다. ``click_tab_label``이 있으면 렌더링된 ``role=tab`` 요소 중 텍스트가 정확히
    일치하는 탭을 선택한 뒤 최종 DOM을 반환한다. 브라우저 미설치·렌더링 실패는 빈
    문자열로 반환하며, 호출자가 성공/실패 정책을 결정한다.
    """
    try:
        from scrapling.fetchers import DynamicFetcher

        interaction_failed = False

        def _page_action(page) -> None:
            nonlocal interaction_failed
            if wait_seconds > 0:
                page.wait_for_timeout(int(wait_seconds * 1000))
            if click_tab_label:
                try:
                    target = page.get_by_role("tab", name=click_tab_label, exact=True)
                    if target.count() == 0:
                        return
                    target.click(timeout=int(interaction_timeout_seconds * 1000))
                except Exception:
                    interaction_failed = True
                    raise
                if post_click_wait_seconds > 0:
                    page.wait_for_timeout(int(post_click_wait_seconds * 1000))

        kwargs = {
            "headless": True,
            "useragent": BROWSER_USER_AGENT,
            "timeout": max(
                30_000,
                int(
                    (wait_seconds + interaction_timeout_seconds + post_click_wait_seconds)
                    * 1000
                ),
            ),
            "page_action": _page_action,
            "retries": 1,
            "google_search": False,
            "disable_resources": True,
            "block_ads": True,
            "extra_flags": [
                "--no-sandbox",
                "--disable-gpu",
                "--disable-dev-shm-usage",
                "--ignore-certificate-errors",
                "--ignore-ssl-errors",
                "--allow-insecure-localhost",
            ],
        }
        executable = _system_chromium_executable()
        if executable:
            kwargs["executable_path"] = executable
        response = DynamicFetcher.fetch(url, **kwargs)
        if interaction_failed:
            return ""
        return response.body.decode("utf-8", errors="replace")
    except Exception:  # noqa: BLE001
        return ""


def _system_chromium_executable() -> str | None:
    """Linux/macOS의 시스템 Chromium 계열 브라우저를 찾는다."""
    for command in ("chromium", "chromium-browser", "google-chrome", "google-chrome-stable"):
        executable = shutil.which(command)
        if executable:
            return executable
    for candidate in (
        Path("/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"),
        Path("/Applications/Chromium.app/Contents/MacOS/Chromium"),
    ):
        if candidate.is_file():
            return str(candidate)
    return None


def _fetch_cdp(url: str) -> tuple[str | None, str, list[str], dict[str, str], list[dict]]:
    """Scrapling으로 시스템 Chromium을 제어해 실제 렌더링(브라우저 필요).

    JS SPA(해시 라우팅 등, 예: uniclawbench.github.io)는 static/scrapling(curl-cffi, 무JS)
    으로는 빈 셸만 받아온다 — 진짜 브라우저 실행이 필요한 최후수단으로
    ``scrapling[fetchers]``와 apt 설치된 chromium 바이너리를 재사용한다.
    미설치/실패 시 빈 결과(체인의 다음 단계 없음 → thin-guard 가 최종 실패 처리).
    """
    try:
        html = render_html_cdp(url)
        if not html:
            return None, "", [], {}, []
        title, text, links, anchors, _, images = _extract_html(str(html), base_url=url)
        return title, text, links, anchors, images
    except Exception:  # noqa: BLE001
        return None, "", [], {}, []
