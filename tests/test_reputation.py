"""Reputation and campaign message (SHARED_KNOWLEDGE_BASES_AND_THEMES_DESIGN.md Sections 8.6 and 8.7; unit CT-11).

Completion criteria: fiction-like documents raise the fiction share; the level changes the theme attachment, the pending limit, the queue order, and the direct contribution; an approved contribution to a ``fictional`` shared knowledge base removes the document from the count; the notices are in the result of the ingest.
"""

from __future__ import annotations

import time

import pytest

from tests.kb_paths import seed
from tests.test_contributions import EXTRACTION, _contribute, _fresh_token_stores, _personal_document, world  # noqa: F401 (fixtures)

from claire import campaign, contributions, reputation, theme_judgement
from claire.core import ids
from claire.store import db as dbm
from claire.store import kb_collab
from claire.store import themes as themes_store

ANN, BEN, CHO = 2, 3, 4


def _fictional_theme(world, label: str = "Novels") -> dict:  # noqa: F811
    conn = dbm.connect(world.settings.db_file)
    try:
        theme = themes_store.create_theme(conn, label=label, knowledge_type="fictional")
        themes_store.update_theme(conn, theme["id"], state="active")
        conn.commit()
        return theme
    finally:
        conn.close()


def _mark_fiction(world, account_id: int, documents: list[str], theme_id: int) -> None:  # noqa: F811
    """The theme match of the documents is the ``fictional`` theme (the match of unit CT-5 has its own tests)."""
    kb_id = world.manager.personal_kb_for(account_id).id
    conn = dbm.connect(world.settings.db_file)
    try:
        for document in documents:
            themes_store.save_document_match(conn, document_id=document, kb_id=kb_id, theme_id=theme_id, knowledge_type="fictional", score=0.9)
        conn.commit()
    finally:
        conn.close()


def _documents(world, account_id: int, count: int, prefix: str) -> list[str]:  # noqa: F811
    return [_personal_document(world.settings, world.manager, account_id, f"{prefix} note number {i} about a subject. " * 3, extraction=EXTRACTION) for i in range(count)]


def _reputation(world, account_id: int) -> reputation.Reputation:  # noqa: F811
    conn = dbm.connect_existing(world.settings.db_file, readonly=True)
    try:
        return reputation.compute(conn, account_id)
    finally:
        conn.close()


def test_approval_rate_and_level_rules() -> None:
    # Fewer decisions than the minimum: no value.
    assert reputation.approval_rate([(1, True)] * 9, min_decisions=10) == (None, 9)
    # One knowledge base: its own rate.
    assert reputation.approval_rate([(1, True)] * 6 + [(1, False)] * 6, min_decisions=10) == (0.5, 12)
    # The knowledge base 1 has 10 of 12 decisions (all rejected), but it counts for one third (4): (0 * 4 + 1 + 1) / (4 + 1 + 1).
    rate, count = reputation.approval_rate([(1, False)] * 10 + [(2, True), (3, True)], min_decisions=10)
    assert count == 12 and rate == pytest.approx(2 / 6)
    settings = dict(reputation.SETTINGS)
    assert [reputation.level_of(share, rate, settings) for share, rate in ((0.0, None), (0.19, 0.5), (0.2, None), (0.0, 0.49), (0.5, None), (0.0, 0.19))] == ["good", "good", "limited", "limited", "low", "low"]
    assert [reputation.pending_limit(settings, name) for name in reputation.LEVELS] == [100, 20, 5]
    assert [reputation.auto_contribution_limit(settings, name) for name in reputation.LEVELS] == [None, 10, 0]
    assert [reputation.queue_rank(name) for name in ("good", "limited", "low", None)] == [0, 1, 2, 3]
    with pytest.raises(ValueError):
        reputation._checked("pending_limit_low", 0)
    with pytest.raises(ValueError):
        reputation._checked("reputation_fiction_share_low", 1.5)


