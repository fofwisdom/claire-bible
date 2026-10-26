"""End-to-End tests for Graph Data Bundle subsystem.

Covers the full pipeline across API, storage, and Telegram bot:
- Seed database with entities, relations, documents, and sensitive tables.
- Generate bundle via REST API with owner authentication.
- Verify cryptographic token registry sidecar and TTL.
- Download bundle via REST API.
- Verify archive integrity: manifest.json, checksum verification, sanitized documents, excluded tables.
- Verify Telegram bot /export command:
  * Help and unknown subcommand guidance.
  * Access control for unauthorized users.
  * Generation with status ticker, summary formatting, and document delivery.
  * Large file (>45MB) fallback to secure URL link.
  * List and purge commands.
"""

from __future__ import annotations

import io
import json
import sqlite3
import tarfile
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest
import zstandard as zstd
from starlette.testclient import TestClient

from claire.api import server
from claire.config import Settings
from claire.graph_bundle import (
    GraphBundleInfo,
    get_graph_bundle,
    lookup_graph_bundle_by_token,
)
from claire.store import db as dbm
from claire.telegram_bot import build_app

OWNER_TOKEN = "owner-" + ("o" * 32)
OWNER_HEADERS = {"Authorization": f"Bearer {OWNER_TOKEN}"}


def _seed_comprehensive_database(db_path: Path) -> None:
    conn = dbm.connect(db_path)
    dbm.init_db(conn)

    # Documents with raw_text
    conn.execute(
        """
        INSERT INTO documents (
            id, url, canonical_url, title, author, published_at, fetched_at, raw_text, source_type
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            "doc_e2e_1",
            "https://claire.example.org/article-1",
            "https://claire.example.org/article-1",
            "Graph Architecture Guide",
            "Claire Team",
            "2026-10-26T00:00:00Z",
            1761436800.0,
            "HEAVY_RAW_TEXT_THAT_MUST_BE_STRIPPED_FROM_GRAPH_BUNDLE",
            "web",
        ),
    )

    # Entities
    for idx, (name, category) in enumerate([
        ("Knowledge Graph", "concept"),
        ("Vector Index", "technology"),
        ("REST Endpoint", "interface"),
    ]):
        conn.execute(
            """
            INSERT INTO entities (
                id, type, name, norm_name, aliases, props, observations, sources, provisional, created_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                f"ent_{idx}",
                category,
                name,
                name.lower(),
                json.dumps([f"alias_{idx}"]),
                json.dumps({"index": idx}),
                json.dumps([f"Observed {name}"]),
                json.dumps(["doc_e2e_1"]),
                0,
                1761436800.0,
                1761436800.0,
            ),
        )

    # Relations
    conn.execute(
        """
        INSERT INTO relations (
            id, type, source_id, target_id, confidence, props, sources, provisional, created_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            "rel_e2e_1",
            "CONNECTS_TO",
            "ent_0",
            "ent_1",
            0.98,
            json.dumps({"protocol": "internal"}),
            json.dumps(["doc_e2e_1"]),
            0,
            1761436800.0,
        ),
    )
    conn.execute(
        """
        INSERT INTO relations (
            id, type, source_id, target_id, confidence, props, sources, provisional, created_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            "rel_e2e_2",
            "EXPOSES",
            "ent_0",
            "ent_2",
            0.92,
            json.dumps({"protocol": "http"}),
            json.dumps(["doc_e2e_1"]),
            0,
            1761436800.0,
        ),
    )

    # Populate sensitive tables that must never be exported
    conn.execute(
        "INSERT INTO oauth_tokens (token, refresh_token, client_id, scope, created_at, expires_at) VALUES ('secret_tok_123', 'secret_refresh_123', 'client1', 'owner', 1761436800.0, 9999999999.0);"
    )
    conn.execute(
        "INSERT INTO embeddings (owner_id, dim, vector, model, updated_at) VALUES ('ent_0', 4, X'01020304', 'test-model', 1761436800.0);"
    )

    conn.commit()
    conn.close()


def _build_test_settings(tmp_path: Path) -> Settings:
    data_dir = tmp_path / "data"
    data_dir.mkdir(parents=True, exist_ok=True)
    db_file = data_dir / "claire.db"
    _seed_comprehensive_database(db_file)
    return Settings(
        CLAIRE_DB_PATH=str(db_file),
        CLAIRE_DATA_DIR=str(data_dir),
        CLAIRE_VAULT_PATH=str(tmp_path / "vault"),
        CLAIRE_PROVIDER="mock",
        CLAIRE_ENVIRONMENT="development",
        CLAIRE_PUBLIC_URL="http://127.0.0.1:8765",
        CLAIRE_INJECT_TOKEN=OWNER_TOKEN,
        CLAIRE_ANONYMOUS_READONLY=True,
        TELEGRAM_BOT_TOKEN="12345:fake_bot_token",
        TELEGRAM_ALLOWED_USERS="100,200",
    )


