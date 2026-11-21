"""Effects of shared knowledge bases on other subsystems (SHARED_KNOWLEDGE_BASES_AND_THEMES_DESIGN.md Section 13; unit CT-8).

Completion criteria: a pinned ingest to a shared knowledge base gets 403; workers take no shared row; a purge keeps contributions with a NULL credit; the option deletes submissions and reason texts and no content.
"""

from __future__ import annotations

import time

import pytest

from tests.test_contributions import EXTRACTION, _contribute, _fresh_token_stores, _graph, _personal_document, world  # noqa: F401 (fixtures)

from claire import cli
from claire.ops import account_backup
from claire.core import ids
from claire.store import accounts as accounts_store
from claire.store import db as dbm
from claire.store import kb_collab
from claire.store import themes as themes_store
from claire.telegram.bot import AccountThemeScope, resolve_selected_theme

ANN, BEN, CHO = 2, 3, 4


def test_pinned_ingest_to_a_shared_knowledge_base_gets_403(world) -> None:  # noqa: F811
    refused = world.client.post(f"{world.base}/ingest", json={"payload": "A text for a shared knowledge base."}, headers=world.ann)
    assert (refused.status_code, refused.json()["detail"] if "detail" in refused.json() else refused.json().get("error")) == (403, "kb_ingest_by_contribution")


def test_workers_take_no_row_of_a_shared_knowledge_base(world) -> None:  # noqa: F811
    personal = world.manager.personal_kb_for(BEN).id
    shared = world.kb["id"]
    conn = dbm.connect(world.settings.db_file)
    try:
        for kb_id, name in ((personal, "p"), (shared, "s")):
            conn.execute("INSERT INTO documents (id, kb_id, title, raw_text, url, watch_enabled, hidden) VALUES (?, ?, ?, 'text', ?, 1, 0)", (f"doc_{name}", kb_id, name, f"https://example.org/{name}"))
            assert dbm.enqueue_refresh(conn, document_id=f"doc_{name}", payload=f"https://example.org/{name}", reason="test", kb_id=kb_id)
            assert dbm.enqueue_expand(conn, f"doc_{name}", kb_id=kb_id)
        conn.commit()
        assert [r["id"] for r in dbm.watch_due_documents(conn, time.time(), default_interval=1.0)] == ["doc_p"]
        assert [r["document_id"] for r in dbm.pending_refresh(conn)] == ["doc_p"]
        assert [r["document_id"] for r in dbm.pending_expand(conn)] == ["doc_p"]
        # Also when the worker asks for the shared knowledge base by its ID.
        assert dbm.watch_due_documents(conn, time.time(), default_interval=1.0, kb_id=shared) == []
        assert dbm.pending_refresh(conn, kb_id=shared) == [] and dbm.pending_expand(conn, kb_id=shared) == []
    finally:
        conn.close()


def _purge_setup(world):  # noqa: F811
    """ben has: a visible contribution in Cities (approved by ann), a pending contribution to Towns through a submission, a membership, and a moderation log row with a reason in his own knowledge base Notes."""
    client = world.client
    towns = client.post("/kb", json={"label": "Towns", "knowledge_type": "real"}, headers=world.cho).json()["knowledge_base"]
    notes = client.post("/kb", json={"label": "Notes", "knowledge_type": "real"}, headers=world.ben).json()["knowledge_base"]
    conn = dbm.connect(world.settings.db_file)
    try:
        theme = themes_store.create_theme(conn, label="Places", knowledge_type="real")
        themes_store.add_theme_member(conn, theme_id=theme["id"], kb_id=towns["id"], source="admin", pinned=True)
        conn.commit()
    finally:
        conn.close()
    visible = _personal_document(world.settings, world.manager, BEN, "Seoul and Busan are cities of Korea. " * 3, extraction=EXTRACTION)
    pending = _personal_document(world.settings, world.manager, BEN, "Daegu and Incheon are cities of Korea. " * 3, extraction=EXTRACTION)
    made = _contribute(world, world.ben, visible).json()["contribution"]
    assert client.patch(f"{world.base}/contributions/{made['number']}", json={"action": "approve"}, headers=world.ann).status_code == 200
    submitted = client.post("/submissions", json={"documents": [pending], "theme": ids.format_public_id(theme["number"])}, headers=world.ben)
    assert submitted.status_code == 201 and submitted.json()["submission"]["items"][0]["targets"][0]["state"] == "pending"
    # ben rejects a contribution of cho in Notes with a reason text... the reason codes are fixed, so the log row gets its text here.
    conn = dbm.connect(world.settings.db_file)
    try:
        kb_collab.log_moderation(conn, kb_id=notes["id"], actor_account_id=BEN, action="reject_contribution", target="1", reason="a text that ben wrote")
        conn.commit()
    finally:
        conn.close()
    return towns, notes, made


