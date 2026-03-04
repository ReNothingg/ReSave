import os
import time
import logging
import subprocess
import shutil
from pathlib import Path

import yt_dlp
import requests
from PIL import Image
from telebot import types

from ..utils.file_utils import sanitize_filename
from .user_stats import get_stats_manager
import config

logger = logging.getLogger(__name__)

cookie_path = os.path.abspath("cookies.txt")


class ProgressHook:
    def __init__(self, task):
        self.task = task

    def __call__(self, d):
        if d['status'] == 'downloading':
            total = d.get('total_bytes') or d.get('total_bytes_estimate')
            if total:
                self.task.progress = d.get('downloaded_bytes', 0) / total
        elif d['status'] == 'finished':
            self.task.progress = 1.0
        if self.task.cancel_event.is_set():
            raise Exception("Загрузка отменена пользователем")


def handle_download_task(task, bot, temp_dir):
    from ..utils.error_handler import get_error_handler
    from ..utils.retry_manager import get_smart_retry_manager, DOWNLOAD_RETRY_CONFIG

    error_handler = get_error_handler()

    try:
        if task.action == "subtitles":
            _download_and_send_subtitles(task, bot, temp_dir)
            return

        if "tiktok.com" in task.url and ("/photo/" in task.url or (task.action == "tiktok_photo")):
            _download_and_send_tiktok_photos(task, bot, temp_dir)
            return

        if task.action == "thumbnail":
            _download_and_send_thumbnail(task, bot, temp_dir)
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
                        task.message_id
                    )
                except:
                    pass

        def on_failure(attempt, exception):
            if not task.silent_mode and exception:
                error_msg = error_handler.handle_error(exception)
                try:
                    bot.edit_message_text(error_msg.user_message, task.chat_id, task.message_id)
                except:
                    pass
            task.error = str(exception)

        retry_manager.retry_operation_smart(
            download_with_retry,
            operation_id=f"download_{task.task_id}",
            on_retry=on_retry,
            on_failure=on_failure
        )

    except Exception as e:
        if not task.cancel_event.is_set():
            logger.exception(f"Ошибка при скачивании и отправке файла: {e}")

            from ..utils.message_templates import ErrorMessages
            user_message = ErrorMessages.format_error_with_suggestion(str(e))

            if not task.is_inline and not task.silent_mode:
                try:
                    bot.edit_message_text(user_message, task.chat_id, task.message_id)
                except:
                    pass

            task.error = str(e)
            raise
    finally:
        if task.file_path and os.path.exists(task.file_path):
            try:
                os.remove(task.file_path)
            except:
                pass


def _download_and_send_video(task, bot, temp_dir):
    title = sanitize_filename(task.info.get("title") or task.info.get("id") or "video")
    timestamp = int(time.time())
    output_path = os.path.join(temp_dir, f"{task.chat_id}_{timestamp}_{title}")

    fmt, post, output_template = _get_format_options(task, output_path)

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
        "cookiefile": cookie_path,
        "nocheckcertificate": True,
        "http_headers": {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
            "Accept-Language": "ru-RU,ru;q=0.9,en-US;q=0.8"
        },
        "progress_hooks": [ProgressHook(task)]
    }

    if post:
        ydl_params["postprocessors"] = post

    if shutil.which("ffmpeg"):
        ydl_params["ffmpeg_location"] = shutil.which("ffmpeg")

    with yt_dlp.YoutubeDL(ydl_params) as ydl:
        ydl.download([task.url])

    downloaded_files = list(Path(temp_dir).glob(f"{task.chat_id}_{timestamp}_{title}*"))
    downloaded_files = [
        p for p in downloaded_files
        if p.is_file() and p.suffix.lower() not in [".part", ".tmp"]
    ]
    if not downloaded_files:
        raise FileNotFoundError("Файл не найден после скачивания")

    file_path = max(downloaded_files, key=lambda p: p.stat().st_mtime)
    task.file_path = str(file_path)

    if task.action == "gif":
        _convert_to_gif_and_send(task, file_path, bot)
    else:
        if not task.silent_mode:
            size_mb = os.path.getsize(file_path) / (1024 * 1024)
            bot.edit_message_text(f"📤 Готово! Отправляю файл ({size_mb:.1f}MB)...", task.chat_id, task.message_id)

        _send_file_with_retry(task, file_path, title, bot)

        if not task.silent_mode:
            try:
                bot.delete_message(task.chat_id, task.message_id)
            except:
                logger.warning(f"Не удалось удалить сообщение о статусе {task.message_id}")


