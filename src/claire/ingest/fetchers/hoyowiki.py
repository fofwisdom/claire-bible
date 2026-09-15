"""HoYoWiki (wiki.hoyolab.com) 공식 위키 수집기 및 웹 어댑터.

원신(Genshin Impact), 붕괴: 스타레일(Honkai: Star Rail), 젠레스 존 제로(Zenless Zone Zero),
붕괴3rd(Honkai Impact 3rd) 등 HoYoverse 공식 게임 위키 데이터를 sg-wiki-api.hoyolab.com
WAPI를 통해 직접 역추적하여 온톨로지 적재에 최적화된 마크다운 Document로 변환한다.
"""

from __future__ import annotations

import json
import logging
import re
import time
import urllib.parse
from typing import Any, Iterator

import httpx
from lxml import html as lh

from ...config import get_settings
from ...extract.table_budget import slice_document_text
from ...ontology.base import Document
from ..normalize import canonicalize_url, content_hash
from .base import BaseFetcher, BaseWebAdapter, FetchError, WebAdapterResult
from .http import SafeHttpClient
from .http_policy import BROWSER_USER_AGENT
from ..registry import register_fetcher, register_web_adapter

log = logging.getLogger(__name__)

_API_BASE = "https://sg-wiki-api.hoyolab.com/hoyowiki/wapi"

_GAME_NAMES = {
    "genshin": "원신 (Genshin Impact)",
    "hsr": "붕괴: 스타레일 (Honkai: Star Rail)",
    "zzz": "젠레스 존 제로 (Zenless Zone Zero)",
    "honkai3rd": "붕괴3rd (Honkai Impact 3rd)",
    "tot": "미결사건부 (Tears of Themis)",
}

_URL_PATTERN = re.compile(
    r"https?://(?:wiki\.)?hoyolab\.com/(?:pc|m)/([a-zA-Z0-9_-]+)/(?:entry|character|entry_preview)/(\d+)",
    re.IGNORECASE,
)

_SEARCH_URL_PATTERN = re.compile(
    r"https?://(?:wiki\.)?hoyolab\.com/(?:pc|m)/([a-zA-Z0-9_-]+)/aggregate",
    re.IGNORECASE,
)


def parse_hoyowiki_url(url: str) -> tuple[str | None, str | None, str | None]:
    """HoYoWiki URL에서 (game, entry_id, lang)을 파싱한다.

    지원 형태:
      - https://wiki.hoyolab.com/pc/genshin/entry/11702
      - https://wiki.hoyolab.com/m/hsr/entry/1919?lang=ko-kr
      - https://wiki.hoyolab.com/pc/zzz/character/29
      - https://wiki.hoyolab.com/pc/genshin/entry_preview/1
    """
    cleaned = (url or "").strip()
    m = _URL_PATTERN.search(cleaned)
    lang = None

    try:
        parsed = urllib.parse.urlsplit(cleaned)
        qs = urllib.parse.parse_qs(parsed.query)
        if "lang" in qs and qs["lang"]:
            lang = qs["lang"][0].strip()
    except Exception:
        pass

    if m:
        game = m.group(1).lower()
        entry_id = m.group(2)
        return game, entry_id, lang

    return None, None, lang


