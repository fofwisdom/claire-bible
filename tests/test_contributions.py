"""Direct contributions, the merge job, review actions, withdrawal, and revisions (SHARED_KNOWLEDGE_BASES_AND_THEMES_DESIGN.md Sections 9.1, 9.4 to 9.6; unit CT-4).

Completion criteria: the graph is built without an LLM call; a duplicate gets 409; a withdrawal after the approval gets 409; a new content hash of the root updates the copies without approval, also after the source becomes private; a stopped copy stays.
"""

from __future__ import annotations

import functools
import json
import time
from pathlib import Path
from types import SimpleNamespace

import pytest
from starlette.testclient import TestClient

from tests.kb_paths import seed

from claire.collaboration import contributions
from claire.core import ids
from claire.api.server import create_app
from claire.core.config import Settings
from claire.extract import provider as provider_module
from claire.ingest.normalize import content_hash
from claire.store import accounts as accounts_store
from claire.store import db as dbm
from claire.store import kb_collab
from claire.store.knowledge_base import KnowledgeBaseManager
from tests.auth_helpers import bearer_headers

PUBLIC_URL = "http://127.0.0.1:8765"
ACCOUNTS = {"admin": 1, "ann": 2, "ben": 3, "cho": 4}
# A stored extraction with entities and relations (the JSON of ``ExtractionResult``).
EXTRACTION = {
    "summary": "Seoul and Busan are cities of Korea.",
    "key_claims": ["Seoul is the capital."],
    "entities": [
        {"name": "Korea", "type": "Concept", "aliases": ["Republic of Korea"], "observations": ["A country in East Asia."]},
        {"name": "Seoul", "type": "Concept", "aliases": [], "observations": ["The capital of Korea."]},
        {"name": "Busan", "type": "Concept", "aliases": [], "observations": ["A port city of Korea."]},
    ],
    "relations": [
        {"source": "Seoul", "target": "Korea", "type": "part_of"},
        {"source": "Busan", "target": "Korea", "type": "part_of"},
        {"source": "Seoul", "target": "Nowhere", "type": "part_of"},
    ],
}


# True while a test helper ingests a source document: that ingest uses the model, as each ingest does.
_SEEDING = {"on": False}


@pytest.fixture
def no_llm(monkeypatch: pytest.MonkeyPatch) -> list[str]:
    """Fail the test when a contribution, its merge job, or a revision makes an LLM call (CT24). Embeddings are not LLM calls."""
    calls: list[str] = []

    def _forbidden(name: str, original):
        @functools.wraps(original)  # the pipeline reads the signature of the provider functions
        def _call(self, *args, **kwargs):
            if _SEEDING["on"]:
                return original(self, *args, **kwargs)
            calls.append(name)
            raise AssertionError(f"the merge job made an LLM call: {name}")

        return _call

    guarded = [name for name in ("extract", "judge_same_entity", "judge_relationship", "summarize_document", "render_detail", "render_document_detail") if hasattr(provider_module.MockProvider, name)]
    assert "extract" in guarded
    for name in guarded:
        monkeypatch.setattr(provider_module.MockProvider, name, _forbidden(name, getattr(provider_module.MockProvider, name)))
    return calls


def _personal_document(settings: Settings, manager: KnowledgeBaseManager, account_id: int, text: str, *, url: str | None = None, extraction: dict | None = None) -> str:
    """Put one document with an extraction into the personal knowledge base of ``account_id``. The server ingests the text in the knowledge base 0 (mock provider), and the rows are copied; then the document gets the given URL and extraction."""
    _SEEDING["on"] = True
    try:
        report = seed(settings, 0, text)
    finally:
        _SEEDING["on"] = False
    assert report.error is None, report.error
    personal = manager.personal_kb_for(account_id)
    conn = dbm.connect(settings.db_file)
    try:
        if personal.id == 0:
            document_id = report.document_id
        else:
            document_id = contributions.copy_document(conn, source_document_id=report.document_id, source_kb_id=0, target_kb_id=personal.id, hidden=False)
            conn.commit()
            dbm.purge_document_cascade(conn, data_dir=Path(settings.data_dir), target_ids=[report.document_id], tombstone=False, kb_id=0)
        if url is not None:
            conn.execute("UPDATE documents SET url=?, canonical_url=? WHERE id=?", (url, url, document_id))
        if extraction is not None:
            conn.execute("UPDATE extractions SET raw_response=? WHERE document_id=?", (json.dumps(extraction), document_id))
        conn.commit()
    finally:
        conn.close()
    return document_id


