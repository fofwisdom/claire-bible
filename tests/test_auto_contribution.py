"""Automatic contribution and mutual collaboration (SHARED_KNOWLEDGE_BASES_AND_THEMES_DESIGN.md Sections 9.9 and 9.10; unit CT-13).

Completion criterion (E2E): the admin turns on ``kb_auto_contribution``; A and B make a mutual collaboration; B turns on its rule with ``mutual`` after the dry run; A makes a shared knowledge base with seed entities; after the run, the matching documents of B are visible in it without approval; a document with "never contribute" and a pasted text do not go; a document that a reviewer rejected does not go again.
"""

from __future__ import annotations

import json
import time

from tests.kb_paths import step_up_request
from tests.test_contributions import _fresh_token_stores, _graph, world  # noqa: F401 (fixtures)

from claire.collaboration import auto_contribution
from claire.core import ids
from claire.ontology.base import normalize_name
from claire.store import db as dbm
from claire.store import kb_collab
from claire.store import themes as themes_store

ANN, BEN, CHO = 2, 3, 4
KOREA = ["Korea", "Seoul", "Busan"]
SPACE = ["Mars", "Jupiter", "Saturn"]
RULE = {"state": "active", "memberships": False, "mutual": True, "include_uploads": False, "themes": []}


def _document(world, account_id: int, name: str, entities: list[str], *, source_type: str = "web", url: bool = True) -> str:  # noqa: F811
    """One document of the personal knowledge base of the account, with its extraction and its entities (the keys of the document match)."""
    kb_id = world.manager.personal_kb_for(account_id).id
    document_id = f"doc_{name}"
    address = f"https://example.org/{name}" if url else None
    extraction = {"summary": name, "key_claims": [], "entities": [{"name": e, "type": "Concept", "aliases": [], "observations": [f"{e} in {name}."]} for e in entities], "relations": []}
    conn = dbm.connect(world.settings.db_file)
    try:
        conn.execute(
            "INSERT INTO documents (id, kb_id, title, raw_text, url, canonical_url, source_type, content_hash, hidden) VALUES (?, ?, ?, ?, ?, ?, ?, ?, 0)",
            (document_id, kb_id, name, f"The text of {name}.", address, address, source_type, f"hash-{name}"),
        )
        conn.execute("INSERT INTO extractions (kb_id, document_id, provider, model, prompt_version, raw_response, created_at) VALUES (?, ?, 'mock', 'mock', 'test', ?, ?)", (kb_id, document_id, json.dumps(extraction), time.time()))
        for entity in entities:
            row = conn.execute("SELECT id, sources FROM entities WHERE kb_id=? AND norm_name=?", (kb_id, normalize_name(entity))).fetchone()
            if row is None:
                conn.execute(
                    "INSERT INTO entities (id, kb_id, type, name, norm_name, aliases, sources, created_at, updated_at) VALUES (?, ?, 'Concept', ?, ?, '[]', ?, 1, 1)",
                    (f"ent_{account_id}_{normalize_name(entity)}", kb_id, entity, normalize_name(entity), json.dumps([document_id])),
                )
            else:
                conn.execute("UPDATE entities SET sources=? WHERE id=?", (json.dumps([*json.loads(row["sources"]), document_id]), row["id"]))
        conn.commit()
    finally:
        conn.close()
    return document_id


def _number(world, account_id: int) -> str:  # noqa: F811
    conn = dbm.connect_existing(world.settings.db_file, readonly=True)
    try:
        return ids.format_public_id(int(conn.execute("SELECT public_id FROM accounts WHERE id=?", (account_id,)).fetchone()[0]))
    finally:
        conn.close()


def _contributions(world, kb_id: int) -> list[tuple]:  # noqa: F811
    conn = dbm.connect_existing(world.settings.db_file, readonly=True)
    try:
        return [(r["source_document_id"], r["origin"], r["state"], r["approved_at"] is not None) for r in kb_collab.list_contributions(conn, kb_id)]
    finally:
        conn.close()


