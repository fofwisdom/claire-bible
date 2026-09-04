"""Seed test database for Playwright E2E tests."""

import sqlite3
from pathlib import Path

from claire.ontology.base import Document, Entity, Relation
from claire.store import db as dbm


def seed(db_path: Path) -> None:
    db_path.parent.mkdir(parents=True, exist_ok=True)
    if db_path.exists():
        db_path.unlink()

    conn = dbm.connect(db_path)
    try:
        dbm.init_db(conn)

        doc1 = Document(
            id="doc-1",
            url="https://example.com/doc1",
            title="테스트 문서 1 (핵심 개념)",
            fetched_at=1700000000,
        )
        doc2 = Document(
            id="doc-2",
            url="https://example.com/doc2",
            title="테스트 문서 2 (응용 도구)",
            fetched_at=1700001000,
        )

        dbm.insert_document(conn, doc1)
        dbm.insert_document(conn, doc2)

        long_detail = "\n\n".join([
            f"## 섹션 {i}\n" + "첫 번째 테스트 문서의 스크롤 및 레일 내비게이션 검증을 위한 상세 본문 내용입니다. " * 15
            for i in range(1, 10)
        ])
        dbm.set_document_detail(
            conn,
            "doc-1",
            detail=long_detail,
            format="md",
        )
        dbm.set_document_detail(
            conn,
            "doc-2",
            detail="두 번째 테스트 문서의 자세한 본문 내용입니다.",
            format="md",
        )
        conn.execute(
            "INSERT OR REPLACE INTO doc_shares(token, document_id, created_at, expires_at) VALUES (?,?,?,?)",
            ("23456789abcdefgh", "doc-1", 1700000000, None),
        )

        e1 = Entity(
            id="ent-1",
            name="엔티티 A",
            type="Concept",
            degree=2,
            sources=["doc-1"],
            provisional=False,
        )
        e2 = Entity(
            id="ent-2",
            name="엔티티 B",
            type="Tool",
            degree=2,
            sources=["doc-1"],
            provisional=False,
        )
        e3 = Entity(
            id="ent-3",
            name="엔티티 C",
            type="Model",
            degree=1,
            sources=["doc-2"],
            provisional=False,
        )

        for ent in (e1, e2, e3):
            dbm.upsert_entity(conn, ent)

        rel1 = Relation(
            id="rel-1",
            type="relates_to",
            source_id="ent-1",
            target_id="ent-2",
            sources=["doc-1"],
        )
        dbm.upsert_relation(conn, rel1)

        conn.commit()
    finally:
        conn.close()


if __name__ == "__main__":
    target = Path("data/e2e.db")
    seed(target)
    print(f"E2E test database seeded at {target}")
