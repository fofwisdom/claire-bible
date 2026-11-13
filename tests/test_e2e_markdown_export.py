"""End-to-end test of the Markdown export of a document (RD24, MD-3, docs/design/MARKDOWN_RETIREMENT_DESIGN.md Section 5).

A real app answers GET /document/export: the AsciiDoc body of an ingested document leaves Claire as a Markdown file with a YAML front matter header. The test also covers the one-time deletion of Markdown bodies at the first start (RD25).
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

from starlette.testclient import TestClient

from claire.config import Settings
from claire.store import db as dbm

ADOC_BODY = """== 개요

*핵심* 내용과 _강조_ 그리고 https://example.com/paper[논문 링크].

* 첫째 항목
** 하위 항목

[%header,cols="2*"]
|===
| 모델 | 처리량
| Alpha | 150
|===

[source,python]
----
print("*x*")
----
"""


def _make_app(tmp_path: Path):
    from claire.api.server import create_app

    db_file = tmp_path / "claire.db"
    conn = sqlite3.connect(str(db_file))
    conn.row_factory = sqlite3.Row
    dbm.init_db(conn)
    conn.execute(
        "INSERT INTO documents (id, title, url, raw_text, fetched_at, detail, detail_format) VALUES (?, ?, ?, ?, ?, ?, 'adoc')",
        ("doc-export", "내보내기 문서", "https://example.com/source", "원문", 1800000000.0, ADOC_BODY),
    )
    dbm.log_extraction(conn, document_id="doc-export", provider="mock", model="m", prompt_version="v", raw_response='{"summary":"문서 요약"}')
    conn.commit()
    conn.close()
    token = "owner-" + ("e" * 32)
    settings = Settings(db_path=str(db_file), data_dir=tmp_path, inject_token=token, environment="development", public_url="http://127.0.0.1:8765")
    return create_app(settings), {"Authorization": f"Bearer {token}"}, settings


def test_e2e_document_export_gives_a_markdown_file(tmp_path: Path):
    app, owner, settings = _make_app(tmp_path)
    with TestClient(app, base_url=settings.public_url) as client:
        res = client.get("/document/export?id=doc-export&format=markdown", headers=owner)
        assert res.status_code == 200, res.text
        assert res.headers["content-type"].startswith("text/markdown")
        disposition = res.headers["content-disposition"]
        assert disposition.startswith("attachment;") and 'filename="claire-doc-export.md"' in disposition and "filename*=UTF-8''" in disposition
        text = res.text
        # Front matter (MX3), then the title, then the body in Markdown.
        assert text.startswith('---\ntitle: "내보내기 문서"\nsource: "https://example.com/source"\ndate: 2027-01-15\nsummary: "문서 요약"\nclaire_document_id: "doc-export"\n---\n\n# 내보내기 문서\n\n## 개요\n')
        assert "**핵심** 내용과 *강조* 그리고 [논문 링크](https://example.com/paper)." in text
        assert "- 첫째 항목\n  - 하위 항목" in text
        assert "| 모델 | 처리량 |\n|---|---|\n| Alpha | 150 |" in text
        assert '```python\nprint("*x*")\n```' in text
        assert "|===" not in text and "----" not in text

        # Only the Markdown format exists, and an unknown document is 404.
        assert client.get("/document/export?id=doc-export&format=pdf", headers=owner).status_code == 400
        assert client.get("/document/export?format=markdown", headers=owner).status_code == 400
        assert client.get("/document/export?id=nope&format=markdown", headers=owner).status_code == 404
        # The access rule is the rule of GET /document: an anonymous client gets the same answer from both.
        assert client.get("/document/export?id=doc-export&format=markdown").status_code == client.get("/document?id=doc-export").status_code


def test_first_start_deletes_markdown_bodies_once(tmp_path: Path):
    db_file = tmp_path / "claire.db"
    conn = sqlite3.connect(str(db_file))
    conn.row_factory = sqlite3.Row
    dbm.init_db(conn)
    conn.execute("DELETE FROM meta WHERE key=?", (dbm.MARKDOWN_BODIES_PURGED_KEY,))
    conn.execute("INSERT INTO documents (id, title, raw_text, fetched_at, detail, detail_html, detail_format) VALUES ('md-doc', 'M', 'raw', 1, '## old', '<h2>old</h2>', 'md')")
    conn.execute("INSERT INTO documents (id, title, raw_text, fetched_at, detail, detail_format) VALUES ('adoc-doc', 'A', 'raw', 1, '== keep', 'adoc')")
    conn.commit()
    dbm.init_db(conn)
    md = conn.execute("SELECT detail, detail_html, detail_format, raw_text FROM documents WHERE id='md-doc'").fetchone()
    assert (md["detail"], md["detail_html"], md["detail_format"], md["raw_text"]) == (None, None, "adoc", "raw")
    assert conn.execute("SELECT detail FROM documents WHERE id='adoc-doc'").fetchone()[0] == "== keep"
    # The step runs one time only: a Markdown row that a later version writes by mistake is not touched again.
    conn.execute("UPDATE documents SET detail='## again', detail_format='md' WHERE id='md-doc'")
    conn.commit()
    dbm.init_db(conn)
    assert conn.execute("SELECT detail FROM documents WHERE id='md-doc'").fetchone()[0] == "## again"
    conn.close()
