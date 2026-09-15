from __future__ import annotations

import sys
from pathlib import Path
from threading import Event
from urllib.parse import urlsplit

from .processes import run_command

IMAGE_EXTENSIONS = {".jpg", ".jpeg", ".png", ".webp"}


def is_tiktok_photo_url(url: str) -> bool:
    parsed = urlsplit(url)
    host = (parsed.hostname or "").lower()
    return (host == "tiktok.com" or host.endswith(".tiktok.com")) and "/photo/" in parsed.path


def download_tiktok_photos(
    url: str,
    output_dir: str | Path,
    *,
    cancel_event: Event | None = None,
    timeout: int = 120,
) -> list[Path]:
    destination = Path(output_dir).resolve()
    destination.mkdir(parents=True, exist_ok=True)
    result = run_command(
        [
            sys.executable,
            "-m",
            "gallery_dl",
            "--quiet",
            "--no-mtime",
            "-D",
            str(destination),
            "-o",
            "extractor.tiktok.archive=null",
            url,
        ],
        cancel_event=cancel_event,
        deadline_seconds=timeout,
    )
    files = sorted(
        path
        for path in destination.rglob("*")
        if path.is_file() and path.suffix.lower() in IMAGE_EXTENSIONS and path.stat().st_size > 0
    )
    if result.returncode == 0 and files:
        return files

    details = (result.stderr or result.stdout).strip()
    raise RuntimeError(details or "gallery-dl не смог скачать фото из TikTok")
