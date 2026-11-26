"""Source identifier and source key of a document (DOCUMENT_RENDITIONS_AND_PUBLICATION_DESIGN.md Section 5.3 rules 4 to 6).

The key is a part of the wire contract between Claire servers (``claire:sourceKey``): two servers must give the same key for the same source.
"""

from __future__ import annotations

import hashlib
from pathlib import Path

import pytest

from claire.ingest.source_key import SOURCE_ID_VERSION, source_identifier, source_key, source_key_of
from claire.ontology.base import Document
from claire.store import db as dbm


@pytest.mark.parametrize(("url", "identifier"), [
    ("https://doi.org/10.1145/3290605.3300233", "https://doi.org/10.1145/3290605.3300233"),
    ("https://dx.doi.org/10.1038/S41586-020-2649-2", "https://doi.org/10.1038/s41586-020-2649-2"),
    ("https://dl.acm.org/doi/pdf/10.1145/3290605.3300233", "https://doi.org/10.1145/3290605.3300233"),
    ("https://link.springer.com/article/10.1007/s00453-020-00731-5", "https://doi.org/10.1007/s00453-020-00731-5"),
    ("https://doi.org/10.48550/arXiv.2101.00001", "https://arxiv.org/abs/2101.00001"),
    ("https://arxiv.org/pdf/2101.00001v3.pdf", "https://arxiv.org/abs/2101.00001"),
    ("http://arxiv.org/abs/2101.00001v2", "https://arxiv.org/abs/2101.00001"),
    ("https://www.example.com/post/?utm_source=x#part", "https://example.com/post"),
    ("file:///tmp/a.pdf", None),
    ("", None),
    (None, None),
])
def test_source_identifier_of_version_1(url: str | None, identifier: str | None) -> None:
    assert source_identifier(url) == identifier


def test_the_key_is_the_sha256_of_the_identifier_and_the_same_source_has_one_key() -> None:
    assert SOURCE_ID_VERSION == 1
    assert source_key("https://arxiv.org/abs/2101.00001") == hashlib.sha256(b"https://arxiv.org/abs/2101.00001").hexdigest()
    assert source_key_of("https://arxiv.org/pdf/2101.00001v1") == source_key_of("https://doi.org/10.48550/arXiv.2101.00001")
    # The original address first; the canonical address when the original one has no identifier.
    assert source_key_of(None, "https://example.com/post") == source_key("https://example.com/post")
    assert source_key_of("file:///tmp/a.pdf", None) is None


def test_documents_get_their_key_at_insert_and_version_51_computes_the_keys_that_exist(tmp_path: Path) -> None:
    path = tmp_path / "claire.db"
    conn = dbm.connect(path)
    dbm.init_db(conn)
    dbm.insert_document(conn, Document(id="web", url="https://arxiv.org/abs/2101.00001v4", title="w", raw_text="x", source_type="web", content_hash="h1"))
    dbm.insert_document(conn, Document(id="note", url=None, title="n", raw_text="x", source_type="text", content_hash="h2"))
    rows = {r[0]: (r[1], r[2]) for r in conn.execute("SELECT id, source_key, source_id_version FROM documents")}
    assert rows == {"web": (source_key("https://arxiv.org/abs/2101.00001"), 1), "note": (None, None)}
    # A file of version 50: the step adds the columns and computes the key of each document; a copy of a remote document has none.
    conn.execute("DROP INDEX idx_documents_source_key")
    conn.execute("ALTER TABLE documents DROP COLUMN source_id_version")
    conn.execute("ALTER TABLE documents DROP COLUMN source_key")
    conn.execute("INSERT INTO documents (id, kb_id, url, canonical_url, title, raw_text, source_type, content_hash) VALUES ('remote', 0, 'https://b.example/ap/objects/9', 'https://b.example/ap/objects/9', 'r', 'x', 'remote', 'h3')")
    conn.commit()
    conn.close()
    conn = dbm.connect(path)
    try:
        dbm.init_db(conn)
        rows = {r[0]: r[1] for r in conn.execute("SELECT id, source_key FROM documents")}
        assert rows == {"web": source_key("https://arxiv.org/abs/2101.00001"), "note": None, "remote": None}
        assert dbm.stored_schema_version(conn) == dbm.SCHEMA_VERSION
    finally:
        conn.close()
