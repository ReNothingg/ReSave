from __future__ import annotations

import asyncio
import logging
import os
import shutil
import sys
from pathlib import Path
from typing import IO

from aiogram import Bot, Dispatcher
from aiogram.client.session.aiohttp import AiohttpSession
from aiogram.client.telegram import TelegramAPIServer
from aiogram.exceptions import TelegramNetworkError
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.types import BotCommand, BotCommandScopeChat

import config
from src.core.download_manager import DownloadManager
from src.core.media_downloader import MediaDownloader
from src.core.media_pipeline import MediaPipeline
from src.core.selection_store import SelectionStore
from src.core.telegram_gateway import TelegramGateway
from src.core.user_stats import UserStatsManager, set_stats_manager
from src.core.video_info import VideoInfoService
from src.handlers.admin_handlers import build_admin_router
from src.handlers.command_handlers import build_command_router
from src.handlers.download_handlers import build_download_router
from src.utils.file_utils import cleanup_old_files

logger = logging.getLogger(__name__)


def configure_logging(settings: config.Settings) -> None:
    settings.log_file.parent.mkdir(parents=True, exist_ok=True)
    handlers: list[logging.Handler] = [
        logging.StreamHandler(sys.stdout),
        logging.FileHandler(settings.log_file, encoding="utf-8"),
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

    lock_path = path or Path(__file__).resolve().with_name(".resave.lock")
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
    await bot.set_my_commands(common)
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
        return Bot(token=settings.bot_token), None
    api = TelegramAPIServer.from_base(
        settings.bot_api_base_url,
        is_local=settings.bot_api_is_local,
    )
    session = AiohttpSession(api=api, timeout=600)
    bot = Bot(token=settings.bot_token, session=session)
    cloud_bot = Bot(token=settings.bot_token) if settings.bot_api_is_local else None
    return bot, cloud_bot


async def run(settings: config.Settings | None = None) -> None:
    settings = config.validate_settings(settings)
    configure_logging(settings)
    settings.temp_dir.mkdir(parents=True, exist_ok=True)
    cleanup_old_files(str(settings.temp_dir), max_age_hours=24)

    bot, cloud_bot = build_bot(settings)
    stats = UserStatsManager(settings.stats_db_path)
    set_stats_manager(stats)
    telegram = TelegramGateway(
        bot,
        cloud_bot=cloud_bot,
        local_api=settings.bot_api_is_local,
        use_file_uri=settings.bot_api_use_file_uri,
        cloud_upload_limit=config.CLOUD_BOT_API_UPLOAD_LIMIT,
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
    )
    selections = SelectionStore(settings.selection_ttl_seconds)
    info_service = VideoInfoService(
        settings.cookies_file,
        settings.max_playlist_items,
        max_concurrent_requests=max(2, settings.max_concurrent_downloads * 2),
    )
    dispatcher = Dispatcher(storage=MemoryStorage())
    dispatcher.include_router(build_admin_router(settings, stats))
    dispatcher.include_router(build_command_router(manager, stats, telegram))
    dispatcher.include_router(
        build_download_router(
            manager=manager,
            info_service=info_service,
            selections=selections,
            settings=settings,
        )
    )

    try:
        try:
            await bot.get_me()
        except TelegramNetworkError as exc:
            if settings.bot_api_base_url:
                raise RuntimeError(
                    f"Telegram Bot API недоступен: {settings.bot_api_base_url}. "
                    "Запустите локальный API или уберите BOT_API_BASE_URL."
                ) from exc
            raise

        if not shutil.which("ffmpeg"):
            logger.warning("FFmpeg not found; audio, GIF and stream merging may fail")
        await manager.start()
        await setup_commands(bot, settings.admin_ids)
        logger.info(
            "ReSave started: workers=%s local_bot_api=%s upload_limit=%s MB",
            settings.max_concurrent_downloads,
            settings.bot_api_is_local,
            settings.effective_upload_limit // (1024 * 1024),
        )
        await dispatcher.start_polling(
            bot,
            allowed_updates=dispatcher.resolve_used_update_types(),
        )
    finally:
        await manager.stop()
        stats.close()
        set_stats_manager(None)
        if cloud_bot:
            await cloud_bot.session.close()
        await bot.session.close()


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
