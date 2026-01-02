"""
/start, /help, /status, /cancel, /stats
"""
import logging
from telebot import types
from datetime import datetime
from ..utils.safe_formatter_new import get_safe_formatter

logger = logging.getLogger(__name__)


def is_admin(user_id: int) -> bool:
    import config
    return user_id in config.ADMIN_IDS


def register_command_handlers(bot):
    from ..utils.ui_manager import get_ui_manager

    ui_manager = get_ui_manager()

    @bot.message_handler(commands=['start'])
    def send_welcome(message):
        safe_formatter = get_safe_formatter()

        welcome_lines = [
            f"Привет! Добро пожаловать в ReSave",
            "",
            "Я - удобный бот для скачивания видео с YouTube, TikTok, Instagram и многих других платформ.",
            "",
            "Как начать?",
            "├ Просто отправьте ссылку на видео",
            "├ Выберите качество",
            "└ Наслаждайтесь!",
            "",
            "Inline-режим для любого чата:",
            "Введите @ReSafeBot и ссылку - видео загрузится мгновенно!",
            "",
            "Поддерживаемые платформы:",
            "YouTube • TikTok • Instagram • Twitter • Facebook • Vimeo • Twitch • Reddit",
            "",
            "Готовы? Отправьте ссылку прямо сейчас!"
        ]

        welcome_text = "\n".join(welcome_lines)
        safe_formatter.safe_reply_to(bot, message, welcome_text)

    @bot.message_handler(commands=['help'])
    def help_command(message):
        safe_formatter = get_safe_formatter()

        help_lines = [
            "ReSave: Полная инструкция",
            "",
            "Обычный режим (в личных сообщениях):",
            "1️⃣ Отправьте ссылку на видео в этот чат",
            "2️⃣ Выберите качество или формат (видео/аудио/превью)",
            "3️⃣ Получите готовый файл!",
            "",
            "Режим в группах:",
            "1️⃣ Добавьте бота в группу",
            "2️⃣ Отправьте ссылку на видео",
            "3️⃣ Бот автоматически скачает видео в среднем качестве!",
            "",
            "Inline-режим (в любом чате):",
            "1️⃣ Введите @ReSafeBot [ссылка]",
            "2️⃣ Подождите 5-15 сек",
            "3️⃣ Отправьте готовое видео в чат!",
            "",
            "Если что-то не так - напишите /start. Удачи!",
        ]

        help_text = "\n".join(help_lines)
        safe_formatter.safe_reply_to(bot, message, help_text)

    @bot.message_handler(commands=['status'])
    def status_command(message):
        from .download_handlers import get_download_manager
        from ..utils.markdown_escape import format_safe_title
        safe_formatter = get_safe_formatter()

        manager = get_download_manager()

        with manager.lock:
            user_tasks = {k: v for k, v in manager.tasks.items()
                          if v.chat_id == message.chat.id and v.status in ["downloading", "pending"]}

        if not user_tasks:
            safe_formatter.safe_reply_to(bot, message, f"У вас нет активных загрузок. Отправьте новую ссылку!")
            return

        status_lines = ["Ваши загрузки в работе", ""]

        for task_id, task in user_tasks.items():
            title = task.info.get("title", "Неизвестное видео")

            if task.status == "downloading":
                progress_bar = manager._generate_progress_bar(task.progress)
                progress_text = f"{int(task.progress * 100)}% {progress_bar}"

                if task.started_at and task.progress > 0.05:
                    import time
                    elapsed = time.time() - task.started_at
                    total_estimated = elapsed / task.progress
                    remaining = total_estimated - elapsed
                    if remaining > 0:
                        remaining_str = f"Осталось: {manager._format_time(remaining)}"
                    else:
                        remaining_str = f"Завершается..."
                else:
                    remaining_str = f"Вычисляется..."

                status_lines.append(f"{title}")
                status_lines.append(f"├ Загрузка: {progress_text}")
                status_lines.append(f"└ {remaining_str}\n")
            else:
                status_lines.append(f"{title}")
                status_lines.append(f"└ В очереди (скоро начнётся)\n")

        keyboard = types.InlineKeyboardMarkup()
        keyboard.add(types.InlineKeyboardButton(
            "❌ Отменить все",
            callback_data="cancel_all_downloads"
        ))

        status_text = "\n".join(status_lines)
        safe_formatter.safe_reply_to(bot, message, status_text, reply_markup=keyboard)

    @bot.message_handler(is_cancel_download=True)
    def cancel_download(message):
        from .download_handlers import get_download_manager
        manager = get_download_manager()

        with manager.lock:
            user_tasks = {k: v for k, v in manager.tasks.items()
                         if v.chat_id == message.chat.id and v.status in ["downloading", "pending"]}

        if not user_tasks:
            bot.reply_to(message, f"{ui_manager.emojis['pending']} Нет активных загрузок для отмены.")
            return

        cancelled_count = 0
        for task_id in user_tasks:
            if manager.cancel_task(task_id):
                cancelled_count += 1

        bot.reply_to(message, f"{ui_manager.emojis['success']} Отменено {cancelled_count} загрузок! Теперь можно начать новую. 📹")

    @bot.message_handler(commands=['stats'])
    def stats_command(message):
        from ..core.user_stats import get_stats_manager
        safe_formatter = get_safe_formatter()

        stats_manager = get_stats_manager()
        user_stats = stats_manager.get_user_stats(message.chat.id)

        stats_lines = ["Ваша статистика", ""]

        if user_stats.downloads_count == 0:
            stats_lines.append("У вас ещё нет загрузок!")
            stats_lines.append("")
            stats_lines.append("Отправьте любую ссылку на видео, чтобы начать!")
        else:
            stats_lines.append(f"Всего загрузок: {user_stats.downloads_count}")
            stats_lines.append(f"Видео: {user_stats.total_videos}")
            stats_lines.append(f"Аудио: {user_stats.total_audios}")
            stats_lines.append(f"Общий размер: {user_stats.total_size_mb:.1f} MB")
            stats_lines.append("")

            if user_stats.failed_downloads > 0:
                stats_lines.append(f"Ошибок загрузок: {user_stats.failed_downloads}")
                stats_lines.append("")

            if user_stats.first_download_date:
                first_date = datetime.fromisoformat(user_stats.first_download_date)
                stats_lines.append(f"Первая загрузка: {first_date.strftime('%d.%m.%Y %H:%M')}")

            if user_stats.last_download_date:
                last_date = datetime.fromisoformat(user_stats.last_download_date)
                stats_lines.append(f"Последняя загрузка: {last_date.strftime('%d.%m.%Y %H:%M')}")

            stats_lines.append("")
            stats_lines.append("=" * 40)

            avg_size = user_stats.total_size_mb / user_stats.downloads_count
            stats_lines.append(f"Средний размер файла: {avg_size:.1f} MB")

            if user_stats.total_videos > 0:
                success_rate = ((user_stats.total_videos + user_stats.total_audios) /
                              (user_stats.downloads_count + user_stats.failed_downloads) * 100)
                stats_lines.append(f"Успешные загрузки: {success_rate:.1f}%")

            stats_lines.append("")
            stats_lines.append("Спасибо что используете ReSave!")

        stats_text = "\n".join(stats_lines)
        safe_formatter.safe_reply_to(bot, message, stats_text)
