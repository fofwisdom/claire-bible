"""Seed test database for Playwright E2E tests."""

import json
import os
import sqlite3
from pathlib import Path

import claire_bible.bootstrap
from claire.ontology.base import Document, Entity, Relation
from claire.store import accounts as accounts_store
from claire.store import db as dbm

claire_bible.bootstrap.configure()


def _insert_entity_row(conn, ent: Entity) -> None:
    """Insert one entity row and its FTS row. dbm.upsert_entity() also adds the name to the tokenizer dictionary, which takes about one second for each entity."""
    conn.execute("INSERT OR REPLACE INTO entities(id,kb_id,type,name,norm_name,aliases,props,observations,sources,provisional,created_at,updated_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)", (ent.id, 0, ent.type, ent.name, ent.norm_name, json.dumps(ent.aliases, ensure_ascii=False), "{}", json.dumps(ent.observations, ensure_ascii=False), json.dumps(ent.sources, ensure_ascii=False), int(ent.provisional), ent.created_at, ent.updated_at))
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

        # AsciiDoc is the only body format (RD25): the section titles are "==" lines.
        long_detail = "\n\n".join([
            f"== 섹션 {i}\n\n" + "첫 번째 테스트 문서의 스크롤 및 레일 내비게이션 검증을 위한 상세 본문 내용입니다. " * 15
            for i in range(1, 10)
        ])
        dbm.set_document_detail(
            conn,
            "doc-1",
            detail=long_detail,
            format="adoc",
        )
        dbm.set_document_detail(
            conn,
            "doc-2",
            detail="두 번째 테스트 문서의 자세한 본문 내용입니다.",
            format="adoc",
        )
        dbm.set_document_detail(
            conn,
            "doc-3",
            detail="이 문서는 PDF 인코딩 결함 및 스캔본 감지 태그 테스트를 위한 문서입니다.",
            format="adoc",
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
        dbm.set_document_detail(conn, "doc-page-1", detail="페이징 자료 01의 본문입니다.", format="adoc")
        for i in range(1, 16):
            hid_id = f"doc-hidden-{i}"
            dbm.insert_document(conn, Document(id=hid_id, url=f"https://example.com/hidden/{i}", title=f"숨긴 문서 {i:02d} (스크롤 테스트)", fetched_at=1699990000 - i * 10))
            dbm.set_document_hidden(conn, hid_id, True)
        # The regenerate test starts a real regeneration, which removes the graph components of its target document. This hidden document has no entity and no relation, thus the other tests keep their data.
        dbm.insert_document(conn, Document(id="doc-regen-1", url="https://example.com/regen/1", title="재생성 대상 문서", fetched_at=1699980000))
        dbm.set_document_hidden(conn, "doc-regen-1", True)
        # Dedicated hidden document with no shared entities for the WebUI purge E2E test.
        dbm.insert_document(conn, Document(id="doc-purge-1", url="https://example.com/purge/1", title="소각 대상 테스트 문서", fetched_at=1699970000))
        dbm.set_document_hidden(conn, "doc-purge-1", True)
        # The address token of the entity "ent-hub" (the address /entity/{token}). The entity row comes later in this seed.
        conn.execute(
            "INSERT OR REPLACE INTO permalinks(token, kind, kb_id, target, link_access, created_at) VALUES (?, 'entity', 0, ?, 0, ?)",
            ("45678923abcdefgh", "ent-hub", 1700000000),
        )
        # The address tokens of two documents, with link access (the permalink registry).
        conn.execute(
            "INSERT OR REPLACE INTO permalinks(token, kind, kb_id, target, link_access, created_at) VALUES (?, 'document', 0, ?, 1, ?)",
            ("23456789abcdefgh", "doc-1", 1700000000),
        )
        conn.execute(
            "INSERT OR REPLACE INTO permalinks(token, kind, kb_id, target, link_access, created_at) VALUES (?, 'document', 0, ?, 1, ?)",
            ("34567892abcdefgh", "doc-3", 1699999000),
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
        # A login token is for one login: each test that needs an owner session has its own token.
        conn.execute("INSERT OR REPLACE INTO auth_sessions(nonce, session_token, approved, created_at, expires_at, scope) VALUES (?,?,1,?,?,?)", ("e2e-deeplink-nonce", "e2e-deeplink-token-0123456789abcdef", 1700000000, 2500000000.0, "owner"))
        conn.execute("INSERT OR REPLACE INTO auth_sessions(nonce, session_token, approved, created_at, expires_at, scope) VALUES (?,?,1,?,?,?)", ("e2e-regenfail-nonce", "e2e-regenfail-token-0123456789abcd", 1700000000, 2500000000.0, "owner"))
        conn.execute("INSERT OR REPLACE INTO auth_sessions(nonce, session_token, approved, created_at, expires_at, scope) VALUES (?,?,1,?,?,?)", ("e2e-aliases-nonce", "e2e-aliases-token-0123456789abcdef0", 1700000000, 2500000000.0, "owner"))
        conn.execute("INSERT OR REPLACE INTO auth_sessions(nonce, session_token, approved, created_at, expires_at, scope) VALUES (?,?,1,?,?,?)", ("e2e-providers-nonce", "e2e-providers-token-0123456789abcd", 1700000000, 2500000000.0, "owner"))
        conn.execute("INSERT OR REPLACE INTO auth_sessions(nonce, session_token, approved, created_at, expires_at, scope) VALUES (?,?,1,?,?,?)", ("e2e-home-nonce", "e2e-home-token-0123456789abcdef012", 1700000000, 2500000000.0, "owner"))
        conn.execute("INSERT OR REPLACE INTO auth_sessions(nonce, session_token, approved, created_at, expires_at, scope) VALUES (?,?,1,?,?,?)", ("e2e-flow-nonce", "e2e-flow-token-0123456789abcdef012", 1700000000, 2500000000.0, "owner"))
        conn.execute("INSERT OR REPLACE INTO auth_sessions(nonce, session_token, approved, created_at, expires_at, scope) VALUES (?,?,1,?,?,?)", ("e2e-panel-nonce", "e2e-panel-token-0123456789abcdef01", 1700000000, 2500000000.0, "owner"))
        conn.execute("INSERT OR REPLACE INTO auth_sessions(nonce, session_token, approved, created_at, expires_at, scope) VALUES (?,?,1,?,?,?)", ("e2e-fedclient-nonce", "e2e-fedclient-token-0123456789abcd", 1700000000, 2500000000.0, "owner"))
        conn.execute("INSERT OR REPLACE INTO auth_sessions(nonce, session_token, approved, created_at, expires_at, scope) VALUES (?,?,1,?,?,?)", ("e2e-console-layout-nonce", "e2e-console-layout-token-0123456789ab", 1700000000, 2500000000.0, "owner"))
        conn.execute("INSERT OR REPLACE INTO auth_sessions(nonce, session_token, approved, created_at, expires_at, scope) VALUES (?,?,1,?,?,?)", ("e2e-fedsingle-nonce", "e2e-fedsingle-token-0123456789abcd", 1700000000, 2500000000.0, "owner"))
        conn.execute("INSERT OR REPLACE INTO auth_sessions(nonce, session_token, approved, created_at, expires_at, scope) VALUES (?,?,1,?,?,?)", ("e2e-fedcompose-nonce", "e2e-fedcompose-token-0123456789abc", 1700000000, 2500000000.0, "owner"))
        conn.execute("INSERT OR REPLACE INTO auth_sessions(nonce, session_token, approved, created_at, expires_at, scope) VALUES (?,?,1,?,?,?)", ("e2e-fedquote-nonce", "e2e-fedquote-token-0123456789abcde", 1700000000, 2500000000.0, "owner"))
        conn.execute("INSERT OR REPLACE INTO auth_sessions(nonce, session_token, approved, created_at, expires_at, scope) VALUES (?,?,1,?,?,?)", ("e2e-feedview-nonce", "e2e-feedview-token-0123456789abcde", 1700000000, 2500000000.0, "owner"))
        # Application bar specs (e2e/app_bar.spec.js): one token for each test.
        conn.execute("INSERT OR REPLACE INTO auth_sessions(nonce, session_token, approved, created_at, expires_at, scope) VALUES (?,?,1,?,?,?)", ("e2e-appbar1-nonce", "e2e-appbar1-token-0123456789abcde", 1700000000, 2500000000.0, "owner"))
        conn.execute("INSERT OR REPLACE INTO auth_sessions(nonce, session_token, approved, created_at, expires_at, scope) VALUES (?,?,1,?,?,?)", ("e2e-appbar2-nonce", "e2e-appbar2-token-0123456789abcde", 1700000000, 2500000000.0, "owner"))
        conn.execute("INSERT OR REPLACE INTO auth_sessions(nonce, session_token, approved, created_at, expires_at, scope) VALUES (?,?,1,?,?,?)", ("e2e-appbar3-nonce", "e2e-appbar3-token-0123456789abcde", 1700000000, 2500000000.0, "owner"))
        conn.execute("INSERT OR REPLACE INTO auth_sessions(nonce, session_token, approved, created_at, expires_at, scope) VALUES (?,?,1,?,?,?)", ("e2e-appbar4-nonce", "e2e-appbar4-token-0123456789abcde", 1700000000, 2500000000.0, "owner"))
        # Text of the knowledge base view of /syndication in English (e2e/syndication_i18n.spec.js).
        conn.execute("INSERT OR REPLACE INTO auth_sessions(nonce, session_token, approved, created_at, expires_at, scope) VALUES (?,?,1,?,?,?)", ("e2e-syndi18n-nonce", "e2e-syndi18n-token-0123456789abcd", 1700000000, 2500000000.0, "owner"))
        # Text of the console in English (e2e/console_i18n.spec.js).
        conn.execute("INSERT OR REPLACE INTO auth_sessions(nonce, session_token, approved, created_at, expires_at, scope) VALUES (?,?,1,?,?,?)", ("e2e-consolei18n-nonce", "e2e-consolei18n-token-0123456789a", 1700000000, 2500000000.0, "owner"))
        # Text of the workspace in English (e2e/workspace_i18n.spec.js).
        conn.execute("INSERT OR REPLACE INTO auth_sessions(nonce, session_token, approved, created_at, expires_at, scope) VALUES (?,?,1,?,?,?)", ("e2e-wsi18n-nonce", "e2e-wsi18n-token-0123456789abcdef", 1700000000, 2500000000.0, "owner"))
        # The provider settings spec (e2e/provider_settings.spec.js), its first test.
        conn.execute("INSERT OR REPLACE INTO auth_sessions(nonce, session_token, approved, created_at, expires_at, scope) VALUES (?,?,1,?,?,?)", ("e2e-routing-nonce", "e2e-routing-token-0123456789abcde", 1700000000, 2500000000.0, "owner"))
        # Shared KB UI specs (e2e/shared_kb_ui.spec.js): one token for each test.
        for n in (1, 2, 3):
            conn.execute("INSERT OR REPLACE INTO auth_sessions(nonce, session_token, approved, created_at, expires_at, scope) VALUES (?,?,1,?,?,?)", (f"e2e-sharedkb{n}-nonce", f"e2e-sharedkb{n}-token-0123456789abc", 1700000000, 2500000000.0, "owner"))
        # Specs of the federation admin and of the shared knowledge base pages (e2e/federation_admin.spec.js, e2e/kb_manage.spec.js, e2e/kb_contribute_dialogs.spec.js, e2e/admin_themes_kb.spec.js, e2e/kb_workspace.spec.js, e2e/kb_public_pages.spec.js, e2e/admin_limits.spec.js, e2e/support_bundles.spec.js, e2e/admin_plugins.spec.js, e2e/public_directory.spec.js, e2e/notifications.spec.js, e2e/ingest_extraction_mode.spec.js, e2e/provider_settings.spec.js, e2e/support_pipeline.spec.js, e2e/preferred_languages.spec.js, e2e/laboratory.spec.js): one token for each test.
        for name in ("fedadmin1", "fedadmin2", "fedadmin3", "fedadmin4", "fedadmin5", "fedadmin6", "fedadmin7", "fedadmin8", "kbmanage1", "kbmanage2", "kbmanage3", "kbmanage4", "kbmanage5", "kbdialog1", "kbdialog2", "admthemes1", "admthemes2", "kbwspace1", "kbwspace2", "kbwspace3", "kbwspace4", "kbpublic1", "kbpublic2", "admlimits1", "admlimits2", "bundlelinks", "adminplug1", "pubdirect1", "mcpgui001", "mcpgui002", "mcpclaude001", "mcpclaude002", "mcpchatgpt001", "mcpchatgpt002", "oauthwrite1", "notifybell1", "notifytab01", "notifypush1", "acctarea01", "acctarea02", "auditadm1", "purgedoc1", "ingestmode1", "ingestmode2", "renditions1", "provsettings1", "provsettings2", "pipeline01", "pipeline02", "languages01", "laboratory1", "laboratory2", "doclisthide1"):
            conn.execute("INSERT OR REPLACE INTO auth_sessions(nonce, session_token, approved, created_at, expires_at, scope) VALUES (?,?,1,?,?,?)", (f"e2e-{name}-nonce", f"e2e-{name}-token-0123456789abc", 1700000000, 2500000000.0, "owner"))

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

        # A shared knowledge base with the number 101 (shared knowledge base design Section 4). The page /moderation/kb/{number} needs a shared knowledge base in the database; e2e/shared_kb_ui.spec.js gives the JSON answers of its routes as fixed data with the same number. The admin is its moderator.
        now = 1700000000.0
        conn.execute(
            "INSERT INTO knowledge_bases (id, seq, label, description, is_personal, is_public, created_at, updated_at, account_id, number, created_by_account_id, join_policy, state, knowledge_type, seed_entities) "
            "VALUES (101, 101, 'Modern History', 'History of the 19th century', 0, 1, ?, ?, NULL, 101, 1, 'approval', 'active', 'real', '[\"Railway\"]')",
            (now, now),
        )
        conn.execute("INSERT INTO kb_memberships (kb_id, account_id, public_id, role, state, requested_at, decided_at) VALUES (101, 1, 101001, 'moderator', 'active', ?, ?)", (now, now))

        conn.commit()
    finally:
        conn.close()


def reset_plugin_folder() -> None:
    """The plugin folder of the E2E server starts empty (e2e/admin_plugins.spec.js puts its test plugins there)."""
    import shutil

    folder = os.environ.get("CLAIRE_PLUGINS_DIR")
    if not folder or Path(folder).name != "e2e-plugins":
        return
    shutil.rmtree(folder, ignore_errors=True)
    Path(folder).mkdir(parents=True, mode=0o755)


if __name__ == "__main__":
    target = Path(os.environ.get("CLAIRE_DB_PATH") or "data/e2e.db")
    seed(target)
    reset_plugin_folder()
    print(f"E2E test database seeded at {target}")
