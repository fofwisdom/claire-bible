"""원문 절단 메타데이터 기록 및 웹 UI docmeta 렌더링 검증 테스트."""

import pytest

from claire.extract.table_budget import (
    slice_text_with_table_exemption,
    slice_text_with_table_exemption_info,
)
from claire.graphview import document_detail, render_graph_html, shared_html
from claire.ingest.fetchers.textfile import fetch_file, fetch_text
from claire.ingest.fetchers.web import fetch_web
from claire.ontology.base import Document
from claire.store import db as dbm

from tests.js_i18n import js_i18n_prelude


def test_slice_text_with_table_exemption_info_short_text():
    text = "Short text under budget."
    sliced, is_trunc, orig_chars, sliced_chars = slice_text_with_table_exemption_info(text, limit=1000)
    assert sliced == text
    assert is_trunc is False
    assert orig_chars == len(text)
    assert sliced_chars == len(text)


def test_slice_text_with_table_exemption_info_long_text_truncated():
    prose = "A" * 15000
    sliced, is_trunc, orig_chars, sliced_chars = slice_text_with_table_exemption_info(prose, limit=10000)
    assert len(sliced) == 10000
    assert is_trunc is True
    assert orig_chars == 15000
    assert sliced_chars == 10000


def test_slice_text_with_table_exemption_info_with_table():
    prose_before = "P" * 8000
    table = "\n| Col1 | Col2 |\n|---|---|\n| Val1 | Val2 |\n"
    prose_after = "Q" * 7000  # Total prose = 15,000
    full_text = prose_before + table + prose_after

    sliced, is_trunc, orig_chars, sliced_chars = slice_text_with_table_exemption_info(full_text, limit=10000)
    assert is_trunc is True
    assert orig_chars == len(full_text)
    # Table must be completely preserved
    assert table in sliced
    # Prose before + prose after should sum to 10,000
    assert "P" * 8000 in sliced
    assert "Q" * 2000 in sliced
    assert "Q" * 2001 not in sliced


def test_fetch_text_meta_truncation():
    text = "Simple note text."
    doc = fetch_text(text)
    assert doc.meta.get("raw_truncated") is False
    assert doc.meta.get("orig_chars") == len(text)
    assert doc.meta.get("raw_chars") == len(text)


def test_fetch_file_meta_truncation(tmp_path):
    # Short file
    f_short = tmp_path / "short.txt"
    f_short.write_text("Hello world", encoding="utf-8")
    doc_short = fetch_file(str(f_short))
    assert doc_short.meta.get("raw_truncated") is False
    assert doc_short.meta.get("orig_chars") == 11
    assert doc_short.meta.get("raw_chars") == 11

    # Long file (>20,000 chars)
    f_long = tmp_path / "long.txt"
    long_content = "X" * 25000
    f_long.write_text(long_content, encoding="utf-8")
    doc_long = fetch_file(str(f_long))
    assert doc_long.meta.get("raw_truncated") is True
    assert doc_long.meta.get("orig_chars") == 25000
    assert doc_long.meta.get("raw_chars") == 20000
    assert len(doc_long.raw_text) == 20000


def test_fetch_web_meta_truncation(monkeypatch):
    from claire.ingest.fetchers import web

    # Mock _fetch_static to return >20,000 chars
    long_body = "This is a detailed paragraph. " * 1000  # ~30,000 chars
    monkeypatch.setattr(
        web,
        "_fetch_static",
        lambda url: ("Test Web Title", long_body, ["https://example.com/sub"], {}, None, url, []),
    )
    monkeypatch.setattr(web, "_is_usable", lambda title, text: (True, None))

    doc = fetch_web("https://example.com/long-article")
    assert doc.meta.get("raw_truncated") is True
    assert doc.meta.get("orig_chars") == len(long_body)
    assert doc.meta.get("raw_chars") == len(doc.raw_text)
    assert len(doc.raw_text) == 20000


