from __future__ import annotations

import logging
import os
from pathlib import Path

import config
from ..utils.file_utils import sanitize_filename
from ..utils.message_templates import MessageTemplate
from .download_support import record_download_success

logger = logging.getLogger(__name__)


def send_file_with_retry(task, file_path, title, bot):
    from ..utils.error_handler import get_error_handler
    from ..utils.retry_manager import UPLOAD_RETRY_CONFIG, get_smart_retry_manager

    error_handler = get_error_handler()
    retry_manager = get_smart_retry_manager(UPLOAD_RETRY_CONFIG)

    file_extension = Path(file_path).suffix.lower()
    audio_extensions = [".mp3", ".m4a", ".aac", ".ogg", ".wav", ".flac"]
    original_title = task.info.get("title") or task.info.get("id") or title or "video"
    file_size_bytes = os.path.getsize(file_path)
    file_size_mb = file_size_bytes / (1024 * 1024)

    def send_operation():
        duration = task.info.get("duration")
        safe_duration = int(duration) if isinstance(duration, (int, float)) and duration > 0 else None

        with open(file_path, "rb") as file_obj:
            if task.action == "audio" and file_extension in audio_extensions:
                audio_kwargs = {}
                if safe_duration is not None:
                    audio_kwargs["duration"] = safe_duration

                bot.send_audio(
                    task.chat_id,
                    file_obj,
                    title=original_title,
                    performer=task.info.get("uploader", "Unknown"),
                    timeout=300,
                    reply_to_message_id=task.reply_to_id,
                    **audio_kwargs,
                )
                return

            caption = MessageTemplate.format_caption(
                title=original_title,
                url=task.url,
                action="video",
                file_size=file_size_mb,
            )
            if file_size_bytes > config.SEND_AS_DOC_LIMIT:
                visible_file_name = (
                    f"{sanitize_filename(original_title) or 'video'}"
                    f"{file_extension or '.mp4'}"
                )
                bot.send_document(
                    task.chat_id,
                    file_obj,
                    caption=caption,
                    parse_mode="HTML",
                    timeout=600,
                    reply_to_message_id=task.reply_to_id,
                    visible_file_name=visible_file_name,
                )
                return

            bot.send_video(
                task.chat_id,
                file_obj,
                caption=caption,
                parse_mode="HTML",
                supports_streaming=True,
                timeout=600,
                reply_to_message_id=task.reply_to_id,
            )

    def on_retry(attempt, delay):
        if not task.silent_mode:
            try:
                bot.edit_message_text(
                    f"📤 Отправка файла (попытка {attempt})...\n"
                    f"⏱️ Ожидание {delay:.0f} сек",
                    task.chat_id,
                    task.message_id,
                )
            except Exception:
                pass

    def on_failure(attempt, exception):
        if exception and not task.silent_mode:
            error_msg = error_handler.handle_error(exception)
            try:
                bot.edit_message_text(error_msg.user_message, task.chat_id, task.message_id)
            except Exception:
                pass

    try:
        retry_manager.retry_operation_smart(
            send_operation,
            operation_id=f"upload_{task.task_id}",
            on_retry=on_retry,
            on_failure=on_failure,
        )
        stats_action = "audio" if task.action == "audio" else "video"
        record_download_success(task.chat_id, action=stats_action, file_size_mb=file_size_mb)
    except Exception as exc:
        logger.error("Ошибка при отправке файла после всех попыток: %s", exc)
        raise
