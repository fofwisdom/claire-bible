"""Contribution to a theme (SHARED_KNOWLEDGE_BASES_AND_THEMES_DESIGN.md Sections 9.2 and 9.3; unit CT-6).

Completion criteria: a document without a selected theme goes to the shared knowledge bases of its matched theme; no match gives ``no_theme``; pending copies are not visible; each reviewer decides alone and sees the other decisions; the contributions of reviewers need no approval; the limits apply.
"""

from __future__ import annotations

import time
from types import SimpleNamespace

import pytest

from tests.test_contributions import EXTRACTION, _fresh_token_stores, _graph, _personal_document, no_llm, world  # noqa: F401 (fixtures)

from claire import contributions, theme_judgement
from claire.store import db as dbm
from claire.store import themes as themes_store

BEN = 3


@pytest.fixture
def theme(world):  # noqa: F811
    """cho founds ``Towns``; the theme ``Places`` holds ``Cities`` (of ann) and ``Towns``. ben is a member of ``Cities`` only."""
    towns = world.client.post("/kb", json={"label": "Towns", "knowledge_type": "real"}, headers=world.cho).json()["knowledge_base"]
    conn = dbm.connect(world.settings.db_file)
    try:
        row = themes_store.create_theme(conn, label="Places", knowledge_type="real")
        for kb in (world.kb, towns):
            themes_store.add_theme_member(conn, theme_id=row["id"], kb_id=kb["id"], source="admin", pinned=True)
        conn.commit()
        row = themes_store.get_theme(conn, row["id"])
    finally:
        conn.close()
    from claire import ids

    return SimpleNamespace(id=row["id"], number=ids.format_public_id(row["number"]), towns=towns, towns_base=f"/kb/{towns['number']}")


def _document(world, account_id: int, text: str) -> str:  # noqa: F811
    return _personal_document(world.settings, world.manager, account_id, text * 3, extraction=EXTRACTION)


def _states(submission: dict) -> list[dict[str, str]]:
    return [{t["label"]: t["state"] for t in item["targets"]} for item in submission["items"]]


def _setting(world, key: str, value: int) -> None:  # noqa: F811
    conn = dbm.connect(world.settings.db_file)
    try:
        conn.execute("INSERT INTO server_settings (key, value, updated_at, updated_by) VALUES (?, ?, ?, 1) ON CONFLICT(key) DO UPDATE SET value=excluded.value", (key, str(value), time.time()))
        conn.commit()
    finally:
        conn.close()


