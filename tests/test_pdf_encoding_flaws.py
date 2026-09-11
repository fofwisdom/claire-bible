"""Unit tests for PDF text encoding flaw detection and Docling escalation."""

from __future__ import annotations

import io
import sqlite3
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from claire.config import get_settings
from claire.ingest.fetchers.pdf import (
    PdfExtractResult,
    detect_pdf_encoding_flaws,
    extract_pdf_stream,
)
from claire.ingest.fetchers.textfile import fetch_file
from claire.ingest.pipeline import IngestReport
from claire.ontology.base import Document
from claire.store import db as dbm
from claire.store.queries import document_detail


def test_clean_texts_have_no_flaws():
    """Verify normal Korean and English texts have no detected encoding flaws."""
    kr_text = "이 논문은 인공지능과 지식 그래프 결합에 관한 학술 연구입니다. 다양한 실험을 통해 정확도를 검증하였습니다."
    res_kr = detect_pdf_encoding_flaws(kr_text, page_count=1, image_count=0)
    assert not res_kr["has_flaw"]
    assert not res_kr["is_scanned"]
    assert res_kr["reasons"] == []

    en_text = "In this work, we present a novel neural architecture for large language model reasoning and knowledge graphs."
    res_en = detect_pdf_encoding_flaws(en_text, page_count=1, image_count=0)
    assert not res_en["has_flaw"]
    assert not res_en["is_scanned"]


def test_flaw_unmapped_cid_fonts():
    """Verify detection of unmapped CID fonts with (cid:xxx) tokens."""
    cid_text = "Mathematical theorem: (cid:10)(cid:20) and (cid:30) where (cid:40) denotes (cid:50)(cid:60)(cid:70) " * 5
    res = detect_pdf_encoding_flaws(cid_text, page_count=1)
    assert res["has_flaw"]
    assert any("unmapped_cid_fonts" in r for r in res["reasons"])


def test_flaw_pua_characters():
    """Verify detection of Unicode Private Use Area (PUA) characters."""
    pua_text = "Chapter 1: " + "\ue001\ue002\ue003\ue004\ue005\uf810\uf811" * 10
    res = detect_pdf_encoding_flaws(pua_text, page_count=1)
    assert res["has_flaw"]
    assert any("pua_characters" in r for r in res["reasons"])


def test_flaw_replacement_characters():
    """Verify detection of Unicode replacement character (\ufffd) bursts."""
    rep_text = "Unreadable section: " + "\ufffd\ufffd\ufffd\ufffd\ufffd\ufffd\ufffd\ufffd\ufffd\ufffd" * 5
    res = detect_pdf_encoding_flaws(rep_text, page_count=1)
    assert res["has_flaw"]
    assert any("replacement_chars" in r for r in res["reasons"])


def test_flaw_control_char_noise():
    """Verify detection of control character noise and null bytes."""
    ctrl_text = "Data stream: " + "\x01\x02\x03\x04\x05\x06\x07\x0e\x0f\x10\x11\x12" * 5
    res = detect_pdf_encoding_flaws(ctrl_text, page_count=1)
    assert res["has_flaw"]
    assert any("control_char_noise" in r for r in res["reasons"])


def test_flaw_scanned_low_density():
    """Verify detection of scanned / image-only PDFs with low text density."""
    # 5 pages, 10 characters total, 5 images -> scanned
    res = detect_pdf_encoding_flaws("1\n2\n3\n4\n5", page_count=5, image_count=5)
    assert res["has_flaw"]
    assert res["is_scanned"]
    assert any("scanned_low_density" in r for r in res["reasons"])


def test_flaw_mojibake():
    """Verify detection of UTF-8 text misdecoded as Latin-1."""
    mojibake_text = "Sample text with broken characters: Ã¬â‚¬í•œê¸€ Ã¬â‚¬í•œê¸€ Ã¬â‚¬í•œê¸€"
    res = detect_pdf_encoding_flaws(mojibake_text, page_count=1)
    assert res["has_flaw"]
    assert any("mojibake_encoding" in r for r in res["reasons"])