@pytest.fixture
def world(tmp_path: Path):
    """A network-mode server. ann founds ``Cities`` (join policy ``open``); ben is a member; cho is not. Each account has a personal knowledge base."""
    (tmp_path / "data").mkdir(parents=True)
    settings = Settings(
        CLAIRE_DB_PATH=str(tmp_path / "data" / "claire.db"),
        CLAIRE_PROVIDER="mock",
        CLAIRE_ENVIRONMENT="development",
        CLAIRE_FQDN=PUBLIC_URL,
    )
    conn = dbm.connect(settings.db_file)
    try:
        dbm.init_db(conn)
        now = time.time()
        for username, account_id in ACCOUNTS.items():
            conn.execute(
                "INSERT INTO accounts (id, public_id, username, role, status, display_name, profile_public, created_at, updated_at) VALUES (?, ?, ?, ?, 'active', ?, 1, ?, ?) "
                "ON CONFLICT(id) DO UPDATE SET username=excluded.username, role=excluded.role, status=excluded.status, display_name=excluded.display_name, profile_public=excluded.profile_public",
                (account_id, (1 << 57) + account_id, username, "admin" if account_id == 1 else "member", username.capitalize(), now, now),
            )
        accounts_store.AccountStore(conn).set_setting("mode", "network", updated_by=1)
        conn.commit()
    finally:
        conn.close()
    manager = KnowledgeBaseManager(settings)
    for username, account_id in ACCOUNTS.items():
        if account_id != 1:
            manager.define_kb(f"{username} home", account_id=account_id, is_personal=True, is_public=True)
    def bearer(name: str, scope: str = "owner") -> dict[str, str]:
        return bearer_headers(settings, account_id=ACCOUNTS[name], scope=scope)

    with TestClient(create_app(settings=settings), base_url=PUBLIC_URL, follow_redirects=False) as client:
        ann, ben, cho, admin = bearer("ann"), bearer("ben"), bearer("cho"), bearer("admin")
        kb = client.post("/kb", json={"label": "Cities", "knowledge_type": "real"}, headers=ann).json()["knowledge_base"]
        base = f"/kb/{kb['number']}"
        assert client.patch(f"{base}/", json={"join_policy": "open"}, headers=ann).status_code == 200
        assert client.post(f"{base}/membership", headers=ben).status_code == 201
        yield SimpleNamespace(settings=settings, manager=manager, client=client, kb=kb, base=base, ann=ann, ben=ben, cho=cho, admin=admin, bearer=bearer)


def _db(world):
    return dbm.connect_existing(world.settings.db_file, readonly=True)


def _graph(world, kb_id: int) -> tuple[dict[str, list[str]], list[tuple[str, str, str]]]:
    """Entities (name → source documents) and relations (source name, type, target name) of one knowledge base."""
    conn = _db(world)
    try:
        entities = {r["name"]: json.loads(r["sources"] or "[]") for r in conn.execute("SELECT name, sources FROM entities WHERE kb_id=?", (kb_id,))}
        relations = sorted(
            (r[0], r[1], r[2])
            for r in conn.execute(
                "SELECT s.name, r.type, t.name FROM relations r JOIN entities s ON s.id = r.source_id JOIN entities t ON t.id = r.target_id WHERE r.kb_id=?", (kb_id,)
            )
        )
        return entities, relations
    finally:
        conn.close()


def _contribute(world, headers, document_id: str):
    return world.client.post(f"{world.base}/contributions", json={"document": document_id}, headers=headers)


# --- Section 9.1 ----------------------------------------------------------------------------------------------------