def test_e2e_submission_to_a_theme_is_pending_and_each_reviewer_decides_alone(world, theme, no_llm) -> None:  # noqa: F811
    client = world.client
    document = _document(world, BEN, "Seoul and Busan are cities of Korea. ")
    made = client.post("/submissions", json={"documents": [document], "theme": theme.number}, headers=world.ben)
    assert made.status_code == 201, made.text
    submission = made.json()["submission"]
    item = submission["items"][0]
    assert (item["document"], item["theme"], item["theme_label"], item["routed_by"]) == (document, theme.number, "Places", "account")
    # A member is not a reviewer: the contribution is pending in each shared knowledge base of the theme.
    assert _states(submission) == [{"Cities": "pending", "Towns": "pending"}]

    # A pending copy is hidden and has no graph. A reader does not see the contribution.
    assert _graph(world, world.kb["id"]) == ({}, []) and _graph(world, theme.towns["id"]) == ({}, [])
    assert client.get(f"{world.base}/contributions").json()["contributions"] == []
    conn = dbm.connect_existing(world.settings.db_file, readonly=True)
    try:
        copies = [tuple(r) for r in conn.execute("SELECT kb_id, hidden FROM documents WHERE kb_id IN (?, ?) ORDER BY kb_id", (world.kb["id"], theme.towns["id"]))]
        assert copies == [(world.kb["id"], 1), (theme.towns["id"], 1)]
        assert conn.execute("SELECT COUNT(*) FROM jobs WHERE source_type='contribution_merge'").fetchone()[0] == 0
        copy_id = conn.execute("SELECT id FROM documents WHERE kb_id=?", (world.kb["id"],)).fetchone()[0]
    finally:
        conn.close()
    assert client.get(f"{world.base}/document?id={copy_id}").status_code == 404

    # The reviewer of Cities sees the help: the decision of Towns, the origin, and the routing. The contributor does not get the help.
    pending = client.get(f"{world.base}/contributions?state=pending", headers=world.ann).json()["contributions"]
    assert len(pending) == 1 and pending[0]["origin"] == "bulk"
    assert pending[0]["review_help"]["routed_by"] == "account"
    assert [(d["label"], d["decision"]) for d in pending[0]["review_help"]["other_decisions"]] == [("Towns", "pending")]
    assert "review_help" not in client.get(f"{world.base}/contributions?state=pending", headers=world.ben).json()["contributions"][0]

    # ann approves in Cities; cho rejects in Towns. One decision does not change the other.
    approved = client.patch(f"{world.base}/contributions/{pending[0]['number']}", json={"action": "approve"}, headers=world.ann)
    assert approved.status_code == 200 and approved.json()["contribution"]["state"] == "visible" and no_llm == []
    assert _graph(world, world.kb["id"])[0].keys() == {"Korea", "Seoul", "Busan"} and _graph(world, theme.towns["id"]) == ({}, [])
    in_towns = client.get(f"{theme.towns_base}/contributions?state=pending", headers=world.cho).json()["contributions"]
    assert [(d["label"], d["decision"]) for d in in_towns[0]["review_help"]["other_decisions"]] == [("Cities", "approved")]
    # ann is not a reviewer of Towns.
    assert client.patch(f"{theme.towns_base}/contributions/{in_towns[0]['number']}", json={"action": "approve"}, headers=world.ann).status_code in (403, 404)
    assert client.patch(f"{theme.towns_base}/contributions/{in_towns[0]['number']}", json={"action": "reject", "reason": "off_subject"}, headers=world.cho).status_code == 200

    # The contributor sees the state of each target. Another account gets 404.
    url = f"/submissions/{submission['number']}"
    mine = client.get(url, headers=world.ben).json()
    assert _states(mine) == [{"Cities": "visible", "Towns": "rejected"}]
    assert [s["number"] for s in client.get("/submissions", headers=world.ben).json()["submissions"]] == [submission["number"]]
    assert client.get(url, headers=world.cho).status_code == 404
    assert client.get("/submissions", headers=world.cho).json()["submissions"] == []
    assert client.get(url).status_code in (401, 404)


def test_contribution_of_a_reviewer_needs_no_approval(world, theme, no_llm) -> None:  # noqa: F811
    document = _document(world, 2, "Seoul and Busan are cities of Korea. ")
    made = world.client.post("/submissions", json={"documents": [document], "theme": theme.number}, headers=world.ann)
    assert made.status_code == 201, made.text
    # ann is the moderator of Cities and no member of Towns.
    assert _states(made.json()["submission"]) == [{"Cities": "visible", "Towns": "pending"}]
    cities = next(t for t in made.json()["submission"]["items"][0]["targets"] if t["label"] == "Cities")
    assert cities["approved"] is True and no_llm == []
    assert _graph(world, world.kb["id"])[0].keys() == {"Korea", "Seoul", "Busan"}


def test_server_routes_a_document_to_its_matched_theme_and_no_match_gives_no_theme(world, theme, monkeypatch: pytest.MonkeyPatch) -> None:  # noqa: F811
    client = world.client
    matched = _document(world, BEN, "Seoul and Busan are cities of Korea. ")
    other = _document(world, BEN, "A note on a subject of no theme. ")
    original = theme_judgement.match_document

    def _match(conn, *, kb_id, document_id, now=None):
        # The match of unit CT-5 has its own tests; here the matched document has the theme, and the other document uses the real match (no signature, thus no theme).
        if document_id == matched:
            return theme_judgement.Match(theme.id, "real", 0.75)
        return original(conn, kb_id=kb_id, document_id=document_id, now=now)

    monkeypatch.setattr(theme_judgement, "match_document", _match)
    made = client.post("/submissions", json={"documents": [matched, other]}, headers=world.ben)
    assert made.status_code == 201, made.text
    first, second = made.json()["submission"]["items"]
    assert (first["theme"], first["routed_by"], first["score"]) == (theme.number, "server", 0.75)
    assert {t["label"]: t["state"] for t in first["targets"]} == {"Cities": "pending", "Towns": "pending"} and "code" not in first
    assert (second["theme"], second["code"], second["targets"]) == (None, "no_theme", [])
    help_ = client.get(f"{world.base}/contributions?state=pending", headers=world.ann).json()["contributions"][0]["review_help"]
    assert (help_["routed_by"], help_["theme_score"]) == ("server", 0.75)


