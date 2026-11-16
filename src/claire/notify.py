"""Small helpers that send operation alerts to the owner as a Telegram direct message.

The helpers call the Telegram HTTP API with httpx, without python-telegram-bot (no async bot runtime). Thus all daemons (recover, refresh) can import them, also outside the bot process. When the token or the chat ID is absent, each helper does nothing: this is safe in development, in tests, and on a server without Telegram.

Interface text comes from the catalog (I18N design Section 7.2). The caller supplies ``locale``, the locale of the target account; without it the active locale of the context applies.
"""

from __future__ import annotations

from typing import Any

import httpx

from .i18n import LocaleCode, resolve_bot_locale, t


def push_locale(account_locale: str | None, settings: Any = None) -> LocaleCode:
    """Locale of a push notification (I18N design Section 7.2): the ``accounts.locale`` of the target account, ``settings.ui_lang`` (``CLAIRE_LANG``), then the default. A push has no Telegram user, thus there is no ``language_code`` step."""
    settings_lang = getattr(settings, "ui_lang", None)
    return resolve_bot_locale(
        owner_locale=account_locale if isinstance(account_locale, str) else None,
        language_code=None,
        settings_lang=settings_lang if isinstance(settings_lang, str) else None,
    )


def send_approval_button(
    token: str, chat_id: int, text: str, nonce: str, *, timeout: float = 10.0, locale: LocaleCode | None = None
) -> int | None:
    """Send the message with the inline button that approves a web access (callback_data=auth:{nonce}). The bot callback processes the button.

    Returns the message_id of the button message (the caller removes the button when the request expires). Returns None on failure."""
    if not token or not chat_id:
        return None
    try:
        resp = httpx.post(
            f"https://api.telegram.org/bot{token}/sendMessage",
            json={
                "chat_id": chat_id, "text": text,
                "reply_markup": {"inline_keyboard": [[
                    {"text": t("notify.approval.button", locale=locale), "callback_data": f"auth:{nonce}"}]]},
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
    text: str | None = None,
    *, timeout: float = 10.0, locale: LocaleCode | None = None,
) -> bool:
    """Expire the approval button message: replace the text and remove the button (empty inline_keyboard). Without ``text`` the catalog text ``notify.approval.expired`` applies.

    Call this when no answer comes in the permitted time, so that no expired button stays in the chat."""
    if not token or not chat_id or not message_id:
        return False
    if text is None:
        text = t("notify.approval.expired", locale=locale)
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
    """Send a Telegram message to the owner chat. Returns True on success and False when Telegram is not configured or the call fails.

    All exceptions are caught, so that a failed notification never stops the caller (for example the recovery loop).
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