def _get_format_options(task, output_path):
    if task.action == "best":
        fmt = "bestvideo[ext=mp4]+bestaudio[ext=m4a]/best[ext=mp4]/bestvideo+bestaudio/best"
        post = []
        output_template = f"{output_path}.%(ext)s"

    elif task.action == "medium":
        fmt = "bestvideo[height<=720][ext=mp4]+bestaudio[ext=m4a]/best[height<=720][ext=mp4]/bestvideo[height<=720]+bestaudio/best[height<=720]/best"
        post = []
        output_template = f"{output_path}.%(ext)s"

    elif task.action == "low":
        fmt = "bestvideo[height<=480][ext=mp4]+bestaudio[ext=m4a]/best[height<=480][ext=mp4]/bestvideo[height<=480]+bestaudio/best[height<=480]/best"
        post = []
        output_template = f"{output_path}.%(ext)s"

    elif task.action == "audio":
        fmt = "bestaudio/best"
        post = [{"key": "FFmpegExtractAudio", "preferredcodec": "mp3", "preferredquality": "192"}]
        output_template = f"{output_path}.mp3"

    elif task.action == "gif":

        fmt = "bestvideo[height<=480][ext=mp4]+bestaudio/best[height<=480][ext=mp4]/bestvideo[height<=480]+bestaudio/best[height<=480]"
        post = []
        output_template = f"{output_path}.%(ext)s"

    elif task.action == "res" and task.format_param:
        fmt = f"{task.format_param}[ext=mp4]/{task.format_param}/best"
        post = []
        output_template = f"{output_path}.%(ext)s"

    else:
        fmt = "best[ext=mp4]/best"
        post = []
        output_template = f"{output_path}.%(ext)s"

    return fmt, post, output_template


