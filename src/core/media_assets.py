from __future__ import annotations

import logging
import os
import shutil
import subprocess
import time
from pathlib import Path

import requests
import yt_dlp
from aiogram.types import BufferedInputFile, InputMediaPhoto
from PIL import Image

import config
from ..utils.file_utils import sanitize_filename
from ..utils.message_templates import MessageTemplate
from .access import build_file_size_limit_error
from .download_support import (
    describe_work_dir,
    ensure_task_work_dir,
    format_file_size,
    record_download_success,
)

logger = logging.getLogger(__name__)


def download_and_send_tiktok_photos(task, bot, temp_dir):
    from .tiktok_photo_handler import download_tiktok_photos

    work_dir = ensure_task_work_dir(task, temp_dir)
    if not task.silent_mode:
        bot.edit_message_text(
            "🖼️ Скачиваю фото из TikTok...",
            task.chat_id,
            task.message_id,
        )

    photo_paths = download_tiktok_photos(task.url, work_dir)
    caption = MessageTemplate.format_tiktok_photo_caption(task.url, len(photo_paths))
    total_size = sum(path.stat().st_size for path in photo_paths)

    if len(photo_paths) == 1:
        bot.send_photo(
            task.chat_id,
            str(photo_paths[0]),
            caption=caption,
            parse_mode="HTML",
            reply_to_message_id=task.reply_to_id,
            timeout=120,
        )
    else:
        # Telegram accepts at most 10 items in one media group.
        for offset in range(0, len(photo_paths), 10):
            chunk = photo_paths[offset:offset + 10]
            if len(chunk) == 1:
                bot.send_photo(
                    task.chat_id,
                    str(chunk[0]),
                    caption=caption if offset == 0 else None,
                    parse_mode="HTML" if offset == 0 else None,
                    reply_to_message_id=task.reply_to_id if offset == 0 else None,
                    timeout=120,
                )
                continue

            media = []
            for index, path in enumerate(chunk):
                media.append(
                    InputMediaPhoto(
                        media=BufferedInputFile(path.read_bytes(), filename=path.name),
                        caption=caption if offset == 0 and index == 0 else None,
                        parse_mode="HTML" if offset == 0 and index == 0 else None,
                    )
                )
            bot.send_media_group(
                task.chat_id,
                media,
                reply_to_message_id=task.reply_to_id if offset == 0 else None,
                timeout=180,
            )

    record_download_success(
        task.chat_id,
        action="tiktok_photo",
        file_size_mb=total_size / (1024 * 1024),
    )
    if not task.silent_mode:
        try:
            bot.delete_message(task.chat_id, task.message_id)
        except Exception:
            pass


def _ffmpeg_location() -> str | None:
    ffmpeg_path = shutil.which("ffmpeg")
    if not ffmpeg_path:
        return None
    return str(Path(ffmpeg_path).parent)