def test_e2e_mutual_collaboration_and_automatic_contribution(world) -> None:  # noqa: F811
    client, s = world.client, world.settings
    ann_number, ben_number = _number(world, ANN), _number(world, BEN)
    match = _document(world, BEN, "match", KOREA)
    blocked = _document(world, BEN, "blocked", KOREA)
    _document(world, BEN, "pasted", KOREA, source_type="text", url=False)
    _document(world, BEN, "upload", KOREA, source_type="pdf", url=False)
    _document(world, BEN, "space", SPACE)

    # The server switch is off at the start. The admin turns it on.
    assert client.get("/api/auto-rule", headers=world.ben).json() == {"server_switch": False, "rule": None}
    assert client.get("/api/admin/auto-contribution", headers=world.ben).status_code == 404
    turned_on = client.put("/api/admin/auto-contribution", json={"state": "on"}, headers=world.admin)
    assert turned_on.status_code == 200 and turned_on.json()["state"] == "on"

    # ann requests a mutual collaboration with ben; ben accepts. Both actions need a recent sign-in: a bearer credential is refused.
    assert client.post("/api/mutual-collaborations", json={"account": ben_number}, headers=world.ann).status_code == 403
    assert step_up_request(client, s, "POST", "/api/mutual-collaborations", account_id=ANN, json={"account": ben_number}).status_code == 201
    assert step_up_request(client, s, "POST", "/api/mutual-collaborations", account_id=ANN, json={"account": ben_number}).json() == {"error": "mutual_exists"}
    assert step_up_request(client, s, "POST", "/api/mutual-collaborations", account_id=ANN, json={"account": ann_number}).status_code == 404
    # The requester cannot accept its own request.
    assert step_up_request(client, s, "PUT", f"/api/mutual-collaborations/{ben_number}", account_id=ANN).status_code == 404
    assert step_up_request(client, s, "PUT", f"/api/mutual-collaborations/{ann_number}", account_id=BEN).status_code == 200
    listed = client.get("/api/mutual-collaborations", headers=world.ben).json()["mutual_collaborations"]
    assert [(m["account"], m["display_name"], m["state"], m["requested_by_me"]) for m in listed] == [(ann_number, "Ann", "active", False)]

    # ben sets the flag "never contribute" on one document.
    assert client.put(f"/api/contribution-blocks/{blocked}", headers=world.ben).json() == {"ok": True, "document": blocked, "blocked": True}
    assert client.put("/api/contribution-blocks/doc_of_nobody", headers=world.ben).status_code == 404
    assert client.get("/api/contribution-blocks", headers=world.ben).json() == {"documents": [blocked]}

    # The dry run: Cities (of ann) has no signature and no seed entities, so no document goes now. Then ben turns the rule on; that needs a recent sign-in.
    dry = client.post("/api/auto-rule/dry-run", json=RULE, headers=world.ben)
    assert dry.status_code == 200 and dry.json() == {"server_switch": True, "remote_state": "on", "targets": []}
    assert client.put("/api/auto-rule", json=RULE, headers=world.ben).status_code == 403
    assert client.put("/api/auto-rule", json={**RULE, "state": "on"}, headers=world.ben).status_code == 400
    saved = step_up_request(client, s, "PUT", "/api/auto-rule", account_id=BEN, json=RULE)
    assert saved.status_code == 200 and (saved.json()["rule"]["state"], saved.json()["rule"]["mutual"], saved.json()["contributions"]) == ("active", True, 0)

    # ann makes a shared knowledge base with seed entities. ben becomes a collaborator in it at once, and the matching document of ben goes there without approval.
    korea = client.post("/kb", json={"label": "Korea", "knowledge_type": "real", "seed_entities": ["Korea", "Seoul"]}, headers=world.ann).json()["knowledge_base"]
    base = f"/kb/{korea['number']}"
    roles = {m["display_name"]: m["role"] for m in client.get(f"{base}/members").json()["members"]}
    assert roles == {"Ann": "moderator", "Ben": "collaborator"}
    assert _contributions(world, korea["id"]) == [(match, "auto", "visible", True)]
    assert set(_graph(world, korea["id"])[0]) == set(KOREA)
    # Each reader sees it. The list of ben has it.
    assert [c["origin"] for c in client.get(f"{base}/contributions").json()["contributions"]] == ["auto"]
    mine = client.get("/api/contributions?origin=auto", headers=world.ben).json()["contributions"]
    assert [(c["label"], c["state"], c["approved"]) for c in mine] == [("Korea", "visible", True)]
    conn = dbm.connect_existing(s.db_file, readonly=True)
    try:
        log = [(r["action"], r["actor_account_id"], r["target"], r["reason"]) for r in kb_collab.list_moderation_log(conn, korea["id"])]
        assert log == [("grant_collaborator", ANN, ben_number, "mutual_collaboration")]
    finally:
        conn.close()

    # A second run sends nothing: the document has a contribution in the knowledge base.
    again = client.put("/api/auto-rule", json=RULE, headers=world.ben)  # the same rule is not wider: no recent sign-in is necessary
    assert again.status_code == 200 and again.json()["contributions"] == 0

    # A theme target where ben is no member: the contribution waits for approval. cho rejects it; the document does not go again.
    peninsula = client.post("/kb", json={"label": "Peninsula", "knowledge_type": "real", "seed_entities": ["Korea", "Busan"]}, headers=world.cho).json()["knowledge_base"]
    conn = dbm.connect(s.db_file)
    try:
        theme = themes_store.create_theme(conn, label="Places", knowledge_type="real")
        themes_store.add_theme_member(conn, theme_id=theme["id"], kb_id=peninsula["id"], source="admin", pinned=True)
        conn.commit()
    finally:
        conn.close()
    wider = {**RULE, "themes": [ids.format_public_id(theme["number"])]}
    plan = client.post("/api/auto-rule/dry-run", json=wider, headers=world.ben).json()["targets"]
    assert [(t["label"], t["mode"], [d["document"] for d in t["documents"]]) for t in plan] == [("Peninsula", "pending", [match])]
    assert client.put("/api/auto-rule", json=wider, headers=world.ben).status_code == 403  # a wider rule needs a recent sign-in
    assert step_up_request(client, s, "PUT", "/api/auto-rule", account_id=BEN, json=wider).json()["contributions"] == 1
    assert _contributions(world, peninsula["id"]) == [(match, "auto", "pending", False)]
    pending = client.get(f"/kb/{peninsula['number']}/contributions?state=pending", headers=world.cho).json()["contributions"]
    assert client.patch(f"/kb/{peninsula['number']}/contributions/{pending[0]['number']}", json={"action": "reject", "reason": "off_subject"}, headers=world.cho).status_code == 200
    assert client.put("/api/auto-rule", json=wider, headers=world.ben).json()["contributions"] == 0
    assert _contributions(world, peninsula["id"]) == [(match, "auto", "rejected", False)]

    # The end of the mutual collaboration does not change the role that it gave. A new knowledge base of ann is not affected.
    assert client.delete(f"/api/mutual-collaborations/{ann_number}", headers=world.ben).status_code == 200
    assert client.get("/api/mutual-collaborations", headers=world.ann).json() == {"mutual_collaborations": []}
    assert {m["display_name"]: m["role"] for m in client.get(f"{base}/members").json()["members"]}["Ben"] == "collaborator"
    later = client.post("/kb", json={"label": "Korea later", "knowledge_type": "real", "seed_entities": ["Korea", "Seoul"]}, headers=world.ann).json()["knowledge_base"]
    assert [m["display_name"] for m in client.get(f"/kb/{later['number']}/members").json()["members"]] == ["Ann"]
    assert _contributions(world, later["id"]) == []