class HoYoWikiClient:
    """sg-wiki-api.hoyolab.com 클라이언트."""

    def __init__(self, default_lang: str = "ko-kr", timeout: float = 25.0):
        self.default_lang = default_lang
        self.timeout = timeout
        self.safe_http = SafeHttpClient(timeout=timeout)

    def _headers(self, game: str, lang: str | None = None, referer: str | None = None) -> dict[str, str]:
        headers = {
            "User-Agent": BROWSER_USER_AGENT,
            "Accept": "application/json, text/plain, */*",
            "x-rpc-language": lang or self.default_lang,
            "x-rpc-wiki_app": game,
            "x-rpc-client_type": "4",
            "Referer": referer or f"https://wiki.hoyolab.com/pc/{game}/home",
        }
        return headers

    def get_menus(self, game: str, lang: str | None = None) -> list[dict[str, Any]]:
        """게임의 전체 메뉴 트리 조회."""
        url = f"{_API_BASE}/get_menus"
        headers = self._headers(game, lang=lang)
        self.safe_http._resolve_and_check(url)
        with httpx.Client(timeout=self.timeout, headers=headers) as client:
            resp = client.get(url)
            if resp.status_code >= 400:
                raise FetchError(f"HoYoWiki get_menus failed: HTTP {resp.status_code}")
            data = resp.json()
            if data.get("retcode") != 0:
                raise FetchError(f"HoYoWiki get_menus error: {data.get('message')}")
            return data.get("data", {}).get("menus", [])

    def get_menu_filters(self, menu_id: str | int, game: str, lang: str | None = None) -> list[dict[str, Any]]:
        """메뉴 필터 목록 조회."""
        url = f"{_API_BASE}/get_menu_filters?menu_id={menu_id}"
        headers = self._headers(game, lang=lang)
        self.safe_http._resolve_and_check(url)
        with httpx.Client(timeout=self.timeout, headers=headers) as client:
            resp = client.get(url)
            if resp.status_code >= 400:
                raise FetchError(f"HoYoWiki get_menu_filters failed: HTTP {resp.status_code}")
            data = resp.json()
            if data.get("retcode") != 0:
                raise FetchError(f"HoYoWiki get_menu_filters error: {data.get('message')}")
            return data.get("data", {}).get("filters", [])

    def get_entry_list(
        self,
        game: str,
        menu_id: str | int,
        page_num: int = 1,
        page_size: int = 30,
        filters: list[Any] | None = None,
        lang: str | None = None,
    ) -> dict[str, Any]:
        """특정 메뉴의 엔트리 목록 페이지네이션 조회."""
        url = f"{_API_BASE}/get_entry_page_list"
        headers = self._headers(game, lang=lang)
        headers["Content-Type"] = "application/json"
        payload = {
            "filters": filters or [],
            "menu_id": str(menu_id),
            "page_num": int(page_num),
            "page_size": min(int(page_size), 30),
            "use_es": True,
        }
        self.safe_http._resolve_and_check(url)
        with httpx.Client(timeout=self.timeout, headers=headers) as client:
            resp = client.post(url, json=payload)
            if resp.status_code >= 400:
                raise FetchError(f"HoYoWiki get_entry_page_list failed: HTTP {resp.status_code}")
            data = resp.json()
            if data.get("retcode") != 0:
                raise FetchError(f"HoYoWiki get_entry_page_list error: {data.get('message')}")
            return data.get("data", {})

    def get_entry(self, entry_id: str | int, game: str, lang: str | None = None) -> dict[str, Any]:
        """단건 엔트리 상세 정보 조회."""
        url = f"{_API_BASE}/entry_page?entry_page_id={entry_id}"
        referer = f"https://wiki.hoyolab.com/pc/{game}/entry/{entry_id}"
        headers = self._headers(game, lang=lang, referer=referer)
        self.safe_http._resolve_and_check(url)
        with httpx.Client(timeout=self.timeout, headers=headers) as client:
            resp = client.get(url)
            if resp.status_code >= 400:
                raise FetchError(f"HoYoWiki entry_page failed: HTTP {resp.status_code}")
            data = resp.json()
            if data.get("retcode") != 0:
                raise FetchError(f"HoYoWiki entry_page error: {data.get('message')}")
            page = data.get("data", {}).get("page")
            if not page:
                raise FetchError(f"HoYoWiki entry_page {entry_id} not found")
            return page

    def search(self, keyword: str, game: str, lang: str | None = None) -> list[dict[str, Any]]:
        """키워드 검색."""
        encoded_kw = urllib.parse.quote(keyword)
        url = f"{_API_BASE}/search?keyword={encoded_kw}"
        headers = self._headers(game, lang=lang)
        self.safe_http._resolve_and_check(url)
        with httpx.Client(timeout=self.timeout, headers=headers) as client:
            resp = client.get(url)
            if resp.status_code >= 400:
                raise FetchError(f"HoYoWiki search failed: HTTP {resp.status_code}")
            data = resp.json()
            if data.get("retcode") != 0:
                raise FetchError(f"HoYoWiki search error: {data.get('message')}")
            return data.get("data", {}).get("list", [])


