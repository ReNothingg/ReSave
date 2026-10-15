from __future__ import annotations

import asyncio
import logging
import shutil
import time
from pathlib import Path
from typing import Any

from config import Settings

from .errors import DownloadCancelled, FileTooLarge
from .isolated import JobFailed, run_job
from .media_tools import media_tool
from .models import DownloadAction, DownloadTask, DownloadVariant, TaskStatus
from .processes import run_command
from .tiktok_fallback import (
    TikTokFallbackError,
    canonical_tiktok_url,
    download_tiktok_video,
    fetch_tiktok_video_info,
    is_tiktok_url,
    tiktok_video_id,
)

logger = logging.getLogger(__name__)
POSTPROCESSORS = {
    "Merger": "Склеиваю видео и звук",
    "VideoRemuxer": "Упаковываю в MP4",
    "VideoConvertor": "Конвертирую видео",
    "ExtractAudio": "Конвертирую в MP3",
    "FixupM3u8": "Чиню поток",
    "FixupM4a": "Чиню звук",
    "FixupStretched": "Исправляю пропорции",
    "MoveFiles": "-",
}
PERMANENT_ERRORS = (
    "private",
    "unsupported url",
    "not a valid url",
    "copyright",
    "sign in",
    "login required",
    "video unavailable",
    "404",
    "not available in your country",
    "no space",
    "disk quota",
    "ffmpeg is not installed",
)


