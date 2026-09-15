from __future__ import annotations

import os
import shutil
from functools import lru_cache
from pathlib import Path


@lru_cache(maxsize=2)
def media_tool(name: str) -> str | None:
    discovered = shutil.which(name)
    if discovered:
        return discovered

    candidates: list[Path] = []
    configured = os.getenv("FFMPEG_LOCATION", "").strip()
    if configured:
        location = Path(configured).expanduser()
        candidates.append(location / name if location.is_dir() else location.with_name(name))

    candidates.append(Path.home() / ".local" / "bin" / name)
    ffmpeg_home = Path.home() / "ffmpeg"
    if ffmpeg_home.is_dir():
        candidates.extend(sorted(ffmpeg_home.glob(f"*/{name}"), reverse=True))

    for candidate in candidates:
        if candidate.is_file() and os.access(candidate, os.X_OK):
            return str(candidate)
    return None
