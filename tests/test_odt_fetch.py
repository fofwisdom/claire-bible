"""ODT fetcher, 파서 및 PDF/ODT 우선순위 테스트."""

from __future__ import annotations

import io
import zipfile
from pathlib import Path

import pytest

from claire.expand.onehop import find_candidates
from claire.ingest.fetchers.base import FetchError
from claire.ingest.fetchers.odt import (
    extract_odt_bytes,
    is_odt_bytes,
    prioritize_odt_links,
)
from claire.ingest.fetchers.textfile import fetch_file
from claire.ingest.fetchers.web import _fetch_static, fetch_web
from claire.ingest.router import classify
from claire.ontology.base import Document
from claire.store import db as dbm


def _create_sample_odt(
    title: str = "Test ODT Document",
    author: str = "Claire Author",
    date: str = "2026-09-11",
    headings: list[tuple[int, str]] | None = None,
    paragraphs: list[str] | None = None,
    include_table: bool = True,
    empty_content: bool = False,
) -> bytes:
    """인메모리 ODT 바이너리 생성 헬퍼."""
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr("mimetype", "application/vnd.oasis.opendocument.text")

        meta_xml = f"""<?xml version="1.0" encoding="UTF-8"?>
<office:document-meta xmlns:office="urn:oasis:names:tc:opendocument:xmlns:office:1.0"
                      xmlns:dc="http://purl.org/dc/elements/1.1/"
                      xmlns:meta="urn:oasis:names:tc:opendocument:xmlns:meta:1.0">
  <office:meta>
    <dc:title>{title}</dc:title>
    <dc:creator>{author}</dc:creator>
    <dc:date>{date}</dc:date>
    <dc:description>Sample description for ODT</dc:description>
    <meta:keyword>AI</meta:keyword>
    <meta:keyword>Knowledge</meta:keyword>
  </office:meta>
</office:document-meta>"""
        zf.writestr("meta.xml", meta_xml)

        if empty_content:
            content_xml = """<?xml version="1.0" encoding="UTF-8"?>
<office:document-content xmlns:office="urn:oasis:names:tc:opendocument:xmlns:office:1.0"
                         xmlns:text="urn:oasis:names:tc:opendocument:xmlns:text:1.0">
  <office:body>
    <office:text/>
  </office:body>
</office:document-content>"""
        else:
            body_parts = []
            if headings:
                for lvl, htext in headings:
                    body_parts.append(f'<text:h text:outline-level="{lvl}">{htext}</text:h>')
            else:
                body_parts.append(f'<text:h text:outline-level="1">{title}</text:h>')

            if paragraphs:
                for ptext in paragraphs:
                    body_parts.append(f'<text:p>{ptext}</text:p>')
            else:
                body_parts.append(
                    '<text:p>This is standard body paragraph with <text:a xlink:href="https://example.com/ref">a reference link</text:a>.</text:p>'
                )

            if include_table:
                table_xml = """<table:table table:name="Table1">
  <table:table-row>
    <table:table-cell><text:p>Header A</text:p></table:table-cell>
    <table:table-cell><text:p>Header B</text:p></table:table-cell>
  </table:table-row>
  <table:table-row>
    <table:table-cell><text:p>Val 1</text:p></table:table-cell>
    <table:table-cell><text:p>Val 2</text:p></table:table-cell>
  </table:table-row>
</table:table>"""
                body_parts.append(table_xml)

            content_xml = f"""<?xml version="1.0" encoding="UTF-8"?>
<office:document-content xmlns:office="urn:oasis:names:tc:opendocument:xmlns:office:1.0"
                         xmlns:text="urn:oasis:names:tc:opendocument:xmlns:text:1.0"
                         xmlns:table="urn:oasis:names:tc:opendocument:xmlns:table:1.0"
                         xmlns:xlink="http://www.w3.org/1999/xlink">
  <office:body>
    <office:text>
      {"".join(body_parts)}
    </office:text>
  </office:body>
</office:document-content>"""
        zf.writestr("content.xml", content_xml)

    return buf.getvalue()


def test_is_odt_bytes():
    odt_data = _create_sample_odt()
    assert is_odt_bytes(odt_data) is True
    assert is_odt_bytes(b"%PDF-1.4 dummy pdf bytes") is False
    assert is_odt_bytes(b"just some plain text") is False
    assert is_odt_bytes(b"") is False


def test_extract_odt_bytes_structure():
    odt_data = _create_sample_odt(
        title="Knowledge Architecture",
        author="Claire Team",
        date="2026-09-11",
        headings=[(1, "Main Section"), (2, "Sub Section")],
        paragraphs=[
            "First paragraph text.",
            'Second paragraph with <text:a xlink:href="https://example.com/paper">link anchor</text:a>.',
        ],
        include_table=True,
    )
    title, text, links, anchors, err, imgs = extract_odt_bytes(odt_data)
    assert err is None
    assert title == "Knowledge Architecture"
    assert "# Main Section" in text
    assert "## Sub Section" in text
    assert "First paragraph text." in text
    assert "| Header A | Header B |" in text
    assert "| Val 1 | Val 2 |" in text
    assert links == ["https://example.com/paper"]
    assert anchors.get("https://example.com/paper") == "link anchor"


def test_extract_odt_bytes_empty():
    odt_data = _create_sample_odt(empty_content=True)
    title, text, links, anchors, err, imgs = extract_odt_bytes(odt_data)
    assert err == "empty ODT content"
    assert text == ""


