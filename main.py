import sys
import os
import signal
import logging
import time
import io

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import telebot
import config

from src.utils.ffmpeg_handler import ensure_ffmpeg
from src.utils.admin_notifier import notify_admins
from src.utils.file_utils import cleanup_old_files, ensure_temp_dir
from src.utils.telegram_bot_wrapper import TelegramBotWrapper
from src.core.download_manager import DownloadManager
from src.core.user_stats import get_stats_manager
from src.handlers.command_handlers import register_command_handlers
from src.handlers.download_handlers import register_download_handlers, set_download_manager
from src.handlers.admin_handlers import register_admin_handlers


class UnicodeStreamHandler(logging.StreamHandler):
    def __init__(self):
        super().__init__(sys.stdout)
        if sys.platform == "win32":
            self.stream = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    handlers=[
        logging.FileHandler("bot.log", encoding="utf-8"),
        UnicodeStreamHandler(),
    ],
)
logger = logging.getLogger(__name__)


def ensure_module(name, package=None):
    try:
        return __import__(name)
    except ImportError:
        pkg = package or name
        logger.info(f"Установка модуля {pkg}...")
        import subprocess

        subprocess.check_call([sys.executable, "-m", "pip", "install", pkg])
        return __import__(name)


def setup_bot_commands(bot):
    commands = [
        telebot.types.BotCommand("/start", "🚀 Перезапустить бота"),
        telebot.types.BotCommand("/help", "📖 Помощь и инструкция"),
        telebot.types.BotCommand("/status", "📊 Статус текущих загрузок"),
        telebot.types.BotCommand("/stats", "📈 Ваша статистика"),
        telebot.types.BotCommand("/cancel", "🚫 Отменить текущую загрузку"),
    ]

    admin_commands = [
        telebot.types.BotCommand("/admin", "🔐 Панель администратора"),
    ]

    bot.set_my_commands(commands)

    for admin_id in config.ADMIN_IDS:
        try:
            scope = telebot.types.BotCommandScopeChat(admin_id)
            bot.set_my_commands(commands + admin_commands, scope=scope)
        except Exception as e:
            logger.debug(f"Не удалось установить команды для админа {admin_id}: {e}")


class CancelDownloadFilter(telebot.custom_filters.SimpleCustomFilter):
    key = "is_cancel_download"

    def check(self, message):
        return message.text and message.text.strip().lower() == "/cancel"


def main():
    logger.info("Проверка требуемых модулей...")
    ensure_module("yt_dlp", "yt-dlp")
    ensure_module("aiohttp")
    ensure_module("aiofiles")
    ensure_module("PIL", "Pillow")
    ensure_module("gallery_dl", "gallery-dl")

    ensure_temp_dir(config.TEMP_DIR)

    logger.info("Инициализация менеджера загрузок...")
    download_manager = DownloadManager(
        max_concurrent_downloads=config.MAX_CONCURRENT_DOWNLOADS,
        max_retries=3,
    )

    logger.info("Инициализация менеджера статистики...")
    get_stats_manager()

    logger.info("Инициализация бота...")
    bot = telebot.TeleBot(config.BOT_TOKEN, parse_mode=None, threaded=True)

    logger.info("Активация защиты от ошибок Markdown...")
    TelegramBotWrapper(bot)

    logger.info("Проверка FFmpeg...")
    if not ensure_ffmpeg(auto_download=False):
        warning_text = (
            "FFmpeg не найден на сервере. "
            "Функции конвертации (GIF/аудио) могут быть недоступны."
        )
        logger.warning(warning_text)
        notify_admins(bot, f"⚠️ [ReSave] {warning_text}")

    download_manager.set_bot(bot)
    set_download_manager(download_manager)

    logger.info("Регистрация обработчиков команд...")
    register_command_handlers(bot)

    logger.info("Регистрация обработчиков загрузок...")
    register_download_handlers(bot)

    logger.info("Регистрация админских команд...")
    register_admin_handlers(bot)

    setup_bot_commands(bot)
    bot.add_custom_filter(CancelDownloadFilter())

    def signal_handler(sig, frame):
        logger.info("\n👋 Завершение работы ReSave...")
        sys.exit(0)

    signal.signal(signal.SIGINT, signal_handler)
    signal.signal(signal.SIGTERM, signal_handler)

    cleanup_old_files(config.TEMP_DIR)

    logger.info("=" * 50)
    logger.info("ReSave запущен! (ReSave started!)")
    logger.info("Inline режим активирован! (Inline mode enabled!)")
    logger.info("Режим работы в группах активирован! (Group mode enabled!)")
    logger.info("Видео загружается в фоне и появляется после готовности")
    logger.info("=" * 50)

    try:
        bot.polling(
            none_stop=True,
            interval=0,
            timeout=60,
            allowed_updates=["message", "inline_query", "chosen_inline_result", "callback_query"],
        )
    except Exception as e:
        logger.error(f"❌ Ошибка polling: {e}")
        notify_admins(bot, f"❌ [ReSave] Ошибка polling: {e}")
        time.sleep(5)
        main()


if __name__ == "__main__":
    main()
