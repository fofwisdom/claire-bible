"""PDF 문서 추출기 — pypdf 기반 본문, 메타데이터(/Title), 링크 추출."""

from __future__ import annotations

import io
import re
from typing import BinaryIO

import pypdf

from ...config import get_settings

_URL_RE = re.compile(r"https?://[^\s)\]\}<>\"']+")


def extract_pdf_stream(
    stream: BinaryIO,
    url: str | None = None,
    fallback_title: str | None = None,
) -> tuple[str | None, str, list[str], dict[str, str], str | None, list[dict]]:
    """(title, text, links, anchors, error, images).

    PDF 스트림에서 텍스트, 메타데이터(/Title), 링크를 추출한다.
    실패 시 error 메시지와 함께 빈 결과를 반환한다.
    """
    try:
        reader = pypdf.PdfReader(stream)
        if reader.is_encrypted:
            try:
                reader.decrypt("")
            except Exception:
                return None, "", [], {}, "encrypted PDF", []

        title: str | None = None
        if reader.metadata:
            t = reader.metadata.get("/Title") or getattr(reader.metadata, "title", None)
            if t and isinstance(t, str) and t.strip():
                clean_t = t.strip().replace("\x00", "")
                if clean_t:
                    title = clean_t[:200]

        pages_text: list[str] = []
        links: list[str] = []
        anchors: dict[str, str] = {}
        seen_links: set[str] = set()

        total_len = 0
        for page in reader.pages:
            try:
                pt = page.extract_text() or ""
            except Exception:
                pt = ""
            pt = pt.strip()
            if pt:
                pages_text.append(pt)
                total_len += len(pt)

            # PDF 어노테이션 링크(URI Action) 추출
            try:
                annots = page.get("/Annots")
                if annots:
                    for a in annots:
                        obj = a.get_object() if hasattr(a, "get_object") else a
                        if isinstance(obj, dict):
                            action = obj.get("/A")
                            if isinstance(action, dict) and action.get("/S") == "/URI":
                                uri = str(action.get("/URI") or "").strip()
                                if uri.startswith(("http://", "https://")) and uri not in seen_links:
                                    seen_links.add(uri)
                                    links.append(uri)
            except Exception:
                pass

            max_chars = get_settings().pdf_max_extract_chars
            if total_len >= max_chars:
                break

        full_text = "\n\n".join(pages_text).replace("\x00", "")
        if not full_text.strip():
            return None, "", [], {}, "empty PDF content", []

        if not title:
            lines = [line.strip() for line in full_text.splitlines() if line.strip()]
            if lines:
                title = " ".join(lines[:2])[:200]
            elif fallback_title:
                title = fallback_title[:200]
            else:
                title = "PDF Document"

        # 본문 텍스트 내 URL 정규식 추출
        for m in _URL_RE.finditer(full_text):
            link = m.group(0).rstrip(".,;)")
            if link not in seen_links:
                seen_links.add(link)
                links.append(link)
                if len(links) >= 50:
                    break

        return title, full_text, links[:50], anchors, None, []
    except Exception as e:  # noqa: BLE001
        return None, "", [], {}, f"PDF extraction failed: {e}", []


def extract_pdf_bytes(
    data: bytes,
    url: str | None = None,
    fallback_title: str | None = None,
) -> tuple[str | None, str, list[str], dict[str, str], str | None, list[dict]]:
    return extract_pdf_stream(io.BytesIO(data), url=url, fallback_title=fallback_title)
