from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import sys
import threading
import time
from pathlib import Path
from typing import Any

import yt_dlp
from yt_dlp.networking import Request
from yt_dlp.utils import determine_ext

from .extractor import youtube_client
from .media_tools import media_tool
from .tiktok_fallback import fetch_tiktok_video_info, is_tiktok_url
from .video_info import collect_playlist_entries, compact_media_info

IGNORED_SUFFIXES = {".part", ".tmp", ".ytdl", ".json"}
SUBTITLE_SUFFIXES = {".srt", ".vtt", ".ttml", ".ass", ".srv3", ".json3"}
IMAGE_SUFFIXES = {"avif", "jpeg", "jpg", "png", "webp"}
_events = sys.stdout


class TooLarge(RuntimeError):
    pass


def emit(event: str, **data: Any) -> None:
    _events.write(json.dumps({"event": event, **data}, ensure_ascii=False, default=str) + "\n")


def number(value: object) -> float:
    try:
        parsed = float(value)
    except (OverflowError, TypeError, ValueError):
        return 0.0
    return parsed if 0 < parsed < float("inf") else 0.0


def watch_parent() -> None:
    parent = os.getppid()

    def loop() -> None:
        while True:
            time.sleep(2)
            if os.getppid() != parent:
                os._exit(1)

    threading.Thread(target=loop, daemon=True).start()


def base_options(job: dict[str, Any]) -> dict[str, Any]:
    options: dict[str, Any] = {
        "quiet": True,
        "no_warnings": True,
        "noprogress": True,
        "noplaylist": True,
        "socket_timeout": 30,
        "retries": 5,
        "fragment_retries": 10,
        "extractor_retries": 3,
        "file_access_retries": 3,
        "continuedl": True,
        "overwrites": True,
        "trim_file_name": 180,
        "buffersize": 64 * 1024,
        "noresizebuffer": True,
        "http_chunk_size": 10 * 1024 * 1024,
        "concurrent_fragment_downloads": 2,
        "http_headers": {"Accept-Language": "ru-RU,ru;q=0.9,en;q=0.8"},
    }
    cookies = job.get("cookies")
    if cookies and Path(cookies).is_file():
        options["cookiefile"] = cookies
    if job.get("rate_limit"):
        options["ratelimit"] = int(job["rate_limit"])
    ffmpeg = media_tool("ffmpeg")
    if ffmpeg:
        options["ffmpeg_location"] = str(Path(ffmpeg).parent)
    return options


def ffprobe_json(path: Path, entries: str) -> dict[str, Any]:
    ffprobe = media_tool("ffprobe")
    if not ffprobe:
        return {}
    try:
        result = subprocess.run(
            [ffprobe, "-v", "error", "-show_entries", entries, "-of", "json", str(path)],
            check=True,
            capture_output=True,
            text=True,
            timeout=20,
        )
        payload = json.loads(result.stdout or "{}")
    except (OSError, subprocess.SubprocessError, ValueError):
        return {}
    return payload if isinstance(payload, dict) else {}


def stream_types(path: Path) -> set[str]:
    streams = ffprobe_json(path, "stream=codec_type").get("streams")
    if not isinstance(streams, list):
        return set()
    return {
        str(stream["codec_type"])
        for stream in streams
        if isinstance(stream, dict) and stream.get("codec_type")
    }


def image_area(path: Path) -> int:
    streams = ffprobe_json(path, "stream=width,height").get("streams")
    if isinstance(streams, list) and streams and isinstance(streams[0], dict):
        try:
            return int(streams[0].get("width") or 0) * int(streams[0].get("height") or 0)
        except (TypeError, ValueError):
            return 0
    return 0


def estimated_size(info: dict[str, Any]) -> int:
    total = 0
    for item in info.get("requested_formats") or [info]:
        if not isinstance(item, dict):
            continue
        explicit = number(item.get("filesize") or item.get("filesize_approx"))
        if explicit:
            total += round(explicit)
            continue
        duration = number(item.get("duration") or info.get("duration"))
        bitrate = number(item.get("tbr") or item.get("vbr") or item.get("abr"))
        if duration and bitrate:
            total += round(duration * bitrate * 1000 / 8)
    return total or round(number(info.get("filesize") or info.get("filesize_approx")))


