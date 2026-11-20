"""Telegram bot and push notification localization (I18N design Section 7, unit IP3-1)."""

from __future__ import annotations

import ast
import datetime
import io
import re
import tokenize
from dataclasses import dataclass, field
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
import telegram
from starlette.testclient import TestClient

from claire import notify, telegram_bot
from claire.config import Settings
from claire.i18n import get_locale, t
from claire.store import db as dbm
from claire.store.accounts import AccountStore

SRC = Path(telegram_bot.__file__).resolve().parent
_HANGUL = re.compile(r"[ᄀ-ᇿ㄰-㆏가-힣]")


def _hangul_outside_comments(path: Path) -> list[tuple[int, str]]:
    """The tokens of ``path`` that contain Hangul and that are not comments or docstrings."""
    source = path.read_text(encoding="utf-8")
    docstring_lines: set[int] = set()
    for node in ast.walk(ast.parse(source)):
        if isinstance(node, (ast.Module, ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)) and node.body:
            first = node.body[0]
            if isinstance(first, ast.Expr) and isinstance(first.value, ast.Constant) and isinstance(first.value.value, str):
                docstring_lines.update(range(first.lineno, (first.end_lineno or first.lineno) + 1))
    found = []
    for tok in tokenize.generate_tokens(io.StringIO(source).readline):
        if tok.type == tokenize.COMMENT or tok.start[0] in docstring_lines:
            continue
        if _HANGUL.search(tok.string):
            found.append((tok.start[0], tok.string))
    return found


@pytest.mark.parametrize("name", ["telegram_bot.py", "notify.py"])
def test_bot_sources_have_no_hangul_outside_comments(name: str) -> None:
    # Interface text is in the catalogs. New Korean text in the code fails here.
    assert _hangul_outside_comments(SRC / name) == []


def test_hangul_check_finds_text_in_code(tmp_path: Path) -> None:
    path = tmp_path / "sample.py"
    path.write_text('"""문서."""\n# 주석\nA = "값"\nB = f"{A} 처리"\n', encoding="utf-8")
    assert [line for line, _ in _hangul_outside_comments(path)] == [3, 4]


def _telegram_update(language_code: str | None) -> telegram.Update:
    user = telegram.User(id=42, first_name="User", is_bot=False, language_code=language_code)
    message = telegram.Message(message_id=1, date=datetime.datetime.now(datetime.timezone.utc), chat=telegram.Chat(id=42, type="private"), from_user=user)
    return telegram.Update(update_id=1, message=message)


@pytest.mark.parametrize(
    ("owner_locale", "language_code", "settings_lang", "expected"),
    [
        ("ko", "en-US", "en", "ko"),  # The owner account locale is first.
        ("en", "ko", "ko", "en"),
        (None, "ko-KR", "en", "ko"),  # Then the language_code of the Telegram user.
        (None, "en", "ko", "en"),
        (None, "fr", "ko", "ko"),  # A language that is not supported continues to CLAIRE_LANG.
        (None, None, "ko", "ko"),
        (None, None, "", "en"),  # Then the default.
        (None, "fr", "", "en"),
    ],
)
def test_update_locale_resolution_chain(owner_locale: str | None, language_code: str | None, settings_lang: str, expected: str) -> None:
    update = _telegram_update(language_code)
    assert telegram_bot.resolve_update_locale(update, owner_locale=owner_locale, settings=SimpleNamespace(ui_lang=settings_lang)) == expected


def test_update_locale_ignores_values_that_are_not_text() -> None:
    # An update without a user (for example a channel post) and a settings object without ui_lang.
    assert telegram_bot.resolve_update_locale(SimpleNamespace(effective_user=None), owner_locale=None, settings=object()) == "en"
    assert telegram_bot.resolve_update_locale(MagicMock(), owner_locale=None, settings=MagicMock()) == "en"


def _bot_settings(tmp_path: Path, **overrides: object) -> Settings:
    data_dir = tmp_path / "data"
    data_dir.mkdir(parents=True, exist_ok=True)
    values: dict[str, object] = {"CLAIRE_DB_PATH": str(data_dir / "claire.db"), "CLAIRE_VAULT_PATH": str(tmp_path / "vault"), "CLAIRE_PROVIDER": "mock", "telegram_bot_token": "12345:" + "A" * 35, **overrides}
    settings = Settings(**values)
    conn = dbm.connect(settings.db_file)
    dbm.init_db(conn)
    conn.close()
    return settings


def _set_admin_locale(db_file: Path, locale: str | None) -> None:
    conn = dbm.connect(db_file)
    try:
        AccountStore(conn).update_locale(dbm.ADMIN_ACCOUNT_ID, locale)
        conn.commit()
    finally:
        conn.close()


