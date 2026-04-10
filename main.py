import asyncio
import io
import logging
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import config
from aiogram import Bot, Dispatcher, Router
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.types import BotCommand, BotCommandScopeChat

from src.core.download_manager import DownloadManager
from src.core.user_stats import get_stats_manager
from src.handlers.admin_handlers import register_admin_handlers
from src.handlers.command_handlers import register_command_handlers
from src.handlers.download_handlers import (
    register_download_handlers,
    set_download_manager,
)
from src.utils.aiogram_bot_adapter import AiogramSyncBotAdapter
from src.utils.ffmpeg_handler import ensure_ffmpeg
from src.utils.file_utils import cleanup_old_files, ensure_temp_dir
from src.utils.telegram_bot_wrapper import TelegramBotWrapper


class UnicodeStreamHandler(logging.StreamHandler):
    def __init__(self):
        super().__init__(sys.stdout)
        if sys.platform == "win32":
            self.stream = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")


logging.basicConfig(
    level=getattr(logging, config.LOG_LEVEL, logging.INFO),
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    handlers=[
        logging.FileHandler("bot.log", encoding="utf-8"),
        UnicodeStreamHandler(),
    ],
)
logger = logging.getLogger(__name__)


async def setup_bot_commands(bot, BotCommand, BotCommandScopeChat):
    commands = [
        BotCommand(command="start", description="Запустить бота"),
        BotCommand(command="help", description="Помощь и инструкция"),
        BotCommand(command="status", description="Статус текущих загрузок"),
        BotCommand(command="stats", description="Ваша статистика"),
        BotCommand(command="cancel", description="Отменить текущую загрузку"),
    ]
    admin_commands = [
        BotCommand(command="admin", description="Панель администратора"),
        BotCommand(command="broadcast", description="Рассылка пользователям"),
        BotCommand(command="stats_global", description="Глобальная статистика"),
    ]

    await bot.set_my_commands(commands)

    for admin_id in config.ADMIN_IDS:
        try:
            await bot.set_my_commands(
                commands + admin_commands,
                scope=BotCommandScopeChat(chat_id=admin_id),
            )
        except Exception as exc:
            logger.debug("Не удалось установить команды для админа %s: %s", admin_id, exc)


async def notify_admins_async(bot, text: str):
    for admin_id in config.ADMIN_IDS:
        try:
            await bot.send_message(admin_id, text)
        except Exception as exc:
            logger.error("Не удалось уведомить администратора %s: %s", admin_id, exc)


async def run():
    settings = config.validate_settings()

    ensure_temp_dir(settings.temp_dir)
    cleanup_old_files(settings.temp_dir)

    logger.info("Инициализация менеджера загрузок...")
    download_manager = DownloadManager(
        max_concurrent_downloads=settings.max_concurrent_downloads,
        max_retries=3,
    )

    logger.info("Инициализация менеджера статистики...")
    get_stats_manager()

    logger.info("Инициализация aiogram...")
    bot = Bot(token=settings.bot_token)
    dispatcher = Dispatcher(storage=MemoryStorage())
    router = Router()

    sync_bot = TelegramBotWrapper(
        AiogramSyncBotAdapter(bot=bot, loop=asyncio.get_running_loop())
    )

    logger.info("Проверка FFmpeg...")
    if not ensure_ffmpeg(auto_download=False):
        warning_text = (
            "FFmpeg не найден на сервере. "
            "Функции конвертации (GIF/аудио) могут быть недоступны."
        )
        logger.warning(warning_text)
        await notify_admins_async(bot, f"⚠️ [ReSave] {warning_text}")

    download_manager.set_bot(sync_bot)
    set_download_manager(download_manager)

    logger.info("Регистрация aiogram-хендлеров...")
    register_command_handlers(router)
    register_download_handlers(router, sync_bot)
    register_admin_handlers(router)
    dispatcher.include_router(router)

    await setup_bot_commands(bot, BotCommand, BotCommandScopeChat)

    logger.info("=" * 50)
    logger.info("ReSave запущен")
    logger.info("Inline-режим активирован")
    logger.info("Групповой режим активирован")
    logger.info("=" * 50)

    try:
        await dispatcher.start_polling(
            bot,
            allowed_updates=dispatcher.resolve_used_update_types(),
        )
    finally:
        await bot.session.close()


def main():
    try:
        asyncio.run(run())
    except (KeyboardInterrupt, SystemExit):
        logger.info("Завершение работы ReSave...")


if __name__ == "__main__":
    main()
