from __future__ import annotations

import asyncio
import logging
import shutil
import time
from pathlib import Path

from aiogram.exceptions import TelegramBadRequest

from config import Settings

from ..utils.cards import progress_card
from ..utils.file_utils import sanitize_filename
from ..utils.presentation import format_duration, media_caption
from ..utils.theme import EFFECTS, emoji
from .errors import DownloadCancelled, FileTooLarge
from .ffmpeg_tools import convert_gif, positive_seconds, probe_video
from .media_downloader import MediaDownloader
from .models import DownloadAction, DownloadTask, TaskStatus
from .telegram_gateway import TelegramGateway
from .tiktok_photo_handler import download_tiktok_photos
from .user_stats import UserStatsManager

logger = logging.getLogger(__name__)
MILESTONES = {1, 10, 25, 50, 100, 250, 500, 1000}


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
            cleanup = False
            raise
        finally:
            if cleanup:
                await asyncio.to_thread(shutil.rmtree, work_dir, True)

    async def update_progress(self, task: DownloadTask) -> None:
        if task.silent:
            return
        await self.telegram.edit_status(
            task.chat_id,
            task.status_message_id,
            progress_card(task),
        )

    async def _milestone(self, task: DownloadTask) -> tuple[str | None, str | None]:
        if task.chat_id < 0 or task.silent:
            return None, None
        try:
            value = await asyncio.to_thread(self.stats.get_user_stats, task.user_id)
        except Exception:
            return None, None
        upcoming = value.downloads_count + 1
        if upcoming not in MILESTONES:
            return None, None
        if upcoming == 1:
            return EFFECTS["party"], f"{emoji('party')} Первая загрузка! Присылайте ещё ссылки."
        return EFFECTS["fire"], f"{emoji('trophy')} Это ваша {upcoming}-я загрузка!"

    async def _caption(
        self,
        task: DownloadTask,
        *,
        kind: str,
        size_mb: float | None = None,
        extra: list[str] | None = None,
        note: str | None = None,
    ) -> str:
        details = list(extra or [])
        if task.info.get("uploader"):
            details.insert(0, str(task.info["uploader"]))
        duration = format_duration(task.info.get("duration"))
        if duration and kind in {"video", "audio"}:
            details.append(duration)
        return media_caption(
            task.title,
            task.url,
            kind=kind,
            size_mb=size_mb,
            details=details,
            bot_username=await self.telegram.username(),
            note=note,
        )

    async def _set_phase(self, task: DownloadTask, status: TaskStatus, phase: str) -> None:
        if task.cancel_event.is_set():
            raise DownloadCancelled("Загрузка отменена пользователем")
        task.status = status
        task.phase = phase
        task.phase_started_at = time.time()
        if not task.silent:
            await self.update_progress(task)

    async def _video_or_audio(self, task: DownloadTask, work_dir: Path) -> None:
        source = await self.downloader.download(task, work_dir)
        if task.cancel_event.is_set():
            raise DownloadCancelled("Загрузка отменена пользователем")

        if task.action == DownloadAction.GIF:
            await self._set_phase(task, TaskStatus.PROCESSING, "Создание GIF")
            result = await convert_gif(self.settings, task, source, work_dir / "animation.gif")
            await self._set_phase(task, TaskStatus.UPLOADING, "Отправка GIF")
            size_mb = result.stat().st_size / (1024 * 1024)
            caption = await self._caption(task, kind="gif", size_mb=size_mb)
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
        await self._set_phase(task, TaskStatus.UPLOADING, f"Отправка файла · {size_mb:.1f} МБ")
        effect, note = await self._milestone(task)
        if task.action == DownloadAction.AUDIO:
            caption = await self._caption(
                task, kind="audio", size_mb=size_mb, extra=["MP3"], note=note
            )
            try:
                await self.telegram.send_audio(
                    task.chat_id,
                    source,
                    caption=caption,
                    title=task.title,
                    performer=task.info.get("uploader"),
                    reply_to=task.reply_to_message_id,
                    duration=self._duration(task.info),
                    effect=effect,
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

        metadata = await probe_video(source)
        quality = []
        if metadata.get("height") and metadata.get("width"):
            quality.append(f"{min(metadata['height'], metadata['width'])}p")
        caption = await self._caption(task, kind="video", size_mb=size_mb, extra=quality, note=note)
        if source.stat().st_size > self.settings.effective_send_as_doc_limit:
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
                    effect=effect,
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
        caption = await self._caption(task, kind="subtitles")
        for index, path in enumerate(files):
            if task.cancel_event.is_set():
                raise DownloadCancelled("Загрузка отменена пользователем")
            if path.stat().st_size > self.settings.effective_upload_limit:
                raise FileTooLarge("Файл субтитров превышает лимит Telegram")
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
            caption=await self._caption(task, kind="thumbnail", size_mb=size_mb),
            reply_to=task.reply_to_message_id,
            filename=f"{sanitize_filename(task.title) or 'thumbnail'}{path.suffix}",
        )
        await self._record(task, "thumbnail", size_mb)

    async def _tiktok_photos(self, task: DownloadTask, work_dir: Path) -> None:
        await self._set_phase(task, TaskStatus.DOWNLOADING, "Скачивание фото TikTok")
        elapsed = max(0.0, time.time() - task.started_at) if task.started_at else 0.0
        remaining = self.settings.download_timeout_seconds - elapsed
        if remaining <= 0:
            raise TimeoutError("TikTok photo download timeout")
        photos = await asyncio.to_thread(
            download_tiktok_photos,
            task.url,
            work_dir,
            cancel_event=task.cancel_event,
            timeout=max(1, round(min(remaining, 300))),
        )
        if task.cancel_event.is_set():
            raise DownloadCancelled("Загрузка отменена пользователем")
        if any(path.stat().st_size > self.settings.effective_upload_limit for path in photos):
            raise FileTooLarge("Одно из фото превышает лимит Telegram")
        await self._set_phase(task, TaskStatus.UPLOADING, "Отправка фото")
        caption = media_caption(
            f"Фото из TikTok · {len(photos)} шт.",
            task.url,
            kind="tiktok_photo",
            bot_username=await self.telegram.username(),
        )
        for offset in range(0, len(photos), 10):
            if task.cancel_event.is_set():
                raise DownloadCancelled("Загрузка отменена пользователем")
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

    @staticmethod
    def _duration(info: dict) -> int | None:
        value = positive_seconds(info.get("duration"))
        return value or None

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
