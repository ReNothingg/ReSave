import asyncio
import logging
import os
import shutil
import threading
import time
from pathlib import Path
from uuid import uuid4

import config
from aiogram import Bot, Router
from aiogram.exceptions import TelegramBadRequest
from aiogram.types import (
    CallbackQuery,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    InlineQuery,
    InlineQueryResultArticle,
    InlineQueryResultCachedVideo,
    InlineQueryResultVideo,
    InlineQueryResultsButton,
    InputTextMessageContent,
    Message,
)

from ..core.access import (
    build_duration_limit_error,
    build_file_size_limit_error,
    build_playlist_limit_error,
    collect_playlist_entries,
)
from ..core.models import InlineQuery as InlineQueryCacheItem
from .download_processing import (
    extract_video_info as _extract_video_info,
    handle_group_download as _handle_group_download,
)
from ..utils.message_templates import ErrorMessages, MessageTemplate
from ..utils.ui_manager import get_ui_manager

logger = logging.getLogger(__name__)


_download_manager = None


INLINE_NETWORK_ERROR_KEYWORDS = (
    "timeout",
    "timed out",
    "ssl",
    "handshake",
    "transporterror",
    "connection",
    "temporarily unavailable",
    "try again",
    "502",
    "503",
    "504",
)


INLINE_UPLOAD_ERROR_KEYWORDS = (
    "failed to decode object",
    "jsondecodeerror",
    "broken pipe",
    "connection reset",
    "server disconnected",
    "can't initiate conversation",
    "bot was blocked",
    "forbidden",
)


INLINE_THUMBNAIL_URL = "https://raw.githubusercontent.com/ReNothingg/ReNothingg/refs/heads/main/main.jpg"


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


def _is_inline_network_error(exc: Exception) -> bool:
    error_text = str(exc).lower()
    return any(keyword in error_text for keyword in INLINE_NETWORK_ERROR_KEYWORDS)


