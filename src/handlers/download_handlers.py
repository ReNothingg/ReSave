import asyncio
import logging
import re
from urllib.parse import urlsplit, urlunsplit

import config
from aiogram import Router
from aiogram.filters import StateFilter
from aiogram.fsm.context import FSMContext
from aiogram.types import (
    CallbackQuery,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    Message,
)

from .download_processing import (
    extract_video_info as _extract_video_info,
    handle_group_download as _handle_group_download,
)
from ..utils.message_templates import ErrorMessages
from ..utils.ui_manager import get_ui_manager

logger = logging.getLogger(__name__)

HTTP_URL_RE = re.compile(r"https?://[^\s<>\"'`]+", re.IGNORECASE)
BARE_URL_RE = re.compile(
    r"(?:www\.)?(?:[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\.)+[a-z]{2,24}"
    r"(?:/[^\s<>\"'`]*)?",
    re.IGNORECASE,
)
SUPPORTED_SCHEMES = {"http", "https"}


_download_manager = None


def set_download_manager(manager):
    global _download_manager
    _download_manager = manager


def get_download_manager():
    return _download_manager


def _build_download_limit_text(chat_id: int) -> str:
    ui_manager = get_ui_manager()
    active_downloads = _download_manager.get_user_task_count(chat_id) if _download_manager else 0
    return ui_manager.format_panel(
        "Лимит активных загрузок",
        [
            ErrorMessages.CONCURRENT_LIMIT,
            "",
            f"Активных загрузок: {active_downloads}/{config.MAX_DOWNLOADS_PER_USER}",
        ],
        icon="⏸️",
    )


def _build_download_markup(message_id: int, info: dict, resolutions: dict[int, dict]) -> InlineKeyboardMarkup:
    rows: list[list[InlineKeyboardButton]] = []

    youtube_resolutions = _youtube_resolution_buttons(message_id, info, resolutions)
    if youtube_resolutions:
        rows.extend(youtube_resolutions)
    else:
        rows.extend(
            [
                [
                    InlineKeyboardButton(
                        text="🎬 Максимум",
                        callback_data=f"dl_best_{message_id}",
                    )
                ],
                [
                    InlineKeyboardButton(
                        text="📹 720p",
                        callback_data=f"dl_medium_{message_id}",
                    ),
                    InlineKeyboardButton(
                        text="📱 480p",
                        callback_data=f"dl_low_{message_id}",
                    )
                ],
            ]
        )

    rows.append(
        [
            InlineKeyboardButton(
                text="🎵 MP3",
                callback_data=f"dl_audio_{message_id}",
                style="primary",
            )
        ]
    )

    duration = info.get("duration")
    if duration and duration <= 30:
        rows.append(
            [
                InlineKeyboardButton(
                    text="✨ GIF",
                    callback_data=f"dl_gif_{message_id}",
                )
            ]
        )

    subtitles = info.get("subtitles", {})
    auto_captions = info.get("automatic_captions", {})
    if subtitles or auto_captions:
        rows.append(
            [
                InlineKeyboardButton(
                    text="📝 Субтитры",
                    callback_data=f"dl_subtitles_{message_id}",
                    style="primary",
                )
            ]
        )

    if info.get("thumbnail"):
        rows.append(
            [
                InlineKeyboardButton(
                    text="🖼️ Превью",
                    callback_data=f"dl_thumbnail_{message_id}",
                    style="success",
                )
            ]
        )

    rows.append([
        InlineKeyboardButton(
            text="✕ Отмена",
            callback_data=f"cancel_{message_id}",
            style="danger",
        )
    ])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def _youtube_resolution_buttons(
    message_id: int,
    info: dict,
    resolutions: dict[int, dict],
) -> list[list[InlineKeyboardButton]]:
    webpage_url = (info.get("webpage_url") or info.get("original_url") or "").lower()
    extractor = (info.get("extractor_key") or info.get("extractor") or "").lower()
    is_youtube = "youtube" in extractor or "youtu.be" in webpage_url or "youtube.com" in webpage_url
    if not is_youtube or not resolutions:
        return []

    preferred_order = [4320, 2160, 1440, 1080, 720, 480, 360]
    available = [height for height in preferred_order if height in resolutions]
    extra = [height for height in sorted(resolutions.keys(), reverse=True) if height not in available]
    heights = (available + extra)[:6]

    buttons = [
        InlineKeyboardButton(
            text=f"🎥 {height}p",
            callback_data=f"dl_res_{height}_{message_id}",
        )
        for height in heights
    ]

    rows: list[list[InlineKeyboardButton]] = []
    for index in range(0, len(buttons), 2):
        rows.append(buttons[index:index + 2])
    return rows