def op_info(job: dict[str, Any]) -> dict[str, Any]:
    url = job["url"]
    options = base_options(job)
    options.update(
        {
            "skip_download": True,
            "extract_flat": "in_playlist",
            "noplaylist": False,
            "playlistend": int(job.get("playlist_limit") or 25),
        }
    )
    try:
        with youtube_client(options) as ydl:
            info = ydl.extract_info(url, download=False)
    except Exception as exc:
        if not is_tiktok_url(url):
            raise
        try:
            info = fetch_tiktok_video_info(url, error=str(exc))
        except Exception as fallback_exc:
            raise RuntimeError(f"{exc}; TikTok fallback: {fallback_exc}") from exc
    if not isinstance(info, dict) or not info:
        raise RuntimeError("Источник не вернул информацию о медиа")
    if info.get("_type") == "playlist":
        limit = int(job.get("playlist_limit") or 25)
        return {"info": {"_type": "playlist", "entries": collect_playlist_entries(info, limit)}}
    return {"info": compact_media_info(info)}


def is_intermediate(path: Path) -> bool:
    return bool(re.search(r"\.f(?:\d+|audio|video)(?:-[^.]+)?\.", path.name))


def select_result(work_dir: Path, audio: bool) -> Path:
    files = [
        path
        for path in work_dir.rglob("*")
        if path.is_file()
        and path.suffix.lower() not in IGNORED_SUFFIXES
        and not is_intermediate(path)
        and path.stat().st_size > 0
    ]
    if not files:
        raise FileNotFoundError("yt-dlp не создал итоговый медиафайл")
    preferred = ".mp3" if audio else ".mp4"
    files.sort(
        key=lambda path: (path.suffix.lower() == preferred, path.stat().st_mtime), reverse=True
    )
    required = "audio" if audio else "video"
    for path in files:
        types = stream_types(path)
        if not types or required in types:
            return path
    raise FileNotFoundError(f"yt-dlp не создал итоговый файл с потоком типа {required}")


class DownloadHooks:
    def __init__(self, size_limit: int, work_dir: Path, min_free: int):
        self.size_limit = size_limit
        self.work_dir = work_dir
        self.min_free = min_free
        self.next_disk_check = 0.0
        self.safe_limit = max(1, round(size_limit * 0.97) - 1024 * 1024)
        self.rejected = False
        self.last_emit = 0.0
        self.last_bytes = -1

    def size_filter(self, info: dict[str, Any], *, incomplete: bool) -> str | None:
        if incomplete:
            return None
        size = estimated_size(info)
        if size and size > self.safe_limit:
            self.rejected = True
            return f"Estimated file size {size} exceeds limit {self.safe_limit}"
        return None

    def progress(self, data: dict[str, Any]) -> None:
        status = data.get("status")
        if status == "finished":
            emit("phase", stage="postprocess")
        if status != "downloading":
            return
        downloaded = round(number(data.get("downloaded_bytes")))
        now = time.monotonic()
        if downloaded == self.last_bytes or now - self.last_emit < 0.5:
            return
        self.last_bytes, self.last_emit = downloaded, now
        if downloaded > self.size_limit * 2:
            raise TooLarge("Downloaded data exceeds the size limit")
        if now >= self.next_disk_check:
            self.next_disk_check = now + 2
            if shutil.disk_usage(self.work_dir).free < self.min_free:
                raise OSError("No space left for media processing")
        emit(
            "progress",
            downloaded=downloaded,
            total=round(number(data.get("total_bytes") or data.get("total_bytes_estimate"))),
            speed=data.get("_speed_str"),
            eta=round(number(data.get("eta"))) or None,
        )

    @staticmethod
    def postprocess(data: dict[str, Any]) -> None:
        emit("phase", stage="postprocess")


def op_download(job: dict[str, Any]) -> dict[str, Any]:
    size_limit = int(job["size_limit"])
    audio = bool(job.get("audio"))
    work_dir = Path(job["work_dir"])
    hooks = DownloadHooks(size_limit, work_dir, int(job.get("min_free") or 0))
    ffmpeg = media_tool("ffmpeg")
    options = base_options(job)
    options.update(
        {
            "format": job["format"],
            "outtmpl": job["outtmpl"],
            "merge_output_format": "mp4",
            "progress_hooks": [hooks.progress],
            "postprocessor_hooks": [hooks.postprocess],
            "match_filter": hooks.size_filter,
        }
    )
    postprocessors = list(job.get("postprocessors") or [])
    if ffmpeg:
        options["external_downloader"] = {"m3u8": "ffmpeg"}
        if not audio:
            postprocessors.append({"key": "FFmpegVideoRemuxer", "preferedformat": "mp4"})
    if postprocessors:
        options["postprocessors"] = postprocessors
    with youtube_client(options) as ydl:
        ydl.download([job["url"]])
    try:
        result = select_result(work_dir, audio)
    except FileNotFoundError:
        if hooks.rejected:
            raise TooLarge("Estimated file size exceeds limit") from None
        raise
    if result.stat().st_size > size_limit:
        raise TooLarge(f"File size {result.stat().st_size} exceeds limit {size_limit}")
    return {"path": str(result)}


