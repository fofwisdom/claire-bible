"""Orbrium 등 비인가 미디어 플랫폼 계정 서지 오염 문서 일괄 정화 스크립트."""

from __future__ import annotations

import argparse
import sqlite3
import sys
from pathlib import Path

# Auto-reexec with virtual environment python if running under system python
if sys.prefix == sys.base_prefix:
    root_dir = Path(__file__).resolve().parent.parent
    for candidate in (root_dir / ".venv" / "bin" / "python3", root_dir / ".venv" / "bin" / "python", Path("/app/.venv/bin/python3")):
        if candidate.exists() and candidate.resolve() != Path(sys.executable).resolve():
            import os
            os.execv(str(candidate), [str(candidate)] + sys.argv)

# Add project root to sys.path
root_dir = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(root_dir / "src"))

from claire.extract.prompts import sanitize_rendered_detail
from claire.render import render_to_html


def remediate_database(db_path: Path, *, target_doc_ids: list[str] | None = None, dry_run: bool = False) -> int:
    db_path = Path(db_path).expanduser().resolve()
    if not db_path.exists():
        print(f"[ERROR] Database file not found: {db_path}", file=sys.stderr)
        return 1

    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row

    cursor = conn.cursor()
    if target_doc_ids:
        placeholders = ",".join("?" for _ in target_doc_ids)
        query = f"SELECT id, title, author, source_type, detail, detail_format FROM documents WHERE id IN ({placeholders})"
        params = list(target_doc_ids)
    else:
        query = (
            "SELECT id, title, author, source_type, detail, detail_format FROM documents "
            "WHERE author LIKE '%Orbrium%' OR detail LIKE '%Orbrium%' OR source_type IN ('video', 'youtube')"
        )
        params = []

    rows = cursor.execute(query, params).fetchall()
    print(f"[*] Found {len(rows)} candidate document(s) in {db_path}")

    remediated_count = 0
    for r in rows:
        doc_id = r["id"]
        title = r["title"]
        author = r["author"]
        detail = r["detail"] or ""
        fmt = (r["detail_format"] or "adoc").strip().lower()

        new_author = author
        if author and (author.lower() in ("orbrium", "youtube", "채널") or r["source_type"] in ("video", "youtube")):
            new_author = None

        new_detail = sanitize_rendered_detail(detail, format=fmt)

        author_changed = author != new_author
        detail_changed = detail != new_detail

        if author_changed or detail_changed:
            remediated_count += 1
            print(f"\n[REMEDIATE] Doc {doc_id} ('{title}')")
            if author_changed:
                print(f"  - Author: {author!r} -> {new_author!r}")
            if detail_changed:
                print("  - Detail sanitized (leading author/subtitle or platform quotes stripped)")

            if not dry_run:
                new_html = render_to_html(new_detail, format=fmt)
                conn.execute(
                    "UPDATE documents SET author=?, detail=?, detail_html=? WHERE id=?",
                    (new_author, new_detail, new_html, doc_id),
                )

    if not dry_run:
        conn.commit()
        print(f"\n[SUCCESS] Remediated {remediated_count} document(s) in {db_path}")
    else:
        print(f"\n[DRY RUN] Would remediate {remediated_count} document(s)")

    conn.close()
    return 0


def main() -> None:
    parser = argparse.ArgumentParser(description="Remediate Orbrium and media platform bibliographic pollution.")
    parser.add_argument("--db", type=Path, default=Path("data/claire.db"), help="Path to SQLite database")
    parser.add_argument("--doc", nargs="*", help="Specific document ID(s) to remediate")
    parser.add_argument("--dry-run", action="store_true", help="Simulate changes without modifying the database")
    args = parser.parse_args()

    sys.exit(remediate_database(args.db, target_doc_ids=args.doc, dry_run=args.dry_run))


if __name__ == "__main__":
    main()