def _extract_url_from_entities(text: str, entities) -> str | None:
    for entity in entities or []:
        entity_url = getattr(entity, "url", None)
        if entity_url:
            return entity_url

        if getattr(entity, "type", None) != "url":
            continue

        extract_from = getattr(entity, "extract_from", None)
        if callable(extract_from):
            return extract_from(text)

    return None


def _clean_download_target(value: str) -> str:
    target = value.strip().rstrip(".,;:!?)»”’]")

    if "://" not in target and "." in target.split("/", 1)[0]:
        target = f"https://{target}"

    try:
        parsed = urlsplit(target)
    except ValueError:
        return target

    host = (parsed.hostname or "").lower()
    twitter_hosts = {
        "x.com",
        "www.x.com",
        "mobile.x.com",
        "m.x.com",
        "twitter.com",
        "www.twitter.com",
        "mobile.twitter.com",
        "m.twitter.com",
    }
    if host in twitter_hosts:
        path_parts = [part for part in parsed.path.split("/") if part]
        if (
            len(path_parts) >= 3
            and path_parts[1] in {"status", "statuses"}
            and path_parts[2].isdigit()
        ):
            return urlunsplit(
                (
                    parsed.scheme,
                    "twitter.com",
                    f"/{path_parts[0]}/status/{path_parts[2]}",
                    "",
                    "",
                )
            )

    return target


def _is_probably_url(value: str) -> bool:
    try:
        parsed = urlsplit(value)
    except ValueError:
        return False

    if parsed.scheme and parsed.scheme.lower() not in SUPPORTED_SCHEMES:
        return False

    host = parsed.hostname or ""
    if not parsed.scheme and not host:
        return False

    if host == "localhost":
        return True

    if "." not in host:
        return False

    try:
        ascii_host = host.encode("idna").decode("ascii")
    except UnicodeError:
        return False
    if ascii_host != host.lower():
        return False

    labels = ascii_host.split(".")
    tld = labels[-1]
    if len(tld) < 2 or len(tld) > 24:
        return False

    return all(
        label
        and len(label) <= 63
        and re.fullmatch(r"[a-z0-9-]+", label, re.IGNORECASE)
        and not label.startswith("-")
        and not label.endswith("-")
        for label in labels
    )


def _extract_download_target(text: str, entities, caption_entities) -> str | None:
    entity_url = (
        _extract_url_from_entities(text, entities)
        or _extract_url_from_entities(text, caption_entities)
    )
    if entity_url:
        target = _clean_download_target(entity_url)
        return target if _is_probably_url(target) else None

    match = HTTP_URL_RE.search(text) or BARE_URL_RE.search(text)
    if match:
        target = _clean_download_target(match.group(0))
        return target if _is_probably_url(target) else None

    if (
        not any(char.isspace() for char in text)
        and ("://" in text or text.lower().startswith("www."))
    ):
        target = _clean_download_target(text)
        return target if _is_probably_url(target) else None

    return None


def _run_background_thread(func, *args, label: str):
    task = asyncio.create_task(asyncio.to_thread(func, *args))

    def _consume_result(done_task: asyncio.Task):
        try:
            done_task.result()
        except asyncio.CancelledError:
            logger.debug("Фоновая задача отменена: %s", label)
        except Exception:
            logger.exception("Ошибка в фоновой задаче: %s", label)

    task.add_done_callback(_consume_result)
    return task


