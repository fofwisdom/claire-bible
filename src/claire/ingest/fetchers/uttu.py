"""uttu.merui.net (UTTU Archive) web adapter for Reverse: 1999 story viewer and database pages."""

from __future__ import annotations

import json
import logging
import re
import urllib.parse
from typing import Any

from bs4 import BeautifulSoup

from ...config import get_settings
from ..registry import register_web_adapter
from .base import BaseWebAdapter, WebAdapterResult
from .http import SafeHttpClient

logger = logging.getLogger(__name__)

_UTTU_DOMAINS = ("uttu.merui.net", "*.merui.net")

_COMMON_CHARACTER_PREFIX_MAP: dict[str, str] = {
    "3001": "버틴",
    "3002": "소네토",
    "3003": "레굴루스",
    "3047": "메디슨 포켓",
    "3065": "마커스",
    "3080": "카카니아",
    "3081": "이졸데",
    "5121": "호프만",
    "5122": "칼",
    "5123": "하인리히",
    "5149": "루시",
    "6239": "제멜바이스",
}

_HEADICON_ID_RE = re.compile(r"headicon(?:_small)?/([a-zA-Z0-9_-]+?)(?:\.[a-zA-Z0-9]+)?(?:[?#]|$)")

_LANG_INDEX_MAP: dict[str, int] = {
    "en": 0,
    "chs": 1,
    "cht": 2,
    "jp": 3,
    "kr": 4,
}

_UTTU_MODAL_AND_BANNER_IDS = (
    "wipBanner",
    "loading-screen",
    "nav__container",
    "mobile-nav__container",
    "cnModal",
    "search-modal",
    "storyControlsBar",
    "settingsDropdown",
    "firstVisitModal",
    "infoModal",
    "enemyModal",
    "changelogBackdrop",
    "uttuCopyright",
    "custom-cursor",
    "portraitModalBackdrop",
    "insightMaterialsModal",
    "skillModal",
    "statusModal",
    "imageModal",
    "videoModal",
)


def _is_uttu_url(url: str) -> bool:
    """Check if the given URL belongs to uttu.merui.net or *.merui.net."""
    try:
        parts = urllib.parse.urlsplit(url.strip())
        host = (parts.hostname or "").lower()
        return host == "uttu.merui.net" or host == "merui.net" or host.endswith(".merui.net")
    except Exception:
        return False


def resolve_uttu_language(url: str, settings: Any = None) -> str:
    """Resolve target language from URL path, query parameter, or system settings."""
    parts = urllib.parse.urlsplit(url)

    # 1. URL path (e.g. /kr/, /en/)
    path_segments = [seg.strip().lower() for seg in parts.path.split("/") if seg.strip()]
    for seg in path_segments:
        if seg in ("kr", "en", "jp", "chs", "cht"):
            return seg

    # 2. URL query (e.g. ?lang=kr)
    qs = urllib.parse.parse_qs(parts.query)
    if "lang" in qs and qs["lang"]:
        q_lang = qs["lang"][0].strip().lower()
        if q_lang == "ko":
            return "kr"
        if q_lang == "ja":
            return "jp"
        if q_lang in ("kr", "en", "jp", "chs", "cht"):
            return q_lang

    # 3. System settings
    s = settings or get_settings()
    pref = ""
    if getattr(s, "effective_preferred_languages", None):
        pref = s.effective_preferred_languages[0]
    elif getattr(s, "preferred_languages", None):
        pref = s.preferred_languages.split(",")[0].strip()
    pref = (pref or "ko").lower()

    if pref.startswith("ko") or pref == "kr":
        return "kr"
    if pref.startswith("ja") or pref == "jp":
        return "jp"
    if pref.startswith("zh-tw") or pref.startswith("zh-hk") or pref == "cht":
        return "cht"
    if pref.startswith("zh") or pref == "chs":
        return "chs"
    if pref.startswith("en"):
        return "en"

    return "kr"


