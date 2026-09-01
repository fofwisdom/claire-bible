"""Unit and integration tests for refetch options splitting, effort customization, and Telegram bot support."""

from __future__ import annotations

import argparse
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch
import pytest

from claire import cli
from claire.config import Settings, extract_own_share_token, get_settings
from claire.extract.provider import ExtractionResult
from claire.ingest.fetchers.textfile import fetch_file, fetch_text
from claire.ingest.fetchers.web import fetch_web
from claire.ingest.fetchers.xcom import fetch_xcom
from claire.ingest.fetchers.youtube import fetch_youtube
from claire.ingest.router import fetch as router_fetch
from claire.ingest.service import IngestService
from claire.ontology.base import Document
from claire.store import db as dbm
from claire.telegram_bot import build_app, parse_regenerate_flags


@pytest.fixture
def clean_db(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    db_file = tmp_path / "test.db"
    vault_dir = tmp_path / "vault"
    monkeypatch.setenv("CLAIRE_DB_PATH", str(db_file))
    monkeypatch.setenv("CLAIRE_VAULT_PATH", str(vault_dir))
    monkeypatch.setenv("CLAIRE_PROVIDER", "mock")
    monkeypatch.setenv("CLAIRE_RAW_CHAR_BUDGET", "100")
    monkeypatch.setenv("CLAIRE_PUBLIC_URL", "https://claire.example.org")
    monkeypatch.setenv("CLAIRE_FQDN", "claire.example.org")
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "123456:ABC-DEF1234ghIkl-zyx57W2v1u123ew11")
    get_settings.cache_clear()

    conn = dbm.connect(db_file)
    dbm.init_db(conn)
    conn.close()

    s = get_settings()
    svc = IngestService(s)
    return svc, db_file


# --- 1. Fetchers & Router full_content Tests ---

def test_fetch_text_full_content():
    long_text = "A" * 500
    doc_default = fetch_text(long_text, full_content=False)
    assert len(doc_default.raw_text) == 500  # fetch_text does not truncate raw notes by design

    doc_full = fetch_text(long_text, full_content=True)
    assert len(doc_full.raw_text) == 500


