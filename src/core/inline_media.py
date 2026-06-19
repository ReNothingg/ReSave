from __future__ import annotations

import asyncio
import hashlib
import logging
import os
import shutil
import sqlite3
import subprocess
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit
from uuid import uuid4

import yt_dlp

import config
from .access import build_duration_limit_error, build_file_size_limit_error
from .download_support import find_completed_files

logger = logging.getLogger(__name__)


def canonicalize_inline_url(url: str) -> str:
    parsed = urlsplit(url.strip())
    scheme = parsed.scheme.lower()
    hostname = (parsed.hostname or "").lower()
    if parsed.port:
        hostname = f"{hostname}:{parsed.port}"
    path = parsed.path.rstrip("/") or "/"
    tracking_parameters = {"fbclid", "gclid"}
    if hostname in {"youtube.com", "www.youtube.com", "youtu.be"}:
        tracking_parameters.update({"si", "feature"})
    if hostname.endswith("tiktok.com"):
        tracking_parameters.update({"_r", "_t"})
    query = urlencode(
        [
            (key, value)
            for key, value in parse_qsl(parsed.query, keep_blank_values=True)
            if key.lower() not in tracking_parameters and not key.lower().startswith("utm_")
        ],
        doseq=True,
    )
    return urlunsplit((scheme, hostname, path, query, ""))


def inline_cache_key(url: str) -> str:
    return hashlib.sha256(canonicalize_inline_url(url).encode("utf-8")).hexdigest()


@dataclass(frozen=True)
class InlineMediaRecord:
    cache_key: str
    url: str
    file_id: str
    title: str
    uploader: str | None = None
    duration: int | None = None
    width: int | None = None
    height: int | None = None
    file_size: int | None = None
    updated_at: float = 0.0


@dataclass(frozen=True)
class InlinePreparationState:
    status: str
    error: str | None = None
    updated_at: float = 0.0


