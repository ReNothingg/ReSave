from __future__ import annotations

import asyncio
import json
import logging
import time
from pathlib import Path

from config import Settings

from .errors import DownloadCancelled, FileTooLarge
from .media_tools import media_tool
from .models import DownloadTask

logger = logging.getLogger(__name__)


async def communicate_process(
    process: asyncio.subprocess.Process,
    *,
    deadline_seconds: float,
    cancel_event=None,
) -> tuple[bytes, bytes]:
    communication = asyncio.create_task(process.communicate())
    deadline = asyncio.get_running_loop().time() + deadline_seconds
    try:
        while not communication.done():
            if cancel_event is not None and cancel_event.is_set():
                raise DownloadCancelled("Загрузка отменена пользователем")
            remaining = deadline - asyncio.get_running_loop().time()
            if remaining <= 0:
                raise TimeoutError
            await asyncio.wait({communication}, timeout=min(0.25, remaining))
        return await communication
    except BaseException:
        if process.returncode is None:
            process.kill()
        await process.wait()
        communication.cancel()
        await asyncio.gather(communication, return_exceptions=True)
        raise


async def convert_gif(
    settings: Settings,
    task: DownloadTask,
    source: Path,
    target: Path,
) -> Path:
    ffmpeg = media_tool("ffmpeg")
    if not ffmpeg:
        raise RuntimeError("FFmpeg is not installed")
    profiles = ((12, 480), (10, 360), (8, 240))
    for fps, width in profiles:
        elapsed = max(0.0, time.time() - task.started_at) if task.started_at else 0.0
        remaining = settings.download_timeout_seconds - elapsed
        if remaining <= 0:
            raise RuntimeError("FFmpeg GIF conversion timeout")
        process = await asyncio.create_subprocess_exec(
            ffmpeg,
            "-hide_banner",
            "-loglevel",
            "error",
            "-threads",
            "1",
            "-filter_threads",
            "1",
            "-i",
            str(source),
            "-t",
            "30",
            "-vf",
            f"fps={fps},scale={width}:-2:flags=lanczos,split[s0][s1];"
            "[s0]palettegen[p];[s1][p]paletteuse",
            "-y",
            str(target),
            stdout=asyncio.subprocess.DEVNULL,
            stderr=asyncio.subprocess.PIPE,
        )
        try:
            _, stderr = await communicate_process(
                process,
                deadline_seconds=min(remaining, 120),
                cancel_event=task.cancel_event,
            )
        except TimeoutError:
            raise RuntimeError("FFmpeg GIF conversion timeout") from None
        if process.returncode:
            detail = stderr.decode("utf-8", errors="replace")[-500:]
            raise RuntimeError(f"FFmpeg GIF conversion failed: {detail}")
        target_size = (await asyncio.to_thread(target.stat)).st_size
        if target_size <= settings.effective_upload_limit:
            return target
        logger.info("GIF is too large at %sfps/%sp; retrying smaller", fps, width)
    raise FileTooLarge("GIF превышает лимит Telegram даже после сжатия")


async def probe_video(path: Path) -> dict[str, int]:
    ffprobe = media_tool("ffprobe")
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
        stdout, _ = await communicate_process(process, deadline_seconds=15)
        payload = json.loads(stdout or b"{}")
    except (TimeoutError, ValueError):
        return {}
    return probe_metadata(payload)


def probe_metadata(payload: object) -> dict[str, int]:
    if not isinstance(payload, dict):
        return {}
    streams = payload.get("streams")
    if not isinstance(streams, list) or not streams:
        return {}
    stream = streams[0]
    if not isinstance(stream, dict):
        return {}
    result: dict[str, int] = {}
    for key in ("width", "height"):
        value = stream.get(key)
        if isinstance(value, int) and value > 0:
            result[key] = value
    ratio = _ratio(stream.get("sample_aspect_ratio"))
    if ratio and result.get("width"):
        result["width"] = max(1, round(result["width"] * ratio))
    rotation = _rotation(stream)
    if abs(rotation) % 180 == 90 and result.get("width") and result.get("height"):
        result["width"], result["height"] = result["height"], result["width"]
    format_info = payload.get("format")
    duration = stream.get("duration") or (
        format_info.get("duration") if isinstance(format_info, dict) else None
    )
    parsed_duration = positive_seconds(duration)
    if parsed_duration > 0:
        result["duration"] = parsed_duration
    return result


def _rotation(stream: dict) -> int:
    tags = stream.get("tags")
    values = list(
        item.get("rotation")
        for item in stream.get("side_data_list") or []
        if isinstance(item, dict)
    )
    values.append(tags.get("rotate") if isinstance(tags, dict) else None)
    for value in values:
        try:
            return int(value)
        except (TypeError, ValueError):
            continue
    return 0


def _ratio(value: str | None) -> float | None:
    if not isinstance(value, str) or not value or value in {"0:1", "1:0", "0:0"}:
        return None
    try:
        numerator, denominator = value.split(":", 1)
        ratio = int(numerator) / int(denominator)
    except (TypeError, ValueError, ZeroDivisionError):
        return None
    return ratio if ratio > 0 else None


def positive_seconds(raw: object) -> int:
    try:
        value = round(float(raw))
    except (OverflowError, TypeError, ValueError):
        return 0
    return value if value > 0 else 0
