from __future__ import annotations

import asyncio
import json
import logging
import shutil
from pathlib import Path

from aiogram.exceptions import TelegramBadRequest

from config import Settings

from ..utils.file_utils import sanitize_filename
from ..utils.presentation import media_caption, panel
from .media_downloader import DownloadCancelled, FileTooLarge, MediaDownloader
from .models import DownloadAction, DownloadTask, TaskStatus
from .telegram_gateway import TelegramGateway
from .tiktok_photo_handler import download_tiktok_photos
from .user_stats import UserStatsManager

logger = logging.getLogger(__name__)


class MediaPipeline:
    def __init__(
        self,
        settings: Settings,
        downloader: MediaDownloader,
        telegram: TelegramGateway,
        stats: UserStatsManager,
    ):
        self.settings = settings
        self.downloader = downloader
        self.telegram = telegram
        self.stats = stats

    async def process(self, task: DownloadTask) -> None:
        work_dir = self.settings.temp_dir / f"task_{task.task_id}"
        work_dir.mkdir(parents=True, exist_ok=False)
        task.work_dir = str(work_dir)
        cleanup = True
        try:
            if task.action == DownloadAction.SUBTITLES:
                await self._subtitles(task, work_dir)
            elif task.action == DownloadAction.THUMBNAIL:
                await self._thumbnail(task, work_dir)
            elif task.action == DownloadAction.TIKTOK_PHOTO:
                await self._tiktok_photos(task, work_dir)
            else:
                await self._video_or_audio(task, work_dir)
        except asyncio.CancelledError:
            # A cancelled to_thread call can still be using this directory.
            # Startup cleanup will remove it safely on the next launch.
            cleanup = False
            raise
        finally:
            if cleanup:
                await asyncio.to_thread(shutil.rmtree, work_dir, True)

    async def update_progress(self, task: DownloadTask) -> None:
        if task.silent or task.status not in {
            TaskStatus.DOWNLOADING,
            TaskStatus.PROCESSING,
            TaskStatus.UPLOADING,
        }:
            return
        lines = [task.phase]
        if task.status == TaskStatus.DOWNLOADING and task.progress > 0:
            from ..utils.presentation import progress_bar

            lines.append(progress_bar(task.progress))
            details = " · ".join(
                part for part in (task.speed, f"ETA {task.eta}s" if task.eta else None) if part
            )
            if details:
                lines.append(details)
        await self.telegram.edit_status(
            task.chat_id,
            task.status_message_id,
            panel("Обработка медиа", lines, icon="📦"),
        )

    async def _set_phase(self, task: DownloadTask, status: TaskStatus, phase: str) -> None:
        if task.cancel_event.is_set():
            raise DownloadCancelled("Загрузка отменена пользователем")
        task.status = status
        task.phase = phase
        if not task.silent:
            await self.update_progress(task)

    async def _video_or_audio(self, task: DownloadTask, work_dir: Path) -> None:
        source = await self.downloader.download(task, work_dir)
        if task.cancel_event.is_set():
            raise DownloadCancelled("Загрузка отменена пользователем")

        if task.action == DownloadAction.GIF:
            await self._set_phase(task, TaskStatus.PROCESSING, "Создание GIF")
            result = await self._convert_gif(source, work_dir / "animation.gif")
            if result.stat().st_size > self.settings.effective_upload_limit:
                raise FileTooLarge("Converted GIF exceeds Telegram file size limit")
            await self._set_phase(task, TaskStatus.UPLOADING, "Отправка GIF")
            size_mb = result.stat().st_size / (1024 * 1024)
            caption = media_caption(task.title, task.url, kind="gif", size_mb=size_mb)
            try:
                await self.telegram.send_animation(
                    task.chat_id, result, caption=caption, reply_to=task.reply_to_message_id
                )
            except TelegramBadRequest:
                await self.telegram.send_document(
                    task.chat_id,
                    result,
                    caption=caption,
                    reply_to=task.reply_to_message_id,
                )
            await self._record(task, "gif", size_mb)
            return

        size_mb = source.stat().st_size / (1024 * 1024)
        await self._set_phase(task, TaskStatus.UPLOADING, f"Отправка файла · {size_mb:.1f} MB")
        if task.action == DownloadAction.AUDIO:
            caption = media_caption(task.title, task.url, kind="audio", size_mb=size_mb)
            try:
                await self.telegram.send_audio(
                    task.chat_id,
                    source,
                    caption=caption,
                    title=task.title,
                    performer=task.info.get("uploader"),
                    reply_to=task.reply_to_message_id,
                    duration=self._duration(task.info),
                )
            except TelegramBadRequest:
                await self.telegram.send_document(
                    task.chat_id,
                    source,
                    caption=caption,
                    reply_to=task.reply_to_message_id,
                    filename=f"{sanitize_filename(task.title) or 'audio'}{source.suffix}",
                )
            await self._record(task, "audio", size_mb)
            return

        caption = media_caption(task.title, task.url, kind="video", size_mb=size_mb)
        metadata = await self._probe(source)
        if source.stat().st_size > self.settings.send_as_doc_limit:
            await self.telegram.send_document(
                task.chat_id,
                source,
                caption=caption,
                reply_to=task.reply_to_message_id,
                filename=f"{sanitize_filename(task.title) or 'video'}{source.suffix}",
            )
        else:
            try:
                await self.telegram.send_video(
                    task.chat_id,
                    source,
                    caption=caption,
                    reply_to=task.reply_to_message_id,
                    metadata=metadata,
                )
            except TelegramBadRequest as exc:
                logger.info("Telegram rejected video payload; sending document: %s", exc)
                await self.telegram.send_document(
                    task.chat_id,
                    source,
                    caption=caption,
                    reply_to=task.reply_to_message_id,
                    filename=f"{sanitize_filename(task.title) or 'video'}{source.suffix}",
                )
        await self._record(task, "video", size_mb)

    async def _subtitles(self, task: DownloadTask, work_dir: Path) -> None:
        files = await self.downloader.download_subtitles(task, work_dir)
        await self._set_phase(task, TaskStatus.UPLOADING, "Отправка субтитров")
        total = 0
        caption = media_caption(task.title, task.url, kind="subtitles")
        for index, path in enumerate(files):
            total += path.stat().st_size
            await self.telegram.send_document(
                task.chat_id,
                path,
                caption=caption if index == 0 else None,
                reply_to=task.reply_to_message_id if index == 0 else None,
            )
        await self._record(task, "subtitles", total / (1024 * 1024))

    async def _thumbnail(self, task: DownloadTask, work_dir: Path) -> None:
        path = await self.downloader.download_thumbnail(task, work_dir)
        await self._set_phase(task, TaskStatus.UPLOADING, "Отправка превью")
        size_mb = path.stat().st_size / (1024 * 1024)
        await self.telegram.send_document(
            task.chat_id,
            path,
            caption=media_caption(task.title, task.url, kind="thumbnail", size_mb=size_mb),
            reply_to=task.reply_to_message_id,
            filename=f"{sanitize_filename(task.title) or 'thumbnail'}{path.suffix}",
        )
        await self._record(task, "thumbnail", size_mb)

    async def _tiktok_photos(self, task: DownloadTask, work_dir: Path) -> None:
        await self._set_phase(task, TaskStatus.DOWNLOADING, "Скачивание фото TikTok")
        photos = await asyncio.to_thread(download_tiktok_photos, task.url, work_dir)
        if task.cancel_event.is_set():
            raise DownloadCancelled("Загрузка отменена пользователем")
        await self._set_phase(task, TaskStatus.UPLOADING, "Отправка фото")
        caption = media_caption(
            f"Фото из TikTok · {len(photos)} шт.", task.url, kind="tiktok_photo"
        )
        for offset in range(0, len(photos), 10):
            chunk = photos[offset : offset + 10]
            first = offset == 0
            if len(chunk) == 1:
                await self._send_photo_or_document(
                    task,
                    chunk[0],
                    caption=caption if first else None,
                    reply_to=task.reply_to_message_id if first else None,
                )
            else:
                try:
                    await self.telegram.send_photo_group(
                        task.chat_id,
                        chunk,
                        caption=caption if first else None,
                        reply_to=task.reply_to_message_id if first else None,
                    )
                except TelegramBadRequest:
                    for index, photo in enumerate(chunk):
                        await self._send_photo_or_document(
                            task,
                            photo,
                            caption=caption if first and index == 0 else None,
                            reply_to=(task.reply_to_message_id if first and index == 0 else None),
                        )
        total = sum(path.stat().st_size for path in photos)
        await self._record(task, "tiktok_photo", total / (1024 * 1024))

    async def _send_photo_or_document(
        self,
        task: DownloadTask,
        path: Path,
        *,
        caption: str | None,
        reply_to: int | None,
    ) -> None:
        try:
            await self.telegram.send_photo(
                task.chat_id,
                path,
                caption=caption,
                reply_to=reply_to,
            )
        except TelegramBadRequest:
            await self.telegram.send_document(
                task.chat_id,
                path,
                caption=caption,
                reply_to=reply_to,
            )

    async def _convert_gif(self, source: Path, target: Path) -> Path:
        ffmpeg = shutil.which("ffmpeg")
        if not ffmpeg:
            raise RuntimeError("FFmpeg is not installed")
        process = await asyncio.create_subprocess_exec(
            ffmpeg,
            "-hide_banner",
            "-loglevel",
            "error",
            "-i",
            str(source),
            "-t",
            "30",
            "-vf",
            "fps=12,scale=480:-2:flags=lanczos,split[s0][s1];[s0]palettegen[p];[s1][p]paletteuse",
            "-y",
            str(target),
            stdout=asyncio.subprocess.DEVNULL,
            stderr=asyncio.subprocess.PIPE,
        )
        try:
            _, stderr = await asyncio.wait_for(process.communicate(), timeout=120)
        except asyncio.CancelledError:
            if process.returncode is None:
                process.kill()
                await process.wait()
            raise
        except TimeoutError:
            process.kill()
            await process.wait()
            raise RuntimeError("FFmpeg GIF conversion timeout") from None
        if process.returncode:
            detail = stderr.decode("utf-8", errors="replace")[-500:]
            raise RuntimeError(f"FFmpeg GIF conversion failed: {detail}")
        return target

    async def _probe(self, path: Path) -> dict[str, int]:
        ffprobe = shutil.which("ffprobe")
        if not ffprobe:
            return {}
        process = await asyncio.create_subprocess_exec(
            ffprobe,
            "-v",
            "error",
            "-select_streams",
            "v:0",
            "-show_entries",
            "stream=width,height,duration,sample_aspect_ratio:stream_tags=rotate:stream_side_data=rotation:format=duration",
            "-of",
            "json",
            str(path),
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.DEVNULL,
        )
        try:
            stdout, _ = await asyncio.wait_for(process.communicate(), timeout=15)
            payload = json.loads(stdout or b"{}")
        except asyncio.CancelledError:
            if process.returncode is None:
                process.kill()
                await process.wait()
            raise
        except (TimeoutError, ValueError):
            if process.returncode is None:
                process.kill()
                await process.wait()
            return {}
        stream = (payload.get("streams") or [{}])[0]
        result: dict[str, int] = {}
        for key in ("width", "height"):
            value = stream.get(key)
            if isinstance(value, int) and value > 0:
                result[key] = value
        ratio = self._ratio(stream.get("sample_aspect_ratio"))
        if ratio and result.get("width"):
            result["width"] = max(1, round(result["width"] * ratio))
        rotation = 0
        try:
            rotation = int((stream.get("tags") or {}).get("rotate") or 0)
        except (TypeError, ValueError):
            pass
        for side_data in stream.get("side_data_list") or []:
            try:
                rotation = int(side_data.get("rotation"))
                break
            except (TypeError, ValueError):
                continue
        if abs(rotation) % 180 == 90 and result.get("width") and result.get("height"):
            result["width"], result["height"] = result["height"], result["width"]
        duration = stream.get("duration") or (payload.get("format") or {}).get("duration")
        try:
            parsed_duration = round(float(duration))
        except (TypeError, ValueError):
            parsed_duration = 0
        if parsed_duration > 0:
            result["duration"] = parsed_duration
        return result

    @staticmethod
    def _ratio(value: str | None) -> float | None:
        if not value or value in {"0:1", "1:0", "0:0"}:
            return None
        try:
            numerator, denominator = value.split(":", 1)
            ratio = int(numerator) / int(denominator)
        except (TypeError, ValueError, ZeroDivisionError):
            return None
        return ratio if ratio > 0 else None

    @staticmethod
    def _duration(info: dict) -> int | None:
        try:
            value = round(float(info.get("duration")))
        except (TypeError, ValueError):
            return None
        return value if value > 0 else None

    async def _record(self, task: DownloadTask, action: str, size_mb: float) -> None:
        try:
            await asyncio.to_thread(
                self.stats.record_download,
                task.user_id,
                action,
                size_mb,
            )
        except Exception as exc:
            logger.error("Cannot record successful download for %s: %s", task.user_id, exc)