def register_download_handlers(router: Router, sync_bot):
    ui_manager = get_ui_manager()
    video_info_cache: dict[int, dict] = {}

    async def process_url_message(message: Message, state: FSMContext):
        if message.from_user and message.from_user.is_bot:
            return

        if await state.get_state():
            return

        text = (message.text or message.caption or "").strip()
        if not text:
            return

        if text.startswith("/"):
            return

        url = _extract_download_target(text, message.entities, message.caption_entities)
        if not url:
            return

        from ..core.tiktok_photo_handler import is_tiktok_photo_url

        if message.chat.type in {"group", "supergroup"}:
            logger.info("Получена ссылка в группе %s: %s", message.chat.id, url)
            _run_background_thread(
                handle_group_download,
                url,
                message.chat.id,
                message.message_id,
                label=f"group_download_info:{message.chat.id}:{message.message_id}",
            )
            return

        if is_tiktok_photo_url(url):
            status_message = await message.reply(
                ui_manager.format_panel(
                    "TikTok photo",
                    ["Добавляю фото-пост в очередь."],
                    icon="🖼️",
                )
            )
            try:
                _download_manager.add_task(
                    url=url,
                    chat_id=message.chat.id,
                    message_id=status_message.message_id,
                    info={"title": "TikTok photo", "duration": None},
                    action="tiktok_photo",
                    reply_to_id=message.message_id,
                )
            except ValueError:
                await status_message.edit_text(_build_download_limit_text(message.chat.id))
            return

        status_message = await message.reply(
            ui_manager.format_panel(
                "Ищу видео",
                ["Проверяю ссылку и доступные форматы."],
                icon="🔍",
            )
        )

        _run_background_thread(
            extract_video_info,
            sync_bot,
            message.chat.id,
            message.message_id,
            url,
            status_message.message_id,
            video_info_cache,
            label=f"video_info:{message.chat.id}:{message.message_id}",
        )

    async def handle_url(message: Message, state: FSMContext):
        await process_url_message(message, state)

    async def handle_download(call: CallbackQuery):
        if not call.data or not call.message:
            return

        parts = call.data.split("_")
        action = parts[1]
        original_message_id = int(parts[-1])

        if original_message_id not in video_info_cache:
            await call.answer("❌ Информация устарела. Отправьте ссылку заново.")
            return

        download_info = video_info_cache[original_message_id]
        if not _download_manager.can_add_task(call.message.chat.id):
            await call.answer("Лимит активных загрузок достигнут.", show_alert=True)
            await call.message.edit_text(_build_download_limit_text(call.message.chat.id))
            return

        await call.answer("Начинаю скачивание.")
        await call.message.edit_text(
            ui_manager.format_panel(
                "Добавляю в очередь",
                ["Загрузка начнется автоматически."],
                icon="📥",
            )
        )

        format_param = int(parts[2]) if action == "res" else None
        url = download_info["url"]
        try:
            _download_manager.add_task(
                url=url,
                chat_id=call.message.chat.id,
                message_id=call.message.message_id,
                info=download_info["info"],
                action=action,
                format_param=format_param,
            )
        except ValueError:
            await call.message.edit_text(_build_download_limit_text(call.message.chat.id))
            return

        video_info_cache.pop(original_message_id, None)

    async def handle_cancel_all_downloads(call: CallbackQuery):
        if not call.message:
            return

        with _download_manager.lock:
            user_tasks = {
                task_id: task
                for task_id, task in _download_manager.tasks.items()
                if task.chat_id == call.message.chat.id and task.status in {"downloading", "pending"}
            }

        if not user_tasks:
            await call.answer("Нет активных загрузок для отмены.")
            return

        cancelled_count = 0
        for task_id in user_tasks:
            if _download_manager.cancel_task(task_id):
                cancelled_count += 1

        await call.answer(f"Отменено {cancelled_count} загрузок.")
        await call.message.edit_text(
            ui_manager.format_panel(
                "Загрузки отменены",
                [f"Отменено: {cancelled_count}", "Можно отправить новую ссылку."],
                icon="✅",
            )
        )

    async def handle_cancel(call: CallbackQuery):
        if not call.message:
            return

        await call.answer("Запрос отменен.")
        try:
            await call.message.delete()
        except Exception:
            await call.message.edit_text(
                ui_manager.format_panel("Запрос отменен", icon="✕")
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
        handle_download,
        lambda call: bool(call.data and call.data.startswith("dl_")),
    )
    router.callback_query.register(
        handle_cancel_all_downloads,
        lambda call: call.data == "cancel_all_downloads",
    )
    router.callback_query.register(
        handle_cancel,
        lambda call: bool(call.data and call.data.startswith("cancel_")),
    )

def handle_group_download(url: str, chat_id: int, message_id: int):
    _handle_group_download(
        url,
        chat_id,
        message_id,
        download_manager=_download_manager,
    )


def extract_video_info(
    bot,
    chat_id: int,
    user_message_id: int,
    url: str,
    status_message_id: int,
    cache: dict[int, dict],
):
    _extract_video_info(
        bot,
        chat_id,
        user_message_id,
        url,
        status_message_id,
        cache,
        download_manager=_download_manager,
        build_download_limit_text=_build_download_limit_text,
        build_download_markup=_build_download_markup,
    )
