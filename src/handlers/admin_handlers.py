from __future__ import annotations

import asyncio
import secrets
import time

from aiogram import Bot, F, Router
from aiogram.exceptions import TelegramAPIError
from aiogram.filters import Command, StateFilter
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import CallbackQuery, InlineKeyboardMarkup, Message

from config import Settings

from ..core.broadcast import Campaign, deliver_campaign
from ..core.telegram_gateway import TelegramGateway
from ..core.user_stats import UserStatsManager
from ..utils.presentation import MessageContent, panel
from ..utils.theme import button
from .broadcast_views import draft_controls, notice, report_card, report_controls


class AdminStates(StatesGroup):
    message = State()
    preparing = State()
    broadcast_confirmation = State()
    reset_confirmation = State()


def keyboard(rows: list[list[tuple[str, str]]]) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[[admin_button(label, data) for label, data in row] for row in rows]
    )


def admin_button(label: str, data: str):
    action = data.split(":")[1]
    style = "danger" if action in {"clear", "clear-confirm", "cancel", "stop"} else "primary"
    if action == "confirm":
        style = "success"
    icons = {"stats": "stats", "broadcast": "subtitle", "users": "downloads", "confirm": "done"}
    return button(label, icon=icons.get(action), style=style, callback_data=data)


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
        self.running: Campaign | None = None

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
                AdminStates.preparing,
                AdminStates.broadcast_confirmation,
                AdminStates.reset_confirmation,
            ),
        )
        router.message.register(
            self.receive,
            StateFilter(AdminStates.message, AdminStates.broadcast_confirmation),
            ~F.text.startswith("/"),
        )
        router.callback_query.register(self.navigate, F.data.startswith("admin:"))
        router.callback_query.register(self.broadcast, F.data.startswith("broadcast:"))
        return router

    async def stats_text(self) -> MessageContent:
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
            icon="stats",
            table=True,
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
                message.chat.id,
                report_card(self.running),
                reply_markup=report_controls(self.running)
                if state.key.user_id == self.running.owner_id
                else back_keyboard(),
            )
            return
        await state.clear()
        await state.set_data({"compose_token": secrets.token_urlsafe(6)})
        await state.set_state(AdminStates.message)
        await self.telegram.send_message(
            message.chat.id,
            notice(
                "Новая рассылка",
                [
                    "Пришлите готовый пост: текст или один файл с подписью.",
                    "Покажу, как его увидят пользователи. После этого можно изменить пост или отправить.",
                    "Форматирование и медиа сохранятся. /cancel — выйти.",
                ],
            ),
            reply_markup=back_keyboard(),
        )

    async def cancel(self, message: Message, state: FSMContext) -> None:
        await state.clear()
        await self.telegram.send_message(
            message.chat.id,
            notice("Черновик отменён", ["Никому ничего не отправлено."]),
            reply_markup=back_keyboard(),
        )

    async def receive(self, message: Message, state: FSMContext, bot: Bot) -> None:
        if message.media_group_id or not any(
            (
                message.text,
                message.photo,
                message.video,
                message.audio,
                message.document,
                message.animation,
                message.voice,
                message.video_note,
                message.sticker,
                getattr(message, "rich_message", None),
            )
        ):
            await self.telegram.send_message(
                message.chat.id,
                notice(
                    "Не удалось подготовить пост",
                    ["Пришлите текст или один файл. Альбомы пока не поддерживаются."],
                ),
            )
            return
        if self.running:
            await self.telegram.send_message(message.chat.id, report_card(self.running))
            return
        original = await state.get_data()
        compose_token = original.get("compose_token")
        await state.set_state(AdminStates.preparing)
        try:
            preview = await bot.copy_message(
                message.chat.id,
                from_chat_id=message.chat.id,
                message_id=message.message_id,
                reply_markup=InlineKeyboardMarkup(inline_keyboard=[]),
                request_timeout=30,
            )
        except (TelegramAPIError, TimeoutError):
            if (await state.get_data()).get("compose_token") == compose_token:
                await state.set_state(AdminStates.message)
                await self.telegram.send_message(
                    message.chat.id,
                    notice(
                        "Предпросмотр не готов",
                        [
                            "Не удалось скопировать пост. Пришлите его ещё раз; рассылка не запущена."
                        ],
                    ),
                )
            return
        if await state.get_state() != AdminStates.preparing.state:
            return
        if (await state.get_data()).get("compose_token") != compose_token:
            return
        recipients = list(await asyncio.to_thread(self.stats.get_all_stats))
        if (await state.get_data()).get("compose_token") != compose_token:
            return
        token = secrets.token_urlsafe(6)
        control = await self.telegram.send_message(
            message.chat.id,
            notice(
                "Пост готов к отправке",
                [
                    "Выше — точная копия сообщения для пользователей.",
                    f"Получателей: {len(recipients)}. Список зафиксирован для этой рассылки.",
                    "Служебные кнопки и этот экран пользователям не отправляются.",
                ],
            ),
            reply_markup=draft_controls(token, len(recipients)),
        )
        if (await state.get_data()).get("compose_token") != compose_token:
            await self.telegram.edit_status(
                control.chat.id,
                control.message_id,
                notice("Черновик отменён", ["Никому ничего не отправлено."]),
                reply_markup=None,
            )
            return
        await state.set_data(
            {
                "compose_token": compose_token,
                "token": token,
                "source": preview.message_id,
                "chat": message.chat.id,
                "control": control.message_id,
                "recipients": recipients,
                "expires": time.monotonic() + 600,
            }
        )
        await state.set_state(AdminStates.broadcast_confirmation)

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
                call, "Откройте /broadcast, чтобы подготовить новый пост."
            )
            return
        _, action, token = parts
        if action == "stop":
            await self.stop_campaign(call, token)
            return
        data = await state.get_data()
        if not await self.valid_draft(call, state, data, token):
            return
        if action in {"cancel", "edit"}:
            await self.telegram.answer_callback(call)
            await state.clear()
            await self.telegram.edit_status(
                call.message.chat.id,
                call.message.message_id,
                notice("Черновик снят с отправки", ["Никому ничего не отправлено."]),
                reply_markup=back_keyboard() if action == "cancel" else None,
            )
            if action == "edit":
                await self.begin(call.message, state)
            return
        if action != "confirm":
            await self.telegram.answer_callback(call, "Действие недоступно.")
            return
        await self.start_campaign(call, state, bot, data, token)

    async def valid_draft(
        self, call: CallbackQuery, state: FSMContext, data: dict, token: str
    ) -> bool:
        current = await state.get_state()
        if (
            current != AdminStates.broadcast_confirmation.state
            or token != data.get("token")
            or data.get("expires", 0) <= time.monotonic()
            or data.get("control") != call.message.message_id
        ):
            await self.telegram.answer_callback(call, "Этот черновик устарел. Откройте /broadcast.")
            return False
        return True

    async def start_campaign(
        self, call: CallbackQuery, state: FSMContext, bot: Bot, data: dict, token: str
    ) -> None:
        if self.running:
            await self.telegram.answer_callback(call, "Рассылка уже идёт.")
            return
        if not data["recipients"]:
            await self.telegram.answer_callback(call, "В базе пока нет получателей.")
            return
        campaign = Campaign(
            owner_id=call.from_user.id,
            token=token,
            chat_id=data["chat"],
            source_id=data["source"],
            control_id=data["control"],
            recipients=tuple(data["recipients"]),
        )
        self.running = campaign
        try:
            await state.clear()
            await self.telegram.answer_callback(call, "Отправляю")
            await deliver_campaign(bot, campaign, self.report)
        finally:
            self.running = None

    async def stop_campaign(self, call: CallbackQuery, token: str) -> None:
        campaign = self.running
        if not campaign or (campaign.owner_id, campaign.token) != (call.from_user.id, token):
            await self.telegram.answer_callback(call, "Рассылка уже завершена.")
            return
        campaign.stop.set()
        await self.telegram.answer_callback(call, "Остановлю после текущего сообщения.")
        await self.report(campaign)

    async def report(self, campaign: Campaign) -> None:
        await self.telegram.edit_status(
            campaign.chat_id,
            campaign.control_id,
            report_card(campaign),
            reply_markup=report_controls(campaign),
        )


def build_admin_router(
    settings: Settings, stats: UserStatsManager, telegram: TelegramGateway
) -> Router:
    return AdminHandlers(settings, stats, telegram).router()
