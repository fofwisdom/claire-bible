"""Access rules of shared knowledge bases that a review found broken (SHARED_KNOWLEDGE_BASES_AND_THEMES_DESIGN.md Sections 6.2, 6.8, 8.6, 9.4, 9.6, 10.1).

A hidden copy (a pending or hidden contribution) is for reviewers only, on each read path; a reviewer does not approve its own contribution; a document of a shared knowledge base goes only by the removal of its contribution; the public JSON of a knowledge base has no internal field.
"""

from __future__ import annotations

import json

from tests.kb_paths import step_up_request
from tests.test_contributions import EXTRACTION, _contribute, _fresh_token_stores, _personal_document, world  # noqa: F401 (fixtures)

from claire import contributions
from claire.store import db as dbm
from claire.store import kb_collab

ANN, BEN, CHO = 2, 3, 4


def _pending_copy(world, account_id: int = BEN) -> tuple[str, int]:  # noqa: F811
    """A pending contribution of the account in Cities: the document ID of its hidden copy and the contribution number."""
    document = _personal_document(world.settings, world.manager, account_id, "Seoul and Busan are cities of Korea. " * 3, url="https://example.org/cities", extraction=EXTRACTION)
    personal = world.manager.personal_kb_for(account_id).id
    conn = dbm.connect(world.settings.db_file)
    try:
        row = contributions.contribute_direct(conn, kb_id=world.kb["id"], source_kb_id=personal, source_document_id=document, contributor_account_id=account_id, approved=False, pending=True)
        conn.commit()
        return str(row["document_id"]), int(row["public_id"])
    finally:
        conn.close()


def _mcp(world, headers: dict, name: str, arguments: dict) -> dict:  # noqa: F811
    response = world.client.post("/mcp", json={"jsonrpc": "2.0", "id": 1, "method": "tools/call", "params": {"name": name, "arguments": arguments}}, headers=headers)
    assert response.status_code == 200, response.text
    return json.loads(response.json()["result"]["content"][0]["text"])


def test_e2e_hidden_copy_is_for_reviewers_only_on_each_read_path(world) -> None:  # noqa: F811
    client = world.client
    copy_id, _number = _pending_copy(world)
    url = f"{world.base}/document?id={copy_id}"
    # A reviewer reads the pending copy. A member, another account, a readonly token, and an anonymous request do not.
    assert client.get(url, headers=world.ann).status_code == 200
    for headers in (world.ben, world.cho, world.bearer("cho", "readonly"), None):
        assert client.get(url, headers=headers).status_code == 404
    assert client.get(f"{world.base}/document/raw?id={copy_id}", headers=world.bearer("cho", "write")).status_code == 404
    # The MCP read tools follow the same rule; the document list does not count hidden documents.
    number = world.kb["number"]
    assert _mcp(world, world.cho, "document", {"document_id": copy_id, "kb": number}) == {"error": "not found"}
    assert _mcp(world, world.ann, "document", {"document_id": copy_id, "kb": number})["id"] == copy_id
    listed = _mcp(world, world.cho, "documents", {"kb": number})
    assert (listed["documents"], listed["omitted"], listed["truncated"]) == ([], 0, False)
    # A duplicate of a hidden copy gives the code and no URL of the copy.
    again = _personal_document(world.settings, world.manager, CHO, "Seoul and Busan are cities of Korea. " * 3, url="https://example.org/cities", extraction=EXTRACTION)
    assert client.post(f"{world.base}/membership", headers=world.cho).status_code == 201
    duplicate = _contribute(world, world.cho, again)
    assert (duplicate.status_code, duplicate.json()) == (409, {"error": "contribution_duplicate"})


def test_reviewer_does_not_approve_its_own_contribution(world) -> None:  # noqa: F811
    client = world.client
    _copy, number = _pending_copy(world, ANN)  # ann is the moderator of Cities
    url = f"{world.base}/contributions/{kb_collab_number(number)}"
    refused = client.patch(url, json={"action": "approve"}, headers=world.ann)
    assert (refused.status_code, refused.json()) == (403, {"error": "own_contribution"})
    # The admin approves it.
    assert client.patch(url, json={"action": "approve"}, headers=world.admin).json()["contribution"]["state"] == "visible"


def kb_collab_number(public_id: int) -> str:
    from claire.core import ids

    return ids.format_public_id(public_id)


def test_purge_settings_and_moderator_rules_of_a_shared_knowledge_base(world) -> None:  # noqa: F811
    client = world.client
    document = _personal_document(world.settings, world.manager, BEN, "Seoul and Busan are cities of Korea. " * 3, extraction=EXTRACTION)
    made = _contribute(world, world.ben, document).json()["contribution"]
    # The document purge route does not act on a shared knowledge base: the removal of the contribution is the path.
    purge = client.post(f"{world.base}/document/purge", json={"target": made["document"], "tombstone": False, "reason": "x"}, headers=world.ann)
    assert (purge.status_code, purge.json()["error"]) == (403, "kb_remove_by_contribution")
    assert client.get(f"{world.base}/document?id={made['document']}").status_code == 200
    # A moderator does not set the fields that are not in the capability ``settings``.
    assert client.patch(f"{world.base}/", json={"ga_measurement_id": "G-ABCDEFGH12"}, headers=world.ann).status_code == 403
    assert client.patch(f"{world.base}/", json={"description": "Cities of the world"}, headers=world.ann).status_code == 200
    # The member route never ends a moderator role, also not for the admin: the admin route with a recent sign-in does it.
    ann_number = next(m["account"] for m in client.get(f"{world.base}/members").json()["members"] if m["role"] == "moderator")
    removed = client.patch(f"{world.base}/members/{ann_number}", json={"action": "remove"}, headers=world.admin)
    assert (removed.status_code, removed.json()["error"]) == (403, "moderator_protected")
    assert step_up_request(client, world.settings, "DELETE", f"/api/admin/kb/{world.kb['number']}/moderators/{ann_number}").status_code == 200
    # The public JSON of a knowledge base has no internal field; the moderator and the admin get the settings view.
    public = client.get("/kb").json()["knowledge_bases"][0]
    assert not {"db_path", "account_id", "created_by_account_id", "seq", "fqdn", "visibility_epoch", "ga_measurement_id"} & set(public)
    assert "db_path" in client.get("/kb", headers=world.admin).json()["knowledge_bases"][0]


def test_retry_does_not_run_in_an_archived_knowledge_base(world) -> None:  # noqa: F811
    client = world.client
    _copy, number = _pending_copy(world)
    conn = dbm.connect(world.settings.db_file)
    try:
        kb_collab.update_contribution(conn, number, state="failed", decision_reason="merge_failed")
        conn.commit()
    finally:
        conn.close()
    assert client.patch(f"{world.base}/", json={"state": "archived"}, headers=world.ann).status_code == 200
    retry = client.post(f"{world.base}/contributions/{kb_collab_number(number)}/retry", headers=world.ben)
    assert (retry.status_code, retry.json()["error"]) == (403, "kb_archived")