class InlineMediaStore:
    def __init__(self, db_path: str | os.PathLike):
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self.lock = threading.RLock()
        self.connection = sqlite3.connect(self.db_path, check_same_thread=False, timeout=30)
        self.connection.row_factory = sqlite3.Row
        self.connection.execute("PRAGMA busy_timeout = 30000")
        self._initialize()

    def _initialize(self) -> None:
        with self.lock:
            self.connection.execute(
                """
                CREATE TABLE IF NOT EXISTS inline_media_cache (
                    cache_key TEXT PRIMARY KEY,
                    url TEXT NOT NULL,
                    file_id TEXT NOT NULL,
                    title TEXT NOT NULL,
                    uploader TEXT,
                    duration INTEGER,
                    width INTEGER,
                    height INTEGER,
                    file_size INTEGER,
                    updated_at REAL NOT NULL,
                    last_used_at REAL NOT NULL
                )
                """
            )
            self.connection.execute(
                """
                CREATE TABLE IF NOT EXISTS inline_requests (
                    token TEXT PRIMARY KEY,
                    url TEXT NOT NULL,
                    user_id INTEGER NOT NULL,
                    created_at REAL NOT NULL
                )
                """
            )
            self.connection.execute(
                "CREATE INDEX IF NOT EXISTS inline_requests_created_idx "
                "ON inline_requests(created_at)"
            )
            self.connection.commit()

    @staticmethod
    def _row_to_record(row: sqlite3.Row | None) -> InlineMediaRecord | None:
        if row is None:
            return None
        return InlineMediaRecord(
            cache_key=row["cache_key"],
            url=row["url"],
            file_id=row["file_id"],
            title=row["title"],
            uploader=row["uploader"],
            duration=row["duration"],
            width=row["width"],
            height=row["height"],
            file_size=row["file_size"],
            updated_at=row["updated_at"],
        )

    def get(self, url: str, *, max_age: int | None = None) -> InlineMediaRecord | None:
        key = inline_cache_key(url)
        with self.lock:
            row = self.connection.execute(
                "SELECT * FROM inline_media_cache WHERE cache_key = ?",
                (key,),
            ).fetchone()
            record = self._row_to_record(row)
            if record is None:
                return None
            if max_age and time.time() - record.updated_at > max_age:
                self.connection.execute(
                    "DELETE FROM inline_media_cache WHERE cache_key = ?",
                    (key,),
                )
                self.connection.commit()
                return None
            return record

    def save(self, record: InlineMediaRecord) -> None:
        now = record.updated_at or time.time()
        with self.lock:
            self.connection.execute(
                """
                INSERT INTO inline_media_cache (
                    cache_key, url, file_id, title, uploader, duration,
                    width, height, file_size, updated_at, last_used_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(cache_key) DO UPDATE SET
                    url = excluded.url,
                    file_id = excluded.file_id,
                    title = excluded.title,
                    uploader = excluded.uploader,
                    duration = excluded.duration,
                    width = excluded.width,
                    height = excluded.height,
                    file_size = excluded.file_size,
                    updated_at = excluded.updated_at,
                    last_used_at = excluded.last_used_at
                """,
                (
                    record.cache_key,
                    record.url,
                    record.file_id,
                    record.title,
                    record.uploader,
                    record.duration,
                    record.width,
                    record.height,
                    record.file_size,
                    now,
                    now,
                ),
            )
            self.connection.commit()

    def delete(self, url: str) -> None:
        with self.lock:
            self.connection.execute(
                "DELETE FROM inline_media_cache WHERE cache_key = ?",
                (inline_cache_key(url),),
            )
            self.connection.commit()

    def create_request(self, url: str, user_id: int, *, ttl: int = 3600) -> str:
        token = uuid4().hex[:20]
        now = time.time()
        with self.lock:
            self.connection.execute(
                "DELETE FROM inline_requests WHERE created_at < ?",
                (now - ttl,),
            )
            self.connection.execute(
                "INSERT INTO inline_requests(token, url, user_id, created_at) VALUES (?, ?, ?, ?)",
                (token, canonicalize_inline_url(url), user_id, now),
            )
            self.connection.commit()
        return token

    def resolve_request(self, token: str, *, ttl: int = 3600) -> tuple[str, int] | None:
        with self.lock:
            row = self.connection.execute(
                "SELECT url, user_id, created_at FROM inline_requests WHERE token = ?",
                (token,),
            ).fetchone()
            if row is None:
                return None
            if time.time() - row["created_at"] > ttl:
                self.connection.execute("DELETE FROM inline_requests WHERE token = ?", (token,))
                self.connection.commit()
                return None
            return row["url"], row["user_id"]

    def close(self) -> None:
        with self.lock:
            self.connection.close()


