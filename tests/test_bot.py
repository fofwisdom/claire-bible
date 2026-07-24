"""텔레그램 봇 UX 헬퍼 — reaction 이모지 선택 + 진행 ticker(네트워크/PTB 없이)."""

from __future__ import annotations

from claire.telegram_bot import _status_emoji, _run_with_ticker


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
