"""Entity language of an account: ``accounts.entity_language`` (schema v26), ``PATCH /api/me`` and the label selection in ``GET /graph`` and ``GET /node``.

All state is in ``tmp_path``.
"""

from __future__ import annotations

import time
from dataclasses import dataclass
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
from starlette.testclient import TestClient

from claire.api import server
from claire.ontology.base import Entity
from claire.ontology.label_language import display_names
from claire.store import accounts as accounts_store
from claire.store import db as dbm

OWNER_TOKEN = "owner-" + "o" * 32
READONLY_TOKEN = "readonly-" + "r" * 32
PUBLIC_URL = "http://127.0.0.1:8765"


@dataclass
class _Settings:
    db_file: Path
    data_dir: Path
    environment: str = "development"
    public_url: str = PUBLIC_URL
    inject_token: str = OWNER_TOKEN
    readonly_token: str = READONLY_TOKEN
    cors_allowed_origins: str = ""
    anonymous_readonly: bool = False
    ui_lang: str = ""


@pytest.fixture
def settings(tmp_path: Path) -> _Settings:
    return _Settings(db_file=tmp_path / "claire.db", data_dir=tmp_path)


@pytest.fixture
def client(settings: _Settings):
    app = server.create_app(settings, SimpleNamespace(provider=SimpleNamespace(name="stub")))
    with TestClient(app, base_url=PUBLIC_URL) as tc:
        yield tc


def _request(client: TestClient, method: str, path: str, *, session: str | None = None, **kwargs: Any):
    headers = {"Origin": PUBLIC_URL} if method not in {"GET", "HEAD"} else {}
    if session is not None:
        headers["Cookie"] = f"claire_session={session}"
    client.cookies.clear()
    response = client.request(method, path, headers=headers, follow_redirects=False, **kwargs)
    client.cookies.clear()
    return response


def _session(settings: _Settings) -> str:
    conn = dbm.connect_existing(settings.db_file)
    try:
        accounts_store.AccountStore(conn).complete_setup(1, username="kim_lee", display_name="Admin")
        return accounts_store.create_account_session(conn, account_id=1, scope="owner", now=time.time())
    finally:
        conn.close()


def test_a_v25_database_gets_the_column_and_the_store_rejects_an_unknown_code(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    path = tmp_path / "claire.db"
    with monkeypatch.context() as m:
        m.setattr(dbm, "SCHEMA_VERSION", 25)
        m.setattr(dbm, "_migrate_v26_account_entity_language", lambda conn: None)
        conn = dbm.connect(path)
        try:
            dbm.init_db(conn)
            assert "entity_language" not in dbm._column_names(conn, "accounts")
        finally:
            conn.close()

    for _ in range(2):
        conn = dbm.connect(path)
        try:
            dbm.init_db(conn)
            assert dbm.stored_schema_version(conn) == dbm.SCHEMA_VERSION
            store = accounts_store.AccountStore(conn)
            assert store.get(1).entity_language is None
            with pytest.raises(ValueError, match="invalid_entity_language"):
                store.update_entity_language(1, "fr")
            assert store.get(1).entity_language is None
        finally:
            conn.close()


@pytest.mark.parametrize(
    ("language", "expected"),
    [
        (None, ("개척자", ["Trailblazer", "開拓者"])),
        ("ko", ("개척자", ["Trailblazer", "開拓者"])),
        ("en", ("Trailblazer", ["개척자", "開拓者"])),
        ("ja", ("開拓者", ["개척자", "Trailblazer"])),
    ],
)
def test_display_names_selects_by_script_and_keeps_the_stored_label_first(language: str | None, expected: tuple[str, list[str]]) -> None:
    assert display_names("개척자", ["Trailblazer", "開拓者"], language) == expected


def test_e2e_the_viewer_sees_labels_in_the_entity_language_and_the_stored_labels_do_not_change(client: TestClient, settings: _Settings) -> None:
    session = _session(settings)
    conn = dbm.connect_existing(settings.db_file)
    try:
        hero = Entity(type="Person", name="개척자", aliases=["Trailblazer"])
        tool = Entity(type="Tool", name="한글전용")
        dbm.upsert_entity(conn, hero, kb_id=0)
        dbm.upsert_entity(conn, tool, kb_id=0)
        conn.commit()
    finally:
        conn.close()

    def labels(**kwargs: Any) -> dict[str, str]:
        r = _request(client, "GET", "/graph", **kwargs)
        assert r.status_code == 200, r.text
        return {n["id"]: n["label"] for n in r.json()["nodes"]}

    assert labels(session=session) == {hero.id: "개척자", tool.id: "한글전용"}

    assert _request(client, "PATCH", "/api/me", session=session, json={"entity_language": "fr"}).status_code == 400
    r = _request(client, "PATCH", "/api/me", session=session, json={"entity_language": "en"})
    assert r.status_code == 200 and r.json()["entity_language"] == "en"

    # The account sees the English alias. An entity without an English name keeps its stored label.
    assert labels(session=session) == {hero.id: "Trailblazer", tool.id: "한글전용"}
    node = _request(client, "GET", f"/node?id={hero.id}", session=session).json()
    assert node["name"] == "Trailblazer" and node["aliases"] == ["개척자"]

    # The stored main label did not change: a viewer with a different entity language gets its label from the same data.
    conn = dbm.connect_existing(settings.db_file)
    try:
        assert dbm.get_entity(conn, hero.id, kb_id=0).name == "개척자"
    finally:
        conn.close()

    page = _request(client, "GET", "/settings/account", session=session)
    assert '<option value="en" selected>English (Latin script)</option>' in page.text and "data-settings-entity-language" in page.text

    r = _request(client, "PATCH", "/api/me", session=session, json={"entity_language": None})
    assert r.status_code == 200 and r.json()["entity_language"] is None
    assert labels(session=session)[hero.id] == "개척자"