def _extract_links_and_anchors(soup: BeautifulSoup, base_url: str) -> tuple[list[str], dict[str, str]]:
    """Extract links and anchor texts from HTML."""
    links: list[str] = []
    anchors: dict[str, str] = {}
    seen: set[str] = set()

    for a in soup.find_all("a", href=True):
        href = a["href"].strip()
        if not href or href.startswith(("#", "javascript:", "mailto:")):
            continue
        abs_url = urllib.parse.urljoin(base_url, href)
        if abs_url not in seen:
            seen.add(abs_url)
            links.append(abs_url)
            anchor_text = " ".join(a.get_text().split())
            if anchor_text:
                anchors[abs_url] = anchor_text
        if len(links) >= 50:
            break

    return links, anchors


def _extract_headicon_id(src: str) -> str:
    """Extract headicon identifier from image source URL or path."""
    if not src:
        return ""
    m = _HEADICON_ID_RE.search(src)
    if m:
        return m.group(1).strip()
    filename = src.split("?")[0].split("#")[0].rstrip("/").split("/")[-1]
    stem = filename.split(".")[0]
    if stem.isdigit():
        return stem
    return ""


def _scan_headicon_maps_from_soup(
    soup: BeautifulSoup,
    headicon_map: dict[str, str],
    prefix_map: dict[str, str],
) -> None:
    """Extract headicon identifier to speaker name mappings from HTML elements."""
    # 1. Elements with data-headicon (e.g. <b data-headicon="620520">관람객Ⅰ:</b>)
    for el in soup.find_all(attrs={"data-headicon": True}):
        hid = str(el.get("data-headicon", "")).strip()
        name = el.get_text().strip().rstrip(":").strip()
        if hid and name:
            headicon_map[hid] = name
            if len(hid) >= 4 and hid[:4] not in prefix_map:
                prefix_map[hid[:4]] = name

    # 2. Extract headIconId -> name from button.enemy-info-trigger
    for btn in soup.find_all("button", class_="enemy-info-trigger"):
        raw_attr = btn.get("data-enemy-groups")
        if not raw_attr:
            continue
        try:
            groups = json.loads(raw_attr)
            for group in groups:
                for monster in group.get("monsters", []):
                    m_name = (monster.get("name") or "").strip()
                    hid = monster.get("headIconId") or monster.get("headIcon")
                    if hid is not None and m_name:
                        hid_str = str(hid).strip()
                        if hid_str:
                            headicon_map[hid_str] = m_name
                            if len(hid_str) >= 4 and hid_str[:4] not in prefix_map:
                                prefix_map[hid_str[:4]] = m_name
        except Exception:
            pass


def _scan_headicon_maps_from_html(
    raw_html: str,
    headicon_map: dict[str, str],
    prefix_map: dict[str, str],
) -> None:
    """Scan raw HTML string to build headicon and prefix mappings."""
    if not raw_html:
        return
    if "data-headicon" not in raw_html and "enemy-info-trigger" not in raw_html:
        return
    soup = BeautifulSoup(raw_html, "html.parser")
    _scan_headicon_maps_from_soup(soup, headicon_map, prefix_map)


