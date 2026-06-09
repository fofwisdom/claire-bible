"""Gemini 호출 retry/throttle 동작 검증 (네트워크/라이브러리 비의존)."""

from __future__ import annotations

import time

import pytest

import claire.extract.gemini_provider as gp


class _Err(Exception):
    """code 속성을 가진 가짜 API 오류."""

    def __init__(self, code, msg=""):
        super().__init__(msg or f"{code} error")
        self.code = code


def _provider():
    # __init__ 의 genai.Client 를 우회해 객체만 구성.
    p = gp.GeminiProvider.__new__(gp.GeminiProvider)
    p.model = "m"
    p.embed_model = "e"
    p.min_interval = 0.0
    p.max_retries = 3
    return p


def test_is_retryable():
    assert gp._is_retryable(_Err(429))
    assert gp._is_retryable(_Err(503))
    assert gp._is_retryable(Exception("RESOURCE_EXHAUSTED quota"))
    assert not gp._is_retryable(_Err(400))
    assert not gp._is_retryable(Exception("bad request"))


def test_retry_succeeds_after_429(monkeypatch):
    p = _provider()
    monkeypatch.setattr(gp._time, "sleep", lambda *_: None)
    calls = {"n": 0}

    def fn():
        calls["n"] += 1
        if calls["n"] < 3:
            raise _Err(429)
        return "ok"

    assert p._call(fn) == "ok"
    assert calls["n"] == 3


def test_retry_gives_up_and_raises(monkeypatch):
    p = _provider()
    monkeypatch.setattr(gp._time, "sleep", lambda *_: None)
    with pytest.raises(_Err):
        p._call(lambda: (_ for _ in ()).throw(_Err(429)))


def test_non_retryable_raises_immediately(monkeypatch):
    p = _provider()
    monkeypatch.setattr(gp._time, "sleep", lambda *_: None)
    calls = {"n": 0}

    def fn():
        calls["n"] += 1
        raise _Err(400)

    with pytest.raises(_Err):
        p._call(fn)
    assert calls["n"] == 1


def test_throttle_enforces_min_interval():
    p = _provider()
    p.min_interval = 0.05
    gp._LAST_CALL[0] = 0.0
    t0 = time.monotonic()
    p._call(lambda: "a")
    p._call(lambda: "b")
    assert time.monotonic() - t0 >= 0.05


def test_retry_delay_parsed_from_message():
    assert gp._retry_delay_from_error(Exception("retryDelay: 7s blah")) == 7.0
    assert gp._retry_delay_from_error(Exception("no delay")) is None


# --- circuit breaker: 일일 quota 소진은 fail-fast, 분당 rate 는 계속 재시도 ---

def test_is_daily_quota_discriminates():
    # daily 마커
    assert gp._is_daily_quota(Exception("RESOURCE_EXHAUSTED GenerateRequestsPerDay"))
    assert gp._is_daily_quota(Exception("exceeded your daily quota"))
    # 비정상적으로 큰 retryDelay
    assert gp._is_daily_quota(Exception("retryDelay: 3600s"))
    # 결제/크레딧 소진(실제 관측: prepayment credits depleted) → fail-fast
    assert gp._is_daily_quota(Exception(
        "429 RESOURCE_EXHAUSTED Your prepayment credits are depleted. billing"))
    # 분당 rate(짧은 retryDelay) 또는 신호 없음 → daily 아님
    assert not gp._is_daily_quota(Exception("429 retryDelay: 7s"))
    assert not gp._is_daily_quota(Exception("429 rate limit"))


def test_daily_quota_fails_fast(monkeypatch):
    p = _provider()
    monkeypatch.setattr(gp._time, "sleep", lambda *_: None)
    calls = {"n": 0}

    def fn():
        calls["n"] += 1
        raise _Err(429, "RESOURCE_EXHAUSTED GenerateRequestsPerDay retryDelay: 3600s")

    with pytest.raises(_Err):
        p._call(fn)
    assert calls["n"] == 1  # 재시도 없이 즉시 실패(max_retries 안 태움)


def test_per_minute_rate_still_retries(monkeypatch):
    """분당 rate(작은 retryDelay)는 종전대로 재시도 후 성공."""
    p = _provider()
    monkeypatch.setattr(gp._time, "sleep", lambda *_: None)
    calls = {"n": 0}

    def fn():
        calls["n"] += 1
        if calls["n"] < 3:
            raise _Err(429, "429 rate limit retryDelay: 5s")
        return "ok"

    assert p._call(fn) == "ok"
    assert calls["n"] == 3
