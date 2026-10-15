from __future__ import annotations

import asyncio
import contextlib
import logging
import os
import sys
import threading
import time
from contextlib import AsyncExitStack
from logging.handlers import RotatingFileHandler
from pathlib import Path
from typing import IO

from aiogram import Bot, Dispatcher
from aiogram.client.session.aiohttp import AiohttpSession
from aiogram.client.telegram import TelegramAPIServer
from aiogram.exceptions import TelegramNetworkError, TelegramUnauthorizedError
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.types import BotCommand, BotCommandScopeChat

import config
from src.core.broadcast import BroadcastStore
from src.core.download_manager import DownloadManager
from src.core.media_downloader import MediaDownloader
from src.core.media_pipeline import MediaPipeline
from src.core.selection_store import SelectionStore
from src.core.telegram_gateway import TelegramGateway
from src.core.updater import ToolUpdater
from src.core.user_stats import UserStatsManager
from src.core.video_info import VideoInfoService
from src.handlers.admin_handlers import AdminHandlers
from src.handlers.command_handlers import build_command_router
from src.handlers.download_handlers import build_download_router
from src.handlers.error_handlers import ErrorHandler
from src.handlers.middlewares import UserRegistry
from src.utils.file_utils import cleanup_old_files

logger = logging.getLogger(__name__)
WATCHDOG_TIMEOUT_SECONDS = 180


def configure_logging(settings: config.Settings) -> None:
    settings.log_file.parent.mkdir(parents=True, exist_ok=True)
    handlers: list[logging.Handler] = [
        logging.StreamHandler(sys.stdout),
        RotatingFileHandler(settings.log_file, maxBytes=5_000_000, backupCount=2, encoding="utf-8"),
    ]
    logging.basicConfig(
        level=getattr(logging, settings.log_level),
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
        handlers=handlers,
        force=True,
    )
    logging.getLogger("aiogram.event").setLevel(logging.WARNING)


def acquire_instance_lock(path: Path | None = None) -> IO[str] | None:
    import fcntl

    lock_path = path or Path(
        os.environ.get("RESAVE_LOCK_PATH", str(Path(__file__).resolve().with_name(".resave.lock")))
    )
    handle = lock_path.open("a+", encoding="ascii")
    try:
        fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        handle.close()
        return None
    handle.seek(0)
    handle.truncate()
    handle.write(str(os.getpid()))
    handle.flush()
    return handle


async def setup_commands(bot: Bot, admin_ids: tuple[int, ...]) -> None:
    common = [
        BotCommand(command="start", description="Запустить бота"),
        BotCommand(command="help", description="Инструкция"),
        BotCommand(command="status", description="Текущие загрузки"),
        BotCommand(command="stats", description="Ваша статистика"),
        BotCommand(command="cancel", description="Отменить загрузки"),
    ]
    admin = [
        BotCommand(command="admin", description="Панель администратора"),
        BotCommand(command="broadcast", description="Рассылка"),
        BotCommand(command="stats_global", description="Общая статистика"),
    ]
    try:
        await bot.set_my_commands(common)
    except Exception as exc:
        logger.warning("Cannot set global bot commands: %s", exc)
        return
    for admin_id in admin_ids:
        try:
            await bot.set_my_commands(
                common + admin,
                scope=BotCommandScopeChat(chat_id=admin_id),
            )
        except Exception as exc:
            logger.warning("Cannot set commands for admin %s: %s", admin_id, exc)


def build_bot(settings: config.Settings) -> tuple[Bot, Bot | None]:
    if not settings.bot_api_base_url:
        return Bot(token=settings.bot_token, session=AiohttpSession(timeout=120)), None
    api = TelegramAPIServer.from_base(
        settings.bot_api_base_url,
        is_local=settings.bot_api_is_local,
    )
    bot = Bot(token=settings.bot_token, session=AiohttpSession(api=api, timeout=120))
    cloud_bot = (
        Bot(token=settings.bot_token, session=AiohttpSession(timeout=120))
        if settings.bot_api_is_local
        else None
    )
    return bot, cloud_bot


def start_watchdog(loop: asyncio.AbstractEventLoop) -> asyncio.Task:
    heartbeat = [time.monotonic()]

    async def beat() -> None:
        while True:
            heartbeat[0] = time.monotonic()
            await asyncio.sleep(5)

    def watch() -> None:
        while loop.is_running():
            time.sleep(15)
            stalled = time.monotonic() - heartbeat[0]
            if stalled > WATCHDOG_TIMEOUT_SECONDS:
                logger.critical("Event loop is frozen for %.0f s; restarting", stalled)
                logging.shutdown()
                os._exit(3)

    threading.Thread(target=watch, name="watchdog", daemon=True).start()
    return asyncio.create_task(beat(), name="watchdog-heartbeat")


async def periodic_cleanup(settings: config.Settings, manager: DownloadManager) -> None:
    max_age_hours = (settings.download_timeout_seconds + 3600) / 3600
    while True:
        await asyncio.sleep(1800)
        try:
            await asyncio.to_thread(
                cleanup_old_files,
                settings.temp_dir,
                max_age_hours=max_age_hours,
                keep=manager.active_work_dirs(),
            )
        except Exception as exc:
            logger.warning("Temporary files cleanup failed: %s", exc)