def test_e2e_fiction_share_sets_the_level_and_its_effects(world) -> None:  # noqa: F811
    client = world.client
    theme = _fictional_theme(world)
    documents = _documents(world, BEN, 4, "Ben")
    assert (_reputation(world, BEN).level, _reputation(world, BEN).fiction_share) == ("good", 0.0)
    # One of four documents is fiction-like: 0.25, thus ``limited``. Two of four: 0.5, thus ``low``.
    _mark_fiction(world, BEN, documents[:1], theme["id"])
    assert (_reputation(world, BEN).level, _reputation(world, BEN).fiction_share) == ("limited", 0.25)
    mine = client.get("/api/reputation", headers=world.ben).json()
    assert (mine["level"], mine["fiction_like_documents"], mine["visible_documents"], mine["approval_rate"]) == ("limited", 1, 4, None)
    assert [(d["document"], d["theme"], d["theme_label"]) for d in mine["documents"]] == [(documents[0], ids.format_public_id(theme["number"]), "Novels")]
    # ``limited``: a member still contributes directly.
    assert _contribute(world, world.ben, documents[3]).json()["contribution"]["state"] == "visible"

    _mark_fiction(world, BEN, documents[1:2], theme["id"])
    assert _reputation(world, BEN).level == "low"
    # ``low``: the direct contribution of a member waits for approval; the copy is hidden.
    waiting = _contribute(world, world.ben, documents[2])
    assert waiting.status_code == 201 and waiting.json()["contribution"]["state"] == "pending"
    assert client.get(f"{world.base}/contributions?state=pending").json()["contributions"] == []
    # The reviewer sees the level of the contributor; the contributor and other readers do not.
    queue = client.get(f"{world.base}/contributions?state=pending", headers=world.ann).json()["contributions"]
    assert [c["contributor_reputation"] for c in queue] == [{"level": "low", "approval_rate": None}]
    assert "contributor_reputation" not in client.get(f"{world.base}/contributions?state=pending", headers=world.ben).json()["contributions"][0]
    # The pending limit of the level ``low``.
    assert client.put("/api/admin/reputations/settings", json={"pending_limit_low": 1}, headers=world.admin).json()["settings"]["pending_limit_low"] == 1
    extra = _documents(world, BEN, 1, "More")[0]
    _mark_fiction(world, BEN, [extra], theme["id"])  # three of five documents: the level stays ``low``
    limited = _contribute(world, world.ben, extra)
    assert (limited.status_code, limited.json()["error"]) == (409, "pending_limit")

    # Only the account and the admin see a reputation.
    assert client.get("/api/reputation").status_code in (401, 404)
    assert client.get("/api/admin/reputations", headers=world.ben).status_code == 404
    listed = {row["display_name"]: row["level"] for row in client.get("/api/admin/reputations", headers=world.admin).json()["accounts"]}
    assert (listed["Ben"], listed["Ann"]) == ("low", "good")
    assert client.put("/api/admin/reputations/settings", json={"pending_limit_low": 0}, headers=world.admin).status_code == 400
    assert client.put("/api/admin/reputations/settings", json={"unknown": 1}, headers=world.admin).status_code == 400


def test_low_level_of_a_reviewer_waits_for_approval_and_the_queue_order(world) -> None:  # noqa: F811
    client = world.client
    theme = _fictional_theme(world)
    # cho becomes a member; ann (the moderator) gets the level ``low``.
    assert client.post(f"{world.base}/membership", headers=world.cho).status_code == 201
    ann_documents = _documents(world, ANN, 2, "Ann")
    _mark_fiction(world, ANN, ann_documents[:1], theme["id"])
    assert _reputation(world, ANN).level == "low"
    first = _contribute(world, world.ann, ann_documents[1]).json()["contribution"]
    assert (first["state"], first["approved"]) == ("pending", False)
    # cho has ``good`` and contributes later; a pending contribution of cho comes first in the queue.
    cho_document = _documents(world, CHO, 1, "Cho")[0]
    personal = world.manager.personal_kb_for(CHO).id
    conn = dbm.connect(world.settings.db_file)
    try:
        contributions.contribute_direct(conn, kb_id=world.kb["id"], source_kb_id=personal, source_document_id=cho_document, contributor_account_id=CHO, approved=False, pending=True, now=time.time() + 60)
        conn.commit()
    finally:
        conn.close()
    queue = client.get(f"{world.base}/contributions?state=pending", headers=world.admin).json()["contributions"]
    assert [(c["contributor"]["display_name"], c["contributor_reputation"]["level"]) for c in queue] == [("Cho", "good"), ("Ann", "low")]
    # The admin has no reputation rule.
    admin_document = _documents(world, 1, 1, "Admin")[0]
    assert _contribute(world, world.admin, admin_document).json()["contribution"]["approved"] is True


