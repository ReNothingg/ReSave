from __future__ import annotations

import asyncio
from datetime import datetime

from aiogram import Router
from aiogram.exceptions import TelegramBadRequest, TelegramForbiddenError
from aiogram.filters import Command, CommandStart
from aiogram.fsm.context import FSMContext
from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup, Message

from ..core.download_manager import DownloadManager
from ..core.models import TaskStatus
from ..core.user_stats import UserStatsManager
from ..utils.presentation import panel, progress_bar


def build_command_router(manager: DownloadManager, stats: UserStatsManager) -> Router:
    router = Router(name="commands")

    async def safe_reply(message: Message, text: str, **kwargs):
        try:
            return await message.reply(text, parse_mode="HTML", **kwargs)
        except (TelegramBadRequest, TelegramForbiddenError):
            return None

    async def start(message: Message, state: FSMContext) -> None:
        await state.clear()
        if message.from_user:
            await asyncio.to_thread(stats.ensure_user, message.from_user.id)
        await safe_reply(
            message,
            panel(
                "ReSave",
                [
                    "Скачиваю видео, аудио, превью, субтитры и фото по ссылке.",
                    "",
                    "1. Отправьте ссылку.",
                    "2. Выберите формат и качество.",
                    "3. Получите готовый файл.",
                    "",
                    "Работаю с YouTube, TikTok, Instagram, X/Twitter, Facebook, Vimeo, Twitch, Reddit и другими источниками yt-dlp.",
                ],
                icon="⚡",
            ),
        )

    async def help_command(message: Message, state: FSMContext) -> None:
        await state.clear()
        await safe_reply(
            message,
            panel(
                "Как пользоваться",
                [
                    "В личном чате отправьте ссылку и выберите формат кнопкой.",
                    "В группе бот автоматически скачивает видео до 720p в ответ на ссылку.",
                    "Плейлисты добавляются в очередь, но ограничены безопасным числом элементов.",
                    "",
                    "/status — текущие загрузки",
                    "/cancel — отменить свои загрузки",
                    "/stats — личная статистика",
                ],
                icon="📖",
            ),
        )

    async def status(message: Message, state: FSMContext) -> None:
        await state.clear()
        if not message.from_user:
            return
        tasks = manager.snapshot(user_id=message.from_user.id, chat_id=message.chat.id)
        if not tasks:
            await safe_reply(
                message,
                panel("Активных загрузок нет", ["Отправьте новую ссылку."], icon="✅"),
            )
            return

        lines: list[str] = []
        for task in tasks:
            lines.append(task.title)
            if task.status == TaskStatus.PENDING:
                lines.append("⏳ В очереди")
            else:
                lines.append(f"{task.phase}: {progress_bar(task.progress)}")
            lines.append("")
        keyboard = InlineKeyboardMarkup(
            inline_keyboard=[
                [InlineKeyboardButton(text="❌ Отменить все", callback_data="cancel_all_downloads")]
            ]
        )
        await safe_reply(message, panel("Ваши загрузки", lines, icon="📦"), reply_markup=keyboard)

    async def cancel(message: Message, state: FSMContext) -> None:
        await state.clear()
        if not message.from_user:
            return
        count = manager.cancel_for_user(message.from_user.id, chat_id=message.chat.id)
        title = "Загрузки отменены" if count else "Отменять нечего"
        await safe_reply(
            message, panel(title, [f"Отменено: {count}"], icon="✅" if count else "⏳")
        )

    async def stats_command(message: Message, state: FSMContext) -> None:
        await state.clear()
        if not message.from_user:
            return
        value = await asyncio.to_thread(stats.get_user_stats, message.from_user.id)
        if value.downloads_count == 0:
            lines = ["Пока нет завершённых загрузок."]
        else:
            attempts = value.downloads_count + value.failed_downloads
            success = value.downloads_count / attempts * 100 if attempts else 0
            lines = [
                f"Всего: {value.downloads_count}",
                f"Видео: {value.total_videos}",
                f"Аудио: {value.total_audios}",
                f"Прочее: {value.total_other_downloads}",
                f"Ошибок: {value.failed_downloads}",
                f"Объём: {value.total_size_mb:.1f} MB",
                f"Успешность: {success:.1f}%",
            ]
            if value.first_download_date:
                lines.append(f"Первая загрузка: {_format_date(value.first_download_date)}")
            if value.last_download_date:
                lines.append(f"Последняя загрузка: {_format_date(value.last_download_date)}")
        await safe_reply(message, panel("Ваша статистика", lines, icon="📊"))

    router.message.register(start, CommandStart())
    router.message.register(help_command, Command("help"))
    router.message.register(status, Command("status"))
    router.message.register(cancel, Command("cancel"))
    router.message.register(stats_command, Command("stats"))
    return router


def _format_date(value: str) -> str:
    try:
        return datetime.fromisoformat(value).astimezone().strftime("%d.%m.%Y %H:%M")
    except ValueError:
        return value