def test_e2e_direct_contribution_builds_the_graph_without_an_llm_call(world, no_llm) -> None:
    client, kb_id = world.client, world.kb["id"]
    document = _personal_document(world.settings, world.manager, 3, "Seoul and Busan are cities of Korea. " * 3, url="https://example.org/cities", extraction=EXTRACTION)
    made = _contribute(world, world.ben, document)
    assert made.status_code == 201, made.text
    contribution = made.json()["contribution"]
    # A member contributes directly; the contribution is visible after the merge job and is not approved yet (the contributor can withdraw).
    assert (contribution["state"], contribution["origin"], contribution["approved"], contribution["contributor"]["display_name"]) == ("visible", "direct", False, "Ben")
    copy_id = contribution["document"]
    assert copy_id != document and no_llm == []

    # The graph of K comes from the stored extraction: three entities, and the two relations whose ends are in the extraction.
    entities, relations = _graph(world, kb_id)
    assert entities == {"Korea": [copy_id], "Seoul": [copy_id], "Busan": [copy_id]}
    assert relations == [("Busan", "part_of", "Korea"), ("Seoul", "part_of", "Korea")]
    conn = _db(world)
    try:
        # The embeddings use the provider of the server and fix the model of K.
        assert conn.execute("SELECT COUNT(*) FROM embeddings WHERE kb_id=?", (kb_id,)).fetchone()[0] == 3
        assert tuple(conn.execute("SELECT embed_provider, embed_model FROM knowledge_bases WHERE id=?", (kb_id,)).fetchone()) == ("mock", "mock-sha256")
        copy = conn.execute("SELECT kb_id, hidden, canonical_url, content_hash, watch_enabled, meta FROM documents WHERE id=?", (copy_id,)).fetchone()
        source = conn.execute("SELECT kb_id, content_hash FROM documents WHERE id=?", (document,)).fetchone()
        assert (copy["kb_id"], copy["hidden"], copy["canonical_url"], copy["watch_enabled"]) == (kb_id, 0, "https://example.org/cities", 0)
        assert copy["content_hash"] == source["content_hash"] and source["kb_id"] != kb_id
        row = kb_collab.get_contribution(conn, ids.parse_public_id(contribution["number"]))
        assert (row["root_document_id"], row["source_document_id"], row["contributor_account_id"], row["approved_at"]) == (document, document, 3, None)
        assert [tuple(r) for r in conn.execute("SELECT source_type, status FROM jobs WHERE source_type='contribution_merge'")] == [("contribution_merge", "done")]
    finally:
        conn.close()

    # Each reader sees the document and the contribution in K.
    assert client.get(f"{world.base}/document?id={copy_id}").status_code == 200
    listed = client.get(f"{world.base}/contributions").json()["contributions"]
    assert [(c["number"], c["state"]) for c in listed] == [(contribution["number"], "visible")] and "reason" not in listed[0]

    # A duplicate (the same canonical URL or the same content) gets 409 with the URL of the document that K has.
    again = _contribute(world, world.ben, document)
    assert (again.status_code, again.json()) == (409, {"error": "contribution_duplicate", "url": f"{PUBLIC_URL}{world.base}/?doc={copy_id}"})
    other = _personal_document(world.settings, world.manager, 2, "Another text about the same page. " * 3, url="https://example.org/cities")
    assert _contribute(world, world.ann, other).json()["error"] == "contribution_duplicate"

    # A reviewer contributes without approval (Section 6.2 rule 2).
    second = _personal_document(world.settings, world.manager, 2, "Daegu is a city of Korea too. " * 3, extraction={**EXTRACTION, "entities": [EXTRACTION["entities"][0], {"name": "Daegu", "type": "Concept", "aliases": [], "observations": ["A city."]}], "relations": [{"source": "Daegu", "target": "Korea", "type": "part_of"}]})
    by_moderator = _contribute(world, world.ann, second).json()["contribution"]
    assert (by_moderator["state"], by_moderator["approved"]) == ("visible", True)
    # The entity ``Korea`` merges by its name, without a judge: it has two sources now.
    entities, relations = _graph(world, kb_id)
    assert sorted(entities["Korea"]) == sorted([copy_id, by_moderator["document"]]) and ("Daegu", "part_of", "Korea") in relations
    assert no_llm == []


def test_contribution_checks(world) -> None:
    client = world.client
    document = _personal_document(world.settings, world.manager, 3, "A note about rivers of Korea. " * 3)
    # Only a member contributes directly (CT5): another account gets 403, an anonymous request 404, a ``readonly`` token 403.
    assert _contribute(world, world.cho, document).json() == {"error": "kb_capability_required"}
    assert _contribute(world, {}, document).status_code == 404
    assert _contribute(world, world.bearer("ben", "readonly"), document).json()["error"] == "scope_insufficient"
    # The document must be in the personal knowledge base of the requester.
    foreign = _personal_document(world.settings, world.manager, 4, "A note of another account. " * 3)
    assert _contribute(world, world.ben, foreign).json() == {"error": "document_not_found"}
    assert _contribute(world, world.ben, "doc_missing").json() == {"error": "document_not_found"}
    for body in ({}, {"document": ""}, {"document": 5}):
        assert client.post(f"{world.base}/contributions", json=body, headers=world.ben).json() == {"error": "document_required"}
    # A document without a completed extraction: 409.
    conn = dbm.connect(world.settings.db_file)
    try:
        conn.execute("DELETE FROM extractions WHERE document_id=?", (document,))
        conn.commit()
    finally:
        conn.close()
    refused = _contribute(world, world.ben, document)
    assert (refused.status_code, refused.json()) == (409, {"error": "document_not_extracted"})
    # An archived knowledge base takes no contribution; a personal knowledge base has no contributions.
    assert client.patch(f"{world.base}/", json={"state": "archived"}, headers=world.ann).status_code == 200
    assert _contribute(world, world.ben, foreign).json() == {"error": "kb_archived"}
    assert client.post("/@ben/contributions", json={"document": document}, headers=world.ben).status_code == 404
    # The scope ``write`` contributes (Section 6.6).
    assert client.patch(f"{world.base}/", json={"state": "active"}, headers=world.ann).status_code == 200
    by_tool = _personal_document(world.settings, world.manager, 3, "A note that a tool contributes. " * 3)
    made = _contribute(world, world.bearer("ben", "write"), by_tool)
    assert (made.status_code, made.json()["contribution"]["state"]) == (201, "visible")


