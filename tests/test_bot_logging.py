"""Tests for Telegram bot file logging coverage, secret scrubbing, and document receipt logs."""

from __future__ import annotations

import logging
from logging.handlers import RotatingFileHandler
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from claire.core.config import Settings
from claire.core.secret_scrub import REDACTED, SecretScrubFilter, forget_secret, register_secret
from claire.ingest.pipeline import IngestReport
from claire.telegram.bot import _TELEGRAM_LOG_LOGGER_NAMES, build_app, setup_telegram_logging


def _matching_handlers(logger_name: str, log_file: Path) -> list[RotatingFileHandler]:
    resolved = log_file.resolve()
    matched: list[RotatingFileHandler] = []
    for handler in logging.getLogger(logger_name).handlers:
        base = getattr(handler, "baseFilename", None)
        if isinstance(handler, RotatingFileHandler) and base and Path(base).resolve() == resolved:
            matched.append(handler)
    return matched


def _flush_and_cleanup(log_file: Path) -> None:
    seen: set[int] = set()
    to_close: list[RotatingFileHandler] = []
    for logger_name in _TELEGRAM_LOG_LOGGER_NAMES:
        target = logging.getLogger(logger_name)
        for handler in _matching_handlers(logger_name, log_file):
            target.removeHandler(handler)
            if id(handler) not in seen:
                seen.add(id(handler))
                to_close.append(handler)
    for handler in to_close:
        handler.flush()
        handler.close()


def _flush_log_file(log_file: Path) -> None:
    for logger_name in _TELEGRAM_LOG_LOGGER_NAMES:
        for handler in _matching_handlers(logger_name, log_file):
            handler.flush()


def test_setup_telegram_logging_captures_inprocess_loggers(tmp_path: Path) -> None:
    """setup_telegram_logging writes INFO logs from Telegram, ingest, extract, plugin, and HTTP loggers into telegram.log."""
    log_file = setup_telegram_logging(tmp_path)
    assert log_file is not None
    assert log_file == tmp_path / "logs" / "telegram.log"
    assert log_file.is_file()

    try:
        samples = [
            ("claire.telegram", "botlog-test-telegram-001"),
            ("telegram", "botlog-test-ptb-002"),
            ("claire.ingest", "botlog-test-ingest-003"),
            ("claire.extract.transcript.gemini_stt", "botlog-test-gemini-stt-004"),
            ("claire_plugins.ingest_adapter.claire_naver_clip.adapter", "botlog-test-naver-clip-005"),
            ("httpx", "botlog-test-httpx-006"),
            ("httpx2", "botlog-test-httpx2-007"),
        ]
        for logger_name, message in samples:
            logging.getLogger(logger_name).info(message)

        _flush_log_file(log_file)
        content = log_file.read_text(encoding="utf-8")

        for logger_name, message in samples:
            assert content.count(message) == 1, f"expected {message!r} from {logger_name!r} once in telegram.log"
            assert f"[{logger_name}]" in content

        attached = [_matching_handlers(name, log_file) for name in _TELEGRAM_LOG_LOGGER_NAMES]
        assert all(len(items) == 1 for items in attached)
        first_handler = attached[0][0]
        assert all(items[0] is first_handler for items in attached)
        assert any(isinstance(f, SecretScrubFilter) for f in first_handler.filters)
    finally:
        _flush_and_cleanup(log_file)


def test_setup_telegram_logging_is_idempotent(tmp_path: Path) -> None:
    """Repeated calls to setup_telegram_logging do not add duplicate handlers or duplicate filters."""
    log_file_1 = setup_telegram_logging(tmp_path)
    assert log_file_1 is not None
    try:
        log_file_2 = setup_telegram_logging(tmp_path)
        assert log_file_2 == log_file_1

        ingest_logger = logging.getLogger("claire.ingest")
        for handler in _matching_handlers("claire.ingest", log_file_1):
            ingest_logger.removeHandler(handler)

        log_file_3 = setup_telegram_logging(tmp_path)
        assert log_file_3 == log_file_1

        for logger_name in _TELEGRAM_LOG_LOGGER_NAMES:
            handlers = _matching_handlers(logger_name, log_file_1)
            assert len(handlers) == 1, f"logger {logger_name!r} had {len(handlers)} handlers"
            scrub_filters = [f for f in handlers[0].filters if isinstance(f, SecretScrubFilter)]
            assert len(scrub_filters) == 1

        marker = "botlog-idempotent-single-write-999"
        logging.getLogger("claire.ingest").info(marker)
        _flush_log_file(log_file_1)
        assert log_file_1.read_text(encoding="utf-8").count(marker) == 1
    finally:
        _flush_and_cleanup(log_file_1)


