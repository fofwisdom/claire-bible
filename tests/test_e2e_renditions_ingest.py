"""Additional renditions of an ingest and 정리본 추가 through the web routes and API v1 (DOCUMENT_RENDITIONS_AND_PUBLICATION_DESIGN.md Section 5.3 rules 1 and 2).

End to end in one app with the mock provider: the request field ``renditions`` is required, each selected template makes one rendition, and the route of API v1 adds one later.
"""

from __future__ import annotations

import pytest
from starlette.testclient import TestClient

from claire.api.server import create_app
from claire.core.config import Settings
from claire.store import db as dbm
from claire.store import document_renditions as renditions_store
from tests.auth_helpers import bearer_headers


@pytest.fixture
def app_env(tmp_path):
    data_dir = tmp_path / "data"
    data_dir.mkdir(parents=True, exist_ok=True)
    (tmp_path / "vault").mkdir(parents=True, exist_ok=True)
    settings = Settings(CLAIRE_DB_PATH=str(data_dir / "claire.db"), CLAIRE_PROVIDER="mock", CLAIRE_ENVIRONMENT="development", CLAIRE_FQDN="127.0.0.1:8765")
    with TestClient(create_app(settings=settings), base_url=settings.public_url) as client:
        yield client, settings


def _renditions(settings: Settings, doc_id: str) -> list[tuple[str, str]]:
    conn = dbm.connect_existing(settings.db_file, readonly=True)
    try:
        return [(r.template, (r.detail or "").split("\n", 1)[0]) for r in renditions_store.list_renditions(conn, doc_id, 0)]
    finally:
        conn.close()


def test_e2e_an_ingest_makes_the_selected_renditions_and_the_route_adds_one_later(app_env) -> None:
    client, settings = app_env
    owner = bearer_headers(settings, scope="owner")
    body = {"payload": "Renditions\nA text about the rendition templates of Claire. " * 4, "extraction_mode": "provider"}
    # The field is required, and it takes only the additional templates, each one time.
    for bad in ({}, {"renditions": ["standard"]}, {"renditions": ["concise", "concise"]}, {"renditions": "concise"}):
        resp = client.post("/ingest", json={**body, **bad}, headers=owner)
        assert (resp.status_code, resp.json().get("error")) == (400, "invalid_renditions"), bad
    resp = client.post("/ingest", json={**body, "renditions": ["concise"]}, headers=owner)
    assert resp.status_code == 200, resp.text
    doc_id = resp.json()["document_id"]
    assert resp.json()["renditions"] == [{"template": "concise", "state": "made", "error": ""}]
    assert [t for t, _ in _renditions(settings, doc_id)] == ["standard", "concise"]
    assert _renditions(settings, doc_id)[1][1].startswith("[mock-detail-adoc] [template: concise]")

    # 정리본 추가 (API v1): a new template is made, a template that the document has is not made again.
    path = f"/api/v1/kbs/personal/documents/{doc_id}/renditions"
    added = client.post(path, json={"template": "academic"}, headers=owner)
    assert added.status_code == 200, added.text
    assert added.json() == {"document_id": doc_id, "rendition": {"template": "academic", "state": "made", "error": ""}}
    assert client.post(path, json={"template": "concise"}, headers=owner).json()["rendition"]["state"] == "existing"
    assert [t for t, _ in _renditions(settings, doc_id)] == ["standard", "concise", "academic"]
    assert client.post(path, json={"template": "standard"}, headers=owner).status_code == 422
    assert client.post("/api/v1/kbs/personal/documents/no-such-document/renditions", json={"template": "academic"}, headers=owner).status_code == 404


def test_e2e_an_agent_client_ingest_queues_the_renditions_in_the_staged_task(app_env) -> None:
    client, settings = app_env
    owner = bearer_headers(settings, scope="owner")
    resp = client.post("/ingest", json={"payload": "Queued\nA text for the agent client queue. " * 4, "extraction_mode": "client", "renditions": ["academic"]}, headers=owner)
    assert resp.status_code == 200, resp.text
    assert resp.json()["renditions"] == [{"template": "academic", "state": "queued", "error": ""}]
    doc_id = resp.json()["document_id"]
    # The document waits for the agent client: 정리본 추가 with the server API waits for the extraction.
    assert client.post(f"/api/v1/kbs/personal/documents/{doc_id}/renditions", json={"template": "concise"}, headers=owner).status_code == 409