def resolve_embedded_entries(
    text: str, game: str
) -> tuple[str, list[str], dict[str, str], list[dict[str, Any]]]:
    """HoYoWiki의 $[{"ep_id": 123, "name": "..."}]$ 표기를 마크다운 링크로 변환하고 메타데이터를 추출한다."""
    links: list[str] = []
    anchors: dict[str, str] = {}
    images: list[dict[str, Any]] = []

    def _repl(m: re.Match) -> str:
        raw = m.group(1)
        try:
            d = json.loads(raw)
            ep_id = d.get("ep_id")
            name = d.get("name") or d.get("title") or (f"Entry {ep_id}" if ep_id else "")
            amount = d.get("amount") or d.get("count")
            icon = d.get("icon")
            if icon and icon.startswith(("http://", "https://")):
                images.append({"url": icon, "alt": f"{name} 아이콘", "src": icon})
            amt_str = f" x{amount}" if amount and int(amount) > 0 else ""
            if ep_id and game:
                u = f"https://wiki.hoyolab.com/pc/{game}/entry/{ep_id}"
                links.append(u)
                if name:
                    anchors[u] = name
                return f"[{name}]({u}){amt_str}"
            return f"{name}{amt_str}" if name else ""
        except Exception:
            return m.group(0)

    transformed = re.sub(r"\$\[(\{.*?\})\]\$", _repl, text)
    return transformed, links, anchors, images


def clean_html_to_markdown(raw_html: str, base_game: str | None = None) -> tuple[str, list[dict[str, Any]], list[str]]:
    """HTML을 정제된 마크다운 텍스트로 변환하고 이미지 및 링크를 추출한다."""
    if not raw_html or not raw_html.strip():
        return "", [], []

    images: list[dict[str, Any]] = []
    links: list[str] = []

    # <custom-image url="..." align="..."> 태그 변환
    def _replace_custom_image(m: re.Match) -> str:
        tag = m.group(0)
        u_match = re.search(r'url=["\']([^"\']+)["\']', tag)
        if u_match:
            img_url = u_match.group(1).strip()
            images.append({"url": img_url, "alt": "image", "src": img_url})
            return f"\n\n![image]({img_url})\n\n"
        return ""

    text = re.sub(r"<custom-image[^>]*>(?:</custom-image>)?", _replace_custom_image, raw_html, flags=re.IGNORECASE)

    try:
        tree = lh.fragment_fromstring(text, create_parent="div")
    except Exception:
        clean = re.sub(r"<[^>]+>", " ", text)
        clean = re.sub(r"\s+", " ", clean).strip()
        return clean, images, links

    # 링크 추출
    for a in tree.xpath(".//a/@href"):
        href = str(a).strip()
        if href.startswith(("http://", "https://")):
            links.append(href)

    # 이미지 태그 수집
    for img in tree.xpath(".//img"):
        src = img.get("src") or ""
        alt = img.get("alt") or ""
        if src.startswith(("http://", "https://")):
            images.append({"url": src, "alt": alt, "src": src})
        parent = img.getparent()
        if parent is not None:
            prev = img.getprevious()
            md_img = f" ![{alt}]({src}) "
            if prev is not None:
                prev.tail = (prev.tail or "") + md_img + (img.tail or "")
            else:
                parent.text = (parent.text or "") + md_img + (img.tail or "")
            parent.remove(img)

    # 테이블 마크다운 변환
    for tbl in list(tree.xpath(".//table")):
        rows: list[list[str]] = []
        for tr in tbl.xpath(".//tr"):
            cells = tr.xpath("./th | ./td")
            if not cells:
                continue
            row_vals = [" ".join(c.text_content().split()) for c in cells]
            rows.append(row_vals)

        if rows:
            max_cols = max(len(r) for r in rows)
            norm_rows = [r + [""] * (max_cols - len(r)) for r in rows]
            header = norm_rows[0]
            separator = ["---"] * max_cols
            md_lines = [
                "| " + " | ".join(header) + " |",
                "| " + " | ".join(separator) + " |",
            ]
            for data_row in norm_rows[1:]:
                md_lines.append("| " + " | ".join(data_row) + " |")
            md_table = "\n\n" + "\n".join(md_lines) + "\n\n"

            parent = tbl.getparent()
            if parent is not None:
                prev = tbl.getprevious()
                if prev is not None:
                    prev.tail = (prev.tail or "") + md_table + (tbl.tail or "")
                else:
                    parent.text = (parent.text or "") + md_table + (tbl.tail or "")
                parent.remove(tbl)

    # <br> 태그 처리
    for br in tree.xpath(".//br"):
        parent = br.getparent()
        if parent is not None:
            prev = br.getprevious()
            if prev is not None:
                prev.tail = (prev.tail or "") + "\n" + (br.tail or "")
            else:
                parent.text = (parent.text or "") + "\n" + (br.tail or "")
            parent.remove(br)

    # 단락 및 제목 블록 줄바꿈
    for tag_name in ("h1", "h2", "h3", "h4", "h5", "h6", "p", "div", "li"):
        for elem in tree.xpath(f".//{tag_name}"):
            elem.tail = (elem.tail or "") + "\n"

    raw_text = tree.text_content()
    clean_text = re.sub(r"\n{3,}", "\n\n", raw_text).strip()
    return clean_text, images, links


