from __future__ import annotations

import subprocess
import sys
from pathlib import Path
from urllib.parse import urlsplit

IMAGE_EXTENSIONS = {".jpg", ".jpeg", ".png", ".webp"}


def is_tiktok_photo_url(url: str) -> bool:
    parsed = urlsplit(url)
    host = (parsed.hostname or "").lower()
    return (host == "tiktok.com" or host.endswith(".tiktok.com")) and "/photo/" in parsed.path


def download_tiktok_photos(url: str, output_dir: str | Path) -> list[Path]:
    destination = Path(output_dir).resolve()
    destination.mkdir(parents=True, exist_ok=True)
    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "gallery_dl",
            "--no-mtime",
            "-D",
            str(destination),
            "-o",
            "extractor.tiktok.archive=null",
            url,
        ],
        capture_output=True,
        text=True,
        timeout=120,
        check=False,
    )
    files = sorted(
        path
        for path in destination.rglob("*")
        if path.is_file() and path.suffix.lower() in IMAGE_EXTENSIONS
    )
    if files:
        return files

    details = (result.stderr or result.stdout).strip()
    raise RuntimeError(details or "gallery-dl не смог скачать фото из TikTok")