def op_subtitles(job: dict[str, Any]) -> dict[str, Any]:
    work_dir = Path(job["work_dir"])
    options = base_options(job)
    options.update(
        {
            "skip_download": True,
            "writesubtitles": True,
            "writeautomaticsub": True,
            "subtitleslangs": job.get("languages") or ["ru", "en"],
            "subtitlesformat": "srt/best",
            "outtmpl": str(work_dir / "subtitle.%(ext)s"),
        }
    )
    with youtube_client(options) as ydl:
        ydl.download([job["url"]])
    files = sorted(
        str(path)
        for path in work_dir.iterdir()
        if path.is_file() and path.suffix in SUBTITLE_SUFFIXES
    )
    if not files:
        raise FileNotFoundError("Для этого видео не удалось получить субтитры")
    return {"paths": files}


def fetch_to(ydl: yt_dlp.YoutubeDL, url: str, path: Path, headers: dict[str, Any]) -> None:
    response = ydl.urlopen(Request(url, headers=headers))
    try:
        with path.open("wb") as output:
            shutil.copyfileobj(response, output)
    finally:
        response.close()


def image_extension(url: str) -> str:
    extension = determine_ext(url, "jpg").lower()
    return extension if extension in IMAGE_SUFFIXES else "jpg"


def op_thumbnail(job: dict[str, Any]) -> dict[str, Any]:
    work_dir = Path(job["work_dir"])
    options = base_options(job)
    options["skip_download"] = True
    with youtube_client(options) as ydl:
        if job.get("thumbnail_url"):
            url = str(job["thumbnail_url"])
            path = work_dir / f"thumbnail.{image_extension(url)}"
            fetch_to(ydl, url, path, {})
            return {"path": str(path)}
        info = ydl.extract_info(job["url"], download=False)
        if not isinstance(info, dict):
            raise FileNotFoundError("Источник не вернул данные превью")
        thumbnails = [item for item in info.get("thumbnails") or [] if isinstance(item, dict)]
        ranked = sorted(
            enumerate(thumbnails),
            key=lambda pair: (
                number(pair[1].get("preference")),
                number(pair[1].get("width")) * number(pair[1].get("height")),
                pair[0],
            ),
            reverse=True,
        )[:8]
        downloaded: list[tuple[int, int, Path]] = []
        for index, (_, thumbnail) in enumerate(ranked):
            url = thumbnail.get("url")
            if not isinstance(url, str) or not url:
                continue
            path = work_dir / f"thumbnail-{index}.{image_extension(url)}"
            headers = thumbnail.get("http_headers") or info.get("http_headers") or {}
            try:
                fetch_to(ydl, url, path, headers if isinstance(headers, dict) else {})
            except Exception:
                path.unlink(missing_ok=True)
                continue
            metadata_area = round(number(thumbnail.get("width")) * number(thumbnail.get("height")))
            downloaded.append((image_area(path) or metadata_area, path.stat().st_size, path))
    if not downloaded:
        raise FileNotFoundError("Превью недоступно")
    return {"path": str(max(downloaded)[2])}


OPERATIONS = {
    "info": op_info,
    "download": op_download,
    "subtitles": op_subtitles,
    "thumbnail": op_thumbnail,
}


def main() -> int:
    global _events
    _events = os.fdopen(os.dup(1), "w", buffering=1, encoding="utf-8")
    os.dup2(2, 1)
    sys.stdout = sys.stderr
    watch_parent()
    try:
        job = json.loads(sys.stdin.read())
        result = OPERATIONS[job["op"]](job)
    except TooLarge as exc:
        emit("error", kind="too_large", message=str(exc))
        return 1
    except FileNotFoundError as exc:
        emit("error", kind="not_found", message=str(exc))
        return 1
    except Exception as exc:
        emit("error", kind="error", message=str(exc) or type(exc).__name__)
        return 1
    emit("result", **result)
    return 0


if __name__ == "__main__":
    code = main()
    _events.flush()
    os._exit(code)
