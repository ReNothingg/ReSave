from __future__ import annotations

import logging
from dataclasses import dataclass

from aiogram import Bot, Router
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup, Message

import config
from ..core.user_stats import get_stats_manager

logger = logging.getLogger(__name__)


def is_admin(user_id: int) -> bool:
    return user_id in config.ADMIN_IDS


class BroadcastStates(StatesGroup):
    waiting_for_message = State()


@dataclass
class BroadcastPayload:
    kind: str
    text: str | None = None
    caption: str | None = None
    file_id: str | None = None


def _build_admin_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="📊 Глобальная статистика",
                    callback_data="admin_stats_global",
                ),
                InlineKeyboardButton(
                    text="📣 Рассылка",
                    callback_data="admin_broadcast",
                ),
            ],
            [
                InlineKeyboardButton(
                    text="👥 Список пользователей",
                    callback_data="admin_user_list",
                ),
                InlineKeyboardButton(
                    text="🗑️ Очистить БД",
                    callback_data="admin_clear_db",
                ),
            ],
        ]
    )


def _build_back_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="⬅️ Назад", callback_data="admin_back")]
        ]
    )


def _build_admin_panel_text():
    stats_manager = get_stats_manager()
    all_stats = stats_manager.get_all_stats()

    total_downloads = sum(item.downloads_count for item in all_stats.values())
    total_videos = sum(item.total_videos for item in all_stats.values())
    total_audios = sum(item.total_audios for item in all_stats.values())
    total_other = sum(item.total_other_downloads for item in all_stats.values())
    total_failed = sum(item.failed_downloads for item in all_stats.values())
    total_size = sum(item.total_size_mb for item in all_stats.values())

    return "\n".join(
        [
            "Панель администратора",
            "",
            f"Активных пользователей: {len(all_stats)}",
            f"Всего загрузок: {total_downloads}",
            f"Видео загружено: {total_videos}",
            f"Аудио загружено: {total_audios}",
            f"Прочие файлы: {total_other}",
            f"Ошибок: {total_failed}",
            f"Общий размер: {total_size:.1f} MB",
            "",
            "Выберите действие ниже.",
        ]
    )


def _build_global_stats_text():
    stats_manager = get_stats_manager()
    all_stats = stats_manager.get_all_stats()

    total_users = len(all_stats)
    total_downloads = sum(item.downloads_count for item in all_stats.values())
    total_videos = sum(item.total_videos for item in all_stats.values())
    total_audios = sum(item.total_audios for item in all_stats.values())
    total_other = sum(item.total_other_downloads for item in all_stats.values())
    total_failed = sum(item.failed_downloads for item in all_stats.values())
    total_size = sum(item.total_size_mb for item in all_stats.values())

    avg_downloads = total_downloads / total_users if total_users else 0
    avg_size = total_size / total_downloads if total_downloads else 0
    active_users = sum(1 for item in all_stats.values() if item.downloads_count > 0)
    attempts = total_downloads + total_failed
    success_rate = (total_downloads / attempts * 100) if attempts else 0

    lines = [
        "Глобальная статистика ReSave",
        "",
        f"Пользователей: {total_users}",
        f"Активных пользователей: {active_users}",
        f"Всего загрузок: {total_downloads}",
        f"Видео: {total_videos}",
        f"Аудио: {total_audios}",
        f"Прочее: {total_other}",
        f"Ошибок: {total_failed}",
        f"Успешность: {success_rate:.1f}%",
        f"Общий размер: {total_size:.1f} MB",
        f"Средний размер файла: {avg_size:.2f} MB",
        f"Среднее число загрузок на пользователя: {avg_downloads:.1f}",
        "",
        "Топ-5 пользователей:",
    ]

    top_users = sorted(
        all_stats.items(),
        key=lambda item: item[1].downloads_count,
        reverse=True,
    )[:5]

    if not top_users:
        lines.append("Пока нет пользователей в статистике.")
    else:
        for index, (user_id, stats) in enumerate(top_users, start=1):
            lines.append(f"{index}. ID {user_id}: {stats.downloads_count} загрузок")

    return "\n".join(lines)


def _build_user_list_text():
    stats_manager = get_stats_manager()
    all_stats = stats_manager.get_all_stats()
    user_list = sorted(
        all_stats.items(),
        key=lambda item: item[1].downloads_count,
        reverse=True,
    )

    lines = ["Список пользователей", ""]

    if not user_list:
        lines.append("База пользователей пуста.")
        return "\n".join(lines)

    for user_id, stats in user_list[:20]:
        lines.extend(
            [
                f"ID: {user_id}",
                f"├ Загрузок: {stats.downloads_count}",
                f"├ Видео: {stats.total_videos} | Аудио: {stats.total_audios} | Прочее: {stats.total_other_downloads}",
                f"└ Размер: {stats.total_size_mb:.1f} MB",
                "",
            ]
        )

    if len(user_list) > 20:
        lines.append(f"... и еще {len(user_list) - 20} пользователей")

    return "\n".join(lines).strip()


