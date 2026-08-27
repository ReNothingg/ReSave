from __future__ import annotations

import asyncio
import json
import logging
import os
import re
import shutil
import subprocess
import time
from functools import lru_cache
from pathlib import Path
from typing import Any

import yt_dlp
from yt_dlp.networking import Request
from yt_dlp.utils import determine_ext

from config import Settings

from .models import DownloadAction, DownloadTask, DownloadVariant

logger = logging.getLogger(__name__)


@lru_cache(maxsize=2)
def _media_tool(name: str) -> str | None:
    discovered = shutil.which(name)
    if discovered:
        return discovered

    candidates: list[Path] = []
    configured = os.getenv("FFMPEG_LOCATION", "").strip()
    if configured:
        location = Path(configured).expanduser()
        candidates.append(location / name if location.is_dir() else location.with_name(name))

    candidates.append(Path.home() / ".local" / "bin" / name)
    ffmpeg_home = Path.home() / "ffmpeg"
    if ffmpeg_home.is_dir():
        candidates.extend(sorted(ffmpeg_home.glob(f"*/{name}"), reverse=True))

    for candidate in candidates:
        if candidate.is_file() and os.access(candidate, os.X_OK):
            return str(candidate)
    return None


class DownloadCancelled(RuntimeError):
    pass


class FileTooLarge(RuntimeError):
    pass


