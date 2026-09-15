from __future__ import annotations

import asyncio
import json
import logging
import re
import shutil
import subprocess
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yt_dlp
from yt_dlp.networking import Request
from yt_dlp.utils import determine_ext

from config import Settings

from .errors import DownloadCancelled, FileTooLarge
from .extractor import youtube_client
from .media_tools import media_tool as _media_tool
from .models import DownloadAction, DownloadTask, DownloadVariant
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
MIN_FREE_SPACE_BYTES = 64 * 1024 * 1024


@dataclass(slots=True)
class _ProgressState:
    deadline: float
    last_activity: float
    last_bytes: int = -1
    next_resource_check: float = 0.0


@dataclass(frozen=True, slots=True)
class _VariantAttempt:
    result: Path | None = None
    errors: tuple[str, ...] = ()
    too_large: bool = False
    used_tiktok_fallback: bool = False


class MediaDownloader:
    def __init__(self, settings: Settings):
        self.settings = settings

    @property
    def ffmpeg_available(self) -> bool:
        return _media_tool("ffmpeg") is not None

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
            options["external_downloader"] = {"m3u8": "ffmpeg"}
        return options

    def _size_filter(self, info: dict[str, Any], *, incomplete: bool) -> str | None:
        if incomplete:
            return None
        formats = [
            item for item in info.get("requested_formats") or [info] if isinstance(item, dict)
        ]
        estimated_size = sum(self._estimated_format_size(item, info) for item in formats)
        if not estimated_size:
            estimated_size = self._number(info.get("filesize") or info.get("filesize_approx"))
        safe_limit = max(1, round(self.settings.effective_upload_limit * 0.97) - 1024 * 1024)
        if estimated_size and estimated_size > safe_limit:
            return (
                f"Estimated merged file size {estimated_size} exceeds safe Telegram limit "
                f"{safe_limit}"
            )
        return None

    @classmethod
    def _estimated_format_size(cls, media_format: dict[str, Any], info: dict[str, Any]) -> int:
        explicit = cls._number(media_format.get("filesize") or media_format.get("filesize_approx"))
        if explicit:
            return round(explicit)
        duration = cls._number(media_format.get("duration") or info.get("duration"))
        bitrate = cls._number(
            media_format.get("tbr") or media_format.get("vbr") or media_format.get("abr")
        )
        return round(duration * bitrate * 1000 / 8) if duration and bitrate else 0

    @staticmethod
    def _number(value: object) -> float:
        try:
            parsed = float(value)
        except (OverflowError, TypeError, ValueError):
            return 0.0
        return parsed if 0 < parsed < float("inf") else 0.0

    @staticmethod
    def _clear(directory: Path) -> None:
        for item in directory.iterdir():
            if item.is_dir():
                shutil.rmtree(item, ignore_errors=True)
            else:
                item.unlink(missing_ok=True)

    def _check_work_directory(self, task: DownloadTask) -> None:
        if not task.work_dir:
            return
        work_dir = Path(task.work_dir)
        if shutil.disk_usage(work_dir).free < MIN_FREE_SPACE_BYTES:
            raise OSError("No space left for media processing")
        total_size = 0
        for path in work_dir.rglob("*"):
            try:
                if path.is_file():
                    total_size += path.stat().st_size
            except FileNotFoundError:
                continue
        if total_size > self.settings.effective_upload_limit * 3:
            raise FileTooLarge("Temporary files exceed the safe processing limit")

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
        if not isinstance(payload, dict):
            return set()
        streams = payload.get("streams")
        if not isinstance(streams, list):
            return set()
        return {
            str(stream["codec_type"])
            for stream in streams
            if isinstance(stream, dict) and stream.get("codec_type")
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
            payload = json.loads(result.stdout or "{}")
            if not isinstance(payload, dict):
                return (0, 0)
            streams = payload.get("streams")
            if isinstance(streams, list) and streams and isinstance(streams[0], dict):
                return (int(streams[0].get("width") or 0), int(streams[0].get("height") or 0))
        except (OSError, subprocess.SubprocessError, TypeError, ValueError):
            return (0, 0)
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
            if not stream_types or required_stream in stream_types:
                return path
        raise FileNotFoundError(f"yt-dlp не создал итоговый файл с потоком типа {required_stream}")

    def _progress_hook(self, task: DownloadTask):
        started = time.monotonic()
        elapsed = max(0.0, time.time() - task.started_at) if task.started_at else 0.0
        state = _ProgressState(
            deadline=started + max(0.0, self.settings.download_timeout_seconds - elapsed),
            last_activity=started,
            next_resource_check=started,
        )

        def hook(data: dict[str, Any]) -> None:
            now = time.monotonic()
            self._validate_progress_state(task, state, now)
            if data.get("status") == "downloading":
                self._update_download_progress(task, state, data, now)
            elif data.get("status") == "finished":
                task.progress = 0.99
                task.phase = "Обработка"
                state.last_activity = now
            if now - state.last_activity >= self.settings.download_stall_timeout_seconds:
                raise TimeoutError("Download made no progress")

        return hook

    def _validate_progress_state(
        self, task: DownloadTask, state: _ProgressState, now: float
    ) -> None:
        if task.cancel_event.is_set():
            raise DownloadCancelled("Загрузка отменена пользователем")
        if now >= state.deadline:
            raise TimeoutError("Download timeout")
        if now >= state.next_resource_check:
            self._check_work_directory(task)
            state.next_resource_check = now + 1

    @staticmethod
    def _update_download_progress(
        task: DownloadTask,
        state: _ProgressState,
        data: dict[str, Any],
        now: float,
    ) -> None:
        downloaded = max(0, round(MediaDownloader._number(data.get("downloaded_bytes"))))
        total = round(
            MediaDownloader._number(data.get("total_bytes") or data.get("total_bytes_estimate"))
        )
        if downloaded != state.last_bytes:
            state.last_bytes = downloaded
            state.last_activity = now
        if total:
            task.progress = min(0.99, max(0.0, downloaded / total))
        task.speed = str(data["_speed_str"]) if data.get("_speed_str") else None
        eta = round(MediaDownloader._number(data.get("eta")))
        task.eta = eta or None
        task.phase = "Скачивание"

    def _download_sync(self, task: DownloadTask, variant: DownloadVariant) -> Path:
        self._check_work_directory(task)
        if (
            task.started_at
            and time.time() - task.started_at >= self.settings.download_timeout_seconds
        ):
            raise TimeoutError("Download timeout")
        options = self._common_options()
        size_rejected = False

        def size_filter(info: dict, *, incomplete: bool) -> str | None:
            nonlocal size_rejected
            reason = self._size_filter(info, incomplete=incomplete)
            size_rejected = size_rejected or reason is not None
            return reason

        options.update(
            {
                "format": variant.format_selector,
                "outtmpl": variant.output_template,
                "merge_output_format": "mp4",
                "progress_hooks": [self._progress_hook(task)],
                "match_filter": size_filter,
            }
        )
        postprocessors = list(variant.postprocessors)
        if task.action != DownloadAction.AUDIO and _media_tool("ffmpeg"):
            postprocessors.append({"key": "FFmpegVideoRemuxer", "preferedformat": "mp4"})
        if postprocessors:
            options["postprocessors"] = postprocessors

        with youtube_client(options) as ydl:
            ydl.download([task.url])

        work_dir = Path(task.work_dir or "")
        try:
            result = self._select_completed_media(task, work_dir)
        except FileNotFoundError as exc:
            if size_rejected:
                raise FileTooLarge("Estimated file size exceeds limit") from exc
            raise
        if result.stat().st_size > self.settings.effective_upload_limit:
            raise FileTooLarge(
                f"File size {result.stat().st_size} exceeds Telegram limit "
                f"{self.settings.effective_upload_limit}"
            )
        return result

    def _download_tiktok_sync(self, task: DownloadTask, work_dir: Path) -> Path:
        try:
            source = download_tiktok_video(
                task.url,
                work_dir,
                known_id=task.info.get("id"),
                cancel_event=task.cancel_event,
                timeout=self.settings.download_timeout_seconds,
            )
        except TikTokFallbackError as exc:
            if task.cancel_event.is_set():
                raise DownloadCancelled("Загрузка отменена пользователем") from exc
            raise
        if task.action != DownloadAction.AUDIO:
            return source

        ffmpeg = _media_tool("ffmpeg")
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
            deadline_seconds=min(self.settings.download_timeout_seconds, 300),
        )
        if result.returncode:
            raise RuntimeError(f"FFmpeg conversion failed: {result.stderr[-1000:]}")
        source.unlink(missing_ok=True)
        return target

    async def download(self, task: DownloadTask, work_dir: Path) -> Path:
        task.work_dir = str(work_dir)
        if task.info.get("_tiktok_fallback"):
            return await self._download_tiktok(task, work_dir)

        errors: list[str] = []
        too_large = False
        tiktok_fallback_attempted = False
        for variant in self.variants(task, work_dir / "media"):
            attempt = await self._attempt_variant(
                task,
                work_dir,
                variant,
                allow_tiktok_fallback=not tiktok_fallback_attempted,
            )
            if attempt.result is not None:
                return attempt.result
            errors.extend(attempt.errors)
            too_large = too_large or attempt.too_large
            tiktok_fallback_attempted = tiktok_fallback_attempted or attempt.used_tiktok_fallback

        if too_large:
            raise FileTooLarge("Все доступные варианты превышают лимит Telegram")
        message = errors[-1] if errors else "нет доступных вариантов"
        raise RuntimeError(f"Не удалось скачать медиа: {message}")

    async def _attempt_variant(
        self,
        task: DownloadTask,
        work_dir: Path,
        variant: DownloadVariant,
        *,
        allow_tiktok_fallback: bool,
    ) -> _VariantAttempt:
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
            return _VariantAttempt(result=await self._download_variant(task, variant))
        except (DownloadCancelled, TimeoutError):
            raise
        except FileTooLarge as exc:
            error = f"{variant.label}: {exc}"
            logger.info("Variant is too large task=%s: %s", task.task_id, error)
            return _VariantAttempt(errors=(error,), too_large=True)
        except Exception as exc:
            errors: list[str] = []
            use_tiktok = self._can_use_tiktok_fallback(task, exc, allow_tiktok_fallback)
            if use_tiktok:
                fallback, fallback_error = await self._try_tiktok_fallback(task, work_dir, exc)
                if fallback is not None:
                    return _VariantAttempt(result=fallback, used_tiktok_fallback=True)
                if fallback_error:
                    errors.append(fallback_error)
            error = f"{variant.label}: {exc}"
            errors.append(error)
            logger.warning("Variant failed task=%s: %s", task.task_id, error)
            if not self._can_fallback(exc):
                raise
            return _VariantAttempt(errors=tuple(errors), used_tiktok_fallback=use_tiktok)

    @staticmethod
    def _can_use_tiktok_fallback(task: DownloadTask, exc: Exception, allowed: bool) -> bool:
        return bool(
            allowed
            and is_tiktok_url(task.url)
            and tiktok_video_id(task.url, str(exc), task.info.get("id"))
        )

    async def _download_variant(self, task: DownloadTask, variant: DownloadVariant) -> Path:
        result = await asyncio.to_thread(self._download_sync, task, variant)
        task.progress = 1.0
        return result

    async def _download_tiktok(self, task: DownloadTask, work_dir: Path) -> Path:
        logger.info("Using gallery-dl TikTok fallback task=%s", task.task_id)
        result = await asyncio.to_thread(self._download_tiktok_sync, task, work_dir)
        self._validate_result_size(result)
        task.progress = 1.0
        return result

    async def _try_tiktok_fallback(
        self,
        task: DownloadTask,
        work_dir: Path,
        original_error: Exception,
    ) -> tuple[Path | None, str | None]:
        task.info["id"] = tiktok_video_id(task.url, str(original_error), task.info.get("id"))
        logger.info("yt-dlp rejected TikTok; using gallery-dl task=%s", task.task_id)
        try:
            return await self._download_tiktok(task, work_dir), None
        except (DownloadCancelled, TimeoutError, FileTooLarge):
            raise
        except Exception as fallback_exc:
            logger.warning("TikTok fallback failed task=%s: %s", task.task_id, fallback_exc)
            return None, f"TikTok fallback: {fallback_exc}"

    def _download_subtitles_sync(self, task: DownloadTask, work_dir: Path) -> list[Path]:
        options = self._common_options()
        options.update(
            {
                "skip_download": True,
                "writesubtitles": True,
                "writeautomaticsub": True,
                "subtitleslangs": task.info.get("subtitle_languages") or ["ru", "en"],
                "subtitlesformat": "srt/best",
                "outtmpl": str(work_dir / "subtitle.%(ext)s"),
                "progress_hooks": [self._progress_hook(task)],
            }
        )
        with youtube_client(options) as ydl:
            ydl.download([task.url])
        extensions = {".srt", ".vtt", ".ttml", ".ass", ".srv3", ".json3"}
        return sorted(
            path for path in work_dir.iterdir() if path.is_file() and path.suffix in extensions
        )

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
        with youtube_client(options) as ydl:
            info = ydl.extract_info(task.url, download=False)
            if not isinstance(info, dict):
                raise FileNotFoundError("Источник не вернул данные превью")
            candidates = self._ranked_thumbnails(info)
            downloaded = self._download_thumbnail_candidates(task, work_dir, ydl, info, candidates)

        if not downloaded:
            raise FileNotFoundError("Превью недоступно")
        return max(downloaded, key=self._thumbnail_quality)[0]

    @classmethod
    def _ranked_thumbnails(cls, info: dict[str, Any]) -> list[dict[str, Any]]:
        thumbnails = [item for item in info.get("thumbnails") or [] if isinstance(item, dict)]

        def rank(index_and_thumbnail: tuple[int, dict[str, Any]]) -> tuple[float, int, int]:
            index, thumbnail = index_and_thumbnail
            preference = cls._number(thumbnail.get("preference"))
            width = round(cls._number(thumbnail.get("width")))
            height = round(cls._number(thumbnail.get("height")))
            return (preference, width * height, index)

        return [item for _, item in sorted(enumerate(thumbnails), key=rank, reverse=True)[:8]]

    def _download_thumbnail_candidates(
        self,
        task: DownloadTask,
        work_dir: Path,
        ydl: yt_dlp.YoutubeDL,
        info: dict[str, Any],
        candidates: list[dict[str, Any]],
    ) -> list[tuple[Path, int]]:
        downloaded: list[tuple[Path, int]] = []
        for output_index, thumbnail in enumerate(candidates):
            if task.cancel_event.is_set():
                raise DownloadCancelled("Загрузка отменена пользователем")
            url = thumbnail.get("url")
            if not isinstance(url, str) or not url:
                continue
            extension = determine_ext(url, "jpg").lower()
            if extension not in {"avif", "jpeg", "jpg", "png", "webp"}:
                extension = "jpg"
            path = work_dir / f"thumbnail-{output_index}.{extension}"
            headers = thumbnail.get("http_headers") or info.get("http_headers") or {}
            if not isinstance(headers, dict):
                headers = {}
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
            width = round(self._number(thumbnail.get("width")))
            height = round(self._number(thumbnail.get("height")))
            downloaded.append((path, width * height))
        return downloaded

    def _thumbnail_quality(self, candidate: tuple[Path, int]) -> tuple[int, int, int]:
        path, metadata_area = candidate
        width, height = self._image_dimensions(path)
        actual_area = width * height
        return (actual_area or metadata_area, path.stat().st_size, metadata_area)

    def _download_tiktok_thumbnail_sync(self, task: DownloadTask, work_dir: Path) -> Path:
        video_id = tiktok_video_id(task.url, known_id=task.info.get("id"))
        if not video_id:
            raise FileNotFoundError("Не удалось определить ID видео TikTok")
        info = fetch_tiktok_video_info(canonical_tiktok_url(video_id))
        url = info.get("thumbnail")
        if not url:
            raise FileNotFoundError("Превью TikTok недоступно")

        extension = determine_ext(str(url), "jpg").lower()
        if extension not in {"avif", "jpeg", "jpg", "png", "webp"}:
            extension = "jpg"
        path = work_dir / f"thumbnail.{extension}"
        with youtube_client(self._common_options()) as ydl:
            response = ydl.urlopen(Request(str(url)))
            try:
                with path.open("wb") as output:
                    shutil.copyfileobj(response, output)
            finally:
                response.close()
        return path

    async def download_thumbnail(self, task: DownloadTask, work_dir: Path) -> Path:
        task.work_dir = str(work_dir)
        task.phase = "Скачивание превью"
        if task.info.get("_tiktok_fallback"):
            result = await asyncio.to_thread(self._download_tiktok_thumbnail_sync, task, work_dir)
            return self._validate_result_size(result)
        try:
            result = await asyncio.to_thread(self._download_thumbnail_sync, task, work_dir)
            if task.cancel_event.is_set():
                raise DownloadCancelled("Загрузка отменена пользователем")
            return self._validate_result_size(result)
        except (DownloadCancelled, FileTooLarge, TimeoutError):
            raise
        except Exception as exc:
            if is_tiktok_url(task.url) and tiktok_video_id(task.url, str(exc), task.info.get("id")):
                task.info["id"] = tiktok_video_id(task.url, str(exc), task.info.get("id"))
                result = await asyncio.to_thread(
                    self._download_tiktok_thumbnail_sync, task, work_dir
                )
                if task.cancel_event.is_set():
                    raise DownloadCancelled("Загрузка отменена пользователем") from exc
                return self._validate_result_size(result)
            raise

    def _validate_result_size(self, path: Path) -> Path:
        if path.stat().st_size > self.settings.effective_upload_limit:
            raise FileTooLarge("Файл превышает лимит Telegram")
        return path

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
            "no space",
            "disk quota",
            "ffmpeg is not installed",
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
        heights = self._candidate_heights(task)
        variants: list[DownloadVariant] = []
        for index, height in enumerate(heights):
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
        if _media_tool("ffmpeg") and task.action == DownloadAction.BEST:
            variants.append(
                DownloadVariant(
                    label="совместимый формат",
                    format_selector="bv*+ba/b",
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
            available: list[int] = []
            for item in task.info.get("formats") or []:
                if not isinstance(item, dict) or item.get("vcodec", "none") == "none":
                    continue
                try:
                    height = int(item.get("height"))
                except (TypeError, ValueError):
                    continue
                if height > 0:
                    available.append(height)
            available = sorted(set(available), reverse=True)
            heights = available or [2160, 1440, 1080, 720, 480, 360]
        return list(dict.fromkeys(height for height in heights if height > 0))
