"""ODT (OpenDocument Text) 문서 추출기 — content.xml 구조 파싱, 메타데이터 추출 및 마크다운 변환."""

from __future__ import annotations

import io
import logging
import re
import zipfile
from typing import Any, BinaryIO

from lxml import etree as ET

logger = logging.getLogger("claire.ingest.odt")

NS = {
    "office": "urn:oasis:names:tc:opendocument:xmlns:office:1.0",
    "text": "urn:oasis:names:tc:opendocument:xmlns:text:1.0",
    "table": "urn:oasis:names:tc:opendocument:xmlns:table:1.0",
    "xlink": "http://www.w3.org/1999/xlink",
    "dc": "http://purl.org/dc/elements/1.1/",
    "meta": "urn:oasis:names:tc:opendocument:xmlns:meta:1.0",
}

_URL_RE = re.compile(r"https?://[^\s)\]\}<>\"']+")
ODT_MIMETYPE = "application/vnd.oasis.opendocument.text"


def is_odt_bytes(data: bytes) -> bool:
    """바이너리 버퍼가 ODT(OpenDocument Text) 파일인지 검사."""
    if not data or len(data) < 30:
        return False
    if not data.startswith(b"PK\x03\x04"):
        return False
    # ODT는 mimetype 파일이 압축되지 않고 맨 앞에 위치하거나 content.xml을 포함
    if b"application/vnd.oasis.opendocument.text" in data[:200]:
        return True
    try:
        with zipfile.ZipFile(io.BytesIO(data)) as zf:
            names = set(zf.namelist())
            if "mimetype" in names:
                mtype = zf.read("mimetype").decode("utf-8", errors="ignore").strip()
                if "application/vnd.oasis.opendocument.text" in mtype:
                    return True
            return "content.xml" in names and ("meta.xml" in names or "styles.xml" in names)
    except Exception:
        return False


class OdtExtractResult(tuple):
    """6개 튜플(title, text, links, anchors, error, images)과 완벽 호환되면서 .biblio 및 파서 속성을 제공."""

    def __new__(
        cls,
        title: str | None,
        text: str,
        links: list[str],
        anchors: dict[str, str],
        error: str | None,
        images: list[dict],
        biblio: dict[str, Any] | None = None,
        parser_used: str = "odt",
    ):
        instance = super().__new__(cls, (title, text, links, anchors, error, images))
        instance.biblio = biblio or {}
        instance.parser_used = parser_used
        return instance


def _extract_inline(elem: ET._Element, links: list[str], anchors: dict[str, str]) -> str:
    """문단/제목 내부의 인라인 서식, 링크, 공백 등을 텍스트/마크다운으로 추출."""
    parts: list[str] = []
    if elem.text:
        parts.append(elem.text)
    for child in elem:
        tag = child.tag
        if tag == f"{{{NS['text']}}}s":
            cnt = int(child.get(f"{{{NS['text']}}}c", "1"))
            parts.append(" " * cnt)
        elif tag == f"{{{NS['text']}}}tab":
            parts.append("\t")
        elif tag == f"{{{NS['text']}}}line-break":
            parts.append("\n")
        elif tag == f"{{{NS['text']}}}a":
            href = child.get(f"{{{NS['xlink']}}}href", "").strip()
            sub = _extract_inline(child, links, anchors)
            if href:
                if href.startswith(("http://", "https://")):
                    if href not in links:
                        links.append(href)
                    if href not in anchors and sub:
                        anchors[href] = sub[:160]
                parts.append(f"[{sub}]({href})")
            else:
                parts.append(sub)
        else:
            parts.append(_extract_inline(child, links, anchors))
        if child.tail:
            parts.append(child.tail)
    return "".join(parts)


