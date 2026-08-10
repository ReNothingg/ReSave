from __future__ import annotations

import asyncio
import logging
from pathlib import Path
from typing import Any

import yt_dlp

logger = logging.getLogger(__name__)


class VideoInfoError(RuntimeError):
    pass


class VideoInfoService:
    def __init__(
        self,
        cookies_file: Path,
        playlist_limit: int = 25,
        max_concurrent_requests: int = 4,
    ):
        self.cookies_file = cookies_file
        self.playlist_limit = playlist_limit
        self._semaphore = asyncio.Semaphore(max_concurrent_requests)

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
            with yt_dlp.YoutubeDL(self._options()) as ydl:
                info = ydl.extract_info(url, download=False)
        except Exception as exc:
            raise VideoInfoError(str(exc)) from exc
        if not info:
            raise VideoInfoError("Источник не вернул информацию о медиа")
        return info

    async def fetch(self, url: str) -> dict[str, Any]:
        async with self._semaphore:
            return await asyncio.to_thread(self._fetch_sync, url)


def collect_resolutions(info: dict[str, Any]) -> list[int]:
    heights = {
        int(item["height"])
        for item in info.get("formats") or []
        if item.get("height") and item.get("vcodec", "none") != "none"
    }
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
    compact["formats"] = [
        {
            "height": item.get("height"),
            "vcodec": item.get("vcodec"),
            "filesize": item.get("filesize") or item.get("filesize_approx"),
        }
        for item in info.get("formats") or []
        if item.get("height") and item.get("vcodec", "none") != "none"
    ]
    return compact


def collect_playlist_entries(info: dict[str, Any], limit: int) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    for entry in info.get("entries") or []:
        if not entry:
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


# Lightweight compatibility functions.
def fetch_video_info_result(url: str):
    import config

    try:
        return VideoInfoService(Path(config.COOKIES_FILE))._fetch_sync(url), None
    except VideoInfoError as exc:
        logger.info("Media info lookup failed: %s", exc)
        return None, str(exc)


def fetch_video_info(url: str):
    return fetch_video_info_result(url)[0]
