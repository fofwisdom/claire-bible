"""텔레그램 봇 UX 헬퍼 — reaction 이모지 선택 + 진행 ticker(네트워크/PTB 없이)."""

from __future__ import annotations

import pytest

from claire import telegram_bot as botmod
from claire.telegram_bot import (
    _consume_pending_expansion,
    _create_pending_expansion,
    _run_with_ticker,
    _status_emoji,
)


def test_status_emoji_maps_result():
    assert _status_emoji(None, False) == "👍"   # 신규/갱신 완료
    assert _status_emoji(None, True) == "🤔"    # 중복
    assert _status_emoji("boom", False) == "👎"  # 실패
    assert _status_emoji("boom", True) == "👎"   # error 가 duplicate 보다 우선


async def test_run_with_ticker_returns_work_result():
    """work(블로킹)의 반환값을 그대로 돌려주고, ticker 가 깔끔히 정리되어야 한다."""
    class FakeStatus:
        def __init__(self):
            self.edits = []

        async def edit_text(self, t):
            self.edits.append(t)

    st = FakeStatus()
    out = await _run_with_ticker(st, "label", lambda: {"ok": 1})
    assert out == {"ok": 1}


async def test_run_with_ticker_propagates_exception():
    class FakeStatus:
        async def edit_text(self, t):
            pass

    def boom():
        raise RuntimeError("work failed")

    try:
        await _run_with_ticker(FakeStatus(), "l", boom)
        raised = False
    except RuntimeError:
        raised = True
    assert raised  # work 예외는 호출측으로 전파(핸들러가 👎 처리)


def test_pending_expansion_is_random_bound_and_single_use(monkeypatch):
    pending = {}
    monkeypatch.setattr(botmod, "_is_allowed", lambda user_id: user_id == 101)
    tokens = iter(("random-token-one", "random-token-two"))
    monkeypatch.setattr(botmod.secrets, "token_urlsafe", lambda _size: next(tokens))

    first = _create_pending_expansion(
        pending, ["https://example.com/a"], 101, 202, now=10,
    )
    second = _create_pending_expansion(
        pending, ["https://example.com/b"], 101, 202, now=10,
    )

    assert (first, second) == ("random-token-one", "random-token-two")
    assert _consume_pending_expansion(pending, first, 999, 202, now=11) is None
    assert _consume_pending_expansion(pending, first, 101, 999, now=11) is None
    assert first in pending  # 다른 사용자의 클릭으로 소유자의 요청이 소모되지 않는다.
    assert _consume_pending_expansion(
        pending, first, 101, 202, now=11,
    ) == ["https://example.com/a"]
    assert _consume_pending_expansion(pending, first, 101, 202, now=11) is None


def test_pending_expansion_checks_allowlist_and_ttl(monkeypatch):
    pending = {}
    token = _create_pending_expansion(
        pending, ["https://example.com/a"], 101, 202, now=100, ttl_seconds=10,
    )

    monkeypatch.setattr(botmod, "_is_allowed", lambda _user_id: False)
    assert _consume_pending_expansion(pending, token, 101, 202, now=101) is None
    assert token in pending

    monkeypatch.setattr(botmod, "_is_allowed", lambda user_id: user_id == 101)
    assert _consume_pending_expansion(pending, token, 101, 202, now=110) is None
    assert token not in pending


def test_empty_allowlist_is_closed_unless_explicitly_enabled(monkeypatch):
    from claire.config import get_settings

    monkeypatch.setenv("CLAIRE_ALLOWED_USERS", "")
    monkeypatch.setenv("CLAIRE_ALLOW_ALL_USERS", "false")
    get_settings.cache_clear()
    assert botmod._is_allowed(101) is False

    monkeypatch.setenv("CLAIRE_ALLOW_ALL_USERS", "true")
    get_settings.cache_clear()
    assert botmod._is_allowed(101) is True
    get_settings.cache_clear()


def test_invalid_allowlist_entry_is_not_silently_ignored(monkeypatch):
    from claire.config import get_settings

    monkeypatch.setenv("CLAIRE_ALLOWED_USERS", "101,not-a-number")
    get_settings.cache_clear()
    with pytest.raises(ValueError, match="numeric IDs"):
        _ = get_settings().allowed_user_ids
    get_settings.cache_clear()
