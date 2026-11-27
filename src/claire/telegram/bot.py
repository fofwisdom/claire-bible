"""텔레그램 진입점 (long-polling).

링크/문서/키워드/PDF를 받으면 IngestService(공유 통로)로 적재하고, 링크 확장 후보가 있으면 inline 버튼으로 "가져오기"를 제안한다. /search 로 검색. 적재 로직은 IngestService 에 있어 inject API · CLI 와 완전히 동일한 경로를 탄다.
"""

from __future__ import annotations

import asyncio
import functools
import logging
from logging.handlers import RotatingFileHandler
from pathlib import Path
from urllib.parse import urlsplit

from typing import Any
from html import escape as _h
import re as _re


def _plain(html_text: str) -> str:
    """Plain text of a Telegram HTML message, for the fallback when Telegram refuses the HTML (RD24: no Markdown)."""
    import html as _html

    return _html.unescape(_re.sub(r"<[^>]+>", "", html_text))

from ..core.config import Settings, get_settings
from ..i18n import SUPPORTED_LOCALES, resolve_bot_locale, use_locale
from ..i18n import t as _t
from ..store import db as dbm

log = logging.getLogger("claire.telegram")


class AccountThemeScope:
    """텔레그램 봇이 보는 테마 관리자. 봇 소유 계정의 테마만 드러낸다(유저 단위 봇).

    다른 계정의 테마는 없는 테마와 같게 다룬다(``KnowledgeBaseNotFound``). 그래서 ``#태그``·``/theme`` 선택·재생성 대상·공유 토큰 어느 경로로도
    다른 계정 테마에 적재하거나 다른 계정의 컨텍스트로 작업하지 않는다. 나머지 메서드는 감싼 관리자에게 넘긴다.
    """

    def __init__(self, inner: Any, account_id: int = dbm.ADMIN_ACCOUNT_ID) -> None:
        self._inner = inner
        self.account_id = int(account_id)

    def __getattr__(self, name: str) -> Any:
        return getattr(self._inner, name)

    def _owned(self, theme: Any) -> bool:
        return getattr(theme, "account_id", dbm.ADMIN_ACCOUNT_ID) == self.account_id

    def get_kb(self, theme_ref: Any = None, *args: Any, **kwargs: Any) -> Any:
        from ..store.knowledge_base import KnowledgeBaseNotFound

        if theme_ref is None or str(theme_ref).strip() == "":
            return self._inner.personal_kb_for(self.account_id)
        theme = self._inner.get_kb(theme_ref, *args, **kwargs)
        if not self._owned(theme):
            raise KnowledgeBaseNotFound(theme_ref)
        return theme

    def list_kbs(self, *args: Any, **kwargs: Any) -> list[Any]:
        return [t for t in self._inner.list_kbs(*args, **kwargs) if self._owned(t)]

    # Search and read (SHARED_KNOWLEDGE_BASES_AND_THEMES_DESIGN.md Section 13.4): ``/kb`` selects the personal knowledge base or a shared knowledge base. Ingest goes to the personal knowledge base only, so ``get_kb`` and ``list_kbs`` stay with the knowledge base of the account.
    def get_readable_kb(self, theme_ref: Any = None, *args: Any, **kwargs: Any) -> Any:
        from ..store.knowledge_base import KnowledgeBaseNotFound

        if theme_ref is None or str(theme_ref).strip() == "":
            return self._inner.personal_kb_for(self.account_id)
        theme = self._inner.get_kb(theme_ref, *args, **kwargs)
        if not self._owned(theme) and not getattr(theme, "is_shared", False):
            raise KnowledgeBaseNotFound(theme_ref)
        return theme

    def readable_kbs(self, *args: Any, **kwargs: Any) -> list[Any]:
        return [t for t in self._inner.list_kbs(*args, **kwargs) if self._owned(t) or getattr(t, "is_shared", False)]

    def personal_kb(self) -> Any:
        return self._inner.personal_kb_for(self.account_id)

    def _kb_id_owned(self, kb_id: Any) -> bool:
        from ..store.knowledge_base import KnowledgeBaseNotFound

        if kb_id is None:
            return False
        try:
            return self._owned(self._inner.get_kb(kb_id))
        except (KnowledgeBaseNotFound, KeyError, ValueError):
            return False

    def resolve_document_targets(self, *args: Any, **kwargs: Any) -> list[dict[str, Any]]:
        targets = self._inner.resolve_document_targets(*args, **kwargs) or []
        return [tg for tg in targets if self._kb_id_owned(tg.get("kb_id"))]

    def resolve_share_token(self, *args: Any, **kwargs: Any) -> Any:
        resolved = self._inner.resolve_share_token(*args, **kwargs)
        if resolved is None or not self._kb_id_owned(resolved[0]):
            return None
        return resolved


# 이전 이름. 관리자 봇도 같은 범위 규칙을 쓴다.
AdminThemeScope = AccountThemeScope

# 운영자(관리자 계정) 봇에서만 쓰는 명령. 멤버 계정 봇은 이 명령을 거부한다(서버 세션·전체 현황을 다루기 때문이다).
ADMIN_ONLY_COMMANDS: frozenset[str] = frozenset({"web", "webro", "status"})
# 운영자 봇에서만 받는 콜백(OAuth 승인 푸시).
ADMIN_ONLY_CALLBACK_PREFIXES: tuple[str, ...] = ("oauth_appr:", "oauth_deny:")
# The commands of the Telegram command menu, in menu order. The description of each command is the catalog key ``bot.command.<name>``.
COMMAND_MENU: tuple[str, ...] = ("kb", "help", "status", "support", "export", "repo", "search", "ingest", "web", "webro", "failed", "retry", "start")


def resolve_update_locale(update: Any, *, owner_locale: str | None, settings: Any) -> str:
    """Locale of one Telegram update (I18N design Section 7.1): the ``accounts.locale`` of the bot owner account, the ``language_code`` of the Telegram user, ``settings.ui_lang`` (``CLAIRE_LANG``), then the default."""
    user = getattr(update, "effective_user", None)
    language_code = getattr(user, "language_code", None)
    settings_lang = getattr(settings, "ui_lang", None)
    return resolve_bot_locale(
        owner_locale=owner_locale if isinstance(owner_locale, str) else None,
        language_code=language_code if isinstance(language_code, str) else None,
        settings_lang=settings_lang if isinstance(settings_lang, str) else None,
    )


def server_bot_profile(settings: Any) -> Any:
    """``TELEGRAM_BOT_TOKEN``(서버 봇)을 관리자 계정 봇 프로필로 만든다. 토큰이 없으면 None.

    허용 목록은 ``TELEGRAM_ALLOWED_USERS``이다. 비어 있으면 예전처럼 모두 허용한다(서버 봇 호환).
    """
    from ..store.telegram_bots import TelegramBotProfile, bot_id_from_token

    token = str(getattr(settings, "telegram_bot_token", "") or "").strip()
    if not token:
        return None
    return TelegramBotProfile(
        account_id=dbm.ADMIN_ACCOUNT_ID,
        bot_id=bot_id_from_token(token) or 0,
        token=token,
        allowed_user_ids=tuple(sorted(getattr(settings, "allowed_user_ids", None) or ())),
        fingerprint="server",
        source="server",
    )


_TELEGRAM_LOG_LOGGER_NAMES: tuple[str, ...] = ("claire.telegram", "telegram", "claire.ingest", "claire.extract", "claire_plugins", "httpx", "httpx2")


def setup_telegram_logging(data_dir: Path | str | None = None) -> Path | None:
    """Configure the rotating file logger for the Telegram bot (``data/logs/telegram.log``).

    Keep up to 3 rotating log files of 5 MB each and attach one scrubbed handler to the Telegram, ingest, extract, plugin, and HTTP loggers.
    Install the log scrubber first so provider error text cannot write keys to the log file (design Section 11.3).
    """
    from ..core.secret_scrub import SecretScrubFilter, install_log_scrubber

    install_log_scrubber()
    try:
        if data_dir is None:
            from ..core.config import get_settings
            data_dir = get_settings().data_dir
        log_dir = Path(data_dir) / "logs"
        log_dir.mkdir(parents=True, exist_ok=True)
        log_file = log_dir / "telegram.log"
        resolved_log_file = log_file.resolve()

        def _find_matching_handler(target_logger: logging.Logger) -> RotatingFileHandler | None:
            for existing in target_logger.handlers:
                base = getattr(existing, "baseFilename", None)
                if isinstance(existing, RotatingFileHandler) and base and Path(base).resolve() == resolved_log_file:
                    return existing
            return None

        handler: RotatingFileHandler | None = None
        for logger_name in _TELEGRAM_LOG_LOGGER_NAMES:
            handler = _find_matching_handler(logging.getLogger(logger_name))
            if handler is not None:
                break

        if handler is None:
            handler = RotatingFileHandler(log_file, maxBytes=5 * 1024 * 1024, backupCount=3, encoding="utf-8")
            fmt = logging.Formatter("%(asctime)s [%(levelname)s] [%(name)s] %(message)s")
            handler.setFormatter(fmt)
            handler.setLevel(logging.INFO)

        if not any(isinstance(f, SecretScrubFilter) for f in handler.filters):
            handler.addFilter(SecretScrubFilter())

        for logger_name in _TELEGRAM_LOG_LOGGER_NAMES:
            target_logger = logging.getLogger(logger_name)
            if _find_matching_handler(target_logger) is None:
                target_logger.addHandler(handler)
            if target_logger.level == logging.NOTSET or target_logger.level > logging.INFO:
                target_logger.setLevel(logging.INFO)

        return log_file
    except Exception as e:  # noqa: BLE001
        log.warning("Failed to setup telegram file logger: %s", e)
        return None


def _status_emoji(
    error,
    duplicate: bool = False,
    *,
    stt_error: str | None = None,
    detail_error: str | None = None,
    summary_error: str | None = None,
) -> str:
    """처리 결과 → 원본 메시지에 달 텔레그램 허용 reaction 이모지."""
    if error or stt_error or detail_error or summary_error:
        return "👎"
    if duplicate:
        return "👌"
    return "👍"  # 신규/갱신 완료


async def _run_with_ticker(status, label: str, work, interval: float = 5.0):
    """work(블로킹)를 스레드에서 실행하며 status 메시지를 5초마다 편집(진행 표시).

    emit_progress 콜백을 통해 파이프라인의 실시간 진행 단계(원문 수집, STT, LLM 추출 등)를 수신하고, 5초 주기로 Telegram 메시지를 갱신하여 텔레그램 부하 기준(초당 1회 이하)을 충족합니다."""
    stop = asyncio.Event()
    current_stage: str = ""
    last_sent_text: str = ""

    def on_progress(msg: str) -> None:
        nonlocal current_stage
        if msg:
            current_stage = str(msg).strip()

    def _worker():
        from ..extract.provider import set_progress_callback

        set_progress_callback(on_progress)
        try:
            return work()
        finally:
            set_progress_callback(None)

    async def tick():
        t = 0
        nonlocal last_sent_text
        while not stop.is_set():
            try:
                await asyncio.sleep(interval)
            except asyncio.CancelledError:
                break
            t += int(interval)
            stage_part = f"\n• {current_stage}" if current_stage else ""
            text = f"⏳ {label} ({t}s){stage_part}"
            if text != last_sent_text:
                try:
                    await status.edit_text(text)
                    last_sent_text = text
                except Exception:  # noqa: BLE001
                    pass  # 편집 실패(동일내용/rate)는 무시

    tk = asyncio.create_task(tick())
    try:
        return await asyncio.to_thread(_worker)
    finally:
        stop.set()
        tk.cancel()


async def _reply_extraction_flags_conflict(msg) -> None:
    """The message has --force-provider and --agent-client: Claire does not ingest it."""
    try:
        await msg.reply_text(_t("bot.ingest.extraction_flags_conflict"))
    except Exception:  # noqa: BLE001
        log.debug("extraction flags notice failed", exc_info=True)


async def _reply_invalid_rendition(msg, error: Exception) -> None:
    """Tell the sender that a --rendition flag has an invalid or empty template name."""
    from ..extract.rendition_templates import ADDITIONAL_RENDITION_TEMPLATES

    try:
        await msg.reply_text(_t("bot.ingest.invalid_rendition", error=str(error), allowed=", ".join(ADDITIONAL_RENDITION_TEMPLATES)))
    except Exception:  # noqa: BLE001
        log.debug("invalid rendition notice failed", exc_info=True)


async def _reply_focus_sealed(msg) -> None:
    """CT28: tell the sender that the text after the separator is not used as a focus."""
    try:
        await msg.reply_text(_t("bot.focus.sealed"))
    except Exception:  # noqa: BLE001
        log.debug("focus sealed notice failed", exc_info=True)


async def _react(msg, emoji: str) -> None:
    """원본 메시지에 reaction(best-effort — 불허 이모지/권한 실패는 무시)."""
    try:
        await msg.set_reaction(emoji)
    except Exception:  # noqa: BLE001
        pass


# Callback data of the button "Contribute to this theme": ``ctb:<theme number>:<document ID>``. Telegram accepts at most 64 bytes of callback data.
CONTRIBUTE_CALLBACK_PREFIX = "ctb:"
CALLBACK_DATA_MAX_BYTES = 64


def contribute_callback_data(notice: dict[str, Any]) -> str | None:
    """The callback data that contributes the document of a ``fiction_like_document`` notice to its theme (SHARED_KNOWLEDGE_BASES_AND_THEMES_DESIGN.md Section 8.7 rule 2). None for each other notice, and when the data is longer than the limit of Telegram."""
    if notice.get("code") != "fiction_like_document" or not notice.get("document") or not notice.get("theme"):
        return None
    data = f"{CONTRIBUTE_CALLBACK_PREFIX}{notice['theme']}:{notice['document']}"
    if len(data.encode("utf-8")) > CALLBACK_DATA_MAX_BYTES:
        log.warning("no contribute button for document %s: the callback data is longer than %d bytes", notice["document"], CALLBACK_DATA_MAX_BYTES)
        return None
    return data


_RENDITION_CALLBACK_SPECS: dict[str, tuple[str, str]] = {
    "concise": ("c", "bot.button.add_rendition_concise"),
    "academic": ("a", "bot.button.add_rendition_academic"),
}
_RENDITION_CODE_TO_TEMPLATE: dict[str, str] = {spec[0]: tpl for tpl, spec in _RENDITION_CALLBACK_SPECS.items()}


def _missing_rendition_buttons(db_file: Path | str, doc_id: str, kb_id: int) -> list[Any]:
    """Return one inline button row for additional rendition templates not yet stored for the document."""
    from telegram import InlineKeyboardButton

    from ..extract.rendition_templates import ADDITIONAL_RENDITION_TEMPLATES
    from ..store import document_renditions as renditions_store

    existing: set[str] = set()
    db_path = Path(db_file)
    if db_path.exists():
        try:
            conn = dbm.connect_existing(db_path, readonly=True)
            try:
                existing = {r.template for r in renditions_store.list_renditions(conn, doc_id, kb_id)}
            finally:
                conn.close()
        except Exception:  # noqa: BLE001
            log.debug("Failed to list renditions for doc_id=%s kb_id=%s", doc_id, kb_id, exc_info=True)
    row: list[Any] = []
    for tpl in ADDITIONAL_RENDITION_TEMPLATES:
        if tpl in existing:
            continue
        spec = _RENDITION_CALLBACK_SPECS.get(tpl)
        if spec is None:
            continue
        code, label_key = spec
        cb_data = f"rd:{kb_id}:{doc_id}:{code}"
        if len(cb_data.encode("utf-8")) > CALLBACK_DATA_MAX_BYTES:
            log.warning("Skip rendition button for document %s: callback_data exceeds %d bytes", doc_id, CALLBACK_DATA_MAX_BYTES)
            continue
        row.append(InlineKeyboardButton(_t(label_key), callback_data=cb_data))
    return row