def format_entry_to_markdown(
    page: dict[str, Any], game: str
) -> tuple[str, str, list[str], dict[str, str], list[dict[str, Any]]]:
    """HoYoWiki 엔트리 페이지 JSON을 완전한 마크다운 문서로 포맷팅한다.

    반환값: (title, text, links, anchors, images)
    """
    name = (page.get("name") or "").strip()
    game_display = _GAME_NAMES.get(game, game.upper())
    title = f"{name} - {game_display}"

    sections: list[str] = []
    all_images: list[dict[str, Any]] = []
    all_links: list[str] = []
    anchors: dict[str, str] = {}

    icon_url = page.get("icon_url")
    if icon_url and icon_url.startswith(("http://", "https://")):
        all_images.append({"url": icon_url, "alt": f"{name} 아이콘", "src": icon_url})

    header_img_url = page.get("header_img_url")
    if header_img_url and header_img_url.startswith(("http://", "https://")):
        all_images.append({"url": header_img_url, "alt": f"{name} 헤더", "src": header_img_url})

    sections.append(f"# {title}\n")

    # 기본 개요 설명
    desc_html = page.get("desc") or ""
    if desc_html:
        desc_md, d_imgs, d_links = clean_html_to_markdown(desc_html, base_game=game)
        if desc_md:
            sections.append(f"## 개요\n\n{desc_md}\n")
        all_images.extend(d_imgs)
        all_links.extend(d_links)

    # 필터 속성 정보 (성급, 원소, 운명의 길, 소속 진영 등)
    filter_values = page.get("filter_values") or {}
    if isinstance(filter_values, dict) and filter_values:
        meta_items: list[str] = []
        for f_data in filter_values.values():
            if not isinstance(f_data, dict):
                continue
            f_key_info = f_data.get("key") or {}
            key_text = f_key_info.get("text") or ""
            vals = f_data.get("values") or []
            if key_text and vals:
                meta_items.append(f"- **{key_text}**: {', '.join(vals)}")
        if meta_items:
            sections.append("## 분류 및 특성\n\n" + "\n".join(meta_items) + "\n")

    # 모듈 순회
    modules = page.get("modules") or []
    for mod in modules:
        mod_name = (mod.get("name") or "").strip()
        components = mod.get("components") or []

        for comp in components:
            cid = comp.get("component_id") or ""
            raw_data = comp.get("data")
            if not raw_data:
                continue

            parsed_data: Any = raw_data
            if isinstance(raw_data, str) and (raw_data.startswith("{") or raw_data.startswith("[")):
                try:
                    parsed_data = json.loads(raw_data)
                except Exception:
                    parsed_data = raw_data

            # 1. 기본 속성 / 스탯 테이블 (baseInfo)
            if cid == "baseInfo":
                b_list = parsed_data.get("list", []) if isinstance(parsed_data, dict) else []
                if b_list:
                    sec_title = mod_name or "기본 정보"
                    b_lines = [f"## {sec_title}\n"]
                    b_rows: list[str] = []
                    for it in b_list:
                        k = (it.get("key") or "").strip()
                        v_list = it.get("value") or []
                        v_str = ", ".join(clean_html_to_markdown(str(v))[0] for v in v_list)
                        if k and v_str:
                            b_rows.append(f"- **{k}**: {v_str}")
                    if b_rows:
                        b_lines.append("\n".join(b_rows))
                        sections.append("\n".join(b_lines) + "\n")

            # 2. 스킬 및 특성 (talent, agent_talent, trace)
            elif cid in ("talent", "agent_talent", "trace"):
                sec_title = mod_name or "스킬 및 특성"
                t_list = parsed_data.get("list", []) if isinstance(parsed_data, dict) else []
                if t_list:
                    t_lines = [f"## {sec_title}\n"]
                    for it in t_list:
                        s_name = it.get("title") or it.get("name") or it.get("key") or ""
                        s_desc = it.get("desc") or ""
                        s_icon = it.get("icon_url")
                        if s_icon and s_icon.startswith(("http://", "https://")):
                            all_images.append({"url": s_icon, "alt": f"{s_name} 아이콘", "src": s_icon})

                        d_clean, s_imgs, s_lnks = clean_html_to_markdown(s_desc, base_game=game)
                        all_images.extend(s_imgs)
                        all_links.extend(s_lnks)

                        t_lines.append(f"### {s_name}\n")
                        if d_clean:
                            t_lines.append(f"{d_clean}\n")

                        # 스킬 레벨별 수치 속성 테이블
                        attrs = it.get("attributes") or []
                        if attrs and isinstance(attrs, list):
                            lvl_row = None
                            other_rows = []
                            for a in attrs:
                                a_key = a.get("key") or ""
                                a_vals = a.get("values") or []
                                if a_key.lower() == "level":
                                    lvl_row = a_vals
                                else:
                                    other_rows.append((a_key, a_vals))

                            if lvl_row and other_rows:
                                num_levels = len(lvl_row)
                                table_head = ["속성"] + lvl_row
                                sep = ["---"] * len(table_head)
                                md_t = ["| " + " | ".join(table_head) + " |", "| " + " | ".join(sep) + " |"]
                                for r_key, r_vals in other_rows:
                                    padded = r_vals + [""] * (num_levels - len(r_vals))
                                    md_t.append("| " + " | ".join([r_key] + padded) + " |")
                                t_lines.append("\n" + "\n".join(md_t) + "\n")

                    sections.append("\n".join(t_lines) + "\n")

            # 3. 운명의 자리 / 성혼 / 형상 시네마 (summaryList)
            elif cid == "summaryList":
                sec_title = mod_name or "돌파 특성"
                c_list = parsed_data.get("list", []) if isinstance(parsed_data, dict) else []
                if c_list:
                    c_lines = [f"## {sec_title}\n"]
                    for it in c_list:
                        c_name = it.get("name") or it.get("title") or ""
                        c_desc = it.get("desc") or ""
                        c_icon = it.get("icon_url")
                        if c_icon and c_icon.startswith(("http://", "https://")):
                            all_images.append({"url": c_icon, "alt": f"{c_name} 아이콘", "src": c_icon})
                        d_clean, _, _ = clean_html_to_markdown(c_desc, base_game=game)
                        c_lines.append(f"### {c_name}\n\n{d_clean}\n")
                    sections.append("\n".join(c_lines) + "\n")

            # 4. 돌파 / 승급 재료 (ascension)
            elif cid == "ascension":
                sec_title = mod_name or "돌파 및 승급 재료"
                a_list = parsed_data.get("list", []) if isinstance(parsed_data, dict) else []
                if a_list:
                    a_lines = [f"## {sec_title}\n"]
                    for it in a_list:
                        level_label = it.get("key") or it.get("level") or "단계"
                        materials = it.get("materials") or []
                        mat_descs = []
                        for m_item in materials:
                            if isinstance(m_item, dict):
                                m_name = m_item.get("name") or ""
                                m_cnt = m_item.get("amount") or m_item.get("count") or ""
                                if m_name:
                                    mat_descs.append(f"{m_name} x{m_cnt}")
                            elif isinstance(m_item, str):
                                mat_descs.append(m_item)
                        if mat_descs:
                            a_lines.append(f"- **{level_label}**: {', '.join(mat_descs)}")
                    if len(a_lines) > 1:
                        sections.append("\n".join(a_lines) + "\n")

            # 5. 배경 스토리 및 설정 (story)
            elif cid == "story":
                sec_title = mod_name or "스토리 및 배경"
                s_list = parsed_data.get("list", []) if isinstance(parsed_data, dict) else []
                if s_list:
                    st_lines = [f"## {sec_title}\n"]
                    for it in s_list:
                        st_title = it.get("title") or "스토리"
                        st_desc = it.get("desc") or it.get("text") or ""
                        st_clean, _, _ = clean_html_to_markdown(st_desc, base_game=game)
                        if st_clean:
                            st_lines.append(f"### {st_title}\n\n{st_clean}\n")
                    if len(st_lines) > 1:
                        sections.append("\n".join(st_lines) + "\n")

            # 6. 음성 대사 (voice) - 주요 대사 발췌
            elif cid == "voice":
                v_list = parsed_data.get("list", []) if isinstance(parsed_data, dict) else []
                if v_list:
                    v_lines = [f"## {mod_name or '음성 대사'}\n"]
                    for it in v_list[:15]:
                        v_title = it.get("title") or it.get("name") or "대사"
                        v_desc = it.get("desc") or it.get("text") or ""
                        v_clean, _, _ = clean_html_to_markdown(v_desc, base_game=game)
                        if v_clean:
                            v_lines.append(f"- **{v_title}**: {v_clean}")
                    if len(v_lines) > 1:
                        sections.append("\n".join(v_lines) + "\n")

            # 7. 커스텀 위키 모듈 (customize)
            elif cid == "customize":
                html_body = parsed_data.get("data") if isinstance(parsed_data, dict) else str(parsed_data)
                if html_body and len(html_body.strip()) > 20:
                    c_clean, c_imgs, c_links = clean_html_to_markdown(html_body, base_game=game)
                    all_images.extend(c_imgs)
                    all_links.extend(c_links)
                    if c_clean and len(c_clean.strip()) > 30:
                        sec_title = mod_name or "추천 및 상세 정보"
                        sections.append(f"## {sec_title}\n\n{c_clean}\n")

            # 8. 갤러리 일러스트 (gallery_character)
            elif cid == "gallery_character":
                g_list = parsed_data.get("list", []) if isinstance(parsed_data, dict) else []
                for it in g_list:
                    g_img = it.get("img") or it.get("url")
                    g_desc = it.get("key") or it.get("imgDesc") or f"{name} 일러스트"
                    if g_img and g_img.startswith(("http://", "https://")):
                        all_images.append({"url": g_img, "alt": g_desc, "src": g_img})

    # 연관 위키 링크 정규화
    entry_id = page.get("id") or page.get("entry_page_id")
    canonical_entry_url = f"https://wiki.hoyolab.com/pc/{game}/entry/{entry_id}" if entry_id else ""
    if canonical_entry_url:
        all_links.append(canonical_entry_url)
        anchors[canonical_entry_url] = name

    # 본문 결합 및 임베디드 엔트리($[{"ep_id": ...}]$) 역해소
    raw_full_text = "\n\n".join(s.strip() for s in sections if s.strip())
    resolved_text, emb_links, emb_anchors, emb_imgs = resolve_embedded_entries(raw_full_text, game)
    all_links.extend(emb_links)
    anchors.update(emb_anchors)
    all_images.extend(emb_imgs)

    # 중복 링크 및 이미지 정제
    seen_links: set[str] = set()
    dedup_links: list[str] = []
    for lk in all_links:
        if lk not in seen_links:
            seen_links.add(lk)
            dedup_links.append(lk)

    seen_imgs: set[str] = set()
    dedup_imgs: list[dict[str, Any]] = []
    for im in all_images:
        u = im.get("url")
        if u and u not in seen_imgs:
            seen_imgs.add(u)
            dedup_imgs.append(im)

    return title, resolved_text, dedup_links[:50], anchors, dedup_imgs[:20]


