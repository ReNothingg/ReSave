from __future__ import annotations

import asyncio
import logging
from pathlib import Path
from typing import Any
from urllib.parse import urljoin, urlsplit

import aiohttp

from .errors import VideoInfoError
from .extractor import youtube_client
from .tiktok_fallback import fetch_tiktok_video_info, is_tiktok_url

logger = logging.getLogger(__name__)


class VideoInfoService:
    def __init__(
        self,
        cookies_file: Path,
        playlist_limit: int = 25,
        max_concurrent_requests: int = 1,
    ):
        self.cookies_file = cookies_file
        self.playlist_limit = playlist_limit
        self._semaphore = asyncio.Semaphore(max_concurrent_requests)
        self._pending: set[asyncio.Task] = set()

    async def resolve_url(self, url: str) -> str:
        if not is_tiktok_url(url) or "/video/" in url or "/photo/" in url:
            return url
        original = url
        await self._acquire_slot()
        try:
            try:
                async with asyncio.timeout(15):
                    async with aiohttp.ClientSession() as session:
                        for _ in range(5):
                            parsed = urlsplit(url)
                            if (
                                parsed.scheme != "https"
                                or parsed.username
                                or parsed.password
                                or parsed.port not in (None, 443)
                                or not is_tiktok_url(url)
                            ):
                                raise VideoInfoError("Unsafe TikTok redirect")
                            async with session.get(url, allow_redirects=False) as response:
                                if response.status not in {301, 302, 303, 307, 308}:
                                    return url
                                location = response.headers.get("Location")
                                if not location:
                                    return url
                                url = urljoin(url, location)
                        raise VideoInfoError("Too many TikTok redirects")
            except (aiohttp.ClientError, TimeoutError):
                return original
        finally:
            self._semaphore.release()

    def _options(self) -> dict[str, Any]:
        options: dict[str, Any] = {
            "quiet": True,
            "no_warnings": True,
            "skip_download": True,
            "extract_flat": "in_playlist",
            "socket_timeout": 20,
            "retries": 3,
            "extractor_retries": 3,
            "playlistend": self.playlist_limit,
        }
        if self.cookies_file.is_file():
            options["cookiefile"] = str(self.cookies_file)
        return options

    def _fetch_sync(self, url: str) -> dict[str, Any]:
        try:
            with youtube_client(self._options()) as ydl:
                info = ydl.extract_info(url, download=False)
        except Exception as exc:
            if is_tiktok_url(url):
                try:
                    return fetch_tiktok_video_info(url, error=str(exc))
                except Exception as fallback_exc:
                    raise VideoInfoError(f"{exc}; TikTok fallback: {fallback_exc}") from exc
            raise VideoInfoError(str(exc)) from exc
        if not isinstance(info, dict) or not info:
            raise VideoInfoError("Источник не вернул информацию о медиа")
        return info

    async def fetch(self, url: str) -> dict[str, Any]:
        await self._acquire_slot()
        operation = asyncio.create_task(asyncio.to_thread(self._fetch_sync, url))
        self._pending.add(operation)
        operation.add_done_callback(self._finished)
        try:
            return await asyncio.wait_for(asyncio.shield(operation), timeout=90)
        except TimeoutError as exc:
            raise VideoInfoError("Metadata lookup timeout") from exc

    async def _acquire_slot(self) -> None:
        try:
            await asyncio.wait_for(self._semaphore.acquire(), timeout=2)
        except TimeoutError as exc:
            raise VideoInfoError("Проверка ссылок занята. Попробуйте через минуту.") from exc

    def _finished(self, operation: asyncio.Task) -> None:
        self._pending.discard(operation)
        self._semaphore.release()
        if not operation.cancelled():
            operation.exception()


def _positive_int(value: object) -> int | None:
    try:
        parsed = int(value)
    except (OverflowError, TypeError, ValueError):
        return None
    return parsed if parsed > 0 else None


def collect_resolutions(info: dict[str, Any]) -> list[int]:
    heights: set[int] = set()
    for item in info.get("formats") or []:
        if not isinstance(item, dict) or item.get("vcodec", "none") == "none":
            continue
        height = _positive_int(item.get("height"))
        if height is not None:
            heights.add(height)
    return sorted(heights, reverse=True)


def compact_media_info(info: dict[str, Any]) -> dict[str, Any]:
    compact = {
        key: info.get(key)
        for key in ("id", "title", "uploader", "duration", "width", "height")
        if info.get(key) is not None
    }
    compact["thumbnail"] = bool(info.get("thumbnail"))
    compact["subtitles"] = bool(info.get("subtitles"))
    compact["automatic_captions"] = bool(info.get("automatic_captions"))
    formats = [item for item in info.get("formats") or [] if isinstance(item, dict)]
    compact["has_video"] = (
        any(item.get("vcodec", "none") != "none" for item in formats)
        if formats
        else info.get("vcodec") != "none"
    )
    compact["has_audio"] = (
        any(item.get("acodec", "unknown") != "none" for item in formats)
        if formats
        else info.get("acodec") != "none"
    )
    languages = list(
        dict.fromkeys(
            [
                *(info.get("subtitles") or {}),
                *(info.get("automatic_captions") or {}),
            ]
        )
    )
    preferred = [lang for lang in ("ru", "en", "ru-orig", "en-orig") if lang in languages]
    compact["subtitle_languages"] = preferred[:2] or languages[:1]
    if info.get("_tiktok_fallback"):
        compact["_tiktok_fallback"] = True
    compact["formats"] = []
    for item in info.get("formats") or []:
        if not isinstance(item, dict) or item.get("vcodec", "none") == "none":
            continue
        height = _positive_int(item.get("height"))
        if height is None:
            continue
        compact["formats"].append(
            {
                "height": height,
                "vcodec": item.get("vcodec"),
                "filesize": item.get("filesize") or item.get("filesize_approx"),
            }
        )
    return compact


def collect_playlist_entries(info: dict[str, Any], limit: int) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    for entry in info.get("entries") or []:
        if not isinstance(entry, dict):
            continue
        url = entry.get("webpage_url") or entry.get("original_url")
        raw_url = entry.get("url")
        if not url and isinstance(raw_url, str) and "://" in raw_url:
            url = raw_url
        extractor = str(entry.get("extractor_key") or entry.get("extractor") or "").lower()
        if not url and raw_url and "youtube" in extractor:
            url = f"https://www.youtube.com/watch?v={raw_url}"
        if not url:
            continue
        result.append(
            {
                "url": url,
                "info": {
                    "id": entry.get("id"),
                    "title": entry.get("title") or "video",
                    "uploader": entry.get("uploader") or info.get("uploader"),
                    "duration": entry.get("duration"),
                },
            }
        )
        if len(result) >= limit:
            break
    return result
