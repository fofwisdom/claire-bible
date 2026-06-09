"""소유자 텔레그램 경보 헬퍼 + notify_chat_id 폴백 (네트워크 없이)."""

from __future__ import annotations

from claire import notify
from claire.config import Settings


def test_notify_noop_when_unset():
    assert notify.notify_owner("", 123, "hi") is False   # 토큰 없음
    assert notify.notify_owner("tok", 0, "hi") is False  # chat 없음


def test_notify_posts_to_telegram(monkeypatch):
    captured = {}

    class _Resp:
        status_code = 200

    def fake_post(url, **kw):
        captured["url"] = url
        captured["json"] = kw.get("json")
        return _Resp()

    monkeypatch.setattr(notify.httpx, "post", fake_post)
    assert notify.notify_owner("BOTTOKEN", 555, "alert!") is True
    assert "botBOTTOKEN/sendMessage" in captured["url"]
    assert captured["json"]["chat_id"] == 555
    assert captured["json"]["text"] == "alert!"


def test_notify_swallows_exceptions(monkeypatch):
    def boom(*a, **k):
        raise RuntimeError("network down")

    monkeypatch.setattr(notify.httpx, "post", boom)
    # 알림 실패가 호출측(복구 루프)을 막으면 안 됨 → False 반환, 예외 전파 금지.
    assert notify.notify_owner("tok", 1, "x") is False


def test_notify_chat_id_fallback(monkeypatch):
    monkeypatch.setenv("CLAIRE_ALLOWED_USERS", "999,1000")
    monkeypatch.delenv("CLAIRE_OWNER_CHAT_ID", raising=False)
    assert Settings().notify_chat_id == 999  # owner 미설정 → allowed 최솟값 폴백

    monkeypatch.setenv("CLAIRE_OWNER_CHAT_ID", "42")
    assert Settings().notify_chat_id == 42   # owner 설정 시 우선