def _format_table(
    table_elem: ET._Element, links: list[str], anchors: dict[str, str]
) -> str:
    """<table:table> 요소를 Markdown 테이블 문자열로 변환."""
    rows: list[list[str]] = []
    max_cols = 0

    for row_elem in table_elem.iter(f"{{{NS['table']}}}table-row"):
        current_row: list[str] = []
        for cell_elem in row_elem.iter(f"{{{NS['table']}}}table-cell"):
            repeat_str = cell_elem.get(f"{{{NS['table']}}}number-columns-repeated", "1")
            try:
                repeat = max(1, min(int(repeat_str), 50))
            except ValueError:
                repeat = 1

            cell_paras = [
                _extract_inline(p, links, anchors).strip()
                for p in cell_elem.iter(f"{{{NS['text']}}}p")
            ]
            cell_text = " ".join(p for p in cell_paras if p).replace("|", "\\|").replace("\n", " ")
            # trailing 빈 셀 반복 방지: 빈 셀이 5번 이상 반복되면 1개만 추가
            if not cell_text and repeat > 5:
                repeat = 1
            for _ in range(repeat):
                current_row.append(cell_text)

        # 끝부분 연속 빈 셀 정리
        while current_row and not current_row[-1]:
            current_row.pop()

        if current_row:
            rows.append(current_row)
            if len(current_row) > max_cols:
                max_cols = len(current_row)

    if not rows or max_cols == 0:
        return ""

    # 모든 행의 열 수를 max_cols로 맞춤
    for r in rows:
        if len(r) < max_cols:
            r.extend([""] * (max_cols - len(r)))

    header = rows[0]
    sep = ["---"] * max_cols
    body = rows[1:]

    lines = [
        "| " + " | ".join(header) + " |",
        "| " + " | ".join(sep) + " |",
    ]
    for r in body:
        lines.append("| " + " | ".join(r) + " |")

    return "\n" + "\n".join(lines) + "\n"


def _extract_list(
    list_elem: ET._Element, links: list[str], anchors: dict[str, str], depth: int = 0
) -> list[str]:
    """<text:list> 요소를 들여쓰기가 적용된 Markdown 글머리 기호 리스트로 변환."""
    items: list[str] = []
    indent = "  " * depth

    for child in list_elem:
        if child.tag == f"{{{NS['text']}}}list-item":
            item_parts: list[str] = []
            for sub in child:
                if sub.tag == f"{{{NS['text']}}}p":
                    t = _extract_inline(sub, links, anchors).strip()
                    if t:
                        item_parts.append(t)
                elif sub.tag == f"{{{NS['text']}}}list":
                    sub_items = _extract_list(sub, links, anchors, depth + 1)
                    item_parts.extend(sub_items)
            if item_parts:
                first = item_parts[0]
                items.append(f"{indent}- {first}")
                for extra in item_parts[1:]:
                    if extra.startswith("  "):
                        items.append(extra)
                    else:
                        items.append(f"{indent}  {extra}")
        elif child.tag == f"{{{NS['text']}}}list":
            items.extend(_extract_list(child, links, anchors, depth + 1))

    return items


def extract_odt_metadata(meta_xml_bytes: bytes) -> tuple[str | None, dict[str, Any]]:
    """meta.xml 에서 제목 추출 (서지 정보 추출은 소각됨)."""
    title: str | None = None
    if not meta_xml_bytes:
        return None, {}

    try:
        root = ET.fromstring(meta_xml_bytes)
        t_elem = root.find(f".//{{{NS['dc']}}}title")
        if t_elem is not None and t_elem.text and t_elem.text.strip():
            title = t_elem.text.strip()[:200]
    except Exception as e:
        logger.debug("Failed to parse meta.xml: %s", e)

    return title, {}