def test_fetch_file_full_content(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("CLAIRE_RAW_CHAR_BUDGET", "50")
    get_settings.cache_clear()

    test_file = tmp_path / "long.txt"
    test_file.write_text("X" * 300, encoding="utf-8")

    doc_budget = fetch_file(str(test_file), full_content=False)
    assert len(doc_budget.raw_text) == 50
    assert doc_budget.meta["raw_truncated"] is True
    assert doc_budget.meta["orig_chars"] == 300

    doc_full = fetch_file(str(test_file), full_content=True)
    assert len(doc_full.raw_text) == 300
    assert doc_full.meta["raw_truncated"] is False
    assert doc_full.meta["orig_chars"] == 300


def test_fetch_web_full_content(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("CLAIRE_RAW_CHAR_BUDGET", "80")
    get_settings.cache_clear()

    fake_html_text = "W" * 400
    # Mock _fetch_static
    monkeypatch.setattr(
        "claire.ingest.fetchers.web._fetch_static",
        lambda url: ("Web Title", fake_html_text, [], {}, None, url, [], False),
    )

    doc_budget = fetch_web("https://example.com/article", full_content=False)
    assert len(doc_budget.raw_text) == 80
    assert doc_budget.meta["raw_truncated"] is True

    doc_full = fetch_web("https://example.com/article", full_content=True)
    assert len(doc_full.raw_text) == 400
    assert doc_full.meta["raw_truncated"] is False


def test_router_fetch_forwards_full_content(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("CLAIRE_RAW_CHAR_BUDGET", "50")
    get_settings.cache_clear()

    monkeypatch.setattr(
        "claire.ingest.fetchers.web._fetch_static",
        lambda url: ("Web Title", "Z" * 300, [], {}, None, url, [], False),
    )

    doc_budget = router_fetch("https://example.com/test", full_content=False)
    assert len(doc_budget.raw_text) == 50

    doc_full = router_fetch("https://example.com/test", full_content=True)
    assert len(doc_full.raw_text) == 300


# --- 2. IngestService.regenerate_components Tests ---

def test_regenerate_components_refetch_and_refetch_full(clean_db, monkeypatch: pytest.MonkeyPatch):
    svc, db_file = clean_db

    # Insert initial document and share
    conn = dbm.connect(db_file)
    doc_id = "doc_refetch_test"
    initial_doc = Document(
        id=doc_id,
        title="Initial Document",
        url="https://example.com/doc",
        canonical_url="https://example.com/doc",
        raw_text="Initial short text",
        content_hash="hash1",
        source_type="web",
    )
    dbm.insert_document(conn, initial_doc)
    share_token = dbm.create_doc_share(conn, doc_id)
    conn.close()

    # Mock default_fetch in service
    def mock_fetch(url, full_content=False):
        if full_content:
            text = "F" * 500  # full 500 chars
        else:
            text = "B" * 100  # budget limited 100 chars
        return Document(
            id=doc_id,
            title="Refetched Title",
            url=url,
            canonical_url=url,
            raw_text=text,
            content_hash="fresh_hash",
            source_type="web",
        )

    monkeypatch.setattr("claire.ingest.service.default_fetch", mock_fetch)

    # 1. Dry run with refetch
    diag_refetch = svc.regenerate_components(target=share_token, refetch=True, force=False)
    assert "refetch_content" in diag_refetch["targets"][0]["actions"]

    # 2. Dry run with refetch_full
    diag_full = svc.regenerate_components(target=share_token, refetch_full=True, force=False)
    assert "refetch_full_content" in diag_full["targets"][0]["actions"]

    # 3. Apply refetch (budget limited) with effort override
    res_budget = svc.regenerate_components(
        target=share_token,
        refetch=True,
        effort="high",
        force=True,
    )
    assert res_budget["count"] == 1
    t_budget = res_budget["targets"][0]
    assert t_budget["refetched"] is True
    assert t_budget["refetched_full"] is False
    assert t_budget["new_len"] == 100

    conn = dbm.connect(db_file)
    saved_budget = dbm.get_document(conn, doc_id)
    assert len(saved_budget.raw_text) == 100
    conn.close()

    # 4. Apply refetch_full (unlimited)
    res_full = svc.regenerate_components(
        target=share_token,
        refetch_full=True,
        effort="medium",
        force=True,
    )
    assert res_full["count"] == 1
    t_full = res_full["targets"][0]
    assert t_full["refetched"] is True
    assert t_full["refetched_full"] is True
    assert t_full["new_len"] == 500

    conn = dbm.connect(db_file)
    saved_full = dbm.get_document(conn, doc_id)
    assert len(saved_full.raw_text) == 500
    conn.close()


# --- 3. CLI Options & Standardization Tests ---

def test_cli_regenerate_refetch_options():
    parser = cli.build_parser()

    # Standard regenerate with --refetch
    args1 = parser.parse_args(["regenerate", "doc_123", "--refetch", "--effort", "high"])
    assert args1.refetch is True
    assert args1.refetch_full is False
    assert args1.effort == "high"

    # Standard regenerate with --refetch-full
    args2 = parser.parse_args(["regenerate", "doc_123", "--refetch-full", "--effort", "low"])
    assert args2.refetch is False
    assert args2.refetch_full is True
    assert args2.effort == "low"

    # summary-regenerate with --refetch-full
    args3 = parser.parse_args(["summary-regenerate", "doc_123", "--refetch-full", "--effort", "high"])
    assert args3.refetch is False
    assert args3.refetch_full is True
    assert args3.effort == "high"

    # Mutually exclusive: --refetch and --refetch-full together should raise error
    with pytest.raises(SystemExit):
        parser.parse_args(["regenerate", "doc_123", "--refetch", "--refetch-full"])


# --- 4. FQDN Matching & extract_own_share_token Tests ---

def test_extract_own_share_token(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("CLAIRE_PUBLIC_URL", "https://claire.myorg.com")
    monkeypatch.setenv("CLAIRE_FQDN", "claire.myorg.com")
    get_settings.cache_clear()

    # Matching FQDN share URL
    token1 = extract_own_share_token("https://claire.myorg.com/p?s=dzr73zpxh2bah4vp")
    assert token1 == "dzr73zpxh2bah4vp"

    # Matching FQDN with trailing slash
    token2 = extract_own_share_token("https://claire.myorg.com/p/?s=dzr73zpxh2bah4vp")
    assert token2 == "dzr73zpxh2bah4vp"

    # Relative paths
    token3 = extract_own_share_token("/p?s=dzr73zpxh2bah4vp")
    assert token3 == "dzr73zpxh2bah4vp"

    token4 = extract_own_share_token("?s=dzr73zpxh2bah4vp")
    assert token4 == "dzr73zpxh2bah4vp"

    # External website with /p?s=... (MUST NOT match)
    token_ext = extract_own_share_token("https://external-news.com/p?s=dzr73zpxh2bah4vp")
    assert token_ext is None


# --- 5. Telegram Bot Flag Parsing Tests ---

def test_parse_regenerate_flags():
    # 1. Standard flags
    text, refetch, refetch_full, effort = parse_regenerate_flags("https://example.com/p?s=tok --refetch --effort high")
    assert text == "https://example.com/p?s=tok"
    assert refetch is True
    assert refetch_full is False
    assert effort == "high"

    # 2. Refetch full flag
    text, refetch, refetch_full, effort = parse_regenerate_flags("doc_123 --refetch-full -e low")
    assert text == "doc_123"
    assert refetch is False
    assert refetch_full is True
    assert effort == "low"

    # 3. Korean flags
    text, refetch, refetch_full, effort = parse_regenerate_flags("doc_123 --전체재수집 --추론 medium")
    assert text == "doc_123"
    assert refetch is False
    assert refetch_full is True
    assert effort == "medium"

    text, refetch, refetch_full, effort = parse_regenerate_flags("doc_123 --재수집 --사고 high")
    assert text == "doc_123"
    assert refetch is True
    assert refetch_full is False
    assert effort == "high"

    # 4. Short flags (-r, -R, -e)
    text, refetch, refetch_full, effort = parse_regenerate_flags("doc_123 -R -e 2000")
    assert text == "doc_123"
    assert refetch is False
    assert refetch_full is True
    assert effort == "2000"

    text, refetch, refetch_full, effort = parse_regenerate_flags("doc_123 -r")
    assert text == "doc_123"
    assert refetch is True
    assert refetch_full is False
    assert effort is None


# --- 6. Telegram Bot Message & Callback Flow Tests ---

@pytest.mark.asyncio
async def test_telegram_bot_handlers(clean_db, monkeypatch: pytest.MonkeyPatch):
    svc, db_file = clean_db

    conn = dbm.connect(db_file)
    doc_id = "doc_tg_flow_test"
    doc = Document(
        id=doc_id,
        title="Telegram Flow Doc",
        url="https://example.com/tg-article",
        canonical_url="https://example.com/tg-article",
        raw_text="Original text for telegram test",
        content_hash="h1",
        source_type="web",
    )
    dbm.insert_document(conn, doc)
    share_token = dbm.create_doc_share(conn, doc_id)
    conn.close()

    # Build the app to inspect its handlers
    app = build_app(svc.s)
    handlers = app.handlers[0]  # group 0 handlers

    # Find on_message handler
    from telegram.ext import MessageHandler, CallbackQueryHandler, CommandHandler
    msg_handler = None
    cb_handler = None
    cmd_handlers = {}
    for h in handlers:
        if isinstance(h, MessageHandler) and getattr(h, "filters", None):
            msg_handler = h
        elif isinstance(h, CallbackQueryHandler):
            cb_handler = h
        elif isinstance(h, CommandHandler):
            for cmd in h.commands:
                cmd_handlers[cmd] = h

    assert msg_handler is not None
    assert cb_handler is not None
    assert "refetch" not in cmd_handlers
    assert "regenerate" not in cmd_handlers

    # 1. Test sending own share URL -> Expect reply with InlineKeyboardMarkup buttons
    fake_msg = MagicMock()
    fake_msg.text = f"https://claire.example.org/p?s={share_token}"
    fake_msg.reply_text = AsyncMock()

    fake_update = MagicMock()
    fake_update.message = fake_msg
    fake_update.effective_user.id = 100
    fake_update.effective_chat.id = 200
    fake_update.update_id = 1

    fake_ctx = MagicMock()

    await msg_handler.callback(fake_update, fake_ctx)
    fake_msg.reply_text.assert_called_once()
    reply_args, reply_kwargs = fake_msg.reply_text.call_args
    assert "Telegram Flow Doc" in reply_args[0]
    markup = reply_kwargs.get("reply_markup")
    assert markup is not None
    button_callbacks = [b.callback_data for row in markup.inline_keyboard for b in row]
    assert f"rg:det:{doc_id}" in button_callbacks
    assert f"rg:ref:{doc_id}" in button_callbacks
    assert f"rg:full:{doc_id}" in button_callbacks

    # 2. Test callback on rg:full:doc_id
    fake_cb_query = MagicMock()
    fake_cb_query.data = f"rg:full:{doc_id}"
    fake_cb_query.answer = AsyncMock()
    fake_cb_query.edit_message_reply_markup = AsyncMock()
    status_msg = MagicMock()
    status_msg.edit_text = AsyncMock()
    fake_cb_query.message.reply_text = AsyncMock(return_value=status_msg)

    fake_cb_update = MagicMock()
    fake_cb_update.callback_query = fake_cb_query
    fake_cb_update.effective_user.id = 100

    # Mock regenerate_components on IngestService
    with patch.object(IngestService, "regenerate_components", return_value={"count": 1, "targets": [{"title": "Telegram Flow Doc", "new_len": 500, "refetched_full": True}]}) as mock_regen:
        await cb_handler.callback(fake_cb_update, fake_ctx)
        mock_regen.assert_called_once()
        _, regen_kwargs = mock_regen.call_args
        assert regen_kwargs["doc_id"] == doc_id
        assert regen_kwargs["refetch_full"] is True
        assert regen_kwargs["force"] is True

    # 3. Test sending share URL directly with flags & directive (e.g. --refetch-full --effort high | 보안 관점)
    fake_msg_flags = MagicMock()
    fake_msg_flags.text = f"https://claire.example.org/p?s={share_token} --refetch-full --effort high | 보안 관점"
    fake_msg_flags.reply_text = AsyncMock(return_value=status_msg)
    fake_update_flags = MagicMock()
    fake_update_flags.message = fake_msg_flags
    fake_update_flags.effective_user.id = 100
    fake_update_flags.effective_chat.id = 200
    fake_update_flags.update_id = 2

    with patch.object(IngestService, "regenerate_components", return_value={"count": 1, "targets": [{"title": "Telegram Flow Doc", "new_len": 600, "refetched_full": True}]}) as mock_regen:
        with patch("claire.telegram_bot._react", new=AsyncMock()):
            await msg_handler.callback(fake_update_flags, fake_ctx)
            mock_regen.assert_called_once()
            _, regen_kwargs = mock_regen.call_args
            assert regen_kwargs["doc_id"] == doc_id
            assert regen_kwargs["refetch_full"] is True
            assert regen_kwargs["refetch"] is False
            assert regen_kwargs["effort"] == "high"
            assert regen_kwargs["directive"] == "보안 관점"
