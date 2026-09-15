from __future__ import annotations

import shutil
import tempfile
from contextlib import contextmanager
from pathlib import Path

import yt_dlp


@contextmanager
def youtube_client(options: dict):
    configured = dict(options)
    cookies = configured.get("cookiefile")
    if not cookies:
        with yt_dlp.YoutubeDL(configured) as client:
            yield client
        return
    with tempfile.TemporaryDirectory(prefix="resave-cookies-") as directory:
        snapshot = Path(directory) / "cookies.txt"
        shutil.copyfile(cookies, snapshot)
        configured["cookiefile"] = str(snapshot)
        with yt_dlp.YoutubeDL(configured) as client:
            yield client