def _is_inline_upload_error(exc: Exception) -> bool:
    error_text = str(exc).lower()
    return any(keyword in error_text for keyword in INLINE_UPLOAD_ERROR_KEYWORDS)


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
    inline_queries: dict[str, InlineQueryCacheItem] = {}
    inline_cache_lock = threading.Lock()
    inline_jobs: set[asyncio.Task] = set()

    def open_bot_button() -> InlineQueryResultsButton:
        return InlineQueryResultsButton(
            text="Открыть ReSave",
            start_parameter="start",
        )

    def inline_article(
        *,
        status: str,
        title: str,
        description: str,
        message_text: str,
    ) -> InlineQueryResultArticle:
        return InlineQueryResultArticle(
            id=f"{status}_{uuid4().hex[:8]}",
            title=title,
            description=description,
            input_message_content=InputTextMessageContent(message_text=message_text),
            thumbnail_url=INLINE_THUMBNAIL_URL,
        )

    def cached_video_result(item: InlineQueryCacheItem, url: str) -> InlineQueryResultCachedVideo:
        info = item.info or {}
        title = info.get("title", "Видео")
        return InlineQueryResultCachedVideo(
            id=f"cached_{uuid4().hex[:8]}",
            video_file_id=item.file_id or "",
            title=title,
            description="Готово к отправке",
            caption=MessageTemplate.format_inline_caption(title, url),
            parse_mode="HTML",
        )

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
        for require_audio in (True, False):
            candidates = []
            for format_info in formats:
                if not _format_is_direct_mp4(format_info, require_audio=require_audio):
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

    def build_direct_inline_video_result(url: str, user_id: int):
        import yt_dlp

        if "tiktok.com" in url and "/photo/" in url:
            return inline_article(
                status="tiktok_photo",
                title="TikTok фото откройте в боте",
                description="Inline-режим не отправляет фото-посты",
                message_text=(
                    "TikTok фото-пост лучше скачать напрямую в @ReSafeBot.\n\n"
                    f"Отправьте эту ссылку боту:\n{url}"
                ),
            )

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

    def prune_inline_cache_locked() -> None:
        now = time.time()
        for url, item in list(inline_queries.items()):
            ttl = 6 * 60 * 60 if item.status == "ready" else 20 * 60
            if now - item.timestamp > ttl:
                inline_queries.pop(url, None)

    def set_inline_item(
        url: str,
        *,
        query_id: str,
        user_id: int,
        status: str,
        info: dict | None = None,
        file_id: str | None = None,
        error: str | None = None,
    ) -> InlineQueryCacheItem:
        with inline_cache_lock:
            prune_inline_cache_locked()
            existing = inline_queries.get(url)
            item = InlineQueryCacheItem(
                query_id=query_id,
                url=url,
                user_id=user_id,
                result_id=(existing.result_id if existing else f"inline_{uuid4().hex[:8]}"),
                info=info if info is not None else (existing.info if existing else None),
                task_id=(existing.task_id if existing else None),
                file_id=file_id if file_id is not None else (existing.file_id if existing else None),
                timestamp=time.time(),
                status=status,
                error=error,
            )
            inline_queries[url] = item
            return item

    def get_inline_item(url: str) -> InlineQueryCacheItem | None:
        with inline_cache_lock:
            prune_inline_cache_locked()
            return inline_queries.get(url)

    def cache_chat_id_for(user_id: int) -> int:
        return config.INLINE_CACHE_CHAT_ID or (config.ADMIN_IDS[0] if config.ADMIN_IDS else user_id)

    def cleanup_inline_dir(path: str | None) -> None:
        if path and os.path.isdir(path):
            shutil.rmtree(path, ignore_errors=True)

    def prepare_inline_video(url: str, user_id: int, query_id: str) -> None:
        import yt_dlp

        inline_dir = None
        try:
            set_inline_item(url, query_id=query_id, user_id=user_id, status="downloading")

            if "tiktok.com" in url and "/photo/" in url:
                set_inline_item(url, query_id=query_id, user_id=user_id, status="tiktok_photo")
                return

            ydl_opts = {
                "quiet": True,
                "no_warnings": True,
                "extract_flat": False,
                "skip_download": True,
                "socket_timeout": 12,
                "retries": 2,
                "extractor_retries": 2,
                "cookiefile": config.COOKIES_FILE,
                "nocheckcertificate": True,
            }

            with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                info = ydl.extract_info(url, download=False)

            if not info:
                set_inline_item(url, query_id=query_id, user_id=user_id, status="error")
                return

            if info.get("_type") == "playlist":
                set_inline_item(
                    url,
                    query_id=query_id,
                    user_id=user_id,
                    status="playlist",
                    info=info,
                )
                return

            if build_duration_limit_error(user_id, info.get("duration")):
                set_inline_item(
                    url,
                    query_id=query_id,
                    user_id=user_id,
                    status="too_long",
                    info=info,
                )
                return

            inline_dir = os.path.join(
                config.TEMP_DIR,
                f"inline_{user_id}_{int(time.time())}_{uuid4().hex[:8]}",
            )
            os.makedirs(inline_dir, exist_ok=True)
            output_path = os.path.join(inline_dir, "media")

            ydl_params = {
                "format": (
                    "bestvideo[height<=720][ext=mp4]+bestaudio[ext=m4a]/"
                    "best[height<=720][ext=mp4]/best"
                ),
                "outtmpl": f"{output_path}.%(ext)s",
                "quiet": True,
                "no_warnings": True,
                "noplaylist": True,
                "merge_output_format": "mp4",
                "http_chunk_size": 10485760,
                "cookiefile": config.COOKIES_FILE,
                "socket_timeout": 20,
                "retries": 2,
                "fragment_retries": 2,
                "nocheckcertificate": True,
            }

            with yt_dlp.YoutubeDL(ydl_params) as ydl:
                ydl.download([url])

            downloaded_files = [
                path
                for path in Path(inline_dir).iterdir()
                if path.is_file() and path.suffix.lower() not in {".part", ".tmp", ".ytdl"}
            ]
            if not downloaded_files:
                set_inline_item(
                    url,
                    query_id=query_id,
                    user_id=user_id,
                    status="error",
                    info=info,
                    error="no_downloaded_file",
                )
                return

            file_path = str(max(downloaded_files, key=lambda path: path.stat().st_mtime))
            file_size = os.path.getsize(file_path)
            if build_file_size_limit_error(user_id, file_size):
                set_inline_item(
                    url,
                    query_id=query_id,
                    user_id=user_id,
                    status="too_large",
                    info=info,
                )
                return

            cache_chat_id = cache_chat_id_for(user_id)
            title = info.get("title", "video")
            caption = MessageTemplate.format_inline_caption(title, url)
            logger.debug(
                "Inline background upload: user_id=%s cache_chat_id=%s size=%.1fMB path=%s",
                user_id,
                cache_chat_id,
                file_size / (1024 * 1024),
                file_path,
            )
            message = sync_bot.send_video(
                cache_chat_id,
                file_path,
                caption=caption,
                parse_mode="HTML",
                supports_streaming=True,
                timeout=120,
            )
            if not message.video:
                raise RuntimeError("Telegram did not return video metadata")

            try:
                sync_bot.delete_message(cache_chat_id, message.message_id)
            except Exception:
                logger.debug("Не удалось удалить временное inline-сообщение")

            set_inline_item(
                url,
                query_id=query_id,
                user_id=user_id,
                status="ready",
                info=info,
                file_id=message.video.file_id,
            )
            logger.info("Inline cache ready: user_id=%s url=%s", user_id, url)
        except Exception as exc:
            logger.exception("Inline background job failed: %s", exc)
            status = "network_error" if _is_inline_network_error(exc) else "upload_error"
            if not _is_inline_upload_error(exc) and status != "network_error":
                status = "error"
            set_inline_item(
                url,
                query_id=query_id,
                user_id=user_id,
                status=status,
                error=str(exc),
            )
        finally:
            cleanup_inline_dir(inline_dir)

    def schedule_inline_job(url: str, user_id: int, query_id: str) -> None:
        item = get_inline_item(url)
        if item and item.status in {"pending", "downloading", "ready"}:
            return

        set_inline_item(url, query_id=query_id, user_id=user_id, status="pending")
        task = asyncio.create_task(asyncio.to_thread(prepare_inline_video, url, user_id, query_id))
        inline_jobs.add(task)

        def on_done(done_task: asyncio.Task) -> None:
            inline_jobs.discard(done_task)
            try:
                done_task.result()
            except Exception as exc:
                logger.exception("Inline worker task crashed: %s", exc)

        task.add_done_callback(on_done)

    async def wait_for_inline_resolution(url: str) -> InlineQueryCacheItem | None:
        deadline = time.monotonic() + config.INLINE_READY_WAIT_TIMEOUT
        terminal_statuses = {
            "ready",
            "error",
            "network_error",
            "upload_error",
            "too_long",
            "too_large",
            "playlist",
            "tiktok_photo",
        }
        item = get_inline_item(url)
        while item and item.status not in terminal_statuses and time.monotonic() < deadline:
            await asyncio.sleep(0.2)
            item = get_inline_item(url)
        return item

    async def inline_query_handler(inline_query: InlineQuery, bot: Bot):
        query_text = (inline_query.query or "").strip()
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
            if not query_text:
                result = inline_article(
                    status="help",
                    title="ReSave - скачать видео",
                    description="Введите ссылку после @ReSafeBot",
                    message_text=ui_manager.format_panel(
                        "ReSave inline",
                        [
                            "Введите ссылку после @ReSafeBot.",
                            "",
                            "Если видео уже готово, я покажу его сразу.",
                            "Если нет, начну подготовку и попрошу повторить запрос.",
                        ],
                        icon="⚡",
                    ),
                )
                await answer_inline_query(
                    results=[result],
                    cache_time=1,
                    is_personal=True,
                )
                return

            if not query_text.startswith(("http://", "https://")):
                await answer_inline_query(
                    results=[],
                    cache_time=1,
                    is_personal=True,
                )
                return

            from ..utils.url_validator import get_url_validator

            url_validator = get_url_validator()
            is_valid, corrected_url, _ = url_validator.validate(query_text)
            if not is_valid:
                await answer_inline_query(
                    results=[],
                    cache_time=1,
                    is_personal=True,
                )
                return
            query_text = corrected_url or query_text

            if not config.INLINE_DOWNLOAD_ENABLED:
                result = inline_article(
                    status="open_bot",
                    title="Откройте ReSave",
                    description="Большие файлы скачиваются напрямую в боте",
                    message_text=(
                        "📦 Большие файлы лучше скачивать напрямую в боте.\n\n"
                        "Откройте @ReSafeBot и отправьте ссылку:\n"
                        f"{query_text}"
                    ),
                )
                await answer_inline_query(
                    results=[result],
                    cache_time=1,
                    is_personal=True,
                    button=open_bot_button(),
                )
                return

            item = get_inline_item(query_text)
            if item and item.status == "ready" and item.file_id:
                await answer_inline_query(
                    results=[cached_video_result(item, query_text)],
                    cache_time=300,
                    is_personal=True,
                )
                return

            if config.INLINE_DIRECT_RESULTS_ENABLED:
                try:
                    direct_result = await asyncio.wait_for(
                        asyncio.to_thread(
                            build_direct_inline_video_result,
                            query_text,
                            user_id,
                        ),
                        timeout=config.INLINE_EXTRACT_TIMEOUT,
                    )
                except asyncio.TimeoutError:
                    logger.warning("Inline direct extraction timed out: user_id=%s url=%s", user_id, query_text)
                    direct_result = None
                except Exception as exc:
                    logger.warning("Inline direct extraction failed: %s", exc)
                    direct_result = None

                if direct_result:
                    try:
                        await answer_inline_query(
                            results=[direct_result],
                            cache_time=60,
                            is_personal=True,
                        )
                        return
                    except TelegramBadRequest as exc:
                        if _is_expired_inline_query_error(exc):
                            logger.warning("Inline query expired before direct answer: %s", exc)
                            return
                        logger.warning("Telegram rejected direct inline video: %s", exc)

            if not config.INLINE_BACKGROUND_CACHE_ENABLED:
                result = inline_article(
                    status="open_bot",
                    title="Откройте ReSave",
                    description="Не удалось подготовить прямое inline-видео",
                    message_text=(
                        "Не удалось подготовить inline-видео без скачивания на сервер.\n\n"
                        "Откройте @ReSafeBot и отправьте ссылку напрямую:\n"
                        f"{query_text}"
                    ),
                )
                await answer_inline_query(
                    results=[result],
                    cache_time=1,
                    is_personal=True,
                    button=open_bot_button(),
                )
                return

            if not item:
                schedule_inline_job(query_text, user_id, inline_query.id)
                item = get_inline_item(query_text)

            if item and item.status in {"pending", "downloading"}:
                item = await wait_for_inline_resolution(query_text)
                if item and item.status == "ready" and item.file_id:
                    await answer_inline_query(
                        results=[cached_video_result(item, query_text)],
                        cache_time=300,
                        is_personal=True,
                    )
                    return

            status = item.status if item else "pending"
            info = item.info if item else None

            if status in {"pending", "downloading"}:
                result = inline_article(
                    status="preparing",
                    title="Готовлю видео",
                    description="Повторите inline-запрос через несколько секунд",
                    message_text=(
                        "Видео готовится для inline-отправки.\n\n"
                        "Повторите этот же inline-запрос через несколько секунд:\n"
                        f"{query_text}"
                    ),
                )
                await answer_inline_query(
                    results=[result],
                    cache_time=1,
                    is_personal=True,
                    button=open_bot_button(),
                )
                return

            if status == "network_error":
                result = inline_article(
                    status="network",
                    title="Не удалось подключиться",
                    description="Источник не ответил вовремя",
                    message_text=(
                        "Не удалось загрузить видео из-за сетевого таймаута.\n\n"
                        "Попробуйте повторить inline-запрос или отправьте ссылку в @ReSafeBot:\n"
                        f"{query_text}"
                    ),
                )
                await answer_inline_query(
                    results=[result],
                    cache_time=1,
                    is_personal=True,
                    button=open_bot_button(),
                )
                return

            if status == "upload_error":
                title = info.get("title", "Видео") if info else "Видео"
                result = inline_article(
                    status="upload_error",
                    title="Inline-видео не подготовлено",
                    description="Отправьте ссылку боту напрямую",
                    message_text=(
                        f'Не удалось подготовить inline-видео "{title}".\n\n'
                        "Откройте @ReSafeBot и отправьте ссылку напрямую:\n"
                        f"{query_text}"
                    ),
                )
                await answer_inline_query(
                    results=[result],
                    cache_time=1,
                    is_personal=True,
                    button=open_bot_button(),
                )
                return

            if status == "too_long":
                limit_text = build_duration_limit_error(
                    user_id,
                    info.get("duration") if info else None,
                ) or "Видео превышает допустимую длительность."
                result = inline_article(
                    status="limit",
                    title="Видео слишком длинное",
                    description="Откройте бота, чтобы скачать с подсказками",
                    message_text=limit_text,
                )
                await answer_inline_query(
                    results=[result],
                    cache_time=1,
                    is_personal=True,
                    button=open_bot_button(),
                )
                return

            if status == "playlist":
                playlist_text = build_playlist_limit_error(
                    user_id,
                    len(collect_playlist_entries(info or {})),
                ) or "Плейлист лучше отправить напрямую в @ReSafeBot."
                result = inline_article(
                    status="playlist",
                    title="Плейлист откройте в боте",
                    description="Inline-режим рассчитан на одиночные ролики",
                    message_text=playlist_text,
                )
                await answer_inline_query(
                    results=[result],
                    cache_time=1,
                    is_personal=True,
                    button=open_bot_button(),
                )
                return

            if status == "too_large":
                limit_text = (
                    build_file_size_limit_error(user_id, config.BOT_API_UPLOAD_LIMIT + 1)
                    or "Видео превышает допустимый размер."
                )
                result = inline_article(
                    status="size_limit",
                    title="Файл слишком большой",
                    description="Откройте бота, чтобы скачать с подсказками",
                    message_text=limit_text,
                )
                await answer_inline_query(
                    results=[result],
                    cache_time=1,
                    is_personal=True,
                    button=open_bot_button(),
                )
                return

            if status == "tiktok_photo":
                result = inline_article(
                    status="tiktok_photo",
                    title="TikTok фото откройте в боте",
                    description="Inline-режим не отправляет фото-посты",
                    message_text=(
                        "TikTok фото-пост лучше скачать напрямую в @ReSafeBot.\n\n"
                        f"Отправьте эту ссылку боту:\n{query_text}"
                    ),
                )
                await answer_inline_query(
                    results=[result],
                    cache_time=1,
                    is_personal=True,
                    button=open_bot_button(),
                )
                return

            result = inline_article(
                status="error",
                title="Не удалось загрузить",
                description="Проверьте ссылку или откройте бота",
                message_text=(
                    "Не удалось загрузить видео.\n\n"
                    f"Попробуйте отправить ссылку в @ReSafeBot:\n{query_text}"
                ),
            )
            await answer_inline_query(
                results=[result],
                cache_time=1,
                is_personal=True,
                button=open_bot_button(),
            )
        except Exception as exc:
            logger.exception("Critical inline error: %s", exc)
            await answer_inline_query(
                results=[],
                cache_time=1,
                is_personal=True,
            )

    async def handle_url(message: Message):
        from ..utils.url_validator import get_url_validator

        if not message.text:
            return

        text = message.text.strip()
        if text.startswith("/"):
            return

        url_validator = get_url_validator()
        extracted_url = url_validator.extract_url(text)
        is_valid, corrected_url, metadata = url_validator.validate(extracted_url or "")
        if not is_valid:
            if message.chat.type == "private":
                suggestions = url_validator.suggest_fixes(extracted_url or text)
                if suggestions:
                    suggestion_lines = ["Возможно, подойдет один из вариантов:", ""]
                    for suggestion in suggestions:
                        suggestion_lines.append(f"• {suggestion['reason']}")
                        suggestion_lines.append(suggestion["url"])
                        suggestion_lines.append("")
                    await message.reply(
                        ui_manager.format_panel(
                            "Ссылка не распознана",
                            suggestion_lines,
                            icon="🔗",
                        )
                    )
                else:
                    await message.reply(
                        ui_manager.format_panel(
                            "Ссылка не распознана",
                            [
                                "Пришлите полный URL, который начинается с http:// или https://.",
                                "Проверьте, что в ссылке нет лишних пробелов.",
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
        action_type = action

        if "tiktok.com" in url and "/photo/" in url:
            action_type = "tiktok_photo"

        try:
            _download_manager.add_task(
                url=url,
                chat_id=call.message.chat.id,
                message_id=call.message.message_id,
                info=download_info["info"],
                action=action_type,
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
    router.message.register(
        handle_url,
        lambda message: bool(message.text and not message.text.strip().startswith("/")),
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