def _purge(world, *, delete_activity_records: bool) -> None:  # noqa: F811
    conn = dbm.connect(world.settings.db_file)
    try:
        accounts_store.AccountStore(conn).purge(BEN, data_dir=world.settings.data_dir, delete_activity_records=delete_activity_records)
    finally:
        conn.close()
    world.manager.reload()


@pytest.mark.parametrize("delete_activity_records", [False, True])
def test_e2e_account_purge_keeps_contributions_without_the_credit(world, delete_activity_records: bool) -> None:  # noqa: F811
    towns, notes, made = _purge_setup(world)
    before = _graph(world, world.kb["id"])
    assert set(before[0]) == {"Korea", "Seoul", "Busan"}
    _purge(world, delete_activity_records=delete_activity_records)

    conn = dbm.connect_existing(world.settings.db_file, readonly=True)
    try:
        # The visible contribution stays, without the credit; its document and graph stay.
        rows = kb_collab.list_contributions(conn, world.kb["id"])
        assert [(r["state"], r["contributor_account_id"], r["approved_by_account_id"], r["document_id"]) for r in rows] == [("visible", None, ANN, made["document"])]
        assert conn.execute("SELECT COUNT(*) FROM documents WHERE id=? AND hidden=0", (made["document"],)).fetchone()[0] == 1
        # The pending contribution and its hidden copy go.
        assert kb_collab.list_contributions(conn, towns["id"]) == []
        assert conn.execute("SELECT COUNT(*) FROM documents WHERE kb_id=?", (towns["id"],)).fetchone()[0] == 0
        # The membership, the personal knowledge base, and the stored reputation go; the knowledge base that ben founded stays without a founder.
        assert kb_collab.get_membership(conn, world.kb["id"], BEN) is None
        assert conn.execute("SELECT COUNT(*) FROM knowledge_bases WHERE account_id=?", (BEN,)).fetchone()[0] == 0
        assert kb_collab.get_reputation(conn, BEN) is None
        assert conn.execute("SELECT created_by_account_id FROM knowledge_bases WHERE id=?", (notes["id"],)).fetchone()[0] is None
        # The moderation action stays without an actor. The option clears the reason text and deletes the submissions.
        log = [(r["action"], r["actor_account_id"], r["reason"]) for r in kb_collab.list_moderation_log(conn, notes["id"])]
        assert log == [("reject_contribution", None, "" if delete_activity_records else "a text that ben wrote")]
        # Without the option the submission stays as a record without an account (design Section 11.2).
        assert [r[0] for r in conn.execute("SELECT account_id FROM kb_submissions")] == ([] if delete_activity_records else [None])
        assert conn.execute("SELECT COUNT(*) FROM kb_submission_items").fetchone()[0] == (0 if delete_activity_records else 1)
    finally:
        conn.close()
    # The option changes no content: the graph of Cities is the same.
    assert _graph(world, world.kb["id"]) == before
    listed = world.client.get(f"{world.base}/contributions").json()["contributions"]
    assert [(c["state"], c["contributor"]) for c in listed] == [("visible", None)]


