from __future__ import annotations

import asyncio
import logging

from aiogram import Router
from aiogram.filters import StateFilter
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message

from config import Settings

from ..core.download_manager import (
    DownloadManager,
    QueueCapacityError,
    UserTaskLimitError,
)
from ..core.models import DownloadAction, DownloadTask
from ..core.selection_store import SelectionStore
from ..core.telegram_gateway import TelegramGateway
from ..core.tiktok_photo_handler import is_tiktok_photo_url
from ..core.url_tools import extract_url, is_public_url_target, normalize_url
from ..core.video_info import (
    VideoInfoError,
    VideoInfoService,
    collect_playlist_entries,
    collect_resolutions,
    compact_media_info,
)
from ..utils.presentation import panel, user_error
from .media_menu import available_choices, media_keyboard, media_text

logger = logging.getLogger(__name__)


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
) -> tuple[int, int, str | None]:
    queued = 0
    entries = collect_playlist_entries(info, settings.max_playlist_items)
    stop_reason: str | None = None
    for entry in entries:
        entry_url = normalize_url(str(entry["url"]))
        if not entry_url or not await is_public_url_target(entry_url):
            logger.warning("Skipping unsafe playlist entry id=%r", entry["info"].get("id"))
            continue
        task = DownloadTask(
            url=entry_url,
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
        except (QueueCapacityError, UserTaskLimitError) as exc:
            stop_reason = str(exc)
            break
        queued += 1
    return queued, len(entries), stop_reason


class DownloadHandlers:
    def __init__(
        self,
        *,
        manager: DownloadManager,
        info_service: VideoInfoService,
        selections: SelectionStore,
        settings: Settings,
        telegram: TelegramGateway,
        ffmpeg_available: bool = True,
    ):
        self.manager = manager
        self.info_service = info_service
        self.selections = selections
        self.settings = settings
        self.telegram = telegram
        self.ffmpeg_available = ffmpeg_available

    async def process_private(self, message: Message, url: str) -> None:
        assert message.from_user is not None
        status = await self.telegram.reply(
            message,
            panel("Проверяю ссылку…"),
            parse_mode="HTML",
        )
        try:
            await self.prepare_private(message, url, status)
        except asyncio.CancelledError:
            await self.telegram.edit_status(
                status.chat.id, status.message_id, "Проверка отменена.", reply_markup=None
            )
            raise
        except (VideoInfoError, ValueError) as exc:
            await self.telegram.edit_status(
                status.chat.id, status.message_id, user_error(exc), reply_markup=None
            )

    async def prepare_private(self, message: Message, url: str, status: Message) -> None:
        url = await self.info_service.resolve_url(url)
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
                position = await self.manager.enqueue(task)
                await self.telegram.edit_status(
                    status.chat.id,
                    status.message_id,
                    panel("В очереди", [f"Место в очереди: {position}."]),
                    parse_mode="HTML",
                )
            except (QueueCapacityError, UserTaskLimitError) as exc:
                await self.telegram.edit_status(
                    status.chat.id,
                    status.message_id,
                    panel("Очередь занята", [str(exc)]),
                    parse_mode="HTML",
                )
            return

        info = await self.info_service.fetch(url)

        if info.get("_type") == "playlist":
            count, discovered, stop_reason = await _enqueue_playlist(
                manager=self.manager,
                settings=self.settings,
                info=info,
                chat_id=message.chat.id,
                user_id=message.from_user.id,
                status_message_id=status.message_id,
                reply_to_message_id=message.message_id,
                silent=True,
            )
            lines = [f"Добавлено видео: {count} из {discovered}"]
            if count:
                lines.append("Качество: до 720p")
            if stop_reason:
                lines.append(stop_reason)
            if discovered >= self.settings.max_playlist_items:
                lines.append(f"Лимит плейлиста: {self.settings.max_playlist_items}")
            title = "Добавлено из плейлиста" if count else "Плейлист не добавлен"
            await self.telegram.edit_status(
                status.chat.id, status.message_id, panel(title, lines), parse_mode="HTML"
            )
            return

        info = compact_media_info(info)
        resolutions = collect_resolutions(info)
        selection = self.selections.put(
            user_id=message.from_user.id,
            chat_id=message.chat.id,
            reply_to_message_id=message.message_id,
            url=url,
            info=info,
            resolutions=resolutions,
        )
        await self.telegram.edit_status(
            status.chat.id,
            status.message_id,
            media_text(
                info,
                upload_limit=self.settings.effective_upload_limit,
                ffmpeg_available=self.ffmpeg_available,
            ),
            parse_mode="HTML",
            reply_markup=media_keyboard(
                selection.token,
                info,
                resolutions,
                ffmpeg_available=self.ffmpeg_available,
            ),
        )

    async def process_group(self, message: Message, url: str) -> None:
        assert message.from_user is not None
        try:
            url = await self.info_service.resolve_url(url)
        except (VideoInfoError, ValueError) as exc:
            logger.info("Cannot resolve group URL: %s", exc)
            return
        if is_tiktok_photo_url(url):
            info = {"title": "Фото из TikTok"}
            action = DownloadAction.TIKTOK_PHOTO
        else:
            try:
                info = await self.info_service.fetch(url)
            except VideoInfoError as exc:
                logger.info("Unsupported group URL in chat %s: %s", message.chat.id, exc)
                return
            if info.get("_type") == "playlist":
                queued, discovered, reason = await _enqueue_playlist(
                    manager=self.manager,
                    settings=self.settings,
                    info=info,
                    chat_id=message.chat.id,
                    user_id=message.from_user.id,
                    status_message_id=message.message_id,
                    reply_to_message_id=message.message_id,
                    silent=True,
                )
                logger.info(
                    "Playlist in chat %s: queued=%s discovered=%s reason=%s",
                    message.chat.id,
                    queued,
                    discovered,
                    reason,
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
            await self.manager.enqueue(task)
        except (QueueCapacityError, UserTaskLimitError) as exc:
            logger.info("Cannot enqueue group media in chat %s: %s", message.chat.id, exc)

    async def handle_url(self, message: Message, state: FSMContext) -> None:
        if not message.from_user or message.from_user.is_bot or await state.get_state():
            return
        text = (message.text or message.caption or "").strip()
        if not text or text.startswith("/"):
            return
        url = extract_url(text, message.entities, message.caption_entities)
        if not url:
            if message.chat.type == "private":
                await self.telegram.reply(message, "Пришлите ссылку на видео или публикацию.")
            return
        if not self.selections.begin_request(message.from_user.id, message.chat.id):
            if message.chat.type == "private":
                await self.telegram.reply(
                    message, "Проверка ссылок занята. Попробуйте через минуту."
                )
            return
        try:
            await self._process_url(message, url)
        finally:
            self.selections.finish_request(message.from_user.id, message.chat.id)

    async def _process_url(self, message: Message, url: str) -> None:
        if not await is_public_url_target(url):
            if message.chat.type not in {"group", "supergroup"}:
                await self.telegram.reply(
                    message,
                    panel(
                        "Ссылка отклонена",
                        ["Не могу открыть этот адрес. Пришлите ссылку на публикацию."],
                    ),
                    parse_mode="HTML",
                )
            return
        if message.chat.type in {"group", "supergroup"}:
            await self.process_group(message, url)
        else:
            await self.process_private(message, url)

    async def handle_media_choice(self, call: CallbackQuery) -> None:
        if not call.data or not isinstance(call.message, Message) or not call.from_user:
            return
        parts = call.data.split("|")
        if len(parts) < 3:
            await self.telegram.answer_callback(call, "Некорректная кнопка")
            return
        _, token, raw_action, *parameters = parts
        selection = self.selections.get(
            token,
            user_id=call.from_user.id,
            chat_id=call.message.chat.id,
        )
        if selection is None:
            await self.telegram.answer_callback(call, "Выбор устарел. Отправьте ссылку заново.")
            return
        if raw_action == "cancel":
            self.selections.pop(token)
            await self.telegram.answer_callback(call, "Отменено")
            await self.telegram.edit_status(
                call.message.chat.id, call.message.message_id, "Отменено.", reply_markup=None
            )
            return
        try:
            action = DownloadAction(raw_action)
            requested_height = int(parameters[0]) if action == DownloadAction.RESOLUTION else None
        except (ValueError, IndexError):
            await self.telegram.answer_callback(call, "Некорректный формат")
            return
        choices = available_choices(
            selection.info,
            selection.resolutions,
            ffmpeg_available=self.ffmpeg_available,
        )
        if (action, requested_height) not in {(item[0], item[1]) for item in choices}:
            await self.telegram.answer_callback(call, "Этот формат недоступен.")
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
            position = await self.manager.enqueue(task)
        except (QueueCapacityError, UserTaskLimitError) as exc:
            await self.telegram.answer_callback(call, str(exc))
            return
        self.selections.pop(token)
        await self.telegram.answer_callback(call, "В очереди")
        await self.telegram.edit_status(
            call.message.chat.id,
            call.message.message_id,
            panel("В очереди", [f"Место в очереди: {position}."]),
            reply_markup=None,
        )

    def router(self) -> Router:
        router = Router(name="downloads")
        router.message.register(
            self.handle_url,
            StateFilter(None),
            lambda message: bool(
                (message.text or message.caption)
                and not (message.text or message.caption or "").strip().startswith("/")
            ),
        )
        router.callback_query.register(
            self.handle_media_choice,
            lambda call: bool(call.data and call.data.startswith("media|")),
        )
        return router


def build_download_router(
    *,
    manager: DownloadManager,
    info_service: VideoInfoService,
    selections: SelectionStore,
    settings: Settings,
    telegram: TelegramGateway,
    ffmpeg_available: bool = True,
) -> Router:
    return DownloadHandlers(
        manager=manager,
        info_service=info_service,
        selections=selections,
        settings=settings,
        telegram=telegram,
        ffmpeg_available=ffmpeg_available,
    ).router()
