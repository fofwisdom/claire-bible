"""Unit tests for pypdfium2 PDF extraction engine."""

from __future__ import annotations

import io
from pathlib import Path
from unittest.mock import patch

import pypdf
import pytest

from claire.core.config import get_settings
from claire.ingest.fetchers.pdf import (
    PdfExtractResult,
    extract_pdf_bytes,
    extract_pdf_stream,
    extract_pdf_stream_pypdf,
    extract_pdf_stream_pypdfium2,
)


def _create_minimal_pdf_with_text(text: str = "Hello PDFium World!") -> bytes:
    """Create a valid PDF containing text using raw PDF streams."""
    content = f"BT\n/F1 18 Tf\n50 150 Td\n({text}) Tj\nET\n".encode("latin1", errors="ignore")
    c_len = len(content)
    obj4 = b"4 0 obj\n<< /Length " + str(c_len).encode("ascii") + b" >>\nstream\n" + content + b"endstream\nendobj\n"
    header = (
        b"%PDF-1.4\n"
        b"1 0 obj\n<< /Type /Catalog /Pages 2 0 R >>\nendobj\n"
        b"2 0 obj\n<< /Type /Pages /Kids [3 0 R] /Count 1 >>\nendobj\n"
        b"3 0 obj\n<< /Type /Page /Parent 2 0 R /MediaBox [0 0 300 300] /Contents 4 0 R /Resources << /Font << /F1 5 0 R >> >> >>\nendobj\n"
    )
    obj5 = b"5 0 obj\n<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>\nendobj\n"

    o1 = 9
    o2 = o1 + len(b"1 0 obj\n<< /Type /Catalog /Pages 2 0 R >>\nendobj\n")
    o3 = o2 + len(b"2 0 obj\n<< /Type /Pages /Kids [3 0 R] /Count 1 >>\nendobj\n")
    o4 = o3 + len(b"3 0 obj\n<< /Type /Page /Parent 2 0 R /MediaBox [0 0 300 300] /Contents 4 0 R /Resources << /Font << /F1 5 0 R >> >> >>\nendobj\n")
    o5 = o4 + len(obj4)

    xref_offset = o5 + len(obj5)
    xref = (
        f"xref\n0 6\n0000000000 65535 f \n{o1:010d} 00000 n \n{o2:010d} 00000 n \n{o3:010d} 00000 n \n{o4:010d} 00000 n \n{o5:010d} 00000 n \n"
        f"trailer\n<< /Size 6 /Root 1 0 R >>\nstartxref\n{xref_offset}\n%%EOF\n"
    ).encode("ascii")

    return header + obj4 + obj5 + xref


def test_pypdfium2_extracts_text_and_default_engine():
    """Verify pypdfium2 extracts text from valid PDF and is the default engine."""
    get_settings.cache_clear()
    pdf_data = _create_minimal_pdf_with_text("Hello PDFium World!")

    res = extract_pdf_bytes(pdf_data)
    assert res.error is None
    assert "Hello PDFium World!" in res.text
    assert res.parser_requested == "pypdfium2"
    assert res.parser_used == "pypdfium2"
    assert not res.parser_fallback
    assert not res.encoding_flaw_detected


def test_pypdfium2_metadata_and_title():
    """Verify pypdfium2 extracts metadata title or generates fallback title."""
    writer = pypdf.PdfWriter()
    writer.add_metadata({
        "/Title": "Quantum Computing Advances",
        "/Author": "Dr. Claire",
    })
    writer.add_blank_page(width=200, height=200)
    buf = io.BytesIO()
    writer.write(buf)

    res = extract_pdf_stream_pypdfium2(io.BytesIO(buf.getvalue()))
    assert res.error == "empty PDF content"

    # With text content
    pdf_text_data = _create_minimal_pdf_with_text("Quantum supremacy achieved in laboratory.")
    res2 = extract_pdf_stream_pypdfium2(io.BytesIO(pdf_text_data), fallback_title="Fallback Title")
    assert res2.error is None
    assert "Quantum supremacy" in (res2.title or "") or "Quantum supremacy" in res2.text


