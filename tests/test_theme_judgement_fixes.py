"""Regression tests for defects of the subject judgement and of the Telegram bot (SHARED_KNOWLEDGE_BASES_AND_THEMES_DESIGN.md Sections 8.2, 8.3, 8.5, and 13.4).

Each test fails with the code before its fix: the schedule that stays due, the new theme for an excluded knowledge base in each run, the idf ceiling of each score, the write lock during the computation, the pin of a personal knowledge base at a merge, the new theme for the cluster of a merged theme, the active theme with only personal knowledge bases, and the knowledge base 0 in the bot of a member account.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

from tests.test_theme_judgement import KOREA, SPACE, _fill, _run, _themes, env  # noqa: F401 (fixture)

from claire import theme_judgement as tj
from claire.config import Settings
from claire.store import db as dbm
from claire.store import themes as themes_store
from claire.store.knowledge_base import KnowledgeBaseManager


def _add_documents(settings: Settings, kb_id: int, count: int, prefix: str) -> None:
    conn = dbm.connect(settings.db_file)
    try:
        for i in range(count):
            conn.execute("INSERT INTO documents (id, kb_id, title, raw_text, hidden) VALUES (?, ?, ?, 'text', 0)", (f"{prefix}_{i}", kb_id, f"{prefix}_{i}"))
        conn.commit()
    finally:
        conn.close()


def _is_due(settings: Settings) -> bool:
    conn = dbm.connect_existing(settings.db_file, readonly=True)
    try:
        return tj.run_is_due(conn)
    finally:
        conn.close()


def _all_themes(settings: Settings) -> list[dict]:
    conn = dbm.connect_existing(settings.db_file, readonly=True)
    try:
        return themes_store.list_themes(conn, include_merged=True)
    finally:
        conn.close()


# --- finding 1: the schedule ---------------------------------------------------------------------------------------


def test_run_is_not_due_again_for_documents_that_a_run_counted(env) -> None:  # noqa: F811
    settings, manager = env
    # A public shared knowledge base with 25 visible documents and no entity: it gets no signature.
    plain = manager.define_kb("No entities", account_id=1, knowledge_type="real").id
    _add_documents(settings, plain, 25, "plain")
    # A public personal knowledge base with 30 visible documents; 25 of them are fiction-like, so its signature counts 5.
    personal = manager.define_kb("Ann home", account_id=2, is_personal=True, is_public=True).id
    documents = _fill(settings, personal, KOREA, documents=30)
    conn = dbm.connect(settings.db_file)
    try:
        for document_id in documents[5:]:
            themes_store.save_document_match(conn, document_id=document_id, kb_id=personal, theme_id=999, knowledge_type="fictional", score=1.0)
        conn.commit()
    finally:
        conn.close()

    assert _is_due(settings) is True
    _run(settings)
    conn = dbm.connect_existing(settings.db_file, readonly=True)
    try:
        assert {r["kb_id"]: r["visible_documents"] for r in themes_store.load_signatures(conn)} == {personal: 5}
        counts = themes_store.load_judged_counts(conn)
        assert (counts[plain], counts[personal]) == (25, 30)
    finally:
        conn.close()
    assert _is_due(settings) is False
    # 19 new visible documents are not sufficient; the next one makes a run due. After that run, no run is due.
    _add_documents(settings, plain, 19, "more")
    assert _is_due(settings) is False
    _add_documents(settings, plain, 1, "last")
    assert _is_due(settings) is True
    _run(settings)
    assert _is_due(settings) is False


def test_start_makes_the_table_of_the_judged_counts_and_keeps_its_rows(tmp_path: Path) -> None:
    conn = dbm.connect(tmp_path / "claire.db")
    try:
        dbm.init_db(conn)
        conn.execute("DROP TABLE kb_judged_counts")  # a database of the schema before this table
        conn.commit()
        dbm.init_db(conn)
        themes_store.replace_judged_counts(conn, {0: 7}, now=1.0)
        conn.commit()
        dbm.init_db(conn)
        assert themes_store.load_judged_counts(conn) == {0: 7}
    finally:
        conn.close()


# --- finding 2: the theme of an excluded knowledge base ------------------------------------------------------------


def test_excluded_knowledge_base_keeps_its_own_theme_across_runs(env) -> None:  # noqa: F811
    settings, manager = env
    kbs = [manager.define_kb(f"Korea {i}", account_id=1, knowledge_type="real").id for i in range(3)]
    for kb_id in kbs:
        _fill(settings, kb_id, KOREA)
    _run(settings)
    (theme,) = _themes(settings).values()
    conn = dbm.connect(settings.db_file)
    try:
        themes_store.set_admin_member(conn, theme_id=theme["id"], kb_id=kbs[2], pinned=False, excluded=True)
        conn.commit()
    finally:
        conn.close()
    _run(settings)
    first = {tuple(t["members"]): number for number, t in _themes(settings).items()}
    assert set(first) == {(kbs[0], kbs[1]), (kbs[2],)}
    for _ in range(2):
        assert _run(settings).themes_new == 0
        assert {tuple(t["members"]): number for number, t in _themes(settings).items()} == first
    # No theme without members stays from the runs.
    assert len(_all_themes(settings)) == 2


# --- finding 3: the idf ceiling ------------------------------------------------------------------------------------


class _CountingIdf(dict):
    reads = 0

    def values(self):  # type: ignore[override]
        self.reads += 1
        return super().values()


def test_matcher_computes_the_idf_ceiling_one_time() -> None:
    idf = _CountingIdf({("Concept", "seoul"): 1.0, ("Concept", "mars"): 2.0})
    matcher = tj.DocumentMatcher(idf=idf, themes=[{"id": 1, "knowledge_type": "real", "keys": {("Concept", "seoul")}, "shared": 1}])
    keys = {("Concept", "seoul"), ("Concept", "unknown"), ("Concept", "mars")}
    for _ in range(5):
        # The key that no signature has gets the highest idf (2.0): 1.0 / (1.0 + 2.0 + 2.0).
        assert matcher.score(keys, {("Concept", "seoul")}) == 0.2
        assert matcher.match(keys, threshold=0.1).theme_id == 1
        assert matcher.seed_score(keys, ["Seoul", "Unknown"]) == 1.0
    assert idf.reads == 1


# --- finding 4: the write lock -------------------------------------------------------------------------------------


def test_run_computes_outside_of_a_transaction_and_commits(env, monkeypatch) -> None:  # noqa: F811
    settings, manager = env
    for label, names in (("Korea A", KOREA), ("Korea B", KOREA)):
        _fill(settings, manager.define_kb(label, account_id=1, knowledge_type="real").id, names)
    personal = manager.define_kb("Ann home", account_id=2, is_personal=True, is_public=True).id
    _fill(settings, personal, KOREA)
    conn = dbm.connect(settings.db_file)
    other = dbm.connect(settings.db_file)
    other.execute("PRAGMA busy_timeout=0;")
    seen: list[str] = []
    real_cluster, real_similarity, real_match = tj.cluster, tj.similarity, tj.DocumentMatcher.match

    def _other_connection_writes(step: str) -> None:
        # The run holds no write lock during its computation: a writer on another connection does not wait.
        assert conn.in_transaction is False, step
        other.execute("INSERT INTO server_settings (key, value, updated_at, updated_by) VALUES (?, '1', 1, NULL) ON CONFLICT(key) DO UPDATE SET value='1'", (f"probe_{step}",))
        other.commit()
        seen.append(step)

    def _cluster(*args, **kwargs):
        _other_connection_writes("cluster")
        return real_cluster(*args, **kwargs)

    def _similarity(*args, **kwargs):
        _other_connection_writes("similarity")
        return real_similarity(*args, **kwargs)

    def _match(self, *args, **kwargs):
        _other_connection_writes("match")
        return real_match(self, *args, **kwargs)

    monkeypatch.setattr(tj, "cluster", _cluster)
    monkeypatch.setattr(tj, "similarity", _similarity)
    monkeypatch.setattr(tj.DocumentMatcher, "match", _match)
    try:
        report = tj.run_judgement(conn)
        assert {"cluster", "similarity", "match"} <= set(seen)
        # The run committed: the connection has no open transaction, and another connection reads the result with no commit of the caller.
        assert conn.in_transaction is False
        assert report.memberships == 3 and report.document_matches == 4  # the two last documents of ``_fill`` have fewer than 3 keys
        assert len(themes_store.all_members(other)) == 3 and len(themes_store.fiction_like_documents(other, personal)) == 0
        assert other.execute("SELECT COUNT(*) FROM kb_document_matches WHERE kb_id=? AND theme_id IS NOT NULL", (personal,)).fetchone()[0] == 4
        assert tj.run_is_due(other) is False
    finally:
        conn.close()
        other.close()


def test_failed_write_of_a_run_leaves_no_part_of_the_membership_change(env, monkeypatch) -> None:  # noqa: F811
    settings, manager = env
    _fill(settings, manager.define_kb("Korea", account_id=1, knowledge_type="real").id, KOREA)

    def _fail(*args, **kwargs):
        raise sqlite3.OperationalError("database is locked")

    monkeypatch.setattr(themes_store, "delete_free_remote_members", _fail)
    conn = dbm.connect(settings.db_file)
    try:
        try:
            tj.run_judgement(conn)
        except sqlite3.OperationalError:
            pass
        else:
            raise AssertionError("the run did not fail")
        assert conn.in_transaction is False
        assert themes_store.list_themes(conn) == [] and themes_store.load_signatures(conn) == [] and tj.run_is_due(conn) is True
    finally:
        conn.close()


# --- finding 5: merge ----------------------------------------------------------------------------------------------


def test_merge_pins_shared_members_only_and_the_merged_cluster_takes_the_target(env) -> None:  # noqa: F811
    settings, manager = env
    korea_a = manager.define_kb("Korea A", account_id=1, knowledge_type="real").id
    korea_b = manager.define_kb("Korea B", account_id=1, knowledge_type="real").id
    space = manager.define_kb("Space", account_id=1, knowledge_type="real").id
    personal = manager.define_kb("Ann home", account_id=2, is_personal=True, is_public=True).id
    for kb_id, names in ((korea_a, KOREA), (korea_b, KOREA), (space, SPACE), (personal, KOREA)):
        _fill(settings, kb_id, names)
    _run(settings)
    themes = {tuple(t["members"]): t for t in _themes(settings).values()}
    korea_theme, space_theme = themes[(korea_a, korea_b, personal)]["id"], themes[(space,)]["id"]
    conn = dbm.connect(settings.db_file)
    try:
        themes_store.merge_themes(conn, source_id=korea_theme, target_id=space_theme)
        conn.commit()
        # The shared members of the merged theme are pinned to the target; the attached personal knowledge base gets no pin.
        assert {m["kb_id"]: m["pinned"] for m in themes_store.theme_members(conn, space_theme)} == {korea_a: 1, korea_b: 1, space: 0}
    finally:
        conn.close()
    for _ in range(2):
        # The cluster of the merged theme takes the target: no new theme, and the personal knowledge base attaches again by its score.
        assert _run(settings).themes_new == 0
        now = _themes(settings)
        assert [t["members"] for t in now.values()] == [[korea_a, korea_b, space, personal]] and next(iter(now.values()))["id"] == space_theme
    assert sorted(t["state"] for t in _all_themes(settings)) == ["active", "merged"]
    # The membership of the personal knowledge base is not pinned: it goes when the knowledge base becomes private.
    conn = dbm.connect_existing(settings.db_file, readonly=True)
    try:
        assert themes_store.get_member(conn, space_theme, personal)["pinned"] == 0
    finally:
        conn.close()
    manager.update_kb(personal, is_public=False)
    _run(settings)
    assert next(iter(_themes(settings).values()))["members"] == [korea_a, korea_b, space]


# --- finding 6: a theme with only personal knowledge bases ---------------------------------------------------------


def test_theme_with_only_personal_knowledge_bases_is_empty(env) -> None:  # noqa: F811
    settings, manager = env
    personal = manager.define_kb("Ann home", account_id=2, is_personal=True, is_public=True).id
    _fill(settings, personal, KOREA, documents=3)  # fewer than 5 documents: no signature, so its memberships stay (rule 6)
    conn = dbm.connect(settings.db_file)
    try:
        theme = themes_store.create_theme(conn, label="Left alone", knowledge_type="real")
        themes_store.add_theme_member(conn, theme_id=theme["id"], kb_id=personal, source="computed", score=0.5)
        conn.commit()
        assert themes_store.get_theme(conn, theme["id"])["state"] == "active"
    finally:
        conn.close()
    report = _run(settings)
    conn = dbm.connect_existing(settings.db_file, readonly=True)
    try:
        assert themes_store.get_theme(conn, theme["id"])["state"] == "empty" and report.themes_empty == 1
        assert themes_store.get_member(conn, theme["id"], personal) is not None
    finally:
        conn.close()


# --- finding 7: the bot of a member account ------------------------------------------------------------------------


async def test_member_bot_ingests_to_the_personal_knowledge_base_of_its_account(tmp_path: Path, monkeypatch) -> None:
    from claire.ingest.pipeline import IngestReport
    from claire.store.telegram_bots import TelegramBotProfile
    from claire.telegram_bot import AccountThemeScope, build_app, resolve_selected_theme

    (tmp_path / "data").mkdir()
    settings = Settings(CLAIRE_DB_PATH=str(tmp_path / "data" / "claire.db"), CLAIRE_VAULT_PATH=str(tmp_path / "vault"), CLAIRE_PROVIDER="mock", CLAIRE_MULTI_THEME=True, telegram_bot_token="")
    conn = dbm.connect(settings.db_file)
    dbm.init_db(conn)
    member = conn.execute("INSERT INTO accounts (public_id, username, role, status, created_at, updated_at) VALUES (223456789012, 'member_alpha', 'member', 'active', 0, 0)").lastrowid
    conn.commit()
    conn.close()
    manager = KnowledgeBaseManager(settings)
    own = manager.define_kb("Member home", account_id=member, is_personal=True)
    assert own.id != 0

    # No selection gives the personal knowledge base of the account, for ingest and for read; it does not give the knowledge base 0 of the admin.
    scope = AccountThemeScope(manager, member)
    assert resolve_selected_theme(scope, {}, 42).id == own.id and resolve_selected_theme(scope, {}, 42, for_read=True).id == own.id
    assert resolve_selected_theme(AccountThemeScope(manager, dbm.ADMIN_ACCOUNT_ID), {}, 42).id == 0

    targets: list[int] = []

    def _ingest(self, *args, **kwargs):
        targets.append(self.kb_id)
        return IngestReport(document_id="doc_member_1", title="Member document", summary="summary")

    monkeypatch.setattr("claire.ingest.service.IngestService.ingest", _ingest)
    profile = TelegramBotProfile(account_id=member, bot_id=7100000001, token="7100000001:" + "A" * 35, allowed_user_ids=(42,), fingerprint="abcd1234")
    app = build_app(settings, profile=profile)
    handlers = {getattr(h.callback, "__name__", ""): h.callback for h in app.handlers[0]}

    def _update(update_id: int, text: str):
        status = AsyncMock()
        msg = AsyncMock()
        msg.text = text
        msg.reply_text = AsyncMock(return_value=status)
        update = MagicMock()
        update.effective_user = SimpleNamespace(id=42)
        update.effective_chat = SimpleNamespace(id=200)
        update.effective_message = msg
        update.message = msg
        update.update_id = update_id
        return update

    ctx = MagicMock()
    ctx.args = ["A", "note", "about", "the", "rivers", "of", "Korea", "for", "the", "command."]
    await handlers["on_ingest"](_update(9001, ""), ctx)
    await handlers["on_message"](_update(9002, "A note about the rivers of Korea for the text message."), MagicMock())
    assert targets == [own.id, own.id]
