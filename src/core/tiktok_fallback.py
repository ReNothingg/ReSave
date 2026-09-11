from __future__ import annotations

import json
import re
import subprocess
import sys
import time
from pathlib import Path
from threading import Event
from typing import Any
from urllib.parse import urlsplit

VIDEO_EXTENSIONS = {".m4v", ".mkv", ".mov", ".mp4", ".webm"}
_VIDEO_ID_RE = re.compile(r"/(?:video|photo)/(\d{10,})")
_ERROR_ID_RE = re.compile(r"\[TikTok\]\s+(\d{10,})")


class TikTokFallbackError(RuntimeError):
    pass


def is_tiktok_url(url: str) -> bool:
    host = (urlsplit(url).hostname or "").lower()
    return host == "tiktok.com" or host.endswith(".tiktok.com")


def tiktok_video_id(url: str, error: str = "", known_id: object = None) -> str | None:
    if known_id is not None and re.fullmatch(r"\d{10,}", str(known_id)):
        return str(known_id)
    for value, pattern in ((url, _VIDEO_ID_RE), (error, _ERROR_ID_RE)):
        match = pattern.search(value)
        if match:
            return match.group(1)
    return None


def canonical_tiktok_url(video_id: str) -> str:
    return f"https://www.tiktok.com/@_/video/{video_id}"


def _gallery_command(*args: str) -> list[str]:
    return [sys.executable, "-m", "gallery_dl", *args]


def _gallery_error(result: subprocess.CompletedProcess[str]) -> str:
    details = (result.stderr or result.stdout).strip()
    return details[-2000:] if details else "gallery-dl не вернул данные TikTok"


def _stop_process(process: subprocess.Popen[str]) -> None:
    process.terminate()
    try:
        process.wait(timeout=5)
    except subprocess.TimeoutExpired:
        process.kill()
        process.wait(timeout=5)


def fetch_tiktok_video_info(url: str, *, error: str = "") -> dict[str, Any]:
    video_id = tiktok_video_id(url, error)
    if not video_id:
        raise TikTokFallbackError("Не удалось определить ID видео TikTok")

    canonical_url = canonical_tiktok_url(video_id)
    result = subprocess.run(
        _gallery_command("--no-download", "--dump-json", canonical_url),
        capture_output=True,
        text=True,
        timeout=60,
        check=False,
    )
    if result.returncode != 0:
        raise TikTokFallbackError(_gallery_error(result))
    try:
        messages = json.loads(result.stdout)
    except (TypeError, ValueError) as exc:
        raise TikTokFallbackError("gallery-dl вернул повреждённые данные TikTok") from exc

    metadata: dict[str, Any] = {}
    download_metadata: dict[str, Any] = {}
    for message in messages if isinstance(messages, list) else ():
        if not isinstance(message, list) or not message:
            continue
        if message[0] == 2 and len(message) > 1 and isinstance(message[1], dict):
            metadata = message[1]
        elif message[0] == 3 and len(message) > 2 and isinstance(message[2], dict):
            download_metadata = message[2]

    video = metadata.get("video") if isinstance(metadata.get("video"), dict) else {}
    if metadata.get("post_type") not in {None, "video"} or not (metadata or download_metadata):
        raise TikTokFallbackError("Публикация TikTok не содержит видео")

    def first(*values: object) -> object | None:
        return next((value for value in values if value is not None and value != ""), None)

    height = first(download_metadata.get("height"), video.get("height"))
    width = first(download_metadata.get("width"), video.get("width"))
    duration = first(download_metadata.get("duration"), video.get("duration"))
    filesize = first(download_metadata.get("size"), video.get("size"))
    extension = str(first(download_metadata.get("extension"), video.get("format"), "mp4"))
    thumbnail = first(video.get("cover"), video.get("originCover"), metadata.get("image"))
    author = metadata.get("author")
    uploader = first(
        metadata.get("user"), author.get("uniqueId") if isinstance(author, dict) else None
    )
    title = str(first(metadata.get("desc"), download_metadata.get("title"), f"TikTok {video_id}"))

    media_format = {
        "format_id": "gallery-dl",
        "ext": extension,
        "height": int(height) if height else None,
        "width": int(width) if width else None,
        "filesize": int(filesize) if filesize else None,
        "vcodec": str(video.get("codecType") or "h264"),
        "acodec": "aac",
    }
    return {
        "id": video_id,
        "title": title,
        "uploader": str(uploader) if uploader else None,
        "duration": float(duration) if duration else None,
        "width": media_format["width"],
        "height": media_format["height"],
        "thumbnail": str(thumbnail) if thumbnail else None,
        "formats": [media_format],
        "webpage_url": canonical_url,
        "extractor": "TikTok:gallery-dl",
        "_tiktok_fallback": True,
    }


def download_tiktok_video(
    url: str,
    destination: Path,
    *,
    known_id: object = None,
    cancel_event: Event | None = None,
    timeout: int = 1800,
) -> Path:
    video_id = tiktok_video_id(url, known_id=known_id)
    if not video_id:
        raise TikTokFallbackError("Не удалось определить ID видео TikTok")

    destination.mkdir(parents=True, exist_ok=True)
    process = subprocess.Popen(
        _gallery_command(
            "--quiet",
            "--no-mtime",
            "-D",
            str(destination),
            "--filename",
            "media.{extension}",
            "-o",
            "extractor.tiktok.archive=null",
            canonical_tiktok_url(video_id),
        ),
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    deadline = time.monotonic() + timeout
    while True:
        if cancel_event and cancel_event.is_set():
            _stop_process(process)
            raise TikTokFallbackError("Загрузка отменена пользователем")
        if time.monotonic() >= deadline:
            _stop_process(process)
            raise TikTokFallbackError("TikTok download timeout")
        try:
            stdout, stderr = process.communicate(timeout=0.25)
            break
        except subprocess.TimeoutExpired:
            continue
    if process.returncode != 0:
        details = (stderr or stdout).strip()
        raise TikTokFallbackError(details[-2000:] or "gallery-dl не смог скачать видео TikTok")

    files = sorted(
        (
            path
            for path in destination.rglob("*")
            if path.is_file()
            and path.suffix.lower() in VIDEO_EXTENSIONS
            and path.stat().st_size > 0
        ),
        key=lambda path: (path.stat().st_size, path.stat().st_mtime),
        reverse=True,
    )
    if not files:
        raise TikTokFallbackError("gallery-dl не создал видеофайл TikTok")
    return files[0]