def _format_rendition_results_line(items: list[dict[str, str]]) -> str:
    """Format one concise localized summary line for additional rendition results."""
    parts: list[str] = []
    for item in items:
        tpl = item.get("template", "")
        label = _t(f"rendition.template.{tpl}.label")
        if label == f"rendition.template.{tpl}.label":
            label = tpl
        state = item.get("state", "failed")
        if state in ("made", "existing", "queued"):
            parts.append(_t(f"bot.ingest.rendition_item.{state}", label=label))
        else:
            parts.append(_t("bot.ingest.rendition_item.failed", label=label, error=item.get("error") or "unknown"))
    return _t("bot.ingest.renditions_line", items=", ".join(parts))


def _markup_with_rows(reply_markup: Any, extra_rows: list[list[Any]]) -> Any:
    """The inline keyboard of ``reply_markup`` (None: no keyboard) with ``extra_rows`` after its rows."""
    from telegram import InlineKeyboardMarkup

    rows = [list(row) for row in (getattr(reply_markup, "inline_keyboard", None) or ())]
    return InlineKeyboardMarkup(rows + [list(row) for row in extra_rows])


async def _settle_status(
    status: Any,
    msg: Any = None,
    summary: str = "",
    cands: list | None = None,
    *extra_args: Any,
    has_error: bool = False,
    is_stt_failed: bool = False,
    is_detail_failed: bool = False,
    is_summary_failed: bool = False,
    is_duplicate: bool = False,
    retry_doc_id: str | None = None,
    cands_markup: Any = None,
    kb_id: int | None = 0,
    db_file: str | Path | None = None,
    inbox_id: int | None = None,
    service_pool: Any = None,
    theme_mgr: Any = None,
    extra_rows: list[list[Any]] | None = None,
    is_staged: bool = False,
    **kwargs: Any,
) -> None:
    """완료 처리:
    - 확장 후보가 있으면 진행 메시지를 결과+후보 선택 버튼으로 편집(버튼 보존)
    - 본문(detail) 생성 실패 시 진행 메시지에 재수집/재생성 원터치 버튼 제공
    - 비디오 자막 추출 실패 시 진행 메시지를 삭제하지 않고 재수집/재적재 원터치 버튼 제공
    - 이미 적재된 문서(중복/dedup)일 때 메시지를 삭제하지 않고 중복 안내 및 재생성/재수집 원터치 버튼 제공
    - 기타 에러 발생 시 진행 메시지를 삭제하지 않고 오류 안내 보존
    - 정상 완료이면서 확장 후보가 없을 때도 진행 메시지를 삭제하지 않고 완료 요약 및 열람 링크로 편집하여 보존
    - ``extra_rows``: rows of inline buttons that each result message gets after its own buttons (the action of a campaign notice)
    """
    if isinstance(status, int):
        inbox_id = status
        status = msg
        msg = summary
        summary = cands if isinstance(cands, str) else ""
        cands = extra_args[0] if extra_args else []
    if cands is None:
        cands = []

    log.info(
        "Settle status: doc_id=%s, has_error=%s, is_stt_failed=%s, is_detail_failed=%s, is_summary_failed=%s, is_duplicate=%s, cands=%d, kb_id=%s",
        retry_doc_id, has_error, is_stt_failed, is_detail_failed, is_summary_failed, is_duplicate, len(cands), kb_id,
    )

    async def _safe_send_or_edit(text: str, reply_markup: Any = None) -> None:
        if extra_rows:
            try:
                reply_markup = _markup_with_rows(reply_markup, extra_rows)
            except Exception as markup_err:  # noqa: BLE001
                log.warning("Failed to add the extra button rows: %s", markup_err)
        for attempt in range(2):
            try:
                if reply_markup is not None:
                    await status.edit_text(text, reply_markup=reply_markup)
                else:
                    await status.edit_text(text)
                log.info(
                    "Status message edited: status_id=%s, has_markup=%s",
                    getattr(status, "message_id", None),
                    reply_markup is not None,
                )
                return
            except Exception as edit_err:  # noqa: BLE001
                if attempt == 0:
                    await asyncio.sleep(0.5)
                else:
                    log.warning(
                        "Failed to edit status message (id=%s): %s",
                        getattr(status, "message_id", None),
                        edit_err,
                    )
        # Fallback: 원래 메시지에 답장 전송 (네트워크 오류 시 태스크 크래시 방지)
        for attempt in range(2):
            try:
                if reply_markup is not None:
                    await msg.reply_text(text, reply_markup=reply_markup)
                else:
                    await msg.reply_text(text)
                log.info(
                    "Fallback reply sent to msg_id=%s, has_markup=%s",
                    getattr(msg, "message_id", None),
                    reply_markup is not None,
                )
                return
            except Exception as reply_err:  # noqa: BLE001
                if attempt == 0:
                    await asyncio.sleep(0.5)
                else:
                    log.error(
                        "Failed to send fallback reply to msg_id=%s: %s",
                        getattr(msg, "message_id", None),
                        reply_err,
                    )

    if cands and cands_markup:
        await _safe_send_or_edit(summary, reply_markup=cands_markup)
    elif (is_summary_failed or is_detail_failed or is_stt_failed) and retry_doc_id:
        markup = None
        try:
            from telegram import InlineKeyboardButton, InlineKeyboardMarkup

            kb = []
            if is_summary_failed:
                kb.append([InlineKeyboardButton(_t("bot.button.regen_summary"), callback_data=f"rg:summ:{retry_doc_id}")])
            if is_detail_failed:
                kb.append([InlineKeyboardButton(_t("bot.button.regen_detail_source"), callback_data=f"rg:det:{retry_doc_id}")])
                kb.append([InlineKeyboardButton(_t("bot.button.refetch_full_regen"), callback_data=f"rg:full:{retry_doc_id}")])
            elif is_stt_failed:
                kb.append([InlineKeyboardButton(_t("bot.button.refetch_subtitles"), callback_data=f"rg:full:{retry_doc_id}")])
                kb.append([InlineKeyboardButton(_t("bot.button.regen_detail_text"), callback_data=f"rg:det:{retry_doc_id}")])
            markup = InlineKeyboardMarkup(kb)
        except Exception:  # noqa: BLE001
            markup = None
        await _safe_send_or_edit(summary, reply_markup=markup)
    elif is_duplicate and retry_doc_id:
        markup = None
        try:
            from telegram import InlineKeyboardButton, InlineKeyboardMarkup

            kb = [
                [InlineKeyboardButton(_t("bot.button.regen_detail_existing"), callback_data=f"rg:det:{retry_doc_id}")],
                [InlineKeyboardButton(_t("bot.button.refetch_full_regen"), callback_data=f"rg:full:{retry_doc_id}")],
            ]
            markup = InlineKeyboardMarkup(kb)
        except Exception:  # noqa: BLE001
            markup = None
        await _safe_send_or_edit(summary, reply_markup=markup)
    elif has_error or is_stt_failed or is_detail_failed or is_summary_failed or is_duplicate:
        await _safe_send_or_edit(summary)
    elif is_staged:
        await _safe_send_or_edit(summary)
    else:
        markup = None
        if retry_doc_id:
            try:
                from pathlib import Path
                from telegram import InlineKeyboardButton, InlineKeyboardMarkup

                from ..core.config import get_settings

                cfg = (
                    kwargs.get("settings")
                    or getattr(service_pool, "base_settings", None)
                    or getattr(service_pool, "s", None)
                    or (theme_mgr.settings if theme_mgr else get_settings())
                )
                base_url = (getattr(cfg, "public_url", "") or "").rstrip("/")

                if theme_mgr is None:
                    try:
                        from ..store.knowledge_base import get_kb_manager

                        theme_mgr = get_kb_manager(cfg)
                    except Exception:
                        theme_mgr = None

                target_db: Path | None = None
                target_kb_id: int = kb_id if kb_id is not None else 0
                if db_file:
                    target_db = Path(db_file)
                elif theme_mgr is not None and target_kb_id == 0:
                    try:
                        matches = theme_mgr.resolve_document_targets(doc_id=retry_doc_id)
                        if matches:
                            target_kb_id = matches[0].get("kb_id", 0)
                            if matches[0].get("db_file"):
                                target_db = Path(matches[0]["db_file"])
                    except Exception:
                        pass

                if target_db is None:
                    target_db = Path(cfg.db_file) if getattr(cfg, "db_file", None) else Path("data/claire.db")

                if base_url and target_db and target_db.exists():
                    conn = dbm.connect_existing(target_db, readonly=False)
                    try:
                        share_tok = dbm.create_doc_share(conn, retry_doc_id, kb_id=target_kb_id)
                    finally:
                        conn.close()
                    if share_tok:
                        share_url = f"{base_url}/silvercord/{share_tok}"
                        markup = InlineKeyboardMarkup(
                            [[InlineKeyboardButton(_t("bot.button.open_reader"), url=share_url)]]
                        )
            except Exception:  # noqa: BLE001
                markup = None
        await _safe_send_or_edit(summary, reply_markup=markup)


def classify_input(text: str) -> str:
    """입력 텍스트를 source_type 후보로 분류(사용자 안내용)."""
    t = (text or "").strip()
    if not t:
        return "empty"
    from ..ingest.registry import registry
    return registry.classify(t)


import re

# 플래그: ASCII 하이픈(-, --), en-dash(–, ––), em-dash(—, ——), horizontal bar(―) 지원
# 예: --focus, —focus, -f 등
_FOCUS_FLAG_RE = re.compile(
    r"(?:\s+|^)(?:[-–—―]{1,2}focus|[-–—―]f)\s+([^\n]+)",
    re.IGNORECASE,
)
_FOCUS_PREFIX_RE = re.compile(
    r"^(?:\[(?:\ucd08\uc810|focus)\]|#(?:\ucd08\uc810|focus)|(?:\ucd08\uc810|focus)\s*[:：])\s*(.+)$",
    re.IGNORECASE,
)
# 파이프(|, ｜, ¦) 또는 대시(--, —, –) 구분자 지원 (파이프 중심 통일)
# 주의: 단일 하이픈('-')은 영상/문서 제목('Title - Subtitle')에 흔히 쓰이므로 구분자로 취급하지 않고 '--' 또는 '—', '–'만 허용.
_FOCUS_SEP_RE = re.compile(
    r"(?:\s*([|｜¦])\s*|\s+([—–]{1,2}|--)\s+)",
)


def parse_message_focus(text: str) -> tuple[str, str | None]:
    """메시지 본문에서 페이로드(URL/텍스트)와 본문 작성 초점(focus)을 분리 추출.

    지원 패턴:
    1. 파이프 구분: `URL | <초점>` 또는 `URL ｜ <초점>` (주 문법)
    2. 줄바꿈 구분: 첫 줄이 단일 URL이고 다음 줄에 텍스트나 [초점] 태그가 오는 경우
    3. 구분자/플래그: `URL --focus <초점>`, `URL -f <초점>` 등
    """
    t = (text or "").strip()
    if not t:
        return "", None

    # 1. 플래그 (--focus, -f 등)
    m = _FOCUS_FLAG_RE.search(t)
    if m:
        focus_val = m.group(1).strip()
        payload = (t[:m.start()] + " " + t[m.end():]).strip()
        if payload:
            return payload, focus_val or None

    lines = [line.strip() for line in t.splitlines() if line.strip()]
    if not lines:
        return t, None

    # 2. 줄 단위 명시적 프리픽스 ([초점], 초점:, #초점 등) 검사
    focus_lines = []
    payload_lines = []
    for line in lines:
        pm = _FOCUS_PREFIX_RE.match(line)
        if pm:
            focus_lines.append(pm.group(1).strip())
        else:
            payload_lines.append(line)

    if focus_lines and payload_lines:
        return "\n".join(payload_lines), " ".join(focus_lines)

    # 3. 첫 줄에 파이프/대시 구분자가 있는 경우 (단일행 또는 다중행 모두 지원)
    first_line = lines[0]
    m_sep = _FOCUS_SEP_RE.search(first_line)
    if m_sep:
        part_a = first_line[:m_sep.start()].strip()
        part_b = first_line[m_sep.end():].strip()
        if part_a:
            extra_lines = lines[1:]
            full_focus = "\n".join([part_b] + extra_lines).strip() if (part_b or extra_lines) else None
            return part_a, full_focus

    # 4. URL 뒤에 두 번 이상의 줄바꿈(빈 줄)을 사이에 두고 평문 텍스트가 오는 경우
    # (줄바꿈 1번은 단순 오타/오입력 사고일 수 있으므로 빈 줄이 있는 2번째 줄바꿈에서만 분리)
    from ..ingest.router import _URL_RE
    blocks = [b.strip() for b in re.split(r"\n\s*\n", t) if b.strip()]
    if len(blocks) >= 2:
        first_block = blocks[0]
        if _URL_RE.fullmatch(first_block) or (first_block.lower().startswith(("http://", "https://")) and len(first_block.split()) == 1):
            rest = "\n\n".join(blocks[1:]).strip()
            pm = _FOCUS_PREFIX_RE.match(rest)
            if pm:
                rest = pm.group(1).strip()
            return first_block, rest or None

    return t, None


# 재생성 / 재수집 / 추론 레벨 플래그 정규식
_EFFORT_FLAG_RE = re.compile(
    r"(?:\s+|^)(?:[-–—―]{1,2}(?i:effort|reasoning)|-[eE])\s+([a-zA-Z0-9_-]+)",
)
_REFETCH_FULL_FLAG_RE = re.compile(
    r"(?:\s+|^)(?:[-–—―]{1,2}(?i:refetch[-_]full|full[-_]refetch|full[-_]content|full|no[-_]truncate)|-R)(?:\s+|$)",
)
_REFETCH_FLAG_RE = re.compile(
    r"(?:\s+|^)(?:[-–—―]{1,2}(?i:refetch)|-r)(?:\s+|$)",
)
# The extraction mode flags of an ingest message (AED4-rev): --force-provider selects the server API, --agent-client selects the queue of the agent client.
_EXTRACTION_MODE_FLAGS_RE: dict[str, re.Pattern[str]] = {
    "provider": re.compile(r"(?:\s+|^)[-–—―]{1,2}(?i:force[-_]provider)(?:\s+|$)"),
    "client": re.compile(r"(?:\s+|^)[-–—―]{1,2}(?i:agent[-_]client)(?:\s+|$)"),
}


