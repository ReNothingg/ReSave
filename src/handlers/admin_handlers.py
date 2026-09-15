from __future__ import annotations

import asyncio
import secrets
import time

from aiogram import Bot, F, Router
from aiogram.exceptions import TelegramAPIError, TelegramRetryAfter
from aiogram.filters import Command, StateFilter
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup, Message

from config import Settings

from ..core.telegram_gateway import TelegramGateway
from ..core.user_stats import UserStatsManager
from ..utils.presentation import clip, panel


class AdminStates(StatesGroup):
    message = State()
    broadcast_confirmation = State()
    reset_confirmation = State()


def keyboard(rows: list[list[tuple[str, str]]]) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text=label, callback_data=data) for label, data in row]
            for row in rows
        ]
    )


def admin_keyboard() -> InlineKeyboardMarkup:
    return keyboard(
        [
            [("Статистика", "admin:stats"), ("Пользователи", "admin:users")],
            [("Рассылка", "admin:broadcast")],
            [("Сбросить счётчики", "admin:clear")],
        ]
    )


def back_keyboard() -> InlineKeyboardMarkup:
    return keyboard([[("Назад", "admin:back")]])


class AdminHandlers:
    def __init__(self, settings: Settings, stats: UserStatsManager, telegram: TelegramGateway):
        self.settings = settings
        self.stats = stats
        self.telegram = telegram
        self.running: tuple[int, str, asyncio.Event] | None = None

    def router(self) -> Router:
        router = Router(name="admin")
        router.message.filter(F.from_user.id.in_(self.settings.admin_ids), F.chat.type == "private")
        router.callback_query.filter(
            F.from_user.id.in_(self.settings.admin_ids),
            F.message.chat.type == "private",
        )
        router.message.register(self.open, Command("admin"))
        router.message.register(self.begin, Command("broadcast"))
        router.message.register(self.global_stats, Command("stats_global"))
        router.message.register(
            self.cancel,
            Command("cancel"),
            StateFilter(
                AdminStates.message,
                AdminStates.broadcast_confirmation,
                AdminStates.reset_confirmation,
            ),
        )
        router.message.register(self.receive, AdminStates.message, ~F.text.startswith("/"))
        router.callback_query.register(self.navigate, F.data.startswith("admin:"))
        router.callback_query.register(self.broadcast, F.data.startswith("broadcast:"))
        return router

    async def stats_text(self) -> str:
        items = await asyncio.to_thread(self.stats.get_all_stats)
        values = list(items.values())
        return panel(
            "Статистика бота",
            [
                f"Пользователей: {len(values)}",
                f"Скачано: {sum(item.downloads_count for item in values)}",
                f"Объём: {sum(item.total_size_mb for item in values):.1f} МБ",
                f"Неудачных загрузок: {sum(item.failed_downloads for item in values)}",
            ],
        )

    async def open(self, message: Message, state: FSMContext) -> None:
        await state.clear()
        await self.telegram.send_message(
            message.chat.id, panel("Управление ботом"), reply_markup=admin_keyboard()
        )

    async def global_stats(self, message: Message) -> None:
        await self.telegram.send_message(
            message.chat.id, await self.stats_text(), reply_markup=admin_keyboard()
        )

    async def begin(self, message: Message, state: FSMContext) -> None:
        if self.running:
            await self.telegram.send_message(
                message.chat.id, "Рассылка уже идёт. Дождитесь завершения."
            )
            return
        await state.clear()
        await state.set_state(AdminStates.message)
        await self.telegram.send_message(
            message.chat.id,
            panel(
                "Рассылка",
                [
                    "Пришлите одно сообщение: текст, фото, видео, аудио или файл.",
                    "/cancel — отменить",
                ],
            ),
            reply_markup=back_keyboard(),
        )

    async def cancel(self, message: Message, state: FSMContext) -> None:
        await state.clear()
        await self.telegram.send_message(
            message.chat.id, "Действие отменено.", reply_markup=admin_keyboard()
        )

    async def receive(self, message: Message, state: FSMContext) -> None:
        if message.media_group_id or not any(
            (
                message.text,
                message.photo,
                message.video,
                message.audio,
                message.document,
                message.animation,
            )
        ):
            await self.telegram.send_message(
                message.chat.id, "Пришлите одно сообщение без альбома."
            )
            return
        token = secrets.token_urlsafe(6)
        items = await asyncio.to_thread(self.stats.get_all_stats)
        await state.set_data(
            {
                "token": token,
                "source": message.message_id,
                "chat": message.chat.id,
                "expires": time.monotonic() + 600,
            }
        )
        await state.set_state(AdminStates.broadcast_confirmation)
        await self.telegram.send_message(
            message.chat.id,
            panel(
                "Отправить рассылку?",
                [
                    clip(message.text or message.caption or "Сообщение с файлом", 240),
                    "",
                    f"Получателей сейчас: {len(items)}",
                ],
            ),
            reply_markup=keyboard(
                [
                    [
                        ("Отправить", f"broadcast:confirm:{token}"),
                        ("Отмена", f"broadcast:cancel:{token}"),
                    ]
                ]
            ),
        )

    async def navigate(self, call: CallbackQuery, state: FSMContext) -> None:
        if not isinstance(call.message, Message):
            await self.telegram.answer_callback(call, "Сообщение недоступно.")
            return
        action = (call.data or "").split(":")[1]
        await self.telegram.answer_callback(call)
        if action == "broadcast":
            await self.begin(call.message, state)
            return
        if action == "clear":
            await self.confirm_reset(call, state)
            return
        if action == "clear-confirm":
            await self.reset(call, state)
            return
        await state.clear()
        markup = back_keyboard()
        if action == "stats":
            text = await self.stats_text()
        elif action == "users":
            items = await asyncio.to_thread(self.stats.get_all_stats)
            text = panel(
                "Пользователи",
                [
                    f"{user_id} · {value.downloads_count} загрузок"
                    for user_id, value in list(items.items())[:20]
                ]
                + ([f"Показаны первые 20 из {len(items)}."] if len(items) > 20 else []),
            )
        else:
            text, markup = panel("Управление ботом"), admin_keyboard()
        await self.telegram.edit_status(
            call.message.chat.id, call.message.message_id, text, reply_markup=markup
        )

    async def confirm_reset(self, call: CallbackQuery, state: FSMContext) -> None:
        token = secrets.token_urlsafe(6)
        await state.set_data({"reset": token, "expires": time.monotonic() + 600})
        await state.set_state(AdminStates.reset_confirmation)
        await self.telegram.edit_status(
            call.message.chat.id,
            call.message.message_id,
            panel(
                "Сбросить статистику?",
                ["Обнулю счётчики загрузок и ошибок. Список пользователей сохранится."],
            ),
            reply_markup=keyboard(
                [
                    [
                        ("Сбросить", f"admin:clear-confirm:{token}"),
                        ("Отмена", "admin:back"),
                    ]
                ]
            ),
        )

    async def reset(self, call: CallbackQuery, state: FSMContext) -> None:
        data = await state.get_data()
        parts = (call.data or "").split(":")
        valid = (
            await state.get_state() == AdminStates.reset_confirmation.state
            and len(parts) == 3
            and parts[2] == data.get("reset")
            and data.get("expires", 0) > time.monotonic()
        )
        if not valid:
            await self.telegram.edit_status(
                call.message.chat.id,
                call.message.message_id,
                "Подтверждение устарело.",
                reply_markup=admin_keyboard(),
            )
            return
        await state.clear()
        await asyncio.to_thread(self.stats.clear_all_stats)
        await self.telegram.edit_status(
            call.message.chat.id,
            call.message.message_id,
            "Статистика сброшена.",
            reply_markup=admin_keyboard(),
        )

    async def broadcast(self, call: CallbackQuery, state: FSMContext, bot: Bot) -> None:
        if not isinstance(call.message, Message):
            return
        parts = (call.data or "").split(":")
        if len(parts) != 3:
            await self.telegram.answer_callback(
                call, "Подтверждение устарело. Откройте /broadcast."
            )
            return
        _, action, token = parts
        if action == "stop":
            if self.running and self.running[:2] == (call.from_user.id, token):
                self.running[2].set()
                await self.telegram.answer_callback(call, "Остановлю после текущего сообщения.")
            else:
                await self.telegram.answer_callback(call, "Рассылка уже завершена.")
            return
        data = await state.get_data()
        current = await state.get_state()
        if (
            current != AdminStates.broadcast_confirmation.state
            or token != data.get("token")
            or data.get("expires", 0) <= time.monotonic()
        ):
            await self.telegram.answer_callback(
                call, "Подтверждение устарело. Откройте /broadcast."
            )
            return
        if action == "cancel":
            await state.clear()
            await self.telegram.answer_callback(call, "Отменено")
            await self.telegram.edit_status(
                call.message.chat.id,
                call.message.message_id,
                "Рассылка отменена.",
                reply_markup=admin_keyboard(),
            )
            return
        if action != "confirm" or self.running is not None:
            await self.telegram.answer_callback(call, "Рассылка уже идёт.")
            return
        stop = asyncio.Event()
        self.running = (call.from_user.id, token, stop)
        try:
            await state.clear()
            await self.telegram.answer_callback(call)
            await self.deliver(call, bot, data, token, stop)
        finally:
            self.running = None

    async def deliver(
        self, call: CallbackQuery, bot: Bot, data: dict, token: str, stop: asyncio.Event
    ) -> None:
        recipients = await asyncio.to_thread(self.stats.get_all_stats)
        sent = failed = 0
        for user_id in recipients:
            if stop.is_set():
                break
            if (sent + failed) % 20 == 0:
                await self.telegram.edit_status(
                    call.message.chat.id,
                    call.message.message_id,
                    panel(
                        "Рассылка",
                        [f"Отправлено: {sent} из {len(recipients)}", f"Не доставлено: {failed}"],
                    ),
                    reply_markup=keyboard([[("Остановить", f"broadcast:stop:{token}")]]),
                )
            delivered = await self.copy(bot, user_id, data, stop)
            if delivered is None:
                break
            sent += int(delivered)
            failed += int(not delivered)
            await asyncio.sleep(0.05)
        title = "Рассылка остановлена" if stop.is_set() else "Рассылка завершена"
        await self.telegram.edit_status(
            call.message.chat.id,
            call.message.message_id,
            panel(title, [f"Отправлено: {sent}", f"Не доставлено: {failed}"]),
            reply_markup=admin_keyboard(),
        )

    @staticmethod
    async def copy(bot: Bot, user_id: int, data: dict, stop: asyncio.Event) -> bool | None:
        for attempt in range(3):
            try:
                await bot.copy_message(
                    user_id,
                    from_chat_id=data["chat"],
                    message_id=data["source"],
                    request_timeout=30,
                )
                return True
            except TelegramRetryAfter as exc:
                if attempt == 2:
                    return False
                try:
                    await asyncio.wait_for(stop.wait(), timeout=float(exc.retry_after) + 0.25)
                    return None
                except TimeoutError:
                    continue
            except (TelegramAPIError, TimeoutError):
                return False
        return False


def build_admin_router(
    settings: Settings, stats: UserStatsManager, telegram: TelegramGateway
) -> Router:
    return AdminHandlers(settings, stats, telegram).router()
