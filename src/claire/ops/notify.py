"""Small helpers that send operation alerts to the owner as a Telegram direct message.

The helpers call the Telegram HTTP API with httpx, without python-telegram-bot (no async bot runtime). Thus all daemons (recover, refresh) can import them, also outside the bot process. When the token or the chat ID is absent, each helper does nothing: this is safe in development, in tests, and on a server without Telegram.

The caller makes the text from the catalog (I18N design Section 7.2); ``push_locale`` gives the locale of the target account.
"""

from __future__ import annotations

from typing import Any

import httpx

from ..i18n import LocaleCode, resolve_bot_locale


def push_locale(account_locale: str | None, settings: Any = None) -> LocaleCode:
    """Locale of a push notification (I18N design Section 7.2): the ``accounts.locale`` of the target account, ``settings.ui_lang`` (``CLAIRE_LANG``), then the default. A push has no Telegram user, thus there is no ``language_code`` step."""
    settings_lang = getattr(settings, "ui_lang", None)
    return resolve_bot_locale(
        owner_locale=account_locale if isinstance(account_locale, str) else None,
        language_code=None,
        settings_lang=settings_lang if isinstance(settings_lang, str) else None,
    )


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