def _convert_to_gif_and_send(task, video_path, bot):
    gif_path = Path(video_path).with_suffix('.gif')
    try:
        if not task.silent_mode:
            bot.edit_message_text("✨ Создаю GIF-анимацию... Это может занять до минуты. 🧙‍♂️",
                                 task.chat_id, task.message_id)

        video_to_convert = video_path
        temp_mp4 = None

        if not video_path.endswith('.mp4'):
            logger.info(f"Конвертирую {Path(video_path).suffix} в MP4 перед созданием GIF")
            temp_mp4 = Path(video_path).with_suffix('.temp.mp4')

            ffmpeg_cmd = ['ffmpeg', '-i', str(video_path), '-y', '-c:v', 'libx264', '-c:a', 'aac', str(temp_mp4)]
            process = subprocess.Popen(ffmpeg_cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
            stdout, stderr = process.communicate(timeout=120)

            if process.returncode != 0:
                logger.error(f"FFmpeg error при конвертации в MP4: {stderr.decode()}")
                raise Exception("Не удалось подготовить видео для GIF. Ошибка конвертации.")

            video_to_convert = str(temp_mp4)

        ffmpeg_cmd = [
            'ffmpeg', '-i', str(video_to_convert),
            '-vf', 'fps=15,scale=480:-1:flags=lanczos,split[s0][s1];[s0]palettegen[p];[s1][p]paletteuse',
            '-y', str(gif_path)
        ]
        process = subprocess.Popen(ffmpeg_cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        stdout, stderr = process.communicate(timeout=120)

        if process.returncode != 0:
            logger.error(f"FFmpeg error при создании GIF: {stderr.decode()}")
            raise Exception("Не удалось создать GIF. Ошибка конвертации.")

        title = task.info.get("title") or "animation"
        from ..utils.message_templates import MessageTemplate
        gif_caption = MessageTemplate.format_gif_caption(title, task.url)
        with open(gif_path, 'rb') as f:
            bot.send_animation(task.chat_id, f, caption=gif_caption,
                              reply_to_message_id=task.reply_to_id,
                              parse_mode='HTML')

        if not task.silent_mode:
            bot.delete_message(task.chat_id, task.message_id)

    finally:

        if gif_path and os.path.exists(gif_path):
            os.remove(gif_path)
        if temp_mp4 and os.path.exists(temp_mp4):
            os.remove(temp_mp4)


def _download_and_send_subtitles(task, bot, temp_dir):
    """Скачивает и отправляет субтитры"""
    try:
        if not task.silent_mode:
            bot.edit_message_text("📝 Ищу и скачиваю субтитры...", task.chat_id, task.message_id)


        sanitized_title = sanitize_filename(task.info.get("title") or "video")

        original_title = task.info.get("title") or "video"
        timestamp = int(time.time())
        output_template = os.path.join(temp_dir, f"{task.chat_id}_{timestamp}_{sanitized_title}.%(ext)s")


        subtitles = task.info.get('subtitles', {})
        auto_captions = task.info.get('automatic_captions', {})

        if not subtitles and not auto_captions:
            error_msg = "❌ К сожалению, для этого видео нет доступных субтитров."
            logger.info(f"Видео {task.url} не имеет субтитров")
            if not task.silent_mode:
                try:
                    bot.edit_message_text(error_msg, task.chat_id, task.message_id)
                except:
                    pass
            task.error = "Субтитры не найдены"
            return

        ydl_params = {
            'writesubtitles': True,
            'writeautomaticsub': True,
            'subtitleslangs': ['en', 'ru'],
            'subtitlesformat': 'srt',
            'skip_download': True,
            'outtmpl': output_template,
            'quiet': False,
            'no_warnings': True,
            'http_chunk_size': 10485760,
            'socket_timeout': 30,
        }

        if shutil.which("ffmpeg"):
            ydl_params["ffmpeg_location"] = shutil.which("ffmpeg")

        from ..utils.retry_manager import get_smart_retry_manager, SUBTITLE_RETRY_CONFIG
        from ..utils.error_handler import get_error_handler

        retry_manager = get_smart_retry_manager(SUBTITLE_RETRY_CONFIG)
        error_handler = get_error_handler()


        languages_sequence = [ ['en', 'ru'], ['en'], ['ru'] ]
        subtitles_success = False

        for attempt_idx, langs in enumerate(languages_sequence, start=1):
            local_params = dict(ydl_params)
            local_params['subtitleslangs'] = langs

            def download_op():

                with yt_dlp.YoutubeDL(local_params) as ydl:
                    ydl.download([task.url])

            def on_retry(attempt, delay):
                if not task.silent_mode:
                    try:
                        bot.edit_message_text(
                            f"📝 Ошибка при скачивании субтитров, попытка {attempt}...",
                            task.chat_id,
                            task.message_id
                        )
                    except:
                        pass

            def on_failure(attempt, exception):
                logger.warning(f"Подсубтитры: попытка завершилась неудачей после {attempt} попыток: {exception}")

            if not task.silent_mode:
                bot.edit_message_text(f"📝 Скачиваю субтитры (языки: {', '.join(langs)})...", task.chat_id, task.message_id)

            try:
                retry_manager.retry_operation_smart(
                    download_op,
                    operation_id=f"subtitles_{task.task_id}_{attempt_idx}",
                    on_retry=on_retry,
                    on_failure=on_failure
                )
                subtitles_success = True
                break
            except Exception as e:
                logger.warning(f"Ошибка при попытке скачать субтитры (langs={langs}): {e}")

                if "429" in str(e) or "too many requests" in str(e).lower():
                    logger.warning("HTTP 429: добавляем дополнительную паузу и пробуем следующую стратегию")
                    if not task.silent_mode:
                        try:
                            bot.edit_message_text("📝 Слишком много запросов — пробуем ещё через немного...", task.chat_id, task.message_id)
                        except:
                            pass
                    time.sleep(10)
                    continue
                else:
                    logger.info("Постоянная ошибка, остановка попыток загрузки субтитров")
                    break

        if not subtitles_success:

            logger.error("Не удалось скачать субтитры после всех попыток")
            if not task.silent_mode:
                try:
                    bot.edit_message_text("❌ Не удалось скачать субтитры. Попробуйте позже.", task.chat_id, task.message_id)
                except:
                    pass

            task.error = "Не удалось скачать субтитры"
            return

        subtitle_files = list(Path(temp_dir).glob(f"{task.chat_id}_{timestamp}_{sanitized_title}*.srt"))
        if not subtitle_files:
            error_msg = "❌ Субтитры не были сохранены. Возможно, сервер их удалил."
            logger.error("Субтитры не найдены после скачивания.")
            if not task.silent_mode:
                try:
                    bot.edit_message_text(error_msg, task.chat_id, task.message_id)
                except:
                    pass
            task.error = "Субтитры не найдены"
            return

        for idx, srt_path in enumerate(subtitle_files):

            if idx > 0:
                time.sleep(1)

            from ..utils.message_templates import MessageTemplate
            sub_caption = MessageTemplate.format_subtitles_caption(original_title, task.url)
            with open(srt_path, 'rb') as f:
                bot.send_document(task.chat_id, f, caption=sub_caption,
                                 reply_to_message_id=task.reply_to_id,
                                 parse_mode='HTML')
            os.remove(srt_path)

        if not task.silent_mode:
            bot.delete_message(task.chat_id, task.message_id)

    except Exception as e:
        logger.error(f"Ошибка скачивания субтитров: {e}")
        if not task.silent_mode:
            from ..utils.message_templates import ErrorMessages
            error_text = ErrorMessages.format_error_with_suggestion(str(e))
            try:
                bot.edit_message_text(error_text, task.chat_id, task.message_id)
            except:
                pass
        raise


def _download_and_send_tiktok_photos(task, bot, temp_dir):
    """Скачивает и отправляет TikTok фото с использованием оптимизированного обработчика"""
    from .tiktok_photo_handler import TikTokPhotoHandler

    downloaded_files = []

    try:
        handler = TikTokPhotoHandler(temp_dir)

        if not task.silent_mode:
            bot.edit_message_text(
                "🖼️ Скачиваю фото из TikTok... Это может занять секунду ⏳",
                task.chat_id,
                task.message_id
            )

        logger.info(f"Начинаю скачивание TikTok фото: {task.url}")

        downloaded_files = handler.download_photos(task.url, temp_dir)

        if not downloaded_files:
            raise RuntimeError("Не удалось скачать ни одного фото из TikTok")

        logger.info(f"Успешно скачано {len(downloaded_files)} фото")

        if len(downloaded_files) == 1:

            file_path = downloaded_files[0]
            task.file_path = file_path

            file_size = os.path.getsize(file_path)
            size_str = _format_file_size(file_size)

            if not task.silent_mode:
                bot.edit_message_text(
                    f"📤 Готово! Отправляю фото ({size_str})...",
                    task.chat_id,
                    task.message_id
                )

            with open(file_path, 'rb') as f:
                message = bot.send_photo(
                    task.chat_id,
                    f,
                    caption=MessageTemplate.format_tiktok_photo_caption(task.url),
                    reply_to_message_id=task.reply_to_id,
                    timeout=60,
                    parse_mode='HTML'
                )

            logger.info(f"Фото успешно отправлено в чат {task.chat_id}")
        else:

            file_size_total = sum(os.path.getsize(f) for f in downloaded_files)
            size_str = _format_file_size(file_size_total)

            if not task.silent_mode:
                bot.edit_message_text(
                    f"📤 Готово! Отправляю {len(downloaded_files)} фото ({size_str})...",
                    task.chat_id,
                    task.message_id
                )


            media_group = []
            for idx, file_path in enumerate(downloaded_files):
                with open(file_path, 'rb') as f:
                    if idx == 0:
                        caption = MessageTemplate.format_tiktok_photo_caption(task.url, len(downloaded_files))
                        media = types.InputMediaPhoto(
                            media=f.read(),
                            caption=caption,
                            parse_mode='HTML'
                        )
                    else:
                        media = types.InputMediaPhoto(media=f.read())
                    media_group.append(media)

            try:
                bot.send_media_group(
                    task.chat_id,
                    media_group,
                    reply_to_message_id=task.reply_to_id,
                    timeout=120
                )
                logger.info(f"Группа из {len(downloaded_files)} фото успешно отправлена в чат {task.chat_id}")
            except Exception as e:
                logger.warning(f"Ошибка при отправке группы фото, отправляю по отдельности: {e}")

                for idx, file_path in enumerate(downloaded_files):
                    caption = MessageTemplate.format_tiktok_photo_caption(task.url) if idx == 0 else f"🖼️ Фото {idx + 1}/{len(downloaded_files)}"
                    with open(file_path, 'rb') as f:
                        bot.send_photo(
                            task.chat_id,
                            f,
                            caption=caption,
                            reply_to_message_id=task.reply_to_id if idx == 0 else None,
                            timeout=60,
                            parse_mode='HTML'
                        )
                    time.sleep(0.5)

        if not task.silent_mode:
            try:
                bot.delete_message(task.chat_id, task.message_id)
            except:
                pass

        try:
            file_size_mb = file_size_total / (1024 * 1024) if len(downloaded_files) > 1 else os.path.getsize(downloaded_files[0]) / (1024 * 1024)
            stats_manager = get_stats_manager()
            stats_manager.record_download(
                task.chat_id,
                action="tiktok_photo",
                file_size_mb=file_size_mb
            )
        except Exception as e:
            logger.error(f"Ошибка при записи статистики: {e}")

    except Exception as e:
        logger.exception(f"Ошибка при скачивании TikTok фото: {e}")

        if not task.silent_mode:
            from ..utils.message_templates import ErrorMessages
            error_text = ErrorMessages.format_error_with_suggestion(
                str(e),
                "Убедитесь, что это пост с фото или видео, а не рилс"
            )
            try:
                bot.edit_message_text(error_text, task.chat_id, task.message_id)
            except:
                pass
        raise

    finally:
        for file_path in downloaded_files:
            try:
                if os.path.exists(file_path):
                    os.remove(file_path)
            except Exception as e:
                logger.debug(f"Ошибка при удалении временного файла {file_path}: {e}")


def _format_file_size(size_bytes: int) -> str:
    if size_bytes < 1024:
        return f"{size_bytes} B"
    elif size_bytes < 1024 * 1024:
        return f"{size_bytes / 1024:.1f} KB"
    else:
        return f"{size_bytes / (1024 * 1024):.1f} MB"


def _download_and_send_thumbnail(task, bot, temp_dir):
    try:
        thumbnail_url = task.info.get("thumbnail")
        if not thumbnail_url:
            raise ValueError("Превью для этого видео недоступно")

        if not task.silent_mode:
            bot.edit_message_text(
                "🖼️ Скачиваю превью видео в максимальном качестве...",
                task.chat_id,
                task.message_id
            )

        if "youtube.com" in task.url or "youtu.be" in task.url:
            video_id = task.info.get("id")
            if video_id:
                youtube_thumbnails = [
                    f"https://img.youtube.com/vi/{video_id}/maxresdefault.jpg",
                    f"https://img.youtube.com/vi/{video_id}/sddefault.jpg",
                    f"https://img.youtube.com/vi/{video_id}/hqdefault.jpg",
                    thumbnail_url
                ]

                for thumb_url in youtube_thumbnails:
                    try:
                        response = requests.head(thumb_url, timeout=5)
                        if response.status_code == 200:
                            thumbnail_url = thumb_url
                            break
                    except:
                        continue

        response = requests.get(thumbnail_url, headers={
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"
        }, timeout=30, stream=True)
        response.raise_for_status()

        content_type = response.headers.get('content-type', '').lower()
        if 'jpeg' in content_type or 'jpg' in content_type:
            ext = 'jpg'
        elif 'png' in content_type:
            ext = 'png'
        else:
            ext = 'jpg'

        title = sanitize_filename(task.info.get("title") or "thumbnail")
        timestamp = int(time.time())
        file_name = f"{title}_thumbnail.{ext}"
        file_path = os.path.join(temp_dir, f"{task.chat_id}_{timestamp}_{file_name}")

        with open(file_path, 'wb') as f:
            for chunk in response.iter_content(chunk_size=8192):
                if chunk:
                    f.write(chunk)

        task.file_path = file_path
        file_size = os.path.getsize(file_path)

        resolution_info = ""
        try:
            with Image.open(file_path) as img:
                width, height = img.size
                resolution_info = f"\n📐 Разрешение: {width}×{height}"
        except:
            pass

        if file_size < 1024:
            size_str = f"{file_size} B"
        elif file_size < 1024 * 1024:
            size_str = f"{file_size / 1024:.1f} KB"
        else:
            size_str = f"{file_size / (1024 * 1024):.2f} MB"

        if not task.silent_mode:
            bot.edit_message_text(
                f"📤 Готово! Отправляю превью ({size_str})...",
                task.chat_id,
                task.message_id
            )

        with open(file_path, 'rb') as f:
            bot.send_document(
                task.chat_id,
                f,
                caption=MessageTemplate.format_thumbnail_caption(title, task.url),
                visible_file_name=file_name,
                reply_to_message_id=task.reply_to_id,
                parse_mode='HTML'
            )

        if not task.silent_mode:
            try:
                bot.delete_message(task.chat_id, task.message_id)
            except:
                pass

    except Exception as e:
        logger.exception(f"Ошибка при скачивании превью: {e}")
        if not task.silent_mode:
            from ..utils.message_templates import ErrorMessages
            error_text = ErrorMessages.format_error_with_suggestion(str(e))
            try:
                bot.edit_message_text(error_text, task.chat_id, task.message_id)
            except:
                pass
        raise

    finally:
        if task.file_path and os.path.exists(task.file_path):
            try:
                os.remove(task.file_path)
            except:
                pass


def _send_file_with_retry(task, file_path, title, bot, retry_count=0, max_retries=3):
    from ..utils.retry_manager import get_smart_retry_manager, UPLOAD_RETRY_CONFIG
    from ..utils.error_handler import get_error_handler
    from ..utils.message_templates import MessageTemplate, ErrorMessages

    error_handler = get_error_handler()
    retry_manager = get_smart_retry_manager(UPLOAD_RETRY_CONFIG)

    file_extension = Path(file_path).suffix.lower()
    audio_extensions = ['.mp3', '.m4a', '.aac', '.ogg', '.wav', '.flac']

    original_title = task.info.get("title") or task.info.get("id") or "video"

    def send_operation():
        file_size_mb = os.path.getsize(file_path) / (1024 * 1024)
        
        with open(file_path, 'rb') as f:
            if task.action == "audio" and file_extension in audio_extensions:
                # Для аудио используем встроенное название/исполнителя
                bot.send_audio(
                    task.chat_id,
                    f,
                    title=original_title,
                    performer=task.info.get('uploader', 'Unknown'),
                    duration=task.info.get('duration'),
                    timeout=300,
                    reply_to_message_id=task.reply_to_id
                )
            else:
                # Для видео используем единый шаблон
                caption = MessageTemplate.format_caption(
                    title=original_title,
                    url=task.url,
                    action="video",
                    file_size=file_size_mb
                )
                bot.send_video(
                    task.chat_id,
                    f,
                    caption=caption,
                    parse_mode='HTML',
                    duration=task.info.get('duration'),
                    width=task.info.get('width'),
                    height=task.info.get('height'),
                    supports_streaming=True,
                    timeout=600,
                    reply_to_message_id=task.reply_to_id
                )

    def on_retry(attempt, delay):
        if not task.silent_mode:
            try:
                bot.edit_message_text(
                    f"📤 Отправка файла (попытка {attempt})...\n"
                    f"⏱️ Ожидание {delay:.0f} сек",
                    task.chat_id,
                    task.message_id
                )
            except:
                pass

    def on_failure(attempt, exception):
        if exception and not task.silent_mode:
            error_msg = error_handler.handle_error(exception)
            try:
                bot.edit_message_text(error_msg.user_message, task.chat_id, task.message_id)
            except:
                pass

    try:
        retry_manager.retry_operation_smart(
            send_operation,
            operation_id=f"upload_{task.task_id}",
            on_retry=on_retry,
            on_failure=on_failure
        )

        try:
            file_size_mb = os.path.getsize(file_path) / (1024 * 1024)
            stats_manager = get_stats_manager()
            stats_manager.record_download(
                task.chat_id,
                action=task.action if task.action in ["video", "audio"] else "video",
                file_size_mb=file_size_mb
            )
        except Exception as e:
            logger.error(f"Ошибка при записи статистики: {e}")

    except Exception as e:
        logger.error(f"Ошибка при отправке файла после всех попыток: {e}")
        raise
