from __future__ import annotations

import shutil
import subprocess
from pathlib import Path


IMAGE_EXTENSIONS = {".jpg", ".jpeg", ".png", ".webp"}


def is_tiktok_photo_url(url: str) -> bool:
    lowered = url.lower()
    return "tiktok.com" in lowered and "/photo/" in lowered


def download_tiktok_photos(url: str, output_dir: str | Path) -> list[Path]:
    executable = shutil.which("gallery-dl")
    if not executable:
        raise RuntimeError("gallery-dl не установлен на сервере")

    destination = Path(output_dir).resolve()
    destination.mkdir(parents=True, exist_ok=True)
    result = subprocess.run(
        [
            executable,
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
