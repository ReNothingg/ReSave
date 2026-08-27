from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass

from aiogram import Bot, Router
from aiogram.exceptions import TelegramRetryAfter
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup, Message

from config import Settings

from ..core.user_stats import UserStats, UserStatsManager
from ..utils.presentation import panel

logger = logging.getLogger(__name__)


class BroadcastStates(StatesGroup):
    waiting_for_message = State()


@dataclass(slots=True)
class BroadcastPayload:
    kind: str
    text: str | None = None
    caption: str | None = None
    file_id: str | None = None
    entities: list | None = None
    caption_entities: list | None = None


def _admin_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="📊 Статистика", callback_data="admin:stats", style="primary"
                ),
                InlineKeyboardButton(
                    text="📣 Рассылка", callback_data="admin:broadcast", style="primary"
                ),
            ],
            [
                InlineKeyboardButton(
                    text="👥 Пользователи", callback_data="admin:users", style="primary"
                ),
                InlineKeyboardButton(
                    text="🧹 Очистить БД", callback_data="admin:clear", style="danger"
                ),
            ],
        ]
    )


def _back_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[[InlineKeyboardButton(text="⬅️ Назад", callback_data="admin:back")]]
    )


def _confirm_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="✅ Подтвердить", callback_data="broadcast:confirm", style="success"
                ),
                InlineKeyboardButton(
                    text="❌ Отменить", callback_data="broadcast:cancel", style="danger"
                ),
            ]
        ]
    )


def _stats_lines(items: dict[int, UserStats]) -> list[str]:
    downloads = sum(item.downloads_count for item in items.values())
    failed = sum(item.failed_downloads for item in items.values())
    attempts = downloads + failed
    return [
        f"Пользователей: {len(items)}",
        f"Загрузок: {downloads}",
        f"Видео: {sum(item.total_videos for item in items.values())}",
        f"Аудио: {sum(item.total_audios for item in items.values())}",
        f"Прочее: {sum(item.total_other_downloads for item in items.values())}",
        f"Ошибок: {failed}",
        f"Успешность: {downloads / attempts * 100 if attempts else 0:.1f}%",
        f"Объём: {sum(item.total_size_mb for item in items.values()):.1f} MB",
    ]


def _payload(message: Message) -> BroadcastPayload | None:
    if message.text:
        return BroadcastPayload("text", text=message.text, entities=list(message.entities or []))
    for kind in ("photo", "video", "document", "audio"):
        value = getattr(message, kind, None)
        if not value:
            continue
        file_id = value[-1].file_id if kind == "photo" else value.file_id
        return BroadcastPayload(
            kind,
            file_id=file_id,
            caption=message.caption,
            caption_entities=list(message.caption_entities or []),
        )
    return None


async def _send_payload(bot: Bot, user_id: int, payload: BroadcastPayload) -> None:
    if payload.kind == "text":
        await bot.send_message(user_id, payload.text or "", entities=payload.entities)
        return
    method = getattr(bot, f"send_{payload.kind}")
    await method(
        chat_id=user_id,
        **{payload.kind: payload.file_id},
        caption=payload.caption,
        caption_entities=payload.caption_entities,
    )