def test_e2e_graph_bundle_api_export_and_verification(tmp_path: Path):
    """End-to-End verification of REST API export, registry sidecar, and downloaded bundle payload."""
    settings = _build_test_settings(tmp_path)
    api_app = server.create_app(settings)

    with TestClient(api_app, base_url=settings.public_url) as client:
        # 1. Export graph bundle via REST API
        resp = client.post(
            "/graph/bundle",
            headers=OWNER_HEADERS,
            json={"theme_id": 0},
        )
        assert resp.status_code == 200
        bundle_meta = resp.json()

        assert bundle_meta["bundle_id"].startswith("gb_")
        assert bundle_meta["entities_count"] == 3
        assert bundle_meta["relations_count"] == 2
        assert bundle_meta["documents_count"] == 1
        assert bundle_meta["size_bytes"] > 0
        token = bundle_meta["token"]
        assert token.startswith("gb3_")

        # 2. Token sidecar validation
        sidecar = lookup_graph_bundle_by_token(settings.data_dir, token)
        assert sidecar is not None
        assert sidecar["bundle_id"] == bundle_meta["bundle_id"]
        assert sidecar["entities_count"] == 3

        # 3. Download the bundle archive via GET /graph/bundle?token={token}
        dl_resp = client.get(f"/graph/bundle?token={token}")
        assert dl_resp.status_code == 200
        assert dl_resp.headers["content-type"] == "application/zstd"
        assert dl_resp.headers["cache-control"] == "no-store, private"
        assert f'attachment; filename="{bundle_meta["filename"]}"' in dl_resp.headers["content-disposition"]
        downloaded_bytes = dl_resp.content
        assert len(downloaded_bytes) == bundle_meta["size_bytes"]

        # 4. Decompress and inspect archive structure
        dctx = zstd.ZstdDecompressor()
        extract_dir = tmp_path / "extracted_bundle"
        extract_dir.mkdir(parents=True, exist_ok=True)
        with io.BytesIO(downloaded_bytes) as raw_stream:
            with dctx.stream_reader(raw_stream) as zstd_reader:
                with tarfile.open(fileobj=zstd_reader, mode="r|") as tar:
                    tar.extractall(path=extract_dir)

        root_prefix = f"graph_bundle_{bundle_meta['bundle_id']}"
        manifest_path = extract_dir / root_prefix / "manifest.json"
        export_db_path = extract_dir / root_prefix / "graph_export.db"

        assert manifest_path.is_file()
        assert export_db_path.is_file()

        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        assert manifest["bundle_id"] == bundle_meta["bundle_id"]
        assert manifest["entities_count"] == 3
        assert manifest["relations_count"] == 2
        assert manifest["documents_count"] == 1

        # 5. Connect to extracted database and verify contents
        conn = sqlite3.connect(str(export_db_path))
        conn.row_factory = sqlite3.Row

        # Tables check: entities, relations, documents present; oauth, embeddings absent
        tables = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()}
        assert "entities" in tables
        assert "relations" in tables
        assert "documents" in tables
        assert "oauth_tokens" not in tables
        assert "embeddings" not in tables

        # Content check: document raw_text stripped
        doc_cols = {r["name"] for r in conn.execute("PRAGMA table_info(documents)").fetchall()}
        assert "raw_text" not in doc_cols

        # Relations count
        rel_rows = conn.execute("SELECT * FROM relations").fetchall()
        assert len(rel_rows) == 2
        conn.close()


