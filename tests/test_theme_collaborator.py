from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock
import pytest
from starlette.testclient import TestClient

from claire import cli
from claire.api.server import create_app
from claire.config import Settings
from claire.store import db as dbm
from claire.store.theme import ThemeManager


OWNER_TOKEN = "owner-" + ("o" * 32)
COLLAB_TOKEN = "collab-" + ("c" * 32)
READONLY_TOKEN = "readonly-" + ("r" * 32)

OWNER_HEADERS = {"Authorization": f"Bearer {OWNER_TOKEN}"}
COLLAB_HEADERS = {"Authorization": f"Bearer {COLLAB_TOKEN}"}
READONLY_HEADERS = {"Authorization": f"Bearer {READONLY_TOKEN}"}


@pytest.fixture
def collab_test_client(tmp_path: Path):
    data_dir = tmp_path / "data"
    vault_dir = tmp_path / "vault"
    data_dir.mkdir(parents=True)
    vault_dir.mkdir(parents=True)

    db_path = str(data_dir / "claire.db")
    vault_path = str(vault_dir)

    settings = Settings(
        CLAIRE_DB_PATH=db_path,
        CLAIRE_VAULT_PATH=vault_path,
        CLAIRE_PROVIDER="mock",
        CLAIRE_ENVIRONMENT="development",
        CLAIRE_PUBLIC_URL="http://127.0.0.1:8765",
        CLAIRE_INJECT_TOKEN=OWNER_TOKEN,
        CLAIRE_COLLABORATOR_TOKEN=COLLAB_TOKEN,
        CLAIRE_READONLY_TOKEN=READONLY_TOKEN,
        CLAIRE_ANONYMOUS_READONLY=True,
        CLAIRE_MULTI_THEME=True,
    )
    app = create_app(settings=settings)
    client = TestClient(app, base_url=settings.public_url)
    yield client, settings


def test_whoami_scopes(collab_test_client):
    client, _ = collab_test_client

    assert client.get("/whoami", headers=OWNER_HEADERS).json() == {"scope": "owner"}
    assert client.get("/whoami", headers=COLLAB_HEADERS).json() == {"scope": "collaborator"}
    assert client.get("/whoami", headers=READONLY_HEADERS).json() == {"scope": "readonly"}
    assert client.get("/whoami").json() == {"scope": "anonymous"}


def test_collaborator_theme_visibility_and_access(collab_test_client):
    client, settings = collab_test_client

    # 1. Owner creates two themes:
    # Theme 1: private, but collaborator accessible (is_public=False, is_collaborator_accessible=True)
    resp1 = client.post(
        "/themes",
        json={"label": "협업 연구", "icon": "🔬", "is_public": False, "is_collaborator_accessible": True},
        headers=OWNER_HEADERS,
    )
    assert resp1.status_code == 201
    t1_data = resp1.json()["theme"]
    assert t1_data["is_public"] is False
    assert t1_data["is_collaborator_accessible"] is True

    # Theme 2: private, collaborator blocked (is_public=False, is_collaborator_accessible=False)
    resp2 = client.post(
        "/themes",
        json={"label": "비공개 보안", "icon": "🔒", "is_public": False, "is_collaborator_accessible": False},
        headers=OWNER_HEADERS,
    )
    assert resp2.status_code == 201
    t2_data = resp2.json()["theme"]
    assert t2_data["is_public"] is False
    assert t2_data["is_collaborator_accessible"] is False

    # 2. Anonymous /themes -> only Theme 0 (default, public)
    anon_resp = client.get("/themes")
    assert anon_resp.status_code == 200
    anon_ids = [t["id"] for t in anon_resp.json()["themes"]]
    assert 0 in anon_ids
    assert 1 not in anon_ids
    assert 2 not in anon_ids

    # 3. Collaborator /themes -> Theme 0 (public) + Theme 1 (collaborator accessible), NOT Theme 2
    collab_resp = client.get("/themes", headers=COLLAB_HEADERS)
    assert collab_resp.status_code == 200
    collab_themes = collab_resp.json()["themes"]
    collab_ids = [t["id"] for t in collab_themes]
    assert 0 in collab_ids
    assert 1 in collab_ids
    assert 2 not in collab_ids

    # 4. Owner /themes -> all themes (0, 1, 2)
    owner_resp = client.get("/themes", headers=OWNER_HEADERS)
    assert owner_resp.status_code == 200
    owner_ids = [t["id"] for t in owner_resp.json()["themes"]]
    assert 0 in owner_ids
    assert 1 in owner_ids
    assert 2 in owner_ids

    # 5. Accessing theme endpoints by Collaborator:
    # 5-1. Theme 0 -> Allowed (it's public)
    assert client.get("/stats?theme=0", headers=COLLAB_HEADERS).status_code == 200
    # 5-2. Theme 1 -> Allowed (collaborator accessible)
    assert client.get("/stats?theme=1", headers=COLLAB_HEADERS).status_code == 200
    assert client.get("/documents?theme=1", headers=COLLAB_HEADERS).status_code == 200
    assert client.get("/graph?theme=1", headers=COLLAB_HEADERS).status_code == 200
    # 5-3. Theme 2 -> 404 (collaborator blocked)
    assert client.get("/stats?theme=2", headers=COLLAB_HEADERS).status_code == 404
    assert client.get("/documents?theme=2", headers=COLLAB_HEADERS).status_code == 404
    assert client.get("/graph?theme=2", headers=COLLAB_HEADERS).status_code == 404


