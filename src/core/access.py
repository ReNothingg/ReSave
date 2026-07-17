from __future__ import annotations

import config
from ..utils.message_templates import ErrorMessages


def build_file_size_limit_error(file_size_bytes: int) -> str | None:
    effective_limit = min(config.MAX_FILE_SIZE, config.BOT_API_UPLOAD_LIMIT)
    if file_size_bytes <= effective_limit:
        return None

    size_mb = effective_limit / (1024 * 1024)
    if config.BOT_API_UPLOAD_LIMIT < config.MAX_FILE_SIZE:
        return (
            f"{ErrorMessages.FILE_SIZE_LIMIT}\n\n"
            f"Текущий Bot API принимает файлы до {size_mb:.0f} MB. "
            "Для отправки до 2000 MB нужен локальный telegram-bot-api "
            "и BOT_API_BASE_URL. Без root/Docker оставьте облачный Bot API "
            "и ограничьте размер файлов до 50 MB."
        )

    return f"{ErrorMessages.FILE_SIZE_LIMIT}\n\nТехнический лимит отправки: {size_mb:.0f} MB."


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
