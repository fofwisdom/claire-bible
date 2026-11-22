"""Unit tests for DOCX parsing, zip-bomb defense, and bytes-based file staging."""

from __future__ import annotations

import io
import zipfile

import pytest

from claire.ingest.fetchers.base import FetchError
from claire.ingest.fetchers.docx import extract_docx_bytes, is_docx_bytes
from claire.ingest.fetchers.textfile import fetch_file_bytes


def make_sample_docx(*, title: str = "Q4 Architecture Review", empty: bool = False) -> bytes:
    """Build a minimal valid DOCX ZIP archive for fetcher and staging tests."""
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, mode="w", compression=zipfile.ZIP_DEFLATED) as zf:
        zf.writestr(
            "[Content_Types].xml",
            '<?xml version="1.0" encoding="UTF-8"?>'
            '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
            '<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>'
            '<Default Extension="xml" ContentType="application/xml"/>'
            '<Override PartName="/word/document.xml" ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.document.main+xml"/>'
            '<Override PartName="/docProps/core.xml" ContentType="application/vnd.openxmlformats-package.core-properties+xml"/>'
            "</Types>",
        )
        zf.writestr(
            "docProps/core.xml",
            '<?xml version="1.0" encoding="UTF-8"?>'
            '<cp:coreProperties xmlns:cp="http://schemas.openxmlformats.org/package/2006/metadata/core-properties" xmlns:dc="http://purl.org/dc/elements/1.1/">'
            f"<dc:title>{title}</dc:title>"
            "</cp:coreProperties>",
        )
        zf.writestr(
            "word/_rels/document.xml.rels",
            '<?xml version="1.0" encoding="UTF-8"?>'
            '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
            '<Relationship Id="rId5" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/hyperlink" Target="https://example.org/spec" TargetMode="External"/>'
            "</Relationships>",
        )
        if empty:
            body_xml = "<w:body/>"
        else:
            body_xml = (
                "<w:body>"
                '<w:p><w:pPr><w:pStyle w:val="Heading1"/></w:pPr><w:r><w:t>Overview</w:t></w:r></w:p>'
                '<w:p><w:r><w:t>Claire Bible indexes graphs via </w:t></w:r><w:hyperlink r:id="rId5"><w:r><w:t>MCP Spec</w:t></w:r></w:hyperlink></w:p>'
                '<w:p><w:pPr><w:numPr><w:ilvl w:val="0"/></w:numPr></w:pPr><w:r><w:t>Bullet Item One</w:t></w:r></w:p>'
                "<w:tbl><w:tr><w:tc><w:p><w:r><w:t>Component</w:t></w:r></w:p></w:tc><w:tc><w:p><w:r><w:t>Role</w:t></w:r></w:p></w:tc></w:tr>"
                "<w:tr><w:tc><w:p><w:r><w:t>MCPServer</w:t></w:r></w:p></w:tc><w:tc><w:p><w:r><w:t>Streamable HTTP</w:t></w:r></w:p></w:tc></w:tr></w:tbl>"
                "</w:body>"
            )
        zf.writestr(
            "word/document.xml",
            '<?xml version="1.0" encoding="UTF-8"?>'
            '<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main" xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships">'
            f"{body_xml}"
            "</w:document>",
        )
    return buf.getvalue()


def test_docx_detection_parsing_and_zip_bomb_guard() -> None:
    sample = make_sample_docx()
    assert is_docx_bytes(sample) is True
    assert is_docx_bytes(b"%PDF-1.5\n") is False
    assert is_docx_bytes(b"plain text") is False

    title, text, links, anchors, err, images = extract_docx_bytes(sample)
    assert err is None
    assert images == []
    assert title == "Q4 Architecture Review"
    assert "== Overview" in text
    assert "link:https://example.org/spec[MCP Spec]" in text
    assert "* Bullet Item One" in text
    assert "|===" in text and "| MCPServer | Streamable HTTP" in text
    assert "https://example.org/spec" in links
    assert anchors.get("https://example.org/spec") == "MCP Spec"

    _, _, _, _, empty_err, _ = extract_docx_bytes(make_sample_docx(empty=True))
    assert empty_err is not None

    _, _, _, _, limit_err, _ = extract_docx_bytes(sample, max_xml_bytes=32)
    assert limit_err and "exceeds" in limit_err


def test_fetch_file_bytes_formats_and_size_cap() -> None:
    docx_doc = fetch_file_bytes(make_sample_docx(title="Spec File"), filename="arch.docx", full_content=True)
    assert docx_doc.source_type == "docx"
    assert docx_doc.title == "Spec File"
    assert docx_doc.meta["file_format"] == "docx"

    md_doc = fetch_file_bytes(b"# Heading\n\nBody line", filename="notes.md", full_content=True)
    assert md_doc.source_type == "file" and md_doc.title == "notes"

    with pytest.raises(FetchError, match="exceeds size limit"):
        fetch_file_bytes(b"x" * 64, filename="tiny.txt", max_bytes=16)

    with pytest.raises(FetchError, match="unsupported file type"):
        fetch_file_bytes(b"\x00\x01\x02\x03", filename="archive.bin")
