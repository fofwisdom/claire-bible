"""The interfaces that the WebUI of the shared knowledge bases uses (SHARED_KNOWLEDGE_BASES_AND_THEMES_DESIGN.md Section 10.3).

HTTP level: the titles in the views of contributions and submissions, ``membership_state``, the moderation log, the limits of the reputation, the theme match in the document list of the personal knowledge base, ``GET /api/contributions``, and the setting ``submission_max_documents``.
"""

from __future__ import annotations

from tests.test_contributions import EXTRACTION, _contribute, _fresh_token_stores, _personal_document, no_llm, world  # noqa: F401 (fixtures)

from claire.collaboration import contributions
from claire.core import ids
from claire.store import db as dbm
from claire.store import kb_collab
from claire.store import themes as themes_store

ANN, BEN = 2, 3
JSON = {"Accept": "application/json"}


def _title(world, document_id: str) -> str:  # noqa: F811
    conn = dbm.connect_existing(world.settings.db_file, readonly=True)
    try:
        return conn.execute("SELECT title FROM documents WHERE id=?", (document_id,)).fetchone()[0]
    finally:
        conn.close()


def _theme_with(world, *kbs: dict, label: str = "Places") -> str:  # noqa: F811
    conn = dbm.connect(world.settings.db_file)
    try:
        row = themes_store.create_theme(conn, label=label, knowledge_type="real")
        for kb in kbs:
            themes_store.add_theme_member(conn, theme_id=row["id"], kb_id=kb["id"], source="admin", pinned=True)
        conn.commit()
        return ids.format_public_id(themes_store.get_theme(conn, row["id"])["number"])
    finally:
        conn.close()


def test_e2e_titles_of_contributions_and_submissions_and_the_contributions_of_the_account(world, no_llm) -> None:  # noqa: F811
    client = world.client
    direct = _personal_document(world.settings, world.manager, BEN, "Seoul and Busan are cities of Korea. " * 3, extraction=EXTRACTION)
    conn = dbm.connect(world.settings.db_file)
    try:
        conn.execute("UPDATE documents SET title='Cities of Korea' WHERE id=?", (direct,))
        conn.commit()
    finally:
        conn.close()
    made = _contribute(world, world.ben, direct)
    assert made.status_code == 201, made.text
    assert made.json()["contribution"]["title"] == "Cities of Korea"
    # Each reader sees the title of the copy.
    assert [c["title"] for c in client.get(f"{world.base}/contributions").json()["contributions"]] == ["Cities of Korea"]

    # A submission to a theme: the item has the title of the source document. The contribution is pending.
    theme = _theme_with(world, world.kb)
    pending = _personal_document(world.settings, world.manager, BEN, "Daegu is a city of Korea too. " * 3, extraction=EXTRACTION)
    submission = client.post("/submissions", json={"documents": [pending], "theme": theme}, headers=world.ben)
    assert submission.status_code == 201, submission.text
    assert [(i["document"], i["title"]) for i in submission.json()["submission"]["items"]] == [(pending, _title(world, pending))]
    assert [i["title"] for i in client.get("/submissions", headers=world.ben).json()["submissions"][0]["items"]] == [_title(world, pending)]

    # ``GET /api/contributions``: each contribution with the credit of the requester, the newest first, with the knowledge base and the title.
    mine = client.get("/api/contributions", headers=world.ben).json()["contributions"]
    assert [(c["origin"], c["state"], c["label"], c["knowledge_base"], c["title"]) for c in mine] == [
        ("bulk", "pending", "Cities", world.kb["number"], _title(world, pending)),
        ("direct", "visible", "Cities", world.kb["number"], "Cities of Korea"),
    ]
    assert "reason" in mine[0] and "revisions" in mine[0]
    assert [c["origin"] for c in client.get("/api/contributions?origin=direct", headers=world.ben).json()["contributions"]] == ["direct"]
    assert client.get("/api/contributions?origin=auto", headers=world.ben).json()["contributions"] == []
    assert client.get("/api/contributions", headers=world.ann).json()["contributions"] == []
    bad = client.get("/api/contributions?origin=other", headers=world.ben)
    assert (bad.status_code, bad.json()) == (400, {"error": "origin_invalid"})
    assert client.get("/api/contributions").status_code in (401, 404)
    # The old path is closed.
    assert client.get("/api/auto-contributions", headers=world.ben).status_code == 404

    # A rejected contribution has no copy: a full view (the contributor, a reviewer) gets the title of the source document.
    number = mine[0]["number"]
    assert client.patch(f"{world.base}/contributions/{number}", json={"action": "reject", "reason": "low_quality"}, headers=world.ann).status_code == 200
    rejected = client.get("/api/contributions?origin=bulk", headers=world.ben).json()["contributions"]
    assert [(c["state"], c["document"], c["title"]) for c in rejected] == [("rejected", None, _title(world, pending))]


