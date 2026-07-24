from __future__ import annotations

import logging

from claire.logging_config import SecretRedactionFilter, redact_log_text


def test_redacts_telegram_url_query_token_and_headers():
    raw = (
        "POST https://api.telegram.org/bot123456:SECRET/sendMessage"
        "?t=session-secret&signature=signed Authorization: Bearer owner-secret"
    )
    clean = redact_log_text(raw)
    for secret in ("123456:SECRET", "session-secret", "signed", "owner-secret"):
        assert secret not in clean
    assert clean.count("[REDACTED]") == 4


def test_filter_redacts_formatted_arguments():
    record = logging.LogRecord(
        "claire.test", logging.INFO, __file__, 1,
        "request %s", ("https://host/?token=sentinel",), None)
    assert SecretRedactionFilter().filter(record)
    assert "sentinel" not in record.getMessage()
    assert "[REDACTED]" in record.getMessage()