def test_switch_off_stops_each_run_and_uploads_need_the_rule(world) -> None:  # noqa: F811
    client, s = world.client, world.settings
    web = _document(world, BEN, "web", KOREA)
    upload = _document(world, BEN, "upload", KOREA, source_type="pdf", url=False)
    _document(world, BEN, "pasted", KOREA, source_type="text", url=False)
    # ben is a member of Cities (the fixture). Cities gets seed entities.
    assert client.patch(f"{world.base}/", json={"seed_entities": ["Korea", "Seoul"]}, headers=world.ann).status_code == 200
    rule = {"state": "active", "memberships": True, "mutual": False, "include_uploads": False, "themes": []}

    # While the switch is off, the rule is saved and no run starts.
    saved = step_up_request(client, s, "PUT", "/api/auto-rule", account_id=BEN, json=rule)
    assert saved.status_code == 200 and (saved.json()["server_switch"], saved.json()["contributions"]) == (False, 0)
    assert _contributions(world, world.kb["id"]) == []
    # The dry run shows what goes: a member contributes directly; the upload and the pasted text do not go.
    plan = client.post("/api/auto-rule/dry-run", json=rule, headers=world.ben).json()
    assert plan["server_switch"] is False and [(t["label"], t["mode"], [d["document"] for d in t["documents"]]) for t in plan["targets"]] == [("Cities", "direct", [web])]
    # With uploads in the rule the upload is a candidate; a pasted text never goes.
    with_uploads = client.post("/api/auto-rule/dry-run", json={**rule, "include_uploads": True}, headers=world.ben).json()["targets"]
    assert sorted(d["document"] for d in with_uploads[0]["documents"]) == sorted([web, upload])

    # The admin turns the switch on: the rules run. A member contributes directly: visible, not approved.
    assert client.put("/api/admin/auto-contribution", json={"state": "on", "auto_run_documents": 5}, headers=world.admin).json()["settings"]["auto_run_documents"] == 5
    assert _contributions(world, world.kb["id"]) == [(web, "auto", "visible", False)]
    assert client.put("/api/admin/auto-contribution", json={"state": "maybe"}, headers=world.admin).status_code == 400
    assert client.put("/api/admin/auto-contribution", json={"auto_match_threshold": 2}, headers=world.admin).status_code == 400

    # A paused rule sends nothing; the switch off stops a run of an active rule.
    paused = client.put("/api/auto-rule", json={**rule, "state": "paused", "include_uploads": True}, headers=world.ben)
    assert paused.status_code == 200 and paused.json()["contributions"] == 0
    assert client.put("/api/admin/auto-contribution", json={"state": "off"}, headers=world.admin).json()["state"] == "off"
    conn = dbm.connect(s.db_file)
    try:
        assert auto_contribution.run_all(conn, origin=None) == 0 and auto_contribution.run_account(conn, BEN, origin=None) == 0
    finally:
        conn.close()
    assert _contributions(world, world.kb["id"]) == [(web, "auto", "visible", False)]


