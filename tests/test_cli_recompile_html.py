"""Tests for recompile-html CLI command and multi-theme support."""

from __future__ import annotations

import json
from pathlib import Path
import pytest

from claire import cli
from claire.config import Settings
from claire.store import db as dbm
from claire.store.theme import ThemeManager


def test_db_recompile_all_detail_html_with_doc_id(tmp_path):
    """Test that recompile_all_detail_html supports filtering by doc_id."""
    db_file = tmp_path / "claire.db"
    conn = dbm.connect(db_file)
    dbm.init_db(conn)

    adoc_content_1 = """[quote, Author, Source]
Line 1 +
... +
Line 2"""

    adoc_content_2 = "= Title\n\nParagraph text."

    conn.execute(
        """
        INSERT INTO documents (id, url, canonical_url, title, raw_text, detail, detail_format, detail_html, fetched_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        ("doc_1", "https://example.com/1", "https://example.com/1", "Doc 1", "raw 1", adoc_content_1, "adoc", "<ol><li>+</li></ol>", 1000.0),
    )
    conn.execute(
        """
        INSERT INTO documents (id, url, canonical_url, title, raw_text, detail, detail_format, detail_html, fetched_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        ("doc_2", "https://example.com/2", "https://example.com/2", "Doc 2", "raw 2", adoc_content_2, "adoc", "<p>Old 2</p>", 2000.0),
    )
    conn.commit()

    # Recompile only doc_1
    updated = dbm.recompile_all_detail_html(conn, doc_id="doc_1")
    assert updated == 1

    row1 = conn.execute("SELECT detail_html FROM documents WHERE id='doc_1'").fetchone()
    assert "<li>+" not in row1["detail_html"]
    assert "<ol>" not in row1["detail_html"]

    row2 = conn.execute("SELECT detail_html FROM documents WHERE id='doc_2'").fetchone()
    assert row2["detail_html"] == "<p>Old 2</p>"

    # Recompile all
    updated_all = dbm.recompile_all_detail_html(conn)
    assert updated_all == 2
    row2_new = conn.execute("SELECT detail_html FROM documents WHERE id='doc_2'").fetchone()
    assert "Paragraph text." in row2_new["detail_html"]
    conn.close()


def test_e2e_recompile_html_multi_theme_and_target_resolution(tmp_path, monkeypatch, capsys):
    """E2E test verifying recompile-html across multi-theme databases and with target resolution."""
    themes_dir = tmp_path / "themes"
    themes_dir.mkdir(parents=True, exist_ok=True)
    theme2_dir = themes_dir / "2"
    theme2_dir.mkdir(parents=True, exist_ok=True)

    db_theme0 = tmp_path / "claire.db"
    db_theme2 = theme2_dir / "claire.db"

    # Set up theme 0 DB
    conn0 = dbm.connect(db_theme0)
    dbm.init_db(conn0)
    conn0.execute(
        """
        INSERT INTO documents (id, url, canonical_url, title, raw_text, detail, detail_format, detail_html, fetched_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        ("doc_t0", "https://example.com/t0", "https://example.com/t0", "Doc T0", "raw", "= T0\n\nBody", "adoc", "stale_t0", 1000.0),
    )
    conn0.commit()
    conn0.close()

    # Set up theme 2 DB with the problematic ellipsis hardbreak pattern
    conn2 = dbm.connect(db_theme2)
    dbm.init_db(conn2)
    stale_voyager_html = """<div class="quoteblock"><blockquote><p>"폭발해서 멈췄어!"<br></p></blockquote></div>
<ol>
<li>+
</li>
</ol>
<p>소녀가 호기심 넘치는 표정으로 살폈다.</p>"""
    voyager_adoc = """[quote, Author, Source]
"폭발해서 멈췄어!" +
... +
소녀가 호기심 넘치는 표정으로 살폈다."""

    conn2.execute(
        """
        INSERT INTO documents (id, url, canonical_url, title, raw_text, detail, detail_format, detail_html, fetched_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        ("doc_1e8c5a3772fb", "https://uttu.merui.net/profiles/voyager/", "https://uttu.merui.net/profiles/voyager", "Voyager", "raw", voyager_adoc, "adoc", stale_voyager_html, 1000.0),
    )
    # Also add a share token in theme 2
    conn2.execute(
        """
        INSERT INTO doc_shares (token, document_id, created_at)
        VALUES (?, ?, ?)
        """,
        ("e3xqe5h97smp66qb", "doc_1e8c5a3772fb", 1000.0),
    )
    conn2.commit()
    conn2.close()

    # Setup themes.json
    themes_json_path = tmp_path / "themes.json"
    themes_data = {
        "active_theme_id": 0,
        "themes": [
            {
                "id": 0,
                "label": "General",
                "icon": "🌐",
                "db_path": str(db_theme0),
                "vault_path": str(tmp_path / "vault"),
                "is_default": True,
            },
            {
                "id": 2,
                "label": "R1999",
                "icon": "⏳",
                "db_path": str(db_theme2),
                "vault_path": str(theme2_dir / "vault"),
                "is_default": False,
            },
        ],
    }
    themes_json_path.write_text(json.dumps(themes_data, ensure_ascii=False), encoding="utf-8")

    test_settings = Settings(
        db_path=str(db_theme0),
        multi_theme=True,
    )
    monkeypatch.setattr(cli, "get_settings", lambda: test_settings)

    # 1. Test targeted recompile via share URL
    parser = cli.build_parser()
    args_target = parser.parse_args(["recompile-html", "https://cb.netspheres.org/p?s=e3xqe5h97smp66qb"])
    rc = cli.cmd_recompile_html(args_target)
    assert rc == 0
    captured = capsys.readouterr()
    assert "doc_1e8c5a3772fb" in captured.out
    assert "R1999" in captured.out

    # Verify theme 2 DB is updated and <li>+ is gone
    conn2 = dbm.connect(db_theme2)
    row = conn2.execute("SELECT detail_html FROM documents WHERE id='doc_1e8c5a3772fb'").fetchone()
    assert "<li>+" not in row["detail_html"]
    assert "<ol>" not in row["detail_html"]
    conn2.close()

    # 2. Test recompile all themes without target
    args_all = parser.parse_args(["recompile-html"])
    rc_all = cli.cmd_recompile_html(args_all)
    assert rc_all == 0
    captured_all = capsys.readouterr()
    assert "General" in captured_all.out
    assert "R1999" in captured_all.out
    assert "전체 테마 AOT HTML 재컴파일 완료" in captured_all.out
