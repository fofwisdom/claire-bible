from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest
from starlette.testclient import TestClient

from claire.api.server import create_app
from claire.store import db as dbm

OWNER_TOKEN = "o" * 32
COLLAB_TOKEN = "c" * 32
READONLY_TOKEN = "r" * 32


@pytest.fixture
def syndication_env(tmp_path: Path):
    db_file = tmp_path / "claire.db"
    vault_dir = tmp_path / "vault"
    vault_dir.mkdir(parents=True, exist_ok=True)
    conn = dbm.connect(db_file)
    dbm.init_db(conn)
    conn.close()

    settings = SimpleNamespace(
        db_file=db_file,
        data_dir=tmp_path,
        vault_dir=vault_dir,
        db_path=str(db_file),
        vault_path=str(vault_dir),
        environment="development",
        public_url="http://127.0.0.1:8765/",
        inject_token=OWNER_TOKEN,
        collaborator_token=COLLAB_TOKEN,
        readonly_token=READONLY_TOKEN,
        anonymous_readonly=False,
        cors_allowed_origins="",
        inject_host="127.0.0.1",
        inject_port=8765,
        multi_theme=False,
    )

    class DummyService:
        def __init__(self, s):
            self.s = s
            self.provider = SimpleNamespace()

    app = create_app(settings, service=DummyService(settings))
    return app


def test_syndication_route_public_access_and_io_interface(syndication_env) -> None:
    with TestClient(syndication_env, base_url="http://127.0.0.1:8765") as client:
        res_public = client.get("/syndication")
        assert res_public.status_code == 200
        assert "Claire Bible — Syndication" in res_public.text
        assert "authScope: 'public'" in res_public.text
        assert "id=\"pane-stream\"" in res_public.text
        assert "id=\"headlines-container\"" in res_public.text

        res_head = client.head("/syndication")
        assert res_head.status_code == 200
