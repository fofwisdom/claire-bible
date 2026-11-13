"""Seed test database for Playwright E2E tests."""

import json
import sqlite3
from pathlib import Path

from claire.ontology.base import Document, Entity, Relation
from claire.store import accounts as accounts_store
from claire.store import db as dbm


def _insert_entity_row(conn, ent: Entity) -> None:
    """Insert one entity row and its FTS row. dbm.upsert_entity() also adds the name to the tokenizer dictionary, which takes about one second for each entity."""
    conn.execute("INSERT OR REPLACE INTO entities(id,theme_id,type,name,norm_name,aliases,props,observations,sources,provisional,created_at,updated_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)", (ent.id, 0, ent.type, ent.name, ent.norm_name, json.dumps(ent.aliases, ensure_ascii=False), "{}", json.dumps(ent.observations, ensure_ascii=False), json.dumps(ent.sources, ensure_ascii=False), int(ent.provisional), ent.created_at, ent.updated_at))
    conn.execute("DELETE FROM entities_fts WHERE entity_id=?", (ent.id,))
    conn.execute("INSERT INTO entities_fts(entity_id,name,body) VALUES (?,?,?)", (ent.id, ent.name, " \n".join(ent.observations)))


def seed(db_path: Path) -> None:
    db_path.parent.mkdir(parents=True, exist_ok=True)
    if db_path.exists():
        db_path.unlink()
    active_state = db_path.parent / "active_ingest.json"
    if active_state.exists():
        active_state.unlink()
    active_jobs_dir = db_path.parent / "active_jobs"
    if active_jobs_dir.exists():
        for f in active_jobs_dir.glob("*.json"):
            try:
                f.unlink()
            except OSError:
                pass

    conn = dbm.connect(db_path)
    try:
        dbm.init_db(conn)
        # The administrator account of a new database has the status setup_required, and GET / sends an owner session of that account to /setup. The seed completes the setup, thus an owner token login opens the home page.
        accounts_store.AccountStore(conn).complete_setup(accounts_store.ADMIN_ACCOUNT_ID, username="e2eadmin", display_name="E2E Admin")

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
            meta={
                "has_decision_stream": True,
                "resolution_log": [
                    {
                        "entity": "엔티티 B",
                        "stage": "exact_match",
                        "decision": "MERGE",
                        "candidate": "엔티티 B",
                        "score": 1.0,
                        "reason": "정확한 명칭 일치로 기존 엔티티와 병합되었습니다.",
                    },
                    {
                        "entity": "엔티티 C",
                        "stage": "semantic_high",
                        "decision": "CREATE_NEW",
                        "candidate": "엔티티 A",
                        "score": 0.42,
                        "reason": "유사도 기준치 미달로 신규 생성되었습니다.",
                    },
                ],
            },
        )
        doc3 = Document(
            id="doc-3",
            url="https://example.com/test-paper.pdf",
            title="테스트 문서 3 (PDF 결함/폴백 테스트)",
            fetched_at=1699999000,
            source_type="file",
            meta={
                "pdf_parser_requested": "pypdfium2",
                "pdf_parser_used": "pypdf",
                "pdf_parser_fallback": True,
                "pdf_parser_fallback_reason": "Encoding flaws detected: unmapped_cid_fonts",
                "pdf_encoding_flaw_detected": True,
                "pdf_encoding_flaws": ["unmapped_cid_fonts", "pua_characters"],
                "pdf_is_scanned": True,
            },
        )

        dbm.insert_document(conn, doc1)
        dbm.insert_document(conn, doc2)
        dbm.insert_document(conn, doc3)

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
        dbm.set_document_detail(
            conn,
            "doc-3",
            detail="이 문서는 PDF 인코딩 결함 및 스캔본 감지 태그 테스트를 위한 문서입니다.",
            format="md",
        )
        # More than one page (31) of documents for the list paging test. They are older than doc-1..3, thus the first rows do not change.
        for i in range(1, 41):
            dbm.insert_document(conn, Document(id=f"doc-page-{i}", url=f"https://example.com/page/{i}", title=f"페이징 자료 {i:02d}", fetched_at=1699000000 - i * 60))
        # A third list page for the server paging test (unit LP-5): 25 documents that are older than the paging documents. The visible documents are 3 + 40 + 25 = 68, thus the pages have 31, 31, and 6 documents.
        for i in range(1, 26):
            dbm.insert_document(conn, Document(id=f"doc-old-{i:02d}", url=f"https://example.com/old/{i}", title=f"오래된 자료 {i:02d}", fetched_at=1698000000 - i * 60))
        # Source document rows of the node detail panel: doc-page-1 has a summary and a detail, doc-page-3 has a summary only, and the other documents have no summary.
        src_summary = "출처 문서 요약 문단입니다. " + "양쪽 정렬을 확인하는 긴 문장을 반복합니다. " * 6
        dbm.log_extraction(conn, document_id="doc-page-1", provider="mock", model="m", prompt_version="v", raw_response=json.dumps({"summary": src_summary}, ensure_ascii=False))
        dbm.log_extraction(conn, document_id="doc-page-3", provider="mock", model="m", prompt_version="v", raw_response=json.dumps({"summary": "세 번째 자료의 요약입니다."}, ensure_ascii=False))
        dbm.set_document_detail(conn, "doc-page-1", detail="페이징 자료 01의 본문입니다.", format="md")
        for i in range(1, 16):
            hid_id = f"doc-hidden-{i}"
            dbm.insert_document(conn, Document(id=hid_id, url=f"https://example.com/hidden/{i}", title=f"숨긴 문서 {i:02d} (스크롤 테스트)", fetched_at=1699990000 - i * 10))
            dbm.set_document_hidden(conn, hid_id, True)
        # The regenerate test starts a real regeneration, which removes the graph components of its target document. This hidden document has no entity and no relation, thus the other tests keep their data.
        dbm.insert_document(conn, Document(id="doc-regen-1", url="https://example.com/regen/1", title="재생성 대상 문서", fetched_at=1699980000))
        dbm.set_document_hidden(conn, "doc-regen-1", True)
        conn.execute(
            "INSERT OR REPLACE INTO doc_shares(token, document_id, created_at, expires_at) VALUES (?,?,?,?)",
            ("23456789abcdefgh", "doc-1", 1700000000, None),
        )
        conn.execute(
            "INSERT OR REPLACE INTO doc_shares(token, document_id, created_at, expires_at) VALUES (?,?,?,?)",
            ("34567892abcdefgh", "doc-3", 1699999000, None),
        )
        conn.execute(
            "INSERT OR REPLACE INTO auth_sessions(nonce, session_token, approved, created_at, expires_at, scope) VALUES (?,?,1,?,?,?)",
            ("e2e-nonce", "e2e-owner-token-0123456789abcdef", 1700000000, 2500000000.0, "owner"),
        )
        conn.execute(
            "INSERT OR REPLACE INTO auth_sessions(nonce, session_token, approved, created_at, expires_at, scope) VALUES (?,?,1,?,?,?)",
            ("e2e-provider-nonce", "e2e-provider-token-0123456789abcdef", 1700000000, 2500000000.0, "owner"),
        )
        conn.execute(
            "INSERT OR REPLACE INTO auth_sessions(nonce, session_token, approved, created_at, expires_at, scope) VALUES (?,?,1,?,?,?)",
            ("e2e-pres-nonce", "e2e-pres-token-0123456789abcdef01", 1700000000, 2500000000.0, "owner"),
        )
        conn.execute(
            "INSERT OR REPLACE INTO auth_sessions(nonce, session_token, approved, created_at, expires_at, scope) VALUES (?,?,1,?,?,?)",
            ("e2e-hidden-nonce", "e2e-hidden-token-0123456789abcdef01", 1700000000, 2500000000.0, "owner"),
        )
        conn.execute(
            "INSERT OR REPLACE INTO auth_sessions(nonce, session_token, approved, created_at, expires_at, scope) VALUES (?,?,1,?,?,?)",
            ("e2e-regen-nonce", "e2e-regen-token-0123456789abcdef01", 1700000000, 2500000000.0, "owner"),
        )
        conn.execute("INSERT OR REPLACE INTO auth_sessions(nonce, session_token, approved, created_at, expires_at, scope) VALUES (?,?,1,?,?,?)", ("e2e-panel-nonce", "e2e-panel-token-0123456789abcdef01", 1700000000, 2500000000.0, "owner"))

        e1 = Entity(
            id="ent-1",
            name="엔티티 A",
            type="Concept",
            aliases=["별칭 A1", "별칭 A2"],
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

        # More than one page (31) of entities for the entity list test. They are a separate component, thus ent-1..3 keep their degrees. Only ent-list-01 has source documents (the 40 paging documents, for the source document rows of the detail panel).
        # Degrees: the hub has 40 (the 40 leaves), ent-list-01 has 8 (the hub and 7 leaves), ent-list-02..08 have 2, and ent-list-09..40 have 1.
        list_types = ["Concept", "Tool", "Model", "Paper", "Org"]
        _insert_entity_row(conn, Entity(id="ent-hub", name="허브 엔티티", type="Framework", observations=[f"허브 관찰 {i}" for i in range(1, 8)], sources=[], provisional=False))
        for i in range(1, 41):
            _insert_entity_row(conn, Entity(id=f"ent-list-{i:02d}", name=f"목록 엔티티 {i:02d}", type=list_types[i % len(list_types)], sources=[f"doc-page-{k}" for k in range(1, 41)] if i == 1 else [], provisional=False))
            dbm.upsert_relation(conn, Relation(id=f"rel-hub-{i:02d}", type="uses", source_id="ent-hub", target_id=f"ent-list-{i:02d}", sources=["doc-page-40"]))
        for i in range(2, 9):
            dbm.upsert_relation(conn, Relation(id=f"rel-list-{i:02d}", type="relates_to", source_id=f"ent-list-{i:02d}", target_id="ent-list-01", sources=["doc-page-40"]))

        conn.commit()
    finally:
        conn.close()


if __name__ == "__main__":
    target = Path("data/e2e.db")
    seed(target)
    print(f"E2E test database seeded at {target}")
