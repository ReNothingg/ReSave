from __future__ import annotations

import logging

from aiogram import Router
from aiogram.filters import StateFilter
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup, Message

from config import Settings

from ..core.download_manager import (
    DownloadManager,
    QueueCapacityError,
    UserTaskLimitError,
)
from ..core.models import DownloadAction, DownloadTask
from ..core.selection_store import SelectionStore
from ..core.tiktok_photo_handler import is_tiktok_photo_url
from ..core.url_tools import extract_url, is_public_url_target
from ..core.video_info import (
    VideoInfoError,
    VideoInfoService,
    collect_playlist_entries,
    collect_resolutions,
    compact_media_info,
)
from ..utils.presentation import panel, user_error

logger = logging.getLogger(__name__)


def _keyboard(token: str, info: dict, resolutions: list[int]) -> InlineKeyboardMarkup:
    rows: list[list[InlineKeyboardButton]] = []
    resolution_buttons = [
        InlineKeyboardButton(
            text=f"🎥 {height}p",
            callback_data=f"media|{token}|res|{height}",
            style="primary",
        )
        for height in resolutions[:12]
    ]
    for offset in range(0, len(resolution_buttons), 2):
        rows.append(resolution_buttons[offset : offset + 2])

    if not resolution_buttons:
        rows.extend(
            [
                [
                    InlineKeyboardButton(
                        text="🎬 Максимум",
                        callback_data=f"media|{token}|best",
                        style="success",
                    )
                ],
                [
                    InlineKeyboardButton(
                        text="📹 720p", callback_data=f"media|{token}|medium", style="primary"
                    ),
                    InlineKeyboardButton(
                        text="📱 480p", callback_data=f"media|{token}|low", style="primary"
                    ),
                ],
            ]
        )
    else:
        rows.append(
            [
                InlineKeyboardButton(
                    text="🎬 Лучшее", callback_data=f"media|{token}|best", style="success"
                )
            ]
        )

    rows.append(
        [InlineKeyboardButton(text="🎵 MP3", callback_data=f"media|{token}|audio", style="primary")]
    )
    duration = info.get("duration")
    if isinstance(duration, (int, float)) and 0 < duration <= 30:
        rows.append(
            [
                InlineKeyboardButton(
                    text="✨ GIF", callback_data=f"media|{token}|gif", style="primary"
                )
            ]
        )
    if info.get("subtitles") or info.get("automatic_captions"):
        rows.append(
            [
                InlineKeyboardButton(
                    text="📝 Субтитры",
                    callback_data=f"media|{token}|subtitles",
                    style="primary",
                )
            ]
        )
    if info.get("thumbnail"):
        rows.append(
            [
                InlineKeyboardButton(
                    text="🖼️ Превью",
                    callback_data=f"media|{token}|thumbnail",
                    style="primary",
                )
            ]
        )
    rows.append(
        [
            InlineKeyboardButton(
                text="✕ Отмена", callback_data=f"media|{token}|cancel", style="danger"
            )
        ]
    )
    return InlineKeyboardMarkup(inline_keyboard=rows)


def _video_found_text(info: dict) -> str:
    lines = [str(info.get("title") or "Видео")]
    if info.get("uploader"):
        lines.append(f"Автор: {info['uploader']}")
    duration = info.get("duration")
    if isinstance(duration, (int, float)) and duration > 0:
        minutes, seconds = divmod(round(duration), 60)
        lines.append(f"Длительность: {minutes:02d}:{seconds:02d}")
    lines.extend(["", "Выберите формат:"])
    return panel("Медиа найдено", lines, icon="✅")


async def _enqueue_playlist(
    *,
    manager: DownloadManager,
    settings: Settings,
    info: dict,
    chat_id: int,
    user_id: int,
    status_message_id: int,
    reply_to_message_id: int,
    silent: bool,
) -> int:
    queued = 0
    for entry in collect_playlist_entries(info, settings.max_playlist_items):
        task = DownloadTask(
            url=entry["url"],
            chat_id=chat_id,
            user_id=user_id,
            status_message_id=status_message_id,
            reply_to_message_id=reply_to_message_id,
            info=entry["info"],
            action=DownloadAction.MEDIUM,
            silent=silent,
        )
        try:
            await manager.enqueue(task)
        except (QueueCapacityError, UserTaskLimitError):
            break
        queued += 1
    return queued