def test_membership_state_in_the_collection_and_in_the_knowledge_base(world) -> None:  # noqa: F811
    client = world.client
    closed = client.post("/kb", json={"label": "Towns", "knowledge_type": "real"}, headers=world.ann).json()["knowledge_base"]
    assert client.patch(f"/kb/{closed['number']}/", json={"join_policy": "approval"}, headers=world.ann).status_code == 200
    asked = client.post(f"/kb/{closed['number']}/membership", headers=world.ben)
    assert asked.status_code in (201, 202), asked.text

    def states(headers: dict | None) -> dict[str, tuple[str | None, str | None]]:
        return {kb["label"]: (kb["membership_state"], kb.get("role")) for kb in client.get("/kb", headers=headers).json()["knowledge_bases"]}

    assert states(world.ben) == {"Cities": ("active", "member"), "Towns": ("pending", None)}
    assert states(world.ann) == {"Cities": ("active", "moderator"), "Towns": ("active", "moderator")}
    assert states(world.cho) == {"Cities": (None, None), "Towns": (None, None)}
    assert states(None) == {"Cities": (None, None), "Towns": (None, None)}

    def one(number: str, headers: dict) -> dict:
        response = client.get(f"/kb/{number}/", headers={**headers, **JSON})
        assert response.status_code == 200, response.text
        return response.json()["knowledge_base"]

    assert one(closed["number"], world.ben)["membership_state"] == "pending" and "role" not in one(closed["number"], world.ben)
    assert (one(world.kb["number"], world.ben)["membership_state"], one(world.kb["number"], world.ben)["role"]) == ("active", "member")
    assert one(world.kb["number"], {})["membership_state"] is None

    # A ban shows as ``banned``.
    ben_number = ids.format_public_id((1 << 57) + BEN)
    assert client.patch(f"{world.base}/members/{ben_number}", json={"action": "ban"}, headers=world.ann).status_code == 200
    assert states(world.ben)["Cities"] == ("banned", None)


def test_moderation_log_is_for_the_people_capability_and_is_newest_first(world) -> None:  # noqa: F811
    client = world.client
    ben_number = ids.format_public_id((1 << 57) + BEN)
    assert client.patch(f"{world.base}/members/{ben_number}", json={"action": "ban", "reason": "spam links"}, headers=world.ann).status_code == 200
    conn = dbm.connect(world.settings.db_file)
    try:
        kb_collab.log_moderation(conn, kb_id=world.kb["id"], actor_account_id=None, action="auto_action", target="x")
        conn.commit()
        stored = kb_collab.list_moderation_log(conn, world.kb["id"])
    finally:
        conn.close()

    response = client.get(f"{world.base}/moderation-log", headers=world.ann)
    assert response.status_code == 200, response.text
    body = response.json()
    log = body["log"]
    assert body["totalItems"] == len(log) == len(stored)
    assert [entry["action"] for entry in log] == [row["action"] for row in reversed(stored)]
    assert {key for entry in log for key in entry} == {"number", "action", "actor", "target", "reason", "created_at"}
    # The newest entry is the automatic action of the server: no actor.
    assert (log[0]["action"], log[0]["actor"], log[0]["target"]) == ("auto_action", None, "x")
    ban = next(entry for entry in log if entry["target"] == ben_number and entry["reason"] == "spam links")
    assert ban["actor"] == {"account": ids.format_public_id((1 << 57) + ANN), "display_name": "Ann"}
    # The admin has the capability ``people``. A member, another account, an anonymous requester, and a token of the scope ``readonly`` do not get the log.
    assert client.get(f"{world.base}/moderation-log", headers=world.admin).status_code == 200
    for headers in (world.ben, world.cho, None, world.bearer("ann", "readonly")):
        assert client.get(f"{world.base}/moderation-log", headers=headers).status_code in (401, 403, 404)
    # A personal knowledge base has no moderation log.
    assert client.get("/@ann/moderation-log", headers=world.ann).status_code == 404