def test_contribution_to_a_fictional_kb_removes_the_document_from_the_count(world) -> None:  # noqa: F811
    client = world.client
    theme = _fictional_theme(world)
    documents = _documents(world, BEN, 2, "Story")
    _mark_fiction(world, BEN, documents, theme["id"])
    assert (_reputation(world, BEN).level, _reputation(world, BEN).fiction_share) == ("low", 1.0)
    novels = client.post("/kb", json={"label": "Novels", "knowledge_type": "fictional"}, headers=world.cho).json()["knowledge_base"]
    base = f"/kb/{novels['number']}"
    assert client.patch(f"{base}/", json={"join_policy": "open"}, headers=world.cho).status_code == 200
    assert client.post(f"{base}/membership", headers=world.ben).status_code == 201
    # ``low``: the contributions wait. A pending contribution does not change the count.
    numbers = [client.post(f"{base}/contributions", json={"document": d}, headers=world.ben).json()["contribution"]["number"] for d in documents]
    assert _reputation(world, BEN).fiction_share == 1.0
    # cho approves one: that document does not count; the share is 0.5. After the second approval: 0, thus ``good``.
    assert client.patch(f"{base}/contributions/{numbers[0]}", json={"action": "approve"}, headers=world.cho).status_code == 200
    assert _reputation(world, BEN).fiction_share == 0.5
    assert client.patch(f"{base}/contributions/{numbers[1]}", json={"action": "approve"}, headers=world.cho).status_code == 200
    assert (_reputation(world, BEN).level, _reputation(world, BEN).fiction_like_documents) == ("good", ())


def test_approval_rate_from_decisions(world) -> None:  # noqa: F811
    """Ten decisions of other accounts with counted reason codes give a rate; a rejection with another reason code and the approval of the contributor itself do not count."""
    conn = dbm.connect(world.settings.db_file)
    try:
        now = time.time()
        clock = iter(range(int(now * 1000), int(now * 1000) + 100))  # one public ID for each row

        def _row(state: str, *, reason: str | None = None, approved_by: int | None = None, age_days: float = 1.0) -> None:
            ts = now - age_days * 86400
            number = kb_collab.add_contribution(conn, kb_id=world.kb["id"], document_id=None, origin="direct", state=state, contributor_account_id=BEN, approved_at=ts if approved_by else None, approved_by_account_id=approved_by, now=ts, now_ms=next(clock))
            if state in ("rejected", "removed"):
                kb_collab.update_contribution(conn, number, now=ts, decision_reason=reason, decided_at=ts)
            elif approved_by is not None and approved_by != BEN:
                kb_collab.update_contribution(conn, number, now=ts, decided_at=ts)  # the approval of a reviewer (``contributions.approve``) sets the decision time

        for _ in range(2):
            _row("visible", approved_by=ANN)
        for _ in range(8):
            _row("rejected", reason="spam")
        _row("rejected", reason="duplicate")  # the reason code does not count
        _row("rejected", reason="other")
        _row("visible", approved_by=BEN)  # the contributor approved it itself
        _row("rejected", reason="spam", age_days=200)  # before the window
        conn.commit()
        current = reputation.compute(conn, BEN, now=now)
        assert (current.decisions, current.approval_rate, current.level) == (10, pytest.approx(0.2), "limited")
        _row("rejected", reason="low_quality")
        conn.commit()
        assert reputation.compute(conn, BEN, now=now).level == "low"
    finally:
        conn.close()