def parse_extraction_mode_flag(text: str | None) -> tuple[str, str | None]:
    """Extract and strip the extraction mode flag from message or caption text. Return the text and the mode, or ``None`` when the text has no flag (the ingest then takes the default mode of the account). Both flags in one text raise ``ValueError('extraction_flags_conflict')``."""
    t = (text or "").strip()
    if not t:
        return "", None
    found: list[str] = []
    for mode, pattern in _EXTRACTION_MODE_FLAGS_RE.items():
        m = pattern.search(t)
        if m:
            found.append(mode)
            t = (t[:m.start()] + " " + t[m.end():]).strip()
    if len(found) > 1:
        raise ValueError("extraction_flags_conflict")
    return t, (found[0] if found else None)


_RENDITION_FLAG_RE = re.compile(
    r"(?:\s+|^)[-–—―]{1,2}(?i:rendition)(?:=(?P<eq_val>\S*)|(?:\s+(?P<sp_val>\S+))?)(?=\s|$)",
)


def parse_rendition_flags(text: str | None) -> tuple[str, list[str]]:
    """Extract and strip ``--rendition <tpl[,tpl]>`` flags from message or caption text.

    Return ``(cleaned_text, templates)`` where ``templates`` are deduplicated in ``ADDITIONAL_RENDITION_TEMPLATES`` order. Raise ``ValueError`` on empty or unknown template names.
    """
    from ..extract.rendition_templates import ADDITIONAL_RENDITION_TEMPLATES

    t = (text or "").strip()
    if not t:
        return "", []
    selected: set[str] = set()
    spans: list[tuple[int, int]] = []
    for m in _RENDITION_FLAG_RE.finditer(t):
        spans.append((m.start(), m.end()))
        raw_val = m.group("eq_val") if m.group("eq_val") is not None else (m.group("sp_val") or "")
        if not raw_val.strip():
            raise ValueError("empty rendition template")
        for part in raw_val.split(","):
            name = part.strip().lower()
            if not name:
                raise ValueError("empty rendition template")
            if name not in ADDITIONAL_RENDITION_TEMPLATES:
                raise ValueError(f"unknown rendition template: {name}")
            selected.add(name)
    if not spans:
        return t, []
    pieces: list[str] = []
    cursor = 0
    for start, end in spans:
        if start > cursor:
            pieces.append(t[cursor:start])
        cursor = end
    if cursor < len(t):
        pieces.append(t[cursor:])
    cleaned = "".join(pieces)
    cleaned = re.sub(r"[ \t]+", " ", cleaned)
    cleaned = re.sub(r" *\n *", "\n", cleaned).strip()
    ordered = [tpl for tpl in ADDITIONAL_RENDITION_TEMPLATES if tpl in selected]
    return cleaned, ordered


def parse_regenerate_flags(text: str) -> tuple[str, bool, bool, str | None]:
    """(cleaned_text, refetch, refetch_full, effort) 추출."""
    t = (text or "").strip()
    if not t:
        return "", False, False, None

    refetch_full = False
    refetch = False
    effort = None

    m_full = _REFETCH_FULL_FLAG_RE.search(t)
    if m_full:
        refetch_full = True
        t = (t[:m_full.start()] + " " + t[m_full.end():]).strip()

    m_ref = _REFETCH_FLAG_RE.search(t)
    if m_ref:
        if not refetch_full:
            refetch = True
        t = (t[:m_ref.start()] + " " + t[m_ref.end():]).strip()

    m_eff = _EFFORT_FLAG_RE.search(t)
    if m_eff:
        effort = m_eff.group(1).strip()
        t = (t[:m_eff.start()] + " " + t[m_eff.end():]).strip()

    return t, refetch, refetch_full, effort


def parse_caption_focus(caption: str | None) -> str | None:
    """파일/문서 첨부 캡션에서 초점 추출."""
    c = (caption or "").strip()
    if not c:
        return None
    pm = _FOCUS_PREFIX_RE.match(c)
    if pm:
        return pm.group(1).strip() or None
    return c


def parse_caption_composite(
    caption: str | None,
    theme_mgr: Any | None = None,
) -> tuple[str | None, str | None, int | None, bool, str | None]:
    """파일 첨부 캡션에서 (composite_url, clean_focus, kb_id, has_refetch_full, has_effort) 추출.

    캡션에 유효한 HTTP(S) URL이 포함된 경우 composite_url을 반환하여 복합 적재로 분기한다.
    URL이 없으면 composite_url은 None이다 (기존 단건 파일 적재 경로 유지).
    """
    c = (caption or "").strip()
    if not c:
        return None, None, None, False, None

    clean_text, explicit_kb_id = parse_message_theme(c, theme_mgr)
    text_no_flags, _, has_refetch_full, has_effort = parse_regenerate_flags(clean_text)

    from ..ingest.router import _URL_RE

    m = _URL_RE.search(text_no_flags)
    if not m:
        focus = parse_caption_focus(text_no_flags)
        return None, (focus or None), explicit_kb_id, has_refetch_full, has_effort

    target_url = m.group(0).rstrip(".,;)。")
    rem_before = text_no_flags[:m.start()].strip()
    rem_after = text_no_flags[m.end():].strip()
    combined_rem = f"{rem_before} {rem_after}".strip()

    focus = None
    if combined_rem:
        _, rem_focus = parse_message_focus(combined_rem)
        focus = rem_focus or combined_rem
        focus = re.sub(r"^[\s|｜¦—–:：]+", "", focus).strip()
        focus = re.sub(r"[\s|｜¦—–:：]+$", "", focus).strip()
        focus = parse_caption_focus(focus)

    return target_url, (focus or None), explicit_kb_id, has_refetch_full, has_effort


_THEME_TAG_RE = re.compile(
    r"(?:^|\s)#(?:\ud14c\ub9c8[::\-_]?)?([0-9]+|[a-zA-Z\uac00-\ud7a3_]+)(?=\s|$)",
    re.IGNORECASE,
)


def parse_message_theme(
    text: str, theme_mgr: Any | None = None, *, account_id: int | None = None
) -> tuple[str, int | None]:
    """메시지 본문에서 `#1`, `#테마1`, `#기술` 등 테마 태그를 감지하여 분리.

    ``account_id``가 주어지면 그 계정 소유 테마만 고른다(다른 계정 테마의 태그는 테마로 보지 않는다).
    반환: (태그가 제거된 본문, 테마 일련번호 ID | None)
    """
    t = (text or "").strip()
    if not t:
        return "", None
    from ..store.knowledge_base import get_kb_manager
    tm = theme_mgr or get_kb_manager()

    matches = list(_THEME_TAG_RE.finditer(t))
    for m in matches:
        cand = m.group(1).strip()
        if cand.lower() in ("\ucd08\uc810", "focus"):
            continue
        try:
            found = tm.get_kb(cand, label_match="partial")
            if account_id is not None and getattr(found, "account_id", dbm.ADMIN_ACCOUNT_ID) != account_id:
                continue
            clean = (t[:m.start()] + " " + t[m.end():]).strip()
            clean = re.sub(r"\s+", " ", clean).strip()
            return clean, found.id
        except (KeyError, Exception):
            continue

    return t, None


def resolve_selected_theme(theme_mgr: Any, selections: dict[int, int], user_id: int, *, for_read: bool = False) -> Any | None:
    """사용자가 /kb 로 고른 지식 베이스를 엄격하게 찾는다.

    고른 적이 없으면 봇 계정의 개인 지식 베이스다. 고른 테마가 삭제됐으면 선택을 지우고 None을 돌려준다. 호출자는 ``bot.theme.stale`` 메시지로 거부하고 적재·검색하지 않는다(테마 0으로 대신하지 않는다).

    ``for_read`` (search and read): the selection can be a shared knowledge base. Else (ingest and the work on ingested items) a selected shared knowledge base gives the personal knowledge base of the account: ingest goes to the personal knowledge base only (SHARED_KNOWLEDGE_BASES_AND_THEMES_DESIGN.md Section 13.4).
    """
    from ..store.knowledge_base import KnowledgeBaseNotFound

    scoped = hasattr(theme_mgr, "get_readable_kb")

    def _personal() -> Any:
        return theme_mgr.personal_kb() if scoped else theme_mgr.personal_kb_for(dbm.ADMIN_ACCOUNT_ID)

    if user_id not in selections:
        # No selection: the personal knowledge base of the account of the bot. The number 0 is the knowledge base of the admin; the bot of a member account cannot use it.
        return _personal()
    try:
        selected = (theme_mgr.get_readable_kb if scoped else theme_mgr.get_kb)(selections[user_id])
        if not for_read and getattr(selected, "is_shared", False):
            return _personal()
        return selected
    except KnowledgeBaseNotFound:
        selections.pop(user_id, None)
        return None


def _is_allowed(user_id: int | None, settings: Settings | None = None) -> bool:
    s = settings or get_settings()
    allow = s.allowed_user_ids
    if not allow:
        return True
    return user_id in allow


_EPHEMERAL_BUNDLE_SESSION_TTL = 60.0


def _issue_ephemeral_bundle_session(settings, *, account_id: int = dbm.ADMIN_ACCOUNT_ID) -> str:
    """Issue a short-lived owner X-Session row in claire.db for internal bundle API delegation."""
    from ..store import accounts as accounts_store

    conn = dbm.connect(settings.db_file)
    try:
        dbm.init_db(conn)
        return accounts_store.create_account_session(conn, account_id=int(account_id), scope="owner", ttl_seconds=_EPHEMERAL_BUNDLE_SESSION_TTL)
    finally:
        conn.close()


def _revoke_ephemeral_bundle_session(settings, session_token: str) -> None:
    """Delete the ephemeral bundle session immediately after the internal API call completes."""
    if not session_token:
        return
    try:
        conn = dbm.connect_existing(settings.db_file, readonly=False)
    except Exception:
        return
    try:
        conn.execute("DELETE FROM auth_sessions WHERE session_token=?", (session_token,))
        conn.commit()
    finally:
        conn.close()


async def _post_bundle_api(settings, *, api_url: str, path: str, json_body: dict[str, Any], account_id: int, url_env_name: str, api_label: str, transport=None) -> dict[str, Any]:
    """Call an internal Compose API bundle endpoint with an ephemeral account-bound X-Session."""
    parsed = urlsplit(api_url)
    if parsed.scheme != "http" or parsed.hostname != "api" or parsed.username is not None or parsed.password is not None or parsed.path not in {"", "/"} or parsed.query or parsed.fragment:
        raise RuntimeError(f"{url_env_name} must be the Compose-internal http://api:<port> URL")
    public_authority = urlsplit(str(settings.public_url)).netloc
    if not public_authority:
        raise RuntimeError(f"CLAIRE_FQDN is required for the internal {api_label} API")

    import httpx

    session_token = await asyncio.to_thread(_issue_ephemeral_bundle_session, settings, account_id=account_id)
    try:
        async with httpx.AsyncClient(timeout=300.0, transport=transport) as client:
            response = await client.post(
                f"{api_url}{path}",
                headers={
                    "X-Session": session_token,
                    # The TCP destination is the Compose service, while the hardened API continues to enforce its canonical public Host.
                    "Host": public_authority,
                },
                json=json_body,
            )
    finally:
        await asyncio.to_thread(_revoke_ephemeral_bundle_session, settings, session_token)
    if response.status_code != 200:
        raise RuntimeError(f"{api_label} API creation failed with HTTP {response.status_code}")
    try:
        return response.json()
    except (TypeError, ValueError) as exc:
        raise RuntimeError(f"{api_label} API returned an invalid response") from exc


async def _create_support_bundle_for_delivery(
    settings,
    *,
    days: int,
    target: str | None,
    channel: str,
    account_id: int = dbm.ADMIN_ACCOUNT_ID,
    transport=None,
):
    """Create through the API service that also serves the public download."""
    from ..ops.support_bundle import SupportBundleInfo, create_support_bundle

    api_url = str(getattr(settings, "support_bundle_api_url", "") or "").rstrip("/")
    if not api_url:
        return await asyncio.to_thread(
            create_support_bundle,
            settings,
            days=days,
            target=target,
            request_context={"channel": channel},
        )

    payload = await _post_bundle_api(
        settings,
        api_url=api_url,
        path="/support/bundle",
        json_body={"days": days, "target": target, "source_channel": channel},
        account_id=account_id,
        url_env_name="CLAIRE_SUPPORT_BUNDLE_API_URL",
        api_label="Support Bundle",
        transport=transport,
    )
    try:
        return SupportBundleInfo.from_dict(payload)
    except (KeyError, TypeError, ValueError) as exc:
        raise RuntimeError("Support Bundle API returned an invalid response") from exc


async def _create_graph_bundle_for_delivery(
    settings,
    *,
    kb_id: int = 0,
    channel: str,
    account_id: int = dbm.ADMIN_ACCOUNT_ID,
    transport=None,
):
    """Create graph bundle through the internal API service or local thread."""
    from ..ops.graph_bundle import GraphBundleInfo, create_graph_bundle

    api_url = str(getattr(settings, "graph_bundle_api_url", "") or getattr(settings, "support_bundle_api_url", "") or "").rstrip("/")
    if not api_url:
        return await asyncio.to_thread(
            create_graph_bundle,
            settings,
            kb_id=kb_id,
            request_context={"channel": channel},
        )

    payload = await _post_bundle_api(
        settings,
        api_url=api_url,
        path="/support/graph-bundle",
        json_body={"kb_id": kb_id, "source_channel": channel},
        account_id=account_id,
        url_env_name="CLAIRE_GRAPH_BUNDLE_API_URL",
        api_label="Graph Bundle",
        transport=transport,
    )
    try:
        return GraphBundleInfo.from_dict(payload)
    except (KeyError, TypeError, ValueError) as exc:
        raise RuntimeError("Graph Bundle API returned an invalid response") from exc


