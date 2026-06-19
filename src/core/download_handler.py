import logging
import os
import shutil

import yt_dlp

import config
from ..utils.admin_notifier import notify_admins
from ..utils.file_utils import sanitize_filename
from .access import build_file_size_limit_error
from .download_support import (
    ProgressHook,
    describe_work_dir,
    ensure_task_work_dir,
    find_completed_files,
    record_failed_download,
)
from .file_sender import send_file_with_retry
from .media_assets import (
    convert_to_gif_and_send,
    download_and_send_subtitles,
    download_and_send_thumbnail,
    download_and_send_tiktok_photos,
)

logger = logging.getLogger(__name__)


def _ffmpeg_location() -> str | None:
    ffmpeg_path = shutil.which("ffmpeg")
    if not ffmpeg_path:
        return None
    return str(os.path.dirname(ffmpeg_path))


def handle_download_task(task, bot, temp_dir):
    from ..utils.error_handler import get_error_handler
    from ..utils.retry_manager import DOWNLOAD_RETRY_CONFIG, get_smart_retry_manager

    error_handler = get_error_handler()

    try:
        if task.action == "subtitles":
            download_and_send_subtitles(task, bot, temp_dir)
            return

        if task.action == "thumbnail":
            download_and_send_thumbnail(task, bot, temp_dir)
            return

        if task.action == "tiktok_photo":
            download_and_send_tiktok_photos(task, bot, temp_dir)
            return

        retry_manager = get_smart_retry_manager(DOWNLOAD_RETRY_CONFIG)

        def download_with_retry():
            _download_and_send_video(task, bot, temp_dir)

        def on_retry(attempt, delay):
            if not task.silent_mode:
                try:
                    bot.edit_message_text(
                        f"⚠️ Ошибка загрузки, повторная попытка {attempt}...\n"
                        f"⏱️ Ожидание {delay:.0f} сек",
                        task.chat_id,
                        task.message_id,
                    )
                except Exception:
                    pass

        def on_failure(attempt, exception):
            if not task.silent_mode and exception:
                error_msg = error_handler.handle_error(exception)
                try:
                    bot.edit_message_text(error_msg.user_message, task.chat_id, task.message_id)
                except Exception:
                    pass
            task.error = str(exception)

        retry_manager.retry_operation_smart(
            download_with_retry,
            operation_id=f"download_{task.task_id}",
            on_retry=on_retry,
            on_failure=on_failure,
        )
    except Exception as exc:
        if not task.cancel_event.is_set():
            record_failed_download(task.chat_id)
            logger.exception("Ошибка при скачивании и отправке файла: %s", exc)

            error_text = str(exc)
            if "ffmpeg" in error_text.lower():
                notify_admins(
                    bot,
                    (
                        "⚠️ [ReSave] FFmpeg issue detected\n"
                        f"Chat ID: {task.chat_id}\n"
                        f"Action: {task.action}\n"
                        f"URL: {task.url}\n"
                        f"Error: {error_text}"
                    ),
                )

            from ..utils.message_templates import ErrorMessages

            if "ffmpeg" in error_text.lower():
                if task.action == "gif":
                    user_message = ErrorMessages.GIF_FFMPEG_MISSING
                else:
                    user_message = "⚠️ FFmpeg не установлен на сервере. Эта функция временно недоступна."
            else:
                user_message = ErrorMessages.format_error_with_suggestion(error_text)

            if not task.is_inline and not task.silent_mode:
                try:
                    bot.edit_message_text(user_message, task.chat_id, task.message_id)
                except Exception:
                    pass

            task.error = str(exc)
            raise
    finally:
        if task.file_path and os.path.exists(task.file_path):
            try:
                os.remove(task.file_path)
            except Exception:
                pass
        if task.work_dir and os.path.exists(task.work_dir):
            try:
                shutil.rmtree(task.work_dir, ignore_errors=True)
            except Exception as cleanup_error:
                logger.debug(
                    "Не удалось очистить рабочую директорию %s: %s",
                    task.work_dir,
                    cleanup_error,
                )


