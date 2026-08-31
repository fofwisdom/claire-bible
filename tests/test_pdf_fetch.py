"""PDF fetcher 및 추출 기능 테스트."""

from __future__ import annotations

import io
from pathlib import Path

import pypdf
import pytest

from claire.ingest.fetchers.base import FetchError
from claire.ingest.fetchers.pdf import extract_pdf_bytes, extract_pdf_stream
from claire.ingest.fetchers.textfile import fetch_file
from claire.ingest.fetchers.web import fetch_web


def _create_sample_pdf(title: str = "Test PDF Title", content: str = "This is sample PDF text content.") -> bytes:
    writer = pypdf.PdfWriter()
    writer.add_metadata({
        "/Title": title,
        "/Author": "Claire Author",
    })
    writer.add_blank_page(width=200, height=200)
    # pypdf writer doesn't easily draw text without reportlab, but let's test metadata & structure
    buf = io.BytesIO()
    writer.write(buf)
    return buf.getvalue()


def test_extract_pdf_bytes_corrupted():
    t, text, links, anchors, err, imgs = extract_pdf_bytes(b"not a real pdf")
    assert err is not None
    assert text == ""


def test_extract_pdf_bytes_empty_content():
    pdf_data = _create_sample_pdf(title="Blank PDF", content="")
    title, text, links, anchors, err, imgs = extract_pdf_bytes(pdf_data, fallback_title="fallback")
    assert err == "empty PDF content"
    assert text == ""


def test_extract_pdf_stream_with_text(monkeypatch):
    class FakePage:
        def extract_text(self):
            return "This is a great research paper about AI finance. https://example.com/source"

        def get(self, key):
            return None

    class FakeReader:
        is_encrypted = False
        metadata = {"/Title": "Paper on AI Finance"}
        pages = [FakePage()]

    monkeypatch.setattr(pypdf, "PdfReader", lambda stream: FakeReader())
    title, text, links, anchors, err, imgs = extract_pdf_bytes(b"%PDF-1.4", fallback_title="fallback")
    assert title == "Paper on AI Finance"
    assert "research paper about AI finance" in text
    assert links == ["https://example.com/source"]
    assert err is None



def test_fetch_file_pdf(tmp_path: Path, monkeypatch):
    pdf_path = tmp_path / "sample.pdf"
    pdf_path.write_bytes(b"dummy pdf bytes")

    monkeypatch.setattr(
        "claire.ingest.fetchers.pdf.extract_pdf_bytes",
        lambda data, url=None, fallback_title=None: (
            "Extracted Title",
            "This is long extracted text from the PDF file for testing.",
            ["https://example.com/ref"],
            {},
            None,
            [],
        ),
    )

    doc = fetch_file(str(pdf_path))
    assert doc.title == "Extracted Title"
    assert doc.source_type == "pdf"
    assert "long extracted text" in doc.raw_text
    assert doc.url == f"file://{pdf_path.resolve()}"


def test_fetch_file_pdf_error(tmp_path: Path, monkeypatch):
    pdf_path = tmp_path / "corrupted.pdf"
    pdf_path.write_bytes(b"corrupted")

    monkeypatch.setattr(
        "claire.ingest.fetchers.pdf.extract_pdf_bytes",
        lambda data, url=None, fallback_title=None: (
            None,
            "",
            [],
            {},
            "corrupted PDF format",
            [],
        ),
    )

    with pytest.raises(FetchError) as exc_info:
        fetch_file(str(pdf_path))
    assert "corrupted PDF format" in str(exc_info.value)


def test_fetch_web_pdf_url(monkeypatch):
    rich_text = "NBER WORKING PAPER SERIES AI FINANCIAL ADVICE " * 30
    monkeypatch.setattr(
        "claire.ingest.fetchers.web._fetch_static",
        lambda url: (
            "AI Financial Advice",
            rich_text,
            ["https://example.com/paper"],
            {},
            None,
            url,
            [],
        ),
    )

    doc = fetch_web("https://www.nber.org/system/files/working_papers/w35574/w35574.pdf")
    assert doc.source_type == "pdf"
    assert doc.title == "AI Financial Advice"
    assert doc.meta["fetch_via"] == "static"
    assert doc.canonical_url == "https://nber.org/system/files/working_papers/w35574/w35574.pdf"


def test_fetch_static_detects_pdf_content_type(monkeypatch):
    """_fetch_static이 Content-Type: application/pdf 및 쿼리 URL을 PDF로 올바르게 판정하는지 검증."""
    from claire.ingest.fetchers.web import _fetch_static

    fake_pdf = b"%PDF-1.4 sample content"

    monkeypatch.setattr(
        "claire.ingest.fetchers.pdf.extract_pdf_bytes",
        lambda data, url=None, fallback_title=None: (
            "KDB Smart Construction",
            "Smart Construction Research Content in full detail",
            ["https://example.com/ref"],
            {},
            None,
            [],
        ),
    )

    class FakeResponse:
        status_code = 200
        url = "https://file.kdb.co.kr/fileView?groupId=F5A76F50&fileId=4E3E5F35"
        headers = {"content-type": "application/pdf; charset=utf-8"}
        content = fake_pdf
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

    title, text, links, anchors, err, eff_url, imgs, is_pdf = _fetch_static(
        "https://file.kdb.co.kr/fileView?groupId=F5A76F50&fileId=4E3E5F35"
    )
    assert is_pdf is True
    assert err is None
    assert title == "KDB Smart Construction"
    assert "Smart Construction" in text
    assert eff_url == "https://file.kdb.co.kr/fileView?groupId=F5A76F50&fileId=4E3E5F35"


def test_fetch_file_magic_bytes_without_pdf_extension(tmp_path: Path, monkeypatch):
    """확장자가 없는 파일이라도 %PDF- 매직 바이트가 있으면 source_type='pdf'로 처리되는지 검증."""
    monkeypatch.setattr(
        "claire.ingest.fetchers.pdf.extract_pdf_bytes",
        lambda data, url=None, fallback_title=None: (
            "Magic Header PDF",
            "PDF text content without extension.",
            [],
            {},
            None,
            [],
        ),
    )
    doc_path = tmp_path / "downloaded_blob"
    doc_path.write_bytes(b"%PDF-1.7 binary data")

    doc = fetch_file(str(doc_path))
    assert doc.source_type == "pdf"
    assert doc.title == "Magic Header PDF"
    assert "PDF text content" in doc.raw_text
    assert doc.meta["raw_truncated"] is False

