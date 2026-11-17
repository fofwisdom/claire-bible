"""Contract fixtures of the internal routes of the knowledge federation (the interface between Claire and the sidecar; units KF1 and KF2).

The directory ``tests/fixtures/knowledge_federation/internal/`` has one JSON file for each call, with the keys ``request`` (``method``, ``path``, and ``body`` for a POST), ``status``, and ``response``, as the files of ``tests/fixtures/federation_contract/``. This test makes the calls on a real internal app and fails when an answer is different from its file. The Deno tests of the sidecar (``sidecar/fedify/tests/knowledge_test.ts``) read the same files: the client of the sidecar must accept each answer and must send each request body.

This server is ``https://a.example``; the other server is ``https://b.example`` of the fixtures of unit KF0. When the contract changes, ``write_fixtures()`` writes the files again.

The test also examines the copy of the context document in the sidecar.
"""

from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path
from typing import Any
from urllib.parse import urlencode

import pytest

from claire.api.federation_internal import INTERNAL_PREFIX
from claire.knowledge_federation import journal, known_servers, vocabulary
from claire.store.secrets import reset_secret_service
from tests.knowledge_federation_servers import FIXTURES, KfServer, fixture, server_actor

INTERNAL_FIXTURES = FIXTURES / "internal"
SIDECAR_CONTEXT = Path(__file__).parent.parent / "sidecar" / "fedify" / "src" / "claire_context_v1.json"
A_KB_NUMBER = 1846000000000000200
B_SERVER = server_actor("https://b.example")


def calls(tmp_path: Path) -> dict[str, dict[str, Any]]:
    """Make each call of the contract on a new server, in the sequence of a real exchange. Return ``{name: {"request", "status", "response"}}``."""
    a = KfServer(tmp_path, "https://a.example", username="alice_admin")
    a.add_shared_kb(A_KB_NUMBER, "Early computing")
    a.db(lambda conn: known_servers.note_server(conn, "b.example", source="admin", added_by=1, now=a.now))
    kb = {key: value for key, value in fixture("objects", "knowledge_base").items() if key != "@context"}
    update = fixture("activities", "update_knowledge_base")
    signed = urlencode({"signer": B_SERVER})
    steps: list[tuple[str, str, str, Any]] = [
        # The reads of a known server (unit KF1): NodeInfo and the server actor.
        ("knowledge_server_reads", "GET", "/knowledge-server-reads?limit=5", None),
        ("knowledge_server_read_error", "POST", "/knowledge-server-reads", {"domain": "b.example", "ok": False, "server_actor": None, "software_name": None, "software_version": None, "shared_kb_count": None, "error": "nodeinfo_unavailable"}),
        ("knowledge_server_read", "POST", "/knowledge-server-reads", {"domain": "b.example", "ok": True, "server_actor": B_SERVER, "software_name": "claire-bible", "software_version": "1.0.0", "shared_kb_count": None, "error": None}),
        ("knowledge_server_read_not_known", "POST", "/knowledge-server-reads", {"domain": "c.example", "ok": False, "server_actor": None, "software_name": None, "software_version": None, "shared_kb_count": None, "error": "nodeinfo_unavailable"}),
        # The read of the ``/kb`` collection of that server (unit KF2).
        ("knowledge_kb_reads", "GET", "/knowledge-kb-reads?limit=5", None),
        ("knowledge_kb_read_batch", "POST", "/knowledge-kb-reads", {"domain": "b.example", "items": [kb], "complete": False, "error": None}),
        ("knowledge_kb_read_complete", "POST", "/knowledge-kb-reads", {"domain": "b.example", "items": [], "complete": True, "error": None}),
        ("knowledge_kb_read_not_due", "POST", "/knowledge-kb-reads", {"domain": "b.example", "items": [], "complete": False, "error": "collection_unavailable"}),
        # The collections of this server for the server actor of B, and for a request without a valid signature.
        ("knowledge_collection_kb", "GET", f"/knowledge-collections/kb?{signed}", None),
        ("knowledge_collection_kb_page", "GET", f"/knowledge-collections/kb?{signed}&page=true", None),
        ("knowledge_collection_themes", "GET", f"/knowledge-collections/themes?{signed}", None),
        ("knowledge_collection_unsigned", "GET", "/knowledge-collections/kb", None),
        # One inbound activity. No handler of the kind is installed, so the row is ``failed`` and stays in the journal.
        ("knowledge_activity", "POST", "/knowledge-activities", {"activity": update, "signer": update["actor"], "verified_by": "http-signature", "recipient": None}),
        ("knowledge_activity_duplicate", "POST", "/knowledge-activities", {"activity": update, "signer": update["actor"], "verified_by": "http-signature", "recipient": None}),
    ]
    out: dict[str, dict[str, Any]] = {}
    for name, method, path, body in steps:
        response = a.internal(method, path, body)
        request: dict[str, Any] = {"method": method, "path": f"{INTERNAL_PREFIX}{path}"}
        if body is not None:
            request["body"] = body
        out[name] = {"request": request, "status": response.status_code, "response": response.json()}
    return out