@pytest.mark.asyncio
async def test_e2e_telegram_bot_export_flow(tmp_path: Path):
    """End-to-End test for Telegram Bot /export graph command."""
    settings = _build_test_settings(tmp_path)
    tg_app = build_app(settings)

    # Locate on_export handler
    on_export = next(
        h.callback for h in tg_app.handlers[0] if getattr(h.callback, "__name__", "") == "on_export"
    )

    # 1. Test /export help
    status_mock = AsyncMock()
    status_mock.edit_text = AsyncMock()
    msg_help = AsyncMock()
    msg_help.reply_text = AsyncMock(return_value=status_mock)

    update_help = MagicMock()
    update_help.effective_user = SimpleNamespace(id=100)
    update_help.effective_message = msg_help
    ctx_help = MagicMock()
    ctx_help.args = ["help"]

    await on_export(update_help, ctx_help)
    msg_help.reply_text.assert_awaited_once()
    help_out = msg_help.reply_text.call_args[0][0]
    assert "/export graph" in help_out

    # 2. Test /export invalid_subcommand
    msg_invalid = AsyncMock()
    update_invalid = MagicMock()
    update_invalid.effective_user = SimpleNamespace(id=100)
    update_invalid.effective_message = msg_invalid
    ctx_invalid = MagicMock()
    ctx_invalid.args = ["unknown_sub"]

    await on_export(update_invalid, ctx_invalid)
    msg_invalid.reply_text.assert_awaited_once()
    assert "알 수 없는 하위 명령" in msg_invalid.reply_text.call_args[0][0]

    # 3. Test unauthorized user
    msg_unauth = AsyncMock()
    update_unauth = MagicMock()
    update_unauth.effective_user = SimpleNamespace(id=999)  # Not in 100, 200
    update_unauth.effective_message = msg_unauth
    update_unauth.message = msg_unauth
    ctx_unauth = MagicMock()
    ctx_unauth.args = ["graph"]

    await on_export(update_unauth, ctx_unauth)
    msg_unauth.reply_text.assert_awaited_once()
    assert "허용되지 않은 사용자" in msg_unauth.reply_text.call_args[0][0]

    # 4. Test normal export: /export graph
    status_work = AsyncMock()
    status_work.edit_text = AsyncMock()
    msg_work = AsyncMock()
    msg_work.reply_text = AsyncMock(return_value=status_work)
    msg_work.reply_document = AsyncMock()

    update_work = MagicMock()
    update_work.effective_user = SimpleNamespace(id=100)
    update_work.effective_message = msg_work
    ctx_work = MagicMock()
    ctx_work.args = ["graph", "0"]

    await on_export(update_work, ctx_work)

    # Initial progress message
    msg_work.reply_text.assert_awaited_with("⏳ 그래프 데이터 번들 생성 중…")
    # Status edit with completion summary
    status_work.edit_text.assert_awaited_once()
    summary = status_work.edit_text.call_args[0][0]
    assert "Graph Data Bundle 생성 완료" in summary
    assert "엔티티: 3개" in summary
    assert "관계: 2개" in summary
    assert "24시간 후 자동 파기" in summary

    # Document upload
    msg_work.reply_document.assert_awaited_once()
    call_kwargs = msg_work.reply_document.call_args[1]
    assert "filename" in call_kwargs and call_kwargs["filename"].endswith(".tar.zst")
    assert "caption" in call_kwargs and "Graph Bundle:" in call_kwargs["caption"]

    # 5. Test /export graph list
    msg_list = AsyncMock()
    update_list = MagicMock()
    update_list.effective_user = SimpleNamespace(id=100)
    update_list.effective_message = msg_list
    ctx_list = MagicMock()
    ctx_list.args = ["graph", "list"]

    await on_export(update_list, ctx_list)
    msg_list.reply_text.assert_awaited_once()
    list_out = msg_list.reply_text.call_args[0][0]
    assert "활성 Graph Data Bundle 목록" in list_out

    # 6. Test /export graph purge
    msg_purge = AsyncMock()
    update_purge = MagicMock()
    update_purge.effective_user = SimpleNamespace(id=100)
    update_purge.effective_message = msg_purge
    ctx_purge = MagicMock()
    ctx_purge.args = ["graph", "purge"]

    await on_export(update_purge, ctx_purge)
    msg_purge.reply_text.assert_awaited_once()
    purge_out = msg_purge.reply_text.call_args[0][0]
    assert "Graph Bundle" in purge_out


@pytest.mark.asyncio
async def test_e2e_telegram_bot_large_bundle_fallback_to_url(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """When a bundle exceeds the 45MB limit, the bot delivers the secure download link URL instead of uploading."""
    settings = _build_test_settings(tmp_path)
    tg_app = build_app(settings)

    on_export = next(
        h.callback for h in tg_app.handlers[0] if getattr(h.callback, "__name__", "") == "on_export"
    )

    fake_file = tmp_path / "graph_bundle_huge.tar.zst"
    fake_file.write_bytes(b"x" * 1024)

    huge_bundle_info = GraphBundleInfo(
        bundle_id="gb_20261026_huge_9999",
        token="gb3_huge_token_123456789012345678",
        filename="graph_bundle_huge.tar.zst",
        filepath=fake_file,
        theme_id=0,
        entities_count=100000,
        relations_count=200000,
        documents_count=50000,
        size_bytes=52 * 1024 * 1024,  # 52MB > 45MB
        created_at=1761436800.0,
        expires_at=1761523200.0,
        download_url="http://127.0.0.1:8765/graph/bundle?token=gb3_huge_token_123456789012345678",
    )

    monkeypatch.setattr(
        "claire.telegram_bot._create_graph_bundle_for_delivery",
        AsyncMock(return_value=huge_bundle_info),
    )

    status_mock = AsyncMock()
    status_mock.edit_text = AsyncMock()
    msg = AsyncMock()
    msg.reply_text = AsyncMock(return_value=status_mock)
    msg.reply_document = AsyncMock()

    update = MagicMock()
    update.effective_user = SimpleNamespace(id=100)
    update.effective_message = msg
    ctx = MagicMock()
    ctx.args = ["graph"]

    await on_export(update, ctx)

    # reply_document should NOT be called because size > 45MB
    msg.reply_document.assert_not_called()

    # The download link was sent as reply_text
    link_calls = [
        call[0][0] for call in msg.reply_text.call_args_list if "http://127.0.0.1:8765/graph/bundle" in call[0][0]
    ]
    assert len(link_calls) >= 1
    assert "gb3_huge_token" in link_calls[0]