def test_pypdfium2_extracts_links():
    """Verify pypdfium2 extracts URL links from text."""
    pdf_data = _create_minimal_pdf_with_text("Visit https://example.com/research for paper")
    res = extract_pdf_bytes(pdf_data)
    assert "https://example.com/research" in res.links



def _create_pdf_with_wrapped_link(full_url: str) -> bytes:
    """Create a PDF whose visible URL wraps after "https://www" and whose link annotation holds the full URL."""
    content = b"BT\n/F1 12 Tf\n20 250 Td\n(See https://www) Tj\n0 -20 Td\n(.example.org/long/path for data) Tj\nET\n"
    objs = [
        b"<< /Type /Catalog /Pages 2 0 R >>",
        b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
        b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 300 300] /Contents 4 0 R /Resources << /Font << /F1 5 0 R >> >> /Annots [6 0 R] >>",
        b"<< /Length " + str(len(content)).encode() + b" >>\nstream\n" + content + b"endstream",
        b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>",
        b"<< /Type /Annot /Subtype /Link /Rect [20 220 280 265] /Border [0 0 0] /A << /S /URI /URI (" + full_url.encode() + b") >> >>",
    ]
    out = b"%PDF-1.4\n"
    offsets = []
    for i, body in enumerate(objs, start=1):
        offsets.append(len(out))
        out += f"{i} 0 obj\n".encode() + body + b"\nendobj\n"
    xref = len(out)
    out += f"xref\n0 {len(objs) + 1}\n0000000000 65535 f \n".encode()
    out += b"".join(f"{o:010d} 00000 n \n".encode() for o in offsets)
    out += f"trailer\n<< /Size {len(objs) + 1} /Root 1 0 R >>\nstartxref\n{xref}\n%%EOF\n".encode()
    return out


def test_pypdfium2_link_annotation_replaces_wrapped_url_fragment():
    """The full URL comes from the link annotation; the text fragment cut at the line wrap is not kept."""
    full_url = "https://www.example.org/long/path"
    res = extract_pdf_stream_pypdfium2(io.BytesIO(_create_pdf_with_wrapped_link(full_url)))
    assert full_url in res.links
    assert "https://www" not in res.links
    assert all("." in u.split("/")[2] for u in res.links)

def test_pypdfium2_encrypted_pdf_handling():
    """Verify encrypted PDF returns 'encrypted PDF' error."""
    writer = pypdf.PdfWriter()
    writer.add_blank_page(width=100, height=100)
    writer.encrypt("super_secret_password")
    buf = io.BytesIO()
    writer.write(buf)

    res = extract_pdf_stream_pypdfium2(io.BytesIO(buf.getvalue()))
    assert res.error == "encrypted PDF"
    assert res.text == ""


def test_pypdfium2_corrupted_pdf_handling():
    """Verify corrupted bytes return extraction failure."""
    res = extract_pdf_stream_pypdfium2(io.BytesIO(b"not a valid pdf binary"))
    assert res.error is not None
    assert "PDF extraction failed" in res.error
    assert res.text == ""


def test_pypdfium2_fallback_to_pypdf_on_pypdfium_runtime_error():
    """Verify extract_pdf_stream falls back to pypdf if pypdfium2 encounters a runtime failure."""
    fake_pdf = b"%PDF-1.4 dummy"

    with patch("claire.ingest.fetchers.pdf.extract_pdf_stream_pypdfium2") as mock_pdfium:
        mock_pdfium.return_value = PdfExtractResult(
            None, "", [], {}, "PDFium C++ segmentation error", []
        )
        with patch("claire.ingest.fetchers.pdf.extract_pdf_stream_pypdf") as mock_pypdf:
            mock_pypdf.return_value = PdfExtractResult(
                "Fallback Title", "Recovered text via PyPDF", [], {}, None, []
            )

            res = extract_pdf_stream(io.BytesIO(fake_pdf))
            assert res.parser_used == "pypdf"
            assert res.parser_fallback is True
            assert "pypdfium2 런타임 오류" in (res.parser_fallback_reason or "")
            assert res.text == "Recovered text via PyPDF"
