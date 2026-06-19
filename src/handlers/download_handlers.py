import asyncio
import logging
import os
import shutil
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from uuid import uuid4

import config
from aiogram import Bot, Router
from aiogram.exceptions import TelegramBadRequest
from aiogram.types import (
    CallbackQuery,
    ChosenInlineResult,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    InlineQuery,
    InlineQueryResultArticle,
    InlineQueryResultVideo,
    InlineQueryResultsButton,
    InputTextMessageContent,
    InputMediaVideo,
    Message,
)

from ..core.access import (
    build_duration_limit_error,
    build_file_size_limit_error,
    build_playlist_limit_error,
    collect_playlist_entries,
)
from ..core.download_support import describe_work_dir, find_completed_files
from .download_processing import (
    extract_video_info as _extract_video_info,
    handle_group_download as _handle_group_download,
)
from ..utils.message_templates import ErrorMessages, MessageTemplate
from ..utils.ui_manager import get_ui_manager

logger = logging.getLogger(__name__)


_download_manager = None


INLINE_THUMBNAIL_URL = "https://raw.githubusercontent.com/ReNothingg/ReNothingg/refs/heads/main/main.jpg"
INLINE_START_LINK_TTL = 15 * 60
INLINE_PREPARATION_TTL = 10 * 60
INLINE_READY_CACHE_TTL = 6 * 60 * 60
INLINE_ERROR_CACHE_TTL = 20 * 60

_inline_start_links: dict[str, tuple[str, float]] = {}
_inline_start_links_lock = threading.Lock()


@dataclass
class InlinePreparation:
    url: str
    user_id: int
    info: dict | None = None
    direct_media_url: str | None = None
    created_at: float = field(default_factory=time.time)


@dataclass
class InlineMediaCacheItem:
    url: str
    user_id: int
    status: str = "downloading"
    info: dict | None = None
    file_id: str | None = None
    direct_media_url: str | None = None
    error: str | None = None
    pending_inline_message_ids: set[str] = field(default_factory=set)
    updated_at: float = field(default_factory=time.time)


def _prune_inline_start_links_locked(now: float | None = None) -> None:
    current_time = now or time.time()
    for token, (_, created_at) in list(_inline_start_links.items()):
        if current_time - created_at > INLINE_START_LINK_TTL:
            _inline_start_links.pop(token, None)


def create_inline_start_parameter(url: str) -> str:
    token = f"i_{uuid4().hex[:16]}"
    with _inline_start_links_lock:
        _prune_inline_start_links_locked()
        _inline_start_links[token] = (url, time.time())
    return token


def resolve_inline_start_url(parameter: str | None) -> str | None:
    if not parameter:
        return None

    token = parameter.strip()
    with _inline_start_links_lock:
        _prune_inline_start_links_locked()
        item = _inline_start_links.get(token)
        if not item:
            return None
        return item[0]


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


def _is_expired_inline_query_error(exc: Exception) -> bool:
    error_text = str(exc).lower()
    return (
        "query is too old" in error_text
        or "response timeout expired" in error_text
        or "query id is invalid" in error_text
    )