def fetch_hoyowiki(url: str, *, full_content: bool = False, lang: str | None = None) -> Document:
    """HoYoWiki URL을 수집하여 정규화된 Document를 생성한다."""
    game, entry_id, url_lang = parse_hoyowiki_url(url)
    effective_lang = lang or url_lang or "ko-kr"

    if not game or not entry_id:
        raise FetchError(f"Invalid HoYoWiki entry URL format: {url}")

    client = HoYoWikiClient(default_lang=effective_lang)
    try:
        page = client.get_entry(entry_id, game=game, lang=effective_lang)
    except Exception as e:
        raise FetchError(f"Failed to fetch HoYoWiki entry {entry_id} for {game}: {e}") from e

    title, text, links, anchors, images = format_entry_to_markdown(page, game)

    settings = get_settings()
    budget = 0 if full_content else settings.raw_char_budget
    raw_text, is_truncated, orig_chars, raw_chars = slice_document_text(
        text or "", budget, strategy=settings.slicing_strategy
    )

    canonical = f"https://wiki.hoyolab.com/pc/{game}/entry/{entry_id}"
    anchor_pairs = [{"url": u, "anchor": anchors.get(u, "")} for u in links[:50]]

    meta: dict[str, Any] = {
        "game": game,
        "game_name": _GAME_NAMES.get(game, game),
        "entry_page_id": entry_id,
        "lang": effective_lang,
        "fetch_via": "hoyowiki",
        "effective_url": canonical,
        "links": links[:50],
        "link_anchors": anchor_pairs,
        "images": images,
        "raw_truncated": is_truncated,
        "orig_chars": orig_chars,
        "raw_chars": raw_chars,
    }

    return Document(
        url=url,
        canonical_url=canonicalize_url(canonical),
        title=title,
        author="HoYoWiki",
        published_at=None,
        raw_text=raw_text,
        source_type="hoyowiki",
        content_hash=content_hash(title or "", text),
        lang=effective_lang,
        meta=meta,
    )