class InlineMediaService:
    def __init__(
        self,
        *,
        sync_bot,
        store: InlineMediaStore,
        cache_chat_id: int,
        temp_dir: str | os.PathLike,
        max_concurrent: int = 1,
        cache_ttl: int = 90 * 24 * 60 * 60,
        error_ttl: int = 60,
    ):
        self.sync_bot = sync_bot
        self.store = store
        self.cache_chat_id = cache_chat_id
        self.temp_dir = Path(temp_dir)
        self.cache_ttl = cache_ttl
        self.error_ttl = error_ttl
        self._semaphore = asyncio.Semaphore(max(1, max_concurrent))
        self._jobs: dict[str, asyncio.Task] = {}
        self._states: dict[str, InlinePreparationState] = {}

    def get_ready(self, url: str) -> InlineMediaRecord | None:
        return self.store.get(url, max_age=self.cache_ttl)

    def get_state(self, url: str) -> InlinePreparationState:
        if self.get_ready(url):
            return InlinePreparationState("ready", updated_at=time.time())
        key = inline_cache_key(url)
        job = self._jobs.get(key)
        if job and not job.done():
            return InlinePreparationState("preparing", updated_at=time.time())
        state = self._states.get(key)
        if state and state.status == "error" and time.time() - state.updated_at <= self.error_ttl:
            return state
        return InlinePreparationState("missing", updated_at=time.time())

    def ensure_started(self, url: str, user_id: int, *, force: bool = False) -> str:
        url = canonicalize_inline_url(url)
        if self.get_ready(url):
            return "ready"
        key = inline_cache_key(url)
        existing = self._jobs.get(key)
        if existing and not existing.done():
            return "preparing"
        state = self._states.get(key)
        if (
            not force
            and state
            and state.status == "error"
            and time.time() - state.updated_at <= self.error_ttl
        ):
            return "error"

        self._states[key] = InlinePreparationState("preparing", updated_at=time.time())
        job = asyncio.create_task(self._run_job(url, user_id), name=f"inline:{key[:12]}")
        self._jobs[key] = job
        job.add_done_callback(lambda completed, cache_key=key: self._job_finished(cache_key, completed))
        return "preparing"

    def _job_finished(self, key: str, task: asyncio.Task) -> None:
        if self._jobs.get(key) is task:
            self._jobs.pop(key, None)
        try:
            task.result()
        except asyncio.CancelledError:
            pass
        except Exception:
            # _run_job records and logs the useful error.
            pass

    async def _run_job(self, url: str, user_id: int) -> None:
        key = inline_cache_key(url)
        try:
            async with self._semaphore:
                record = await asyncio.to_thread(self._prepare_sync, url, user_id)
            self.store.save(record)
            self._states[key] = InlinePreparationState("ready", updated_at=time.time())
            logger.info("Inline cache ready: key=%s title=%s", key[:12], record.title)
        except Exception as exc:
            self._states[key] = InlinePreparationState(
                "error",
                error=str(exc),
                updated_at=time.time(),
            )
            if isinstance(exc, (RuntimeError, yt_dlp.utils.DownloadError)):
                logger.warning("Inline preparation rejected for %s: %s", url, exc)
            else:
                logger.exception("Inline preparation failed for %s: %s", url, exc)
            raise

    async def wait_until_ready(
        self,
        url: str,
        user_id: int,
        *,
        timeout: int,
    ) -> InlineMediaRecord | None:
        record = self.get_ready(url)
        if record:
            return record
        self.ensure_started(url, user_id)
        key = inline_cache_key(url)
        job = self._jobs.get(key)
        if not job:
            return self.get_ready(url)
        try:
            await asyncio.wait_for(asyncio.shield(job), timeout=timeout)
        except Exception:
            return self.get_ready(url)
        return self.get_ready(url)

    def invalidate(self, url: str) -> None:
        self.store.delete(url)
        self._states.pop(inline_cache_key(url), None)

    @staticmethod
    def _ensure_telegram_mp4(file_path: Path) -> Path:
        ffprobe = shutil.which("ffprobe")
        ffmpeg = shutil.which("ffmpeg")
        if not ffprobe or not ffmpeg:
            if file_path.suffix.lower() == ".mp4":
                return file_path
            raise RuntimeError("FFmpeg необходим для подготовки inline-видео")

        probe = subprocess.run(
            [
                ffprobe,
                "-v",
                "error",
                "-show_entries",
                "stream=codec_type,codec_name",
                "-of",
                "default=noprint_wrappers=1:nokey=0",
                str(file_path),
            ],
            capture_output=True,
            text=True,
            timeout=30,
            check=False,
        )
        probe_text = probe.stdout.lower()
        has_h264 = "codec_name=h264" in probe_text
        audio_codecs = [
            line.split("=", 1)[1]
            for line in probe_text.splitlines()
            if line.startswith("codec_name=") and line.split("=", 1)[1] != "h264"
        ]
        compatible_audio = not audio_codecs or all(codec == "aac" for codec in audio_codecs)
        if file_path.suffix.lower() == ".mp4" and has_h264 and compatible_audio:
            return file_path

        converted = file_path.with_name("telegram.mp4")
        command = [
            ffmpeg,
            "-y",
            "-i",
            str(file_path),
            "-c:v",
            "libx264",
            "-preset",
            "veryfast",
            "-crf",
            "23",
            "-c:a",
            "aac",
            "-b:a",
            "128k",
            "-movflags",
            "+faststart",
            str(converted),
        ]
        result = subprocess.run(
            command,
            capture_output=True,
            text=True,
            timeout=600,
            check=False,
        )
        if result.returncode != 0 or not converted.exists():
            raise RuntimeError("FFmpeg не смог подготовить MP4 для inline-режима")
        return converted

    def _prepare_sync(self, url: str, user_id: int) -> InlineMediaRecord:
        if not self.cache_chat_id:
            raise RuntimeError("INLINE_CACHE_CHAT_ID или ADMIN_IDS не настроен")

        work_dir = self.temp_dir / f"inline_{inline_cache_key(url)[:16]}_{uuid4().hex[:8]}"
        work_dir.mkdir(parents=True, exist_ok=True)
        output_path = str(work_dir / "media")
        try:
            info_options = {
                "quiet": True,
                "no_warnings": True,
                "skip_download": True,
                "noplaylist": True,
                "socket_timeout": 15,
                "retries": 2,
                "extractor_retries": 2,
                "cookiefile": config.COOKIES_FILE,
                "nocheckcertificate": True,
            }
            with yt_dlp.YoutubeDL(info_options) as ydl:
                info = ydl.extract_info(url, download=False)
            if not info:
                raise RuntimeError("Источник не вернул информацию о видео")
            if info.get("_type") == "playlist":
                raise RuntimeError("Плейлисты недоступны в inline-режиме")

            duration_error = build_duration_limit_error(user_id, info.get("duration"))
            if duration_error:
                raise RuntimeError(duration_error)

            download_options = {
                "format": (
                    "bv*[height<=720][ext=mp4][vcodec^=avc1]+ba[ext=m4a]/"
                    "b[height<=720][ext=mp4]/bv*[height<=720]+ba/"
                    "best[height<=720]/best"
                ),
                "outtmpl": f"{output_path}.%(ext)s",
                "quiet": True,
                "no_warnings": True,
                "noplaylist": True,
                "merge_output_format": "mp4",
                "http_chunk_size": 10 * 1024 * 1024,
                "cookiefile": config.COOKIES_FILE,
                "socket_timeout": 30,
                "retries": 3,
                "fragment_retries": 3,
                "nocheckcertificate": True,
            }
            ffmpeg_path = shutil.which("ffmpeg")
            if ffmpeg_path:
                download_options["ffmpeg_location"] = str(Path(ffmpeg_path).parent)
            with yt_dlp.YoutubeDL(download_options) as ydl:
                ydl.download([url])

            files = find_completed_files(work_dir)
            if not files:
                raise RuntimeError("Файл не найден после скачивания")
            file_path = max(files, key=lambda path: path.stat().st_mtime)
            file_path = self._ensure_telegram_mp4(file_path)
            file_size = file_path.stat().st_size
            file_size_error = build_file_size_limit_error(user_id, file_size)
            if file_size_error:
                raise RuntimeError(file_size_error)

            message = self.sync_bot.send_video(
                self.cache_chat_id,
                str(file_path),
                supports_streaming=True,
                timeout=600,
            )
            video = getattr(message, "video", None)
            file_id = getattr(video, "file_id", None)
            if not file_id:
                raise RuntimeError("Telegram не вернул file_id для inline-кеша")

            try:
                self.sync_bot.delete_message(self.cache_chat_id, message.message_id)
            except Exception as exc:
                logger.debug("Inline cache message cleanup failed: %s", exc)

            return InlineMediaRecord(
                cache_key=inline_cache_key(url),
                url=canonicalize_inline_url(url),
                file_id=file_id,
                title=str(info.get("title") or "Видео")[:200],
                uploader=str(info.get("uploader"))[:200] if info.get("uploader") else None,
                duration=int(info["duration"]) if isinstance(info.get("duration"), (int, float)) else None,
                width=int(info["width"]) if isinstance(info.get("width"), (int, float)) else None,
                height=int(info["height"]) if isinstance(info.get("height"), (int, float)) else None,
                file_size=file_size,
                updated_at=time.time(),
            )
        finally:
            shutil.rmtree(work_dir, ignore_errors=True)
