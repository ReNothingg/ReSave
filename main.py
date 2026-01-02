#!/usr/bin/env python3
"""
ReSave - Telegram бот для скачивания видео
Основной файл приложения
"""
import sys
import os
import signal
import logging
import time
import threading

# Добавляем корневой каталог в PATH для импорта модулей
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import telebot
from telebot import types
import config

# Импортируем компоненты приложения
from src.utils.ffmpeg_handler import ensure_ffmpeg
from src.utils.file_utils import cleanup_old_files, ensure_temp_dir
from src.utils.telegram_bot_wrapper import TelegramBotWrapper
from src.core.download_manager import DownloadManager
from src.core.user_stats import get_stats_manager
from src.handlers.command_handlers import register_command_handlers
from src.handlers.download_handlers import register_download_handlers, set_download_manager
from src.handlers.admin_handlers import register_admin_handlers

# Настройка логирования с поддержкой Unicode на Windows
import io

class UnicodeStreamHandler(logging.StreamHandler):
    """Обработчик потока с поддержкой Unicode"""
    def __init__(self):
        super().__init__(sys.stdout)
        if sys.platform == 'win32':
            import io
            self.stream = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    handlers=[
        logging.FileHandler("bot.log", encoding='utf-8'),
        UnicodeStreamHandler()
    ]
)
logger = logging.getLogger(__name__)


def ensure_module(name, package=None):
    """Проверяет и устанавливает требуемый модуль"""
    try:
        return __import__(name)
    except ImportError:
        pkg = package or name
        logger.info(f"Установка модуля {pkg}...")
        import subprocess
        subprocess.check_call([sys.executable, "-m", "pip", "install", pkg])
        return __import__(name)


def setup_bot_commands(bot):
    """Устанавливает команды для меню бота"""
    # Основные команды для всех пользователей
    commands = [
        telebot.types.BotCommand("/start", "🚀 Перезапустить бота"),
        telebot.types.BotCommand("/help", "📖 Помощь и инструкция"),
        telebot.types.BotCommand("/status", "📊 Статус текущих загрузок"),
        telebot.types.BotCommand("/stats", "📈 Ваша статистика"),
        telebot.types.BotCommand("/cancel", "🚫 Отменить текущую загрузку")
    ]

    # Добавляем админ-команды только для администраторов
    admin_commands = [
        telebot.types.BotCommand("/admin", "🔐 Панель администратора"),
    ]

    # Устанавливаем команды для всех
    bot.set_my_commands(commands)

    # Устанавливаем команды для админов (scope)
    for admin_id in config.ADMIN_IDS:
        try:
            scope = telebot.types.BotCommandScopeChat(admin_id)
            bot.set_my_commands(commands + admin_commands, scope=scope)
        except Exception as e:
            logger.debug(f"Не удалось установить команды для админа {admin_id}: {e}")


class CancelDownloadFilter(telebot.custom_filters.SimpleCustomFilter):
    """Фильтр для команды /cancel"""
    key = 'is_cancel_download'

    def check(self, message):
        return message.text and message.text.strip().lower() == '/cancel'


def main():
    """Главная функция приложения"""

    # Проверяем и устанавливаем требуемые модули
    logger.info("Проверка требуемых модулей...")
    ensure_module("yt_dlp", "yt-dlp")
    ensure_module("aiohttp")
    ensure_module("aiofiles")
    ensure_module("PIL", "Pillow")
    ensure_module("gallery_dl", "gallery-dl")

    # Проверяем FFmpeg
    logger.info("Проверка FFmpeg...")
    if not ensure_ffmpeg():
        logger.warning("⚠️ FFmpeg не найден. Некоторые функции могут быть недоступны.")

    # Создаем временную директорию
    ensure_temp_dir(config.TEMP_DIR)

    # Инициализируем менеджер загрузок
    logger.info("Инициализация менеджера загрузок...")
    download_manager = DownloadManager(
        max_concurrent_downloads=config.MAX_CONCURRENT_DOWNLOADS,
        max_retries=3
    )

    # Инициализируем менеджер статистики
    logger.info("Инициализация менеджера статистики...")
    stats_manager = get_stats_manager()

    # Инициализируем бота
    logger.info("Инициализация бота...")
    bot = telebot.TeleBot(config.BOT_TOKEN, parse_mode=None, threaded=True)
    
    # Оборачиваем бота для автоматической обработки ошибок парсинга
    logger.info("Активирование защиты от ошибок парсинга Markdown...")
    TelegramBotWrapper(bot)

    # Устанавливаем менеджер загрузок для бота
    download_manager.set_bot(bot)
    set_download_manager(download_manager)

    # Регистрируем обработчики
    logger.info("Регистрация обработчиков команд...")
    register_command_handlers(bot)

    logger.info("Регистрация обработчиков загрузок...")
    register_download_handlers(bot)

    logger.info("Регистрация администраторских команд...")
    register_admin_handlers(bot)

    # Устанавливаем команды ПОСЛЕ регистрации всех хендлеров
    setup_bot_commands(bot)

    # Добавляем пользовательский фильтр
    bot.add_custom_filter(CancelDownloadFilter())

    # Функция для правильного завершения
    def signal_handler(sig, frame):
        logger.info("\n👋 Завершение работы ReSave...")
        sys.exit(0)

    signal.signal(signal.SIGINT, signal_handler)
    signal.signal(signal.SIGTERM, signal_handler)

    # Очищаем старые файлы
    cleanup_old_files(config.TEMP_DIR)

    # Выводим стартовую информацию
    logger.info("="*50)
    logger.info("ReSave запущен! (ReSave started!)")
    logger.info("Inline режим активирован! (Inline mode enabled!)")
    logger.info("Режим работы в группах активирован! (Group mode enabled!)")
    logger.info("Видео загружается в фоне и появляется после готовности")
    logger.info("="*50)

    # Запускаем бота
    try:
        bot.polling(
            none_stop=True,
            interval=0,
            timeout=60,
            allowed_updates=['message', 'inline_query', 'chosen_inline_result', 'callback_query']
        )
    except Exception as e:
        logger.error(f"❌ Ошибка polling: {e}")
        time.sleep(5)
        main()  # Перезапуск в случае ошибки


if __name__ == "__main__":
    main()