def _extract_broadcast_payload(message: Message) -> BroadcastPayload | None:
    if message.text:
        return BroadcastPayload(kind="text", text=message.text)

    if message.photo:
        return BroadcastPayload(
            kind="photo",
            file_id=message.photo[-1].file_id,
            caption=message.caption,
        )

    if message.video:
        return BroadcastPayload(
            kind="video",
            file_id=message.video.file_id,
            caption=message.caption,
        )

    if message.document:
        return BroadcastPayload(
            kind="document",
            file_id=message.document.file_id,
            caption=message.caption,
        )

    if message.audio:
        return BroadcastPayload(
            kind="audio",
            file_id=message.audio.file_id,
            caption=message.caption,
        )

    return None


async def _send_broadcast_payload(bot: Bot, user_id: int, payload: BroadcastPayload):
    if payload.kind == "text":
        await bot.send_message(user_id, payload.text or "")
        return

    if payload.kind == "photo":
        await bot.send_photo(user_id, payload.file_id, caption=payload.caption)
        return

    if payload.kind == "video":
        await bot.send_video(user_id, payload.file_id, caption=payload.caption)
        return

    if payload.kind == "document":
        await bot.send_document(user_id, payload.file_id, caption=payload.caption)
        return

    if payload.kind == "audio":
        await bot.send_audio(user_id, payload.file_id, caption=payload.caption)
        return

    raise ValueError(f"Unsupported broadcast payload kind: {payload.kind}")