def test_failed_merge_job_and_retry(world, monkeypatch: pytest.MonkeyPatch) -> None:
    document = _personal_document(world.settings, world.manager, 3, "A note with a broken extraction. " * 3)
    conn = dbm.connect(world.settings.db_file)
    try:
        conn.execute("UPDATE extractions SET raw_response='not json' WHERE document_id=?", (document,))
        conn.commit()
    finally:
        conn.close()
    made = _contribute(world, world.ben, document)
    failed = made.json()["contribution"]
    assert (made.status_code, failed["state"], failed["reason"]) == (201, "failed", "extraction_unreadable")
    # The copy of a failed contribution is not visible.
    assert world.client.get(f"{world.base}/document?id={failed['document']}").status_code == 404
    assert world.client.get(f"{world.base}/contributions").json()["contributions"] == []
    # The contributor or a reviewer tries again; another account does not.
    retry = f"{world.base}/contributions/{failed['number']}/retry"
    assert world.client.post(retry, headers=world.cho).status_code == 404
    conn = dbm.connect(world.settings.db_file)
    try:
        conn.execute("UPDATE extractions SET raw_response=? WHERE document_id=?", (json.dumps(EXTRACTION), failed["document"]))
        conn.commit()
    finally:
        conn.close()
    done = world.client.post(retry, headers=world.ben)
    assert (done.status_code, done.json()["contribution"]["state"]) == (200, "visible")
    assert world.client.post(retry, headers=world.ann).json() == {"error": "contribution_not_failed"}

    # The provider of the server does not serve the fixed embedding model of K: the merge fails with a code and makes no embedding.
    conn = dbm.connect(world.settings.db_file)
    try:
        conn.execute("UPDATE knowledge_bases SET embed_model='another-model' WHERE id=?", (world.kb["id"],))
        conn.commit()
    finally:
        conn.close()
    other = _personal_document(world.settings, world.manager, 3, "A note for another embedding model. " * 3)
    assert _contribute(world, world.ben, other).json()["contribution"]["reason"] == "embedding_model_unavailable"


# --- Sections 9.4 and 9.6 -------------------------------------------------------------------------------------------


def test_withdraw_before_approval_and_409_after(world) -> None:
    client, kb_id = world.client, world.kb["id"]
    first = _personal_document(world.settings, world.manager, 3, "A note that the contributor withdraws. " * 3, extraction=EXTRACTION)
    contribution = _contribute(world, world.ben, first).json()["contribution"]
    url = f"{world.base}/contributions/{contribution['number']}"
    # Only the contributor withdraws.
    assert client.delete(url, headers=world.ann).json() == {"error": "contribution_not_found"}
    assert client.delete(url, headers=world.bearer("ben", "write")).status_code == 200
    # The row, the copy, and its graph are gone; no tombstone: the document can come again.
    assert client.get(f"{world.base}/contributions", headers=world.ann).json()["contributions"] == []
    assert _graph(world, kb_id) == ({}, [])
    assert client.get(f"{world.base}/document?id={contribution['document']}", headers=world.ann).status_code == 404
    again = _contribute(world, world.ben, first).json()["contribution"]
    assert again["state"] == "visible"

    # An approval is final: after it a withdrawal gets 409.
    url = f"{world.base}/contributions/{again['number']}"
    assert client.patch(url, json={"action": "approve"}, headers=world.ben).json() == {"error": "kb_capability_required"}
    approved = client.patch(url, json={"action": "approve"}, headers=world.ann)
    assert (approved.status_code, approved.json()["contribution"]["approved"]) == (200, True)
    assert client.patch(url, json={"action": "approve"}, headers=world.ann).json() == {"error": "contribution_approved"}
    refused = client.delete(url, headers=world.ben)
    assert (refused.status_code, refused.json()) == (409, {"error": "contribution_approved"})
    assert _graph(world, kb_id)[0].keys() == {"Korea", "Seoul", "Busan"}


