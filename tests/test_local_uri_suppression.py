import json
import sqlite3
from pathlib import Path

from claire.graphview import shared_html
from claire.ingest.fetchers.textfile import fetch_file
from claire.ontology.base import Document, Entity
from claire.store import db as dbm
from claire.store.queries import document_detail, documents_list, node_detail


def test_fetch_file_does_not_set_local_file_url(tmp_path: Path, monkeypatch):
    """fetch_file() must set doc.url to None so local file paths are not exposed."""
    pdf_path = tmp_path / "research.pdf"
    pdf_path.write_bytes(b"%PDF-1.4 dummy binary")

    monkeypatch.setattr(
        "claire.ingest.fetchers.pdf.extract_pdf_bytes",
        lambda data, url=None, fallback_title=None: (
            "Research Title",
            "Extracted body content of research document.",
            [],
            {},
            None,
            [],
        ),
    )

    doc = fetch_file(str(pdf_path))
    assert doc.title == "Research Title"
    assert doc.source_type == "pdf"
    assert doc.url is None


def test_queries_sanitize_legacy_file_urls(tmp_path: Path):
    """queries layer must suppress local file:// URLs across document_detail, documents_list, and node_detail."""
    db_file = tmp_path / "claire.db"
    conn = sqlite3.connect(db_file)
    conn.row_factory = sqlite3.Row
    dbm.init_db(conn)

    legacy_url = "file:///app/data/themes/2/raw/files/707518267_sample.pdf"
    dbm.insert_document(
        conn,
        Document(
            id="doc_legacy",
            url=legacy_url,
            title="Legacy Local PDF",
            raw_text="Legacy document content.",
            source_type="pdf",
            content_hash="hash_legacy",
        ),
    )
    dbm.insert_document(
        conn,
        Document(
            id="doc_web",
            url="https://example.com/web-article",
            title="Web Article",
            raw_text="Web document content.",
            source_type="web",
            content_hash="hash_web",
        ),
    )
    dbm.upsert_entity(
        conn,
        Entity(
            id="ent_arch",
            type="Concept",
            name="Architecture",
            observations=["architectural study"],
            sources=["doc_legacy", "doc_web"],
        ),
    )
    conn.commit()

    # 1. document_detail
    detail_legacy = document_detail(conn, "doc_legacy")
    assert detail_legacy is not None
    assert detail_legacy["url"] is None

    detail_web = document_detail(conn, "doc_web")
    assert detail_web is not None
    assert detail_web["url"] == "https://example.com/web-article"

    # 2. documents_list
    doc_list = documents_list(conn)
    url_map = {d["id"]: d["url"] for d in doc_list}
    assert url_map["doc_legacy"] is None
    assert url_map["doc_web"] == "https://example.com/web-article"

    # 3. node_detail (ent.sources)
    node_info = node_detail(conn, "ent_arch")
    node_docs = {d["id"]: d["url"] for d in node_info["documents"]}
    assert node_docs["doc_legacy"] is None
    assert node_docs["doc_web"] == "https://example.com/web-article"

    conn.close()


def test_shared_html_suppresses_local_file_url():
    """shared_html must not leak file:/// paths in HTML or embedded docdata JSON."""
    legacy_doc = {
        "id": "doc_sample",
        "title": "Brutalism Architecture",
        "url": "file:///app/data/themes/2/raw/files/707518267_brutalism.pdf",
        "canonical_url": "file:///app/data/themes/2/raw/files/707518267_brutalism.pdf",
        "presentation_pdf": {
            "status": "available",
            "public_url": "file:///app/data/themes/2/raw/files/presentation.pdf",
        },
        "summary": "Brutalism architecture overview.",
        "detail": "Brutalism focuses on raw materials.",
    }

    html = shared_html(legacy_doc, base_url="https://cb.example.com", share_token="test_token")

    # Local URI must not appear anywhere in the rendered HTML
    assert "file:///app" not in html
    assert "707518267_brutalism.pdf" not in html
    assert "presentation.pdf" not in html
    assert '<a href="file:' not in html

    # Embedded docdata JSON must have null url
    script_marker = '<script id="docdata" type="application/json">'
    start = html.index(script_marker) + len(script_marker)
    end = html.index("</script>", start)
    embedded = json.loads(html[start:end])
    assert embedded["url"] is None
    assert embedded["canonical_url"] is None
    assert embedded["presentation_pdf"]["public_url"] is None


def test_shared_html_preserves_valid_web_url():
    """shared_html must render ↗ 원문 열기 for valid HTTP/HTTPS URLs."""
    web_doc = {
        "id": "doc_web_sample",
        "title": "Web Architecture",
        "url": "https://example.com/architecture",
        "canonical_url": "https://example.com/architecture",
        "summary": "Web architecture overview.",
        "detail": "Detailed article.",
    }

    html = shared_html(web_doc, base_url="https://cb.example.com", share_token="test_token_2")
    assert "https://example.com/architecture" in html
    assert "↗ 원문 열기" in html


def test_client_docmeta_html_runtime_suppresses_local_uri(tmp_path: Path):
    """Client JS docMetaHtml must suppress file:// URIs and only render ↗ 원문 열기 for web URLs."""
    import shutil
    import subprocess
    import tempfile
    import pytest

    if shutil.which("node") is None:
        import os
        nvm_node = Path(os.path.expanduser("~/.nvm/versions/node/v26.7.0/bin/node"))
        if nvm_node.is_file():
            os.environ["PATH"] = f"{nvm_node.parent}:{os.environ.get('PATH', '')}"
        else:
            pytest.skip("Node.js is not installed on the system")

    share_tmpl_path = Path(__file__).resolve().parent.parent / "src" / "claire" / "templates" / "share.html"
    content = share_tmpl_path.read_text(encoding="utf-8")
    fn_start = content.find("function docMetaHtml(dc)")
    assert fn_start != -1
    fn_end = content.find("function docBiblioHtml(dc)", fn_start)
    assert fn_end != -1
    docmeta_js = content[fn_start:fn_end]

    runner_code = f"""
function esc(s){{return (s||'').replace(/[&<>"]/g,c=>({{'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;'}}[c]));}}
{docmeta_js}

const localDoc = {{
  id: "doc_test_local",
  url: "file:///app/data/themes/2/raw/files/707518267_sample.pdf",
  title: "Local PDF",
  detail: "Sample detail"
}};

const webDoc = {{
  id: "doc_test_web",
  url: "https://example.com/article",
  title: "Web Article",
  detail: "Sample detail"
}};

const resLocal = docMetaHtml(localDoc);
const resWeb = docMetaHtml(webDoc);

console.log(JSON.stringify({{ resLocal, resWeb }}));
process.exit(0);
"""
    with tempfile.NamedTemporaryFile("w", suffix=".js", delete=False) as f_runner:
        f_runner.write(runner_code)
        runner_file = f_runner.name

    try:
        proc = subprocess.run(["node", runner_file], capture_output=True, text=True, timeout=5)
        assert proc.returncode == 0, proc.stderr
        out = json.loads(proc.stdout.strip())
        # Local file URI must not be rendered as link or present in html
        assert "file:///app" not in out["resLocal"]
        assert "↗ 원문 열기" not in out["resLocal"]

        # Web URL must be rendered as link
        assert "https://example.com/article" in out["resWeb"]
        assert "↗ 원문 열기" in out["resWeb"]
    finally:
        Path(runner_file).unlink(missing_ok=True)