def build_download_router(
    *,
    manager: DownloadManager,
    info_service: VideoInfoService,
    selections: SelectionStore,
    settings: Settings,
) -> Router:
    router = Router(name="downloads")

    async def process_private(message: Message, url: str) -> None:
        assert message.from_user is not None
        status = await message.reply(
            panel("Проверяю ссылку", ["Получаю информацию и доступные форматы."], icon="🔍"),
            parse_mode="HTML",
        )
        if is_tiktok_photo_url(url):
            task = DownloadTask(
                url=url,
                chat_id=message.chat.id,
                user_id=message.from_user.id,
                status_message_id=status.message_id,
                reply_to_message_id=message.message_id,
                info={"title": "Фото из TikTok"},
                action=DownloadAction.TIKTOK_PHOTO,
            )
            try:
                position = await manager.enqueue(task)
                await status.edit_text(
                    panel("Добавлено в очередь", [f"Позиция: {position}"], icon="📥"),
                    parse_mode="HTML",
                )
            except (QueueCapacityError, UserTaskLimitError) as exc:
                await status.edit_text(
                    panel("Не удалось добавить", [str(exc)], icon="⚠️"), parse_mode="HTML"
                )
            return

        try:
            info = await info_service.fetch(url)
        except VideoInfoError as exc:
            await status.edit_text(user_error(exc), parse_mode="HTML")
            return

        if info.get("_type") == "playlist":
            count = await _enqueue_playlist(
                manager=manager,
                settings=settings,
                info=info,
                chat_id=message.chat.id,
                user_id=message.from_user.id,
                status_message_id=status.message_id,
                reply_to_message_id=message.message_id,
                silent=True,
            )
            lines = [f"Добавлено видео: {count}", "Качество: до 720p"]
            if count >= settings.max_playlist_items:
                lines.append(f"Лимит плейлиста: {settings.max_playlist_items}")
            await status.edit_text(panel("Плейлист в очереди", lines, icon="🎶"), parse_mode="HTML")
            return

        info = compact_media_info(info)
        resolutions = collect_resolutions(info)
        selection = selections.put(
            user_id=message.from_user.id,
            chat_id=message.chat.id,
            reply_to_message_id=message.message_id,
            url=url,
            info=info,
            resolutions=resolutions,
        )
        await status.edit_text(
            _video_found_text(info),
            parse_mode="HTML",
            reply_markup=_keyboard(selection.token, info, resolutions),
        )

    async def process_group(message: Message, url: str) -> None:
        assert message.from_user is not None
        if is_tiktok_photo_url(url):
            info = {"title": "Фото из TikTok"}
            action = DownloadAction.TIKTOK_PHOTO
        else:
            try:
                info = await info_service.fetch(url)
            except VideoInfoError as exc:
                logger.info("Unsupported group URL in chat %s: %s", message.chat.id, exc)
                return
            if info.get("_type") == "playlist":
                await _enqueue_playlist(
                    manager=manager,
                    settings=settings,
                    info=info,
                    chat_id=message.chat.id,
                    user_id=message.from_user.id,
                    status_message_id=message.message_id,
                    reply_to_message_id=message.message_id,
                    silent=True,
                )
                return
            info = compact_media_info(info)
            action = DownloadAction.MEDIUM

        task = DownloadTask(
            url=url,
            chat_id=message.chat.id,
            user_id=message.from_user.id,
            status_message_id=message.message_id,
            reply_to_message_id=message.message_id,
            info=info,
            action=action,
            silent=True,
        )
        try:
            await manager.enqueue(task)
        except (QueueCapacityError, UserTaskLimitError) as exc:
            logger.info("Cannot enqueue group media in chat %s: %s", message.chat.id, exc)

    async def handle_url(message: Message, state: FSMContext) -> None:
        if not message.from_user or message.from_user.is_bot or await state.get_state():
            return
        text = (message.text or message.caption or "").strip()
        if not text or text.startswith("/"):
            return
        url = extract_url(text, message.entities, message.caption_entities)
        if not url:
            return
        if not await is_public_url_target(url):
            if message.chat.type not in {"group", "supergroup"}:
                await message.reply(
                    panel(
                        "Ссылка отклонена",
                        ["Адрес не существует или ведёт во внутреннюю сеть."],
                        icon="⚠️",
                    ),
                    parse_mode="HTML",
                )
            return
        if message.chat.type in {"group", "supergroup"}:
            await process_group(message, url)
        else:
            await process_private(message, url)

    async def handle_media_choice(call: CallbackQuery) -> None:
        if not call.data or not call.message or not call.from_user:
            return
        parts = call.data.split("|")
        if len(parts) < 3:
            await call.answer("Некорректная кнопка", show_alert=True)
            return
        _, token, raw_action, *parameters = parts
        selection = selections.get(
            token,
            user_id=call.from_user.id,
            chat_id=call.message.chat.id,
        )
        if selection is None:
            await call.answer("Выбор устарел. Отправьте ссылку заново.", show_alert=True)
            return
        if raw_action == "cancel":
            selections.pop(token)
            await call.answer("Отменено")
            try:
                await call.message.delete()
            except Exception:
                await call.message.edit_text(panel("Запрос отменён", icon="✕"), parse_mode="HTML")
            return
        try:
            action = DownloadAction(raw_action)
            requested_height = int(parameters[0]) if action == DownloadAction.RESOLUTION else None
        except (ValueError, IndexError):
            await call.answer("Некорректный формат", show_alert=True)
            return
        if requested_height is not None and requested_height not in selection.resolutions:
            await call.answer("Это разрешение недоступно", show_alert=True)
            return

        task = DownloadTask(
            url=selection.url,
            chat_id=selection.chat_id,
            user_id=selection.user_id,
            status_message_id=call.message.message_id,
            reply_to_message_id=selection.reply_to_message_id,
            info=selection.info,
            action=action,
            requested_height=requested_height,
        )
        try:
            position = await manager.enqueue(task)
        except (QueueCapacityError, UserTaskLimitError) as exc:
            await call.answer(str(exc), show_alert=True)
            return
        selections.pop(token)
        await call.answer("Добавлено в очередь")
        await call.message.edit_text(
            panel("Добавлено в очередь", [f"Позиция: {position}"], icon="📥"),
            parse_mode="HTML",
        )

    async def cancel_all(call: CallbackQuery) -> None:
        if not call.message or not call.from_user:
            return
        count = manager.cancel_for_user(call.from_user.id, chat_id=call.message.chat.id)
        await call.answer(f"Отменено: {count}")
        await call.message.edit_text(
            panel("Загрузки отменены", [f"Отменено: {count}"], icon="✅"),
            parse_mode="HTML",
        )

    router.message.register(
        handle_url,
        StateFilter(None),
        lambda message: bool(
            (message.text or message.caption)
            and not (message.text or message.caption or "").strip().startswith("/")
        ),
    )
    router.callback_query.register(
        handle_media_choice, lambda call: bool(call.data and call.data.startswith("media|"))
    )
    router.callback_query.register(cancel_all, lambda call: call.data == "cancel_all_downloads")
    return router
