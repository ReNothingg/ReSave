import asyncio
import io
import logging
import os
import sys
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import config
from aiogram import Bot, Dispatcher, Router
from aiogram.client.session.aiohttp import AiohttpSession
from aiogram.client.telegram import TelegramAPIServer
from aiogram.exceptions import TelegramNetworkError
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


class SkipInfoFilter(logging.Filter):
    def filter(self, record):
        return record.levelno != logging.INFO


_file_handler = logging.FileHandler("bot.log", encoding="utf-8")
_stream_handler = UnicodeStreamHandler()
for _handler in (_file_handler, _stream_handler):
    _handler.addFilter(SkipInfoFilter())


logging.basicConfig(
    level=logging.DEBUG,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    handlers=[_file_handler, _stream_handler],
)
logger = logging.getLogger(__name__)


def acquire_instance_lock(lock_path: str | os.PathLike | None = None):
    """Hold an advisory process lock for the lifetime of the returned file."""
    import fcntl

    path = Path(lock_path or (Path(__file__).resolve().parent / ".resave.lock"))
    lock_file = path.open("a+", encoding="ascii")
    try:
        fcntl.flock(lock_file.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        lock_file.close()
        return None

    lock_file.seek(0)
    lock_file.truncate()
    lock_file.write(str(os.getpid()))
    lock_file.flush()
    return lock_file


async def ensure_bot_api_available(bot: Bot, settings: config.Settings):
    try:
        await bot.get_me()
    except TelegramNetworkError as exc:
        if settings.bot_api_base_url:
            raise RuntimeError(
                "Локальный Telegram Bot API недоступен по адресу "
                f"{settings.bot_api_base_url}. Запустите локальный "
                "telegram-bot-api любым доступным способом или уберите "
                "BOT_API_BASE_URL из .env, чтобы вернуться к облачному "
                "Bot API с лимитом 50 MB."
            ) from exc
        raise


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
    session = None
    if settings.bot_api_base_url:
        api_server = TelegramAPIServer.from_base(
            settings.bot_api_base_url,
            is_local=settings.bot_api_is_local,
        )
        session = AiohttpSession(api=api_server, timeout=600)
        logger.info(
            "Используется Bot API server: %s (local=%s)",
            settings.bot_api_base_url,
            settings.bot_api_is_local,
        )

    bot = Bot(token=settings.bot_token, session=session)
    cloud_upload_bot = (
        Bot(token=settings.bot_token)
        if settings.bot_api_base_url and settings.bot_api_is_local
        else None
    )
    dispatcher = Dispatcher(storage=MemoryStorage())
    router = Router()

    try:
        await ensure_bot_api_available(bot, settings)

        sync_bot = TelegramBotWrapper(
            AiogramSyncBotAdapter(
                bot=bot,
                loop=asyncio.get_running_loop(),
                cloud_upload_bot=cloud_upload_bot,
            )
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
        register_download_handlers(router, sync_bot)
        register_command_handlers(router)
        register_admin_handlers(router)
        dispatcher.include_router(router)

        await setup_bot_commands(bot, BotCommand, BotCommandScopeChat)

        logger.info("=" * 50)
        logger.info("ReSave запущен")
        logger.info("Групповой режим активирован")
        logger.info("=" * 50)

        await dispatcher.start_polling(
            bot,
            allowed_updates=dispatcher.resolve_used_update_types(),
        )
    finally:
        if cloud_upload_bot:
            await cloud_upload_bot.session.close()
        await bot.session.close()


def main():
    instance_lock = acquire_instance_lock()
    if instance_lock is None:
        logger.warning("ReSave уже запущен; повторный экземпляр завершен")
        return

    try:
        asyncio.run(run())
    except RuntimeError as exc:
        logger.error("%s", exc)
        raise SystemExit(1)
    except (KeyboardInterrupt, SystemExit):
        logger.info("Завершение работы ReSave...")
    finally:
        instance_lock.close()


if __name__ == "__main__":
    main()