def _format_chapter_content(
    raw_html: str,
    base_url: str,
    headicon_map: dict[str, str] | None = None,
    prefix_map: dict[str, str] | None = None,
) -> tuple[str, list[dict[str, Any]]]:
    """Format the chapter content HTML to text/markdown and extract story images."""
    ch_soup = BeautifulSoup(raw_html, "html.parser")

    # Build local headicon mappings from provided maps and current chapter HTML
    local_headicon_map = dict(headicon_map) if headicon_map else {}
    local_prefix_map = dict(prefix_map) if prefix_map else {}
    _scan_headicon_maps_from_soup(ch_soup, local_headicon_map, local_prefix_map)

    # 1. Format <div class="location-label"> to \n### [장소: {location}]\n
    for loc in ch_soup.find_all("div", class_="location-label"):
        loc_text = loc.get_text().strip()
        loc.replace_with(f"\n### [장소: {loc_text}]\n")

    # 2. Remove UI audio/video cue elements like .music-cue and .video-cue
    for cue in ch_soup.find_all(class_=lambda c: c and any(cls_name in c.split() for cls_name in ("music-cue", "video-cue"))):
        cue.decompose()

    # 3. Parse button.enemy-info-trigger elements into [전투 등장: {name} — {description}] sections
    for btn in ch_soup.find_all("button", class_="enemy-info-trigger"):
        raw_attr = btn.get("data-enemy-groups")
        combat_lines: list[str] = []
        if raw_attr:
            try:
                groups = json.loads(raw_attr)
                for group in groups:
                    for monster in group.get("monsters", []):
                        m_name = (monster.get("name") or "").strip()
                        m_desc = (monster.get("description") or "").strip()
                        if m_name and m_desc:
                            combat_lines.append(f"[전투 등장: {m_name} — {m_desc}]")
                        elif m_name:
                            combat_lines.append(f"[전투 등장: {m_name}]")
            except Exception:
                pass
        if combat_lines:
            btn.replace_with("\n\n" + "\n".join(combat_lines) + "\n\n")
        else:
            btn.decompose()

    # 4. Resolve combat dialogues with dialog-portrait-icon
    for img in ch_soup.find_all("img", class_="dialog-portrait-icon"):
        src = img.get("src") or img.get("data-src") or img.get("data-modal-src") or ""
        icon_id = _extract_headicon_id(src)
        if not icon_id:
            icon_id = str(img.get("data-headicon") or img.get("data-id") or "").strip()

        prefix = icon_id[:4] if len(icon_id) >= 4 else icon_id
        speaker_name = (
            local_headicon_map.get(icon_id)
            or local_prefix_map.get(prefix)
            or _COMMON_CHARACTER_PREFIX_MAP.get(prefix)
        )

        speaker_tag = ch_soup.new_tag("b")
        if speaker_name:
            speaker_tag.string = f"{speaker_name}:"
        elif icon_id:
            speaker_tag.string = f"[대사({icon_id})]:"
        else:
            speaker_tag.string = "[전투 대사]:"

        img.replace_with(speaker_tag)
        if speaker_tag.next_sibling and isinstance(speaker_tag.next_sibling, str):
            if not speaker_tag.next_sibling.startswith(" "):
                speaker_tag.insert_after(" ")
        else:
            speaker_tag.insert_after(" ")
        img.decompose()

    # 5. Collect images from data-modal-src or src, excluding black/white background images and decorative UI badges
    images: list[dict[str, Any]] = []
    seen_urls: set[str] = set()
    for img in ch_soup.find_all("img"):
        if "dialog-portrait-icon" in img.get("class", []):
            img.decompose()
            continue
        src = img.get("data-modal-src") or img.get("src")
        if not src:
            img.decompose()
            continue
        abs_img_url = urllib.parse.urljoin(base_url, src.strip())
        lower_url = abs_img_url.lower()

        # Exclude pure black/white background images
        if "heiping.webp" in lower_url or "baiping.webp" in lower_url:
            img.decompose()
            continue

        # Exclude decorative UI badges and UI widgets
        if "/ui/enemy/" in lower_url or "ring.webp" in lower_url or "/ui/" in lower_url:
            img.decompose()
            continue

        # Exclude character headicons
        if "headicon" in lower_url:
            img.decompose()
            continue

        if abs_img_url not in seen_urls:
            seen_urls.add(abs_img_url)
            alt = (img.get("alt") or "").strip()
            images.append({"url": abs_img_url, "alt": alt, "caption": ""})
        img.decompose()

    # 6. Format screen narration <p> elements
    for p in ch_soup.find_all("p"):
        p_text = p.get_text().strip()
        if p_text:
            p.replace_with(f"\n\n{p_text}\n\n")
        else:
            p.decompose()

    # 7. Replace <br> tags with newline characters so dialogues and stage directions do not merge into single lines
    for br in ch_soup.find_all("br"):
        br.replace_with("\n")

    # 8. Normalize whitespace
    raw_text = ch_soup.get_text()
    lines = [line.strip() for line in raw_text.splitlines()]
    collapsed: list[str] = []
    for line in lines:
        if line:
            collapsed.append(line)
        elif collapsed and collapsed[-1] != "":
            collapsed.append("")
    formatted_text = "\n".join(collapsed).strip()

    return formatted_text, images