def test_collaborator_ingestion_rules(collab_test_client):
    client, _ = collab_test_client

    # Create Theme 1 (collaborator accessible) and Theme 2 (collaborator blocked)
    client.post(
        "/themes",
        json={"label": "열린 협업 테마", "is_public": False, "is_collaborator_accessible": True},
        headers=OWNER_HEADERS,
    )
    client.post(
        "/themes",
        json={"label": "닫힌 비밀 테마", "is_public": False, "is_collaborator_accessible": False},
        headers=OWNER_HEADERS,
    )

    # 1. Collaborator cannot ingest into Theme 0 -> 403 Forbidden
    ingest_t0 = client.post(
        "/ingest",
        json={"payload": "Theme 0 적재 시도", "theme": 0},
        headers=COLLAB_HEADERS,
    )
    assert ingest_t0.status_code == 403
    err_msg0 = ingest_t0.json().get("error") or ingest_t0.json().get("detail") or ""
    assert "기본 지식베이스" in err_msg0

    # Ingest without theme parameter (defaults to Theme 0) -> 403 Forbidden
    ingest_default = client.post(
        "/ingest",
        json={"payload": "기본 테마 적재 시도"},
        headers=COLLAB_HEADERS,
    )
    assert ingest_default.status_code == 403

    # 2. Collaborator ingesting into Theme 1 (collaborator accessible) -> 200 OK
    ingest_t1 = client.post(
        "/ingest",
        json={"payload": "https://example.com/collab-doc", "theme": 1},
        headers=COLLAB_HEADERS,
    )
    assert ingest_t1.status_code == 200
    assert ingest_t1.json().get("theme_id") == 1

    # 3. Collaborator ingesting into Theme 2 (collaborator blocked) -> 403 or 404
    ingest_t2 = client.post(
        "/ingest",
        json={"payload": "비밀 테마 적재 시도", "theme": 2},
        headers=COLLAB_HEADERS,
    )
    assert ingest_t2.status_code in (403, 404)

    # 4. Ingest-stream endpoint enforces the exact same rules:
    # 4-1. Theme 0 -> 403
    stream_t0 = client.post(
        "/ingest-stream",
        json={"payload": "스트림 기본 적재 시도", "theme": 0},
        headers=COLLAB_HEADERS,
    )
    assert stream_t0.status_code == 403

    # 4-2. Theme 1 -> 200 streaming
    stream_t1 = client.post(
        "/ingest-stream",
        json={"payload": "https://example.com/collab-stream-doc", "theme": 1},
        headers=COLLAB_HEADERS,
    )
    assert stream_t1.status_code == 200


def test_collaborator_blocked_from_admin_endpoints(collab_test_client):
    client, _ = collab_test_client

    # Define theme by collaborator -> 403/404
    assert client.post("/themes", json={"label": "해킹 테마"}, headers=COLLAB_HEADERS).status_code in (403, 404)
    # Update theme by collaborator -> 403/404
    assert client.patch("/themes", json={"id": 1, "label": "수정 시도"}, headers=COLLAB_HEADERS).status_code in (403, 404)
    # Delete theme by collaborator -> 403/404
    assert client.delete("/themes?id=1", headers=COLLAB_HEADERS).status_code in (403, 404)

    # Admin actions:
    assert client.post("/synthesize", json={"node_ids": ["1", "2"]}, headers=COLLAB_HEADERS).status_code in (403, 404)
    assert client.post("/research", json={"query": "테스트"}, headers=COLLAB_HEADERS).status_code in (403, 404)
    assert client.post("/document/pin", json={"id": "doc1", "pinned": True}, headers=COLLAB_HEADERS).status_code in (403, 404)
    assert client.post("/document/hide", json={"id": "doc1", "hidden": True}, headers=COLLAB_HEADERS).status_code in (403, 404)
    assert client.post("/dedup/scan", headers=COLLAB_HEADERS).status_code in (403, 404)
    assert client.post("/dedup/merge", json={"target_id": "1", "source_ids": ["2"]}, headers=COLLAB_HEADERS).status_code in (403, 404)


