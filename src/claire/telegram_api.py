"""텔레그램 Bot API 시험 호출 (계정 봇 등록 전 검증).

``getMe``로 토큰이 살아 있는지 보고 봇 USERNAME을 얻는다. 토큰 원문은 오류 메시지에 넣지 않는다.
"""

from __future__ import annotations

from dataclasses import dataclass

from .secret_scrub import register_secret, scrub_text

TELEGRAM_API_BASE = "https://api.telegram.org"


class TelegramVerifyRejected(RuntimeError):
    """텔레그램이 토큰을 거부했다(401·404)."""


class TelegramVerifyUnavailable(RuntimeError):
    """판정하지 못했다(네트워크 오류·타임아웃·429·5xx)."""


@dataclass(frozen=True, slots=True)
class BotIdentity:
    bot_id: int
    username: str


def verify_bot_token(token: str, *, timeout: float = 10.0, transport=None) -> BotIdentity:
    """``getMe`` 호출. 성공하면 봇 ID와 USERNAME, 거부면 ``TelegramVerifyRejected``, 판정 불가면 ``TelegramVerifyUnavailable``."""
    import httpx

    register_secret(token)
    url = f"{TELEGRAM_API_BASE}/bot{token}/getMe"
    try:
        with httpx.Client(timeout=timeout, transport=transport) as client:
            response = client.get(url)
    except httpx.HTTPError as exc:
        raise TelegramVerifyUnavailable(scrub_text(f"telegram getMe failed: {type(exc).__name__}", extra=(token,))) from None
    if response.status_code in (401, 403, 404):
        raise TelegramVerifyRejected("telegram rejected the bot token")
    if response.status_code != 200:
        raise TelegramVerifyUnavailable(f"telegram getMe returned HTTP {response.status_code}")
    try:
        payload = response.json()
        result = payload["result"] if payload.get("ok") else None
        if not isinstance(result, dict) or not result.get("is_bot"):
            raise TelegramVerifyRejected("telegram getMe did not return a bot")
        return BotIdentity(bot_id=int(result["id"]), username=str(result.get("username") or ""))
    except (KeyError, TypeError, ValueError):
        raise TelegramVerifyUnavailable("telegram getMe returned an invalid response") from None