class MediaDownloader:
    def __init__(self, settings: Settings):
        self.settings = settings

    def _common_options(self) -> dict[str, Any]:
        options: dict[str, Any] = {
            "quiet": True,
            "no_warnings": True,
            "noprogress": True,
            "noplaylist": True,
            "socket_timeout": 30,
            "retries": 3,
            "fragment_retries": 3,
            "extractor_retries": 3,
            "file_access_retries": 3,
            "continuedl": True,
            "overwrites": True,
            "trim_file_name": 180,
            "buffersize": 64 * 1024,
            "noresizebuffer": True,
            "http_chunk_size": 5 * 1024 * 1024,
            "concurrent_fragment_downloads": 1,
            # Shared hosts often advertise IPv6 even when the route is unstable.
            # A failed IPv6 route commonly surfaces in yt-dlp as HTTP 403.
            "source_address": "0.0.0.0",
            "http_headers": {
                "Accept-Language": "ru-RU,ru;q=0.9,en;q=0.8",
            },
        }
        if self.settings.cookies_file.is_file():
            options["cookiefile"] = str(self.settings.cookies_file)
        if self.settings.download_rate_limit_bytes:
            options["ratelimit"] = self.settings.download_rate_limit_bytes
        ffmpeg = _media_tool("ffmpeg")
        if ffmpeg:
            options["ffmpeg_location"] = str(Path(ffmpeg).parent)
            # yt-dlp's native HLS downloader may create an empty file for some
            # Pinterest/CDN manifests with conflicting byte ranges. ffmpeg handles
            # those manifests correctly and also merges separate audio/video tracks.
            options["external_downloader"] = {"m3u8": "ffmpeg"}
        return options

    def _size_filter(self, info: dict[str, Any], *, incomplete: bool) -> str | None:
        if incomplete:
            return None
        formats = info.get("requested_formats") or [info]
        estimates: list[int] = []
        for media_format in formats:
            size = media_format.get("filesize") or media_format.get("filesize_approx")
            if not size:
                duration = media_format.get("duration") or info.get("duration")
                bitrate = (
                    media_format.get("tbr") or media_format.get("vbr") or media_format.get("abr")
                )
                if duration and bitrate:
                    size = float(duration) * float(bitrate) * 1000 / 8
            if size:
                estimates.append(round(float(size)))

        estimated_size = sum(estimates) or info.get("filesize") or info.get("filesize_approx")
        safe_limit = max(1, round(self.settings.effective_upload_limit * 0.97) - 1024 * 1024)
        if estimated_size and estimated_size > safe_limit:
            return (
                f"Estimated merged file size {estimated_size} exceeds safe Telegram limit "
                f"{safe_limit}"
            )
        return None

    @staticmethod
    def _clear(directory: Path) -> None:
        for item in directory.iterdir():
            if item.is_dir():
                shutil.rmtree(item, ignore_errors=True)
            else:
                item.unlink(missing_ok=True)

    @staticmethod
    def _files(directory: Path) -> list[Path]:
        ignored = {".part", ".tmp", ".ytdl", ".json"}
        return [
            item
            for item in directory.rglob("*")
            if item.is_file() and item.suffix.lower() not in ignored
        ]

    @staticmethod
    def _is_intermediate_stream(path: Path) -> bool:
        return bool(re.search(r"\.f(?:\d+|audio|video)(?:-[^.]+)?\.", path.name))

    @staticmethod
    def _stream_types(path: Path) -> set[str]:
        ffprobe = _media_tool("ffprobe")
        if not ffprobe:
            return set()
        try:
            result = subprocess.run(
                [
                    ffprobe,
                    "-v",
                    "error",
                    "-show_entries",
                    "stream=codec_type",
                    "-of",
                    "json",
                    str(path),
                ],
                check=True,
                capture_output=True,
                text=True,
                timeout=15,
            )
            payload = json.loads(result.stdout or "{}")
        except (OSError, subprocess.SubprocessError, ValueError):
            return set()
        return {
            str(stream["codec_type"])
            for stream in payload.get("streams") or []
            if stream.get("codec_type")
        }

    @staticmethod
    def _image_dimensions(path: Path) -> tuple[int, int]:
        ffprobe = _media_tool("ffprobe")
        if not ffprobe:
            return (0, 0)
        try:
            result = subprocess.run(
                [
                    ffprobe,
                    "-v",
                    "error",
                    "-show_entries",
                    "stream=width,height",
                    "-of",
                    "json",
                    str(path),
                ],
                check=True,
                capture_output=True,
                text=True,
                timeout=15,
            )
            streams = json.loads(result.stdout or "{}").get("streams") or []
            if streams:
                return (int(streams[0].get("width") or 0), int(streams[0].get("height") or 0))
        except (OSError, subprocess.SubprocessError, TypeError, ValueError):
            pass
        return (0, 0)

    def _select_completed_media(self, task: DownloadTask, work_dir: Path) -> Path:
        files = [path for path in self._files(work_dir) if not self._is_intermediate_stream(path)]
        if not files:
            raise FileNotFoundError("yt-dlp не создал итоговый медиафайл")

        preferred_suffix = ".mp3" if task.action == DownloadAction.AUDIO else ".mp4"
        files.sort(
            key=lambda path: (path.suffix.lower() == preferred_suffix, path.stat().st_mtime),
            reverse=True,
        )
        required_stream = "audio" if task.action == DownloadAction.AUDIO else "video"
        for path in files:
            stream_types = self._stream_types(path)
            # A broken or unavailable ffprobe must not discard a completed file.
            # Intermediate video/audio tracks are filtered out above already.
            if not stream_types or required_stream in stream_types:
                return path
        raise FileNotFoundError(f"yt-dlp не создал итоговый файл с потоком типа {required_stream}")

    def _progress_hook(self, task: DownloadTask):
        started = time.monotonic()
        deadline = started + self.settings.download_timeout_seconds
        last_activity = started
        last_bytes = -1

        def hook(data: dict[str, Any]) -> None:
            nonlocal last_activity, last_bytes
            now = time.monotonic()
            if task.cancel_event.is_set():
                raise DownloadCancelled("Загрузка отменена пользователем")
            if now >= deadline:
                raise TimeoutError("Download timeout")

            if data.get("status") == "downloading":
                downloaded = int(data.get("downloaded_bytes") or 0)
                total = data.get("total_bytes") or data.get("total_bytes_estimate")
                if downloaded != last_bytes:
                    last_bytes = downloaded
                    last_activity = now
                if total:
                    task.progress = min(0.99, max(0.0, downloaded / int(total)))
                task.speed = data.get("_speed_str") or None
                eta = data.get("eta")
                task.eta = int(eta) if isinstance(eta, (int, float)) else None
                task.phase = "Скачивание"
            elif data.get("status") == "finished":
                task.progress = 0.99
                task.phase = "Обработка"
                last_activity = now

            if now - last_activity >= self.settings.download_stall_timeout_seconds:
                raise TimeoutError("Download made no progress")

        return hook

    def _download_sync(self, task: DownloadTask, variant: DownloadVariant) -> Path:
        options = self._common_options()
        options.update(
            {
                "format": variant.format_selector,
                "outtmpl": variant.output_template,
                "merge_output_format": "mp4",
                "progress_hooks": [self._progress_hook(task)],
                "match_filter": self._size_filter,
            }
        )
        postprocessors = list(variant.postprocessors)
        if task.action != DownloadAction.AUDIO and _media_tool("ffmpeg"):
            postprocessors.append({"key": "FFmpegVideoRemuxer", "preferedformat": "mp4"})
        if postprocessors:
            options["postprocessors"] = postprocessors

        with yt_dlp.YoutubeDL(options) as ydl:
            ydl.download([task.url])

        work_dir = Path(task.work_dir or "")
        result = self._select_completed_media(task, work_dir)
        if result.stat().st_size > self.settings.effective_upload_limit:
            raise FileTooLarge(
                f"File size {result.stat().st_size} exceeds Telegram limit "
                f"{self.settings.effective_upload_limit}"
            )
        return result

    async def download(self, task: DownloadTask, work_dir: Path) -> Path:
        task.work_dir = str(work_dir)
        errors: list[str] = []
        for variant in self.variants(task, work_dir / "media"):
            if task.cancel_event.is_set():
                raise DownloadCancelled("Загрузка отменена пользователем")
            self._clear(work_dir)
            task.phase = f"Скачивание: {variant.label}"
            task.progress = 0.0
            logger.info(
                "Downloading task=%s action=%s variant=%s",
                task.task_id,
                task.action,
                variant.label,
            )
            try:
                result = await asyncio.to_thread(self._download_sync, task, variant)
                task.progress = 1.0
                return result
            except DownloadCancelled:
                raise
            except Exception as exc:
                errors.append(f"{variant.label}: {exc}")
                logger.warning("Variant failed task=%s: %s", task.task_id, errors[-1])
                if not self._can_fallback(exc):
                    raise

        message = errors[-1] if errors else "нет доступных вариантов"
        raise RuntimeError(f"Не удалось скачать медиа: {message}")

    def _download_subtitles_sync(self, task: DownloadTask, work_dir: Path) -> list[Path]:
        options = self._common_options()
        options.update(
            {
                "skip_download": True,
                "writesubtitles": True,
                "writeautomaticsub": True,
                "subtitleslangs": ["ru", "en", "ru-orig", "en-orig"],
                "subtitlesformat": "srt/best",
                "outtmpl": str(work_dir / "subtitle.%(ext)s"),
                "progress_hooks": [self._progress_hook(task)],
            }
        )
        with yt_dlp.YoutubeDL(options) as ydl:
            ydl.download([task.url])
        return sorted(path for path in work_dir.glob("*.srt") if path.is_file())

    async def download_subtitles(self, task: DownloadTask, work_dir: Path) -> list[Path]:
        task.work_dir = str(work_dir)
        task.phase = "Скачивание субтитров"
        files = await asyncio.to_thread(self._download_subtitles_sync, task, work_dir)
        if not files:
            raise FileNotFoundError("Для этого видео не удалось получить субтитры")
        return files

    def _download_thumbnail_sync(self, task: DownloadTask, work_dir: Path) -> Path:
        options = self._common_options()
        options["skip_download"] = True
        downloaded: list[tuple[Path, int]] = []

        with yt_dlp.YoutubeDL(options) as ydl:
            info = ydl.extract_info(task.url, download=False)
            thumbnails = list((info or {}).get("thumbnails") or [])

            def metadata_rank(
                index_and_thumbnail: tuple[int, dict[str, Any]],
            ) -> tuple[float, int, int]:
                index, thumbnail = index_and_thumbnail
                try:
                    preference = float(thumbnail.get("preference") or 0)
                except (TypeError, ValueError):
                    preference = 0
                width = int(thumbnail.get("width") or 0)
                height = int(thumbnail.get("height") or 0)
                return (preference, width * height, index)

            # The highest-ranked URL is not always the highest-quality image in practice.
            # Compare a small group of source files using their real pixel dimensions.
            candidates = sorted(enumerate(thumbnails), key=metadata_rank, reverse=True)[:8]
            for output_index, (_, thumbnail) in enumerate(candidates):
                if task.cancel_event.is_set():
                    raise DownloadCancelled("Загрузка отменена пользователем")
                url = thumbnail.get("url")
                if not url:
                    continue
                extension = determine_ext(url, "jpg").lower()
                if extension not in {"avif", "jpeg", "jpg", "png", "webp"}:
                    extension = "jpg"
                path = work_dir / f"thumbnail-{output_index}.{extension}"
                headers = thumbnail.get("http_headers") or (info or {}).get("http_headers") or {}
                try:
                    response = ydl.urlopen(Request(url, headers=headers))
                    try:
                        with path.open("wb") as output:
                            shutil.copyfileobj(response, output)
                    finally:
                        response.close()
                except Exception as exc:
                    path.unlink(missing_ok=True)
                    logger.debug("Thumbnail candidate failed: %s", exc)
                    continue
                metadata_area = int(thumbnail.get("width") or 0) * int(thumbnail.get("height") or 0)
                downloaded.append((path, metadata_area))

        if not downloaded:
            raise FileNotFoundError("Превью недоступно")

        def quality_rank(candidate: tuple[Path, int]) -> tuple[int, int, int]:
            path, metadata_area = candidate
            width, height = self._image_dimensions(path)
            actual_area = width * height
            return (actual_area or metadata_area, path.stat().st_size, metadata_area)

        return max(downloaded, key=quality_rank)[0]

    async def download_thumbnail(self, task: DownloadTask, work_dir: Path) -> Path:
        task.work_dir = str(work_dir)
        task.phase = "Скачивание превью"
        return await asyncio.to_thread(self._download_thumbnail_sync, task, work_dir)

    @staticmethod
    def _can_fallback(exc: BaseException) -> bool:
        value = str(exc).lower()
        permanent = (
            "private",
            "unsupported url",
            "not a valid url",
            "copyright",
            "sign in",
            "login required",
            "video unavailable",
            "403",
            "404",
        )
        return not any(marker in value for marker in permanent)

    @staticmethod
    def _height_selector(
        height: int,
        *,
        exact: bool = False,
        allow_non_h264: bool = True,
    ) -> str:
        comparator = "=" if exact else "<="
        if not _media_tool("ffmpeg"):
            return (
                f"b[height{comparator}{height}][ext=mp4]/best[height{comparator}{height}][ext=mp4]"
            )
        compatible = (
            f"bv*[height{comparator}{height}][vcodec^=avc1]+ba[acodec^=mp4a]/"
            f"b[height{comparator}{height}][vcodec^=avc1]"
        )
        if not allow_non_h264:
            return compatible
        # Exact user choices may use AV1/VP9; the result is remuxed to MP4.
        return f"{compatible}/bv*[height{comparator}{height}]+ba/b[height{comparator}{height}]"

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
            available = sorted(
                {
                    int(item["height"])
                    for item in task.info.get("formats") or []
                    if item.get("height") and item.get("vcodec", "none") != "none"
                },
                reverse=True,
            )
            heights = available or [2160, 1440, 1080, 720, 480, 360]

        seen: set[int] = set()
        variants: list[DownloadVariant] = []
        for index, height in enumerate(heights):
            if height in seen or height <= 0:
                continue
            seen.add(height)
            variants.append(
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
            )
        if _media_tool("ffmpeg"):
            variants.append(
                DownloadVariant(
                    label="совместимый формат",
                    format_selector="bv*+ba/b",
                    output_template=template,
                )
            )
        return variants
