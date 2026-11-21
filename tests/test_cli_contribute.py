"""The CLI command ``claire contribute`` (SHARED_KNOWLEDGE_BASES_AND_THEMES_DESIGN.md Sections 8.6, 8.7, 9.1, 9.2, 9.9, 13.4).

The CLI works on the database directly, for the admin account. These tests check the interface between the CLI and the contribution modules, and one end-to-end flow: ingest, notice with the command, contribution.
"""

from __future__ import annotations

import json
import shlex

import pytest

from tests.test_auto_contribution import KOREA, _document
from tests.test_contributions import EXTRACTION, _fresh_token_stores, _personal_document, world  # noqa: F401 (fixtures)

from claire import cli
from claire.collaboration import theme_judgement
from claire.core import ids
from claire.store import db as dbm
from claire.store import kb_collab
from claire.store import themes as themes_store

ADMIN = 1


@pytest.fixture
def cli_world(world, monkeypatch: pytest.MonkeyPatch):  # noqa: F811
    monkeypatch.setattr("claire.cli.get_settings", lambda: world.settings)
    monkeypatch.setattr("claire.store.knowledge_base.get_kb_manager", lambda s=None: world.manager)
    return world


def _theme(world, label: str, knowledge_type: str, kb_ids: list[int]) -> str:  # noqa: F811
    """An active theme with the given shared knowledge bases. Return its number."""
    conn = dbm.connect(world.settings.db_file)
    try:
        theme = themes_store.create_theme(conn, label=label, knowledge_type=knowledge_type)
        themes_store.update_theme(conn, theme["id"], state="active")
        for kb_id in kb_ids:
            themes_store.add_theme_member(conn, theme_id=theme["id"], kb_id=kb_id, source="admin", pinned=True)
        conn.commit()
        return ids.format_public_id(theme["number"])
    finally:
        conn.close()


def _json(capsys) -> dict:
    """The data of the JSON output of the last command (the agent contract of the CLI puts the output of a command into ``data``)."""
    output = json.loads(capsys.readouterr().out)
    assert output["status"] == "success", output
    return output["data"]


def test_e2e_ingest_notice_gives_the_command_that_contributes_to_the_theme(cli_world, monkeypatch: pytest.MonkeyPatch, capsys) -> None:
    world = cli_world
    novels = world.client.post("/kb", json={"label": "Novels", "knowledge_type": "fictional"}, headers=world.cho).json()["knowledge_base"]
    number = _theme(world, "Dragon stories", "fictional", [novels["id"]])
    conn = dbm.connect_existing(world.settings.db_file, readonly=True)
    try:
        theme_id = themes_store.get_theme_by_number(conn, int(number))["id"]
    finally:
        conn.close()

    def _match(conn, *, kb_id, document_id, now=None):
        # The match of the document has its own tests: here the new document matches the ``fictional`` theme.
        themes_store.save_document_match(conn, document_id=document_id, kb_id=kb_id, theme_id=theme_id, knowledge_type="fictional", score=0.8)
        return theme_judgement.Match(theme_id, "fictional", 0.8)

    monkeypatch.setattr(theme_judgement, "match_document", _match)
    assert cli.main(["ingest", "A story about a dragon and a knight in an invented world. " * 3, "--no-expand"]) == 0
    out = capsys.readouterr().out
    assert "NOTICE: This document looks like fiction (theme: Dragon stories)." in out
    action = next(line for line in out.splitlines() if line.startswith("ACTION: Contribute to this theme: "))
    command = shlex.split(action.removeprefix("ACTION: Contribute to this theme: "))
    assert command[:3] == ["claire", "contribute", "submit"] and command[-2:] == ["--theme", number]
    document = command[3]

    # The reputation shows the fiction-like document with its theme and the same command.
    assert cli.main(["contribute", "reputation"]) == 0
    shown = capsys.readouterr().out
    assert "Level:                  low" in shown and f"{document}  theme {number} (Dragon stories)" in shown and shlex.join(command) in shown

    # The command of the notice contributes the document. The admin needs no approval, and the merge job runs in the command.
    assert cli.main(command[1:]) == 0
    result = capsys.readouterr().out
    assert f"theme {number} (Dragon stories), selected by the account" in result
    assert f"{novels['number']}  Novels: visible, contribution " in result and ", approved" in result
    assert cli.main(["contribute", "list", "--json"]) == 0
    listed = _json(capsys)["contributions"]
    assert [(c["knowledge_base"], c["source_document"], c["origin"], c["state"], c["approved"]) for c in listed] == [(novels["number"], document, "bulk", "visible", True)]
    # The approved contribution to a ``fictional`` shared knowledge base removes the document from the count.
    assert cli.main(["contribute", "reputation", "--json"]) == 0
    reputation = _json(capsys)
    assert (reputation["level"], reputation["documents"]) == ("good", [])
    # A second submission skips the target with the reason.
    assert cli.main([*command[1:], "--json"]) == 0
    assert [(t["state"], t["reason"]) for t in _json(capsys)["submission"]["items"][0]["targets"]] == [("skipped", "contribution_duplicate")]