def build_admin_router(settings: Settings, stats: UserStatsManager) -> Router:
    router = Router(name="admin")
    pending: dict[int, BroadcastPayload] = {}

    def allowed(user_id: int | None) -> bool:
        return bool(user_id is not None and user_id in settings.admin_ids)

    async def all_stats() -> dict[int, UserStats]:
        return await asyncio.to_thread(stats.get_all_stats)

    async def panel_text() -> str:
        return panel("Панель администратора", _stats_lines(await all_stats()), icon="🛠️")

    async def admin(message: Message, state: FSMContext) -> None:
        if not message.from_user or not allowed(message.from_user.id):
            return
        await state.clear()
        await message.reply(await panel_text(), parse_mode="HTML", reply_markup=_admin_keyboard())

    async def stats_command(message: Message) -> None:
        if not message.from_user or not allowed(message.from_user.id):
            return
        await message.reply(
            panel("Глобальная статистика", _stats_lines(await all_stats()), icon="📊"),
            parse_mode="HTML",
        )

    async def begin_broadcast(message: Message, state: FSMContext) -> None:
        if not message.from_user or not allowed(message.from_user.id):
            return
        await state.set_state(BroadcastStates.waiting_for_message)
        await message.reply(
            panel(
                "Рассылка",
                [
                    "Отправьте текст, фото, видео, документ или аудио.",
                    "Следующим шагом будет подтверждение.",
                ],
                icon="📣",
            ),
            parse_mode="HTML",
        )

    async def receive_broadcast(message: Message, state: FSMContext) -> None:
        if not message.from_user or not allowed(message.from_user.id):
            return
        value = _payload(message)
        if value is None:
            await message.reply(panel("Формат не поддерживается", icon="⚠️"), parse_mode="HTML")
            return
        pending[message.from_user.id] = value
        await state.clear()
        recipients = len(await all_stats())
        await message.reply(
            panel("Подтвердите рассылку", [f"Получателей: {recipients}"], icon="📣"),
            parse_mode="HTML",
            reply_markup=_confirm_keyboard(),
        )

    async def admin_callback(call: CallbackQuery, state: FSMContext) -> None:
        if not call.from_user or not allowed(call.from_user.id) or not call.message:
            return
        action = (call.data or "").partition(":")[2]
        await call.answer()
        if action == "back":
            await state.clear()
            await call.message.edit_text(
                await panel_text(), parse_mode="HTML", reply_markup=_admin_keyboard()
            )
        elif action == "stats":
            await call.message.edit_text(
                panel("Глобальная статистика", _stats_lines(await all_stats()), icon="📊"),
                parse_mode="HTML",
                reply_markup=_back_keyboard(),
            )
        elif action == "users":
            items = list((await all_stats()).items())[:20]
            lines = [
                f"{user_id}: {value.downloads_count} загрузок · {value.total_size_mb:.1f} MB"
                for user_id, value in items
            ] or ["Пользователей пока нет."]
            await call.message.edit_text(
                panel("Пользователи", lines, icon="👥"),
                parse_mode="HTML",
                reply_markup=_back_keyboard(),
            )
        elif action == "broadcast":
            await state.set_state(BroadcastStates.waiting_for_message)
            await call.message.edit_text(
                panel("Рассылка", ["Отправьте сообщение для рассылки."], icon="📣"),
                parse_mode="HTML",
                reply_markup=_back_keyboard(),
            )
        elif action == "clear":
            keyboard = InlineKeyboardMarkup(
                inline_keyboard=[
                    [
                        InlineKeyboardButton(
                            text="✅ Очистить",
                            callback_data="admin:clear-confirm",
                            style="danger",
                        ),
                        InlineKeyboardButton(
                            text="❌ Отмена", callback_data="admin:back", style="primary"
                        ),
                    ]
                ]
            )
            await call.message.edit_text(
                panel("Очистить статистику?", ["Действие нельзя отменить."], icon="⚠️"),
                parse_mode="HTML",
                reply_markup=keyboard,
            )
        elif action == "clear-confirm":
            await asyncio.to_thread(stats.clear_all_stats)
            await call.message.edit_text(
                panel("Статистика очищена", icon="✅"),
                parse_mode="HTML",
                reply_markup=_back_keyboard(),
            )

    async def broadcast_callback(call: CallbackQuery, state: FSMContext, bot: Bot) -> None:
        if not call.from_user or not allowed(call.from_user.id) or not call.message:
            return
        action = (call.data or "").partition(":")[2]
        if action == "cancel":
            pending.pop(call.from_user.id, None)
            await state.clear()
            await call.answer("Отменено")
            await call.message.edit_text(
                panel("Рассылка отменена", icon="✕"),
                parse_mode="HTML",
                reply_markup=_back_keyboard(),
            )
            return

        payload = pending.get(call.from_user.id)
        if payload is None:
            await call.answer("Данные рассылки устарели", show_alert=True)
            return
        await call.answer()
        user_ids = list((await all_stats()).keys())
        sent = failed = 0
        for index, user_id in enumerate(user_ids, start=1):
            try:
                await _send_payload(bot, user_id, payload)
                sent += 1
            except TelegramRetryAfter as exc:
                await asyncio.sleep(float(exc.retry_after) + 0.25)
                try:
                    await _send_payload(bot, user_id, payload)
                    sent += 1
                except Exception:
                    failed += 1
            except Exception as exc:
                failed += 1
                logger.info("Broadcast to %s failed: %s", user_id, exc)
            await asyncio.sleep(0.05)
            if index % 20 == 0:
                try:
                    await call.message.edit_text(
                        panel(
                            "Рассылка",
                            [f"Отправлено: {sent}/{len(user_ids)}", f"Ошибок: {failed}"],
                            icon="📣",
                        ),
                        parse_mode="HTML",
                    )
                except Exception:
                    pass
        pending.pop(call.from_user.id, None)
        await call.message.edit_text(
            panel("Рассылка завершена", [f"Отправлено: {sent}", f"Ошибок: {failed}"], icon="✅"),
            parse_mode="HTML",
            reply_markup=_back_keyboard(),
        )

    router.message.register(admin, Command("admin"))
    router.message.register(begin_broadcast, Command("broadcast"))
    router.message.register(stats_command, Command("stats_global"))
    router.message.register(receive_broadcast, BroadcastStates.waiting_for_message)
    router.callback_query.register(
        admin_callback, lambda call: bool(call.data and call.data.startswith("admin:"))
    )
    router.callback_query.register(
        broadcast_callback, lambda call: bool(call.data and call.data.startswith("broadcast:"))
    )
    return router
