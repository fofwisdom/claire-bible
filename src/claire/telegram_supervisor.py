"""텔레그램 봇 러너 (유저 단위 봇).

한 프로세스(``claire bot``)가 여러 봇을 long-polling으로 함께 돌린다.

- 인스턴스 봇: ``TELEGRAM_BOT_TOKEN``이 있으면 관리자 계정 봇으로 돌린다(운영자 로그인 승인·알림 호환).
- 계정 봇: auth.db ``account_telegram_bots``에서 켜진(``enabled=1``) 봇 가운데 계정 상태가 ``active``인 것.

``POLL_INTERVAL_SECONDS``마다 원하는 봇 목록을 다시 읽어, 새 봇은 시작하고 지운·끈·바뀐 봇은 멈추거나 다시 시작한다.
WebUI에서 바꾼 설정은 다음 주기에 반영된다. 실행 결과(``running``·``error``)는 auth.db에 기록해 WebUI가 보여 준다.
시작에 실패한 봇은 ``RETRY_BACKOFF_SECONDS`` 동안 다시 시도하지 않는다(같은 설정일 때).
"""

from __future__ import annotations

import asyncio
import logging
import signal
import time
from dataclasses import dataclass
from typing import Any, Callable

from .config import Settings, get_settings
from .secret_scrub import scrub_text
from .store import auth_db
from .store import db as dbm
from .store import telegram_bots

log = logging.getLogger("claire.telegram.supervisor")

POLL_INTERVAL_SECONDS = 15.0
RETRY_BACKOFF_SECONDS = 120.0

BotKey = tuple[str, int]


@dataclass
class _Running:
    profile: telegram_bots.TelegramBotProfile
    app: Any


def _key(profile: telegram_bots.TelegramBotProfile) -> BotKey:
    return (profile.source, int(profile.account_id))


