"""The button "Contribute to this theme" of the Telegram ingest reply (SHARED_KNOWLEDGE_BASES_AND_THEMES_DESIGN.md Sections 8.7 rule 2 and 13.4)."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from tests.test_contributions import EXTRACTION, _personal_document, world  # noqa: F401 (fixtures)

from claire.telegram import bot as telegram_bot
from claire.core import ids
from claire.i18n import t
from claire.ingest.service import IngestReport
from claire.store import db as dbm
from claire.store import kb_collab
from claire.store import themes as themes_store
from claire.store.telegram_bots import TelegramBotProfile

ADMIN, BEN = 1, 3


def test_callback_data_obeys_the_limit_of_telegram() -> None:
    notice = {"code": "fiction_like_document", "document": "doc_0123456789abcdef", "theme": str((1 << 62) + 1)}
    data = telegram_bot.contribute_callback_data(notice)
    assert data == f"ctb:{notice['theme']}:doc_0123456789abcdef" and len(data.encode("utf-8")) <= 64
    # No button when the data is too long, and for each other notice.
    assert telegram_bot.contribute_callback_data({**notice, "document": "d" * 60}) is None
    assert telegram_bot.contribute_callback_data({"code": "fiction_standing_message", "message": "x"}) is None


def _contributions(world, kb_id: int) -> list[tuple]:  # noqa: F811
    conn = dbm.connect_existing(world.settings.db_file, readonly=True)
    try:
        return [(r["source_document_id"], r["contributor_account_id"], r["origin"], r["state"]) for r in kb_collab.list_contributions(conn, kb_id)]
    finally:
        conn.close()


def _bot_handlers(world, account_id: int) -> dict:  # noqa: F811
    """The handlers of the account bot of ``account_id``. Only the Telegram user 42 is in its list."""
    profile = TelegramBotProfile(account_id=account_id, bot_id=7100000000 + account_id, token=f"{7100000000 + account_id}:" + "A" * 35, allowed_user_ids=(42,), fingerprint="abcd1234")
    return {h.callback.__name__: h.callback for h in telegram_bot.build_app(world.settings, profile=profile).handlers[0]}


def _callback(data: str, user_id: int) -> tuple[MagicMock, AsyncMock]:
    query = AsyncMock()
    query.data = data
    query.from_user = SimpleNamespace(id=user_id)
    update = MagicMock()
    update.effective_user = SimpleNamespace(id=user_id, language_code="en")
    update.callback_query = query
    return update, query


async def test_e2e_ingest_reply_has_the_button_and_its_callback_contributes(world, monkeypatch: pytest.MonkeyPatch) -> None:  # noqa: F811
    monkeypatch.delenv("CLAIRE_LANG", raising=False)
    conn = dbm.connect(world.settings.db_file)
    try:
        theme = themes_store.create_theme(conn, label="Dragon stories", knowledge_type="real")
        themes_store.update_theme(conn, theme["id"], state="active")
        themes_store.add_theme_member(conn, theme_id=theme["id"], kb_id=world.kb["id"], source="admin", pinned=True)
        conn.commit()
    finally:
        conn.close()
    number = ids.format_public_id(theme["number"])
    document = _personal_document(world.settings, world.manager, ADMIN, "A story about a dragon and a knight in an invented world. " * 3, url="https://example.org/dragon", extraction=EXTRACTION)
    ben_document = _personal_document(world.settings, world.manager, BEN, "A second story about a wizard and a tower in an invented world. " * 3, url="https://example.org/wizard", extraction=EXTRACTION)
    notice = {"code": "fiction_like_document", "document": document, "theme": number, "theme_label": "Dragon stories"}
    report = IngestReport(title="Dragon", document_id=document, notices=[notice])
    monkeypatch.setattr("claire.ingest.service.IngestService.ingest", lambda *args, **kwargs: report)

    # The ingest reply of the bot of the admin account has the notice and the button.
    handlers = _bot_handlers(world, ADMIN)
    status_msg = AsyncMock()
    msg = AsyncMock()
    msg.reply_text = AsyncMock(return_value=status_msg)
    update = MagicMock()
    update.effective_user = SimpleNamespace(id=42, language_code="en")
    update.effective_chat = SimpleNamespace(id=42)
    update.message = update.effective_message = msg
    update.update_id = 501
    ctx = MagicMock()
    ctx.args = ["https://example.org/dragon"]
    await handlers["on_ingest"](update, ctx)
    final = status_msg.edit_text.call_args
    assert t("campaign.fiction_like", locale="en", theme="Dragon stories") in final.args[0]
    buttons = [button for row in final.kwargs["reply_markup"].inline_keyboard for button in row]
    button = next(b for b in buttons if b.text == t("campaign.contribute_action", locale="en"))
    assert button.callback_data == f"ctb:{number}:{document}"

    # A user that is not in the list of the bot contributes nothing.
    stranger, stranger_query = _callback(button.callback_data, 7)
    await handlers["on_callback"](stranger, None)
    stranger_query.message.reply_text.assert_not_called()
    assert _contributions(world, world.kb["id"]) == []

    # The callback contributes for the account of the bot. The admin needs no approval: the merge job runs and the contribution is visible.
    allowed, query = _callback(button.callback_data, 42)
    await handlers["on_callback"](allowed, None)
    answer = query.message.reply_text.call_args.args[0]
    assert t("campaign.contribute_done", locale="en", count=1) in answer and f"• Cities: {t('kb.state.visible', locale='en')}" in answer
    assert _contributions(world, world.kb["id"]) == [(document, ADMIN, "bulk", "visible")]

    # A second press makes no second contribution: the answer gives the reason.
    again, again_query = _callback(button.callback_data, 42)
    await handlers["on_callback"](again, None)
    answer = again_query.message.reply_text.call_args.args[0]
    assert t("campaign.contribute_none", locale="en") in answer and t("kb.err.contribution_duplicate", locale="en") in answer
    assert len(_contributions(world, world.kb["id"])) == 1

    # An unknown theme gives the failure text and no contribution.
    unknown, unknown_query = _callback(f"ctb:1:{document}", 42)
    await handlers["on_callback"](unknown, None)
    assert unknown_query.message.reply_text.call_args.args[0] == t("campaign.contribute_failed", locale="en", error=t("kb.err.theme_not_found", locale="en"))

    # The bot of ben contributes a document of ben. ben is a member and not a reviewer of Cities: the contribution waits for a decision. The answer is in the locale of the update.
    ben_handlers = _bot_handlers(world, BEN)
    ben_update, ben_query = _callback(f"ctb:{number}:{ben_document}", 42)
    ben_update.effective_user = SimpleNamespace(id=42, language_code="ko")
    await ben_handlers["on_callback"](ben_update, None)
    assert f"• Cities: {t('kb.state.pending', locale='ko')}" in ben_query.message.reply_text.call_args.args[0]
    assert (ben_document, BEN, "bulk", "pending") in _contributions(world, world.kb["id"])
    # The bot of ben does not contribute a document of another account.
    other_update, other_query = _callback(f"ctb:{number}:{document}", 42)
    await ben_handlers["on_callback"](other_update, None)
    assert other_query.message.reply_text.call_args.args[0] == t("campaign.contribute_failed", locale="en", error=t("kb.err.document_not_found", locale="en"))
    assert len(_contributions(world, world.kb["id"])) == 2
