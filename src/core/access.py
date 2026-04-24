from __future__ import annotations

from dataclasses import dataclass

import config
from ..utils.message_templates import ErrorMessages


@dataclass(frozen=True)
class UserLimits:
    tier: str
    max_video_duration: int
    max_playlist_items: int
    max_file_size: int
    max_upload_size: int


def is_premium_user(user_id: int) -> bool:
    if user_id <= 0:
        return False
    return user_id in config.ADMIN_IDS or user_id in config.VIP_USERS


def get_user_limits(user_id: int) -> UserLimits:
    tier = "premium" if is_premium_user(user_id) else "free"
    return UserLimits(
        tier=tier,
        max_video_duration=config.MAX_VIDEO_DURATION[tier],
        max_playlist_items=config.MAX_PLAYLIST_ITEMS[tier],
        max_file_size=config.MAX_FILE_SIZE,
        max_upload_size=config.BOT_API_UPLOAD_LIMIT,
    )


def build_duration_limit_error(user_id: int, duration_seconds: int | float | None) -> str | None:
    if not duration_seconds:
        return None

    limits = get_user_limits(user_id)
    duration_seconds = int(duration_seconds)
    if duration_seconds <= limits.max_video_duration:
        return None

    max_duration_minutes = max(1, limits.max_video_duration // 60)
    return ErrorMessages.VIDEO_DURATION_LIMIT.format(max_duration=max_duration_minutes)


def build_file_size_limit_error(user_id: int, file_size_bytes: int) -> str | None:
    limits = get_user_limits(user_id)
    effective_limit = min(limits.max_file_size, limits.max_upload_size)
    if file_size_bytes <= effective_limit:
        return None

    size_mb = effective_limit / (1024 * 1024)
    if limits.max_upload_size < limits.max_file_size:
        return (
            f"{ErrorMessages.FILE_SIZE_LIMIT}\n\n"
            f"Текущий Bot API принимает файлы до {size_mb:.0f} MB. "
            "Для отправки до 2000 MB нужен локальный telegram-bot-api "
            "и BOT_API_BASE_URL. Без root/Docker оставьте облачный Bot API "
            "и ограничьте размер файлов до 50 MB."
        )

    return (
        f"{ErrorMessages.FILE_SIZE_LIMIT}\n\n"
        f"Лимит для вашей учетной записи: {size_mb:.0f} MB."
    )


def collect_playlist_entries(info: dict) -> list[dict]:
    entries = info.get("entries") or []
    resolved_entries: list[dict] = []
    for entry in entries:
        if not entry:
            continue
        entry_url = entry.get("webpage_url") or entry.get("url")
        if not entry_url:
            continue
        normalized_entry = dict(entry)
        normalized_entry["resolved_url"] = entry_url
        resolved_entries.append(normalized_entry)
    return resolved_entries


def build_playlist_limit_error(user_id: int, entries_count: int) -> str | None:
    limits = get_user_limits(user_id)
    if limits.max_playlist_items <= 0:
        return (
            "🎶 Скачивание плейлистов доступно только для premium-пользователей.\n\n"
            "Отправьте обычную ссылку на один ролик или добавьте пользователя в VIP."
        )

    if entries_count > limits.max_playlist_items:
        return ErrorMessages.PLAYLIST_LIMIT.format(max_items=limits.max_playlist_items)

    return None