def extract_odt_stream(
    stream: BinaryIO,
    url: str | None = None,
    fallback_title: str | None = None,
) -> OdtExtractResult:
    """ODT 파일 스트림에서 본문 텍스트, 메타데이터, 하이퍼링크 추출."""
    try:
        zf = zipfile.ZipFile(stream)
    except Exception as e:
        return OdtExtractResult(
            None, "", [], {}, f"invalid ODT archive: {e}", [], {}
        )

    with zf:
        namelist = set(zf.namelist())
        if "content.xml" not in namelist:
            return OdtExtractResult(
                None, "", [], {}, "corrupted ODT: missing content.xml", [], {}
            )

        # 1. 메타데이터 파싱
        title: str | None = None
        biblio: dict[str, Any] = {}
        if "meta.xml" in namelist:
            try:
                meta_bytes = zf.read("meta.xml")
                title, biblio = extract_odt_metadata(meta_bytes)
            except Exception as e:
                logger.debug("Error reading meta.xml: %s", e)

        # 2. content.xml 파싱
        try:
            content_bytes = zf.read("content.xml")
            root = ET.fromstring(content_bytes)
        except Exception as e:
            return OdtExtractResult(
                None, "", [], {}, f"failed to parse content.xml: {e}", [], biblio
            )

        body_text_elem = root.find(f".//{{{NS['office']}}}body/{{{NS['office']}}}text")
        if body_text_elem is None:
            body_text_elem = root.find(f".//{{{NS['office']}}}text")
        if body_text_elem is None:
            return OdtExtractResult(
                title or fallback_title, "", [], {}, "empty ODT content", [], biblio
            )

        links: list[str] = []
        anchors: dict[str, str] = {}
        doc_elements: list[str] = []
        first_heading: str | None = None

        for child in body_text_elem:
            tag = child.tag
            if tag == f"{{{NS['text']}}}h":
                level_str = child.get(f"{{{NS['text']}}}outline-level", "1")
                try:
                    level = max(1, min(int(level_str), 6))
                except ValueError:
                    level = 1
                heading_text = _extract_inline(child, links, anchors).strip()
                if heading_text:
                    if first_heading is None:
                        first_heading = heading_text
                    doc_elements.append(f"{'#' * level} {heading_text}")
            elif tag == f"{{{NS['text']}}}p":
                p_text = _extract_inline(child, links, anchors).strip()
                if p_text:
                    doc_elements.append(p_text)
            elif tag == f"{{{NS['text']}}}list":
                list_items = _extract_list(child, links, anchors)
                if list_items:
                    doc_elements.append("\n".join(list_items))
            elif tag == f"{{{NS['table']}}}table":
                tbl_text = _format_table(child, links, anchors)
                if tbl_text:
                    doc_elements.append(tbl_text)

        full_text = "\n\n".join(doc_elements).strip()
        if not full_text:
            return OdtExtractResult(
                title or fallback_title, "", [], {}, "empty ODT content", [], biblio
            )

        # 제목 결정: meta.xml title > 첫 번째 헤딩 > fallback_title
        if not title:
            if first_heading:
                title = first_heading[:200]
            elif fallback_title:
                title = fallback_title
            else:
                title = full_text.splitlines()[0][:80]

        # 본문 텍스트 내 URL 정규식 스캔 추가 (인라인 링크 누락 보완)
        for u in _URL_RE.findall(full_text):
            cleaned = u.rstrip(".,;)")
            if cleaned not in links:
                links.append(cleaned)

        return OdtExtractResult(
            title=title,
            text=full_text,
            links=links[:50],
            anchors=anchors,
            error=None,
            images=[],
            biblio=biblio,
            parser_used="odt",
        )


def extract_odt_bytes(
    data: bytes,
    url: str | None = None,
    fallback_title: str | None = None,
) -> OdtExtractResult:
    """ODT 바이트 버퍼로부터 텍스트, 메타데이터 추출."""
    return extract_odt_stream(io.BytesIO(data), url=url, fallback_title=fallback_title)


def prioritize_odt_links(links: list[str]) -> list[str]:
    """동일한 문서 경로에 대해 .odt와 .pdf 링크가 공존할 경우 ODT를 우선 채택하고 PDF 링크를 배제."""
    if not links:
        return []

    odt_bases = set()
    for link in links:
        low = link.lower().split("?", 1)[0]
        if low.endswith(".odt"):
            base = low[:-4]
            odt_bases.add(base)

    if not odt_bases:
        return links

    filtered: list[str] = []
    for link in links:
        low = link.lower().split("?", 1)[0]
        if low.endswith(".pdf"):
            base = low[:-4]
            if base in odt_bases:
                continue
        filtered.append(link)

    odt_links = [l for l in filtered if l.lower().split("?", 1)[0].endswith(".odt")]
    other_links = [l for l in filtered if not l.lower().split("?", 1)[0].endswith(".odt")]
    return odt_links + other_links