def test_submission_checks_and_limits(world, theme) -> None:  # noqa: F811
    client = world.client
    first = _document(world, BEN, "Seoul and Busan are cities of Korea. ")
    second = _document(world, BEN, "Daegu and Incheon are cities of Korea. ")
    foreign = _document(world, 4, "A document of another account. ")
    post = lambda body, headers=world.ben: client.post("/submissions", json=body, headers=headers)  # noqa: E731
    assert post({"documents": []}).status_code == 400 and post({"documents": "x"}).status_code == 400
    assert post({"documents": [first, first], "theme": theme.number}).json()["error"] == "documents_invalid"
    assert (post({"documents": [foreign], "theme": theme.number}).status_code, post({"documents": [first], "theme": "1"}).json()["error"]) == (404, "theme_not_found")
    assert post({"documents": [first], "theme": theme.number}, world.bearer("ben", "readonly")).status_code == 404  # a scope that is too small gets 404 on a route with no knowledge base capability
    assert client.post("/submissions", json={"documents": [first], "theme": theme.number}).status_code in (401, 403, 404)

    # The limit of the documents of one submission.
    _setting(world, "submission_max_documents", 1)
    too_many = post({"documents": [first, second], "theme": theme.number})
    assert (too_many.status_code, too_many.json()) == (400, {"error": "documents_invalid", "limit": 1})
    # The limit of the pending contributions of one account in one knowledge base: the target is skipped with the reason.
    _setting(world, "pending_limit_good", 1)
    assert _states(post({"documents": [first], "theme": theme.number}).json()["submission"]) == [{"Cities": "pending", "Towns": "pending"}]
    limited = post({"documents": [second], "theme": theme.number}).json()["submission"]
    assert [(t["label"], t["state"], t["reason"]) for t in limited["items"][0]["targets"]] == [("Cities", "skipped", "pending_limit"), ("Towns", "skipped", "pending_limit")]
    # The same document again, below the limit: each target has it already.
    _setting(world, "pending_limit_good", 5)
    again = post({"documents": [first], "theme": theme.number}).json()["submission"]
    assert {t["reason"] for t in again["items"][0]["targets"]} == {"contribution_duplicate"}
    # A banned account has no target in that knowledge base.
    conn = dbm.connect(world.settings.db_file)
    try:
        conn.execute("UPDATE kb_memberships SET state='banned' WHERE kb_id=? AND account_id=?", (world.kb["id"], BEN))
        conn.execute("DELETE FROM kb_contributions")
        conn.commit()
    finally:
        conn.close()
    third = _document(world, BEN, "Jeju is an island of Korea. ")
    assert _states(post({"documents": [third], "theme": theme.number}).json()["submission"]) == [{"Towns": "pending"}]


def test_decision_agreement_needs_three_decisions() -> None:
    conn = dbm.connect(":memory:")
    try:
        conn.execute("CREATE TABLE kb_contributions (submission_item_id INTEGER, kb_id INTEGER, state TEXT, approved_at REAL)")
        rows = [(1, 10, "visible", 1.0), (1, 11, "visible", 1.0), (1, 12, "rejected", None), (2, 10, "visible", 1.0), (2, 11, "rejected", None), (3, 10, "pending", None)]
        conn.executemany("INSERT INTO kb_contributions VALUES (?, ?, ?, ?)", rows)
        # Item 1 has three decisions: 10 and 11 agree with the majority of the others only when the others have a majority (one approve, one reject is no majority); 12 does not agree with the two approvals.
        assert contributions.decision_agreement(conn) == [{"kb_id": 12, "decisions": 1, "agreement": 0.0}]
    finally:
        conn.close()