def test_setup_telegram_logging_scrubs_secrets(tmp_path: Path) -> None:
    """Registered secrets and key patterns are scrubbed before writing to telegram.log."""
    secret_val = "botlog-registered-secret-value-987654321"
    google_key = "AIzaSyBotLogTestSecretKey1234567890"
    query_secret = "query-token-abcdef123456"
    register_secret(secret_val)
    log_file = setup_telegram_logging(tmp_path)
    assert log_file is not None
    try:
        logging.getLogger("claire.extract.transcript.gemini_stt").info("STT request failed with token=%s", secret_val)
        logging.getLogger("httpx2").info("GET https://generativelanguage.googleapis.com/v1/models?key=%s key2=%s", query_secret, google_key)

        handler = _matching_handlers("claire.telegram", log_file)[0]
        direct_record = logging.LogRecord(name="claire.telegram", level=logging.INFO, pathname=__file__, lineno=1, msg="direct handler leak %s", args=(secret_val,), exc_info=None)
        handler.handle(direct_record)

        _flush_log_file(log_file)
        content = log_file.read_text(encoding="utf-8")

        assert secret_val not in content
        assert google_key not in content
        assert query_secret not in content
        assert content.count(REDACTED) >= 4
    finally:
        forget_secret(secret_val)
        _flush_and_cleanup(log_file)


@pytest.mark.asyncio
async def test_on_document_emits_receipt_log_and_captures_inprocess_logs(tmp_path: Path) -> None:
    """on_document writes a User document received line before download/ingest and captures in-process ingest logs in telegram.log."""
    settings = Settings(telegram_bot_token="12345:fake_token_for_test", db_path=str(tmp_path / "data" / "claire.db"), provider="mock")
    settings.data_dir.mkdir(parents=True, exist_ok=True)
    log_file = settings.data_dir / "logs" / "telegram.log"

    app = build_app(settings)
    on_document_handler = None
    for handler in app.handlers[0]:
        if getattr(handler, "callback", None) and handler.callback.__name__ == "on_document":
            on_document_handler = handler.callback
            break
    assert on_document_handler is not None

    try:
        async def fake_download(dest: str) -> None:
            Path(dest).write_bytes(b"%PDF-1.4 dummy pdf bytes")

        mock_file = AsyncMock()
        mock_file.download_to_drive = fake_download

        mock_doc = MagicMock()
        mock_doc.file_name = "slides_deck.pdf"
        mock_doc.file_unique_id = "uniq_doc_777"
        mock_doc.get_file = AsyncMock(return_value=mock_file)

        mock_msg = AsyncMock()
        mock_msg.document = mock_doc
        mock_msg.video = None
        mock_msg.audio = None
        mock_msg.caption = "https://example.com/watch?v=abc123"
        mock_msg.reply_text = AsyncMock(return_value=AsyncMock())
        mock_msg.set_reaction = AsyncMock()

        update = MagicMock()
        update.effective_user = MagicMock(id=101, language_code="en")
        update.effective_chat = MagicMock(id=202)
        update.message = mock_msg
        update.update_id = 4242

        def fake_ingest_composite(*_args, **_kwargs) -> IngestReport:
            logging.getLogger("claire.extract.transcript.gemini_stt").info("Simulated STT trace during on_document 777")
            return IngestReport(document_id="doc_log_1", title="Slides Deck", source_type="video", presentation_pdfs=1, summary="Done")

        with patch("claire.ingest.service.IngestService.ingest_composite", side_effect=fake_ingest_composite) as mock_composite:
            await on_document_handler(update, None)
            assert mock_composite.called

        # Verify receipt log is also written before download fails.
        failing_doc = MagicMock()
        failing_doc.file_name = "broken_upload.pdf"
        failing_doc.file_unique_id = "uniq_fail_888"
        failing_doc.get_file = AsyncMock(side_effect=RuntimeError("network drop"))

        fail_msg = AsyncMock()
        fail_msg.document = failing_doc
        fail_msg.video = None
        fail_msg.audio = None
        fail_msg.caption = None
        fail_msg.reply_text = AsyncMock()
        fail_msg.set_reaction = AsyncMock()

        fail_update = MagicMock()
        fail_update.effective_user = MagicMock(id=303, language_code="en")
        fail_update.effective_chat = MagicMock(id=404)
        fail_update.message = fail_msg
        fail_update.update_id = 4243

        await on_document_handler(fail_update, None)

        _flush_log_file(log_file)
        content = log_file.read_text(encoding="utf-8")

        assert "User document received: user_id=101, chat_id=202, file_name='slides_deck.pdf', composite_url='https://example.com/watch?v=abc123'" in content
        assert "Simulated STT trace during on_document 777" in content
        assert "User document received: user_id=303, chat_id=404, file_name='broken_upload.pdf', composite_url=None" in content
    finally:
        _flush_and_cleanup(log_file)