def test_document_detail_returns_truncation_meta():
    import sqlite3

    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    dbm.init_db(conn)
    doc = Document(
        id="doc_trunc_test",
        title="Truncated Doc Test",
        url="https://example.com/test",
        raw_text="A" * 20000,
        source_type="web",
        meta={
            "raw_truncated": True,
            "orig_chars": 35000,
            "raw_chars": 20000,
        },
    )
    dbm.insert_document(conn, doc)

    detail = document_detail(conn, "doc_trunc_test")
    assert detail is not None
    assert detail["raw_truncated"] is True
    assert detail["orig_chars"] == 35000
    assert detail["raw_chars"] == 20000
    assert detail["meta"]["raw_truncated"] is True


def test_graph_html_contains_truncation_ui_and_css():
    html = render_graph_html()
    assert ".trunc-tag" in html
    assert ".focus-tag" in html
    assert ".stt-tag" in html
    assert ".cc-tag" in html
    assert ".vlm-tag" in html
    assert "docMetaHtml" in html
    # The cut of the source text is one notice in the reader: the source badge has the counts, and its hover text has the reason.
    assert "reader.trunc.limit" in html
    assert 'data-source-badge="partial"' in html
    assert '<span data-icon="mic"></span> STT' in html
    assert '<span data-icon="captions"></span> CC' in html
    assert "reader.tag.vlm_label" in html
    assert "reader.tag.stt" in html
    assert "reader.tag.cc" in html
    assert "reader.tag.vlm" in html
    assert "reader.trunc.appendix" in html
    assert "reader.trunc.limit" in html
    assert "작성된 본문" not in html
    assert "적재되었습니다" not in html
    # 20,000자가 하드코딩되어 있지 않고 동적이어야 함
    assert "(20,000자)" not in html


def test_shared_html_contains_docmeta_ui_and_css():
    doc = {
        "id": "doc_test_share",
        "title": "공유 문서 테스트",
        "url": "https://example.com/share-doc",
        "source_type": "web",
        "raw_truncated": True,
        "appendix_truncated": True,
        "orig_chars": 50000,
        "raw_chars": 20000,
        "is_stt": True,
        "summary": "테스트 요약",
    }
    html = shared_html(doc)
    assert ".docmeta" in html
    assert ".docmeta-tags" in html
    assert ".trunc-tag" in html
    assert ".trunc-tag.trunc-appendix" in html
    assert ".focus-tag" in html
    assert ".stt-tag" in html
    assert ".cc-tag" in html
    assert ".vlm-tag" in html
    assert "docMetaHtml" in html
    assert "share.trunc_part" in html
    assert '<span data-icon="mic"></span> STT' in html
    assert "reader.tag.stt" in html
    assert "reader.trunc.appendix" in html
    assert "reader.trunc.limit" in html
    assert "작성된 본문" not in html
    assert "적재되었습니다" not in html
    assert "h1 .rmeta" in html
    assert "(20,000자)" not in html