def register_admin_handlers(router: Router):
    broadcast_cache: dict[int, dict] = {}

    async def admin_command(message: Message, state: FSMContext):
        if not message.from_user or not is_admin(message.from_user.id):
            return

        await state.clear()
        await message.reply(_build_admin_panel_text(), reply_markup=_build_admin_keyboard())

    async def broadcast_command(message: Message, state: FSMContext):
        if not message.from_user or not is_admin(message.from_user.id):
            return

        await state.set_state(BroadcastStates.waiting_for_message)
        await message.reply(
            "Режим рассылки активирован.\n\n"
            "Отправьте сообщение, фото, видео, документ или аудио для рассылки."
        )

    async def process_broadcast_message(message: Message, state: FSMContext):
        if not message.from_user or not is_admin(message.from_user.id):
            return

        payload = _extract_broadcast_payload(message)
        if payload is None:
            await message.reply(
                "Этот тип сообщения не поддерживается для рассылки. "
                "Отправьте текст, фото, видео, документ или аудио."
            )
            return

        stats_manager = get_stats_manager()
        all_stats = stats_manager.get_all_stats()
        user_ids = list(all_stats.keys())

        if not user_ids:
            await state.clear()
            await message.reply("Нет пользователей для рассылки.")
            return

        broadcast_cache[message.from_user.id] = {
            "payload": payload,
            "user_ids": user_ids,
            "total": len(user_ids),
        }

        await state.clear()
        keyboard = InlineKeyboardMarkup(
            inline_keyboard=[
                [
                    InlineKeyboardButton(
                        text="✅ Да, отправить",
                        callback_data="broadcast_confirm",
                    ),
                    InlineKeyboardButton(
                        text="❌ Отменить",
                        callback_data="broadcast_cancel",
                    ),
                ]
            ]
        )

        await message.reply(
            f"Подтверждение рассылки\n\nСообщение будет отправлено {len(user_ids)} пользователям.\n\nПродолжить?",
            reply_markup=keyboard,
        )

    async def stats_global_command(message: Message):
        if not message.from_user or not is_admin(message.from_user.id):
            return

        await message.reply(_build_global_stats_text())

    async def callback_admin_stats_global(call: CallbackQuery):
        if not call.from_user or not is_admin(call.from_user.id) or not call.message:
            return

        await call.answer()
        await call.message.edit_text(
            _build_global_stats_text(),
            reply_markup=_build_back_keyboard(),
        )

    async def callback_admin_broadcast(call: CallbackQuery, state: FSMContext):
        if not call.from_user or not is_admin(call.from_user.id) or not call.message:
            return

        await state.set_state(BroadcastStates.waiting_for_message)
        await call.answer()
        await call.message.edit_text(
            "Режим рассылки активирован.\n\n"
            "Отправьте сообщение, фото, видео, документ или аудио для рассылки.",
            reply_markup=_build_back_keyboard(),
        )

    async def callback_admin_user_list(call: CallbackQuery):
        if not call.from_user or not is_admin(call.from_user.id) or not call.message:
            return

        await call.answer()
        await call.message.edit_text(
            _build_user_list_text(),
            reply_markup=_build_back_keyboard(),
        )

    async def callback_admin_clear_db(call: CallbackQuery):
        if not call.from_user or not is_admin(call.from_user.id) or not call.message:
            return

        await call.answer()
        await call.message.edit_text(
            "Внимание.\n\nВы уверены, что хотите очистить всю статистику?\nЭто действие необратимо.",
            reply_markup=InlineKeyboardMarkup(
                inline_keyboard=[
                    [
                        InlineKeyboardButton(
                            text="✅ Да, очистить",
                            callback_data="admin_clear_confirm",
                        ),
                        InlineKeyboardButton(
                            text="❌ Отменить",
                            callback_data="admin_back",
                        ),
                    ]
                ]
            ),
        )

    async def callback_admin_clear_confirm(call: CallbackQuery):
        if not call.from_user or not is_admin(call.from_user.id) or not call.message:
            return

        stats_manager = get_stats_manager()
        stats_manager.clear_all_stats()

        await call.answer("Статистика очищена.")
        await call.message.edit_text(
            "База статистики успешно очищена.",
            reply_markup=_build_back_keyboard(),
        )

    async def callback_broadcast_confirm(call: CallbackQuery, bot: Bot):
        if not call.from_user or not is_admin(call.from_user.id) or not call.message:
            return

        data = broadcast_cache.get(call.from_user.id)
        if not data:
            await call.answer("Данные рассылки не найдены.", show_alert=True)
            return

        await call.answer()

        payload: BroadcastPayload = data["payload"]
        user_ids: list[int] = data["user_ids"]
        total_users: int = data["total"]

        await call.message.edit_text(
            f"Рассылка начата...\n\nОтправлено: 0/{total_users}\nОшибок: 0"
        )

        sent = 0
        failed = 0

        for index, user_id in enumerate(user_ids, start=1):
            try:
                await _send_broadcast_payload(bot, user_id, payload)
                sent += 1
            except Exception as exc:
                failed += 1
                logger.error("Ошибка отправки пользователю %s: %s", user_id, exc)

            if index % 10 == 0 or index == total_users:
                try:
                    await call.message.edit_text(
                        "Рассылка в процессе...\n\n"
                        f"Отправлено: {sent}/{total_users}\n"
                        f"Ошибок: {failed}"
                    )
                except Exception:
                    logger.debug("Не удалось обновить прогресс рассылки")

        broadcast_cache.pop(call.from_user.id, None)

        await call.message.edit_text(
            "Рассылка завершена.\n\n"
            f"Успешно отправлено: {sent}/{total_users}\n"
            f"Ошибок: {failed}",
            reply_markup=_build_back_keyboard(),
        )

    async def callback_broadcast_cancel(call: CallbackQuery, state: FSMContext):
        if not call.from_user or not is_admin(call.from_user.id) or not call.message:
            return

        await state.clear()
        broadcast_cache.pop(call.from_user.id, None)
        await call.answer("Рассылка отменена.")
        await call.message.edit_text(
            "Рассылка отменена.",
            reply_markup=_build_back_keyboard(),
        )

    async def callback_admin_back(call: CallbackQuery, state: FSMContext):
        if not call.from_user or not is_admin(call.from_user.id) or not call.message:
            return

        await state.clear()
        await call.answer()
        await call.message.edit_text(
            _build_admin_panel_text(),
            reply_markup=_build_admin_keyboard(),
        )

    router.message.register(admin_command, Command("admin"))
    router.message.register(broadcast_command, Command("broadcast"))
    router.message.register(stats_global_command, Command("stats_global"))
    router.message.register(process_broadcast_message, BroadcastStates.waiting_for_message)

    router.callback_query.register(
        callback_admin_stats_global,
        lambda call: call.data == "admin_stats_global",
    )
    router.callback_query.register(
        callback_admin_broadcast,
        lambda call: call.data == "admin_broadcast",
    )
    router.callback_query.register(
        callback_admin_user_list,
        lambda call: call.data == "admin_user_list",
    )
    router.callback_query.register(
        callback_admin_clear_db,
        lambda call: call.data == "admin_clear_db",
    )
    router.callback_query.register(
        callback_admin_clear_confirm,
        lambda call: call.data == "admin_clear_confirm",
    )
    router.callback_query.register(
        callback_broadcast_confirm,
        lambda call: call.data == "broadcast_confirm",
    )
    router.callback_query.register(
        callback_broadcast_cancel,
        lambda call: call.data == "broadcast_cancel",
    )
    router.callback_query.register(
        callback_admin_back,
        lambda call: call.data == "admin_back",
    )
