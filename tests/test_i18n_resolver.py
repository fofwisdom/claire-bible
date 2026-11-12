"""Locale resolution chains and validators (I18N design Section 3, unit IP1-1)."""

from __future__ import annotations

import asyncio

import pytest
from pydantic import ValidationError

from claire.config import Settings
from claire.i18n import InvalidLocale, get_locale, negotiate_locale, parse_accept_language, parse_explicit_locale, resolve_bot_locale, resolve_cli_locale, resolve_cli_locale_with_source, resolve_web_locale, resolve_web_locale_with_source, set_locale, use_locale


@pytest.mark.parametrize(
    ("header", "expected"),
    [
        (None, None),
        ("", None),
        ("ko-KR,ko;q=0.9,en-US;q=0.8,en;q=0.7", "ko"),
        ("en-US,en;q=0.9,ko;q=0.8", "en"),
        ("ja,ko;q=0.5,en;q=0.4", "ko"),
        ("en;q=0.3,ko;q=0.9", "ko"),
        ("ko;q=0.5,en;q=0.5", "ko"),
        ("ko;q=0,en;q=0.1", "en"),
        ("ko;q=0", None),
        ("ko;q=1.5,en;q=0.2", "en"),
        ("ko;q=0.1234,en;q=0.2", "en"),
        ("ko;q=abc,;;,en;q=0.2", "en"),
        ("*,ja;q=0.9,fr", None),
        ("KO-kr", "ko"),
    ],
)
def test_accept_language_follows_the_q_factors(header: str | None, expected: str | None) -> None:
    assert parse_accept_language(header) == expected


def test_web_chain_priority() -> None:
    full = {"account_locale": "ko", "cookie_locale": "en", "accept_language": "en", "settings_lang": "en"}
    assert resolve_web_locale_with_source(**full) == ("ko", "account")
    assert resolve_web_locale_with_source(**{**full, "account_locale": None, "cookie_locale": "ko"}) == ("ko", "cookie")
    # An unsupported cookie is ignored and the chain continues.
    assert resolve_web_locale_with_source(account_locale=None, cookie_locale="ja", accept_language="ko-KR", settings_lang="en") == ("ko", "accept-language")
    assert resolve_web_locale_with_source(account_locale=None, cookie_locale=None, accept_language="ja", settings_lang="ko") == ("ko", "config")
    assert resolve_web_locale_with_source(account_locale=None, cookie_locale=None, accept_language=None, settings_lang="") == ("en", "default")
    assert resolve_web_locale(account_locale=None, cookie_locale="ko-KR", accept_language=None, settings_lang="") == "ko"


def test_bot_chain_priority() -> None:
    assert resolve_bot_locale(owner_locale="en", language_code="ko", settings_lang="ko") == "en"
    assert resolve_bot_locale(owner_locale=None, language_code="ko-KR", settings_lang="en") == "ko"
    assert resolve_bot_locale(owner_locale=None, language_code="ja", settings_lang="ko") == "ko"
    assert resolve_bot_locale(owner_locale=None, language_code=None, settings_lang="") == "en"


def test_cli_chain_priority() -> None:
    env = {"LC_ALL": "", "LC_MESSAGES": "ko_KR.UTF-8", "LANG": "en_US.UTF-8"}
    assert resolve_cli_locale_with_source(cli_lang="en", settings_lang="ko", environ=env) == ("en", "flag")
    assert resolve_cli_locale_with_source(cli_lang=None, settings_lang="en", environ=env) == ("en", "config")
    assert resolve_cli_locale_with_source(cli_lang=None, settings_lang="", environ=env) == ("ko", "env")
    # LC_ALL has priority. The value C stops the POSIX step: LANG is not read.
    assert resolve_cli_locale_with_source(cli_lang=None, settings_lang="", environ={"LC_ALL": "C", "LANG": "ko_KR.UTF-8"}) == ("en", "default")
    assert resolve_cli_locale(cli_lang=None, settings_lang="", environ={}) == "en"


def test_explicit_selection_is_strict_and_negotiation_is_lenient() -> None:
    assert parse_explicit_locale(None) is None and parse_explicit_locale("") is None
    assert parse_explicit_locale("ko") == "ko" and parse_explicit_locale(" EN ") == "en"
    for bad in ("ja", "ko-KR", "english", 123, True, ["ko"]):
        with pytest.raises(InvalidLocale):
            parse_explicit_locale(bad)
    assert negotiate_locale("ko_KR.UTF-8") == "ko" and negotiate_locale("en-US") == "en"
    assert negotiate_locale("C") is None and negotiate_locale("ja") is None and negotiate_locale(None) is None and negotiate_locale("../ko") is None


def test_claire_lang_setting_rejects_unsupported_values(monkeypatch: pytest.MonkeyPatch) -> None:
    # The POSIX variable LANG must not reach the setting.
    monkeypatch.setenv("LANG", "ja_JP.UTF-8")
    monkeypatch.delenv("CLAIRE_LANG", raising=False)
    assert Settings(_env_file=None).ui_lang == ""
    assert Settings(_env_file=None, CLAIRE_LANG=" KO ").ui_lang == "ko"
    with pytest.raises(ValidationError):
        Settings(_env_file=None, CLAIRE_LANG="ja")


def test_locale_context_is_isolated_between_tasks() -> None:
    async def worker(locale: str, gate: asyncio.Event) -> str:
        with use_locale(locale):
            await gate.wait()
            return await asyncio.to_thread(get_locale)

    async def run() -> list[str]:
        gate = asyncio.Event()
        tasks = [asyncio.create_task(worker("ko", gate)), asyncio.create_task(worker("en", gate))]
        await asyncio.sleep(0)
        gate.set()
        return [await t for t in tasks] + [get_locale()]

    assert asyncio.run(run()) == ["ko", "en", "en"]
    with use_locale("ja"):
        assert get_locale() == "en"
    with use_locale("en"):
        assert set_locale("ko") == "ko"
    assert get_locale() == "en"
