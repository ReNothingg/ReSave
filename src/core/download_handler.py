import logging
import multiprocessing
import os
from dataclasses import dataclass
from queue import Empty, Full
import shutil
import time

import yt_dlp

import config
from ..utils.admin_notifier import notify_admins
from ..utils.file_utils import sanitize_filename
from .access import build_file_size_limit_error
from .download_support import (
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


@dataclass(frozen=True)
class DownloadVariant:
    label: str
    format: str
    postprocessors: tuple[dict, ...]
    output_template: str


def _queue_event(event_queue, event: tuple, *, block: bool = False):
    try:
        if block:
            event_queue.put(event, timeout=5)
        else:
            event_queue.put_nowait(event)
    except Full:
        pass


def _yt_dlp_download_worker(url: str, ydl_params: dict, event_queue):
    def progress_hook(data):
        status = data.get("status")
        if status == "downloading":
            total = data.get("total_bytes") or data.get("total_bytes_estimate")
            downloaded = data.get("downloaded_bytes", 0)
            if total:
                _queue_event(event_queue, ("progress", downloaded, total))
        elif status == "finished":
            _queue_event(event_queue, ("progress", 1, 1))

    try:
        child_params = dict(ydl_params)
        child_params["progress_hooks"] = [progress_hook]
        with yt_dlp.YoutubeDL(child_params) as ydl:
            ydl.download([url])
    except BaseException as exc:
        _queue_event(event_queue, ("error", type(exc).__name__, str(exc)), block=True)
    else:
        _queue_event(event_queue, ("ok", "", ""), block=True)


def _stop_process(process: multiprocessing.Process):
    if not process.is_alive():
        return

    process.terminate()
    process.join(5)
    if process.is_alive():
        process.kill()
        process.join(5)


def _drain_download_events(event_queue, task) -> tuple | None:
    result = None

    while True:
        try:
            event = event_queue.get_nowait()
        except Empty:
            return result

        event_type = event[0]
        if event_type == "progress":
            _, downloaded, total = event
            if total:
                task.progress = min(1.0, max(0.0, downloaded / total))
        elif event_type in {"ok", "error"}:
            result = event


def _download_with_timeout(url: str, ydl_params: dict, timeout_seconds: int, task):
    child_params = dict(ydl_params)
    child_params.pop("progress_hooks", None)

    task.progress = 0.0
    event_queue = multiprocessing.Queue(maxsize=100)
    process = multiprocessing.Process(
        target=_yt_dlp_download_worker,
        args=(url, child_params, event_queue),
        daemon=True,
    )
    process.start()

    deadline = time.monotonic() + timeout_seconds
    result = None

    while process.is_alive():
        drained = _drain_download_events(event_queue, task)
        if drained:
            result = drained

        if task.cancel_event.is_set():
            _stop_process(process)
            raise RuntimeError("Загрузка отменена пользователем")

        if time.monotonic() >= deadline:
            _stop_process(process)
            raise TimeoutError(f"Download timed out after {timeout_seconds} seconds")

        process.join(0.25)

    drained = _drain_download_events(event_queue, task)
    if drained:
        result = drained

    if result:
        status, error_type, message = result
        if status == "ok":
            task.progress = 1.0
            return
        raise RuntimeError(f"{error_type}: {message}")

    if process.exitcode:
        if process.exitcode < 0:
            signal_number = abs(process.exitcode)
            raise RuntimeError(
                "yt-dlp download process was killed by server "
                f"(signal {signal_number}, exit code {process.exitcode})"
            )
        raise RuntimeError(f"yt-dlp exited with code {process.exitcode}")


def _ffmpeg_location() -> str | None:
    ffmpeg_path = shutil.which("ffmpeg")
    if not ffmpeg_path:
        return None
    return str(os.path.dirname(ffmpeg_path))


def handle_download_task(task, bot, temp_dir):
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

        _download_and_send_video(task, bot, temp_dir)
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

            if not task.silent_mode:
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

    if task.action in {"audio", "gif"} and not shutil.which("ffmpeg"):
        raise RuntimeError("FFmpeg is not installed on server")

    variants = _get_download_variants(task, output_path)
    file_path = _download_first_available_variant(task, bot, work_dir, variants)

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


def _clear_work_dir(work_dir):
    for path in work_dir.iterdir():
        try:
            if path.is_dir():
                shutil.rmtree(path, ignore_errors=True)
            else:
                path.unlink(missing_ok=True)
        except Exception as exc:
            logger.debug("Не удалось очистить временный файл %s: %s", path, exc)


def _base_ydl_params(variant: DownloadVariant) -> dict:
    ydl_params = {
        "format": variant.format,
        "outtmpl": variant.output_template,
        "quiet": True,
        "no_warnings": True,
        "noplaylist": True,
        "merge_output_format": "mp4",
        "http_chunk_size": 5 * 1024 * 1024,
        "socket_timeout": 30,
        "retries": 3,
        "fragment_retries": 3,
        "extractor_retries": 3,
        "file_access_retries": 3,
        "concurrent_fragment_downloads": 1,
        "max_filesize": config.MAX_FILE_SIZE,
        "continuedl": True,
        "overwrites": True,
        "trim_file_name": 180,
        "cookiefile": config.COOKIES_FILE,
        "nocheckcertificate": True,
        "http_headers": {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
            "Accept-Language": "ru-RU,ru;q=0.9,en-US;q=0.8",
        },
    }

    if variant.postprocessors:
        ydl_params["postprocessors"] = list(variant.postprocessors)

    ffmpeg_location = _ffmpeg_location()
    if ffmpeg_location:
        ydl_params["ffmpeg_location"] = ffmpeg_location

    return ydl_params


def _download_first_available_variant(task, bot, work_dir, variants: list[DownloadVariant]):
    errors: list[str] = []

    for index, variant in enumerate(variants, start=1):
        if task.cancel_event.is_set():
            raise RuntimeError("Загрузка отменена пользователем")

        _clear_work_dir(work_dir)
        if not task.silent_mode:
            status_text = "⏬ Скачиваю видео..."
            if index > 1:
                status_text = f"⏬ Высокое качество не прошло, пробую {variant.label}..."
            try:
                bot.edit_message_text(status_text, task.chat_id, task.message_id)
            except Exception:
                pass

        logger.info(
            "Download format selected: task_id=%s action=%s format_param=%s variant=%s format=%s",
            task.task_id,
            task.action,
            task.format_param,
            variant.label,
            variant.format,
        )

        try:
            _download_with_timeout(
                task.url,
                _base_ydl_params(variant),
                config.DOWNLOAD_TIMEOUT_SECONDS,
                task,
            )
        except Exception as exc:
            message = str(exc)
            errors.append(f"{variant.label}: {message}")
            logger.warning(
                "Download variant failed: task_id=%s variant=%s error=%s",
                task.task_id,
                variant.label,
                message,
            )
            if index == len(variants) or not _can_try_next_variant(exc):
                raise RuntimeError(_format_download_failure(errors)) from exc
            continue

        downloaded_files = find_completed_files(work_dir)
        if downloaded_files:
            return max(downloaded_files, key=lambda path: path.stat().st_mtime)

        contents = describe_work_dir(work_dir)
        errors.append(f"{variant.label}: file not found after download ({contents})")
        logger.error(
            "Файл не найден после скачивания. task_id=%s, action=%s, work_dir=%s, contents=%s",
            task.task_id,
            task.action,
            work_dir,
            contents,
        )

    raise FileNotFoundError(_format_download_failure(errors) or "Файл не найден после скачивания")


def _can_try_next_variant(exc: Exception) -> bool:
    error_text = str(exc).lower()
    permanent_markers = (
        "private",
        "unsupported url",
        "not a valid url",
        "copyright",
        "sign in to confirm",
        "login required",
        "video unavailable",
        "this video is unavailable",
        "403",
        "404",
    )
    return not any(marker in error_text for marker in permanent_markers)


def _format_download_failure(errors: list[str]) -> str:
    if not errors:
        return "Не удалось скачать видео"

    last_error = errors[-1]
    if any("killed by server" in error.lower() or "exit code -9" in error.lower() for error in errors):
        return (
            "Не удалось скачать видео: сервер убил процесс загрузки/склейки. "
            "Я уже попробовал качество ниже, но загрузка всё равно не прошла. "
            f"Последняя ошибка: {last_error}"
        )

    return f"Не удалось скачать видео. Последняя ошибка: {last_error}"


def _height_format(height: int, *, exact_first: bool = False) -> str:
    if exact_first:
        return (
            f"best[height={height}][ext=mp4]/"
            f"bv*[height={height}][ext=mp4]+ba[ext=m4a]/"
            f"bv*[height={height}]+ba/"
            f"best[height<={height}][ext=mp4]/"
            f"best[height<={height}]/"
            f"bv*[height<={height}][ext=mp4]+ba[ext=m4a]/"
            f"bv*[height<={height}]+ba/best"
        )

    return (
        f"best[height<={height}][ext=mp4]/"
        f"best[height<={height}]/"
        f"bv*[height<={height}][ext=mp4]+ba[ext=m4a]/"
        f"bv*[height<={height}]+ba/best"
    )


def _unique_heights(values: list[int]) -> list[int]:
    seen = set()
    result = []
    for value in values:
        if value > 0 and value not in seen:
            seen.add(value)
            result.append(value)
    return result


def _video_height_variants(output_path: str, heights: list[int], *, exact_first: bool = False):
    return [
        DownloadVariant(
            label=f"{height}p",
            format=_height_format(height, exact_first=exact_first and index == 0),
            postprocessors=(),
            output_template=f"{output_path}.%(ext)s",
        )
        for index, height in enumerate(_unique_heights(heights))
    ]


def _get_download_variants(task, output_path) -> list[DownloadVariant]:
    if task.action == "best":
        return _video_height_variants(output_path, [1080, 720, 480, 360])

    if task.action == "medium":
        return _video_height_variants(output_path, [720, 480, 360])

    if task.action == "low":
        return _video_height_variants(output_path, [480, 360])

    if task.action == "audio":
        return [
            DownloadVariant(
                label="MP3",
                format="bestaudio/best",
                postprocessors=(
                    {
                        "key": "FFmpegExtractAudio",
                        "preferredcodec": "mp3",
                        "preferredquality": "192",
                    },
                ),
                output_template=f"{output_path}.mp3",
            )
        ]

    if task.action == "gif":
        return _video_height_variants(output_path, [480, 360])

    if task.action == "res" and task.format_param:
        height = int(task.format_param)
        fallback_heights = [candidate for candidate in [1080, 720, 480, 360] if candidate < height]
        return _video_height_variants(
            output_path,
            [height, *fallback_heights],
            exact_first=True,
        )

    return _video_height_variants(output_path, [720, 480, 360])