class BotSupervisor:
    def __init__(
        self,
        settings: Settings | None = None,
        *,
        app_factory: Callable[..., Any] | None = None,
        now: Callable[[], float] = time.time,
    ) -> None:
        self.s = settings or get_settings()
        if app_factory is None:
            from .telegram_bot import build_app

            app_factory = build_app
        self._factory = app_factory
        self._now = now
        self.running: dict[BotKey, _Running] = {}
        self._failed: dict[BotKey, tuple[tuple[object, ...], float]] = {}

    # --- 원하는 상태 ---------------------------------------------------------------

    def _active_account_ids(self) -> set[int]:
        conn = dbm.connect_existing(self.s.db_file, readonly=True)
        try:
            return {int(r[0]) for r in conn.execute("SELECT id FROM accounts WHERE status='active'")}
        finally:
            conn.close()

    def desired(self) -> dict[BotKey, telegram_bots.TelegramBotProfile]:
        from .telegram_bot import instance_bot_profile

        out: dict[BotKey, telegram_bots.TelegramBotProfile] = {}
        instance = instance_bot_profile(self.s)
        if instance is not None:
            out[_key(instance)] = instance
        try:
            active = self._active_account_ids()
        except Exception as exc:  # noqa: BLE001  # 지식 DB가 아직 없으면 인스턴스 봇만 돌린다
            log.warning("account list unavailable: %s", type(exc).__name__)
            return out
        conn = auth_db.connect_auth_db(auth_db.auth_db_path(self.s.data_dir))
        try:
            profiles, errors = telegram_bots.load_enabled_bots(conn, account_ids=active)
            for account_id, message in errors.items():
                telegram_bots.set_status(conn, account_id=account_id, status="error", error=message)
            for profile in profiles:
                if instance is not None and instance.bot_id and profile.bot_id == instance.bot_id:
                    telegram_bots.set_status(
                        conn, account_id=profile.account_id, status="error",
                        error="this bot is already used as the instance bot", fingerprint=profile.fingerprint,
                    )
                    continue
                out[_key(profile)] = profile
        finally:
            conn.close()
        return out

    # --- 상태 기록 --------------------------------------------------------------------

    def _record(self, profile: telegram_bots.TelegramBotProfile, status: str, error: str | None = None) -> None:
        if profile.source != "account":
            return
        try:
            conn = auth_db.connect_auth_db(auth_db.auth_db_path(self.s.data_dir))
            try:
                telegram_bots.set_status(
                    conn, account_id=profile.account_id, status=status, error=error, fingerprint=profile.fingerprint  # type: ignore[arg-type]
                )
            finally:
                conn.close()
        except Exception as exc:  # noqa: BLE001
            log.warning("bot status write failed for account %s: %s", profile.account_id, type(exc).__name__)

    # --- 시작·정지 -------------------------------------------------------------------

    async def _start(self, profile: telegram_bots.TelegramBotProfile) -> None:
        key = _key(profile)
        failed = self._failed.get(key)
        if failed is not None and failed[0] == profile.signature and failed[1] > self._now():
            return
        app = None
        try:
            app = self._factory(self.s, profile=profile)
            await app.initialize()
            post_init = getattr(app, "post_init", None)
            if post_init is not None:
                await post_init(app)
            await app.start()
            await app.updater.start_polling()
        except Exception as exc:  # noqa: BLE001
            message = scrub_text(f"{type(exc).__name__}: {exc}", extra=(profile.token,))
            log.warning("bot start failed for %s: %s", key, message)
            self._failed[key] = (profile.signature, self._now() + RETRY_BACKOFF_SECONDS)
            self._record(profile, "error", message)
            if app is not None:
                await self._shutdown_app(app)
            return
        self._failed.pop(key, None)
        self.running[key] = _Running(profile=profile, app=app)
        self._record(profile, "running")
        log.info("bot started: %s", key)

    async def _shutdown_app(self, app: Any) -> None:
        for step in ("updater.stop", "stop", "shutdown"):
            try:
                if step == "updater.stop":
                    updater = getattr(app, "updater", None)
                    if updater is not None and getattr(updater, "running", False):
                        await updater.stop()
                elif step == "stop":
                    if getattr(app, "running", False):
                        await app.stop()
                else:
                    await app.shutdown()
            except Exception as exc:  # noqa: BLE001
                log.warning("bot %s failed: %s", step, type(exc).__name__)

    async def _stop(self, key: BotKey) -> None:
        running = self.running.pop(key, None)
        if running is None:
            return
        await self._shutdown_app(running.app)
        log.info("bot stopped: %s", key)

    async def sync(self) -> None:
        """원하는 봇 목록에 맞춰 시작·정지한다."""
        try:
            wanted = await asyncio.to_thread(self.desired)
        except Exception as exc:  # noqa: BLE001
            log.warning("bot list read failed: %s", type(exc).__name__)
            return
        for key, running in list(self.running.items()):
            target = wanted.get(key)
            if target is None or target.signature != running.profile.signature:
                await self._stop(key)
        for key, profile in wanted.items():
            if key not in self.running:
                await self._start(profile)

    async def shutdown(self) -> None:
        for key in list(self.running):
            await self._stop(key)

    async def run(self, stop: asyncio.Event, *, interval: float = POLL_INTERVAL_SECONDS) -> None:
        try:
            while not stop.is_set():
                await self.sync()
                try:
                    await asyncio.wait_for(stop.wait(), timeout=interval)
                except asyncio.TimeoutError:
                    pass
        finally:
            await self.shutdown()


def run_supervisor(settings: Settings | None = None) -> int:
    s = settings or get_settings()
    from .telegram_bot import setup_telegram_logging

    setup_telegram_logging(s.data_dir)
    logging.basicConfig(level=logging.INFO)

    async def _main() -> None:
        stop = asyncio.Event()
        loop = asyncio.get_running_loop()
        for sig in (signal.SIGINT, signal.SIGTERM):
            try:
                loop.add_signal_handler(sig, stop.set)
            except (NotImplementedError, RuntimeError):
                pass
        await BotSupervisor(s).run(stop)

    print("claire telegram bot runner 시작 (인스턴스 봇 + 계정 봇, long-polling). Ctrl+C 로 종료.")
    asyncio.run(_main())
    return 0