def test_submit_without_theme_and_unknown_references(cli_world, capsys) -> None:
    world = cli_world
    document = _personal_document(world.settings, world.manager, ADMIN, "Seoul and Busan are cities of Korea. " * 3, extraction=EXTRACTION)
    # No theme matches the document: the item has ``no_theme`` and no target.
    assert cli.main(["contribute", "submit", document]) == 0
    assert "no_theme: No theme matches this document." in capsys.readouterr().out
    assert cli.main(["contribute", "submit", document, "--json"]) == 0
    item = _json(capsys)["submission"]["items"][0]
    assert (item["document"], item["code"], item["theme"], item["targets"]) == (document, "no_theme", None, [])
    for arguments, message in (
        (["submit", document, "--theme", "1"], "No active theme has the number 1"),
        (["submit", "doc_of_nobody"], "The document is not in the personal knowledge base"),
        (["direct", "1", document], "No shared knowledge base has the number 1"),
        (["direct", world.kb["number"], "doc_of_nobody"], "The document is not in the personal knowledge base"),
        (["withdraw", "1"], "no contribution with the number 1"),
        (["block", "doc_of_nobody"], "no document with the ID doc_of_nobody"),
    ):
        assert cli.main(["contribute", *arguments]) == 1
        assert message in capsys.readouterr().err
    # A command without a sub-command is an error of the syntax.
    with pytest.raises(SystemExit):
        cli.main(["contribute"])


def test_direct_contribution_list_and_withdraw(cli_world, capsys) -> None:
    world = cli_world
    document = _personal_document(world.settings, world.manager, ADMIN, "Seoul and Busan are cities of Korea. " * 3, url="https://example.org/cities", extraction=EXTRACTION)
    assert cli.main(["contribute", "direct", world.kb["number"], document, "--json"]) == 0
    made = _json(capsys)["contribution"]
    assert (made["knowledge_base"], made["label"], made["source_document"], made["origin"], made["state"], made["approved"]) == (world.kb["number"], "Cities", document, "direct", "visible", True)
    assert cli.main(["contribute", "list", "--origin", "direct"]) == 0
    assert f"{made['number']}  visible  direct   approved {world.kb['number']} (Cities)  {document}" in capsys.readouterr().out
    assert cli.main(["contribute", "list", "--origin", "auto"]) == 0
    assert "no contributions" in capsys.readouterr().out
    # The same document does not go again; an approved contribution stays.
    assert cli.main(["contribute", "direct", world.kb["number"], document]) == 1
    assert "has this document already" in capsys.readouterr().err
    assert cli.main(["contribute", "withdraw", made["number"]]) == 1
    assert "The contribution is approved" in capsys.readouterr().err
    # A contribution that is not approved is withdrawn: the row and the copy go.
    conn = dbm.connect(world.settings.db_file)
    try:
        conn.execute("UPDATE kb_contributions SET approved_at=NULL, approved_by_account_id=NULL WHERE public_id=?", (int(made["number"]),))
        conn.commit()
    finally:
        conn.close()
    assert cli.main(["contribute", "withdraw", made["number"]]) == 0
    conn = dbm.connect_existing(world.settings.db_file, readonly=True)
    try:
        assert kb_collab.get_contribution(conn, int(made["number"])) is None
        assert conn.execute("SELECT 1 FROM documents WHERE id=?", (made["document"],)).fetchone() is None
    finally:
        conn.close()


def test_rule_dry_run_apply_and_never_contribute_flag(cli_world, capsys) -> None:
    world = cli_world
    assert world.client.put("/api/admin/auto-contribution", json={"state": "on"}, headers=world.admin).status_code == 200
    korea = world.client.post("/kb", json={"label": "Korea", "knowledge_type": "real", "seed_entities": ["Korea", "Seoul"]}, headers=world.admin).json()["knowledge_base"]
    match, blocked = _document(world, ADMIN, "match", KOREA), _document(world, ADMIN, "blocked", KOREA)
    assert cli.main(["contribute", "block", blocked]) == 0
    assert "never contribute" in capsys.readouterr().out
    assert cli.main(["contribute", "blocks", "--json"]) == 0
    assert _json(capsys)["documents"] == [blocked]
    rule = ["contribute", "rule", "set", "--state", "active", "--memberships", "on", "--mutual", "off", "--uploads", "off", "--no-themes"]
    # Each field of the rule is necessary.
    with pytest.raises(SystemExit):
        cli.main(rule[:-1])
    # The dry run prints the plan and changes nothing.
    assert cli.main(rule) == 0
    dry = capsys.readouterr().out
    assert "Dry run: the rule sends 1 documents to 1 shared knowledge bases now." in dry and f"{korea['number']}  Korea (approved): 1 documents" in dry and match in dry and blocked not in dry
    assert "No change was made." in dry
    assert cli.main(["contribute", "rule", "show", "--json"]) == 0
    assert _json(capsys) == {"server_switch": True, "rule": None}
    # With --apply the rule is set and runs.
    assert cli.main([*rule, "--apply"]) == 0
    assert "The rule is set. The run made 1 contributions." in capsys.readouterr().out
    assert cli.main(["contribute", "rule", "show"]) == 0
    assert "State:       active" in capsys.readouterr().out
    assert cli.main(["contribute", "list", "--origin", "auto", "--json"]) == 0
    assert [(c["knowledge_base"], c["source_document"], c["state"]) for c in _json(capsys)["contributions"]] == [(korea["number"], match, "visible")]
    # Without the flag, the next run sends the other document.
    assert cli.main(["contribute", "unblock", blocked]) == 0
    capsys.readouterr()
    assert cli.main([*rule, "--apply", "--json"]) == 0
    applied = _json(capsys)
    assert (applied["applied"], applied["contributions"], [d["document"] for t in applied["targets"] for d in t["documents"]]) == (True, 1, [blocked])
