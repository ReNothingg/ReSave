from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv

BASE_DIR = Path(__file__).resolve().parent
load_dotenv(BASE_DIR / ".env")

CLOUD_BOT_API_UPLOAD_LIMIT = 50 * 1024 * 1024
LOCAL_BOT_API_UPLOAD_LIMIT = 2000 * 1024 * 1024


def _get_str(name: str, default: str = "") -> str:
    return os.getenv(name, default).strip()


def _get_int(name: str, default: int, *, minimum: int | None = None) -> int:
    raw_value = os.getenv(name)
    if raw_value in {None, ""}:
        value = default
    else:
        try:
            value = int(raw_value)
        except ValueError as exc:
            raise ValueError(f"{name} must be an integer, got: {raw_value!r}") from exc

    if minimum is not None and value < minimum:
        raise ValueError(f"{name} must be >= {minimum}, got: {value}")

    return value


def _get_bool(name: str, default: bool = False) -> bool:
    raw_value = os.getenv(name)
    if raw_value in {None, ""}:
        return default

    normalized = raw_value.strip().lower()
    if normalized in {"1", "true", "yes", "on"}:
        return True
    if normalized in {"0", "false", "no", "off"}:
        return False

    raise ValueError(f"{name} must be a boolean, got: {raw_value!r}")


def _get_id_list(name: str) -> tuple[int, ...]:
    raw_value = os.getenv(name, "")
    if not raw_value.strip():
        return ()

    values: list[int] = []
    for item in raw_value.split(","):
        item = item.strip()
        if not item:
            continue
        try:
            values.append(int(item))
        except ValueError as exc:
            raise ValueError(f"{name} must contain comma-separated integers, got: {raw_value!r}") from exc
    return tuple(values)


def _resolve_path(value: str) -> str:
    path = Path(value)
    if not path.is_absolute():
        path = BASE_DIR / path
    return str(path.resolve())


@dataclass(frozen=True)
class Settings:
    bot_token: str
    temp_dir: str
    max_concurrent_downloads: int
    max_downloads_per_user: int
    max_file_size: int
    send_as_doc_limit: int
    bot_api_base_url: str
    bot_api_is_local: bool
    bot_api_upload_limit: int
    cookies_file: str
    stats_db_path: str
    admin_ids: tuple[int, ...]
    vip_users: tuple[int, ...]
    log_level: str
    download_timeout_seconds: int
    download_rate_limit_bytes: int
    max_download_height: int
    max_video_duration: dict[str, int]
    max_playlist_items: dict[str, int]


def build_settings() -> Settings:
    temp_dir = _resolve_path(_get_str("TEMP_DIR", "temp_downloads"))
    cookies_file = _resolve_path(_get_str("COOKIES_FILE", "cookies.txt"))
    stats_db_path = _resolve_path(_get_str("STATS_DB_PATH", _get_str("DB_NAME", "database.db")))
    bot_api_base_url = _get_str("BOT_API_BASE_URL")
    bot_api_is_local = _get_bool("BOT_API_IS_LOCAL", bool(bot_api_base_url))
    bot_api_upload_limit = (
        LOCAL_BOT_API_UPLOAD_LIMIT if bot_api_is_local else CLOUD_BOT_API_UPLOAD_LIMIT
    )

    return Settings(
        bot_token=_get_str("BOT_TOKEN"),
        temp_dir=temp_dir,
        max_concurrent_downloads=_get_int("MAX_CONCURRENT_DOWNLOADS", 1, minimum=1),
        max_downloads_per_user=_get_int("MAX_DOWNLOADS_PER_USER", 1, minimum=0),
        max_file_size=_get_int("MAX_FILE_SIZE", 2 * 1024 * 1024 * 1024, minimum=1),
        send_as_doc_limit=_get_int("SEND_AS_DOC_LIMIT", bot_api_upload_limit, minimum=1),
        bot_api_base_url=bot_api_base_url,
        bot_api_is_local=bot_api_is_local,
        bot_api_upload_limit=bot_api_upload_limit,
        cookies_file=cookies_file,
        stats_db_path=stats_db_path,
        admin_ids=_get_id_list("ADMIN_IDS"),
        vip_users=_get_id_list("VIP_USERS"),
        log_level=_get_str("LOG_LEVEL", "INFO").upper() or "INFO",
        download_timeout_seconds=_get_int("DOWNLOAD_TIMEOUT_SECONDS", 420, minimum=30),
        download_rate_limit_bytes=_get_int(
            "DOWNLOAD_RATE_LIMIT_BYTES",
            2 * 1024 * 1024,
            minimum=0,
        ),
        max_download_height=_get_int("MAX_DOWNLOAD_HEIGHT", 720, minimum=144),
        max_video_duration={
            "free": _get_int("MAX_VIDEO_DURATION_FREE", 900, minimum=0),
            "premium": _get_int("MAX_VIDEO_DURATION_PREMIUM", 10800, minimum=0),
        },
        max_playlist_items={
            "free": _get_int("MAX_PLAYLIST_ITEMS_FREE", 0, minimum=0),
            "premium": _get_int("MAX_PLAYLIST_ITEMS_PREMIUM", 50, minimum=0),
        },
    )


def validate_settings(settings: Settings | None = None) -> Settings:
    resolved = settings or SETTINGS
    if not resolved.bot_token:
        raise RuntimeError("BOT_TOKEN is required. Add it to the environment or .env file.")

    log_level_name = resolved.log_level.upper()
    if log_level_name not in {"CRITICAL", "ERROR", "WARNING", "INFO", "DEBUG"}:
        raise RuntimeError(
            "LOG_LEVEL must be one of CRITICAL, ERROR, WARNING, INFO, DEBUG."
        )

    if resolved.send_as_doc_limit > resolved.max_file_size:
        raise RuntimeError("SEND_AS_DOC_LIMIT cannot be greater than MAX_FILE_SIZE.")

    return resolved


SETTINGS = build_settings()

BOT_TOKEN = SETTINGS.bot_token
TEMP_DIR = SETTINGS.temp_dir
MAX_CONCURRENT_DOWNLOADS = SETTINGS.max_concurrent_downloads
MAX_DOWNLOADS_PER_USER = SETTINGS.max_downloads_per_user
MAX_FILE_SIZE = SETTINGS.max_file_size
SEND_AS_DOC_LIMIT = SETTINGS.send_as_doc_limit
BOT_API_BASE_URL = SETTINGS.bot_api_base_url
BOT_API_IS_LOCAL = SETTINGS.bot_api_is_local
BOT_API_UPLOAD_LIMIT = SETTINGS.bot_api_upload_limit
COOKIES_FILE = SETTINGS.cookies_file
DB_NAME = SETTINGS.stats_db_path
STATS_DB_PATH = SETTINGS.stats_db_path
ADMIN_IDS = SETTINGS.admin_ids
VIP_USERS = SETTINGS.vip_users
LOG_LEVEL = SETTINGS.log_level
DOWNLOAD_TIMEOUT_SECONDS = SETTINGS.download_timeout_seconds
DOWNLOAD_RATE_LIMIT_BYTES = SETTINGS.download_rate_limit_bytes
MAX_DOWNLOAD_HEIGHT = SETTINGS.max_download_height
MAX_VIDEO_DURATION = SETTINGS.max_video_duration
MAX_PLAYLIST_ITEMS = SETTINGS.max_playlist_items