def test_review_actions_hide_stop_updates_reject_and_remove(world) -> None:
    client, kb_id = world.client, world.kb["id"]
    document = _personal_document(world.settings, world.manager, 3, "A note that reviewers act on. " * 3, url="https://example.org/reviewed", extraction=EXTRACTION)
    contribution = _contribute(world, world.ben, document).json()["contribution"]
    url = f"{world.base}/contributions/{contribution['number']}"
    copy_id = contribution["document"]

    # Hide and unhide are reversible; the generic document route does the same.
    hidden = client.patch(url, json={"action": "hide"}, headers=world.ann).json()["contribution"]
    assert hidden["state"] == "hidden"
    assert client.get(f"{world.base}/document?id={copy_id}").status_code == 404
    assert client.patch(url, json={"action": "hide"}, headers=world.ann).json() == {"error": "contribution_not_visible"}
    assert client.post(f"{world.base}/document/hide", json={"id": copy_id, "hidden": False}, headers=world.ann).status_code == 200
    assert client.get(f"{world.base}/contributions").json()["contributions"][0]["state"] == "visible"
    # Stop and restart the updates.
    assert client.patch(url, json={"action": "stop_updates"}, headers=world.ann).json()["contribution"]["updates_stopped"] is True
    assert client.patch(url, json={"action": "restart_updates"}, headers=world.ann).json()["contribution"]["updates_stopped"] is False
    # A reject is for a pending contribution only, and needs a reason code.
    assert client.patch(url, json={"action": "reject", "reason": "spam"}, headers=world.ann).json() == {"error": "contribution_not_pending"}
    assert client.patch(url, json={"action": "reject", "reason": "because"}, headers=world.ann).json() == {"error": "reason_invalid"}
    assert client.patch(url, json={"action": "delete"}, headers=world.ann).json() == {"error": "action_invalid"}

    # Remove needs the capability ``remove`` (a moderator) and a reason code. It purges the copy and leaves a tombstone.
    conn = dbm.connect(world.settings.db_file)
    try:
        kb_collab.update_membership(conn, kb_id=kb_id, account_id=3, role="collaborator")
        conn.commit()
    finally:
        conn.close()
    assert client.post(f"{url}/remove", json={"reason": "spam"}, headers=world.ben).json() == {"error": "kb_capability_required"}
    assert client.post(f"{url}/remove", json={}, headers=world.ann).json() == {"error": "reason_invalid"}
    assert client.post(f"{url}/remove", json={"reason": "off_subject"}, headers=world.ann).status_code == 200
    assert _graph(world, kb_id) == ({}, [])
    removed = client.get(f"{world.base}/contributions", headers=world.ann).json()["contributions"]
    assert [(c["state"], c["reason"], c["document"]) for c in removed] == [("removed", "off_subject", None)]
    assert client.get(f"{world.base}/contributions").json()["contributions"] == []
    assert client.post(f"{url}/remove", json={"reason": "spam"}, headers=world.ann).json() == {"error": "contribution_closed"}
    # The tombstone keeps the same source out.
    refused = _contribute(world, world.ben, document)
    assert (refused.status_code, refused.json()) == (409, {"error": "contribution_tombstoned"})

    conn = _db(world)
    try:
        actions = [r["action"] for r in kb_collab.list_moderation_log(conn, kb_id) if r["action"].startswith("contribution_")]
    finally:
        conn.close()
    assert actions == ["contribution_hide", "contribution_unhide", "contribution_stop_updates", "contribution_restart_updates", "contribution_remove"]


def test_pending_contribution_is_approved_or_rejected(world, no_llm) -> None:
    """A pending contribution (the reputation ``low`` of unit CT-11, or a contribution to a theme of unit CT-6): its copy is hidden and has no graph until a reviewer approves."""
    client, kb_id = world.client, world.kb["id"]
    documents = [_personal_document(world.settings, world.manager, 3, f"A pending note number {i}. " * 3, extraction=EXTRACTION if i == 0 else None) for i in range(2)]
    numbers = []
    personal_id = world.manager.personal_kb_for(3).id
    conn = dbm.connect(world.settings.db_file)
    try:
        for document in documents:
            copy_id = contributions.copy_document(conn, source_document_id=document, source_kb_id=personal_id, target_kb_id=kb_id, hidden=True)
            numbers.append(kb_collab.add_contribution(conn, kb_id=kb_id, document_id=copy_id, origin="direct", state="pending", contributor_account_id=3, source_kb_id=personal_id, source_document_id=document, root_kb_id=personal_id, root_document_id=document))
        conn.commit()
    finally:
        conn.close()
    urls = [f"{world.base}/contributions/{ids.format_public_id(n)}" for n in numbers]
    # Pending contributions are not visible; the contributor and the reviewers see them.
    assert client.get(f"{world.base}/contributions").json()["contributions"] == []
    assert len(client.get(f"{world.base}/contributions?state=pending", headers=world.ben).json()["contributions"]) == 2
    assert _graph(world, kb_id) == ({}, [])
    # Approve: the merge job runs, then the state is ``visible``.
    approved = client.patch(urls[0], json={"action": "approve"}, headers=world.ann).json()["contribution"]
    assert (approved["state"], approved["approved"]) == ("visible", True) and no_llm == []
    assert _graph(world, kb_id)[0].keys() == {"Korea", "Seoul", "Busan"}
    # Reject with a reason code: the copy is deleted and the state is ``rejected``.
    assert client.patch(urls[1], json={"action": "reject", "reason": "low_quality"}, headers=world.ann).status_code == 200
    rejected = [c for c in client.get(f"{world.base}/contributions", headers=world.ann).json()["contributions"] if c["state"] == "rejected"]
    assert [(c["reason"], c["document"]) for c in rejected] == [("low_quality", None)]
    assert client.delete(urls[1], headers=world.ben).json() == {"error": "contribution_closed"}


# --- Section 9.5 ----------------------------------------------------------------------------------------------------


def _new_version(world, document_id: str, kb_id: int, text: str, extraction: dict) -> None:
    """A new fetch gave the root document a new content and a new extraction (as refresh, watch, and re-ingest do), and the pipeline starts the revisions."""
    from claire.ingest.pipeline import _start_contribution_revisions
    from claire.ingest.service import run_waiting_merge_jobs

    conn = dbm.connect(world.settings.db_file)
    try:
        conn.execute("UPDATE documents SET raw_text=?, content_hash=? WHERE id=?", (text, content_hash(text), document_id))
        conn.execute("INSERT INTO extractions (kb_id, document_id, provider, model, prompt_version, raw_response, created_at) VALUES (?, ?, 'mock', 'mock', 'test', ?, ?)", (kb_id, document_id, json.dumps(extraction), time.time()))
        conn.commit()
        _start_contribution_revisions(conn, world.settings, kb_id=kb_id, document_id=document_id)
    finally:
        conn.close()
    run_waiting_merge_jobs(world.settings)