def _format_html_tables_to_markdown_soup(soup: BeautifulSoup) -> None:
    """Format HTML tables in soup to markdown table syntax."""
    for tbl in soup.find_all("table"):
        # Remove media inside table
        for media in tbl.find_all(["img", "svg", "video", "audio", "iframe"]):
            media.decompose()

        caption = tbl.find("caption")
        caption_txt = caption.get_text().strip() if caption else ""

        rows: list[list[str]] = []
        for tr in tbl.find_all("tr"):
            cells = tr.find_all(["th", "td"])
            if not cells:
                continue
            row = [" ".join(c.get_text().split()).replace("|", "\\|") for c in cells]
            if any(row):
                rows.append(row)

        if not rows:
            tbl.decompose()
            continue

        max_cols = max(len(r) for r in rows)
        if max_cols == 0:
            tbl.decompose()
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
        tbl.replace_with(md_table_text)


def _clean_rich_text(text: str) -> str:
    """Clean Unity/RichText and HTML tags to markdown format while preserving text brackets like <UTTU>."""
    if not text:
        return ""
    text = re.sub(r"<br\s*/?>", "\n", text, flags=re.IGNORECASE)
    text = re.sub(r"<b>(.*?)</b>", r"**\1**", text, flags=re.IGNORECASE | re.DOTALL)
    text = re.sub(r"<i>(.*?)</i>", r"*\1*", text, flags=re.IGNORECASE | re.DOTALL)
    text = re.sub(r"</?(?:size|color|align|nobr)[^>]*>", "", text, flags=re.IGNORECASE)
    lines = [line.strip() for line in text.splitlines()]
    collapsed: list[str] = []
    for line in lines:
        if line:
            collapsed.append(line)
        elif collapsed and collapsed[-1] != "":
            collapsed.append("")
    return "\n".join(collapsed).strip()


def _apply_uttu_localization(soup: BeautifulSoup, target_lang: str) -> None:
    """Apply client-side UTTU i18n data-l and data-la attributes to DOM elements."""
    for el in soup.find_all(attrs={"data-l": True}):
        val = el.get(f"data-l-{target_lang}")
        if val is not None:
            sub = BeautifulSoup(val, "html.parser")
            el.clear()
            el.append(sub)
        la_val = el.get(f"data-la-{target_lang}")
        if la_val:
            try:
                attrs = json.loads(la_val)
                for k, v in attrs.items():
                    if v is None:
                        del el[k]
                    else:
                        el[k] = v
            except Exception:
                pass


def _resolve_trail_button(
    soup: BeautifulSoup,
    trail_param: str,
    part_param: str | None = None,
) -> Any | None:
    """Locate the target trail-link button corresponding to trail and part URL parameters."""
    parts = trail_param.split("-")
    p_num = parts[0].strip()
    trail_idx = int(parts[1].strip()) if len(parts) > 1 and parts[1].strip().isdigit() else 0

    trigger = soup.find(attrs={"data-url-part": p_num}) or soup.find(attrs={"data-chapter": p_num})
    if not trigger and part_param:
        trigger = soup.find(attrs={"data-url-part": part_param}) or soup.find(attrs={"data-chapter": part_param})

    if trigger:
        accordion = trigger.find_parent(class_="accordion-item")
        if accordion:
            trail_links = accordion.find_all(class_=lambda c: c and "trail-link" in c)
            if 0 <= trail_idx < len(trail_links):
                return trail_links[trail_idx]
            if trail_links:
                return trail_links[0]

    all_trail_links = soup.find_all(class_=lambda c: c and "trail-link" in c)
    for btn in all_trail_links:
        try:
            dt = json.loads(btn.get("data-trail", "{}"))
            if str(dt.get("id")) == p_num or str(dt.get("fragment_id")) == p_num:
                return btn
        except Exception:
            pass

    if 0 <= trail_idx < len(all_trail_links):
        return all_trail_links[trail_idx]

    return all_trail_links[0] if all_trail_links else None