async def wait_for_api(bot: Bot, settings: config.Settings, attempts: int = 20) -> None:
    for attempt in range(1, attempts + 1):
        try:
            await bot.get_me()
            return
        except TelegramUnauthorizedError as exc:
            raise RuntimeError("Telegram отклонил BOT_TOKEN: проверьте токен в .env") from exc
        except TelegramNetworkError as exc:
            if attempt == attempts:
                raise RuntimeError(
                    f"Telegram Bot API недоступен: {settings.bot_api_base_url or 'api.telegram.org'}"
                ) from exc
            logger.warning("Telegram Bot API is not ready (%s), retrying", exc)
            await asyncio.sleep(3)


async def cancel_task(task: asyncio.Task) -> None:
    task.cancel()
    with contextlib.suppress(asyncio.CancelledError):
        await task


async def run(settings: config.Settings | None = None) -> None:
    settings = config.validate_settings(settings)
    configure_logging(settings)
    settings.temp_dir.mkdir(parents=True, exist_ok=True)
    cleanup_old_files(str(settings.temp_dir), max_age_hours=24)

    updater = ToolUpdater(
        settings.packages_dir,
        interval_hours=settings.tools_update_hours,
        enabled=settings.tools_auto_update,
    )
    updater.activate()

    async with AsyncExitStack() as cleanup:
        cleanup.push_async_callback(cancel_task, start_watchdog(asyncio.get_running_loop()))
        bot, cloud_bot = build_bot(settings)
        cleanup.push_async_callback(bot.session.close)
        if cloud_bot:
            cleanup.push_async_callback(cloud_bot.session.close)

        stats = UserStatsManager(settings.stats_db_path)
        cleanup.callback(stats.close)
        telegram = TelegramGateway(
            bot,
            cloud_bot=cloud_bot,
            local_api=settings.bot_api_is_local,
            use_file_uri=settings.bot_api_use_file_uri,
            cloud_upload_limit=config.CLOUD_BOT_API_UPLOAD_LIMIT,
            rich_messages=settings.rich_messages_enabled,
            custom_emoji=settings.custom_emoji_enabled,
        )
        downloader = MediaDownloader(settings)
        pipeline = MediaPipeline(settings, downloader, telegram, stats)
        manager = DownloadManager(
            processor=pipeline.process,
            progress_reporter=pipeline.update_progress,
            telegram=telegram,
            stats=stats,
            max_concurrent_downloads=settings.max_concurrent_downloads,
            max_queue_size=settings.max_queue_size,
            max_tasks_per_user=settings.max_tasks_per_user,
            progress_update_seconds=settings.progress_update_seconds,
            task_timeout_seconds=settings.download_timeout_seconds + 900,
        )
        cleanup.push_async_callback(manager.stop)
        selections = SelectionStore(settings.selection_ttl_seconds)
        info_service = VideoInfoService(
            settings.cookies_file,
            settings.max_playlist_items,
            max_concurrent_requests=settings.max_concurrent_lookups,
            timeout_seconds=settings.lookup_timeout_seconds,
        )
        dispatcher = Dispatcher(storage=MemoryStorage())
        dispatcher.errors.register(ErrorHandler(telegram).__call__)
        broadcasts = BroadcastStore(settings.stats_db_path)
        cleanup.callback(broadcasts.close)
        admin = AdminHandlers(settings, stats, telegram, broadcasts)
        cleanup.push_async_callback(admin.shutdown)
        registry = UserRegistry(stats)
        dispatcher.message.outer_middleware(registry)
        dispatcher.callback_query.outer_middleware(registry)
        dispatcher.include_router(admin.router())
        dispatcher.include_router(
            build_command_router(manager, stats, telegram, settings, selections)
        )
        dispatcher.include_router(
            build_download_router(
                manager=manager,
                info_service=info_service,
                selections=selections,
                settings=settings,
                ffmpeg_available=downloader.ffmpeg_available,
                telegram=telegram,
            )
        )

        await wait_for_api(bot, settings)

        if not downloader.ffmpeg_available:
            logger.warning("FFmpeg not found; audio, GIF and stream merging may fail")
        if settings.max_file_size > settings.bot_api_upload_limit:
            logger.warning(
                "MAX_FILE_SIZE exceeds the active Bot API limit and will be clamped to %s MB",
                settings.effective_upload_limit // (1024 * 1024),
            )
        await manager.start()
        cleanup.push_async_callback(
            cancel_task, asyncio.create_task(periodic_cleanup(settings, manager))
        )
        cleanup.push_async_callback(cancel_task, asyncio.create_task(updater.run()))
        await setup_commands(bot, settings.admin_ids)
        await admin.notify_interrupted()
        logger.info(
            "ReSave started: workers=%s local_bot_api=%s upload_limit=%s MB",
            settings.max_concurrent_downloads,
            settings.bot_api_is_local,
            settings.effective_upload_limit // (1024 * 1024),
        )
        await dispatcher.start_polling(
            bot,
            allowed_updates=dispatcher.resolve_used_update_types(),
            tasks_concurrency_limit=16,
        )


def main() -> None:
    lock = acquire_instance_lock()
    if lock is None:
        print("ReSave уже запущен", file=sys.stderr)
        return
    try:
        asyncio.run(run())
    except KeyboardInterrupt:
        pass
    except RuntimeError as exc:
        logger.error("%s", exc)
        raise SystemExit(1) from exc
    finally:
        lock.close()


if __name__ == "__main__":
    main()
