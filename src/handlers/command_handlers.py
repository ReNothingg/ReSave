from __future__ import annotations

import asyncio
from dataclasses import dataclass

from aiogram import F, Router
from aiogram.filters import Command, CommandStart
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, InlineKeyboardMarkup, Message

from config import Settings

from ..core.download_manager import DownloadManager
from ..core.models import TaskStatus
from ..core.selection_store import SelectionStore
from ..core.telegram_gateway import TelegramGateway
from ..core.user_stats import UserStatsManager
from ..utils.cards import downloads_card, help_card, stats_card
from ..utils.presentation import MessageContent, home_card, panel
from ..utils.theme import button


@dataclass(frozen=True, slots=True)
class Screen:
    text: MessageContent
    keyboard: InlineKeyboardMarkup


def menu_keyboard(user_id: int, *, cancellable: bool = False) -> InlineKeyboardMarkup:
    rows = [
        [
            button(
                "Загрузки", icon="downloads", style="primary", callback_data=f"ui:status:{user_id}"
            ),
            button(
                "Статистика", icon="stats", style="success", callback_data=f"ui:stats:{user_id}"
            ),
        ],
        [
            button(
                "Как пользоваться", icon="help", style="primary", callback_data=f"ui:help:{user_id}"
            ),
            button("Главная", icon="brand", callback_data=f"ui:home:{user_id}"),
        ],
    ]
    if cancellable:
        rows.insert(
            0, [button("✕ Отменить загрузки", style="danger", callback_data=f"ui:cancel:{user_id}")]
        )
    return InlineKeyboardMarkup(inline_keyboard=rows)


class CommandHandlers:
    def __init__(
        self,
        manager: DownloadManager,
        stats: UserStatsManager,
        telegram: TelegramGateway,
        settings: Settings,
        selections: SelectionStore,
    ):
        self.manager = manager
        self.stats = stats
        self.telegram = telegram
        self.settings = settings
        self.selections = selections

    def router(self) -> Router:
        router = Router(name="commands")
        router.message.register(self.start, CommandStart())
        router.message.register(self.help, Command("help"))
        router.message.register(self.status, Command("status"))
        router.message.register(self.stats_command, Command("stats"))
        router.message.register(self.cancel, Command("cancel"))
        router.callback_query.register(self.menu, F.data.startswith("ui:"))
        router.callback_query.register(self.legacy_cancel, F.data == "cancel_all_downloads")
        return router

    async def screen(self, action: str, user_id: int, chat_id: int) -> Screen:
        keyboard = menu_keyboard(user_id)
        if action == "help":
            return Screen(
                help_card(
                    upload_limit=self.settings.effective_upload_limit,
                    playlist_limit=self.settings.max_playlist_items,
                    user_limit=self.settings.max_tasks_per_user,
                ),
                keyboard,
            )
        if action == "status":
            return self.status_screen(user_id, chat_id)
        if action == "stats":
            value = await asyncio.to_thread(self.stats.get_user_stats, user_id)
            return Screen(stats_card(value), keyboard)
        if action == "cancel":
            count = self.manager.cancel_for_user(user_id, chat_id=chat_id)
            choices = self.selections.cancel_for_user(user_id, chat_id=chat_id)
            lines = [f"Отменено загрузок: {count}."] if count else ["Нет загрузок для отмены."]
            if choices:
                lines.append("Проверка ссылок и выбор формата отменены.")
            if any(
                t.status == TaskStatus.UPLOADING
                for t in self.manager.snapshot(user_id=user_id, chat_id=chat_id)
            ):
                lines.append("Файл уже отправляется. Дождитесь завершения.")
            return Screen(panel("Отмена", lines, icon="done"), keyboard)
        return Screen(
            home_card(
                upload_limit=self.settings.effective_upload_limit,
                playlist_limit=self.settings.max_playlist_items,
                user_limit=self.settings.max_tasks_per_user,
            ),
            keyboard,
        )

    def status_screen(self, user_id: int, chat_id: int) -> Screen:
        tasks = self.manager.snapshot(user_id=user_id, chat_id=chat_id)
        return Screen(
            downloads_card(tasks),
            menu_keyboard(user_id, cancellable=bool(tasks)),
        )

    async def show(self, message: Message, action: str) -> None:
        if message.from_user is None:
            return
        screen = await self.screen(action, message.from_user.id, message.chat.id)
        await self.telegram.send_message(
            message.chat.id,
            screen.text,
            reply_markup=screen.keyboard,
            reply_parameters=self.telegram._reply(message.message_id),
        )

    async def start(self, message: Message, state: FSMContext) -> None:
        await state.clear()
        if message.from_user and message.chat.type == "private":
            await asyncio.to_thread(self.stats.ensure_user, message.from_user.id)
        await self.show(message, "start")

    async def help(self, message: Message) -> None:
        await self.show(message, "help")

    async def status(self, message: Message) -> None:
        await self.show(message, "status")

    async def stats_command(self, message: Message) -> None:
        await self.show(message, "stats")

    async def cancel(self, message: Message, state: FSMContext) -> None:
        await state.clear()
        await self.show(message, "cancel")

    async def menu(self, call: CallbackQuery, state: FSMContext) -> None:
        if not isinstance(call.message, Message):
            await self.telegram.answer_callback(call, "Сообщение недоступно.")
            return
        parts = (call.data or "").split(":")
        if len(parts) != 3 or parts[2] != str(call.from_user.id):
            await self.telegram.answer_callback(call, "Откройте своё меню командой /start.")
            return
        if parts[1] not in {"help", "status", "stats", "cancel", "home"}:
            await self.telegram.answer_callback(call, "Кнопка устарела. Откройте /start.")
            return
        if parts[1] == "cancel":
            await state.clear()
        await self.telegram.answer_callback(call)
        screen = await self.screen(parts[1], call.from_user.id, call.message.chat.id)
        await self.telegram.edit_status(
            call.message.chat.id,
            call.message.message_id,
            screen.text,
            reply_markup=screen.keyboard,
        )

    async def legacy_cancel(self, call: CallbackQuery) -> None:
        await self.telegram.answer_callback(call, "Откройте /status, чтобы отменить загрузки.")


def build_command_router(
    manager: DownloadManager,
    stats: UserStatsManager,
    telegram: TelegramGateway,
    settings: Settings,
    selections: SelectionStore,
) -> Router:
    return CommandHandlers(manager, stats, telegram, settings, selections).router()