def test_fetch_file_odt(tmp_path: Path):
    odt_path = tmp_path / "sample_doc.odt"
    odt_data = _create_sample_odt(
        title="Sample ODT Report",
        author="Alice",
        date="2026-09-11",
    )
    odt_path.write_bytes(odt_data)

    doc = fetch_file(str(odt_path))
    assert doc.source_type == "odt"
    assert doc.title == "Sample ODT Report"
    assert doc.author is None
    assert doc.published_at is None
    assert "| Header A | Header B |" in doc.raw_text
    assert doc.meta["odt_parser_used"] == "odt"
    assert doc.meta["raw_truncated"] is False
    assert "biblio" not in doc.meta


def test_fetch_file_pdf_prioritizes_odt(tmp_path: Path):
    """PDF 파일 경로가 전달되었을 때 동일 이름의 ODT 파일이 존재하면 ODT를 우선 로드하는지 검증."""
    pdf_path = tmp_path / "annual_report.pdf"
    odt_path = tmp_path / "annual_report.odt"

    # PDF는 더미 파일, ODT는 온전한 구조화 데이터 생성
    pdf_path.write_bytes(b"%PDF-1.4 dummy pdf bytes")
    odt_data = _create_sample_odt(
        title="Annual Report ODT",
        author="Finance Team",
        date="2026-09-11",
    )
    odt_path.write_bytes(odt_data)

    # PDF 경로를 직접 주어도 ODT로 대체되어야 함
    doc = fetch_file(str(pdf_path))
    assert doc.source_type == "odt"
    assert doc.title == "Annual Report ODT"
    assert doc.author is None
    assert doc.meta["format_preference"] == "odt_over_pdf"
    assert doc.meta["original_requested_file"] == str(pdf_path.resolve())
    assert doc.url == f"file://{odt_path.resolve()}"


def test_prioritize_odt_links():
    urls = [
        "https://example.com/reports/financial_2026.pdf",
        "https://example.com/reports/financial_2026.odt",
        "https://example.com/articles/index.html",
        "https://example.com/papers/research.pdf",
    ]
    prioritized = prioritize_odt_links(urls)
    # financial_2026.pdf는 financial_2026.odt가 있으므로 제외되어야 함
    assert "https://example.com/reports/financial_2026.odt" in prioritized
    assert "https://example.com/reports/financial_2026.pdf" not in prioritized
    # 다른 파일들은 유지되어야 함
    assert "https://example.com/articles/index.html" in prioritized
    assert "https://example.com/papers/research.pdf" in prioritized
    # ODT가 앞쪽에 정렬되어야 함
    assert prioritized[0] == "https://example.com/reports/financial_2026.odt"


def test_fetch_static_detects_odt(monkeypatch):
    odt_data = _create_sample_odt(title="Web Hosted ODT", author="Web Author")

    class FakeResponse:
        status_code = 200
        url = "https://example.org/downloads/doc?id=123"
        headers = {"content-type": "application/vnd.oasis.opendocument.text; charset=utf-8"}
        content = odt_data
        text = ""

    class FakeClient:
        def __init__(self, *args, **kwargs):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *args):
            pass

        def get(self, url):
            return FakeResponse()

    monkeypatch.setattr("httpx.Client", FakeClient)

    res = _fetch_static("https://example.org/downloads/doc?id=123")
    title, text, links, anchors, err, eff_url, imgs, is_pdf = res[:8]
    assert err is None
    assert title == "Web Hosted ODT"
    assert getattr(res, "doc_type", None) == "odt"


def test_fetch_web_odt_url(monkeypatch):
    rich_text = "OpenDocument Text Standard Format for structured knowledge base " * 15
    monkeypatch.setattr(
        "claire.ingest.fetchers.web._fetch_static",
        lambda url: (
            "Remote ODT Document",
            rich_text,
            ["https://example.com/ref"],
            {},
            None,
            url,
            [],
            False,
            {"author": "Remote Author"},
            {"odt_parser_used": "odt"},
            "odt",
        ),
    )

    doc = fetch_web("https://example.org/docs/whitepaper.odt")
    assert doc.source_type == "odt"
    assert doc.title == "Remote ODT Document"
    assert doc.author is None
    assert doc.meta["fetch_via"] == "static"
    assert "biblio" not in doc.meta


def test_router_classify_odt(tmp_path: Path):
    odt_file = tmp_path / "test_classify.odt"
    odt_file.write_bytes(_create_sample_odt())
    assert classify(str(odt_file)) == "file"
    assert classify(f"file://{odt_file.resolve()}") == "file"


def test_find_candidates_prioritizes_odt():
    """1홉 후보 탐색 시 동일 문서의 .odt 와 .pdf 가 있으면 .odt를 남기고 .pdf는 제외."""
    conn = dbm.connect(":memory:")
    dbm.init_db(conn)

    doc = Document(
        url="https://example.com/index.html",
        raw_text="Download here: https://example.com/manual.pdf or https://example.com/manual.odt and https://example.com/guide.html",
        meta={"links": ["https://example.com/manual.pdf", "https://example.com/manual.odt", "https://example.com/guide.html"]},
    )
    candidates = find_candidates(conn, doc, limit=5)
    assert "https://example.com/manual.odt" in candidates
    assert "https://example.com/manual.pdf" not in candidates
    assert "https://example.com/guide.html" in candidates
    conn.close()