def test_reputation_gives_the_limits_of_the_level(world) -> None:  # noqa: F811
    client = world.client
    mine = client.get("/api/reputation", headers=world.ben).json()
    conn = dbm.connect_existing(world.settings.db_file, readonly=True)
    try:
        from claire.collaboration import reputation

        settings = reputation.read_settings(conn)
    finally:
        conn.close()
    assert mine["level"] == "good"
    assert mine["limits"] == {"pending": int(settings["pending_limit_good"]), "auto_documents": None}


def test_document_list_has_no_contribution_fields(world) -> None:  # noqa: F811
    """The workspace of a personal knowledge base has no contribution functions (owner decision 2026-11-19): the theme match and the flag "never contribute" are in /settings/kb, not in the document list."""
    client = world.client
    matched = _personal_document(world.settings, world.manager, BEN, "Seoul and Busan are cities of Korea. " * 3)
    plain = _personal_document(world.settings, world.manager, BEN, "A note about a quiet river. " * 3)
    theme = _theme_with(world, world.kb)
    personal = world.manager.personal_kb_for(BEN)
    conn = dbm.connect(world.settings.db_file)
    try:
        theme_id = themes_store.get_theme_by_number(conn, ids.parse_public_id(theme))["id"]
        themes_store.save_document_match(conn, document_id=matched, kb_id=personal.id, theme_id=theme_id, knowledge_type="real", score=0.75)
        conn.commit()
    finally:
        conn.close()
    assert client.put(f"/api/contribution-blocks/{plain}", headers=world.ben).status_code == 200

    for path, headers in (("/@ben/documents", world.ben), ("/@ben/documents", world.ann), ("/@ben/documents", None), (f"{world.base}/documents", world.ben), (f"{world.base}/documents", world.ann)):
        response = client.get(path, headers=headers)
        assert response.status_code == 200, (path, response.text)
        for document in response.json()["documents"]:
            assert "theme_match" not in document and "never_contribute" not in document, path


def test_admin_sets_the_limit_of_the_documents_of_one_submission(world) -> None:  # noqa: F811
    client = world.client
    assert client.get("/api/admin/themes", headers=world.admin).json()["settings"]["submission_max_documents"] == contributions.LIMITS["submission_max_documents"]
    saved = client.put("/api/admin/themes/settings", json={"submission_max_documents": 5, "theme_match_threshold": 0.3}, headers=world.admin)
    assert saved.status_code == 200, saved.text
    assert (saved.json()["settings"]["submission_max_documents"], saved.json()["settings"]["theme_match_threshold"]) == (5, 0.3)
    assert client.get("/api/admin/themes", headers=world.admin).json()["settings"]["submission_max_documents"] == 5
    conn = dbm.connect_existing(world.settings.db_file, readonly=True)
    try:
        assert contributions.read_limits(conn) == {**contributions.LIMITS, "submission_max_documents": 5}
    finally:
        conn.close()
    for value in (0, -1, 2.5, "5", True, None):
        bad = client.put("/api/admin/themes/settings", json={"submission_max_documents": value}, headers=world.admin)
        assert (bad.status_code, bad.json()) == (400, {"error": "settings_invalid"}), value
    assert client.put("/api/admin/themes/settings", json={"submission_max_documents": 5}, headers=world.ann).status_code in (403, 404)


def test_syndication_page_has_the_shared_knowledge_base_dialogs(world) -> None:  # noqa: F811
    """The page ``/syndication`` loads ``shared_ui.js``, ``kb_dialogs.js`` and ``shared_ui.css``, as the workspace page does."""
    from bible import STATIC_DIR as _STATIC_DIR

    page = world.client.get("/syndication", headers={**world.ben, "Accept": "text/html"})
    assert page.status_code == 200
    for name in ("js/shared_ui.js", "js/kb_dialogs.js", "css/shared_ui.css"):
        source = (_STATIC_DIR / name).read_text(encoding="utf-8")
        marker = next(line for line in source.splitlines() if len(line.strip()) > 40 and "${t:" not in line and "__" not in line)
        assert marker in page.text, name