def try_hoyowiki_adapter(url: str, **kwargs: Any) -> WebAdapterResult | None:
    """BaseWebAdapter 호환 호출 래퍼."""
    game, entry_id, lang = parse_hoyowiki_url(url)
    if not game or not entry_id:
        return None

    client = HoYoWikiClient(default_lang=lang or "ko-kr")
    try:
        page = client.get_entry(entry_id, game=game, lang=lang)
    except Exception:
        return None

    title, text, links, anchors, images = format_entry_to_markdown(page, game)
    if not text or len(text) < 30:
        return None

    return WebAdapterResult(
        title=title,
        text=text,
        links=links,
        anchors=anchors,
        images=images,
        doc_type="hoyowiki",
    )


def crawl_hoyowiki(
    game: str = "genshin",
    menu_filter: str | None = None,
    limit: int | None = None,
    delay: float = 0.5,
    lang: str = "ko-kr",
) -> Iterator[Document]:
    """HoYoWiki 특정 게임 또는 카테고리의 엔트리들을 순회하며 수집(Document 생성)한다."""
    games = ["genshin", "hsr", "zzz", "honkai3rd"] if game.lower() == "all" else [game.lower()]
    client = HoYoWikiClient(default_lang=lang)
    yielded = 0

    for g in games:
        try:
            menus = client.get_menus(g, lang=lang)
        except Exception as e:
            log.warning("Failed to get menus for %s: %s", g, e)
            continue

        # 수집 대상 메뉴(has_page가 True인 최하위 메뉴들) 선별
        target_menus: list[tuple[str, str]] = []  # (menu_id, menu_name)

        def _traverse(m_list: list[dict[str, Any]]):
            for m in m_list:
                m_id = str(m.get("id") or "")
                m_name = (m.get("name") or "").strip()
                sub = m.get("sub_menus") or []
                if sub:
                    _traverse(sub)
                elif m.get("has_page") or not sub:
                    target_menus.append((m_id, m_name))

        _traverse(menus)

        # 메뉴 필터링 (명칭 또는 ID 일치)
        if menu_filter and menu_filter.lower() != "all":
            filtered = []
            mf = menu_filter.strip().lower()
            for m_id, m_name in target_menus:
                if mf == m_id or mf in m_name.lower():
                    filtered.append((m_id, m_name))
            target_menus = filtered

        for m_id, m_name in target_menus:
            page_num = 1
            while True:
                try:
                    res = client.get_entry_list(g, m_id, page_num=page_num, page_size=30, lang=lang)
                except Exception as e:
                    log.warning("Failed to get entry list for %s menu %s (page %d): %s", g, m_name, page_num, e)
                    break

                items = res.get("list") or []
                if not items:
                    break

                for it in items:
                    ep_id = it.get("entry_page_id")
                    if not ep_id:
                        continue

                    entry_url = f"https://wiki.hoyolab.com/pc/{g}/entry/{ep_id}"
                    try:
                        doc = fetch_hoyowiki(entry_url, full_content=True, lang=lang)
                        yield doc
                        yielded += 1
                        if limit and yielded >= limit:
                            return
                    except Exception as e:
                        log.warning("Failed to fetch entry %s (%s): %s", ep_id, entry_url, e)

                    if delay > 0:
                        time.sleep(delay)

                total = int(res.get("total") or 0)
                if page_num * 30 >= total:
                    break
                page_num += 1


@register_fetcher("hoyowiki", priority=120)
class HoyowikiFetcher(BaseFetcher):
    @classmethod
    def can_handle(cls, url: str) -> bool:
        game, entry_id, _ = parse_hoyowiki_url(url)
        return bool(game and entry_id)

    @classmethod
    def name(cls) -> str:
        return "hoyowiki"

    @classmethod
    def fetch(cls, url: str, **kwargs: Any) -> Document:
        return fetch_hoyowiki(url, **kwargs)


@register_web_adapter(name="hoyowiki", domains=("wiki.hoyolab.com", "*.hoyolab.com"), priority=40)
class HoyowikiAdapter(BaseWebAdapter):
    @classmethod
    def name(cls) -> str:
        return "hoyowiki"

    @classmethod
    def try_fetch(cls, url: str, **kwargs: Any) -> WebAdapterResult | None:
        return try_hoyowiki_adapter(url, **kwargs)
