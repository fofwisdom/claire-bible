"""Request locale for the WebUI (I18N design Sections 5.4 and 6.1): ``I18nMiddleware`` and the ``claire_locale`` cookie.

The middleware is immediately inside ``AuthenticationMiddleware`` (``security.wrap_web_app``). It resolves the locale in this order: ``Principal.locale``, cookie ``claire_locale``, ``Accept-Language``, ``CLAIRE_LANG``, ``en``. On a public route the principal is anonymous (the authentication middleware does not validate credentials there), thus the cookie is the first step that applies.
"""

from __future__ import annotations

from starlette.requests import Request
from starlette.responses import Response
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from ..i18n import LOCALE_COOKIE_MAX_AGE, LOCALE_COOKIE_NAME, LocaleCode, resolve_web_locale, use_locale
from .principal import PRINCIPAL_STATE_KEY, Principal


def set_locale_cookie(response: Response, locale: LocaleCode, *, secure: bool) -> None:
    """The cookie is not ``HttpOnly``: client JavaScript can read it. It holds a language code only."""
    response.set_cookie(LOCALE_COOKIE_NAME, locale, max_age=LOCALE_COOKIE_MAX_AGE, path="/", secure=secure, httponly=False, samesite="lax")


def clear_locale_cookie(response: Response, *, secure: bool) -> None:
    response.delete_cookie(LOCALE_COOKIE_NAME, path="/", secure=secure, httponly=False, samesite="lax")


class I18nMiddleware:
    """Sets the active locale for the request and adds ``Content-Language`` to HTML responses.

    Responses that are not HTML do not get the header: API and protocol payloads (JSON errors, ActivityPub, OAuth, MCP) do not change with the locale.
    """

    def __init__(self, app: ASGIApp, *, default_lang: str = "") -> None:
        self.app = app
        self.default_lang = default_lang

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        request = Request(scope)
        principal = scope.get("state", {}).get(PRINCIPAL_STATE_KEY)
        locale = resolve_web_locale(
            account_locale=principal.locale if isinstance(principal, Principal) else None,
            cookie_locale=request.cookies.get(LOCALE_COOKIE_NAME),
            accept_language=request.headers.get("accept-language"),
            settings_lang=self.default_lang,
        )

        async def send_with_language(message: Message) -> None:
            if message["type"] == "http.response.start":
                headers = list(message.get("headers", []))
                names = {name.lower(): value for name, value in headers}
                if names.get(b"content-type", b"").lower().startswith(b"text/html") and b"content-language" not in names:
                    headers.append((b"content-language", locale.encode("ascii")))
                    message = {**message, "headers": headers}
            await send(message)

        with use_locale(locale):
            await self.app(scope, receive, send_with_language)