def test_document_detail_and_ui_with_vlm():
    import sqlite3

    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    dbm.init_db(conn)
    doc = Document(
        id="doc_vlm_test",
        title="VLM Infographic Test",
        url="https://example.com/infographic",
        raw_text="# Title\n\n## Infographic & Image Transcription\n\n### Card 1\nDetails",
        source_type="hoyolab",
        meta={
            "vlm_transcribed": True,
            "vlm_images_count": 5,
            "vlm_model": "gemini-3.5-flash-lite",
            "raw_truncated": False,
        },
    )
    dbm.insert_document(conn, doc)

    detail = document_detail(conn, "doc_vlm_test")
    assert detail is not None
    assert detail["vlm_transcribed"] is True
    assert detail["vlm_images_count"] == 5
    assert detail["vlm_model"] == "gemini-3.5-flash-lite"
    assert detail["meta"]["vlm_transcribed"] is True

    html = shared_html(detail)
    assert ".vlm-tag" in html
    assert "reader.tag.vlm_label" in html
    assert "reader.tag.vlm" in html

    # Node.js 런타임 평가 검증
    import json
    import subprocess
    from claire.graphview import GRAPH_HTML
    start = GRAPH_HTML.index("function docMetaHtml(dc){")
    end = GRAPH_HTML.index("function renderReader(dc){")
    fn = GRAPH_HTML[start:end]
    js = f"""
    function esc(s){{ return String(s||'').replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;').replace(/"/g,'&quot;'); }}
    {fn}
    const d = {json.dumps(detail)};
    console.log(docMetaHtml(d));
    """
    res = subprocess.run(["node", "-e", js_i18n_prelude() + js], capture_output=True, text=True, check=True)
    out = res.stdout
    assert '<span data-icon="image"></span> Image transcription (5 images)' in out
    assert "vlm-tag" in out
    assert "Document written with image transcription (VLM) (5 images transcribed) · Model: gemini-3.5-flash-lite" in out

    msg_start = GRAPH_HTML.index("function getDocMetaMessages(dc){")
    msg_end = GRAPH_HTML.index("function docMetaMessageHtml(dc){")
    msg_fn = GRAPH_HTML[msg_start:msg_end]
    js_msg = f"""
    function esc(s){{ return String(s||'').replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;').replace(/"/g,'&quot;'); }}
    {msg_fn}
    const d = {json.dumps(detail)};
    console.log(JSON.stringify(getDocMetaMessages(d)));
    """
    res_msg = subprocess.run(["node", "-e", js_i18n_prelude() + js_msg], capture_output=True, text=True, check=True)
    msgs = json.loads(res_msg.stdout)
    assert any("Document written with image transcription (VLM) (5 images transcribed) · Model: gemini-3.5-flash-lite" in m for m in msgs)

    # With vlm_language and stt_language specified
    detail_lang = dict(detail)
    detail_lang["vlm_language"] = "ko"
    js_lang = f"""
    function esc(s){{ return String(s||'').replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;').replace(/"/g,'&quot;'); }}
    {fn}
    {msg_fn}
    const d = {json.dumps(detail_lang)};
    console.log(JSON.stringify({{html: docMetaHtml(d), msgs: getDocMetaMessages(d)}}));
    """
    res_l = subprocess.run(["node", "-e", js_i18n_prelude() + js_lang], capture_output=True, text=True, check=True)
    out_l = json.loads(res_l.stdout)
    assert "Document written with image transcription (VLM: ko) (5 images transcribed)" in out_l["html"]
    assert any("Document written with image transcription (VLM: ko)" in m for m in out_l["msgs"])

    # STT language
    stt_detail = {
        "id": "doc_stt_lang",
        "url": "https://example.com/stt",
        "is_stt": True,
        "stt_language": "ko",
        "raw_text": "STT raw text",
    }
    js_stt = f"""
    function esc(s){{ return String(s||'').replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;').replace(/"/g,'&quot;'); }}
    {fn}
    {msg_fn}
    const d = {json.dumps(stt_detail)};
    console.log(JSON.stringify({{html: docMetaHtml(d), msgs: getDocMetaMessages(d)}}));
    """
    res_stt = subprocess.run(["node", "-e", js_i18n_prelude() + js_stt], capture_output=True, text=True, check=True)
    out_stt = json.loads(res_stt.stdout)
    assert "Document written with speech recognition (STT: ko)" in out_stt["html"]
    assert any("Document written with speech recognition (STT: ko)" in m for m in out_stt["msgs"])



def test_document_detail_and_ui_with_stt():
    import sqlite3

    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    dbm.init_db(conn)
    doc = Document(
        id="doc_stt_test",
        title="STT Video Test",
        url="https://example.com/video",
        raw_text="STT transcribed speech content",
        source_type="video",
        meta={
            "is_stt": True,
            "has_transcript": True,
            "raw_truncated": False,
        },
    )
    dbm.insert_document(conn, doc)

    detail = document_detail(conn, "doc_stt_test")
    assert detail is not None
    assert detail["is_stt"] is True
    assert detail["meta"]["is_stt"] is True