class MediaDownloader:
    def __init__(self, settings: Settings):
        self.settings = settings

    @property
    def ffmpeg_available(self) -> bool:
        return media_tool("ffmpeg") is not None

    def _remaining(self, task: DownloadTask) -> float:
        elapsed = time.time() - task.started_at if task.started_at else 0.0
        remaining = self.settings.download_timeout_seconds - elapsed
        if remaining <= 0:
            raise TimeoutError("Download timeout")
        return remaining

    def _check_disk(self, path: Path) -> None:
        if shutil.disk_usage(path).free < self.settings.min_free_disk_bytes:
            raise OSError("No space left for media processing")

    def _job(self, op: str, task: DownloadTask, work_dir: Path, **extra: Any) -> dict[str, Any]:
        cookies = self.settings.cookies_file
        return {
            "op": op,
            "url": task.url,
            "work_dir": str(work_dir),
            "cookies": str(cookies) if cookies.is_file() else None,
            "rate_limit": self.settings.download_rate_limit_bytes,
            "min_free": self.settings.min_free_disk_bytes,
            **extra,
        }

    async def _run(self, task: DownloadTask, job: dict[str, Any], *, stall: bool = True):
        self._check_disk(Path(job["work_dir"]))
        return await run_job(
            job,
            deadline=self._remaining(task),
            stall_timeout=self.settings.download_stall_timeout_seconds if stall else None,
            cancel_event=task.cancel_event,
            on_event=lambda event: self._apply_event(task, event),
        )

    def _apply_event(self, task: DownloadTask, event: dict[str, Any]) -> None:
        if event.get("event") == "phase":
            name = POSTPROCESSORS.get(str(event.get("name") or ""), "Обрабатываю файл")
            if task.status != TaskStatus.PROCESSING:
                task.phase_started_at = time.time()
                task.status = TaskStatus.PROCESSING
            if name != "-":
                task.phase = name
            task.progress = max(task.progress, 0.99)
            task.speed = 0.0
            task.eta = None
            return
        if event.get("event") != "progress":
            return
        downloaded = int(event.get("downloaded") or 0)
        total = int(event.get("total") or 0)
        task.downloaded_bytes = downloaded
        task.total_bytes = total
        if total:
            task.progress = min(0.99, max(task.progress, downloaded / total))
        speed = float(event.get("speed") or 0)
        if speed:
            task.speed = speed if not task.speed else task.speed * 0.6 + speed * 0.4
        task.eta = round((total - downloaded) / task.speed) if total and task.speed else None
        task.stream = event.get("stream")
        task.stream_index = int(event.get("stream_index") or 1)
        task.streams = int(event.get("streams") or 1)
        fragments = (event.get("fragment"), event.get("fragments"))
        task.fragments = (int(fragments[0]), int(fragments[1])) if all(fragments) else None
        task.phase = "Скачивание"

    async def download(self, task: DownloadTask, work_dir: Path) -> Path:
        task.work_dir = str(work_dir)
        if task.info.get("_tiktok_fallback"):
            return await self._download_tiktok(task, work_dir)
        errors: list[str] = []
        state = {"too_large": False, "tiktok_tried": False}
        for variant in self.variants(task, work_dir / "media"):
            result = await self._attempt(task, work_dir, variant, errors, state)
            if result is not None:
                return result
        if state["too_large"]:
            raise FileTooLarge("Все доступные варианты превышают лимит Telegram")
        raise RuntimeError(f"Не удалось скачать медиа: {errors[-1] if errors else 'нет вариантов'}")

    async def _attempt(
        self,
        task: DownloadTask,
        work_dir: Path,
        variant: DownloadVariant,
        errors: list[str],
        state: dict[str, bool],
    ) -> Path | None:
        if task.cancel_event.is_set():
            raise DownloadCancelled("Загрузка отменена пользователем")
        self._clear(work_dir)
        task.phase = f"Скачивание: {variant.label}"
        task.status = TaskStatus.DOWNLOADING
        task.progress = 0.0
        task.downloaded_bytes = task.total_bytes = 0
        task.speed = 0.0
        task.eta = None
        task.phase_started_at = time.time()
        logger.info(
            "Downloading task=%s action=%s variant=%s", task.task_id, task.action, variant.label
        )
        try:
            return await self._download_with_retry(task, work_dir, variant)
        except (DownloadCancelled, TimeoutError):
            raise
        except FileTooLarge as exc:
            logger.info("Variant too large task=%s %s: %s", task.task_id, variant.label, exc)
            errors.append(f"{variant.label}: {exc}")
            state["too_large"] = True
            if variant.label.endswith("p"):
                task.oversized.append(variant.label)
            return None
        except Exception as exc:
            logger.warning("Variant failed task=%s %s: %s", task.task_id, variant.label, exc)
            errors.append(f"{variant.label}: {exc}")
            if not state["tiktok_tried"] and self._tiktok_id(task, exc):
                state["tiktok_tried"] = True
                result = await self._try_tiktok_fallback(task, work_dir, exc, errors)
                if result is not None:
                    return result
            if not self._can_fallback(exc):
                raise
            return None

    async def _download_with_retry(
        self, task: DownloadTask, work_dir: Path, variant: DownloadVariant
    ) -> Path:
        try:
            return await self._download_variant(task, work_dir, variant)
        except JobFailed as exc:
            if "403" not in str(exc):
                raise
            logger.info("Retrying variant after 403 task=%s %s", task.task_id, variant.label)
            self._clear(work_dir)
            return await self._download_variant(task, work_dir, variant)

    async def _download_variant(
        self, task: DownloadTask, work_dir: Path, variant: DownloadVariant
    ) -> Path:
        event = await self._run(
            task,
            self._job(
                "download",
                task,
                work_dir,
                format=variant.format_selector,
                outtmpl=variant.output_template,
                postprocessors=list(variant.postprocessors),
                audio=task.action == DownloadAction.AUDIO,
                size_limit=self.settings.effective_upload_limit,
            ),
        )
        task.progress = 1.0
        return self._validate_result_size(Path(event["path"]))

    def _tiktok_id(self, task: DownloadTask, exc: BaseException) -> str | None:
        if not is_tiktok_url(task.url):
            return None
        return tiktok_video_id(task.url, str(exc), task.info.get("id"))

    async def _try_tiktok_fallback(
        self, task: DownloadTask, work_dir: Path, original: Exception, errors: list[str]
    ) -> Path | None:
        task.info["id"] = self._tiktok_id(task, original)
        self._clear(work_dir)
        try:
            return await self._download_tiktok(task, work_dir)
        except (DownloadCancelled, TimeoutError, FileTooLarge):
            raise
        except Exception as exc:
            logger.warning("TikTok fallback failed task=%s: %s", task.task_id, exc)
            errors.append(f"TikTok fallback: {exc}")
            return None

    async def _download_tiktok(self, task: DownloadTask, work_dir: Path) -> Path:
        logger.info("Using gallery-dl TikTok fallback task=%s", task.task_id)
        self._check_disk(work_dir)
        result = await asyncio.to_thread(
            self._download_tiktok_sync, task, work_dir, round(self._remaining(task))
        )
        task.progress = 1.0
        return self._validate_result_size(result)

    def _download_tiktok_sync(self, task: DownloadTask, work_dir: Path, timeout: int) -> Path:
        try:
            source = download_tiktok_video(
                task.url,
                work_dir,
                known_id=task.info.get("id"),
                cancel_event=task.cancel_event,
                timeout=timeout,
            )
        except TikTokFallbackError as exc:
            if task.cancel_event.is_set():
                raise DownloadCancelled("Загрузка отменена пользователем") from exc
            raise
        if task.action != DownloadAction.AUDIO:
            return source
        ffmpeg = media_tool("ffmpeg")
        if not ffmpeg:
            raise RuntimeError("FFmpeg is required to extract TikTok audio")
        target = work_dir / "media.mp3"
        result = run_command(
            [
                ffmpeg,
                "-hide_banner",
                "-loglevel",
                "error",
                "-i",
                str(source),
                "-vn",
                "-codec:a",
                "libmp3lame",
                "-b:a",
                "192k",
                "-y",
                str(target),
            ],
            cancel_event=task.cancel_event,
            deadline_seconds=min(timeout, 300),
        )
        if result.returncode:
            raise RuntimeError(f"FFmpeg conversion failed: {result.stderr[-1000:]}")
        source.unlink(missing_ok=True)
        return target

    async def download_subtitles(self, task: DownloadTask, work_dir: Path) -> list[Path]:
        task.work_dir = str(work_dir)
        task.phase = "Скачивание субтитров"
        event = await self._run(
            task,
            self._job(
                "subtitles",
                task,
                work_dir,
                languages=task.info.get("subtitle_languages") or ["ru", "en"],
            ),
            stall=False,
        )
        return [Path(path) for path in event["paths"]]

    async def download_thumbnail(self, task: DownloadTask, work_dir: Path) -> Path:
        task.work_dir = str(work_dir)
        task.phase = "Скачивание превью"
        if task.info.get("_tiktok_fallback"):
            return await self._tiktok_thumbnail(task, work_dir)
        try:
            event = await self._run(task, self._job("thumbnail", task, work_dir), stall=False)
        except (DownloadCancelled, FileTooLarge, TimeoutError):
            raise
        except Exception as exc:
            if not self._tiktok_id(task, exc):
                raise
            task.info["id"] = self._tiktok_id(task, exc)
            return await self._tiktok_thumbnail(task, work_dir)
        return self._validate_result_size(Path(event["path"]))

    async def _tiktok_thumbnail(self, task: DownloadTask, work_dir: Path) -> Path:
        video_id = tiktok_video_id(task.url, known_id=task.info.get("id"))
        if not video_id:
            raise FileNotFoundError("Не удалось определить ID видео TikTok")
        info = await asyncio.to_thread(fetch_tiktok_video_info, canonical_tiktok_url(video_id))
        if not info.get("thumbnail"):
            raise FileNotFoundError("Превью TikTok недоступно")
        event = await self._run(
            task,
            self._job("thumbnail", task, work_dir, thumbnail_url=info["thumbnail"]),
            stall=False,
        )
        return self._validate_result_size(Path(event["path"]))

    def _validate_result_size(self, path: Path) -> Path:
        if path.stat().st_size > self.settings.effective_upload_limit:
            raise FileTooLarge("Файл превышает лимит Telegram")
        return path

    @staticmethod
    def _clear(directory: Path) -> None:
        for item in directory.iterdir():
            if item.is_dir():
                shutil.rmtree(item, ignore_errors=True)
            else:
                item.unlink(missing_ok=True)

    @staticmethod
    def _can_fallback(exc: BaseException) -> bool:
        if isinstance(exc, JobFailed) and "worker exited with code -9" in str(exc):
            return True
        value = str(exc).lower()
        return not any(marker in value for marker in PERMANENT_ERRORS)

    @staticmethod
    def _height_selector(height: int, *, exact: bool = False, allow_non_h264: bool = True) -> str:
        op = "=" if exact else "<="
        if not media_tool("ffmpeg"):
            return f"b[height{op}{height}][ext=mp4]/best[height{op}{height}][ext=mp4]"
        compatible = (
            f"bv*[height{op}{height}][vcodec^=avc1]+ba[acodec^=mp4a]/"
            f"b[height{op}{height}][vcodec^=avc1]"
        )
        if not allow_non_h264:
            return compatible
        return f"{compatible}/bv*[height{op}{height}]+ba/b[height{op}{height}]"

    def variants(self, task: DownloadTask, output: Path) -> list[DownloadVariant]:
        template = f"{output}.%(ext)s"
        if task.action == DownloadAction.AUDIO:
            return [
                DownloadVariant(
                    label="MP3 192 kbps",
                    format_selector="bestaudio/best",
                    output_template=template,
                    postprocessors=(
                        {
                            "key": "FFmpegExtractAudio",
                            "preferredcodec": "mp3",
                            "preferredquality": "192",
                        },
                    ),
                )
            ]
        variants = [
            DownloadVariant(
                label=f"{height}p",
                format_selector=self._height_selector(
                    height,
                    exact=task.action == DownloadAction.RESOLUTION and index == 0,
                    allow_non_h264=(
                        index == 0
                        or task.action not in {DownloadAction.BEST, DownloadAction.RESOLUTION}
                    ),
                ),
                output_template=template,
            )
            for index, height in enumerate(self._candidate_heights(task))
        ]
        if task.action != DownloadAction.RESOLUTION:
            variants.append(
                DownloadVariant(
                    label="любой доступный формат",
                    format_selector="bv*+ba/b" if media_tool("ffmpeg") else "b",
                    output_template=template,
                )
            )
        return variants

    @staticmethod
    def _candidate_heights(task: DownloadTask) -> list[int]:
        if task.action == DownloadAction.RESOLUTION and task.requested_height:
            heights = [task.requested_height]
            heights.extend(
                height
                for height in (2160, 1440, 1080, 720, 480, 360)
                if height < task.requested_height
            )
        elif task.action in {DownloadAction.LOW, DownloadAction.GIF}:
            heights = [480, 360]
        elif task.action == DownloadAction.MEDIUM:
            heights = [720, 480, 360]
        else:
            available: set[int] = set()
            for item in task.info.get("formats") or []:
                if not isinstance(item, dict) or item.get("vcodec", "none") == "none":
                    continue
                try:
                    height = int(item.get("height"))
                except (TypeError, ValueError):
                    continue
                if height > 0:
                    available.add(height)
            heights = sorted(available, reverse=True) or [2160, 1440, 1080, 720, 480, 360]
        return list(dict.fromkeys(height for height in heights if height > 0))
