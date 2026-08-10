from __future__ import annotations

import logging
import shutil
import time
import unicodedata
from pathlib import Path

logger = logging.getLogger(__name__)


def sanitize_filename(name: str, max_length: int = 160) -> str:
    normalized = unicodedata.normalize("NFKC", str(name or ""))
    forbidden = '<>:"/\\|?*\n\r\t\0'
    safe = "".join("_" if char in forbidden or ord(char) < 32 else char for char in normalized)
    safe = " ".join(safe.split()).strip(" .")
    return (safe or "media")[:max_length].rstrip(" .")


def cleanup_old_files(temp_dir: str | Path, *, max_age_hours: int = 24) -> None:
    root = Path(temp_dir)
    if not root.exists():
        return
    threshold = time.time() - max_age_hours * 3600
    for entry in root.iterdir():
        try:
            if entry.stat().st_mtime > threshold:
                continue
            if entry.is_dir() and not entry.is_symlink():
                shutil.rmtree(entry)
            else:
                entry.unlink(missing_ok=True)
        except OSError as exc:
            logger.warning("Cannot remove stale temporary path %s: %s", entry, exc)
