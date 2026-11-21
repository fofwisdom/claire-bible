"""CT28 / CT-12: the ingest focus is sealed at all entry points.

No entry point gives a focus to the pipeline. A stored focus stays as data, and no render reads it.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from starlette.testclient import TestClient

from claire.api import server
from claire.core.config import Settings
from claire.extract.provider import MockProvider
from claire.store import db as dbm

TOKEN = "owner-test-token-12345678901234567890"


@pytest.fixture()
def seal_env(tmp_path: Path, monkeypatch):
    seen: list[object] = []
    original = MockProvider.render_detail

    def spy(self, doc, format="adoc", focus=None, **kwargs):
        seen.append(focus)
        return original(self, doc, format=format, focus=focus)

    monkeypatch.setattr(MockProvider, "render_detail", spy)
    settings = Settings(
        db_path=str(tmp_path / "test.db"),
        vault_path=str(tmp_path / "vault"),
        environment="development",
        provider="mock",
        vector_backend="mock",
        public_url="http://127.0.0.1:8765",
        inject_token=TOKEN,
        anonymous_readonly=False,
    )
    app = server.create_app(settings)
    with TestClient(app, base_url=settings.public_url) as client:
        yield client, settings, seen


def _auth() -> dict[str, str]:
    return {"Authorization": f"Bearer {TOKEN}"}


def _final(resp) -> dict:
    assert resp.status_code == 200, resp.text
    event = json.loads(resp.text.strip().split("\n")[-1])
    assert event.get("done") is True, event
    return event["result"]


def test_e2e_no_entry_point_gives_a_focus_to_the_pipeline(seal_env):
    client, settings, seen = seal_env

    # Ingest with a focus field and with a separator in the payload.
    resp = client.post(
        "/ingest",
        json={"payload": "Title: Seal one\nBody: first document text. | system architecture", "focus": "API focus"},
        headers=_auth(),
    )
    assert resp.status_code == 200, resp.text
    doc1 = resp.json()["document_id"]
    assert "focus" not in resp.json() or not resp.json()["focus"]

    # Stream ingest with the empty-line separator.
    doc2 = _final(
        client.post(
            "/ingest-stream",
            json={"payload": "Title: Seal two\nBody: second document text.\n\n[focus] for beginners", "focus": "stream focus"},
            headers=_auth(),
        )
    )["document_id"]

    # Regenerate with a focus field.
    resp = client.post("/regenerate", json={"id": doc1, "component": "detail", "focus": "regenerate focus"}, headers=_auth())
    assert resp.status_code == 200, resp.text

    conn = dbm.connect(settings.db_file)
    try:
        for doc_id in (doc1, doc2):
            assert dbm.get_document_focus(conn, doc_id) is None
            detail = dbm.get_document_detail(conn, doc_id) or ""
            assert "[focus:" not in detail
        text = dbm.get_document(conn, doc2).raw_text or ""
        assert "for beginners" not in text
    finally:
        conn.close()

    assert seen, "the pipeline rendered no detail"
    assert all(f is None for f in seen), seen


def test_a_stored_focus_is_not_read_by_regenerate(seal_env):
    client, settings, seen = seal_env
    resp = client.post("/ingest", json={"payload": "Title: Stored\nBody: a document with an old focus."}, headers=_auth())
    assert resp.status_code == 200, resp.text
    doc_id = resp.json()["document_id"]

    conn = dbm.connect(settings.db_file)
    try:
        dbm.set_document_focus(conn, doc_id, "old stored focus")
    finally:
        conn.close()

    seen.clear()
    resp = client.post("/regenerate", json={"id": doc_id, "component": "detail"}, headers=_auth())
    assert resp.status_code == 200, resp.text

    conn = dbm.connect(settings.db_file)
    try:
        # The stored value stays as data.
        assert dbm.get_document_focus(conn, doc_id) == "old stored focus"
        assert "[focus:" not in (dbm.get_document_detail(conn, doc_id) or "")
    finally:
        conn.close()
    assert seen and all(f is None for f in seen), seen


def test_theme_settings_ignore_default_focus(seal_env):
    client, _settings, _seen = seal_env
    resp = client.get("/themes", headers=_auth())
    assert resp.status_code == 200, resp.text
    body = json.dumps(resp.json())
    assert "default_focus" not in body


def test_cli_has_no_focus_option():
    from claire.cli import build_parser

    parser = build_parser()
    for argv in (["ingest", "x"], ["reingest", "x"], ["regenerate", "x"], ["backfill-detail"], ["kb", "define", "--type", "real", "--label", "x"], ["kb", "update", "x"]):
        parser.parse_args(argv)  # the command exists without the option
        with pytest.raises(SystemExit):
            parser.parse_args([*argv, "--focus", "y"])


def test_telegram_parser_keeps_the_url_and_returns_the_text():
    from claire.telegram_bot import parse_message_focus

    payload, rest = parse_message_focus("https://example.com/doc | system architecture")
    assert payload == "https://example.com/doc"
    assert rest == "system architecture"
