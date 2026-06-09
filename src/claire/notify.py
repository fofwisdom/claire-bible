"""소유자에게 텔레그램 DM 으로 운영 경보를 보내는 가벼운 헬퍼.

python-telegram-bot(async/봇 런타임) 없이 httpx 로 sendMessage 만 호출한다 →
recover/refresh 같은 데몬 어디서나 임포트해 쓸 수 있다(봇 프로세스가 아니어도).
token 또는 chat_id 가 없으면 조용히 no-op = 개발/테스트/미설정 환경 안전.
"""

from __future__ import annotations

import httpx


def send_approval_button(
    token: str, chat_id: int, text: str, nonce: str, *, timeout: float = 10.0
) -> int | None:
    """웹 접속 승인용 inline 버튼 메시지(callback_data=auth:{nonce}). 봇 콜백이 처리.

    버튼 메시지의 message_id 를 반환(만료 시 그 메시지의 버튼을 지우기 위해). 실패 시 None."""
    if not token or not chat_id:
        return None
    try:
        resp = httpx.post(
            f"https://api.telegram.org/bot{token}/sendMessage",
            json={
                "chat_id": chat_id, "text": text,
                "reply_markup": {"inline_keyboard": [[
                    {"text": "✅ 웹 접속 승인", "callback_data": f"auth:{nonce}"}]]},
            },
            timeout=timeout,
        )
        if resp.status_code == 200:
            return resp.json().get("result", {}).get("message_id")
    except Exception:  # noqa: BLE001
        pass
    return None


def expire_button(
    token: str, chat_id: int, message_id: int,
    text: str = "⏱ 승인 요청이 만료되었습니다. 웹에서 다시 시도하세요.",
    *, timeout: float = 10.0,
) -> bool:
    """승인 버튼 메시지를 만료 처리 — 텍스트 교체 + 버튼 제거(빈 inline_keyboard).

    일정시간 내 응답이 없으면 호출(스팸/유효기간 지난 버튼 잔존 방지)."""
    if not token or not chat_id or not message_id:
        return False
    try:
        resp = httpx.post(
            f"https://api.telegram.org/bot{token}/editMessageText",
            json={"chat_id": chat_id, "message_id": message_id, "text": text,
                  "reply_markup": {"inline_keyboard": []}},
            timeout=timeout,
        )
        return resp.status_code == 200
    except Exception:  # noqa: BLE001
        return False


def notify_owner(token: str, chat_id: int, text: str, *, timeout: float = 10.0) -> bool:
    """소유자 chat 으로 텔레그램 메시지 전송. 성공 시 True, 미설정/실패 시 False.

    알림 실패가 호출측(예: 복구 루프)을 절대 막지 않도록 모든 예외를 삼킨다.
    """
    if not token or not chat_id:
        return False
    try:
        resp = httpx.post(
            f"https://api.telegram.org/bot{token}/sendMessage",
            json={"chat_id": chat_id, "text": text, "disable_web_page_preview": True},
            timeout=timeout,
        )
        return resp.status_code == 200
    except Exception:  # noqa: BLE001
        return False
