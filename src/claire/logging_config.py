"""애플리케이션 로그 설정과 비밀값 방어."""

from __future__ import annotations

import logging
import re

_BOT_TOKEN = re.compile(r"(/bot)[^/\s]+", re.IGNORECASE)
_QUERY_SECRET = re.compile(
    r"([?&](?:t|s|token|key|api_key|signature|sig|code)=)[^&\s]+",
    re.IGNORECASE,
)
_AUTH_HEADER = re.compile(
    r"(\b(?:authorization|x-token)\s*[:=]\s*)(?:bearer\s+)?[^\s,;]+",
    re.IGNORECASE,
)


def redact_log_text(value: object) -> str:
    text = str(value)
    text = _BOT_TOKEN.sub(r"\1[REDACTED]", text)
    text = _QUERY_SECRET.sub(r"\1[REDACTED]", text)
    return _AUTH_HEADER.sub(r"\1[REDACTED]", text)


class SecretRedactionFilter(logging.Filter):
    """format 인자까지 합친 뒤 민감 URL/header 값을 지운다."""

    def filter(self, record: logging.LogRecord) -> bool:
        record.msg = redact_log_text(record.getMessage())
        record.args = ()
        return True


def configure_logging() -> None:
    """claire 로그는 INFO로 유지하고 URL을 기록하는 외부 라이브러리는 제한한다."""
    logging.basicConfig(level=logging.WARNING)
    logging.getLogger("claire").setLevel(logging.INFO)
    for name in ("httpx", "httpcore", "aiohttp.access"):
        logging.getLogger(name).setLevel(logging.WARNING)

    root = logging.getLogger()
    if not root.handlers:
        root.addHandler(logging.StreamHandler())
    for handler in root.handlers:
        if not any(isinstance(item, SecretRedactionFilter) for item in handler.filters):
            handler.addFilter(SecretRedactionFilter())
