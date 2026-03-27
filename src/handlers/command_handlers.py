import logging
import time
from datetime import datetime

from aiogram import Router
from aiogram.filters import Command, CommandStart
from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup, Message

from ..core.user_stats import get_stats_manager
from ..utils.ui_manager import get_ui_manager

logger = logging.getLogger(__name__)


def register_command_handlers(router: Router):
    ui_manager = get_ui_manager()

    async def send_welcome(message: Message):
        welcome_text = "\n".join(
            [
                "Привет! Добро пожаловать в ReSave.",
                "",
                "Я скачиваю видео и медиа по ссылке с YouTube, TikTok, Instagram и других платформ.",
                "",
                "Как начать:",
                "1. Отправьте ссылку на видео.",
                "2. Выберите качество или формат.",
                "3. Получите готовый файл.",
                "",
                "Inline-режим:",
                "Введите @ReSafeBot и ссылку в любом чате.",
                "",
                "Поддерживаемые платформы:",
                "YouTube, TikTok, Instagram, Twitter, Facebook, Vimeo, Twitch, Reddit.",
            ]
        )
        await message.reply(welcome_text)

    async def help_command(message: Message):
        help_text = "\n".join(
            [
                "ReSave: инструкция",
                "",
                "Личные сообщения:",
                "1. Отправьте ссылку.",
                "2. Выберите качество или формат.",
                "3. Дождитесь готового файла.",
                "",
                "Группы:",
                "1. Добавьте бота в группу.",
                "2. Отправьте ссылку.",
                "3. Бот автоматически скачает видео в среднем качестве.",
                "",
                "Inline:",
                "1. Введите @ReSafeBot [ссылка].",
                "2. Подождите несколько секунд.",
                "3. Отправьте готовое видео в чат.",
                "",
                "Если что-то сломалось, отправьте /start и попробуйте снова.",
            ]
        )
        await message.reply(help_text)

    async def status_command(message: Message):
        from .download_handlers import get_download_manager

        manager = get_download_manager()
        if manager is None:
            await message.reply("Менеджер загрузок еще не инициализирован.")
            return

        with manager.lock:
            user_tasks = {
                task_id: task
                for task_id, task in manager.tasks.items()
                if task.chat_id == message.chat.id and task.status in {"downloading", "pending"}
            }

        if not user_tasks:
            await message.reply("У вас нет активных загрузок. Отправьте новую ссылку.")
            return

        status_lines = ["Ваши загрузки в работе", ""]

        for task in user_tasks.values():
            title = task.info.get("title", "Неизвестное видео")

            if task.status == "downloading":
                progress_bar = manager._generate_progress_bar(task.progress)
                progress_text = f"{int(task.progress * 100)}% {progress_bar}"

                if task.started_at and task.progress > 0.05:
                    elapsed = time.time() - task.started_at
                    total_estimated = elapsed / task.progress
                    remaining = total_estimated - elapsed
                    if remaining > 0:
                        remaining_str = f"Осталось: {manager._format_time(remaining)}"
                    else:
                        remaining_str = "Завершается..."
                else:
                    remaining_str = "Вычисляется..."

                status_lines.append(title)
                status_lines.append(f"├ Загрузка: {progress_text}")
                status_lines.append(f"└ {remaining_str}")
                status_lines.append("")
            else:
                status_lines.append(title)
                status_lines.append("└ В очереди")
                status_lines.append("")

        keyboard = InlineKeyboardMarkup(
            inline_keyboard=[
                [
                    InlineKeyboardButton(
                        text="❌ Отменить все",
                        callback_data="cancel_all_downloads",
                    )
                ]
            ]
        )

        await message.reply("\n".join(status_lines).strip(), reply_markup=keyboard)

    async def cancel_download(message: Message):
        from .download_handlers import get_download_manager

        manager = get_download_manager()
        if manager is None:
            await message.reply("Менеджер загрузок еще не инициализирован.")
            return

        with manager.lock:
            user_tasks = {
                task_id: task
                for task_id, task in manager.tasks.items()
                if task.chat_id == message.chat.id and task.status in {"downloading", "pending"}
            }

        if not user_tasks:
            await message.reply(f"{ui_manager.emojis['pending']} Нет активных загрузок для отмены.")
            return

        cancelled_count = 0
        for task_id in user_tasks:
            if manager.cancel_task(task_id):
                cancelled_count += 1

        await message.reply(
            f"{ui_manager.emojis['success']} Отменено {cancelled_count} загрузок. "
            "Теперь можно начать новую."
        )

    async def stats_command(message: Message):
        stats_manager = get_stats_manager()
        user_stats = stats_manager.get_user_stats(message.chat.id)

        stats_lines = ["Ваша статистика", ""]

        if user_stats.downloads_count == 0:
            stats_lines.extend(
                [
                    "У вас еще нет загрузок.",
                    "",
                    "Отправьте любую ссылку на видео, чтобы начать.",
                ]
            )
        else:
            stats_lines.append(f"Всего загрузок: {user_stats.downloads_count}")
            stats_lines.append(f"Видео: {user_stats.total_videos}")
            stats_lines.append(f"Аудио: {user_stats.total_audios}")
            stats_lines.append(f"Общий размер: {user_stats.total_size_mb:.1f} MB")

            if user_stats.failed_downloads > 0:
                stats_lines.append(f"Ошибок загрузок: {user_stats.failed_downloads}")

            if user_stats.first_download_date:
                first_date = datetime.fromisoformat(user_stats.first_download_date)
                stats_lines.append(
                    f"Первая загрузка: {first_date.strftime('%d.%m.%Y %H:%M')}"
                )

            if user_stats.last_download_date:
                last_date = datetime.fromisoformat(user_stats.last_download_date)
                stats_lines.append(
                    f"Последняя загрузка: {last_date.strftime('%d.%m.%Y %H:%M')}"
                )

            stats_lines.append("")
            avg_size = user_stats.total_size_mb / user_stats.downloads_count
            stats_lines.append(f"Средний размер файла: {avg_size:.1f} MB")

            total_attempts = user_stats.downloads_count + user_stats.failed_downloads
            if total_attempts > 0:
                success_rate = (
                    (user_stats.total_videos + user_stats.total_audios) / total_attempts
                ) * 100
                stats_lines.append(f"Успешные загрузки: {success_rate:.1f}%")

        await message.reply("\n".join(stats_lines).strip())

    router.message.register(send_welcome, CommandStart())
    router.message.register(help_command, Command("help"))
    router.message.register(status_command, Command("status"))
    router.message.register(cancel_download, Command("cancel"))
    router.message.register(stats_command, Command("stats"))