def test_new_content_of_the_root_updates_the_copies_without_approval(world, no_llm) -> None:
    client, kb_id = world.client, world.kb["id"]
    personal = world.manager.personal_kb_for(3)
    followed = _personal_document(world.settings, world.manager, 3, "The first version of a page about Korea. " * 3, url="https://example.org/followed", extraction=EXTRACTION)
    stopped = _personal_document(world.settings, world.manager, 3, "The first version of another page. " * 3, url="https://example.org/stopped", extraction={**EXTRACTION, "entities": [{"name": "Jeju", "type": "Concept", "aliases": [], "observations": ["An island."]}], "relations": []})
    first = _contribute(world, world.ben, followed).json()["contribution"]
    second = _contribute(world, world.ben, stopped).json()["contribution"]
    assert client.patch(f"{world.base}/contributions/{first['number']}", json={"action": "approve"}, headers=world.ann).status_code == 200
    assert client.patch(f"{world.base}/contributions/{second['number']}", json={"action": "stop_updates"}, headers=world.ann).status_code == 200
    # The personal knowledge base becomes private after the contribution (CT26): the revisions still go.
    assert client.patch("/@ben/", json={"is_public": False}, headers=world.ben).status_code == 200

    revised = {**EXTRACTION, "entities": [EXTRACTION["entities"][0], {"name": "Incheon", "type": "Concept", "aliases": [], "observations": ["A port city."]}], "relations": [{"source": "Incheon", "target": "Korea", "type": "part_of"}]}
    _new_version(world, followed, personal.id, "The second version of a page about Korea. " * 3, revised)
    _new_version(world, stopped, personal.id, "The second version of another page. " * 3, revised)
    assert no_llm == []

    after = {c["number"]: c for c in client.get(f"{world.base}/contributions", headers=world.ann).json()["contributions"]}
    # The followed contribution points to a new copy with the new content; the state and the approval stay; no new approval.
    new_copy = after[first["number"]]["document"]
    assert new_copy != first["document"] and (after[first["number"]]["state"], after[first["number"]]["approved"]) == ("visible", True)
    assert [(r["document"], r["state"]) for r in after[first["number"]]["revisions"]] == [(new_copy, "done")]
    conn = _db(world)
    try:
        assert conn.execute("SELECT content_hash FROM documents WHERE id=?", (new_copy,)).fetchone()[0] == content_hash("The second version of a page about Korea. " * 3)
        assert conn.execute("SELECT 1 FROM documents WHERE id=?", (first["document"],)).fetchone() is None
    finally:
        conn.close()
    # The graph has the sources of the new copy only; the entities of the old version that the new one does not have are gone.
    entities, relations = _graph(world, kb_id)
    assert entities["Korea"] == [new_copy] and entities["Incheon"] == [new_copy] and "Seoul" not in entities and "Busan" not in entities
    assert relations == [("Incheon", "part_of", "Korea")]
    # A stopped copy stays as it was.
    assert after[second["number"]]["document"] == second["document"] and after[second["number"]]["revisions"] == []
    assert entities["Jeju"] == [second["document"]]
    assert client.get(f"{world.base}/document?id={new_copy}").status_code == 200

    # The same content makes no revision (regenerate, or a re-ingest without a change).
    _new_version(world, followed, personal.id, "The second version of a page about Korea. " * 3, revised)
    assert client.get(f"{world.base}/contributions", headers=world.ann).json()["contributions"][0]["document"] in (new_copy, second["document"])
    assert len({c["number"]: c for c in client.get(f"{world.base}/contributions", headers=world.ann).json()["contributions"]}[first["number"]]["revisions"]) == 1

    # A hidden contribution takes the revision and stays hidden.
    assert client.patch(f"{world.base}/contributions/{first['number']}", json={"action": "hide"}, headers=world.ann).status_code == 200
    _new_version(world, followed, personal.id, "The third version of a page about Korea. " * 3, EXTRACTION)
    third = {c["number"]: c for c in client.get(f"{world.base}/contributions", headers=world.ann).json()["contributions"]}[first["number"]]
    assert third["state"] == "hidden" and third["document"] != new_copy and len(third["revisions"]) == 2
    assert client.get(f"{world.base}/document?id={third['document']}").status_code == 404

    # When the root document is deleted, the copies keep their last version (Section 9.5 rule 5).
    conn = dbm.connect(world.settings.db_file)
    try:
        dbm.purge_document_cascade(conn, data_dir=Path(world.settings.data_dir), target_ids=[followed], tombstone=False, kb_id=personal.id)
    finally:
        conn.close()
    kept = {c["number"]: c for c in client.get(f"{world.base}/contributions", headers=world.ann).json()["contributions"]}[first["number"]]
    assert (kept["state"], kept["document"]) == ("hidden", third["document"])


