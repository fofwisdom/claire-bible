"""소유자에게 텔레그램 DM 으로 운영 경보를 보내는 가벼운 헬퍼.

python-telegram-bot(async/봇 런타임) 없이 httpx 로 sendMessage 만 호출한다 →
recover/refresh 같은 데몬 어디서나 임포트해 쓸 수 있다(봇 프로세스가 아니어도).
token 또는 chat_id 가 없으면 조용히 no-op = 개발/테스트/미설정 환경 안전.
"""

from __future__ import annotations

import httpx


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