def test_low_level_has_no_theme_attachment_and_is_no_curation_source(world) -> None:  # noqa: F811
    client = world.client
    fictional = _fictional_theme(world)
    documents = _documents(world, BEN, 2, "Ben")
    ben_kb = world.manager.personal_kb_for(BEN)
    conn = dbm.connect(world.settings.db_file)
    try:
        places = themes_store.create_theme(conn, label="Places", knowledge_type="real")
        for kb_id in (world.kb["id"], ben_kb.id):
            themes_store.add_theme_member(conn, theme_id=places["id"], kb_id=kb_id, source="computed")
        conn.commit()
    finally:
        conn.close()
    number = ids.format_public_id(places["number"])
    body = {"source": ids.format_public_id(ben_kb.number), "documents": [documents[0]]}
    labels = lambda: [(kb["label"], kb.get("limited")) for kb in client.get(f"/themes/{number}/", headers={"Accept": "application/json"}).json()["knowledge_bases"]]  # noqa: E731
    assert labels() == [("Cities", None), (ben_kb.label, False)]
    # ``limited``: the personal knowledge base stays in the theme with a mark.
    extra = _documents(world, BEN, 2, "Extra")
    _mark_fiction(world, BEN, extra[:1], fictional["id"])
    assert _reputation(world, BEN).level == "limited" and labels() == [("Cities", None), (ben_kb.label, True)]
    # ``low``: the theme page does not list it, and it is no curation source.
    _mark_fiction(world, BEN, extra[1:], fictional["id"])
    assert _reputation(world, BEN).level == "low" and labels() == [("Cities", None)]
    refused = client.post(f"{world.base}/curations", json=body, headers=world.ann)
    assert (refused.status_code, refused.json()) == (409, {"error": "source_not_in_theme"})


def test_e2e_ingest_result_has_the_campaign_notices(world, monkeypatch: pytest.MonkeyPatch) -> None:  # noqa: F811
    theme = _fictional_theme(world)
    original = theme_judgement.match_document

    def _match(conn, *, kb_id, document_id, now=None):
        # The match of unit CT-5 has its own tests: here each new document matches the ``fictional`` theme.
        themes_store.save_document_match(conn, document_id=document_id, kb_id=kb_id, theme_id=theme["id"], knowledge_type="fictional", score=0.8)
        return theme_judgement.Match(theme["id"], "fictional", 0.8)

    monkeypatch.setattr(theme_judgement, "match_document", _match)
    first = seed(world.settings, 0, "A story about a dragon and a knight in an invented world. " * 3)
    assert first.error is None
    number = ids.format_public_id(theme["number"])
    # The first ingest of the day: the notice for the fiction-like document, and the standing message.
    assert [n["code"] for n in first.notices] == [campaign.FICTION_LIKE, campaign.STANDING]
    assert (first.notices[0]["document"], first.notices[0]["theme"], first.notices[0]["theme_label"]) == (first.document_id, number, "Novels")
    lines = first.notice_lines()
    assert "Novels" in lines[0] and "knowledge type Fiction" in lines[1]
    assert "허구" in first.notice_lines(locale="ko")[1]
    # The second ingest of the day: no standing message.
    second = seed(world.settings, 0, "A second story about a wizard and a tower in an invented world. " * 3)
    assert [n["code"] for n in second.notices] == [campaign.FICTION_LIKE]
    monkeypatch.setattr(theme_judgement, "match_document", original)
    # The admin has two fiction-like documents of two: the stored reputation is ``low``.
    conn = dbm.connect_existing(world.settings.db_file, readonly=True)
    try:
        stored = kb_collab.get_reputation(conn, 1)
        assert (stored["level"], stored["fiction_like_documents"], stored["campaign_day"]) == ("low", 2, time.strftime("%Y-%m-%d", time.gmtime()))
    finally:
        conn.close()