def test_shared_html_contains_cc_docmeta_badge():
    import json
    import subprocess
    from claire.graphview import GRAPH_HTML

    doc = {
        "id": "doc_video_cc",
        "title": "AI 에이전트를 위한 Playwright E2E 테스트 하네스 구축하기",
        "url": "https://tv.naver.com/v/101263731",
        "source_type": "video",
        "has_transcript": True,
        "is_stt": False,
        "caption_language": "ko_KR",
        "meta": {
            "has_transcript": True,
            "is_stt": False,
            "transcript_source": "manual_caption",
            "caption_status": "available",
            "caption_language": "ko_KR",
        },
        "summary": "네이버 D2 발표 요약",
    }
    html = shared_html(doc)
    assert ".cc-tag" in html
    assert '<span data-icon="captions"></span> CC' in html
    assert "reader.tag.cc" in html
    assert "reader.tag.cc_lang" in html

    # Node.js 런타임 평가 검증
    start = GRAPH_HTML.index("function docMetaHtml(dc){")
    end = GRAPH_HTML.index("function renderReader(dc){")
    fn = GRAPH_HTML[start:end]
    js = f"""
    function esc(s){{ return String(s||'').replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;').replace(/"/g,'&quot;'); }}
    {fn}
    const d = {json.dumps(doc)};
    console.log(docMetaHtml(d));
    """
    res = subprocess.run(["node", "-e", js_i18n_prelude() + js], capture_output=True, text=True, check=True)
    out = res.stdout
    assert '<span data-icon="captions"></span> CC' in out
    assert "cc-tag" in out
    assert "Document written with video subtitles (CC: ko_KR)" in out
    assert '<span data-icon="mic"></span> STT' not in out


def test_document_detail_and_ui_with_cc():
    import sqlite3

    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    dbm.init_db(conn)
    doc = Document(
        id="doc_cc_test",
        title="CC Video Test",
        url="https://tv.naver.com/v/101263731",
        raw_text="Manual caption video content",
        source_type="video",
        meta={
            "is_stt": False,
            "has_transcript": True,
            "transcript_source": "manual_caption",
            "caption_status": "available",
            "caption_language": "ko_KR",
            "raw_truncated": False,
        },
    )
    dbm.insert_document(conn, doc)

    detail = document_detail(conn, "doc_cc_test")
    assert detail is not None
    assert detail["has_transcript"] is True
    assert detail["is_stt"] is False
    assert detail["caption_status"] == "available"
    assert detail["caption_language"] == "ko_KR"
    assert detail["transcript_source"] == "manual_caption"


def test_docmeta_rollover_shows_total_character_count():
    import json
    import subprocess
    from claire.graphview import GRAPH_HTML

    # Case 1: non-truncated document with raw_chars
    doc1 = {
        "id": "doc_test_chars_1",
        "title": "Title 1",
        "url": "https://example.com/1",
        "orig_chars": 15420,
        "raw_chars": 15420,
    }
    # Case 2: truncated document with orig_chars and raw_chars
    doc2 = {
        "id": "doc_test_chars_2",
        "title": "Title 2",
        "url": "https://example.com/2",
        "orig_chars": 50000,
        "raw_chars": 20000,
        "raw_truncated": True,
    }

    start = GRAPH_HTML.index("function docMetaHtml(dc){")
    end = GRAPH_HTML.index("function renderReader(dc){")
    fn = GRAPH_HTML[start:end]
    js = f"""
    function esc(s){{ return String(s||'').replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;').replace(/"/g,'&quot;'); }}
    {fn}
    const d1 = {json.dumps(doc1)};
    const d2 = {json.dumps(doc2)};
    console.log("OUT1:" + docMetaHtml(d1));
    console.log("OUT2:" + docMetaHtml(d2));
    """
    res = subprocess.run(["node", "-e", js_i18n_prelude() + js], capture_output=True, text=True, check=True)
    out = res.stdout
    assert 'title="Total characters: 15,420"' in out
    assert 'title="Total characters: 50,000 (ingested: 20,000)"' in out