def write_fixtures() -> None:
    """Write the fixture files from the answers of the internal app. Run it only when the contract changes: ``python -c "import tests.test_knowledge_federation_internal_contract as t; t.write_fixtures()"`` with ``CLAIRE_SECRET_KEY`` set."""
    INTERNAL_FIXTURES.mkdir(parents=True, exist_ok=True)
    journal.HANDLERS.clear()
    with tempfile.TemporaryDirectory() as directory:
        for name, value in calls(Path(directory)).items():
            (INTERNAL_FIXTURES / f"{name}.json").write_text(json.dumps(value, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


@pytest.fixture(autouse=True)
def _isolate(monkeypatch: pytest.MonkeyPatch):
    for key in list(os.environ):
        if key.startswith(("CLAIRE_", "CB_")):
            monkeypatch.delenv(key, raising=False)
    monkeypatch.setenv("CLAIRE_SECRET_KEY", "66" * 32)
    monkeypatch.setattr(journal, "HANDLERS", {})
    reset_secret_service()
    yield
    reset_secret_service()


def test_each_answer_of_the_internal_app_is_its_fixture(tmp_path: Path) -> None:
    made = calls(tmp_path)
    stored = {path.stem: json.loads(path.read_text(encoding="utf-8")) for path in sorted(INTERNAL_FIXTURES.glob("*.json"))}
    assert set(stored) == set(made)
    for name, value in made.items():
        assert json.loads(json.dumps(value)) == stored[name], name
    # The important answers, so that a wrong fixture file is found here too.
    assert stored["knowledge_kb_read_batch"]["response"] == {"domain": "b.example", "stored": 1, "refused": 0, "refused_code": None, "removed": None}
    assert stored["knowledge_kb_read_complete"]["response"]["removed"] == 0
    assert (stored["knowledge_kb_read_not_due"]["status"], stored["knowledge_collection_unsigned"]["status"], stored["knowledge_server_read_not_known"]["status"]) == (404, 404, 404)
    page = stored["knowledge_collection_kb_page"]["response"]["document"]
    assert [vocabulary.validate_object(item, top_level=False) for item in page["orderedItems"]] == ["knowledge_base"]
    assert stored["knowledge_activity"]["response"] == {"decision": "failed", "reason": "no_handler", "journal_id": 1}
    assert stored["knowledge_activity_duplicate"]["response"] == {"decision": "duplicate", "reason": "failed", "journal_id": 1}


def test_the_sidecar_has_a_copy_of_the_context_document() -> None:
    """The sidecar gives the context document to the document loaders of Fedify from this file, so that it never comes from the network (design Section 9.1 rule 2)."""
    assert SIDECAR_CONTEXT.read_text(encoding="utf-8") == vocabulary.context_json()
