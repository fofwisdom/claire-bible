"""/stats 와 /ingest/active 는 요청자가 접근할 수 없는 테마의 작업을 노출하지 않는다.

단일 DB(v14) 이후 작업 디렉터리는 모든 테마가 공유하므로 응답에서 테마 접근 권한으로 걸러야 한다.
"""

from __future__ import annotations

import secrets
from pathlib import Path
from unittest.mock import MagicMock

from starlette.testclient import TestClient

from claire import ids
from claire.api import server
from claire.config import Settings
from claire.ingest.active import clear_active_ingest, set_active_ingest
from claire.store import db as dbm
from claire.store.knowledge_base import KnowledgeBaseManager


def _client(tmp_path: Path):
    data_dir = tmp_path / "data"
    data_dir.mkdir(parents=True, exist_ok=True)
    db_file = data_dir / "claire.db"
    conn = dbm.connect(db_file)
    dbm.init_db(conn)
    conn.close()

    owner_tok = secrets.token_urlsafe(32)
    s = Settings(
        db_path=str(db_file),
        environment="development",
        public_url="http://127.0.0.1:8765",
        inject_token=owner_tok,
        multi_theme=True,
    )
    tm = KnowledgeBaseManager(s)
    public = tm.define_kb("공개 테마", knowledge_type="real")
    private = tm.define_kb("비공개 테마", is_public=False, knowledge_type="real")
    app = server.create_app(s, MagicMock())
    client = TestClient(app, base_url="http://127.0.0.1:8765")
    return client, data_dir, public, private, {"Authorization": f"Bearer {owner_tok}"}