def test_level_low_pauses_the_rule_and_the_new_document_hook_runs_it(world) -> None:  # noqa: F811
    client, s = world.client, world.settings
    first = _document(world, BEN, "first", KOREA)
    assert client.patch(f"{world.base}/", json={"seed_entities": ["Korea", "Seoul"]}, headers=world.ann).status_code == 200
    assert client.put("/api/admin/auto-contribution", json={"state": "on"}, headers=world.admin).status_code == 200
    rule = {"state": "active", "memberships": True, "mutual": False, "include_uploads": False, "themes": []}

    # The reputation ``low`` pauses the automatic contribution (Section 8.6).
    personal_id = world.manager.personal_kb_for(BEN).id
    conn = dbm.connect(s.db_file)
    try:
        fiction = themes_store.create_theme(conn, label="Novels", knowledge_type="fictional")
        themes_store.save_document_match(conn, document_id=first, kb_id=personal_id, theme_id=fiction["id"], knowledge_type="fictional", score=0.9)
        conn.commit()
    finally:
        conn.close()
    assert step_up_request(client, s, "PUT", "/api/auto-rule", account_id=BEN, json=rule).json()["contributions"] == 0
    second = _document(world, BEN, "second", KOREA)
    conn = dbm.connect(s.db_file)
    try:
        themes_store.delete_document_matches_of_kb(conn, personal_id)
        conn.commit()
        # After the extraction of a new document, the rule runs for that document only (the hook of the ingest pipeline).
        from claire.ingest.pipeline import _auto_contribute_document

        _auto_contribute_document(conn, s, kb_id=personal_id, document_id=second)
        assert [(r["source_document_id"], r["origin"], r["state"]) for r in kb_collab.list_contributions(conn, world.kb["id"])] == [(second, "auto", "merging")]
    finally:
        conn.close()


def _mcp(world, headers: dict, name: str, arguments: dict) -> dict:  # noqa: F811
    response = world.client.post("/mcp", json={"jsonrpc": "2.0", "id": 1, "method": "tools/call", "params": {"name": name, "arguments": arguments}}, headers=headers)
    assert response.status_code == 200, response.text
    return json.loads(response.json()["result"]["content"][0]["text"])


def test_mcp_tools_give_candidates_and_submit_with_the_scope_write(world) -> None:  # noqa: F811
    """Design Section 9.9 rule 7: the AI agent of the account selects with ``kb_candidates`` (read) and contributes with ``kb_submit`` (scope ``write``)."""
    client = world.client
    match = _document(world, BEN, "match", KOREA)
    _document(world, BEN, "space", SPACE)
    assert client.patch(f"{world.base}/", json={"seed_entities": ["Korea", "Seoul"]}, headers=world.ann).status_code == 200
    conn = dbm.connect(world.settings.db_file)
    try:
        theme = themes_store.create_theme(conn, label="Places", knowledge_type="real")
        themes_store.add_theme_member(conn, theme_id=theme["id"], kb_id=world.kb["id"], source="admin", pinned=True)
        conn.commit()
    finally:
        conn.close()
    number = ids.format_public_id(theme["number"])
    readonly, write = world.bearer("ben", "readonly"), world.bearer("ben", "write")

    by_kb = _mcp(world, readonly, "kb_candidates", {"kb": world.kb["number"]})
    assert [(t["label"], [(d["document"], d["score"]) for d in t["documents"]]) for t in by_kb["targets"]] == [("Cities", [(match, 1.0)])]
    assert _mcp(world, readonly, "kb_candidates", {"theme": number}) == by_kb
    assert _mcp(world, readonly, "kb_candidates", {}) == {"error": "kb_or_theme_required"}
    assert _mcp(world, readonly, "kb_candidates", {"theme": "1"}) == {"error": "theme_not_found"}

    # A credential with the scope ``readonly`` does not contribute.
    assert _mcp(world, readonly, "kb_submit", {"documents": [match], "theme": number}) == {"error": "scope_insufficient"}
    made = _mcp(world, write, "kb_submit", {"documents": [match], "theme": number})
    # ben is a member and not a reviewer of Cities: the contribution to a theme waits for approval.
    assert [(i["document"], i["routed_by"], i["targets"]) for i in made["items"]] == [(match, "account", [{"kb": world.kb["number"], "label": "Cities", "state": "pending"}])]
    assert _contributions(world, world.kb["id"]) == [(match, "bulk", "pending", False)]
    assert _mcp(world, write, "kb_submit", {"documents": ["doc_of_nobody"]})["error"] == "document_not_found"