def _download_and_send_video(task, bot, temp_dir):
    title = sanitize_filename(task.info.get("title") or task.info.get("id") or "video")
    work_dir = ensure_task_work_dir(task, temp_dir)
    output_path = str(work_dir / "media")

    fmt, post, output_template = _get_format_options(task, output_path)
    logger.info(
        "Download format selected: task_id=%s action=%s format_param=%s format=%s",
        task.task_id,
        task.action,
        task.format_param,
        fmt,
    )
    if task.action in {"audio", "gif"} and not shutil.which("ffmpeg"):
        raise RuntimeError("FFmpeg is not installed on server")

    if not task.is_inline and not task.silent_mode:
        bot.edit_message_text("⏬ Скачиваю видео...", task.chat_id, task.message_id)

    ydl_params = {
        "format": fmt,
        "outtmpl": output_template,
        "quiet": True,
        "no_warnings": True,
        "noplaylist": True,
        "merge_output_format": "mp4",
        "http_chunk_size": 10485760,
        "cookiefile": config.COOKIES_FILE,
        "nocheckcertificate": True,
        "http_headers": {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
            "Accept-Language": "ru-RU,ru;q=0.9,en-US;q=0.8",
        },
        "progress_hooks": [ProgressHook(task)],
    }

    if post:
        ydl_params["postprocessors"] = post
    ffmpeg_location = _ffmpeg_location()
    if ffmpeg_location:
        ydl_params["ffmpeg_location"] = ffmpeg_location

    with yt_dlp.YoutubeDL(ydl_params) as ydl:
        ydl.download([task.url])

    downloaded_files = find_completed_files(work_dir)
    if not downloaded_files:
        logger.error(
            "Файл не найден после скачивания. task_id=%s, action=%s, work_dir=%s, contents=%s",
            task.task_id,
            task.action,
            work_dir,
            describe_work_dir(work_dir),
        )
        raise FileNotFoundError("Файл не найден после скачивания")

    file_path = max(downloaded_files, key=lambda path: path.stat().st_mtime)
    task.file_path = str(file_path)
    file_size_bytes = os.path.getsize(file_path)
    file_size_error = build_file_size_limit_error(task.chat_id, file_size_bytes)
    if file_size_error:
        raise RuntimeError(file_size_error)

    if task.action == "gif":
        convert_to_gif_and_send(task, file_path, bot)
        return

    if not task.silent_mode:
        size_mb = file_size_bytes / (1024 * 1024)
        bot.edit_message_text(
            f"📤 Готово! Отправляю файл ({size_mb:.1f}MB)...",
            task.chat_id,
            task.message_id,
        )

    send_file_with_retry(task, file_path, title, bot)

    if not task.silent_mode:
        try:
            bot.delete_message(task.chat_id, task.message_id)
        except Exception:
            logger.warning("Не удалось удалить сообщение о статусе %s", task.message_id)


def _get_format_options(task, output_path):
    if task.action == "best":
        return (
            "bv*+ba/best",
            [],
            f"{output_path}.%(ext)s",
        )

    if task.action == "medium":
        return (
            "bv*[height<=720]+ba/best[height<=720]/best",
            [],
            f"{output_path}.%(ext)s",
        )

    if task.action == "low":
        return (
            "bv*[height<=480]+ba/best[height<=480]/best",
            [],
            f"{output_path}.%(ext)s",
        )

    if task.action == "audio":
        return (
            "bestaudio/best",
            [{"key": "FFmpegExtractAudio", "preferredcodec": "mp3", "preferredquality": "192"}],
            f"{output_path}.mp3",
        )

    if task.action == "gif":
        return (
            "bv*[height<=480]+ba/best[height<=480]/best",
            [],
            f"{output_path}.%(ext)s",
        )

    if task.action == "res" and task.format_param:
        height = int(task.format_param)
        return (
            f"bv*[height={height}]+ba/best[height={height}]/bv*[height<={height}]+ba/best[height<={height}]/best",
            [],
            f"{output_path}.%(ext)s",
        )

    return ("best[ext=mp4]/best", [], f"{output_path}.%(ext)s")
