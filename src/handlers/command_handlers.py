from __future__ import annotations

import asyncio
from datetime import datetime

from aiogram import F, Router
from aiogram.exceptions import TelegramBadRequest, TelegramForbiddenError
from aiogram.filters import Command, CommandStart
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup, Message

from ..core.download_manager import DownloadManager
from ..core.models import TaskStatus
from ..core.telegram_gateway import TelegramGateway
from ..core.user_stats import UserStatsManager
from ..utils.presentation import panel, progress_bar, rich_panel


def _main_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="📖 Возможности", callback_data="ui:help", style="primary"
                ),
                InlineKeyboardButton(
                    text="📦 Загрузки", callback_data="ui:status", style="primary"
                ),
            ],
            [
                InlineKeyboardButton(
                    text="📊 Моя статистика", callback_data="ui:stats", style="success"
                )
            ],
        ]
    )


def build_command_router(
    manager: DownloadManager,
    stats: UserStatsManager,
    telegram: TelegramGateway,
) -> Router:
    router = Router(name="commands")

    async def safe_reply(message: Message, text: str, **kwargs):
        try:
            return await message.reply(text, parse_mode="HTML", **kwargs)
        except (TelegramBadRequest, TelegramForbiddenError):
            return None

    async def safe_rich_reply(
        message: Message,
        *,
        rich_html: str,
        fallback_html: str,
        reply_markup: InlineKeyboardMarkup | None = None,
    ):
        try:
            return await telegram.send_rich_message(
                message.chat.id,
                rich_html=rich_html,
                fallback_html=fallback_html,
                reply_to=message.message_id,
                reply_markup=reply_markup,
            )
        except (TelegramBadRequest, TelegramForbiddenError):
            return None

    async def start(message: Message, state: FSMContext) -> None:
        await state.clear()
        if message.from_user:
            await asyncio.to_thread(stats.ensure_user, message.from_user.id)
        await safe_rich_reply(
            message,
            rich_html=rich_panel(
                "ReSave",
                lead="Отправьте ссылку — бот сам определит источник и предложит подходящие форматы.",
                sections=(
                    (
                        "Что можно скачать",
                        (
                            "Видео в доступном качестве",
                            "MP3, превью и субтитры",
                            "Фото и публикации из соцсетей",
                        ),
                    ),
                    (
                        "Как это работает",
                        (
                            "Отправьте ссылку",
                            "Выберите формат цветной кнопкой",
                            "Получите готовый файл",
                        ),
                    ),
                ),
            ),
            fallback_html=panel(
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
            reply_markup=_main_keyboard(),
        )

    async def help_command(message: Message, state: FSMContext) -> None:
        await state.clear()
        await safe_rich_reply(
            message,
            rich_html=rich_panel(
                "Как скачать медиа",
                lead="Просто пришлите ссылку в этот чат. В группе бот автоматически выберет видео до 720p.",
                sections=(
                    (
                        "Доступные действия",
                        (
                            "Выбор разрешения или максимального качества",
                            "Извлечение MP3",
                            "Скачивание превью, субтитров и GIF",
                        ),
                    ),
                    (
                        "Команды",
                        (
                            "/status — текущие загрузки",
                            "/cancel — отменить загрузки",
                            "/stats — личная статистика",
                        ),
                    ),
                ),
            ),
            fallback_html=panel(
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
            reply_markup=_main_keyboard(),
        )

    async def status(message: Message, state: FSMContext, user_id: int | None = None) -> None:
        await state.clear()
        target_user_id = user_id or (message.from_user.id if message.from_user else None)
        if target_user_id is None:
            return
        tasks = manager.snapshot(user_id=target_user_id, chat_id=message.chat.id)
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
                [
                    InlineKeyboardButton(
                        text="❌ Отменить все",
                        callback_data="cancel_all_downloads",
                        style="danger",
                    )
                ]
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

    async def stats_command(
        message: Message, state: FSMContext, user_id: int | None = None
    ) -> None:
        await state.clear()
        target_user_id = user_id or (message.from_user.id if message.from_user else None)
        if target_user_id is None:
            return
        value = await asyncio.to_thread(stats.get_user_stats, target_user_id)
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
        await safe_rich_reply(
            message,
            rich_html=rich_panel(
                "Ваша статистика",
                lead="Личная история успешных и неудачных загрузок.",
                sections=(("Результаты", tuple(lines)),),
            ),
            fallback_html=panel("Ваша статистика", lines, icon="📊"),
            reply_markup=_main_keyboard(),
        )

    async def menu_callback(callback: CallbackQuery, state: FSMContext) -> None:
        await callback.answer()
        if not callback.message or not isinstance(callback.message, Message):
            return
        action = callback.data or ""
        if action == "ui:help":
            await help_command(callback.message, state)
        elif action == "ui:status":
            await status(callback.message, state, user_id=callback.from_user.id)
        elif action == "ui:stats":
            await stats_command(callback.message, state, user_id=callback.from_user.id)

    router.message.register(start, CommandStart())
    router.message.register(help_command, Command("help"))
    router.message.register(status, Command("status"))
    router.message.register(cancel, Command("cancel"))
    router.message.register(stats_command, Command("stats"))
    router.callback_query.register(menu_callback, F.data.in_({"ui:help", "ui:status", "ui:stats"}))
    return router


def _format_date(value: str) -> str:
    try:
        return datetime.fromisoformat(value).astimezone().strftime("%d.%m.%Y %H:%M")
    except ValueError:
        return value
