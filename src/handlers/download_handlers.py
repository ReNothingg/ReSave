import asyncio
import logging
import os
import threading
import time
from pathlib import Path
from uuid import uuid4

import config
from aiogram import Bot, Router
from aiogram.types import (
    CallbackQuery,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    InlineQuery,
    InlineQueryResultArticle,
    InlineQueryResultCachedVideo,
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

logger = logging.getLogger(__name__)


_download_manager = None


def set_download_manager(manager):
    global _download_manager
    _download_manager = manager


def get_download_manager():
    return _download_manager


def _build_download_limit_text(chat_id: int) -> str:
    active_downloads = _download_manager.get_user_task_count(chat_id) if _download_manager else 0
    return (
        f"{ErrorMessages.CONCURRENT_LIMIT}\n\n"
        f"Активных загрузок: {active_downloads}/{config.MAX_DOWNLOADS_PER_USER}"
    )


def _build_download_markup(message_id: int, info: dict, resolutions: dict[int, dict]) -> InlineKeyboardMarkup:
    rows: list[list[InlineKeyboardButton]] = [
        [
            InlineKeyboardButton(
                text="🎬 Лучшее качество (авто)",
                callback_data=f"dl_best_{message_id}",
            )
        ],
        [
            InlineKeyboardButton(
                text="📹 Среднее качество (720p)",
                callback_data=f"dl_medium_{message_id}",
            )
        ],
        [
            InlineKeyboardButton(
                text="📱 Низкое качество (480p)",
                callback_data=f"dl_low_{message_id}",
            )
        ],
        [
            InlineKeyboardButton(
                text="🎵 Только аудио (MP3)",
                callback_data=f"dl_audio_{message_id}",
            )
        ],
    ]

    duration = info.get("duration")
    if duration and duration <= 30:
        rows.append(
            [
                InlineKeyboardButton(
                    text="✨ Создать GIF",
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
                    text="📝 Скачать субтитры (.srt)",
                    callback_data=f"dl_subtitles_{message_id}",
                )
            ]
        )

    if info.get("thumbnail"):
        rows.append(
            [
                InlineKeyboardButton(
                    text="🖼️ Скачать превью",
                    callback_data=f"dl_thumbnail_{message_id}",
                )
            ]
        )

    resolution_buttons: list[InlineKeyboardButton] = []
    for height in sorted(resolutions.keys(), reverse=True)[:3]:
        fmt_info = resolutions[height]
        size_bytes = fmt_info.get("filesize") or 0
        size_suffix = f" (~{size_bytes / (1024 * 1024):.1f}MB)" if size_bytes else ""
        resolution_buttons.append(
            InlineKeyboardButton(
                text=f"🎥 {height}p{size_suffix}",
                callback_data=f"dl_res_{height}_{message_id}",
            )
        )

    if resolution_buttons:
        rows.append(resolution_buttons)

    rows.append([InlineKeyboardButton(text="❌ Отмена", callback_data=f"cancel_{message_id}")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def register_download_handlers(router: Router, sync_bot):
    video_info_cache: dict[int, dict] = {}
    inline_queries: dict[str, InlineQueryCacheItem] = {}
    inline_cache_lock = threading.Lock()

    async def inline_query_handler(inline_query: InlineQuery, bot: Bot):
        query_text = (inline_query.query or "").strip()
        user_id = inline_query.from_user.id

        try:
            if not query_text:
                result = InlineQueryResultArticle(
                    id="help",
                    title="ReSave - скачать видео",
                    description="Введите ссылку после @ReSafeBot",
                    input_message_content=InputTextMessageContent(
                        message_text=(
                            "ReSave поможет скачать видео быстро и просто.\n\n"
                            "1. Введите @ReSafeBot\n"
                            "2. Вставьте ссылку на видео\n"
                            "3. Подождите загрузки\n"
                            "4. Отправьте результат в чат"
                        )
                    ),
                    thumbnail_url="https://raw.githubusercontent.com/ReNothingg/ReNothingg/refs/heads/main/main.jpg",
                )
                await bot.answer_inline_query(
                    inline_query_id=inline_query.id,
                    results=[result],
                    cache_time=1,
                    is_personal=True,
                )
                return

            if not query_text.startswith(("http://", "https://")):
                await bot.answer_inline_query(
                    inline_query_id=inline_query.id,
                    results=[],
                    cache_time=1,
                    is_personal=True,
                )
                return

            for cached_item in inline_queries.values():
                if cached_item.url == query_text and cached_item.status == "ready" and cached_item.file_id:
                    title = cached_item.info.get("title", "Видео")
                    result = InlineQueryResultCachedVideo(
                        id=f"cached_{uuid4().hex[:8]}",
                        video_file_id=cached_item.file_id,
                        title=title,
                        description="Готово к отправке из кэша",
                        caption=f"{title}\n\n@ReSafeBot",
                    )
                    await bot.answer_inline_query(
                        inline_query_id=inline_query.id,
                        results=[result],
                        cache_time=300,
                        is_personal=True,
                    )
                    return

            def quick_download():
                import yt_dlp

                try:
                    ydl_opts = {
                        "quiet": True,
                        "no_warnings": True,
                        "extract_flat": False,
                        "skip_download": True,
                        "socket_timeout": 5,
                        "cookiefile": config.COOKIES_FILE,
                        "nocheckcertificate": True,
                    }

                    with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                        info = ydl.extract_info(query_text, download=False)

                    if not info:
                        return None, None

                    if info.get("_type") == "playlist":
                        return "playlist", info

                    original_title = info.get("title", "video")
                    duration_error = build_duration_limit_error(user_id, info.get("duration"))
                    if duration_error:
                        return "too_long", info

                    timestamp = int(time.time())
                    inline_dir = os.path.join(
                        config.TEMP_DIR,
                        f"inline_{user_id}_{timestamp}",
                    )
                    os.makedirs(inline_dir, exist_ok=True)
                    output_path = os.path.join(inline_dir, "media")
                    file_path = None

                    ydl_params = {
                        "format": "bestvideo[height<=720][ext=mp4]+bestaudio[ext=m4a]/best[height<=720][ext=mp4]/best",
                        "outtmpl": f"{output_path}.%(ext)s",
                        "quiet": True,
                        "no_warnings": True,
                        "noplaylist": True,
                        "merge_output_format": "mp4",
                        "http_chunk_size": 10485760,
                        "cookiefile": config.COOKIES_FILE,
                        "socket_timeout": 10,
                        "retries": 1,
                        "fragment_retries": 1,
                        "nocheckcertificate": True,
                    }

                    with yt_dlp.YoutubeDL(ydl_params) as ydl:
                        ydl.download([query_text])

                    downloaded_files = [
                        path
                        for path in Path(inline_dir).iterdir()
                        if path.is_file() and path.suffix.lower() not in {".part", ".tmp", ".ytdl"}
                    ]
                    if not downloaded_files:
                        return None, None

                    file_path = str(downloaded_files[0])
                    if not os.path.exists(file_path):
                        return None, None

                    file_size = os.path.getsize(file_path)
                    if build_file_size_limit_error(user_id, file_size):
                        os.remove(file_path)
                        return "too_large", info

                    try:
                        caption = MessageTemplate.format_inline_caption(original_title, query_text)
                        message = sync_bot.send_video(
                            user_id,
                            file_path,
                            caption=caption,
                            parse_mode="HTML",
                            supports_streaming=True,
                            timeout=60,
                        )
                        file_id = message.video.file_id

                        try:
                            sync_bot.delete_message(user_id, message.message_id)
                        except Exception:
                            logger.debug("Не удалось удалить временное inline-сообщение")

                        os.remove(file_path)
                        try:
                            os.rmdir(inline_dir)
                        except OSError:
                            pass

                        result_id = f"video_{uuid4().hex[:8]}"
                        with inline_cache_lock:
                            inline_queries[result_id] = InlineQueryCacheItem(
                                query_id=inline_query.id,
                                url=query_text,
                                user_id=user_id,
                                result_id=result_id,
                                info=info,
                                file_id=file_id,
                                timestamp=time.time(),
                                status="ready",
                            )

                        return file_id, info
                    except Exception as exc:
                        logger.error("Ошибка при inline-загрузке: %s", exc)
                        if file_path and os.path.exists(file_path):
                            os.remove(file_path)
                        try:
                            if os.path.isdir(inline_dir):
                                for leftover in Path(inline_dir).iterdir():
                                    if leftover.is_file():
                                        leftover.unlink(missing_ok=True)
                                os.rmdir(inline_dir)
                        except Exception:
                            logger.debug("Не удалось очистить inline-директорию %s", inline_dir)
                        return None, None

                except Exception as exc:
                    logger.error("Ошибка quick_download: %s", exc)
                    try:
                        if "inline_dir" in locals() and os.path.isdir(inline_dir):
                            for leftover in Path(inline_dir).iterdir():
                                if leftover.is_file():
                                    leftover.unlink(missing_ok=True)
                            os.rmdir(inline_dir)
                    except Exception:
                        logger.debug("Не удалось очистить inline-директорию %s", locals().get("inline_dir"))
                    return None, None

            try:
                file_id, info = await asyncio.wait_for(
                    asyncio.to_thread(quick_download),
                    timeout=15,
                )
            except asyncio.TimeoutError:
                file_id, info = "timeout", None

            if file_id and file_id not in {"timeout", "too_long", "too_large", "playlist"}:
                title = info.get("title", "Видео")
                caption = MessageTemplate.format_inline_caption(title, query_text)
                result = InlineQueryResultCachedVideo(
                    id=f"video_{uuid4().hex[:8]}",
                    video_file_id=file_id,
                    title=title,
                    description="720p Ready to send",
                    caption=caption,
                    parse_mode="HTML",
                )
                await bot.answer_inline_query(
                    inline_query_id=inline_query.id,
                    results=[result],
                    cache_time=300,
                    is_personal=True,
                )
                return

            open_bot_button = InlineQueryResultsButton(
                text="Open ReSave",
                start_parameter="start",
            )

            if file_id == "timeout":
                title = info.get("title", "Видео") if info else "Видео"
                result = InlineQueryResultArticle(
                    id=f"pending_{uuid4().hex[:8]}",
                    title="Loading...",
                    description=f"{title} - click to open bot",
                    input_message_content=InputTextMessageContent(
                        message_text=(
                            f'Видео "{title}" еще загружается.\n\n'
                            f"Откройте @ReSafeBot и отправьте ссылку:\n{query_text}"
                        )
                    ),
                    thumbnail_url="https://raw.githubusercontent.com/ReNothingg/ReNothingg/refs/heads/main/main.jpg",
                )
                await bot.answer_inline_query(
                    inline_query_id=inline_query.id,
                    results=[result],
                    cache_time=1,
                    is_personal=True,
                    button=open_bot_button,
                )
                return

            if file_id == "too_long":
                limit_text = build_duration_limit_error(
                    user_id,
                    info.get("duration") if info else None,
                ) or "Видео превышает допустимую длительность."
                result = InlineQueryResultArticle(
                    id=f"limit_{uuid4().hex[:8]}",
                    title="Видео слишком длинное",
                    description="Откройте бота, чтобы скачать с подсказками",
                    input_message_content=InputTextMessageContent(
                        message_text=limit_text
                    ),
                    thumbnail_url="https://raw.githubusercontent.com/ReNothingg/ReNothingg/refs/heads/main/main.jpg",
                )
                await bot.answer_inline_query(
                    inline_query_id=inline_query.id,
                    results=[result],
                    cache_time=1,
                    is_personal=True,
                    button=open_bot_button,
                )
                return

            if file_id == "playlist":
                playlist_text = build_playlist_limit_error(
                    user_id,
                    len(collect_playlist_entries(info or {})),
                ) or "Плейлист лучше отправить напрямую в @ReSafeBot."
                result = InlineQueryResultArticle(
                    id=f"playlist_{uuid4().hex[:8]}",
                    title="Плейлист откройте в боте",
                    description="Inline-режим рассчитан на одиночные ролики",
                    input_message_content=InputTextMessageContent(
                        message_text=playlist_text
                    ),
                    thumbnail_url="https://raw.githubusercontent.com/ReNothingg/ReNothingg/refs/heads/main/main.jpg",
                )
                await bot.answer_inline_query(
                    inline_query_id=inline_query.id,
                    results=[result],
                    cache_time=1,
                    is_personal=True,
                    button=open_bot_button,
                )
                return

            if file_id == "too_large":
                limit_text = (
                    build_file_size_limit_error(user_id, config.BOT_API_UPLOAD_LIMIT + 1)
                    or "Видео превышает допустимый размер."
                )
                result = InlineQueryResultArticle(
                    id=f"size_limit_{uuid4().hex[:8]}",
                    title="Файл слишком большой",
                    description="Откройте бота, чтобы скачать с подсказками",
                    input_message_content=InputTextMessageContent(
                        message_text=limit_text
                    ),
                    thumbnail_url="https://raw.githubusercontent.com/ReNothingg/ReNothingg/refs/heads/main/main.jpg",
                )
                await bot.answer_inline_query(
                    inline_query_id=inline_query.id,
                    results=[result],
                    cache_time=1,
                    is_personal=True,
                    button=open_bot_button,
                )
                return

            result = InlineQueryResultArticle(
                id=f"error_{uuid4().hex[:8]}",
                title="Error loading",
                description="Check link or use bot directly",
                input_message_content=InputTextMessageContent(
                    message_text=(
                        "Не удалось загрузить видео.\n\n"
                        f"Попробуйте отправить ссылку в @ReSafeBot:\n{query_text}"
                    )
                ),
                thumbnail_url="https://raw.githubusercontent.com/ReNothingg/ReNothingg/refs/heads/main/main.jpg",
            )
            await bot.answer_inline_query(
                inline_query_id=inline_query.id,
                results=[result],
                cache_time=1,
                is_personal=True,
                button=open_bot_button,
            )
        except Exception as exc:
            logger.exception("Critical inline error: %s", exc)
            await bot.answer_inline_query(
                inline_query_id=inline_query.id,
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
        is_valid, corrected_url, metadata = url_validator.validate(text)
        if not is_valid:
            if message.chat.type == "private":
                suggestions = url_validator.suggest_fixes(text)
                if suggestions:
                    suggestion_lines = ["Может быть, вы имели в виду?", ""]
                    for suggestion in suggestions:
                        suggestion_lines.append(f"• {suggestion['reason']}")
                        suggestion_lines.append(suggestion["url"])
                        suggestion_lines.append("")
                    await message.reply(
                        "❌ Это не похоже на ссылку\n\n" + "\n".join(suggestion_lines).strip()
                    )
                else:
                    await message.reply(
                        "❌ Не удается распознать ссылку\n\n"
                        "Убедитесь, что ссылка полная, начинается с http:// или https:// "
                        "и не содержит лишних пробелов."
                    )
            return

        url = corrected_url or text

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
                "✅ Ссылка исправлена\n\n"
                "🔍 Ищу информацию о видео... Подождите секунду."
            )
        else:
            status_message = await message.reply(
                "🔍 Ищу информацию о видео... Подождите секунду."
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

        await call.answer("✅ Начинаю скачивание.")
        await call.message.edit_text("📥 Добавляю в очередь...")

        format_param = f"best[height<={int(parts[2])}]" if action == "res" else None
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
            f"✅ Отменено {cancelled_count} загрузок. Готов к новым задачам."
        )

    async def handle_cancel(call: CallbackQuery):
        if not call.message:
            return

        await call.answer("Запрос отменен.")
        try:
            await call.message.delete()
        except Exception:
            await call.message.edit_text("❌ Запрос отменен.")

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