def test_copy_has_its_own_files(world, tmp_path: Path) -> None:
    """The files of a document are named by its ID, and a purge deletes them by that ID. The copy gets its own files."""
    data_dir = Path(world.settings.data_dir)
    document = _personal_document(world.settings, world.manager, 3, "A page with an image. " * 3, extraction=EXTRACTION)
    (data_dir / "images").mkdir(parents=True, exist_ok=True)
    (data_dir / "images" / f"{document}_0.png").write_bytes(b"png-bytes")
    (data_dir / "raw" / "artifacts").mkdir(parents=True, exist_ok=True)
    (data_dir / "raw" / "artifacts" / f"{document}.txt.zst").write_bytes(b"artifact")
    conn = dbm.connect(world.settings.db_file)
    try:
        dbm.set_document_images(conn, document, [{"url": "https://example.org/a.png", "local": f"images/{document}_0.png"}, {"url": "https://example.org/b.png"}], kb_id=world.manager.personal_kb_for(3).id)
        conn.commit()
    finally:
        conn.close()
    copy_id = _contribute(world, world.ben, document).json()["contribution"]["document"]
    assert (data_dir / "images" / f"{copy_id}_0.png").read_bytes() == b"png-bytes"
    assert (data_dir / "raw" / "artifacts" / f"{copy_id}.txt.zst").read_bytes() == b"artifact"
    conn = _db(world)
    try:
        images = json.loads(conn.execute("SELECT meta FROM documents WHERE id=?", (copy_id,)).fetchone()[0])["images"]
    finally:
        conn.close()
    assert images == [{"url": "https://example.org/a.png", "local": f"images/{copy_id}_0.png"}, {"url": "https://example.org/b.png"}]
    # A purge of the source deletes only the files of the source.
    conn = dbm.connect(world.settings.db_file)
    try:
        dbm.purge_document_cascade(conn, data_dir=data_dir, target_ids=[document], tombstone=False, kb_id=world.manager.personal_kb_for(3).id)
    finally:
        conn.close()
    assert not (data_dir / "images" / f"{document}_0.png").exists() and (data_dir / "images" / f"{copy_id}_0.png").exists()


def test_merge_job_is_taken_once_and_a_stale_job_runs_again(world) -> None:
    document = _personal_document(world.settings, world.manager, 3, "A note for the job queue. " * 3, extraction=EXTRACTION)
    kb_id = world.kb["id"]
    conn = dbm.connect(world.settings.db_file)
    try:
        row = contributions.contribute_direct(conn, kb_id=kb_id, source_kb_id=world.manager.personal_kb_for(3).id, source_document_id=document, contributor_account_id=3, approved=False)
        conn.commit()
        (job,) = contributions.pending_merge_jobs(conn)
        assert contributions.claim_job(conn, job["id"]) is True and contributions.claim_job(conn, job["id"]) is False
        # Another process has the job: this one does nothing.
        from claire.extract.provider import get_provider

        provider = get_provider(world.settings)
        taken = contributions.run_merge_job(conn, provider, world.settings, contribution=row["public_id"], job_id=job["id"])
        assert (taken.state, taken.error) == ("merging", "job_taken")
        assert contributions.pending_merge_jobs(conn) == [] and not contributions.has_pending_merge_jobs(conn)
        # The process of the job stopped: after the limit the job is pending again and runs.
        conn.execute("UPDATE jobs SET updated_at=? WHERE id=?", (time.time() - contributions.STALE_JOB_SECONDS - 1, job["id"]))
        conn.commit()
        assert contributions.has_pending_merge_jobs(conn)
        (result,) = contributions.run_pending_merge_jobs(conn, lambda _kb: provider, lambda _kb: world.settings)
        assert result.state == "visible" and (result.entities, result.relations) == (3, 2)
    finally:
        conn.close()