def _format_dialog_steps(dialog_steps: dict[str, Any]) -> str:
    """Format structured dialog steps JSON into readable markdown lines."""
    steps = dialog_steps.get("steps", [])
    lines: list[str] = []
    for step in steps:
        speaker = (step.get("speaker") or "").strip()
        dialog = (step.get("dialog") or "").strip()
        if not dialog or dialog in ("대화 종료", "END OF DIALOGUE", "对话结束", "對話結束", "会話終了"):
            continue
        clean_text = _clean_rich_text(dialog)
        if speaker:
            lines.append(f"**{speaker}**: {clean_text}")
        else:
            lines.append(clean_text)
    return "\n\n".join(lines).strip()


@register_web_adapter(name="uttu", domains=_UTTU_DOMAINS, priority=35)
class UttuAdapter(BaseWebAdapter):
    """Web adapter for uttu.merui.net Reverse: 1999 database and story reader."""

    @classmethod
    def name(cls) -> str:
        return "uttu"

    @classmethod
    def try_fetch(cls, url: str, **kwargs: Any) -> WebAdapterResult | None:
        if not _is_uttu_url(url):
            return None

        timeout = kwargs.get("timeout", 25.0)
        client = SafeHttpClient(timeout=timeout)
        try:
            resp = client.get(url)
            if resp.status_code >= 400:
                return None
            html_text = resp.text
        except Exception as e:
            logger.debug("UttuAdapter fetch failed for %s: %s", url, e)
            return None

        soup = BeautifulSoup(html_text, "html.parser")
        chapters_script = soup.find("script", id="chaptersData")

        # -------------------------------------------------------------
        # Branch A: When chaptersData is present (story viewer page)
        # -------------------------------------------------------------
        if chapters_script and chapters_script.string:
            try:
                data = json.loads(chapters_script.string)
            except Exception as e:
                logger.debug("Failed to parse chaptersData JSON: %s", e)
                return None

            chapters = data.get("chapters", [])
            frontmatter = data.get("frontmatter", {}) or {}
            locale_title = frontmatter.get("localeTitle") or data.get("localeTitle")
            chapter_title = frontmatter.get("title") or data.get("title") or data.get("name")
            base_title = locale_title or chapter_title or "Reverse: 1999"

            # Pre-scan all chapters to build comprehensive headicon and prefix mappings
            episode_headicon_map: dict[str, str] = {}
            episode_prefix_map: dict[str, str] = {}
            for ch in chapters:
                ch_content = ch.get("content", "")
                if ch_content:
                    _scan_headicon_maps_from_html(ch_content, episode_headicon_map, episode_prefix_map)

            # Read part query parameter from URL if present
            parsed = urllib.parse.urlsplit(url)
            qs = urllib.parse.parse_qs(parsed.query)
            part_param = qs.get("part", [None])[0]
            if part_param is not None:
                part_param = part_param.strip()

            target_chapter = None
            is_single_part = False

            if part_param is not None and part_param != "":
                for idx, ch in enumerate(chapters, start=1):
                    if str(ch.get("number")) == part_param or str(ch.get("textPartNumber")) == part_param:
                        target_chapter = ch
                        is_single_part = True
                        break
                    if part_param.isdigit() and idx == int(part_param):
                        target_chapter = ch
                        is_single_part = True
                        break

            all_images: list[dict[str, Any]] = []
            seen_images: set[str] = set()

            if is_single_part and target_chapter is not None:
                part_number = target_chapter.get("textPartNumber") or target_chapter.get("number") or part_param
                part_title = target_chapter.get("title") or target_chapter.get("englishTitle") or f"Part {part_number}"
                doc_title = f"{locale_title or chapter_title} - {part_number}. {part_title} | Reverse: 1999"

                text, ch_images = _format_chapter_content(
                    target_chapter.get("content", ""),
                    base_url=url,
                    headicon_map=episode_headicon_map,
                    prefix_map=episode_prefix_map,
                )
                for img in ch_images:
                    if img["url"] not in seen_images:
                        seen_images.add(img["url"])
                        all_images.append(img)
            else:
                doc_title = f"{locale_title or chapter_title} | Reverse: 1999"
                formatted_chapters: list[str] = []
                for ch in chapters:
                    p_num = ch.get("textPartNumber") or ch.get("number")
                    p_title = ch.get("title") or ch.get("englishTitle") or ""
                    heading = f"## {p_num}. {p_title}".strip() if p_num else f"## {p_title}".strip()
                    ch_text, ch_images = _format_chapter_content(
                        ch.get("content", ""),
                        base_url=url,
                        headicon_map=episode_headicon_map,
                        prefix_map=episode_prefix_map,
                    )
                    for img in ch_images:
                        if img["url"] not in seen_images:
                            seen_images.add(img["url"])
                            all_images.append(img)
                    if heading:
                        formatted_chapters.append(f"{heading}\n\n{ch_text}")
                    else:
                        formatted_chapters.append(ch_text)
                text = "\n\n".join(formatted_chapters).strip()

            links, anchors = _extract_links_and_anchors(soup, base_url=url)

            return WebAdapterResult(
                title=doc_title,
                text=text,
                links=links,
                anchors=anchors,
                images=all_images,
                doc_type="web",
                parser_info={
                    "adapter": "uttu",
                    "page_type": "story",
                    "part": part_param if is_single_part else None,
                    "story_type": data.get("type"),
                    "story_name": data.get("name"),
                },
            )

        # -------------------------------------------------------------
        # Branch B: When storyData is present (event story, trails, and stage map)
        # -------------------------------------------------------------
        target_lang = resolve_uttu_language(url, settings=kwargs.get("settings"))
        _apply_uttu_localization(soup, target_lang)

        story_data = soup.find(id="storyData")
        event_title = ""
        if story_data is not None:
            story_block = soup.find(id="storyBlock")
            if story_block and story_block.get("data-titles"):
                try:
                    t_map = json.loads(story_block.get("data-titles"))
                    event_title = t_map.get(target_lang) or t_map.get("en") or ""
                except Exception:
                    pass
            if not event_title:
                h1 = soup.find("h1")
                event_title = h1.get_text(strip=True) if h1 else "Reverse: 1999"

            parsed = urllib.parse.urlsplit(url)
            qs = urllib.parse.parse_qs(parsed.query)
            trail_param = qs.get("trail", [None])[0]
            if trail_param is not None:
                trail_param = trail_param.strip()
            part_param = qs.get("part", [None])[0]
            if part_param is not None:
                part_param = part_param.strip()

            lang_idx = _LANG_INDEX_MAP.get(target_lang, 0)
            trails_map: dict[str, Any] = {}
            if story_data.get("data-trails"):
                try:
                    trails_map = json.loads(story_data.get("data-trails", "{}"))
                except Exception:
                    pass

            # Sub-branch B.1: Specific trail requested via trail query parameter
            if trail_param:
                btn = _resolve_trail_button(soup, trail_param=trail_param, part_param=part_param)
                if btn:
                    la_dict: dict[str, Any] = {}
                    if btn.get(f"data-la-{target_lang}"):
                        try:
                            la_dict = json.loads(btn.get(f"data-la-{target_lang}"))
                        except Exception:
                            pass

                    trail_obj: dict[str, Any] = {}
                    if "data-trail" in la_dict:
                        raw_dt = la_dict["data-trail"]
                        trail_obj = json.loads(raw_dt) if isinstance(raw_dt, str) else raw_dt
                    else:
                        try:
                            trail_obj = json.loads(btn.get("data-trail", "{}"))
                        except Exception:
                            pass

                    fragment_id = trail_obj.get("fragment_id")
                    frag = trails_map.get(str(fragment_id)) if fragment_id else None

                    trail_title = _clean_rich_text(trail_obj.get("title") or "")
                    trail_desc = _clean_rich_text(trail_obj.get("description") or "")

                    frag_title = ""
                    frag_desc = ""
                    images: list[dict[str, Any]] = []

                    if frag:
                        f_titles = frag.get("title", [])
                        raw_ft = f_titles[lang_idx] if lang_idx < len(f_titles) else (f_titles[0] if f_titles else "")
                        frag_title = _clean_rich_text(raw_ft)
                        f_descs = frag.get("description", [])
                        raw_fd = f_descs[lang_idx] if lang_idx < len(f_descs) else (f_descs[0] if f_descs else "")
                        frag_desc = _clean_rich_text(raw_fd)
                        frag_img = frag.get("image")
                        if frag_img:
                            images.append({
                                "url": f"https://assets.merui.net/dungeon/fragmenticon/{frag_img}.webp",
                                "alt": frag_title or trail_title,
                                "caption": "",
                            })

                    dialog_text = ""
                    dialog_steps = trail_obj.get("dialogSteps")
                    if dialog_steps and isinstance(dialog_steps, dict):
                        dialog_text = _format_dialog_steps(dialog_steps)

                    after_desc = ""
                    if trail_obj.get("after") and isinstance(trail_obj["after"], dict):
                        after_desc = _clean_rich_text(trail_obj["after"].get("description") or "")

                    if frag_title:
                        doc_title = f"{event_title} - {frag_title} | Reverse: 1999"
                    elif trail_title:
                        doc_title = f"{event_title} - {trail_title} | Reverse: 1999"
                    else:
                        doc_title = f"{event_title} | Reverse: 1999"

                    text_blocks: list[str] = []
                    if frag_desc:
                        text_blocks.append(f"# {frag_title or trail_title}\n")
                        if trail_title and trail_desc:
                            text_blocks.append(f"> **[단서: {trail_title}]**\n> {trail_desc}\n")
                        text_blocks.append(frag_desc)
                    elif dialog_text:
                        text_blocks.append(f"# {trail_title}\n")
                        if trail_desc:
                            text_blocks.append(f"> {trail_desc}\n")
                        text_blocks.append("### [대화 기록]\n")
                        text_blocks.append(dialog_text)
                    elif trail_title:
                        text_blocks.append(f"# {trail_title}\n")
                        if trail_desc:
                            text_blocks.append(trail_desc)

                    if after_desc:
                        text_blocks.append(f"\n---\n\n### [후속 기록]\n{after_desc}")

                    links, anchors = _extract_links_and_anchors(soup, base_url=url)
                    if trail_obj.get("state") and story_data.get("data-trail-transcripts"):
                        try:
                            transcripts_map = json.loads(story_data.get("data-trail-transcripts", "{}"))
                            t_info = transcripts_map.get(str(trail_obj["state"]))
                            if t_info and isinstance(t_info, dict):
                                t_url = f"https://uttu.merui.net/story/trails/{t_info.get('name')}/{target_lang}?part={t_info.get('part', 1)}"
                                if t_url not in links:
                                    links.append(t_url)
                                    anchors[t_url] = f"Trail Transcript {t_info.get('name')}"
                        except Exception:
                            pass

                    return WebAdapterResult(
                        title=doc_title,
                        text="\n\n".join(text_blocks).strip(),
                        links=links,
                        anchors=anchors,
                        images=images,
                        doc_type="web",
                        parser_info={
                            "adapter": "uttu",
                            "page_type": "event_trail",
                            "event_title": event_title,
                            "trail": trail_param,
                            "part": part_param,
                            "trail_id": trail_obj.get("id"),
                            "fragment_id": fragment_id,
                            "target_lang": target_lang,
                        },
                    )

            # Sub-branch B.2: Part parameter is present (without trail)
            if part_param:
                trigger = soup.find(attrs={"data-url-part": part_param}) or soup.find(attrs={"data-chapter": part_param})
                if trigger:
                    accordion = trigger.find_parent(class_="accordion-item")
                    stage_title = trigger.get_text(separator=" ").strip()
                    part_blocks: list[str] = [f"# {event_title} - {stage_title}\n"]
                    if accordion:
                        p_tag = accordion.find("p")
                        if p_tag:
                            p_txt = p_tag.get_text(strip=True)
                            if p_txt:
                                part_blocks.append(f"> {p_txt}\n")
                        t_links = accordion.find_all(class_=lambda c: c and "trail-link" in c)
                        if t_links:
                            part_blocks.append("### [단서 목록]\n")
                            for t_btn in t_links:
                                try:
                                    la_raw = t_btn.get(f"data-la-{target_lang}")
                                    if la_raw:
                                        lo = json.loads(la_raw)
                                        dt = json.loads(lo["data-trail"]) if "data-trail" in lo and isinstance(lo["data-trail"], str) else lo.get("data-trail", {})
                                    else:
                                        dt = json.loads(t_btn.get("data-trail", "{}"))
                                    t_t = _clean_rich_text(dt.get("title") or "")
                                    t_d = _clean_rich_text(dt.get("description") or "")
                                    f_id = dt.get("fragment_id")
                                    frag_info = trails_map.get(str(f_id)) if f_id else None
                                    if frag_info:
                                        ft = frag_info.get("title", [])
                                        ft_text = _clean_rich_text(ft[lang_idx] if lang_idx < len(ft) else (ft[0] if ft else ""))
                                        part_blocks.append(f"- **{t_t}** ({ft_text}): {t_d}")
                                    else:
                                        part_blocks.append(f"- **{t_t}**: {t_d}")
                                except Exception:
                                    pass

                    links, anchors = _extract_links_and_anchors(soup, base_url=url)
                    return WebAdapterResult(
                        title=f"{event_title} - {stage_title} | Reverse: 1999",
                        text="\n\n".join(part_blocks).strip(),
                        links=links,
                        anchors=anchors,
                        images=[],
                        doc_type="web",
                        parser_info={
                            "adapter": "uttu",
                            "page_type": "event_part",
                            "event_title": event_title,
                            "part": part_param,
                            "target_lang": target_lang,
                        },
                    )

        # -------------------------------------------------------------
        # Branch C: When chaptersData is absent (profile / general database page)
        # -------------------------------------------------------------
        # Remove elements with data-lang not matching target language
        all_data_lang_els = soup.find_all(attrs={"data-lang": True})
        available_langs = {el.get("data-lang", "").lower() for el in all_data_lang_els if el.get("data-lang")}
        active_lang = target_lang if target_lang in available_langs else ("en" if "en" in available_langs else target_lang)

        for el in all_data_lang_els:
            el_lang = (el.get("data-lang") or "").lower()
            if el_lang != active_lang:
                el.decompose()

        # Remove UI modals and banners
        for modal_id in _UTTU_MODAL_AND_BANNER_IDS:
            for el in soup.find_all(id=modal_id):
                el.decompose()

        # Remove script, style, noscript, svg
        for tag in soup.find_all(["script", "style", "noscript", "svg"]):
            tag.decompose()

        # Format tables to markdown
        _format_html_tables_to_markdown_soup(soup)

        # Collect images
        images: list[dict[str, Any]] = []
        seen_img: set[str] = set()
        for img in soup.find_all("img"):
            src = img.get("data-modal-src") or img.get("src")
            if not src:
                continue
            abs_img_url = urllib.parse.urljoin(url, src.strip())
            lower_url = abs_img_url.lower()

            if "heiping.webp" in lower_url or "baiping.webp" in lower_url:
                continue
            if "/ui/enemy/" in lower_url or "ring.webp" in lower_url or "/ui/" in lower_url:
                continue
            if "headicon" in lower_url:
                continue

            if abs_img_url not in seen_img:
                seen_img.add(abs_img_url)
                alt = (img.get("alt") or "").strip()
                images.append({"url": abs_img_url, "alt": alt, "caption": ""})

        # Replace <br> tags with newline characters
        for br in soup.find_all("br"):
            br.replace_with("\n")

        # Extract clean text
        raw_text = soup.get_text(separator="\n")
        lines = [line.strip() for line in raw_text.splitlines()]
        collapsed: list[str] = []
        for line in lines:
            if line:
                collapsed.append(line)
            elif collapsed and collapsed[-1] != "":
                collapsed.append("")
        clean_text = "\n".join(collapsed).strip()

        # Document title
        title_tag = soup.find("title")
        page_title = title_tag.get_text().strip() if title_tag else None
        if not page_title:
            h1 = soup.find("h1")
            page_title = h1.get_text().strip() if h1 else None
        if story_data is not None and event_title:
            page_title = f"{event_title} | Reverse: 1999"

        links, anchors = _extract_links_and_anchors(soup, base_url=url)

        return WebAdapterResult(
            title=page_title,
            text=clean_text,
            links=links,
            anchors=anchors,
            images=images[:12],
            doc_type="web",
            parser_info={
                "adapter": "uttu",
                "page_type": "database",
                "target_lang": target_lang,
            },
        )