def _fake_update(language_code: str | None) -> tuple[MagicMock, AsyncMock]:
    msg = AsyncMock()
    msg.reply_text = AsyncMock(return_value=AsyncMock())
    update = MagicMock()
    update.effective_user = SimpleNamespace(id=42, language_code=language_code)
    update.effective_message = msg
    update.message = msg
    update.callback_query = None
    return update, msg


def _handlers(app: object) -> dict[str, object]:
    return {h.callback.__name__: h.callback for h in app.handlers[0]}  # type: ignore[attr-defined]


async def test_bot_handlers_reply_in_the_locale_of_the_update(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """End to end: the server bot gets fake updates and replies in English and in Korean."""
    monkeypatch.delenv("CLAIRE_LANG", raising=False)
    settings = _bot_settings(tmp_path)
    app = telegram_bot.build_app(settings)
    handlers = _handlers(app)
    ctx = MagicMock()
    ctx.args = []

    expected_text = {"en": "Usage: /search <keyword>", "ko": "사용법: /search <키워드>"}
    for language_code, locale in (("en-US", "en"), ("ko-KR", "ko"), (None, "en"), ("fr", "en")):
        update, msg = _fake_update(language_code)
        await handlers["on_search"](update, ctx)
        assert msg.reply_text.call_args[0][0] == expected_text[locale]
        update, msg = _fake_update(language_code)
        await handlers["on_help"](update, ctx)
        assert msg.reply_text.call_args[0][0] == t("bot.help.text", locale=locale, repo=settings.effective_source_base_url)
    # The handler restores the locale of the context.
    assert get_locale() == "en"

    # A callback toast and the edited message use the locale of the user that pushes the button.
    for language_code, cancelled in (("en", "The ingest is cancelled."), ("ko", "적재가 취소되었습니다.")):
        update, _msg = _fake_update(language_code)
        query = AsyncMock()
        query.data = "igc:no-such-token"
        query.from_user = SimpleNamespace(id=42)
        update.callback_query = query
        await handlers["on_callback"](update, ctx)
        assert query.answer.call_args[0][0] == cancelled
        assert query.edit_message_text.call_args[0][0] == "❌ " + cancelled

    # The command menu: the default menu and one menu for each Telegram language.
    bot = AsyncMock()
    await app.post_init(SimpleNamespace(bot=bot))
    menus = {call.kwargs.get("language_code"): {c.command: c.description for c in call.args[0]} for call in bot.set_my_commands.call_args_list}
    assert set(menus) == {None, "en", "ko"}
    assert menus[None]["search"] == menus["en"]["search"] == "Search + summary"
    assert menus["ko"]["search"] == "검색 + 요약"
    assert list(menus["ko"]) == list(telegram_bot.COMMAND_MENU)

    # The locale of the bot owner account is before the language_code of the Telegram user.
    _set_admin_locale(settings.db_file, "ko")
    update, msg = _fake_update("en-US")
    await handlers["on_search"](update, ctx)
    assert msg.reply_text.call_args[0][0] == expected_text["ko"]
    bot = AsyncMock()
    await app.post_init(SimpleNamespace(bot=bot))
    assert {call.args[0][0].description for call in bot.set_my_commands.call_args_list} == {t("bot.command.kb", locale="ko")}
    _set_admin_locale(settings.db_file, "en")
    update, msg = _fake_update("ko")
    await handlers["on_search"](update, ctx)
    assert msg.reply_text.call_args[0][0] == expected_text["en"]


async def test_bot_uses_claire_lang_after_the_telegram_language(tmp_path: Path) -> None:
    settings = _bot_settings(tmp_path, CLAIRE_LANG="ko")
    handlers = _handlers(telegram_bot.build_app(settings))
    ctx = MagicMock()
    ctx.args = []
    for language_code, expected in ((None, "사용법: /search <키워드>"), ("de", "사용법: /search <키워드>"), ("en", "Usage: /search <keyword>")):
        update, msg = _fake_update(language_code)
        await handlers["on_search"](update, ctx)
        assert msg.reply_text.call_args[0][0] == expected


async def test_member_bot_resolves_the_locale_of_its_owner_account(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from claire.store.telegram_bots import TelegramBotProfile
    from claire.store.knowledge_base import KnowledgeBaseManager

    monkeypatch.delenv("CLAIRE_LANG", raising=False)
    settings = _bot_settings(tmp_path, CLAIRE_MULTI_THEME=True, telegram_bot_token="")
    conn = dbm.connect(settings.db_file)
    member = conn.execute("INSERT INTO accounts (public_id, username, role, status, created_at, updated_at) VALUES (223456789012, 'member_alpha', 'member', 'active', 0, 0)").lastrowid
    conn.commit()
    conn.close()
    KnowledgeBaseManager(settings).define_kb("Member theme", account_id=member, is_personal=True)
    profile = TelegramBotProfile(account_id=member, bot_id=7100000001, token="7100000001:" + "A" * 35, allowed_user_ids=(42,), fingerprint="abcd1234")
    handlers = _handlers(telegram_bot.build_app(settings, profile=profile))
    ctx = MagicMock()
    ctx.args = []

    # The refusal of an operator command: the Telegram language, then the locale of the member account. The locale of the administrator account has no effect.
    _set_admin_locale(settings.db_file, "ko")
    update, msg = _fake_update("en")
    await handlers["on_web"](update, ctx)
    assert msg.reply_text.call_args[0][0] == t("bot.error.member_refusal", locale="en")
    update, msg = _fake_update("ko")
    await handlers["on_web"](update, ctx)
    assert msg.reply_text.call_args[0][0] == t("bot.error.member_refusal", locale="ko")
    conn = dbm.connect(settings.db_file)
    AccountStore(conn).update_locale(member, "ko")
    conn.commit()
    conn.close()
    update, msg = _fake_update("en")
    await handlers["on_web"](update, ctx)
    assert msg.reply_text.call_args[0][0] == t("bot.error.member_refusal", locale="ko")
    assert t("bot.error.member_refusal", locale="ko") != t("bot.error.member_refusal", locale="en")


def test_push_locale_chain() -> None:
    assert notify.push_locale("ko", SimpleNamespace(ui_lang="en")) == "ko"
    assert notify.push_locale(None, SimpleNamespace(ui_lang="ko")) == "ko"
    assert notify.push_locale(None, SimpleNamespace(ui_lang="")) == "en"
    assert notify.push_locale(None, None) == "en"


@dataclass
class _ApiSettings:
    db_file: Path
    data_dir: Path
    environment: str = "development"
    public_url: str = "http://127.0.0.1:8765"
    inject_host: str = "127.0.0.1"
    inject_port: int = 8765
    inject_token: str = "owner-" + ("o" * 32)
    readonly_token: str = "readonly-" + ("r" * 32)
    cors_allowed_origins: str = ""
    anonymous_readonly: bool = False
    effective_provider: str = "mock"
    telegram_bot_token: str = "fake_bot_token"
    telegram_owner_chat_id: int = 0
    allowed_user_ids: set[int] = field(default_factory=lambda: {12345})
    ui_lang: str = ""


def test_oauth_approval_push_localizes_for_the_administrator_account(tmp_path: Path) -> None:
    """The API server sends the OAuth client approval prompt in the locale of the target account, then CLAIRE_LANG, then English."""
    from claire.api import server

    db_file = tmp_path / "test.db"
    conn = dbm.connect(db_file)
    dbm.init_db(conn)
    dbm.register_oauth_client(conn, client_id="test_client", client_secret="secret", client_name="Test <Client>", redirect_uris=["https://example.com/cb"])
    conn.close()
    settings = _ApiSettings(db_file=db_file, data_dir=tmp_path / "data")
    app = server.create_app(settings, SimpleNamespace(provider=SimpleNamespace(name="stub")))
    body = {"client_id": "test_client", "redirect_uri": "https://example.com/cb", "code_challenge": "abc", "code_challenge_method": "S256", "state": "s123"}

    def push(client: TestClient, post: AsyncMock) -> dict:
        post.reset_mock()
        resp = client.post("/oauth/authorize/telegram-push", json=body)
        assert resp.status_code == 200
        return post.call_args.kwargs["json"]

    with patch("httpx.AsyncClient.post", new_callable=AsyncMock, return_value=SimpleNamespace(status_code=200)) as post:
        with TestClient(app, base_url=settings.public_url) as client:
            sent = push(client, post)
            assert sent["text"].startswith("🤖 <b>Claire MCP connection approval request</b>")
            assert "• Client: <b>Test &lt;Client&gt;</b>" in sent["text"]
            assert [b["text"] for b in sent["reply_markup"]["inline_keyboard"][0]] == ["✅ Approve", "❌ Deny"]
            nonce = sent["reply_markup"]["inline_keyboard"][0][0]["callback_data"].split(":", 1)[1]
            assert sent["reply_markup"]["inline_keyboard"][0][1]["callback_data"] == f"oauth_deny:{nonce}"

            settings.ui_lang = "ko"
            sent = push(client, post)
            assert sent["text"].startswith("🤖 <b>Claire MCP 연결 승인 요청</b>")
            assert "• 클라이언트: <b>Test &lt;Client&gt;</b>" in sent["text"]
            assert [b["text"] for b in sent["reply_markup"]["inline_keyboard"][0]] == ["✅ 승인 (Approve)", "❌ 거절 (Deny)"]

            # The locale of the administrator account is before CLAIRE_LANG.
            _set_admin_locale(db_file, "en")
            sent = push(client, post)
            assert sent["text"] == t("notify.oauth.request", locale="en", client="Test &lt;Client&gt;")
            settings.ui_lang = ""
            _set_admin_locale(db_file, "ko")
            sent = push(client, post)
            assert sent["text"] == t("notify.oauth.request", locale="ko", client="Test &lt;Client&gt;")