def register_download_handlers(router: Router, sync_bot):
    ui_manager = get_ui_manager()
    video_info_cache: dict[int, dict] = {}
    inline_preparations: dict[str, InlinePreparation] = {}
    inline_media_cache: dict[str, InlineMediaCacheItem] = {}
    inline_jobs: dict[str, asyncio.Task] = {}

    def open_bot_button(url: str | None = None) -> InlineQueryResultsButton:
        return InlineQueryResultsButton(
            text="Открыть ReSave",
            start_parameter=create_inline_start_parameter(url) if url else "start",
        )

    def inline_open_markup(url: str, result_id: str | None = None) -> InlineKeyboardMarkup:
        token = create_inline_start_parameter(url)
        rows: list[list[InlineKeyboardButton]] = []
        if result_id:
            rows.append(
                [
                    InlineKeyboardButton(
                        text="Подготовить",
                        callback_data=f"inline_prepare_{result_id}",
                    )
                ]
            )
        rows.append(
            [
                InlineKeyboardButton(
                    text="Открыть ReSave",
                    url=f"https://t.me/ReSafeBot?start={token}",
                )
            ]
        )
        return InlineKeyboardMarkup(
            inline_keyboard=rows
        )

    def inline_article(
        *,
        status: str,
        title: str,
        description: str,
        message_text: str,
        reply_markup: InlineKeyboardMarkup | None = None,
    ) -> InlineQueryResultArticle:
        return InlineQueryResultArticle(
            id=f"{status}_{uuid4().hex[:8]}",
            title=title,
            description=description,
            input_message_content=InputTextMessageContent(message_text=message_text),
            thumbnail_url=INLINE_THUMBNAIL_URL,
            reply_markup=reply_markup,
        )

    def prune_inline_state() -> None:
        now = time.time()
        for result_id, item in list(inline_preparations.items()):
            if now - item.created_at > INLINE_PREPARATION_TTL:
                inline_preparations.pop(result_id, None)

        for url, item in list(inline_media_cache.items()):
            ttl = INLINE_READY_CACHE_TTL if item.status == "ready" else INLINE_ERROR_CACHE_TTL
            if now - item.updated_at > ttl and url not in inline_jobs:
                inline_media_cache.pop(url, None)

    def cache_chat_id_for(user_id: int) -> int:
        if config.INLINE_CACHE_CHAT_ID:
            return config.INLINE_CACHE_CHAT_ID
        if config.ADMIN_IDS:
            return config.ADMIN_IDS[0]
        return user_id

    def build_loading_inline_result(result_id: str, url: str) -> InlineQueryResultArticle:
        return InlineQueryResultArticle(
            id=result_id,
            title="Скачать и заменить здесь",
            description="Сначала отправлю заглушку, потом заменю ее видео",
            input_message_content=InputTextMessageContent(
                message_text=ui_manager.format_panel(
                    "Скачиваю видео",
                    [
                        "Видео появится здесь после загрузки.",
                        "",
                        url,
                    ],
                    icon="⏳",
                )
            ),
            thumbnail_url=INLINE_THUMBNAIL_URL,
            reply_markup=inline_open_markup(url, result_id),
        )

    def build_inline_video_media(info: dict, media: str, url: str) -> InputMediaVideo:
        return InputMediaVideo(
            media=media,
            caption=MessageTemplate.format_inline_caption(info.get("title", "Видео"), url),
            parse_mode="HTML",
            width=info.get("width") if isinstance(info.get("width"), int) else None,
            height=info.get("height") if isinstance(info.get("height"), int) else None,
            duration=info.get("duration") if isinstance(info.get("duration"), int) else None,
            supports_streaming=True,
        )

    async def edit_inline_message_to_error(
        bot: Bot,
        inline_message_id: str,
        url: str,
        text: str,
    ) -> None:
        try:
            await bot.edit_message_text(
                inline_message_id=inline_message_id,
                text=ui_manager.format_panel(
                    "Не удалось подготовить видео",
                    [text, "", url],
                    icon="❌",
                ),
                reply_markup=inline_open_markup(url),
            )
        except TelegramBadRequest as exc:
            logger.warning("Inline error edit rejected: %s", exc)

    async def edit_inline_message_to_video(
        bot: Bot,
        inline_message_id: str,
        item: InlineMediaCacheItem,
    ) -> None:
        media = item.file_id or item.direct_media_url
        if not media or not item.info:
            await edit_inline_message_to_error(
                bot,
                inline_message_id,
                item.url,
                item.error or "Файл не был загружен в кеш Telegram.",
            )
            return

        try:
            await bot.edit_message_media(
                inline_message_id=inline_message_id,
                media=build_inline_video_media(item.info, media, item.url),
                reply_markup=inline_open_markup(item.url),
            )
        except TelegramBadRequest as exc:
            logger.warning("Inline media edit rejected: %s", exc)
            await edit_inline_message_to_error(
                bot,
                inline_message_id,
                item.url,
                "Telegram не принял замену inline-сообщения. Откройте ссылку в боте.",
            )

    def prepare_inline_cached_video(url: str, user_id: int) -> tuple[dict, str]:
        import yt_dlp

        inline_dir = Path(config.TEMP_DIR) / f"inline_{user_id}_{int(time.time())}_{uuid4().hex[:8]}"
        inline_dir.mkdir(parents=True, exist_ok=True)
        output_path = str(inline_dir / "media")
        try:
            info_opts = {
                "quiet": True,
                "no_warnings": True,
                "extract_flat": False,
                "skip_download": True,
                "socket_timeout": 15,
                "retries": 2,
                "extractor_retries": 2,
                "cookiefile": config.COOKIES_FILE,
                "nocheckcertificate": True,
            }
            with yt_dlp.YoutubeDL(info_opts) as ydl:
                info = ydl.extract_info(url, download=False)

            if not info:
                raise RuntimeError("yt-dlp не вернул информацию о видео.")
            if info.get("_type") == "playlist":
                raise RuntimeError("Плейлисты в inline не скачиваются. Отправьте ссылку боту напрямую.")

            duration_error = build_duration_limit_error(user_id, info.get("duration"))
            if duration_error:
                raise RuntimeError(duration_error)

            ydl_params = {
                "format": "bv*[height<=720]+ba/best[height<=720]/best",
                "outtmpl": f"{output_path}.%(ext)s",
                "quiet": True,
                "no_warnings": True,
                "noplaylist": True,
                "merge_output_format": "mp4",
                "http_chunk_size": 10485760,
                "cookiefile": config.COOKIES_FILE,
                "socket_timeout": 30,
                "retries": 2,
                "fragment_retries": 2,
                "nocheckcertificate": True,
            }

            with yt_dlp.YoutubeDL(ydl_params) as ydl:
                ydl.download([url])

            downloaded_files = find_completed_files(inline_dir)
            if not downloaded_files:
                logger.error(
                    "Inline file not found after download. url=%s work_dir=%s contents=%s",
                    url,
                    inline_dir,
                    describe_work_dir(inline_dir),
                )
                raise RuntimeError("Файл не найден после скачивания.")

            file_path = max(downloaded_files, key=lambda path: path.stat().st_mtime)
            file_size = os.path.getsize(file_path)
            file_size_error = build_file_size_limit_error(user_id, file_size)
            if file_size_error:
                raise RuntimeError(file_size_error)

            cache_chat_id = cache_chat_id_for(user_id)
            message = sync_bot.send_video(
                cache_chat_id,
                str(file_path),
                caption=MessageTemplate.format_inline_caption(info.get("title", "Видео"), url),
                parse_mode="HTML",
                supports_streaming=True,
                timeout=600,
            )
            if not message.video:
                raise RuntimeError("Telegram не вернул file_id видео.")

            try:
                sync_bot.delete_message(cache_chat_id, message.message_id)
            except Exception as exc:
                logger.debug("Не удалось удалить cache-сообщение: %s", exc)

            return info, message.video.file_id
        finally:
            shutil.rmtree(inline_dir, ignore_errors=True)

    async def process_inline_media_job(url: str, bot: Bot) -> None:
        item = inline_media_cache[url]
        try:
            info, file_id = await asyncio.to_thread(
                prepare_inline_cached_video,
                url,
                item.user_id,
            )
            item.status = "ready"
            item.info = info
            item.file_id = file_id
            item.error = None
            item.updated_at = time.time()
            pending_ids = list(item.pending_inline_message_ids)
            item.pending_inline_message_ids.clear()
            logger.info("Inline media cache ready: url=%s pending=%s", url, len(pending_ids))
            for inline_message_id in pending_ids:
                await edit_inline_message_to_video(bot, inline_message_id, item)
        except Exception as exc:
            item.status = "error"
            item.error = str(exc)
            item.updated_at = time.time()
            pending_ids = list(item.pending_inline_message_ids)
            item.pending_inline_message_ids.clear()
            logger.exception("Inline media preparation failed: %s", exc)
            for inline_message_id in pending_ids:
                await edit_inline_message_to_error(bot, inline_message_id, url, item.error)
        finally:
            inline_jobs.pop(url, None)

    def _is_http_url(value: str | None) -> bool:
        return bool(value and value.startswith(("http://", "https://")))

    def _direct_thumbnail_url(info: dict) -> str:
        thumbnail = str(info.get("thumbnail") or "")
        if thumbnail.lower().split("?", 1)[0].endswith((".jpg", ".jpeg")):
            return thumbnail
        return INLINE_THUMBNAIL_URL

    def _format_filesize(format_info: dict) -> int | None:
        filesize = format_info.get("filesize") or format_info.get("filesize_approx")
        return int(filesize) if isinstance(filesize, (int, float)) and filesize > 0 else None

    def _format_is_direct_mp4(format_info: dict, *, require_audio: bool) -> bool:
        video_url = format_info.get("url")
        if not _is_http_url(video_url):
            return False
        if (format_info.get("ext") or "").lower() != "mp4":
            return False
        if format_info.get("vcodec") in {None, "none"}:
            return False
        if require_audio and format_info.get("acodec") in {None, "none"}:
            return False

        protocol = (format_info.get("protocol") or "").lower()
        return "m3u8" not in protocol and "dash" not in protocol

    def _select_direct_mp4(info: dict, user_id: int) -> dict | None:
        formats = list(info.get("formats") or [])
        if info.get("url"):
            formats.append(info)

        too_large_seen = False
        candidates = []
        for format_info in formats:
            if not _format_is_direct_mp4(format_info, require_audio=True):
                continue

            height = format_info.get("height")
            if isinstance(height, int) and height > 720:
                continue

            filesize = _format_filesize(format_info)
            if filesize and build_file_size_limit_error(user_id, filesize):
                too_large_seen = True
                continue

            width = format_info.get("width") if isinstance(format_info.get("width"), int) else 0
            normalized_height = height if isinstance(height, int) else 0
            candidates.append((normalized_height, width, format_info))

        if candidates:
            return max(candidates, key=lambda item: (item[0], item[1]))[2]

        if too_large_seen:
            return {"_inline_status": "too_large"}
        return None

    def build_direct_inline_video_result(
        url: str,
        user_id: int,
        capture: dict | None = None,
    ):
        import yt_dlp

        ydl_opts = {
            "quiet": True,
            "no_warnings": True,
            "extract_flat": False,
            "skip_download": True,
            "socket_timeout": 8,
            "retries": 1,
            "extractor_retries": 1,
            "cookiefile": config.COOKIES_FILE,
            "nocheckcertificate": True,
        }

        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            info = ydl.extract_info(url, download=False)

        if not info:
            return None

        if info.get("_type") == "playlist":
            playlist_text = build_playlist_limit_error(
                user_id,
                len(collect_playlist_entries(info or {})),
            ) or "Плейлист лучше отправить напрямую в @ReSafeBot."
            return inline_article(
                status="playlist",
                title="Плейлист откройте в боте",
                description="Inline-режим рассчитан на одиночные ролики",
                message_text=playlist_text,
            )

        duration_error = build_duration_limit_error(user_id, info.get("duration"))
        if duration_error:
            return inline_article(
                status="limit",
                title="Видео слишком длинное",
                description="Откройте бота, чтобы скачать с подсказками",
                message_text=duration_error,
            )

        direct_format = _select_direct_mp4(info, user_id)
        if direct_format and direct_format.get("_inline_status") == "too_large":
            limit_text = (
                build_file_size_limit_error(user_id, config.BOT_API_UPLOAD_LIMIT + 1)
                or "Видео превышает допустимый размер."
            )
            return inline_article(
                status="size_limit",
                title="Файл слишком большой",
                description="Откройте бота, чтобы скачать с подсказками",
                message_text=limit_text,
            )
        if not direct_format:
            return None

        title = info.get("title", "Видео")
        height = direct_format.get("height") if isinstance(direct_format.get("height"), int) else None
        width = direct_format.get("width") if isinstance(direct_format.get("width"), int) else None
        duration = info.get("duration") if isinstance(info.get("duration"), int) else None
        logger.debug(
            "Inline direct video: user_id=%s height=%s url=%s",
            user_id,
            height,
            url,
        )
        if capture is not None:
            capture["info"] = info
            capture["direct_media_url"] = direct_format["url"]
        return InlineQueryResultVideo(
            id=f"direct_{uuid4().hex[:8]}",
            video_url=direct_format["url"],
            mime_type="video/mp4",
            thumbnail_url=_direct_thumbnail_url(info),
            title=title,
            caption=MessageTemplate.format_inline_caption(title, url),
            parse_mode="HTML",
            video_width=width,
            video_height=height,
            video_duration=duration,
            description="Отправить видео напрямую",
        )

    async def inline_query_handler(inline_query: InlineQuery, bot: Bot):
        prune_inline_state()
        raw_query = (inline_query.query or "").strip()
        user_id = inline_query.from_user.id

        async def answer_inline_query(**kwargs) -> bool:
            try:
                await bot.answer_inline_query(inline_query_id=inline_query.id, **kwargs)
                return True
            except TelegramBadRequest as exc:
                if _is_expired_inline_query_error(exc):
                    logger.warning("Inline query expired before answer: %s", exc)
                    return False
                raise

        try:
            if not raw_query:
                result = inline_article(
                    status="help",
                    title="ReSave - скачать видео",
                    description="Введите ссылку после @ReSafeBot",
                    message_text=ui_manager.format_panel(
                        "ReSave inline",
                        ["Введите ссылку после @ReSafeBot."],
                        icon="⚡",
                    ),
                )
                await answer_inline_query(
                    results=[result],
                    cache_time=1,
                    is_personal=True,
                    button=open_bot_button(),
                )
                return

            from ..utils.url_validator import get_url_validator

            url_validator = get_url_validator()
            extracted_url = url_validator.extract_url(raw_query)
            is_valid, corrected_url, _ = url_validator.validate(extracted_url or raw_query)
            if not is_valid:
                result = inline_article(
                    status="invalid_url",
                    title="Вставьте ссылку на видео",
                    description="Подойдёт любой http/https URL, который умеет yt-dlp",
                    message_text="Вставьте ссылку после @ReSafeBot.",
                )
                await answer_inline_query(
                    results=[result],
                    cache_time=1,
                    is_personal=True,
                    button=open_bot_button(),
                )
                return

            url = corrected_url or extracted_url or raw_query
            direct_result = None
            direct_capture: dict = {}
            if config.INLINE_DIRECT_RESULTS_ENABLED:
                try:
                    direct_result = await asyncio.wait_for(
                        asyncio.to_thread(
                            build_direct_inline_video_result,
                            url,
                            user_id,
                            direct_capture,
                        ),
                        timeout=config.INLINE_EXTRACT_TIMEOUT,
                    )
                except asyncio.TimeoutError:
                    logger.info("Inline direct extraction timed out: user_id=%s url=%s", user_id, url)
                except Exception as exc:
                    logger.info("Inline direct extraction unavailable for %s: %s", url, exc)

            result_id = f"inline_{uuid4().hex[:24]}"
            inline_preparations[result_id] = InlinePreparation(
                url=url,
                user_id=user_id,
                info=direct_capture.get("info"),
                direct_media_url=direct_capture.get("direct_media_url"),
            )

            if direct_result:
                if isinstance(direct_result, InlineQueryResultVideo):
                    results = [
                        direct_result,
                        build_loading_inline_result(result_id, url),
                    ]
                else:
                    results = [direct_result]
            else:
                results = [build_loading_inline_result(result_id, url)]

            await answer_inline_query(
                results=results,
                cache_time=1,
                is_personal=True,
            )
        except Exception as exc:
            logger.exception("Critical inline error: %s", exc)
            fallback = inline_article(
                status="inline_error",
                title="Откройте ReSave",
                description="Inline не успел обработать ссылку",
                message_text="Откройте @ReSafeBot и отправьте ссылку напрямую.",
            )
            await answer_inline_query(
                results=[fallback],
                cache_time=1,
                is_personal=True,
                button=open_bot_button(),
            )

    async def queue_inline_preparation(
        *,
        result_id: str,
        inline_message_id: str,
        user_id: int,
        bot: Bot,
    ) -> bool:
        preparation = inline_preparations.get(result_id)
        if not preparation:
            return False

        url = preparation.url
        item = inline_media_cache.get(url)
        if preparation.info and preparation.direct_media_url and (
            not item or item.status != "ready"
        ):
            item = InlineMediaCacheItem(
                url=url,
                user_id=user_id,
                status="ready",
                info=preparation.info,
                direct_media_url=preparation.direct_media_url,
            )
            inline_media_cache[url] = item
            await edit_inline_message_to_video(bot, inline_message_id, item)
            return True

        if item and item.status == "ready":
            await edit_inline_message_to_video(bot, inline_message_id, item)
            return True

        if item and item.status == "error":
            await edit_inline_message_to_error(
                bot,
                inline_message_id,
                url,
                item.error or "Предыдущая подготовка завершилась ошибкой.",
            )
            return True

        if not item:
            item = InlineMediaCacheItem(
                url=url,
                user_id=user_id,
                info=preparation.info,
                direct_media_url=preparation.direct_media_url,
            )
            inline_media_cache[url] = item

        item.pending_inline_message_ids.add(inline_message_id)
        item.updated_at = time.time()

        if url not in inline_jobs:
            inline_jobs[url] = asyncio.create_task(process_inline_media_job(url, bot))
        return True

    async def chosen_inline_result_handler(chosen_result: ChosenInlineResult, bot: Bot):
        prune_inline_state()
        inline_message_id = chosen_result.inline_message_id
        if not inline_message_id:
            logger.warning(
                "Chosen inline result has no inline_message_id. "
                "Enable inline feedback in BotFather. result_id=%s",
                chosen_result.result_id,
            )
            return

        await queue_inline_preparation(
            result_id=chosen_result.result_id,
            inline_message_id=inline_message_id,
            user_id=chosen_result.from_user.id,
            bot=bot,
        )

    async def handle_inline_prepare_callback(call: CallbackQuery, bot: Bot):
        if not call.data or not call.inline_message_id:
            await call.answer("Не вижу inline-сообщение.", show_alert=True)
            return

        result_id = call.data.removeprefix("inline_prepare_")
        queued = await queue_inline_preparation(
            result_id=result_id,
            inline_message_id=call.inline_message_id,
            user_id=call.from_user.id,
            bot=bot,
        )
        if queued:
            await call.answer("Готовлю видео.")
        else:
            await call.answer("Запрос устарел. Повторите inline-поиск.", show_alert=True)

    async def process_url_message(message: Message, text_override: str | None = None):
        from ..utils.url_validator import get_url_validator

        if text_override is None and message.from_user and message.from_user.is_bot:
            return

        text = (
            text_override
            if text_override is not None
            else (message.text or message.caption or "")
        ).strip()
        if not text:
            return

        if text_override is None and text.startswith("/"):
            return

        url_validator = get_url_validator()
        extracted_url = None
        if text_override is None:
            for entity in [*(message.entities or []), *(message.caption_entities or [])]:
                entity_url = getattr(entity, "url", None)
                if entity_url:
                    extracted_url = entity_url
                    break

        extracted_url = extracted_url or url_validator.extract_url(text)
        is_valid, corrected_url, metadata = url_validator.validate(extracted_url or "")
        if not is_valid:
            if message.chat.type == "private":
                await message.reply(
                    ui_manager.format_panel(
                        "Ссылка не распознана",
                        [
                            "Пришлите обычный URL с http:// или https://.",
                            "Если источник поддерживается yt-dlp, я попробую скачать видео.",
                        ],
                        icon="🔗",
                    )
                )
            return

        url = corrected_url or extracted_url

        if message.chat.type in {"group", "supergroup"}:
            logger.info("Получена ссылка в группе %s: %s", message.chat.id, url)
            asyncio.create_task(
                asyncio.to_thread(
                    handle_group_download,
                    url,
                    message.chat.id,
                    message.message_id,
                )
            )
            return

        if metadata.get("fixed"):
            logger.info("Ссылка исправлена: %s", metadata.get("fix_type"))
            status_message = await message.reply(
                ui_manager.format_panel(
                    "Ссылка исправлена",
                    ["Ищу информацию о видео. Обычно это занимает несколько секунд."],
                    icon="✅",
                )
            )
        else:
            status_message = await message.reply(
                ui_manager.format_panel(
                    "Ищу видео",
                    ["Проверяю ссылку и доступные форматы."],
                    icon="🔍",
                )
            )

        asyncio.create_task(
            asyncio.to_thread(
                extract_video_info,
                sync_bot,
                message.chat.id,
                message.message_id,
                url,
                status_message.message_id,
                video_info_cache,
            )
        )

    async def handle_url(message: Message):
        await process_url_message(message)

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

    router.inline_query.register(inline_query_handler)
    router.chosen_inline_result.register(chosen_inline_result_handler)
    router.message.register(
        handle_url,
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
        handle_inline_prepare_callback,
        lambda call: bool(call.data and call.data.startswith("inline_prepare_")),
    )
    router.callback_query.register(
        handle_cancel,
        lambda call: bool(call.data and call.data.startswith("cancel_")),
    )

    return process_url_message


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