def test_theme_update_collaborator_option_and_guard_theme0(collab_test_client):
    client, _ = collab_test_client

    # Create Theme 1 with is_collaborator_accessible: False
    r = client.post(
        "/themes",
        json={"label": "팀 테마", "is_collaborator_accessible": False},
        headers=OWNER_HEADERS,
    )
    assert r.status_code == 201
    assert r.json()["theme"]["is_collaborator_accessible"] is False

    # Update Theme 1 to is_collaborator_accessible: True
    r_patch = client.patch(
        "/themes",
        json={"id": 1, "is_collaborator_accessible": True},
        headers=OWNER_HEADERS,
    )
    assert r_patch.status_code == 200
    assert r_patch.json()["theme"]["is_collaborator_accessible"] is True

    # Attempt to set is_collaborator_accessible: True on Theme 0 -> should remain False
    r_patch0 = client.patch(
        "/themes",
        json={"id": 0, "is_collaborator_accessible": True},
        headers=OWNER_HEADERS,
    )
    assert r_patch0.status_code == 200
    assert r_patch0.json()["theme"]["is_collaborator_accessible"] is False


def test_db_session_scope_collaborator():
    conn = dbm.connect(":memory:")
    dbm.init_db(conn)

    owner_token = dbm.create_session(conn, scope="owner")
    collab_token = dbm.create_session(conn, scope="collaborator")
    ro_token = dbm.create_session(conn, scope="readonly")

    assert dbm.validate_session_scope(conn, owner_token) == "owner"
    assert dbm.validate_session_scope(conn, collab_token) == "collaborator"
    assert dbm.validate_session_scope(conn, ro_token) == "readonly"

    # Owner gate (default scopes=("owner",))
    assert dbm.validate_session(conn, owner_token) is True
    assert dbm.validate_session(conn, collab_token) is False
    assert dbm.validate_session(conn, ro_token) is False

    # Ingest / collaborator gate (scopes=("owner", "collaborator"))
    assert dbm.validate_session(conn, owner_token, scopes=("owner", "collaborator")) is True
    assert dbm.validate_session(conn, collab_token, scopes=("owner", "collaborator")) is True
    assert dbm.validate_session(conn, ro_token, scopes=("owner", "collaborator")) is False

    # Read gate (scopes=("owner", "collaborator", "readonly"))
    assert dbm.validate_session(conn, owner_token, scopes=("owner", "collaborator", "readonly")) is True
    assert dbm.validate_session(conn, collab_token, scopes=("owner", "collaborator", "readonly")) is True
    assert dbm.validate_session(conn, ro_token, scopes=("owner", "collaborator", "readonly")) is True


def test_cli_theme_collaborator_flags(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]):
    data_dir = tmp_path / "data"
    vault_dir = tmp_path / "vault"
    data_dir.mkdir(parents=True)
    vault_dir.mkdir(parents=True)

    settings = Settings(
        CLAIRE_DB_PATH=str(data_dir / "claire.db"),
        CLAIRE_VAULT_PATH=str(vault_dir),
        CLAIRE_PROVIDER="mock",
        CLAIRE_MULTI_THEME=True,
    )
    monkeypatch.setattr("claire.cli.get_settings", lambda: settings)
    tm = ThemeManager(settings)
    monkeypatch.setattr("claire.store.theme.get_theme_manager", lambda s=None: tm)

    # 1. Define theme with --no-collaborator
    ret = cli.main(["theme", "define", "--label", "비공개테마", "--no-collaborator", "--private"])
    assert ret == 0
    captured = capsys.readouterr()
    assert "차단 (Collaborator Blocked)" in captured.out

    # 2. List themes
    ret = cli.main(["theme", "list"])
    assert ret == 0
    captured_list = capsys.readouterr()
    assert "차단" in captured_list.out

    # 3. Update theme with --collaborator
    ret = cli.main(["theme", "update", "1", "--collaborator"])
    assert ret == 0
    captured_up = capsys.readouterr()
    assert "공개 (Collaborator Allowed)" in captured_up.out


@pytest.mark.asyncio
async def test_telegram_webco_command(tmp_path: Path):
    from claire.telegram_bot import build_app

    settings = Settings(
        telegram_bot_token="12345:fake_token_for_test",
        allowed_user_ids=[],
        data_dir=tmp_path,
        CLAIRE_MULTI_THEME=True,
    )
    app = build_app(settings)

    on_webco = next(
        h.callback
        for h in app.handlers[0]
        if getattr(h.callback, "__name__", "") in ("on_webco", "on_webcollab")
    )

    msg = AsyncMock()
    msg.reply_text = AsyncMock()
    update = MagicMock()
    update.effective_user = SimpleNamespace(id=100)
    update.effective_message = msg
    update.message = msg

    ctx = MagicMock()
    ctx.args = []
    await on_webco(update, ctx)

    sent_text = msg.reply_text.call_args[0][0]
    assert "협력자(Collaborator) 웹 링크" in sent_text
    assert "?t=" in sent_text