def test_flaw_decomposed_hangul_jamo():
    """Verify detection of decomposed Hangul compatibility jamo."""
    jamo_text = "ㄱㅏ ㄴㄷㅏ ㅂㅗㅁ ㅁㅜㄹ ㅅㅏㄴ " * 5
    res = detect_pdf_encoding_flaws(jamo_text, page_count=1)
    assert res["has_flaw"]
    assert any("decomposed_hangul_jamo" in r for r in res["reasons"])


def test_flaw_triggers_docling_escalation():
    """Verify that when pypdfium2 text has an encoding flaw, extract_pdf_stream escalates to Docling if available."""
    flawed_text = "Formula: (cid:10)(cid:20)(cid:30)(cid:40)(cid:50)(cid:60)(cid:70) " * 5
    recovered_text = "Clean recovered markdown from Docling layout model."

    with patch("claire.ingest.fetchers.pdf.extract_pdf_stream_pypdfium2") as mock_pdfium:
        mock_pdfium.return_value = PdfExtractResult(
            "Flawed Paper",
            flawed_text,
            [],
            {},
            None,
            [],
            parser_requested="pypdfium2",
            parser_used="pypdfium2",
            encoding_flaw_detected=True,
            encoding_flaws=["unmapped_cid_fonts (35 tokens)"],
        )
        with patch("claire.ingest.fetchers.pdf.extract_pdf_stream_docling") as mock_docling:
            mock_docling.return_value = PdfExtractResult(
                "Docling Recovered Paper",
                recovered_text,
                [],
                {},
                None,
                [],
                parser_requested="pypdfium2",
                parser_used="docling",
            )

            res = extract_pdf_stream(io.BytesIO(b"%PDF-1.4 dummy"))
            assert res.parser_used == "docling"
            assert res.parser_fallback is True
            assert "pypdfium2 인코딩 결함 감지" in (res.parser_fallback_reason or "")
            assert res.text == recovered_text


def test_encoding_flaw_metadata_in_ingest_report_and_queries(tmp_path: Path):
    """Verify encoding flaw metadata propagates to Document, IngestReport, and document_detail."""
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    dbm.init_db(conn)

    flawed_pdf = tmp_path / "flawed.pdf"
    flawed_pdf.write_bytes(b"%PDF-1.4 dummy")

    flawed_text = "Equation with (cid:11)(cid:22)(cid:33)(cid:44)(cid:55)(cid:66)(cid:77) " * 5

    with patch("claire.ingest.fetchers.pdf.extract_pdf_bytes") as mock_extract:
        mock_extract.return_value = PdfExtractResult(
            "Flawed Document",
            flawed_text,
            [],
            {},
            None,
            [],
            parser_requested="pypdfium2",
            parser_used="pypdfium2",
            encoding_flaw_detected=True,
            encoding_flaws=["unmapped_cid_fonts (35 tokens)"],
            is_scanned=False,
        )

        doc = fetch_file(str(flawed_pdf))
        assert doc.meta["pdf_encoding_flaw_detected"] is True
        assert "unmapped_cid_fonts (35 tokens)" in doc.meta["pdf_encoding_flaws"]
        assert doc.meta["pdf_is_scanned"] is False

        # IngestReport test
        report = IngestReport()
        report.title = doc.title
        report.source_type = "pdf"
        report.pdf_encoding_flaw_detected = doc.meta["pdf_encoding_flaw_detected"]
        report.pdf_encoding_flaws = doc.meta["pdf_encoding_flaws"]
        report.pdf_is_scanned = doc.meta["pdf_is_scanned"]

        summary = report.telegram_summary()
        assert "⚠️ PDF 텍스트 인코딩 결함 감지" in summary
        assert "unmapped_cid_fonts (35 tokens)" in summary

        # document_detail API test
        doc.id = "doc_flawed_test"
        dbm.insert_document(conn, doc)
        detail = document_detail(conn, "doc_flawed_test")
        assert detail["pdf_encoding_flaw_detected"] is True
        assert detail["pdf_encoding_flaws"] == ["unmapped_cid_fonts (35 tokens)"]
        assert detail["pdf_is_scanned"] is False