def test_restore_skips_a_knowledge_base_that_is_shared_now(world) -> None:  # noqa: F811
    """A backup made before version 28 can have more knowledge bases than the personal one. The conversion made them shared: the restore skips them and lists them."""
    s = world.settings
    _personal_document(s, world.manager, BEN, "Seoul and Busan are cities of Korea. " * 3, extraction=EXTRACTION)
    personal = world.manager.personal_kb_for(BEN)
    record = account_backup.create_account_backup(s, account_id=BEN)
    conn = dbm.connect(s.db_file)
    try:
        conn.execute("UPDATE knowledge_bases SET account_id=NULL, is_personal=0, knowledge_type='real', created_by_account_id=? WHERE id=?", (BEN, personal.id))
        conn.execute("DELETE FROM documents WHERE kb_id=?", (personal.id,))
        conn.commit()
    finally:
        conn.close()
    result = account_backup.restore_account_backup(s, account_id=BEN, backup_id=record["backup_id"], actor_account_id=1)
    assert result["restored_themes"] == [] and result["skipped_knowledge_bases"] == [{"kb_id": personal.id, "label": personal.label}]
    conn = dbm.connect_existing(s.db_file, readonly=True)
    try:
        assert conn.execute("SELECT COUNT(*) FROM documents WHERE kb_id=?", (personal.id,)).fetchone()[0] == 0
        assert tuple(conn.execute("SELECT account_id, is_personal FROM knowledge_bases WHERE id=?", (personal.id,)).fetchone()) == (None, 0)
    finally:
        conn.close()


def test_telegram_selects_a_shared_knowledge_base_for_reading_only(world) -> None:  # noqa: F811
    scope = AccountThemeScope(world.manager, BEN)
    personal = world.manager.personal_kb_for(BEN)
    shared = world.kb["id"]
    assert {kb.id for kb in scope.readable_kbs()} == {personal.id, shared} and [kb.id for kb in scope.list_kbs()] == [personal.id]
    assert scope.get_readable_kb(shared).id == shared
    selections = {100: shared}
    # Search and read use the shared knowledge base; ingest goes to the personal knowledge base.
    assert resolve_selected_theme(scope, selections, 100, for_read=True).id == shared
    assert resolve_selected_theme(scope, selections, 100).id == personal.id
    assert selections == {100: shared}
    # The personal knowledge base of another account is not there.
    other = world.manager.personal_kb_for(CHO).id
    selections[100] = other
    assert resolve_selected_theme(scope, selections, 100, for_read=True) is None and selections == {}


def test_cli_theme_lists_judges_and_adjusts(world, monkeypatch: pytest.MonkeyPatch, capsys) -> None:  # noqa: F811
    monkeypatch.setattr("claire.cli.get_settings", lambda: world.settings)
    monkeypatch.setattr("claire.store.knowledge_base.get_kb_manager", lambda s=None: world.manager)
    conn = dbm.connect(world.settings.db_file)
    try:
        first = themes_store.create_theme(conn, label="Places", knowledge_type="real")
        second = themes_store.create_theme(conn, label="Towns", knowledge_type="real")
        conn.commit()
    finally:
        conn.close()
    number, other = ids.format_public_id(first["number"]), ids.format_public_id(second["number"])
    assert cli.main(["theme", "pin", number, world.kb["number"]]) == 0
    assert cli.main(["theme", "rename", number, "Cities of Korea"]) == 0
    capsys.readouterr()
    assert cli.main(["theme", "list"]) == 0
    listed = capsys.readouterr().out
    assert f"{number}  real      active    1 knowledge bases  Cities of Korea" in listed
    assert cli.main(["theme", "judge"]) == 0
    assert "The judgement is complete" in capsys.readouterr().out
    # The pin and the locked label stay after the judgement.
    conn = dbm.connect_existing(world.settings.db_file, readonly=True)
    try:
        theme = themes_store.get_theme(conn, first["id"])
        assert (theme["label"], theme["label_locked"]) == ("Cities of Korea", 1)
        assert [(m["kb_id"], m["pinned"]) for m in themes_store.theme_members(conn, first["id"])] == [(world.kb["id"], 1)]
    finally:
        conn.close()
    assert cli.main(["theme", "exclude", number, world.kb["number"]]) == 0
    assert cli.main(["theme", "merge", other, "--into", number]) == 0
    assert cli.main(["theme", "rename", "1", "x"]) == 1
    assert "No theme has the number 1" in capsys.readouterr().err