def convert_to_gif_and_send(task, video_path, bot):
    gif_path = Path(video_path).with_suffix(".gif")
    temp_mp4 = None
    try:
        if not shutil.which("ffmpeg"):
            raise RuntimeError("FFmpeg is not installed on server")
        if not task.silent_mode:
            bot.edit_message_text(
                "✨ Создаю GIF-анимацию... Это может занять до минуты. 🧙‍♂️",
                task.chat_id,
                task.message_id,
            )

        video_to_convert = video_path
        if not video_path.endswith(".mp4"):
            logger.info("Конвертирую %s в MP4 перед созданием GIF", Path(video_path).suffix)
            temp_mp4 = Path(video_path).with_suffix(".temp.mp4")

            ffmpeg_cmd = [
                "ffmpeg",
                "-i",
                str(video_path),
                "-y",
                "-c:v",
                "libx264",
                "-c:a",
                "aac",
                str(temp_mp4),
            ]
            process = subprocess.Popen(ffmpeg_cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
            _, stderr = process.communicate(timeout=120)

            if process.returncode != 0:
                logger.error("FFmpeg error при конвертации в MP4: %s", stderr.decode())
                raise Exception("Не удалось подготовить видео для GIF. Ошибка конвертации.")

            video_to_convert = str(temp_mp4)

        ffmpeg_cmd = [
            "ffmpeg",
            "-i",
            str(video_to_convert),
            "-vf",
            "fps=15,scale=480:-1:flags=lanczos,split[s0][s1];[s0]palettegen[p];[s1][p]paletteuse",
            "-y",
            str(gif_path),
        ]
        process = subprocess.Popen(ffmpeg_cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        _, stderr = process.communicate(timeout=120)

        if process.returncode != 0:
            logger.error("FFmpeg error при создании GIF: %s", stderr.decode())
            raise Exception("Не удалось создать GIF. Ошибка конвертации.")

        title = task.info.get("title") or "animation"
        gif_caption = MessageTemplate.format_gif_caption(title, task.url)
        with open(gif_path, "rb") as file_obj:
            bot.send_animation(
                task.chat_id,
                file_obj,
                caption=gif_caption,
                reply_to_message_id=task.reply_to_id,
                parse_mode="HTML",
            )

        record_download_success(
            task.chat_id,
            action="gif",
            file_size_mb=os.path.getsize(gif_path) / (1024 * 1024),
        )

        if not task.silent_mode:
            bot.delete_message(task.chat_id, task.message_id)
    finally:
        if gif_path.exists():
            gif_path.unlink()
        if temp_mp4 and temp_mp4.exists():
            temp_mp4.unlink()


def download_and_send_subtitles(task, bot, temp_dir):
    try:
        if not task.silent_mode:
            bot.edit_message_text("📝 Ищу и скачиваю субтитры...", task.chat_id, task.message_id)

        original_title = task.info.get("title") or "video"
        work_dir = ensure_task_work_dir(task, temp_dir)
        output_template = str(work_dir / "subtitle.%(ext)s")

        subtitles = task.info.get("subtitles", {})
        auto_captions = task.info.get("automatic_captions", {})
        if not subtitles and not auto_captions:
            error_msg = "❌ К сожалению, для этого видео нет доступных субтитров."
            logger.info("Видео %s не имеет субтитров", task.url)
            if not task.silent_mode:
                try:
                    bot.edit_message_text(error_msg, task.chat_id, task.message_id)
                except Exception:
                    pass
            task.error = "Субтитры не найдены"
            return

        ydl_params = {
            "writesubtitles": True,
            "writeautomaticsub": True,
            "subtitleslangs": ["en", "ru"],
            "subtitlesformat": "srt",
            "skip_download": True,
            "outtmpl": output_template,
            "quiet": False,
            "no_warnings": True,
            "http_chunk_size": 10485760,
            "socket_timeout": 30,
            "cookiefile": config.COOKIES_FILE,
        }

        ffmpeg_location = _ffmpeg_location()
        if ffmpeg_location:
            ydl_params["ffmpeg_location"] = ffmpeg_location

        from ..utils.retry_manager import SUBTITLE_RETRY_CONFIG, get_smart_retry_manager

        retry_manager = get_smart_retry_manager(SUBTITLE_RETRY_CONFIG)
        languages_sequence = [["en", "ru"], ["en"], ["ru"]]
        subtitles_success = False

        for attempt_idx, langs in enumerate(languages_sequence, start=1):
            local_params = dict(ydl_params)
            local_params["subtitleslangs"] = langs

            def download_op():
                with yt_dlp.YoutubeDL(local_params) as ydl:
                    ydl.download([task.url])

            def on_retry(attempt, delay):
                if not task.silent_mode:
                    try:
                        bot.edit_message_text(
                            f"📝 Ошибка при скачивании субтитров, попытка {attempt}...",
                            task.chat_id,
                            task.message_id,
                        )
                    except Exception:
                        pass

            def on_failure(attempt, exception):
                logger.warning(
                    "Субтитры: попытка завершилась неудачей после %s попыток: %s",
                    attempt,
                    exception,
                )

            if not task.silent_mode:
                bot.edit_message_text(
                    f"📝 Скачиваю субтитры (языки: {', '.join(langs)})...",
                    task.chat_id,
                    task.message_id,
                )

            try:
                retry_manager.retry_operation_smart(
                    download_op,
                    operation_id=f"subtitles_{task.task_id}_{attempt_idx}",
                    on_retry=on_retry,
                    on_failure=on_failure,
                )
                subtitles_success = True
                break
            except Exception as exc:
                logger.warning("Ошибка при попытке скачать субтитры (langs=%s): %s", langs, exc)
                if "429" in str(exc) or "too many requests" in str(exc).lower():
                    if not task.silent_mode:
                        try:
                            bot.edit_message_text(
                                "📝 Слишком много запросов — пробуем ещё через немного...",
                                task.chat_id,
                                task.message_id,
                            )
                        except Exception:
                            pass
                    time.sleep(10)
                    continue
                break

        if not subtitles_success:
            logger.error("Не удалось скачать субтитры после всех попыток")
            if not task.silent_mode:
                try:
                    bot.edit_message_text(
                        "❌ Не удалось скачать субтитры. Попробуйте позже.",
                        task.chat_id,
                        task.message_id,
                    )
                except Exception:
                    pass
            task.error = "Не удалось скачать субтитры"
            return

        subtitle_files = sorted(work_dir.glob("*.srt"))
        if not subtitle_files:
            error_msg = "❌ Субтитры не были сохранены. Возможно, сервер их удалил."
            logger.error(
                "Субтитры не найдены после скачивания. task_id=%s, work_dir=%s, contents=%s",
                task.task_id,
                work_dir,
                describe_work_dir(work_dir),
            )
            if not task.silent_mode:
                try:
                    bot.edit_message_text(error_msg, task.chat_id, task.message_id)
                except Exception:
                    pass
            task.error = "Субтитры не найдены"
            return

        total_size_mb = sum(path.stat().st_size for path in subtitle_files) / (1024 * 1024)
        sub_caption = MessageTemplate.format_subtitles_caption(original_title, task.url)
        for index, srt_path in enumerate(subtitle_files):
            if index > 0:
                time.sleep(1)

            with open(srt_path, "rb") as file_obj:
                bot.send_document(
                    task.chat_id,
                    file_obj,
                    caption=sub_caption,
                    reply_to_message_id=task.reply_to_id,
                    parse_mode="HTML",
                )
            srt_path.unlink(missing_ok=True)

        record_download_success(task.chat_id, action="subtitles", file_size_mb=total_size_mb)

        if not task.silent_mode:
            bot.delete_message(task.chat_id, task.message_id)
    except Exception as exc:
        logger.error("Ошибка скачивания субтитров: %s", exc)
        if not task.silent_mode:
            from ..utils.message_templates import ErrorMessages

            error_text = ErrorMessages.format_error_with_suggestion(str(exc))
            try:
                bot.edit_message_text(error_text, task.chat_id, task.message_id)
            except Exception:
                pass
        raise


def download_and_send_thumbnail(task, bot, temp_dir):
    try:
        work_dir = ensure_task_work_dir(task, temp_dir)
        thumbnail_url = task.info.get("thumbnail")
        if not thumbnail_url:
            raise ValueError("Превью для этого видео недоступно")

        if not task.silent_mode:
            bot.edit_message_text(
                "🖼️ Скачиваю превью видео в максимальном качестве...",
                task.chat_id,
                task.message_id,
            )

        if "youtube.com" in task.url or "youtu.be" in task.url:
            video_id = task.info.get("id")
            if video_id:
                youtube_thumbnails = [
                    f"https://img.youtube.com/vi/{video_id}/maxresdefault.jpg",
                    f"https://img.youtube.com/vi/{video_id}/sddefault.jpg",
                    f"https://img.youtube.com/vi/{video_id}/hqdefault.jpg",
                    thumbnail_url,
                ]
                for thumb_url in youtube_thumbnails:
                    try:
                        response = requests.head(thumb_url, timeout=5)
                        if response.status_code == 200:
                            thumbnail_url = thumb_url
                            break
                    except Exception:
                        continue

        response = requests.get(
            thumbnail_url,
            headers={"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"},
            timeout=30,
            stream=True,
        )
        response.raise_for_status()

        content_type = response.headers.get("content-type", "").lower()
        if "jpeg" in content_type or "jpg" in content_type:
            ext = "jpg"
        elif "png" in content_type:
            ext = "png"
        else:
            ext = "jpg"

        title = sanitize_filename(task.info.get("title") or "thumbnail")
        file_name = f"{title}_thumbnail.{ext}"
        file_path = str(work_dir / file_name)

        with open(file_path, "wb") as file_obj:
            for chunk in response.iter_content(chunk_size=8192):
                if chunk:
                    file_obj.write(chunk)

        task.file_path = file_path
        file_size = os.path.getsize(file_path)
        file_size_error = build_file_size_limit_error(file_size)
        if file_size_error:
            raise RuntimeError(file_size_error)

        try:
            with Image.open(file_path) as image:
                width, height = image.size
            _ = f"{width}x{height}"
        except Exception:
            pass

        size_str = format_file_size(file_size)
        if not task.silent_mode:
            bot.edit_message_text(
                f"📤 Готово! Отправляю превью ({size_str})...",
                task.chat_id,
                task.message_id,
            )

        bot.send_document(
            task.chat_id,
            file_path,
            caption=MessageTemplate.format_thumbnail_caption(title, task.url),
            visible_file_name=file_name,
            reply_to_message_id=task.reply_to_id,
            parse_mode="HTML",
            timeout=120,
        )

        record_download_success(
            task.chat_id,
            action="thumbnail",
            file_size_mb=file_size / (1024 * 1024),
        )

        if not task.silent_mode:
            try:
                bot.delete_message(task.chat_id, task.message_id)
            except Exception:
                pass
    except Exception as exc:
        logger.exception("Ошибка при скачивании превью: %s", exc)
        if not task.silent_mode:
            from ..utils.message_templates import ErrorMessages

            error_text = ErrorMessages.format_error_with_suggestion(str(exc))
            try:
                bot.edit_message_text(error_text, task.chat_id, task.message_id)
            except Exception:
                pass
        raise
    finally:
        if task.file_path and os.path.exists(task.file_path):
            try:
                os.remove(task.file_path)
            except Exception:
                pass
