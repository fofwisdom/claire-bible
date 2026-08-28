"""국가법령정보센터(law.go.kr) 어댑터.

law.go.kr 은 본문 조문을 정적 HTML 본문에 바로 담지 않고
  1) 외곽 페이지(`/법령/<명칭>`, `/행정규칙/<명칭>` 등)가 iframe 으로 `*InfoP.do` 를 로드하고,
  2) `*InfoP.do` 가 브라우저 JS(AJAX)로 `*InfoR.do` 를 호출해 본문을 주입하는 2중 구조를 갖는다.

이 어댑터는:
  - 외곽 페이지의 iframe `*InfoP.do` 경로를 파싱하여 본문 렌더러 `*InfoR.do` 로 변환
  - 숨겨진 입력필드(lsNm, admRulNm 등)에서 정확한 법령/규칙명을 제목으로 추출
  - 상단 툴바(판례·연혁 등) 및 팝업 레이어 등 UI 노이즈를 제거하여 순수 조문/본문을 정적으로 확보한다.
"""

from __future__ import annotations

import re
import urllib.parse
from lxml import html as lh

_LAW_HOSTS = ("law.go.kr", "www.law.go.kr")
_UA = (
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0 Safari/537.36"
)


def _is_law_kr_url(url: str) -> bool:
    """URL 호스트가 law.go.kr 인지 판별."""
    try:
        parts = urllib.parse.urlsplit(url.strip())
        host = (parts.hostname or parts.netloc).lower()
        return host in _LAW_HOSTS or host.endswith(".law.go.kr")
    except Exception:  # noqa: BLE001
        return False


def try_law_kr(
    url: str,
) -> tuple[str | None, str, list[str], dict[str, str], list[dict]] | None:
    """law.go.kr URL 이면 본문 렌더러(*InfoR.do)를 역추적해 정적 콘텐츠를 추출.

    반환: (title, text, links, anchors, images) 또는 대상 아닐 시 None.
    """
    if not _is_law_kr_url(url):
        return None

    import httpx

    parts = urllib.parse.urlsplit(url.strip())
    # HTTP 는 JS redirect 로 날아갈 수 있으므로 HTTPS 로 승격
    target_url = urllib.parse.urlunsplit(("https", parts.netloc, parts.path, parts.query, ""))

    try:
        with httpx.Client(follow_redirects=True, timeout=25, headers={"User-Agent": _UA}) as client:
            # 1) 이미 *InfoR.do 리더 URL 인 경우 바로 본문 추출
            if "InfoR.do" in target_url:
                resp = client.get(target_url)
                if resp.status_code < 400:
                    return _extract_from_reader(resp.text, str(resp.url))

            # 2) *InfoP.do 팝업 URL 인 경우 *InfoR.do 로 변환하여 요청
            if "InfoP.do" in target_url:
                r_url = re.sub(r"([a-zA-Z]+)InfoP\.do", r"\1InfoR.do", target_url)
                resp = client.get(r_url)
                if resp.status_code < 400:
                    res = _extract_from_reader(resp.text, str(resp.url))
                    if res:
                        return res

            # 3) 외곽 페이지(예: /법령/<법령명>, /행정규칙/<규칙명> 등) 조회
            resp = client.get(target_url)
            if resp.status_code >= 400:
                return None

            # JS 리다이렉트 (location.href = "...") 처리
            js_loc = re.search(r"location\.href\s*=\s*['\"]([^'\"]+)['\"]", resp.text)
            if js_loc:
                next_url = urllib.parse.urljoin(str(resp.url), js_loc.group(1))
                if next_url != target_url:
                    return try_law_kr(next_url)

            try:
                tree = lh.fromstring(resp.text)
            except Exception:  # noqa: BLE001
                return None

            outer_title = None
            t = tree.xpath("//title/text()")
            if t and t[0].strip():
                outer_title = t[0].strip()

            iframes = tree.xpath("//iframe/@src")
            for ifr in iframes:
                ifr_abs = urllib.parse.urljoin(str(resp.url), ifr)
                # *InfoP.do -> *InfoR.do 변환으로 본문 리더 직접 접근
                ifr_r = re.sub(r"([a-zA-Z]+)InfoP\.do", r"\1InfoR.do", ifr_abs)
                r_ifr = client.get(ifr_r)
                if r_ifr.status_code < 400:
                    res = _extract_from_reader(r_ifr.text, ifr_abs, fallback_title=outer_title)
                    if res and len(res[1]) >= 100:
                        return res

            return None
    except Exception:  # noqa: BLE001
        return None


def _extract_from_reader(
    html_text: str, base_url: str, fallback_title: str | None = None
) -> tuple[str | None, str, list[str], dict[str, str], list[dict]] | None:
    """law.go.kr 본문 리더 HTML 에서 불필요한 UI 툴바 제거 및 본문/제목 추출."""
    from .web import _extract_html

    try:
        tree = lh.fromstring(html_text)
    except Exception:  # noqa: BLE001
        return None

    # 상단 버튼 툴바(판례/연혁/규제/한눈보기 등) 및 팝업 레이어 제거
    for bad in tree.xpath(
        "//ul[contains(@class, 'cont_icon')] | "
        "//div[contains(@class, 'byl_pop')] | "
        "//div[contains(@class, 'fileSaveLayer')]"
    ):
        if bad.getparent() is not None:
            bad.getparent().remove(bad)

    # 법령명 / 규칙명 hidden 필드 우선 탐색
    title = None
    hidden_names = tree.xpath(
        "//input[@id='lsNm' or @id='admRulNm' or @id='ordinNm' or "
        "@id='precNm' or @id='detcNm' or @id='expcNm' or @name='lsNm']/@value"
    )
    if hidden_names and hidden_names[0].strip():
        title = hidden_names[0].strip()

    cleaned_html = lh.tostring(tree, encoding="unicode")
    t_extracted, text, links, anchors, _, images = _extract_html(cleaned_html, base_url=base_url)

    final_title = title or t_extracted or fallback_title
    if not text or len(text) < 50:
        return None
    return final_title, text, links, anchors, images