def build_app(settings: Settings | None = None, *, profile: Any | None = None) -> Any:
    """봇 하나의 Application을 만든다.

    ``profile``이 None이면 서버 봇(``TELEGRAM_BOT_TOKEN``, 관리자 계정)이다. 계정 봇은 ``store.telegram_bots.TelegramBotProfile``을 넘긴다.
    계정 봇은 그 계정의 테마·프로바이더 컨텍스트로만 동작하고, 허용 목록(``allowed_user_ids``)에 없는 사용자는 무시한다.
    """
    s = settings or get_settings()
    if profile is None:
        profile = server_bot_profile(s)
        if profile is None:
            raise ValueError("TELEGRAM_BOT_TOKEN is not set. Set it in .env.")
    setup_telegram_logging(s.data_dir)
    bot_account_id = int(profile.account_id)
    is_admin_bot = bot_account_id == dbm.ADMIN_ACCOUNT_ID
    is_server_bot = getattr(profile, "source", "account") == "server"
    allowed_ids = frozenset(int(i) for i in (profile.allowed_user_ids or ()))

    def _is_allowed(user_id: int | None) -> bool:
        if not allowed_ids:
            # 허용 목록이 빈 봇은 서버 봇뿐이다(예전 동작). 계정 봇은 저장 때 빈 목록을 거부한다.
            return is_server_bot
        return user_id in allowed_ids

    try:
        from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
        from telegram.ext import (
            Application,
            CallbackQueryHandler,
            CommandHandler,
            ContextTypes,
            MessageHandler,
            filters,
        )
    except Exception as e:  # noqa: BLE001
        raise RuntimeError(f"python-telegram-bot is not installed: {e}\n  Run uv sync, then try again.") from e

    import asyncio
    import tempfile
    from pathlib import Path

    from ..ingest.service import IngestService, IngestServicePool
    from ..store.knowledge_base import KnowledgeBaseNotFound, get_kb_manager

    logging.basicConfig(level=logging.INFO)
    # 텔레그램 봇은 관리자 전용이다(설계 §10.1). 서비스·풀은 관리자 컨텍스트(전역 settings = providers.json)로 만들고,
    # 테마 관리자를 관리자 계정 소유 테마로 좁혀 #태그·/theme·재생성 대상이 멤버 테마를 고르지 못하게 한다.
    theme_mgr = AccountThemeScope(get_kb_manager(s), bot_account_id)
    if is_admin_bot:
        svc = IngestService(s, kb_id=None, account_id=dbm.ADMIN_ACCOUNT_ID)
        service_pool = IngestServicePool(s, svc, theme_manager=theme_mgr, account_id=dbm.ADMIN_ACCOUNT_ID)
    else:
        # 멤버 계정 봇: 그 계정의 기본 테마 서비스(멤버 프로바이더 컨텍스트, 설계 §10.2)를 기본으로 쓴다.
        service_pool = IngestServicePool(s, None, theme_manager=theme_mgr, account_id=bot_account_id)
        svc = service_pool.get_service(None)

    async def _deliver_support_bundle(*, days: int, target: str | None, channel: str):
        if is_admin_bot:
            return await _create_support_bundle_for_delivery(s, days=days, target=target, channel=channel)
        from ..ops.account_support_bundle import create_account_support_bundle

        return await asyncio.to_thread(
            create_account_support_bundle,
            s,
            account_id=bot_account_id,
            days=days,
            target=target,
            request_context={"channel": channel},
        )

    async def _deliver_graph_bundle(*, kb_id: int, channel: str):
        if is_admin_bot:
            return await _create_graph_bundle_for_delivery(s, kb_id=kb_id, channel=channel)
        from ..ops.graph_bundle import create_graph_bundle

        return await asyncio.to_thread(
            create_graph_bundle,
            s,
            kb_id=kb_id,
            request_context={"channel": channel},
            account_id=bot_account_id,
        )

    def _owner_locale() -> str | None:
        """The ``accounts.locale`` of the bot owner account. ``None`` when the value is not set or when the database is not readable."""
        try:
            from ..store.accounts import AccountStore

            conn = dbm.connect_existing(s.db_file, readonly=True)
            try:
                return AccountStore(conn).get(bot_account_id).locale
            finally:
                conn.close()
        except Exception:  # noqa: BLE001
            return None

    def _localized(handler: Any) -> Any:
        """Run the handler of one update in the locale of that update (I18N design Section 7.1)."""

        @functools.wraps(handler)
        async def _wrapper(update: Any, ctx: Any) -> Any:
            with use_locale(resolve_update_locale(update, owner_locale=_owner_locale(), settings=s)):
                return await handler(update, ctx)

        return _wrapper

    async def _refuse_member(update: Any) -> bool:
        """멤버 계정 봇이면 거부 응답을 보내고 True."""
        if is_admin_bot:
            return False
        msg = getattr(update, "effective_message", None)
        if msg is not None:
            await msg.reply_text(_t("bot.error.member_refusal"))
        return True
    user_active_themes: dict[int, int] = {}
    pending: dict[str, tuple[list[str], int]] = {}  # 확장 후보 임시 보관(콜백 토큰 -> (urls, kb_id))
    pending_ingest: dict[str, dict[str, Any]] = {}  # 적재 대기 요청 임시 보관(콜백 토큰 -> dict)

    def _markup(update_id: int, candidates: list[str], doc_id: str | None = None, kb_id: int = 0):
        token = f"{update_id}"
        pending[token] = (candidates, kb_id)
        kb = []
        if doc_id:
            try:
                base_url = (getattr(s, "public_url", "") or "").rstrip("/")
                theme_db = service_pool.get_service(kb_id).s.db_file
                if base_url and theme_db and Path(theme_db).exists():
                    conn = dbm.connect_existing(theme_db, readonly=False)
                    try:
                        share_tok = dbm.create_doc_share(conn, doc_id, kb_id=kb_id)
                    finally:
                        conn.close()
                    if share_tok:
                        share_url = f"{base_url}/silvercord/{share_tok}"
                        kb.append([InlineKeyboardButton(_t("bot.button.open_reader"), url=share_url)])
            except Exception:  # noqa: BLE001
                pass
        kb.extend([
            [InlineKeyboardButton(
                _t("bot.button.expand", count=len(candidates)),
                callback_data=f"exp:{token}")],
            [InlineKeyboardButton(_t("bot.button.no"), callback_data=f"no:{token}")],
        ])
        return InlineKeyboardMarkup(kb)

    async def _settle(
        status,
        msg,
        summary: str,
        cands: list,
        update_id: int,
        *,
        has_error: bool = False,
        is_stt_failed: bool = False,
        is_detail_failed: bool = False,
        is_summary_failed: bool = False,
        is_duplicate: bool = False,
        is_staged: bool = False,
        retry_doc_id: str | None = None,
        kb_id: int = 0,
        extra_rows: list[list[Any]] | None = None,
    ) -> None:
        cands_markup = _markup(update_id, cands, retry_doc_id, kb_id=kb_id) if cands else None
        await _settle_status(
            status,
            msg,
            summary,
            cands,
            has_error=has_error,
            is_stt_failed=is_stt_failed,
            is_detail_failed=is_detail_failed,
            is_summary_failed=is_summary_failed,
            is_duplicate=is_duplicate,
            is_staged=is_staged,
            retry_doc_id=retry_doc_id,
            cands_markup=cands_markup,
            kb_id=kb_id,
            service_pool=service_pool,
            theme_mgr=theme_mgr,
            extra_rows=extra_rows,
        )

    def _contribute_to_theme(theme_ref: str, document_id: str) -> str:
        """Contribute one document of the personal knowledge base of the bot account to one theme, run the merge jobs, and return the reply text with the state of each target (SHARED_KNOWLEDGE_BASES_AND_THEMES_DESIGN.md Sections 8.7 rule 2 and 9.2). The caller runs it in a worker thread, in the locale of the update."""
        from ..collaboration import contributions
        from ..core import ids
        from ..ingest.service import run_waiting_merge_jobs
        from ..store import kb_collab
        from ..store import themes as themes_store

        def _code_text(code: str) -> str:
            text = _t(f"kb.err.{code}")
            return code if text == f"kb.err.{code}" else text

        try:
            personal = theme_mgr.personal_kb()
        except KnowledgeBaseNotFound:
            return _t("campaign.contribute_failed", error=_code_text("document_not_found"))
        conn = dbm.connect_existing(s.db_file)
        try:
            try:
                theme = themes_store.get_theme_by_number(conn, ids.parse_public_id(theme_ref))
            except ValueError:
                theme = None
            if theme is None or theme["state"] != "active":
                return _t("campaign.contribute_failed", error=_code_text("theme_not_found"))
            try:
                number = contributions.submit_to_themes(conn, account_id=bot_account_id, is_admin=is_admin_bot, personal_kb_id=personal.id, documents=[document_id], theme_id=int(theme["id"]), origin=contributions.canonical_origin(s))
            except contributions.ContributionError as exc:
                conn.rollback()
                return _t("campaign.contribute_failed", error=_code_text(exc.code))
            conn.commit()
            # The contribution of a reviewer needs no approval: its merge job runs now, with the provider of the server.
            run_waiting_merge_jobs(s)
            lines: list[str] = []
            taken = 0
            for item in kb_collab.submission_items(conn, number):
                made = {int(c["kb_id"]): c for c in kb_collab.contributions_of_item(conn, int(item["id"]))}
                reasons = {int(entry["kb_id"]): entry["reason"] for entry in item["skipped"]}
                for kb_id in item["target_kb_ids"]:
                    try:
                        label = theme_mgr.get_readable_kb(kb_id).label
                    except KnowledgeBaseNotFound:
                        label = f"#{kb_id}"
                    if kb_id in made:
                        taken += 1
                        lines.append(f"• {label}: {_t('kb.state.' + str(made[kb_id]['state']))}")
                    else:
                        lines.append(f"• {label}: {_t('kb.state.skipped')} ({_code_text(str(reasons.get(kb_id) or ''))})")
        finally:
            conn.close()
        if not lines:
            return f"{_t('campaign.contribute_none')}\n{_t('kb.dlg.result_no_target')}"
        head = _t("campaign.contribute_done", count=taken) if taken else _t("campaign.contribute_none")
        return "\n".join([f"{head} ({theme['label']})", *lines])

    def _theme_selection_markup(token: str, themes: list[Any]):
        kb = []
        for t in themes:
            pub_mark = "" if getattr(t, "is_public", True) else " 🔒"
            kb.append([InlineKeyboardButton(f"{t.icon} {t.label} (#{t.id}){pub_mark}", callback_data=f"igt:{token}:{t.id}")])
        kb.append([InlineKeyboardButton(_t("bot.button.cancel"), callback_data=f"igc:{token}")])
        return InlineKeyboardMarkup(kb)

    async def _prompt_theme_selection(
        msg,
        token: str,
        themes: list[Any],
        target_desc: str,
        *,
        kind_label: str = "",
    ):
        # RD24: bot messages use Telegram HTML, not Markdown. Each value is escaped.
        prompt_lines = [
            "🎯 <b>" + _t("bot.ingest.select_theme.title") + "</b>",
            "",
            f"• {_t('bot.label.target')}: <code>{_h(target_desc)}</code>" + (f" (<code>{_h(kind_label)}</code>)" if kind_label else ""),
        ]
        prompt_lines.append("")
        prompt_lines.append(_t("bot.ingest.select_theme.hint"))
        prompt_text = "\n".join(prompt_lines)
        markup = _theme_selection_markup(token, themes)
        try:
            return await msg.reply_text(prompt_text, reply_markup=markup, parse_mode="HTML")
        except Exception as e:
            log.warning("HTML entity parsing failed in _prompt_theme_selection, falling back to plain text: %s", e)
            plain_lines = [
                "🎯 " + _t("bot.ingest.select_theme.title"),
                "",
                f"• {_t('bot.label.target')}: {target_desc}" + (f" ({kind_label})" if kind_label else ""),
            ]
            plain_lines.append("")
            plain_lines.append(_t("bot.ingest.select_theme.hint"))
            return await msg.reply_text("\n".join(plain_lines), reply_markup=markup)

    async def _execute_ingest(
        status_msg,
        orig_msg,
        target_theme,
        update_id: int,
        *,
        payload: str | None = None,
        doc_work=None,
        has_effort: str | None = None,
        has_refetch_full: bool = False,
        extraction_mode: str | None = None,
        renditions: list[str] | None = None,
        kind_label: str = "",
        uid: int | None = None,
        cid: int | None = None,
    ) -> None:
        label = _t("bot.progress.processing_kind", kind=kind_label) if kind_label else _t("bot.progress.processing")
        if target_theme.id != 0:
            label = f"[{target_theme.icon} {target_theme.label}] " + label
        if has_refetch_full:
            label += " " + _t("bot.progress.tag.full_source")
        if has_effort:
            label += " " + _t("bot.progress.tag.effort", effort=has_effort)

        try:
            await status_msg.edit_text(f"⏳ {label}")
        except Exception:
            pass

        target_svc = service_pool.get_service(target_theme.id)
        has_error = False
        is_stt_failed = False
        is_duplicate = False
        is_staged = False
        did = None
        contribute_rows: list[list[Any]] = []
        rendition_list = list(renditions or ())

        def _work():
            if doc_work is not None:
                rep = doc_work(target_svc)
            else:
                rep = target_svc.ingest(
                    payload or "",
                    source="telegram",
                    user_id=uid,
                    chat_id=cid,
                    effort=has_effort,
                    full_content=has_refetch_full,
                    extraction_mode=extraction_mode,
                )
            rd_items: list[dict[str, str]] = []
            if rendition_list and getattr(rep, "document_id", None) and not getattr(rep, "error", None) and not getattr(rep, "duplicate", False):
                from ..extract.provider import emit_progress
                from ..ingest.renditions import run_ingest_renditions

                effective_mode = "client" if getattr(rep, "staged", False) else (extraction_mode if extraction_mode is not None else (target_svc.resolve_extraction_mode(None) if hasattr(target_svc, "resolve_extraction_mode") else "provider"))
                if effective_mode == "provider":
                    emit_progress(", ".join(rendition_list))
                try:
                    rd_items = run_ingest_renditions(
                        kb_svc=target_svc,
                        db_file=s.db_file,
                        kb_id=target_theme.id,
                        report=rep,
                        templates=rendition_list,
                        extraction_mode=effective_mode,
                        effort=has_effort,
                    )
                except Exception as rd_exc:  # noqa: BLE001
                    log.warning("Ingest renditions failed for doc_id=%s: %s", rep.document_id, rd_exc)
                    rd_items = [{"template": tpl, "state": "failed", "error": str(rd_exc)} for tpl in rendition_list]
            return rep, rd_items

        try:
            report, rendition_items = await _run_with_ticker(status_msg, label, _work)
            summary, cands = report.telegram_summary(), report.candidates
            # Campaign notices (SHARED_KNOWLEDGE_BASES_AND_THEMES_DESIGN.md Section 8.7), in the locale of the chat.
            for notice_line in report.notice_lines(locale=None):
                summary += f"\n\nℹ️ {notice_line}"
            # The action of the notice for a fiction-like document (Section 8.7 rule 2): a button that contributes the document to the theme.
            for notice in getattr(report, "notices", None) or []:
                callback_data = contribute_callback_data(notice) if isinstance(notice, dict) else None
                if callback_data is not None:
                    contribute_rows.append([InlineKeyboardButton(_t("campaign.contribute_action"), callback_data=callback_data)])
            if target_theme.id != 0:
                summary = f"🎯 *[{target_theme.icon} {target_theme.label}]*\n" + summary
            if rendition_items:
                summary += "\n\n" + _format_rendition_results_line(rendition_items)
            is_stt_failed = bool(
                report.stt_error
                or (report.source_type == "video" and report.has_transcript is False and report.stt_error)
            )
            is_detail_failed = not getattr(report, "detail_rendered", True)
            is_summary_failed = not getattr(report, "summary_rendered", True)
            has_error = bool(report.error) or is_detail_failed or is_summary_failed
            is_duplicate = bool(report.duplicate)
            is_staged = bool(getattr(report, "staged", False))
            emoji = _status_emoji(
                report.error,
                report.duplicate,
                stt_error=report.stt_error,
                detail_error=report.detail_error if is_detail_failed else None,
                summary_error="summary missing" if is_summary_failed else None,
            )
            did = report.document_id
            if did and not has_error and not is_staged:
                rendition_row = _missing_rendition_buttons(target_svc.s.db_file, did, target_theme.id)
                if rendition_row:
                    contribute_rows.append(rendition_row)
            log.info(
                "Ingest completed: doc_id=%s, title=%r, error=%s, detail_error=%s, duplicate=%s, staged=%s, cands=%d",
                report.document_id,
                report.title,
                report.error,
                getattr(report, "detail_error", None),
                report.duplicate,
                is_staged,
                len(report.candidates),
            )
        except Exception as e:  # noqa: BLE001
            log.exception("Unhandled error in _execute_ingest: %s", e)
            summary, cands, emoji, is_stt_failed, is_detail_failed, is_summary_failed, has_error, is_duplicate, is_staged, did = (
                _t("bot.error.processing", error=e),
                [],
                "👎",
                False,
                False,
                False,
                True,
                False,
                False,
                None,
            )

        await _react(orig_msg, emoji)
        await _settle(
            status_msg,
            orig_msg,
            summary,
            cands,
            update_id,
            has_error=has_error,
            is_stt_failed=is_stt_failed,
            is_detail_failed=is_detail_failed,
            is_summary_failed=is_summary_failed,
            is_duplicate=is_duplicate,
            is_staged=is_staged,
            retry_doc_id=did,
            kb_id=target_theme.id,
            extra_rows=contribute_rows,
        )

    def _help_text() -> str:
        text = _t("bot.help.text", repo=s.effective_source_base_url)
        if is_admin_bot:
            return text
        # The help of a member account bot does not show the lines of the operator commands.
        return "\n".join(
            line
            for line in text.splitlines()
            if not any(line.strip().startswith(f"/{cmd} ") or line.strip() == f"/{cmd}" for cmd in ADMIN_ONLY_COMMANDS)
        )

    async def on_start(update: Update, _ctx: ContextTypes.DEFAULT_TYPE) -> None:
        await update.message.reply_text(_help_text())

    async def on_help(update: Update, _ctx: ContextTypes.DEFAULT_TYPE) -> None:
        await update.message.reply_text(_help_text())

    async def on_theme(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
        user = update.effective_user
        if not _is_allowed(user.id if user else None):
            return
        uid = user.id if user else 0
        theme_mgr.reload()
        themes = theme_mgr.readable_kbs()

        if ctx.args:
            arg = " ".join(ctx.args).strip()
            try:
                # 사람이 입력하는 값이라 레이블 부분 일치를 허용한다
                target = theme_mgr.get_readable_kb(arg, label_match="partial")
                user_active_themes[uid] = target.id
                msg_text = (
                    _t("bot.theme.selected", icon=_h(target.icon), label=_h(target.label), id=target.id)
                )
                try:
                    await update.message.reply_text(msg_text, parse_mode="HTML")
                except Exception:
                    await update.message.reply_text(_plain(msg_text))
                return
            except KeyError:
                await update.message.reply_text(_t("bot.theme.not_found", name=arg))
                return

        current_theme = resolve_selected_theme(theme_mgr, user_active_themes, uid, for_read=True)
        stale_lines = []
        if current_theme is None:
            # 삭제된 선택은 지웠으므로 선택 없음(기본 테마 0)으로 보여 주고 그 사실을 알린다
            stale_lines = [_t("bot.theme.stale_cleared"), ""]
            current_theme = theme_mgr.personal_kb()
        current_tid = current_theme.id
        lines = stale_lines + [
            _t("bot.theme.current", icon=_h(current_theme.icon), label=_h(current_theme.label), id=current_theme.id),
            "",
            _t("bot.theme.choose"),
            _t("bot.theme.choose_note"),
        ]
        kb = []
        for t in themes:
            mark = "✓ " if t.id == current_tid else ""
            pub_mark = "" if getattr(t, "is_public", True) else " 🔒"
            kb.append([InlineKeyboardButton(f"{mark}{t.icon} {t.label} (#{t.id}){pub_mark}", callback_data=f"set_theme:{t.id}")])
        try:
            await update.message.reply_text(
                "\n".join(lines),
                reply_markup=InlineKeyboardMarkup(kb),
                parse_mode="HTML",
            )
        except Exception:
            await update.message.reply_text(
                _plain("\n".join(lines)),
                reply_markup=InlineKeyboardMarkup(kb),
            )

    async def on_message(update: Update, _ctx: ContextTypes.DEFAULT_TYPE) -> None:
        user = update.effective_user
        if not _is_allowed(user.id if user else None):
            await update.message.reply_text(_t("bot.error.not_allowed"))
            return
        raw_text = (update.message.text or "").strip()
        if not raw_text:
            return

        from ..core.config import extract_own_share_token

        explicit_kb_id = None
        theme_mgr.reload()
        text, explicit_kb_id = parse_message_theme(raw_text, theme_mgr)
        user_id = user.id if user else 0
        if explicit_kb_id is not None:
            active_theme = theme_mgr.get_kb(explicit_kb_id)
        else:
            active_theme = resolve_selected_theme(theme_mgr, user_active_themes, user_id)
            if active_theme is None:
                await update.message.reply_text(_t("bot.theme.stale"))
                return
        active_kb_id = active_theme.id
        active_svc = service_pool.get_service(active_kb_id)

        # CT28: the parser still separates the URL from the text after a separator; the text is ignored.
        payload, sealed_focus = parse_message_focus(text)
        if sealed_focus:
            await _reply_focus_sealed(update.message)
        try:
            payload_fp_clean, flag_mode = parse_extraction_mode_flag(payload)
        except ValueError:
            await _reply_extraction_flags_conflict(update.message)
            return
        try:
            payload_rd_clean, renditions = parse_rendition_flags(payload_fp_clean)
        except ValueError as exc:
            await _reply_invalid_rendition(update.message, exc)
            return
        payload_clean, has_refetch, has_refetch_full, has_effort = parse_regenerate_flags(payload_rd_clean)

        # 자체 FQDN의 공유 링크 또는 document_id 검사 (타 사이트 /p?s= 오인 방지)
        target_doc_id = None
        share_tok = extract_own_share_token(payload_clean, s)
        if share_tok:
            resolved = theme_mgr.resolve_share_token(share_tok)
            if resolved:
                res_tid, res_doc_id, _ = resolved
                try:
                    active_theme = theme_mgr.get_kb(res_tid)
                except KnowledgeBaseNotFound:
                    # 삭제된 테마의 공유 링크를 테마 0에서 처리하지 않는다
                    await update.message.reply_text(_t("bot.share.theme_gone"))
                    return
                active_kb_id = res_tid
                active_svc = service_pool.get_service(res_tid)
                target_doc_id = res_doc_id
            else:
                conn = dbm.connect(active_svc.s.db_file)
                try:
                    dbm.init_db(conn)
                    target_doc_id = dbm.resolve_doc_share(conn, share_tok, kb_id=active_kb_id)
                    if not target_doc_id:
                        target_doc_id = dbm.resolve_doc_share(conn, share_tok, kb_id=None)
                finally:
                    conn.close()
        elif payload_clean.startswith("doc_") and len(payload_clean.split()) == 1:
            conn = dbm.connect(active_svc.s.db_file)
            try:
                dbm.init_db(conn)
                row = conn.execute("SELECT id FROM documents WHERE id=?", (payload_clean,)).fetchone()
                if row:
                    target_doc_id = row["id"]
            finally:
                conn.close()
            if not target_doc_id and theme_mgr:
                try:
                    matches = theme_mgr.resolve_document_targets(doc_id=payload_clean)
                    if matches:
                        matched_tid = matches[0].get("kb_id")
                        if matched_tid is not None:
                            # 삭제된 테마면 ThemeNotFound로 이 후보를 건너뛴다
                            active_theme = theme_mgr.get_kb(matched_tid)
                            active_kb_id = matched_tid
                            active_svc = service_pool.get_service(matched_tid)
                            target_doc_id = payload_clean
                            if user:
                                user_active_themes[user.id] = matched_tid
                except Exception:
                    pass

        msg = update.message

        if target_doc_id:
            # 1. 초점 또는 재수집 플래그가 함께 전달된 경우 -> 즉시 실행
            if has_refetch or has_refetch_full or has_effort:
                label = _t("bot.regen.progress", doc_id=target_doc_id)
                if has_refetch_full:
                    label += " " + _t("bot.progress.tag.refetch_full")
                elif has_refetch:
                    label += " " + _t("bot.progress.tag.refetch")
                if has_effort:
                    label += " " + _t("bot.progress.tag.effort", effort=has_effort)
                status = await msg.reply_text(f"⏳ {label}")
                try:
                    res = await _run_with_ticker(
                        status, label,
                        lambda: active_svc.regenerate_components(
                            doc_id=target_doc_id,
                            detail=True,
                            refetch=has_refetch,
                            refetch_full=has_refetch_full,
                            effort=has_effort,
                            force=True,
                        ),
                    )
                    if res.get("error"):
                        ans = _t("bot.regen.failed", error=res["error"])
                        emoji = "👎"
                    elif res.get("count", 0) > 0:
                        tinfo = res["targets"][0]
                        if tinfo.get("refetch_error"):
                            ans = _t("bot.regen.refetch_error", error=tinfo["refetch_error"])
                            emoji = "👎"
                        elif tinfo.get("error"):
                            ans = _t("bot.regen.knowledge_error", error=tinfo["error"])
                            emoji = "👎"
                        elif (has_refetch or has_refetch_full) and tinfo.get("source_type") == "video" and tinfo.get("has_transcript") is False:
                            stt_err = tinfo.get("stt_error")
                            err_detail = f" ({_t('bot.label.error')}: {stt_err})" if stt_err else ""
                            ans = _t("bot.regen.subtitle_failed", detail=err_detail)
                            emoji = "👎"
                        else:
                            ref_msg = ""
                            if tinfo.get("refetched_full"):
                                ref_msg = " (" + _t("bot.regen.note.refetched_full", chars=f"{tinfo.get('new_len', 0):,}") + ")"
                            elif tinfo.get("refetched"):
                                ref_msg = " (" + _t("bot.regen.note.refetched", chars=f"{tinfo.get('new_len', 0):,}") + ")"
                            ans = _t("bot.regen.done", title=str(tinfo.get("title", target_doc_id)), detail=ref_msg)
                            emoji = "👍"
                    else:
                        ans = _t("bot.regen.doc_not_found", doc_id=target_doc_id)
                        emoji = "🤔"
                except Exception as e:
                    ans = _t("bot.regen.error", error=e)
                    emoji = "👎"
                await _react(msg, emoji)
                await status.edit_text(ans)
                return

            # 2. 순수 공유 링크/doc_id만 보낸 경우 -> 원터치 스마트 인라인 액션 버튼 제공
            conn = dbm.connect(active_svc.s.db_file)
            doc = None
            try:
                doc = dbm.get_document(conn, target_doc_id, kb_id=active_kb_id)
            finally:
                conn.close()

            doc_title = (doc.title if doc else None) or target_doc_id
            raw_len = len(doc.raw_text) if doc and doc.raw_text else 0
            trunc_info = ""
            if doc and ((doc.meta or {}).get("stt_truncated") or (doc.meta or {}).get("raw_truncated")):
                orig = (doc.meta or {}).get("stt_orig_chars") or (doc.meta or {}).get("orig_chars", raw_len)
                if (doc.meta or {}).get("stt_truncated"):
                    trunc_info = "\n" + _t("bot.doc.trunc.stt", chars=f"{raw_len:,}", orig=f"{orig:,}")
                elif (doc.meta or {}).get("appendix_truncated") and (doc.meta or {}).get("references_truncated"):
                    trunc_info = "\n" + _t("bot.doc.trunc.appendix_references", chars=f"{raw_len:,}", orig=f"{orig:,}")
                elif (doc.meta or {}).get("references_truncated"):
                    trunc_info = "\n" + _t("bot.doc.trunc.references", chars=f"{raw_len:,}", orig=f"{orig:,}")
                elif (doc.meta or {}).get("appendix_truncated"):
                    trunc_info = "\n" + _t("bot.doc.trunc.appendix", chars=f"{raw_len:,}", orig=f"{orig:,}")
                else:
                    trunc_info = "\n" + _t("bot.doc.trunc.limit", chars=f"{raw_len:,}", orig=f"{orig:,}")

            if doc and (doc.meta or {}).get("pdf_parser_fallback"):
                meta_p = doc.meta or {}
                req = (meta_p.get("pdf_parser_requested") or "Docling").capitalize()
                used = (meta_p.get("pdf_parser_used") or "PyPDF").capitalize()
                if req == "Pypdfium2":
                    req = "PyPDFium2"
                elif req == "Pypdf":
                    req = "PyPDF"
                if used == "Pypdfium2":
                    used = "PyPDFium2"
                elif used == "Pypdf":
                    used = "PyPDF"
                reason = meta_p.get("pdf_parser_fallback_reason") or _t("bot.doc.pdf.runtime_error", parser=req)
                trunc_info += "\n" + _t("bot.doc.pdf.fallback", requested=req, used=used, reason=reason)

            if doc and (doc.meta or {}).get("pdf_encoding_flaw_detected"):
                flaws = (doc.meta or {}).get("pdf_encoding_flaws") or []
                flaw_txt = ", ".join(flaws) if flaws else _t("bot.doc.pdf.text_layer_flaw")
                trunc_info += "\n" + _t("bot.doc.pdf.encoding_flaw", flaws=flaw_txt)

            if doc and (doc.meta or {}).get("pdf_is_scanned"):
                trunc_info += "\n" + _t("bot.doc.pdf.scanned")

            is_video = bool(doc and doc.source_type == "video")
            has_ts = bool((doc.meta or {}).get("has_transcript")) if doc else False
            video_info = ""
            if is_video and not has_ts:
                video_info = "\n" + _t("bot.doc.video.no_transcript")

            if is_video and not has_ts:
                kb = [
                    [InlineKeyboardButton(_t("bot.button.refetch_subtitles"), callback_data=f"rg:full:{target_doc_id}")],
                    [
                        InlineKeyboardButton(_t("bot.button.regen_detail_text"), callback_data=f"rg:det:{target_doc_id}"),
                    ],
                    [InlineKeyboardButton(_t("bot.button.support_bundle"), callback_data=f"sb:{target_doc_id}")],
                ]
            else:
                kb = [
                    [
                        InlineKeyboardButton(_t("bot.button.regen_summary"), callback_data=f"rg:summ:{target_doc_id}"),
                        InlineKeyboardButton(_t("bot.button.regen_detail"), callback_data=f"rg:det:{target_doc_id}"),
                    ],
                    [
                        InlineKeyboardButton(_t("bot.button.refetch_limited"), callback_data=f"rg:ref:{target_doc_id}"),
                        InlineKeyboardButton(_t("bot.button.refetch_full"), callback_data=f"rg:full:{target_doc_id}"),
                    ],
                    [InlineKeyboardButton(_t("bot.button.support_bundle"), callback_data=f"sb:{target_doc_id}")],
                ]
            markup = InlineKeyboardMarkup(kb)
            await msg.reply_text(
                _t("bot.doc.actions", title=str(doc_title), chars=f"{raw_len:,}", info=trunc_info + video_info),
                reply_markup=markup,
            )
            return

        # 일반 외부 웹페이지/텍스트 신규 적재
        if not payload_clean:
            if msg is not None:
                await msg.reply_text(_t("bot.ingest.usage"))
            return
        payload_to_ingest = payload_clean
        kind = classify_input(payload_to_ingest)
        uid = user.id if user else None
        cid = update.effective_chat.id if update.effective_chat else None
        log.info(
            "User message received: user_id=%s, chat_id=%s, text_length=%d, snippet=%r",
            uid, cid, len(payload_to_ingest), payload_to_ingest[:60],
        )

        themes = theme_mgr.list_kbs()
        if len(themes) > 1 and explicit_kb_id is None:
            token = f"{update.update_id}"
            target_snippet = payload_to_ingest[:60] + ("…" if len(payload_to_ingest) > 60 else "")
            prompt_msg = await _prompt_theme_selection(
                msg,
                token,
                themes,
                target_snippet,
                kind_label=kind,
            )
            pending_ingest[token] = {
                "orig_msg": msg,
                "status_msg": prompt_msg,
                "update_id": update.update_id,
                "payload": payload_to_ingest,
                "has_effort": has_effort,
                "has_refetch_full": has_refetch_full,
                "extraction_mode": flag_mode,
                "renditions": renditions,
                "kind_label": kind,
                "uid": uid,
                "cid": cid,
            }
            return

        # Ingest with no named knowledge base goes to the personal knowledge base of the account of the bot (SHARED_KNOWLEDGE_BASES_AND_THEMES_DESIGN.md Section 13.4).
        active_theme = theme_mgr.get_kb(explicit_kb_id) if explicit_kb_id is not None else theme_mgr.personal_kb()
        status = await msg.reply_text("⏳ " + _t("bot.progress.processing"))
        await _execute_ingest(
            status,
            msg,
            active_theme,
            update.update_id,
            payload=payload_to_ingest,
            has_effort=has_effort,
            has_refetch_full=has_refetch_full,
            extraction_mode=flag_mode,
            renditions=renditions,
            kind_label=kind,
            uid=uid,
            cid=cid,
        )

    async def on_document(update: Update, _ctx: ContextTypes.DEFAULT_TYPE) -> None:
        user = update.effective_user
        if not _is_allowed(user.id if user else None):
            await update.message.reply_text(_t("bot.error.not_allowed"))
            return
        doc = update.message.document or update.message.video or update.message.audio
        if not doc:
            return
        name = getattr(doc, "file_name", None)
        if not name:
            if update.message.video:
                name = f"video_{doc.file_unique_id}.mp4"
            elif update.message.audio:
                name = f"audio_{doc.file_unique_id}.mp3"
            else:
                name = "document"
        msg = update.message
        caption = update.message.caption
        theme_mgr.reload()

        try:
            caption_clean, flag_mode = parse_extraction_mode_flag(caption)
        except ValueError:
            await _reply_extraction_flags_conflict(msg)
            return
        try:
            caption_rd_clean, renditions = parse_rendition_flags(caption_clean)
        except ValueError as exc:
            await _reply_invalid_rendition(msg, exc)
            return
        composite_url, sealed_focus, explicit_tid, has_refetch_full, has_effort = parse_caption_composite(
            caption_rd_clean or None, theme_mgr
        )
        if sealed_focus:
            await _reply_focus_sealed(msg)
        uid = user.id if user else None
        cid = update.effective_chat.id if update.effective_chat else None
        log.info("User document received: user_id=%s, chat_id=%s, file_name=%r, composite_url=%r", uid, cid, name, composite_url)

        async def _download() -> str:
            tg_file = await doc.get_file()
            tmp = Path(tempfile.gettempdir()) / f"claire_{doc.file_unique_id}_{name}"
            await tg_file.download_to_drive(str(tmp))
            return str(tmp)

        try:
            tmp_path = await _download()
        except Exception as e:  # noqa: BLE001
            await _react(msg, "👎")
            await msg.reply_text(_t("bot.error.download", error=e))
            return

        is_composite = bool(composite_url)
        kind_label = _t("bot.kind.composite") if is_composite else _t("bot.kind.file")

        themes = theme_mgr.list_kbs()
        if len(themes) > 1 and explicit_tid is None:
            token = f"{update.update_id}"
            prompt_msg = await _prompt_theme_selection(
                msg,
                token,
                themes,
                name if not is_composite else f"{name} + {composite_url[:30]}",
                kind_label=kind_label,
            )
            def _doc_work_for_theme(target_svc):
                kept = target_svc.save_inbound_file(int(update.update_id), Path(tmp_path), name)
                if is_composite:
                    return target_svc.ingest_composite(
                        Path(kept),
                        name,
                        composite_url,
                        source="telegram",
                        user_id=uid,
                        chat_id=cid,
                        effort=has_effort,
                        full_content=has_refetch_full,
                        extraction_mode=flag_mode,
                    )
                return target_svc.ingest(
                    kept,
                    source="telegram",
                    user_id=uid,
                    chat_id=cid,
                    inbox_kind="document",
                    file_ref=kept,
                    file_name=name,
                    effort=has_effort,
                    full_content=has_refetch_full,
                    extraction_mode=flag_mode,
                )
            pending_ingest[token] = {
                "orig_msg": msg,
                "status_msg": prompt_msg,
                "update_id": update.update_id,
                "doc_work": _doc_work_for_theme,
                "has_effort": has_effort,
                "has_refetch_full": has_refetch_full,
                "extraction_mode": flag_mode,
                "renditions": renditions,
                "kind_label": kind_label,
                "uid": uid,
                "cid": cid,
                "tmp_path": tmp_path,
            }
            return

        # Ingest with no named knowledge base goes to the personal knowledge base of the account of the bot (SHARED_KNOWLEDGE_BASES_AND_THEMES_DESIGN.md Section 13.4).
        active_theme = theme_mgr.get_kb(explicit_tid) if explicit_tid is not None else theme_mgr.personal_kb()
        status_text = (
            _t("bot.progress.composite", name=name)
            if is_composite
            else _t("bot.progress.file", name=name)
        )
        status = await msg.reply_text(status_text)
        def _work(target_svc):
            kept = target_svc.save_inbound_file(int(update.update_id), Path(tmp_path), name)
            if is_composite:
                return target_svc.ingest_composite(
                    Path(kept),
                    name,
                    composite_url,
                    source="telegram",
                    user_id=uid,
                    chat_id=cid,
                    effort=has_effort,
                    full_content=has_refetch_full,
                    extraction_mode=flag_mode,
                )
            return target_svc.ingest(
                kept,
                source="telegram",
                user_id=uid,
                chat_id=cid,
                inbox_kind="document",
                file_ref=kept,
                file_name=name,
                effort=has_effort,
                full_content=has_refetch_full,
                extraction_mode=flag_mode,
            )
        await _execute_ingest(
            status,
            msg,
            active_theme,
            update.update_id,
            doc_work=_work,
            has_effort=has_effort,
            has_refetch_full=has_refetch_full,
            extraction_mode=flag_mode,
            renditions=renditions,
            kind_label=kind_label,
            uid=uid,
            cid=cid,
        )

    async def on_ingest(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
        user = update.effective_user
        if not _is_allowed(user.id if user else None):
            await update.message.reply_text(_t("bot.error.not_allowed"))
            return
        raw = " ".join(ctx.args) if ctx.args else ""
        if not raw:
            await update.message.reply_text(
                _t("bot.ingest.usage")
            )
            return

        theme_mgr.reload()
        raw_clean_theme, explicit_tid = parse_message_theme(raw, theme_mgr)

        payload, sealed_focus = parse_message_focus(raw_clean_theme)
        if sealed_focus:
            await _reply_focus_sealed(update.message)
        try:
            payload_fp_clean, flag_mode = parse_extraction_mode_flag(payload)
        except ValueError:
            await _reply_extraction_flags_conflict(update.message)
            return
        try:
            payload_rd_clean, renditions = parse_rendition_flags(payload_fp_clean)
        except ValueError as exc:
            await _reply_invalid_rendition(update.message, exc)
            return
        payload_clean, _, has_refetch_full, has_effort = parse_regenerate_flags(payload_rd_clean)
        if not payload_clean:
            if update.message is not None:
                await update.message.reply_text(_t("bot.ingest.usage"))
            return
        payload_to_ingest = payload_clean
        msg = update.message
        kind = classify_input(payload_to_ingest)
        uid = user.id if user else None
        cid = update.effective_chat.id if update.effective_chat else None

        themes = theme_mgr.list_kbs()
        if len(themes) > 1 and explicit_tid is None:
            token = f"{update.update_id}"
            target_snippet = payload_to_ingest[:60] + ("…" if len(payload_to_ingest) > 60 else "")
            prompt_msg = await _prompt_theme_selection(
                msg,
                token,
                themes,
                target_snippet,
                kind_label=kind,
            )
            pending_ingest[token] = {
                "orig_msg": msg,
                "status_msg": prompt_msg,
                "update_id": update.update_id,
                "payload": payload_to_ingest,
                "has_effort": has_effort,
                "has_refetch_full": has_refetch_full,
                "extraction_mode": flag_mode,
                "renditions": renditions,
                "kind_label": kind,
                "uid": uid,
                "cid": cid,
            }
            return

        # Ingest with no named knowledge base goes to the personal knowledge base of the account of the bot (SHARED_KNOWLEDGE_BASES_AND_THEMES_DESIGN.md Section 13.4).
        active_theme = theme_mgr.get_kb(explicit_tid) if explicit_tid is not None else theme_mgr.personal_kb()
        status = await msg.reply_text("⏳ " + _t("bot.progress.processing"))
        await _execute_ingest(
            status,
            msg,
            active_theme,
            update.update_id,
            payload=payload_to_ingest,
            has_effort=has_effort,
            has_refetch_full=has_refetch_full,
            extraction_mode=flag_mode,
            renditions=renditions,
            kind_label=kind,
            uid=uid,
            cid=cid,
        )

    async def on_callback(update: Update, _ctx: ContextTypes.DEFAULT_TYPE) -> None:
        query = update.callback_query
        await query.answer()
        data = query.data or ""
        log.info(
            "Callback query received: data=%r, user_id=%s",
            data,
            query.from_user.id if query.from_user else None,
        )
        if not is_admin_bot and data.startswith(ADMIN_ONLY_CALLBACK_PREFIXES):
            # 멤버 계정 봇은 OAuth 승인 콜백을 처리하지 않는다(위조 콜백 방지).
            return
        if data.startswith("oauth_appr:"):
            user = update.effective_user
            if not _is_allowed(user.id if user else None):
                return
            nonce = data.split(":", 1)[1]
            conn = dbm.connect_existing(s.db_file)
            try:
                code = dbm.approve_oauth_auth_request(conn, nonce)
                if code:
                    await query.edit_message_text(_t("bot.oauth.approved"))
                else:
                    await query.edit_message_text(_t("bot.oauth.expired"))
            finally:
                conn.close()
            return

        if data.startswith("oauth_deny:"):
            user = update.effective_user
            if not _is_allowed(user.id if user else None):
                return
            nonce = data.split(":", 1)[1]
            conn = dbm.connect_existing(s.db_file)
            try:
                dbm.deny_oauth_auth_request(conn, nonce)
                await query.edit_message_text(_t("bot.oauth.denied"))
            finally:
                conn.close()
            return

        if data.startswith("igt:"):
            user = update.effective_user
            if not _is_allowed(user.id if user else None):
                return
            parts = data.split(":", 2)
            if len(parts) < 3:
                return
            token, tid_str = parts[1], parts[2]
            item = pending_ingest.pop(token, None)
            if not item:
                await query.answer(_t("bot.ingest.expired_toast"))
                try:
                    await query.edit_message_text(_t("bot.ingest.expired"))
                except Exception:
                    pass
                return

            try:
                target_tid = int(tid_str)
                target_theme = theme_mgr.get_kb(target_tid)
            except Exception as e:
                await query.answer(_t("bot.theme.check_failed", error=e))
                return

            await query.answer(_t("bot.theme.picked", icon=target_theme.icon, label=target_theme.label))
            status_msg = query.message
            await _execute_ingest(
                status_msg,
                item["orig_msg"],
                target_theme,
                item["update_id"],
                payload=item.get("payload"),
                doc_work=item.get("doc_work"),
                has_effort=item.get("has_effort"),
                has_refetch_full=item.get("has_refetch_full", False),
                extraction_mode=item.get("extraction_mode"),
                renditions=item.get("renditions"),
                kind_label=item.get("kind_label", ""),
                uid=item.get("uid"),
                cid=item.get("cid"),
            )
            return

        if data.startswith("igc:"):
            user = update.effective_user
            if not _is_allowed(user.id if user else None):
                return
            parts = data.split(":", 1)
            token = parts[1] if len(parts) > 1 else ""
            item = pending_ingest.pop(token, None)
            if item and item.get("tmp_path"):
                Path(item["tmp_path"]).unlink(missing_ok=True)
            await query.answer(_t("bot.ingest.cancelled"))
            try:
                await query.edit_message_text("❌ " + _t("bot.ingest.cancelled"))
            except Exception:
                pass
            return

        if data.startswith("rg:"):
            user = update.effective_user
            if not _is_allowed(user.id if user else None):
                return
            parts = data.split(":")
            if len(parts) >= 3 and parts[1] in ("det", "ref", "full", "summ"):
                mode, did = parts[1], ":".join(parts[2:])
            else:
                mode, did = "det", data[3:]
            do_refetch = (mode == "ref")
            do_refetch_full = (mode == "full")
            do_summary = (mode == "summ")
            do_detail = (mode == "det" or do_refetch or do_refetch_full)
            mode_name = _t("bot.regen.mode.refetch_full") if do_refetch_full else (_t("bot.regen.mode.refetch") if do_refetch else (_t("bot.regen.mode.summary") if do_summary else _t("bot.regen.mode.detail")))
            try:
                await query.edit_message_reply_markup(reply_markup=None)
            except Exception:
                pass
            status_msg = await query.message.reply_text(_t("bot.regen.mode_progress", mode=mode_name, doc_id=did))

            # Determine the document's theme: check active theme first, then theme_mgr.resolve_document_targets
            user_id = user.id if user else 0
            active_theme = resolve_selected_theme(theme_mgr, user_active_themes, user_id)
            if active_theme is None:
                await status_msg.edit_text(_t("bot.theme.stale"))
                return
            target_svc = service_pool.get_service(active_theme.id)
            doc_in_active = False
            try:
                chk_conn = dbm.connect_existing(target_svc.s.db_file, readonly=True)
                try:
                    if dbm.get_document_row(chk_conn, did, kb_id=target_svc.kb_id):
                        doc_in_active = True
                finally:
                    chk_conn.close()
            except Exception:
                pass

            if not doc_in_active and theme_mgr:
                try:
                    matches = theme_mgr.resolve_document_targets(doc_id=did)
                    if matches:
                        resolved_tid = matches[0].get("kb_id", 0)
                        target_svc = service_pool.get_service(resolved_tid)
                        if user:
                            user_active_themes[user.id] = resolved_tid
                except Exception:
                    pass

            try:
                res = await _run_with_ticker(
                    status_msg, mode_name,
                    lambda: target_svc.regenerate_components(
                        doc_id=did,
                        summary=do_summary,
                        detail=do_detail,
                        refetch=do_refetch,
                        refetch_full=do_refetch_full,
                        force=True,
                    ),
                )
                if res.get("error"):
                    ans = _t("bot.regen.mode_failed", mode=mode_name, error=res["error"])
                elif res.get("count", 0) > 0:
                    tinfo = res["targets"][0]
                    if tinfo.get("refetch_error"):
                        ans = _t("bot.regen.refetch_error", error=tinfo["refetch_error"])
                    elif tinfo.get("error"):
                        ans = _t("bot.regen.knowledge_error", error=tinfo["error"])
                    elif (do_refetch or do_refetch_full) and tinfo.get("source_type") == "video" and tinfo.get("has_transcript") is False:
                        stt_err = tinfo.get("stt_error")
                        err_detail = f" ({_t('bot.label.error')}: {stt_err})" if stt_err else ""
                        ans = _t("bot.regen.mode_subtitle_failed", mode=mode_name, detail=err_detail)
                        kb = [[InlineKeyboardButton(_t("bot.button.refetch_subtitles"), callback_data=f"rg:full:{did}")]]
                        await status_msg.edit_text(ans, reply_markup=InlineKeyboardMarkup(kb))
                        return
                    else:
                        len_info = " (" + _t("bot.count.chars", chars=f"{tinfo.get('new_len', 0):,}") + ")" if (do_refetch or do_refetch_full) else ""
                        ans = _t("bot.regen.mode_done", mode=mode_name, title=str(tinfo.get("title", did)), detail=len_info)
                else:
                    ans = _t("bot.regen.doc_not_found", doc_id=did)
            except Exception as e:
                ans = _t("bot.regen.mode_error", mode=mode_name, error=e)
            await status_msg.edit_text(ans)
            return
        if data.startswith(CONTRIBUTE_CALLBACK_PREFIX):
            # The button "Contribute to this theme" of a campaign notice: ``ctb:<theme number>:<document ID>``.
            user = update.effective_user
            if not _is_allowed(user.id if user else None):
                return
            parts = data.split(":", 2)
            if len(parts) < 3 or not parts[1] or not parts[2]:
                return
            try:
                # ``asyncio.to_thread`` copies the context, thus the worker thread has the locale of this update.
                answer = await asyncio.to_thread(_contribute_to_theme, parts[1], parts[2])
            except Exception as exc:  # noqa: BLE001
                log.exception("Contribution to a theme failed: %s", exc)
                answer = _t("campaign.contribute_failed", error=type(exc).__name__)
            await query.message.reply_text(answer)
            return
        if data.startswith("rd:"):
            user = update.effective_user
            if not _is_allowed(user.id if user else None):
                return
            if query is None:
                return
            cb_query: Any = query
            cb_msg: Any = cb_query.message
            if cb_msg is None or not hasattr(cb_msg, "reply_text"):
                return
            parts = data.split(":")
            if len(parts) < 4:
                return
            tid_str = parts[1]
            code = parts[-1]
            did = ":".join(parts[2:-1])
            template = _RENDITION_CODE_TO_TEMPLATE.get(code)
            try:
                target_tid = int(tid_str)
            except ValueError:
                return
            if not did or template is None:
                return
            template_label = _t(f"rendition.template.{template}.label")
            await cb_query.answer(_t("bot.rendition.progress_toast", template=template_label))
            try:
                target_svc = service_pool.get_service(target_tid)
            except KnowledgeBaseNotFound:
                await cb_msg.reply_text(_t("bot.theme.stale"))
                return
            status_msg = await cb_msg.reply_text(_t("bot.rendition.progress", template=template_label, doc_id=did))
            completed = False
            try:
                items = await asyncio.to_thread(target_svc.add_renditions, did, [template])
                item = items[0] if items else {"template": template, "state": "failed", "error": "empty result"}
                state = item.get("state", "failed")
                if state in ("made", "existing"):
                    completed = True
                    rendition_url = ""
                    base_url = (getattr(s, "public_url", "") or "").rstrip("/")
                    if base_url:
                        try:
                            from ..store import permalinks as permalinks_store

                            db_path = Path(target_svc.s.db_file)
                            if db_path.exists():
                                conn = dbm.connect_existing(db_path, readonly=True)
                                try:
                                    tok = permalinks_store.token_of(conn, "document", did)
                                finally:
                                    conn.close()
                                if tok:
                                    rendition_url = f"{base_url}/silvercord/{tok}/renditions/{template}"
                        except Exception:  # noqa: BLE001
                            pass
                    key_base = "bot.rendition.existing" if state == "existing" else "bot.rendition.done"
                    if rendition_url:
                        ans = _t(f"{key_base}_with_link", template=template_label, doc_id=did, url=rendition_url)
                    else:
                        ans = _t(key_base, template=template_label, doc_id=did)
                else:
                    ans = _t("bot.rendition.failed", template=template_label, error=item.get("error") or "unknown")
            except LookupError:
                ans = _t("bot.rendition.doc_not_found", doc_id=did)
            except Exception as exc:  # noqa: BLE001
                ans = _t("bot.rendition.error", template=template_label, error=exc)
            await status_msg.edit_text(ans)
            if completed:
                existing_markup = getattr(cb_msg, "reply_markup", None)
                if existing_markup is not None and getattr(existing_markup, "inline_keyboard", None):
                    new_rows = []
                    for row in existing_markup.inline_keyboard:
                        kept = [btn for btn in row if getattr(btn, "callback_data", None) != data]
                        if kept:
                            new_rows.append(kept)
                    new_markup = InlineKeyboardMarkup(new_rows) if new_rows else None
                    try:
                        await cb_query.edit_message_reply_markup(reply_markup=new_markup)
                    except Exception:  # noqa: BLE001
                        pass
            return
        if data.startswith("sb:"):
            user = update.effective_user
            if not _is_allowed(user.id if user else None):
                return
            did = data[3:]
            try:
                await query.edit_message_reply_markup(reply_markup=None)
            except Exception:
                pass
            status_msg = await query.message.reply_text(_t("bot.support.doc_progress", doc_id=did))
            try:
                info = await _deliver_support_bundle(
                    days=1,
                    target=did,
                    channel="telegram_callback",
                )
                reply_text = (
                    _t("bot.support.doc_done_title", doc_id=did) + "\n\n" + _t("bot.support.done_body", bundle_id=info.bundle_id, size_kb=f"{info.size_bytes / 1024:.1f}", days=info.days_covered, target_line="", url=info.download_url)
                )
                await status_msg.edit_text(reply_text, disable_web_page_preview=True)
                if info.filepath.is_file() and info.size_bytes < 45 * 1024 * 1024:
                    try:
                        with open(info.filepath, "rb") as doc_file:
                            await query.message.reply_document(
                                document=doc_file,
                                filename=info.filename,
                                caption=f"Support Bundle: {info.bundle_id}",
                            )
                    except Exception as doc_err:
                        log.warning("Failed to send support bundle as telegram document: %s", doc_err)
            except Exception as exc:
                await status_msg.edit_text(_t("bot.support.error", error=exc))
            return
        if data.startswith("set_theme:"):
            user = update.effective_user
            if not _is_allowed(user.id if user else None):
                return
            target_tid_str = data[10:]
            try:
                target_tid = int(target_tid_str)
                theme = theme_mgr.get_readable_kb(target_tid)
                uid = user.id if user else 0
                user_active_themes[uid] = theme.id
                await query.answer(_t("bot.theme.toast", icon=theme.icon, label=theme.label))
                msg_text = (
                    _t("bot.theme.selected", icon=_h(theme.icon), label=_h(theme.label), id=theme.id)
                )
                try:
                    await query.edit_message_text(msg_text, parse_mode="HTML")
                except Exception:
                    await query.edit_message_text(_plain(msg_text))
            except Exception as e:
                await query.answer(_t("bot.theme.change_failed", error=e))
            return
        if data.startswith("no:"):
            # 거절: 진행/결과 메시지를 아예 삭제(스팸 감소 — 적재 결과는 원본 reaction 으로
            # 이미 표시됨). 삭제 불가(시간초과 등)면 버튼만 제거로 폴백.
            pending.pop(data[3:], None)
            try:
                await query.message.delete()
            except Exception:  # noqa: BLE001
                await query.edit_message_reply_markup(reply_markup=None)
            return
        if data.startswith("exp:"):
            res = pending.pop(data[4:], ([], 0))
            if isinstance(res, tuple):
                urls, exp_tid = res
            else:
                urls, exp_tid = res, 0
            if not urls:
                await query.edit_message_reply_markup(reply_markup=None)
                return
            try:
                exp_svc = service_pool.get_service(exp_tid)
            except KnowledgeBaseNotFound:
                # 적재 뒤 테마가 삭제됐으면 테마 0으로 확장 적재하지 않는다
                await query.edit_message_text(_t("bot.expand.theme_gone"))
                return
            # 같은 메시지를 in-place 편집해 진행→결과로 갱신(새 메시지 2개 더 안 만든다).
            async def _edit(text: str) -> None:
                try:
                    await query.edit_message_text(text)
                except Exception:  # noqa: BLE001
                    pass
            await _edit(_t("bot.expand.progress", count=len(urls)))
            lines = []
            for url in urls:
                try:
                    sub = await asyncio.to_thread(
                        exp_svc.ingest, url, source="expand-proposal", expand_max=0)
                    lines.append(f"• {sub.telegram_summary().splitlines()[0]}")
                except Exception as e:  # noqa: BLE001
                    lines.append(f"• ❌ {url}: {e}")
            await _edit(_t("bot.expand.result") + "\n" + "\n".join(lines))

    async def on_search(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
        if not _is_allowed(update.effective_user.id if update.effective_user else None):
            return
        q = " ".join(ctx.args) if ctx.args else ""
        if not q:
            await update.message.reply_text(_t("bot.search.usage"))
            return
        theme_mgr.reload()
        clean_q, explicit_tid = parse_message_theme(q, theme_mgr)
        user_id = update.effective_user.id if update.effective_user else 0
        if explicit_tid is not None:
            active_theme = theme_mgr.get_kb(explicit_tid)
        else:
            active_theme = resolve_selected_theme(theme_mgr, user_active_themes, user_id, for_read=True)
            if active_theme is None:
                await update.message.reply_text(_t("bot.theme.stale"))
                return
        active_tid = active_theme.id
        active_svc = service_pool.get_service(active_tid)

        await update.message.reply_text(_t("bot.search.progress", icon=active_theme.icon, label=active_theme.label))
        try:
            result = await asyncio.to_thread(active_svc.search, clean_q)
            text = result.telegram_text()
            if active_theme.id != 0:
                text = f"🎯 *[{active_theme.icon} {active_theme.label}]*\n\n" + text
        except Exception as e:  # noqa: BLE001
            text = _t("bot.search.error", error=e)
        await update.message.reply_text(text)

    async def on_web(update: Update, _ctx: ContextTypes.DEFAULT_TYPE) -> None:
        # 웹 접속 링크 발급: 세션 토큰 즉시 발급 → ?t= 가 붙은 1회용 진입 URL 회신.
        # 링크는 첫 접속에서 cookie 세션으로 회전하고 URL 자체는 즉시 무효화된다.
        if not _is_allowed(update.effective_user.id if update.effective_user else None):
            return
        if await _refuse_member(update):
            return

        if not s.public_url:
            await update.message.reply_text(
                _t("bot.web.no_fqdn"))
            return

        def _mint() -> str:
            conn = dbm.connect(svc.s.db_file)
            try:
                dbm.init_db(conn)
                return dbm.create_session(conn)
            finally:
                conn.close()

        tok = await asyncio.to_thread(_mint)
        url = f"{s.public_url.rstrip('/')}/?t={tok}"
        await update.message.reply_text(
            _t("bot.web.link", url=url),
            disable_web_page_preview=True,
        )

    async def on_webro(update: Update, _ctx: ContextTypes.DEFAULT_TYPE) -> None:
        # 읽기전용 웹 링크: /web 과 동일 메커니즘(세션+쿠키, 7일 슬라이딩)이지만
        # scope='readonly' 라 그래프·검색·문서 목록/상세만 보이고 적재·병합·공유발급 등
        # 쓰기는 막힌다(server.py 게이트, READONLY_PATHS 밖은 애초에 도달 불가). owner
        # 세션(/web)과 별도 scope 라 서로 안 끊고 공존 — 이 링크를 남에게 공유해도
        # 내 소유자 세션은 그대로 살아있다. 소유자만 발급 가능.
        if not _is_allowed(update.effective_user.id if update.effective_user else None):
            return
        if await _refuse_member(update):
            return

        if not s.public_url:
            await update.message.reply_text(
                _t("bot.web.no_fqdn"))
            return

        def _mint() -> str:
            conn = dbm.connect(svc.s.db_file)
            try:
                dbm.init_db(conn)
                return dbm.create_session(conn, scope="readonly")
            finally:
                conn.close()

        tok = await asyncio.to_thread(_mint)
        url = f"{s.public_url.rstrip('/')}/?t={tok}"
        await update.message.reply_text(
            _t("bot.web.link_readonly", url=url),
            disable_web_page_preview=True,
        )

    async def on_status(update: Update, _ctx: ContextTypes.DEFAULT_TYPE) -> None:
        if not _is_allowed(update.effective_user.id if update.effective_user else None):
            return
        if await _refuse_member(update):
            return
        msg = update.effective_message
        if not msg:
            return
        from ..ops.status import build_status_text

        try:
            text = await asyncio.to_thread(build_status_text, s, full=False)
        except Exception as e:  # noqa: BLE001
            text = _t("bot.status.error", error=e)
        await msg.reply_text(text)

    async def on_support(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
        user = update.effective_user
        if not _is_allowed(user.id if user else None):
            await update.message.reply_text(_t("bot.error.not_allowed"))
            return
        msg = update.effective_message
        if not msg:
            return

        args = list(ctx.args) if ctx.args else []
        if not args or args[0].lower() in ("help", "-h", "--help"):
            await msg.reply_text(
                _t("bot.support.usage")
            )
            return

        subcmd = args[0].lower()
        if subcmd != "bundle":
            await msg.reply_text(
                _t("bot.support.unknown_subcommand", subcommand=subcmd)
            )
            return

        sub_args = args[1:]
        from ..ops.support_bundle import (
            DEFAULT_SUPPORT_BUNDLE_DAYS,
            list_active_support_bundles,
            purge_expired_bundles,
            validate_bundle_days,
        )

        if sub_args and sub_args[0].lower() == "list":
            if is_admin_bot:
                # 운영자 봇은 서버 번들만 본다(계정 번들은 그 계정의 것이다).
                bundles = [
                    b for b in await asyncio.to_thread(list_active_support_bundles, s.data_dir)
                    if b.get("account_id") is None
                ]
            else:
                bundles = await asyncio.to_thread(
                    lambda: list_active_support_bundles(s.data_dir, account_id=bot_account_id)
                )
            if not bundles:
                await msg.reply_text(_t("bot.support.list_empty"))
                return
            import datetime
            lines = [_t("bot.support.list_title")]
            for b in bundles:
                e_str = datetime.datetime.fromtimestamp(b["expires_at"]).strftime("%H:%M")
                size_kb = f"{b['size_bytes'] / 1024:.1f}KB"
                tgt = f" ({_t('bot.label.document')}: {b['target_doc_id']})" if b.get("target_doc_id") else ""
                lines.append(_t("bot.support.list_item", bundle_id=b["bundle_id"], size=size_kb, days=b["days_covered"], expires=e_str, target=tgt))
                pub_url = s.public_url.rstrip('/') if s.public_url else ""
                dl = f"{pub_url}/support/bundle/{b['token']}" if pub_url else f"/support/bundle/{b['token']}"
                lines.append(f"  ↳ {_t('bot.label.link')}: {dl}")
            await msg.reply_text("\n".join(lines), disable_web_page_preview=True)
            return

        if sub_args and sub_args[0].lower() == "purge":
            purged = await asyncio.to_thread(purge_expired_bundles, s.data_dir)
            await msg.reply_text(_t("bot.support.purged", count=purged))
            return

        # Parse days and target from sub_args
        days = DEFAULT_SUPPORT_BUNDLE_DAYS
        target = None
        idx = 0
        while idx < len(sub_args):
            a = sub_args[idx]
            if a in ("--days", "-d") and idx + 1 < len(sub_args) and sub_args[idx + 1].isdigit():
                days = int(sub_args[idx + 1])
                idx += 2
            elif a in ("--target", "-t") and idx + 1 < len(sub_args):
                target = sub_args[idx + 1]
                idx += 2
            elif a.isdigit():
                days = int(a)
                idx += 1
            else:
                if not target:
                    target = a
                idx += 1

        max_ret = getattr(s, "telemetry_retention_days", 30)
        try:
            validate_bundle_days(days, max_ret)
        except ValueError as exc:
            await msg.reply_text(f"⚠️ {exc}")
            return

        target_info = f" ({_t('bot.label.target')}: {target})" if target else ""
        status_msg = await msg.reply_text(_t("bot.support.progress", days=days, target=target_info))

        try:
            info = await _deliver_support_bundle(
                days=days,
                target=target,
                channel="telegram_command",
            )
            theme_str = f", {_t('bot.label.theme')} #{info.target_kb_id}" if info.target_kb_id else ""
            if info.target_doc_id:
                tgt_line = _t("bot.support.target_line", doc_id=info.target_doc_id, matched=f"{info.target_matched_by}{theme_str}") + "\n"
            elif info.target_resolution_status:
                tgt_line = _t("bot.support.target_status_line", status=info.target_resolution_status) + "\n"
            else:
                tgt_line = ""
            reply_text = (
                _t("bot.support.done_title") + "\n\n" + _t("bot.support.done_body", bundle_id=info.bundle_id, size_kb=f"{info.size_bytes / 1024:.1f}", days=info.days_covered, target_line=tgt_line, url=info.download_url)
            )
            await status_msg.edit_text(reply_text, disable_web_page_preview=True)

            if info.filepath.is_file() and info.size_bytes < 45 * 1024 * 1024:
                try:
                    with open(info.filepath, "rb") as doc_file:
                        await msg.reply_document(
                            document=doc_file,
                            filename=info.filename,
                            caption=f"Support Bundle: {info.bundle_id}",
                        )
                except Exception as doc_err:
                    log.warning("Failed to send support bundle as telegram document: %s", doc_err)
        except Exception as exc:
            await status_msg.edit_text(_t("bot.support.error", error=exc))

    async def on_export(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
        user = update.effective_user
        if not _is_allowed(user.id if user else None):
            await update.message.reply_text(_t("bot.error.not_allowed"))
            return
        msg = update.effective_message
        if not msg:
            return

        args = list(ctx.args) if ctx.args else []
        if not args or args[0].lower() in ("help", "-h", "--help"):
            await msg.reply_text(
                _t("bot.export.usage")
            )
            return

        subcmd = args[0].lower()
        if subcmd != "graph":
            await msg.reply_text(
                _t("bot.export.unknown_subcommand", subcommand=subcmd)
            )
            return

        sub_args = args[1:]
        from ..ops.graph_bundle import (
            graph_bundle_download_url,
            list_active_graph_bundles,
            purge_expired_graph_bundles,
        )

        if sub_args and sub_args[0].lower() == "list":
            pub_url = s.public_url.rstrip('/') if s.public_url else ""
            if is_admin_bot:
                bundles = await asyncio.to_thread(list_active_graph_bundles, s.data_dir)
            else:
                from ..ops.graph_bundle import list_account_graph_bundles

                bundles = await asyncio.to_thread(lambda: list_account_graph_bundles(s.data_dir, bot_account_id, public_url=pub_url))
            if not bundles:
                await msg.reply_text(_t("bot.export.list_empty"))
                return
            import datetime
            lines = [_t("bot.export.list_title")]
            for b in bundles:
                # The registry record has epoch seconds; the record of the account list has ISO text.
                raw_expires = b["expires_at"]
                expires_dt = datetime.datetime.fromisoformat(raw_expires).astimezone() if isinstance(raw_expires, str) else datetime.datetime.fromtimestamp(raw_expires)
                e_str = expires_dt.strftime("%Y-%m-%d %H:%M")
                size_kb = f"{b['size_bytes'] / 1024:.1f}KB"
                theme_str = f"{_t('bot.label.theme')} #{b.get('kb_id', 0)}"
                token_val = b.get("token")
                link = b.get("download_url") or (graph_bundle_download_url(pub_url, token_val) if token_val else None)
                token_part = f"\n  ↳ {_t('bot.label.link')}: {link}" if link else ""
                lines.append(_t("bot.export.list_item", bundle_id=b["bundle_id"], theme=theme_str, size=size_kb, entities=b.get("entities_count", 0), relations=b.get("relations_count", 0), expires=e_str, link=token_part))
            await msg.reply_text("\n".join(lines), disable_web_page_preview=True)
            return

        if sub_args and sub_args[0].lower() == "purge":
            purged = await asyncio.to_thread(purge_expired_graph_bundles, s.data_dir)
            await msg.reply_text(_t("bot.export.purged", count=purged))
            return

        kb_id = 0
        if sub_args:
            first_arg = sub_args[0]
            if first_arg.isdigit():
                kb_id = int(first_arg)
            else:
                await msg.reply_text(
                    _t("bot.export.invalid_theme", value=first_arg)
                )
                return
        if not is_admin_bot:
            # 멤버 계정 봇은 자기 테마만 내보낸다. 번호가 없으면 기본 테마다.
            try:
                kb_id = theme_mgr.get_kb(kb_id if sub_args else None).id
            except KnowledgeBaseNotFound:
                await msg.reply_text(_t("bot.export.not_own_theme"))
                return

        status_msg = await msg.reply_text(_t("bot.export.progress"))
        try:
            info = await _deliver_graph_bundle(
                kb_id=kb_id,
                channel="telegram_command",
            )
            theme_line = _t("bot.export.theme_line", id=info.kb_id) + "\n" if info.kb_id else ""
            reply_text = (
                _t("bot.export.done", bundle_id=info.bundle_id, theme_line=theme_line, entities=f"{info.entities_count:,}", relations=f"{info.relations_count:,}", documents=f"{info.documents_count:,}", size_mb=f"{info.size_bytes / (1024 * 1024):.2f}", size_kb=f"{info.size_bytes / 1024:.1f}", url=info.download_url)
            )
            await status_msg.edit_text(reply_text, disable_web_page_preview=True)

            sent_document = False
            if info.filepath.is_file() and info.size_bytes <= 45 * 1024 * 1024:
                try:
                    caption = (
                        _t("bot.export.caption", bundle_id=info.bundle_id, entities=f"{info.entities_count:,}", relations=f"{info.relations_count:,}", size_kb=f"{info.size_bytes / 1024:.1f}")
                    )
                    with open(info.filepath, "rb") as doc_file:
                        await msg.reply_document(
                            document=doc_file,
                            filename=info.filename,
                            caption=caption,
                        )
                    sent_document = True
                except Exception as doc_err:
                    log.warning("Failed to send graph bundle as telegram document: %s", doc_err)

            if not sent_document:
                await msg.reply_text(
                    _t("bot.export.link", url=info.download_url),
                    disable_web_page_preview=True,
                )
        except Exception as exc:
            await status_msg.edit_text(_t("bot.export.error", error=exc))

    async def on_repo(update: Update, _ctx: ContextTypes.DEFAULT_TYPE) -> None:
        if not _is_allowed(update.effective_user.id if update.effective_user else None):
            return
        msg = update.effective_message
        if not msg:
            return
        await msg.reply_text(
            _t("bot.repo.text", url=s.effective_source_base_url, repository=s.effective_github_repository),
            disable_web_page_preview=False,
        )

    async def on_failed(update: Update, _ctx: ContextTypes.DEFAULT_TYPE) -> None:
        # SSH 없이 폰에서 점검 가능하게: error/영구실패 목록 + /retry 사용법.
        if not _is_allowed(update.effective_user.id if update.effective_user else None):
            return
        msg = update.effective_message
        if not msg:
            return

        user = update.effective_user
        user_id = user.id if user else 0
        active_theme = resolve_selected_theme(theme_mgr, user_active_themes, user_id)
        if active_theme is None:
            await msg.reply_text(_t("bot.theme.stale"))
            return
        active_kb_id = active_theme.id
        active_svc = service_pool.get_service(active_kb_id)

        try:
            items = await asyncio.to_thread(active_svc.list_failures, limit=10)
            if not items:
                all_items = []
                for t in theme_mgr.list_kbs(include_private=True):
                    if t.id == active_kb_id:
                        continue
                    tsvc = service_pool.get_service(t.id)
                    t_items = await asyncio.to_thread(tsvc.list_failures, limit=10)
                    for it in t_items:
                        it["theme_label"] = t.label
                    all_items.extend(t_items)
                if all_items:
                    items = all_items[:10]
        except Exception as e:  # noqa: BLE001
            await msg.reply_text(_t("bot.failed.query_error", error=e))
            return
        if not items:
            await msg.reply_text(_t("bot.failed.none"))
            return
        lines = [_t("bot.failed.title")]
        for it in items:
            mark = _t("bot.failed.mark_permanent") if it["status"] == "failed" else _t("bot.failed.mark_retry")
            tlabel = f"[{it['theme_label']}] " if it.get("theme_label") else ""
            lines.append(
                _t("bot.failed.item", id=it["id"], theme=tlabel, mark=mark, attempts=it["attempts"], payload=str(it["payload"]), error=str(it["error"])))
        lines.append("\n" + _t("bot.failed.retry_hint", id=items[0]["id"]))
        await msg.reply_text("\n".join(lines))

    async def on_retry(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
        if not _is_allowed(update.effective_user.id if update.effective_user else None):
            return
        msg = update.effective_message
        if not msg:
            return
        arg = ctx.args[0] if ctx.args else ""
        if not arg.isdigit():
            await msg.reply_text(_t("bot.retry.usage"))
            return
        inbox_id = int(arg)

        user = update.effective_user
        user_id = user.id if user else 0
        active_theme = resolve_selected_theme(theme_mgr, user_active_themes, user_id)
        if active_theme is None:
            await msg.reply_text(_t("bot.theme.stale"))
            return
        active_kb_id = active_theme.id
        target_svc = service_pool.get_service(active_kb_id)

        found = False
        try:
            conn = dbm.connect_existing(target_svc.s.db_file, readonly=True)
            try:
                if dbm.get_inbox(conn, inbox_id, kb_id=active_kb_id) is not None:
                    found = True
            finally:
                conn.close()
        except Exception:
            pass

        if not found:
            for t in theme_mgr.list_kbs(include_private=True):
                if t.id == active_kb_id:
                    continue
                tsvc = service_pool.get_service(t.id)
                if not Path(tsvc.s.db_file).is_file():
                    continue
                try:
                    conn = dbm.connect_existing(tsvc.s.db_file, readonly=True)
                    try:
                        if dbm.get_inbox(conn, inbox_id, kb_id=t.id) is not None:
                            target_svc = tsvc
                            user_active_themes[user_id] = t.id
                            found = True
                            break
                    finally:
                        conn.close()
                except Exception:
                    continue

        status = await msg.reply_text(_t("bot.retry.progress", id=inbox_id))
        try:
            report = await _run_with_ticker(
                status, _t("bot.retry.label", id=inbox_id),
                lambda: target_svc.retry_inbox(inbox_id))
            await status.edit_text(report.telegram_summary())
        except Exception as e:  # noqa: BLE001
            await status.edit_text(_t("bot.retry.error", error=e))

    app = Application.builder().token(profile.token).build()
    app.add_handler(CommandHandler("start", _localized(on_start)))
    app.add_handler(CommandHandler("help", _localized(on_help)))
    app.add_handler(CommandHandler("status", _localized(on_status)))
    app.add_handler(CommandHandler("support", _localized(on_support)))
    app.add_handler(CommandHandler("export", _localized(on_export)))
    app.add_handler(CommandHandler("repo", _localized(on_repo)))
    app.add_handler(CommandHandler("kb", _localized(on_theme)))
    app.add_handler(CommandHandler("search", _localized(on_search)))
    app.add_handler(CommandHandler("ingest", _localized(on_ingest)))
    app.add_handler(CommandHandler("web", _localized(on_web)))
    app.add_handler(CommandHandler("webro", _localized(on_webro)))
    app.add_handler(CommandHandler("failed", _localized(on_failed)))
    app.add_handler(CommandHandler("retry", _localized(on_retry)))
    app.add_handler(CallbackQueryHandler(_localized(on_callback)))
    app.add_handler(MessageHandler(filters.Document.ALL | filters.VIDEO | filters.AUDIO, _localized(on_document)))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, _localized(on_message)))

    async def _error_handler(update: object, context: ContextTypes.DEFAULT_TYPE) -> None:
        log.error("Unhandled exception while handling update: %s", context.error, exc_info=context.error)
        cb_query = getattr(update, "callback_query", None)
        if cb_query:
            try:
                await cb_query.answer(_t("bot.error.unhandled"))
            except Exception:
                pass
        else:
            msg = getattr(update, "effective_message", None)
            if msg and hasattr(msg, "reply_text"):
                try:
                    await msg.reply_text(_t("bot.error.unhandled"))
                except Exception:
                    pass

    app.add_error_handler(_localized(_error_handler))

    async def _post_init(application) -> None:
        # 텔레그램 클라이언트 입력창의 '/' 명령 메뉴에 노출.
        from telegram import BotCommand

        names = [name for name in COMMAND_MENU if is_admin_bot or name not in ADMIN_ONLY_COMMANDS]
        owner_locale = _owner_locale()
        settings_lang = getattr(s, "ui_lang", None)
        settings_lang = settings_lang if isinstance(settings_lang, str) else None

        def _commands(language_code: str | None) -> list[Any]:
            locale = resolve_bot_locale(owner_locale=owner_locale, language_code=language_code, settings_lang=settings_lang)
            return [BotCommand(name, _t(f"bot.command.{name}", locale=locale)) for name in names]

        # The default menu has no Telegram language. Each supported locale also gets its menu, thus a client in that language sees the same locale as the replies.
        await application.bot.set_my_commands(_commands(None))
        for code in SUPPORTED_LOCALES:
            await application.bot.set_my_commands(_commands(code), language_code=code)

    app.post_init = _post_init
    return app

