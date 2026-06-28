import logging
import time
from datetime import datetime

from aiogram import Router
from aiogram.exceptions import TelegramBadRequest, TelegramForbiddenError
from aiogram.filters import Command, CommandStart
from aiogram.fsm.context import FSMContext
from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup, Message

from ..core.user_stats import get_stats_manager
from ..utils.ui_manager import get_ui_manager

logger = logging.getLogger(__name__)


def register_command_handlers(router: Router):
    ui_manager = get_ui_manager()

    async def safe_reply(message: Message, text: str, **kwargs):
        try:
            return await message.reply(text, **kwargs)
        except (TelegramBadRequest, TelegramForbiddenError) as exc:
            logger.warning("Не удалось ответить на команду в чате %s: %s", message.chat.id, exc)
            return None

    async def send_welcome(message: Message, state: FSMContext):
        await state.clear()
        welcome_text = ui_manager.format_panel(
            "ReSave",
            [
                "Скачиваю видео, аудио, превью и медиа по ссылке.",
                "",
                "Как начать:",
                "1. Отправьте ссылку на видео.",
                "2. Выберите качество или формат.",
                "3. Получите готовый файл.",
                "",
                "Платформы: YouTube, TikTok, Instagram, X/Twitter, Facebook, Vimeo, Twitch, Reddit.",
            ],
            icon="⚡",
            footer="Команды: /help · /status · /stats · /cancel",
        )
        await safe_reply(message, welcome_text)

    async def help_command(message: Message, state: FSMContext):
        await state.clear()
        help_text = ui_manager.format_panel(
            "Как пользоваться ReSave",
            [
                "Личные сообщения",
                "1. Отправьте ссылку.",
                "2. Выберите качество, MP3, GIF, субтитры или превью.",
                "3. Дождитесь готового файла.",
                "",
                "Группы",
                "1. Добавьте бота в группу.",
                "2. Отправьте ссылку.",
                "3. Бот скачает видео в среднем качестве.",
                "",
                "Если платформа поддерживается, бот попробует обработать ссылку через yt-dlp.",
            ],
            icon="📖",
        )
        await safe_reply(message, help_text)

    async def status_command(message: Message, state: FSMContext):
        await state.clear()
        from .download_handlers import get_download_manager

        manager = get_download_manager()
        if manager is None:
            await safe_reply(message, "⚙️ Менеджер загрузок еще запускается. Повторите через пару секунд.")
            return

        with manager.lock:
            user_tasks = {
                task_id: task
                for task_id, task in manager.tasks.items()
                if task.chat_id == message.chat.id and task.status in {"downloading", "pending"}
            }

        if not user_tasks:
            await safe_reply(
                message,
                ui_manager.format_panel(
                    "Активных загрузок нет",
                    ["Отправьте новую ссылку, и я покажу варианты скачивания."],
                    icon="✅",
                )
            )
            return

        status_lines = []

        for task in user_tasks.values():
            title = task.info.get("title", "Неизвестное видео")

            if task.status == "downloading":
                progress_text = ui_manager.create_progress_bar(task.progress)

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

                status_lines.append(f"🎬 {title}")
                status_lines.append(f"⬇️ {progress_text}")
                status_lines.append(f"⏱️ {remaining_str}")
                status_lines.append("")
            else:
                status_lines.append(f"🎬 {title}")
                status_lines.append("⏳ В очереди")
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

        await safe_reply(
            message,
            ui_manager.format_panel("Ваши загрузки", status_lines, icon="📦"),
            reply_markup=keyboard,
        )

    async def cancel_download(message: Message, state: FSMContext):
        await state.clear()
        from .download_handlers import get_download_manager

        manager = get_download_manager()
        if manager is None:
            await safe_reply(message, "⚙️ Менеджер загрузок еще запускается. Повторите через пару секунд.")
            return

        with manager.lock:
            user_tasks = {
                task_id: task
                for task_id, task in manager.tasks.items()
                if task.chat_id == message.chat.id and task.status in {"downloading", "pending"}
            }

        if not user_tasks:
            await safe_reply(
                message,
                ui_manager.format_panel(
                    "Отменять нечего",
                    ["Сейчас у вас нет активных загрузок."],
                    icon="⏳",
                )
            )
            return

        cancelled_count = 0
        for task_id in user_tasks:
            if manager.cancel_task(task_id):
                cancelled_count += 1

        await safe_reply(
            message,
            ui_manager.format_panel(
                "Загрузки отменены",
                [f"Отменено: {cancelled_count}", "Теперь можно начать новую."],
                icon="✅",
            )
        )

    async def stats_command(message: Message, state: FSMContext):
        await state.clear()
        stats_manager = get_stats_manager()
        user_stats = stats_manager.get_user_stats(message.chat.id)

        stats_lines = []

        if user_stats.downloads_count == 0:
            stats_lines.extend(
                [
                    "Пока нет загрузок.",
                    "Отправьте любую ссылку на видео, чтобы начать.",
                ]
            )
        else:
            stats_lines.extend(
                ui_manager.format_key_value_list(
                    [
                        ("Всего загрузок", str(user_stats.downloads_count)),
                        ("Видео", str(user_stats.total_videos)),
                        ("Аудио", str(user_stats.total_audios)),
                    ]
                )
            )
            if user_stats.total_other_downloads:
                stats_lines.append(f"• Прочее: {user_stats.total_other_downloads}")
            stats_lines.append(f"• Общий размер: {user_stats.total_size_mb:.1f} MB")

            if user_stats.failed_downloads > 0:
                stats_lines.append(f"• Ошибок загрузок: {user_stats.failed_downloads}")

            if user_stats.first_download_date:
                first_date = datetime.fromisoformat(user_stats.first_download_date)
                stats_lines.append(
                    f"• Первая загрузка: {first_date.strftime('%d.%m.%Y %H:%M')}"
                )

            if user_stats.last_download_date:
                last_date = datetime.fromisoformat(user_stats.last_download_date)
                stats_lines.append(
                    f"• Последняя загрузка: {last_date.strftime('%d.%m.%Y %H:%M')}"
                )

            stats_lines.append("")
            avg_size = user_stats.total_size_mb / user_stats.downloads_count
            stats_lines.append(f"Средний размер файла: {avg_size:.1f} MB")

            total_attempts = user_stats.downloads_count + user_stats.failed_downloads
            if total_attempts > 0:
                success_rate = (user_stats.downloads_count / total_attempts) * 100
                stats_lines.append(f"Успешные загрузки: {success_rate:.1f}%")

        await safe_reply(
            message,
            ui_manager.format_panel("Ваша статистика", stats_lines, icon="📊")
        )

    router.message.register(send_welcome, CommandStart())
    router.message.register(help_command, Command("help"))
    router.message.register(status_command, Command("status"))
    router.message.register(cancel_download, Command("cancel"))
    router.message.register(stats_command, Command("stats"))