def test_e2e_refresh_of_the_root_document_revises_its_copies(world, monkeypatch: pytest.MonkeyPatch) -> None:
    """The real path: a new fetch of the source in the personal knowledge base (``IngestService.refresh_document``) changes the content; the pipeline starts the revision and the service runs its merge job."""
    from claire.ingest import service as service_module
    from claire.ingest.service import IngestService
    from claire.ontology.base import Document

    client, kb_id = world.client, world.kb["id"]
    first_text = "The first text of a page that the admin follows. " * 3
    document = _personal_document(world.settings, world.manager, 1, first_text, url="https://example.org/admin-page")
    contribution = _contribute(world, world.admin, document).json()["contribution"]
    assert (contribution["state"], contribution["approved"]) == ("visible", True)  # the admin has each capability

    second_text = "The second text of the page, after a new fetch. " * 3

    def _fetch(payload, *args, **kwargs):
        return Document(url="https://example.org/admin-page", canonical_url="https://example.org/admin-page", title="Admin page", raw_text=second_text, source_type="web", content_hash=content_hash(second_text))

    monkeypatch.setattr(service_module, "default_fetch", _fetch)
    service = IngestService(world.manager.get_settings_for_kb(0, world.settings), kb_id=0)
    result = service.refresh_document(document, "https://example.org/admin-page")
    assert result["status"] == "done", result

    after = client.get(f"{world.base}/contributions", headers=world.ann).json()["contributions"][0]
    assert after["document"] != contribution["document"] and after["state"] == "visible" and [r["state"] for r in after["revisions"]] == ["done"]
    conn = _db(world)
    try:
        assert conn.execute("SELECT raw_text FROM documents WHERE id=? AND kb_id=?", (after["document"], kb_id)).fetchone()[0] == second_text
        assert [tuple(r) for r in conn.execute("SELECT status FROM jobs WHERE source_type='contribution_merge' ORDER BY id")] == [("done",), ("done",)]
    finally:
        conn.close()
    # A refresh without a change makes no revision.
    assert service.refresh_document(document, "https://example.org/admin-page")["status"] == "nochange"
    assert len(client.get(f"{world.base}/contributions", headers=world.ann).json()["contributions"][0]["revisions"]) == 1


def test_copy_document_copies_only_primary_standard_rendition_metadata(world) -> None:
    from claire.extract.rendition_templates import PRE_TEMPLATE_VERSION
    from claire.store import document_renditions as renditions_store

    document = _personal_document(world.settings, world.manager, 3, "Rendition isolation in shared KB. " * 3, extraction=EXTRACTION)
    personal_kb_id = world.manager.personal_kb_for(3).id
    conn = dbm.connect(world.settings.db_file)
    try:
        conn.execute(
            "INSERT INTO document_renditions (document_id, kb_id, template, template_version, detail, detail_html, model_lineage, created_at, updated_at) "
            "VALUES (?, ?, 'standard', ?, NULL, NULL, ?, 100.0, 100.0) "
            "ON CONFLICT(document_id, template) DO UPDATE SET template_version=excluded.template_version, model_lineage=excluded.model_lineage",
            (document, personal_kb_id, PRE_TEMPLATE_VERSION, json.dumps({"provider": "gemini", "model": "gemini-test"})),
        )
        renditions_store.store_additional(conn, document, personal_kb_id, template="concise", detail="= Concise\n\n* Short.", lineage={"provider": "gemini", "model": "gemini-test"})
        renditions_store.store_additional(conn, document, personal_kb_id, template="academic", detail="= Academic\n\nLong.", lineage={"provider": "gemini", "model": "gemini-test"})
        conn.commit()
    finally:
        conn.close()

    made = _contribute(world, world.ben, document)
    assert made.status_code == 201, made.text
    copy_id = made.json()["contribution"]["document"]
    conn = _db(world)
    try:
        copied = renditions_store.list_renditions(conn, copy_id, world.kb["id"])
        assert [(r.template, r.template_version, r.model_lineage) for r in copied] == [
            ("standard", PRE_TEMPLATE_VERSION, {"provider": "gemini", "model": "gemini-test"}),
        ]
    finally:
        conn.close()


def test_copy_remote_document_records_primary_standard_rendition(world) -> None:
    from claire.extract.rendition_templates import PRIMARY_RENDITION_TEMPLATE, rendition_template

    article = {
        "id": "https://remote.example/ap/documents/1",
        "type": "Article",
        "name": "Remote Note",
        "published": "2026-10-01T00:00:00Z",
        "url": "https://remote.example/p/1",
        "claire:extraction": {
            "summary": "Remote summary.",
            "claire:keyClaims": [],
            "claire:entities": [],
            "claire:relations": [],
        },
    }
    conn = dbm.connect(world.settings.db_file)
    try:
        copy_with_body = contributions.copy_remote_document(
            conn,
            article=article,
            sanitized_html="<p>Remote body content.</p>",
            canonical_url=article["url"],
            credit="https://remote.example/ap/actors/alice",
            root=article["id"],
            target_kb_id=world.kb["id"],
            hidden=True,
            now=1800000000.0,
        )
        copy_empty = contributions.copy_remote_document(
            conn,
            article=article,
            sanitized_html="   ",
            canonical_url=None,
            credit="https://remote.example/ap/actors/alice",
            root=article["id"],
            target_kb_id=world.kb["id"],
            hidden=True,
            now=1800000001.0,
        )
        conn.commit()
        rows_with = conn.execute("SELECT template, template_version FROM document_renditions WHERE document_id=? AND kb_id=?", (copy_with_body, world.kb["id"])).fetchall()
        rows_empty = conn.execute("SELECT template FROM document_renditions WHERE document_id=? AND kb_id=?", (copy_empty, world.kb["id"])).fetchall()
        assert [(r[0], r[1]) for r in rows_with] == [(PRIMARY_RENDITION_TEMPLATE, rendition_template(PRIMARY_RENDITION_TEMPLATE).version)]
        assert rows_empty == []
    finally:
        conn.close()
