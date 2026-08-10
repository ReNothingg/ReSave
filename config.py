from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv

from src import version_guard as _version_guard  # noqa: F401

BASE_DIR = Path(__file__).resolve().parent
load_dotenv(BASE_DIR / ".env")

CLOUD_BOT_API_UPLOAD_LIMIT = 50 * 1024 * 1024
LOCAL_BOT_API_UPLOAD_LIMIT = 2_000 * 1024 * 1024


def _text(name: str, default: str = "") -> str:
    return os.getenv(name, default).strip()


def _integer(name: str, default: int, *, minimum: int = 0) -> int:
    raw = os.getenv(name)
    try:
        value = default if raw in {None, ""} else int(raw)
    except ValueError as exc:
        raise ValueError(f"{name} must be an integer, got {raw!r}") from exc
    if value < minimum:
        raise ValueError(f"{name} must be >= {minimum}, got {value}")
    return value


def _boolean(name: str, default: bool = False) -> bool:
    raw = os.getenv(name)
    if raw in {None, ""}:
        return default
    normalized = raw.strip().lower()
    if normalized in {"1", "true", "yes", "on"}:
        return True
    if normalized in {"0", "false", "no", "off"}:
        return False
    raise ValueError(f"{name} must be a boolean, got {raw!r}")


def _ids(name: str) -> tuple[int, ...]:
    raw = _text(name)
    if not raw:
        return ()
    try:
        return tuple(int(item.strip()) for item in raw.split(",") if item.strip())
    except ValueError as exc:
        raise ValueError(f"{name} must contain comma-separated integers") from exc


def _path(name: str, default: str) -> Path:
    value = Path(_text(name, default)).expanduser()
    return (value if value.is_absolute() else BASE_DIR / value).resolve()


@dataclass(frozen=True, slots=True)
class Settings:
    bot_token: str
    admin_ids: tuple[int, ...]
    temp_dir: Path
    stats_db_path: Path
    cookies_file: Path
    log_file: Path
    log_level: str
    bot_api_base_url: str
    bot_api_is_local: bool
    bot_api_use_file_uri: bool
    bot_api_upload_limit: int
    max_file_size: int
    send_as_doc_limit: int
    max_concurrent_downloads: int
    max_queue_size: int
    max_tasks_per_user: int
    max_playlist_items: int
    selection_ttl_seconds: int
    download_timeout_seconds: int
    download_stall_timeout_seconds: int
    download_rate_limit_bytes: int
    progress_update_seconds: int

    @property
    def effective_upload_limit(self) -> int:
        return min(self.max_file_size, self.bot_api_upload_limit)


def build_settings() -> Settings:
    base_url = _text("BOT_API_BASE_URL").rstrip("/")
    is_local = bool(base_url) and _boolean("BOT_API_IS_LOCAL", True)
    api_limit = LOCAL_BOT_API_UPLOAD_LIMIT if is_local else CLOUD_BOT_API_UPLOAD_LIMIT
    max_file_size = _integer("MAX_FILE_SIZE", api_limit, minimum=1)

    return Settings(
        bot_token=_text("BOT_TOKEN"),
        admin_ids=_ids("ADMIN_IDS"),
        temp_dir=_path("TEMP_DIR", "temp_downloads"),
        stats_db_path=_path("STATS_DB_PATH", _text("DB_NAME", "database.db")),
        cookies_file=_path("COOKIES_FILE", "cookies.txt"),
        log_file=_path("LOG_FILE", "bot.log"),
        log_level=_text("LOG_LEVEL", "INFO").upper() or "INFO",
        bot_api_base_url=base_url,
        bot_api_is_local=is_local,
        bot_api_use_file_uri=is_local and _boolean("BOT_API_USE_FILE_URI", False),
        bot_api_upload_limit=api_limit,
        max_file_size=max_file_size,
        send_as_doc_limit=_integer("SEND_AS_DOC_LIMIT", api_limit, minimum=1),
        max_concurrent_downloads=_integer("MAX_CONCURRENT_DOWNLOADS", 2, minimum=1),
        max_queue_size=_integer("MAX_QUEUE_SIZE", 100, minimum=1),
        max_tasks_per_user=_integer("MAX_TASKS_PER_USER", 10, minimum=1),
        max_playlist_items=_integer("MAX_PLAYLIST_ITEMS", 25, minimum=1),
        selection_ttl_seconds=_integer("SELECTION_TTL_SECONDS", 900, minimum=60),
        download_timeout_seconds=_integer("DOWNLOAD_TIMEOUT_SECONDS", 1800, minimum=30),
        download_stall_timeout_seconds=_integer("DOWNLOAD_STALL_TIMEOUT_SECONDS", 180, minimum=30),
        download_rate_limit_bytes=_integer("DOWNLOAD_RATE_LIMIT_BYTES", 0, minimum=0),
        progress_update_seconds=_integer("PROGRESS_UPDATE_SECONDS", 3, minimum=1),
    )


def validate_settings(settings: Settings | None = None) -> Settings:
    resolved = settings or SETTINGS
    if not resolved.bot_token:
        raise RuntimeError("BOT_TOKEN is required. Add it to .env or the environment.")
    if resolved.log_level not in {"CRITICAL", "ERROR", "WARNING", "INFO", "DEBUG"}:
        raise RuntimeError("LOG_LEVEL must be CRITICAL, ERROR, WARNING, INFO or DEBUG")
    if resolved.send_as_doc_limit > resolved.max_file_size:
        raise RuntimeError("SEND_AS_DOC_LIMIT cannot be greater than MAX_FILE_SIZE")
    if resolved.download_stall_timeout_seconds >= resolved.download_timeout_seconds:
        raise RuntimeError(
            "DOWNLOAD_STALL_TIMEOUT_SECONDS must be less than DOWNLOAD_TIMEOUT_SECONDS"
        )
    return resolved


SETTINGS = build_settings()

# Compatibility constants for deployment scripts and third-party imports.
BOT_TOKEN = SETTINGS.bot_token
ADMIN_IDS = SETTINGS.admin_ids
TEMP_DIR = str(SETTINGS.temp_dir)
STATS_DB_PATH = str(SETTINGS.stats_db_path)
DB_NAME = STATS_DB_PATH
COOKIES_FILE = str(SETTINGS.cookies_file)
LOG_LEVEL = SETTINGS.log_level
BOT_API_BASE_URL = SETTINGS.bot_api_base_url
BOT_API_IS_LOCAL = SETTINGS.bot_api_is_local
BOT_API_USE_FILE_URI = SETTINGS.bot_api_use_file_uri
BOT_API_UPLOAD_LIMIT = SETTINGS.bot_api_upload_limit
MAX_FILE_SIZE = SETTINGS.max_file_size
SEND_AS_DOC_LIMIT = SETTINGS.send_as_doc_limit
MAX_CONCURRENT_DOWNLOADS = SETTINGS.max_concurrent_downloads
DOWNLOAD_TIMEOUT_SECONDS = SETTINGS.download_timeout_seconds
DOWNLOAD_STALL_TIMEOUT_SECONDS = SETTINGS.download_stall_timeout_seconds
DOWNLOAD_RATE_LIMIT_BYTES = SETTINGS.download_rate_limit_bytes
