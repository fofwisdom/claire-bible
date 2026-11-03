"""기록 전 비밀 지우기 (설계 §11.3).

프로바이더 오류 문자열에 키가 섞일 수 있으므로 로그·예외·``raw_inbox`` 오류·HTTP 응답에 쓰기 전에 지운다.
복호화한 계정 키는 ``register_secret``으로 등록하고, ``SecretScrubFilter``가 로그 레코드에서 등록 값과 키 모양 문자열을 치환한다.
"""

from __future__ import annotations

import logging
import re
import threading
from typing import Final

REDACTED: Final = "[REDACTED]"
_MIN_SECRET_LEN: Final = 8
# Google API 키(AIza…), OpenAI·Anthropic 계열(sk-…), Bearer 토큰, URL 쿼리의 key= 값
_KEY_PATTERNS: Final[tuple[re.Pattern[str], ...]] = (
    re.compile(r"AIza[0-9A-Za-z_\-]{20,}"),
    re.compile(r"\bsk-[0-9A-Za-z_\-]{16,}"),
    re.compile(r"(?i)(bearer\s+)[0-9A-Za-z._\-~+/=]{8,}"),
    re.compile(r"(?i)([?&](?:key|api_key|access_token)=)[^&\s\"']+"),
)

_lock = threading.Lock()
_registered: set[str] = set()


def register_secret(value: str) -> None:
    """복호화한 비밀을 등록한다. 짧은 값은 오탐이 커서 등록하지 않는다(패턴으로만 지운다)."""
    if value and len(value) >= _MIN_SECRET_LEN:
        with _lock:
            _registered.add(value)


def forget_secret(value: str) -> None:
    with _lock:
        _registered.discard(value)


def scrub_text(text: object, extra: tuple[str, ...] = ()) -> str:
    """등록 값, ``extra``, 키 모양 문자열을 ``[REDACTED]``로 바꾼 문자열."""
    out = str(text)
    with _lock:
        values = sorted(_registered.union(v for v in extra if v and len(v) >= _MIN_SECRET_LEN), key=len, reverse=True)
    for value in values:
        if value in out:
            out = out.replace(value, REDACTED)
    for pattern in _KEY_PATTERNS:
        out = pattern.sub(lambda m: (m.group(1) if m.lastindex else "") + REDACTED, out)
    return out


def scrub_exception(exc: BaseException) -> str:
    """``str(exc)``를 지운 문자열. 예외 원문을 기록할 자리에서 ``str(e)`` 대신 쓴다."""
    return scrub_text(exc)


class SecretScrubFilter(logging.Filter):
    """로그 레코드의 메시지·인자·예외 문자열을 지운다. 레코드를 버리지 않는다."""

    def filter(self, record: logging.LogRecord) -> bool:
        try:
            message = record.getMessage()
        except Exception:
            message = str(record.msg)
        scrubbed = scrub_text(message)
        if record.exc_info and record.exc_info[1] is not None:
            exc_text = record.exc_text or logging.Formatter().formatException(record.exc_info)
            record.exc_text = scrub_text(exc_text)
            record.exc_info = None
        record.msg = scrubbed
        record.args = None
        return True


_installed = False


def install_log_scrubber() -> None:
    """루트 로거의 모든 핸들러와 이후 추가되는 핸들러가 지운 레코드만 받게 한다(멱등)."""
    global _installed
    with _lock:
        if _installed:
            return
        _installed = True
    factory = logging.getLogRecordFactory()
    scrub_filter = SecretScrubFilter()

    def _factory(*args, **kwargs):  # type: ignore[no-untyped-def]
        record = factory(*args, **kwargs)
        scrub_filter.filter(record)
        return record

    logging.setLogRecordFactory(_factory)


def _reset_for_tests() -> None:
    with _lock:
        _registered.clear()
