"""End-to-end interface language across the web surfaces (I18N design unit IP3-3).

One owner moves through the workspace (/), /syndication, the console, and the login page. Each page follows the same resolution chain: account preference, then the ``claire_locale`` cookie, then ``Accept-Language``, then ``CLAIRE_LANG``, then ``en``.
"""

from __future__ import annotations

import re

from tests.test_i18n_api import _activate_admin, _locale_cookies, _session, client, settings  # noqa: F401  (pytest fixtures)

PAGES = {
    "/": {"en": "<title>Claire Bible — Home</title>", "ko": "<title>Claire Bible — 홈</title>"},
    # The view text of /syndication comes from the inline catalog of the page.
    "/syndication": {"en": '"synd.kb.refresh": "Refresh"', "ko": '"synd.kb.refresh": "새로고침"'},
    "/settings/account": {"en": "Account information", "ko": "계정 정보"},
}


def _lang(page_text: str) -> str:
    match = re.search(r'<html lang="([a-z]+)"', page_text)
    assert match is not None
    return match.group(1)


def _check_all(client, token: str, locale: str, **kwargs) -> None:
    for path, markers in PAGES.items():
        page = client.request("GET", path, session=token, **kwargs)
        assert page.status_code == 200, path
        assert _lang(page.text) == locale, (path, locale)
        assert page.headers["content-language"] == locale, path
        assert markers[locale] in page.text, (path, locale)


def test_each_web_surface_follows_the_interface_language_of_the_owner(client, settings) -> None:  # noqa: F811
    _activate_admin(settings)
    token = _session(settings)

    # 1. No preference, no cookie, no header: the default is en.
    _check_all(client, token, "en")
    # 2. The browser header asks for Korean.
    _check_all(client, token, "ko", headers={"Accept-Language": "ko-KR,ko;q=0.9,en;q=0.5"})

    # 3. The owner stores Korean in /settings/account. A browser that asks for English still gets Korean, on each surface.
    saved = client.request("PATCH", "/api/me", session=token, json={"locale": "ko"})
    assert saved.status_code == 200 and saved.json()["locale"] == "ko"
    _check_all(client, token, "ko", headers={"Accept-Language": "en-US"})

    # 4. After logout the session has no account: the cookie of step 3 keeps Korean on the public login page, and without the cookie the header decides.
    cookie = _locale_cookies(saved)[0].split(";")[0].split("=")[1]
    assert client.request("POST", "/logout", session=token, cookies={"claire_locale": cookie}).status_code == 303
    login = client.request("GET", "/login", cookies={"claire_locale": cookie}, headers={"Accept-Language": "en-US"})
    assert _lang(login.text) == "ko"
    assert _lang(client.request("GET", "/login", headers={"Accept-Language": "en-US"}).text) == "en"

    # 5. Back to automatic: the header decides again.
    token = _session(settings)
    reset = client.request("PATCH", "/api/me", session=token, json={"locale": None})
    assert reset.json()["locale"] is None
    _check_all(client, token, "en", headers={"Accept-Language": "en-US"})
    _check_all(client, token, "ko", headers={"Accept-Language": "ko"})
