from __future__ import annotations

from html import escape


def panel(title: str, lines: list[str] | tuple[str, ...] = (), *, icon: str = "") -> str:
    heading = f"{icon} <b>{escape(title)}</b>".strip()
    body = "\n".join(escape(str(line)) for line in lines)
    return f"{heading}\n\n{body}" if body else heading


def progress_bar(progress: float, width: int = 12) -> str:
    value = min(1.0, max(0.0, progress))
    filled = round(value * width)
    return f"{'█' * filled}{'░' * (width - filled)} {value * 100:.0f}%"


def media_caption(title: str, url: str, *, kind: str, size_mb: float | None = None) -> str:
    icons = {
        "video": "🎬",
        "audio": "🎵",
        "gif": "✨",
        "thumbnail": "🖼️",
        "subtitles": "📝",
        "tiktok_photo": "🖼️",
    }
    safe_title = escape(title or "media")
    safe_url = escape(url, quote=True)
    lines = [f"{icons.get(kind, '📁')} <b>{safe_title}</b>"]
    if size_mb is not None:
        lines.append(f"📦 {size_mb:.1f} MB")
    lines.extend([f'🔗 <a href="{safe_url}">Открыть оригинал</a>', "⚡ @ReSafeBot"])
    return "\n\n".join(lines)


def user_error(exc: BaseException) -> str:
    value = str(exc).lower()
    if "private" in value:
        detail = "Видео приватное или недоступно для аккаунта бота."
    elif "unsupported url" in value or "not a valid url" in value:
        detail = "Эта ссылка не поддерживается. Отправьте прямую ссылку на публикацию."
    elif "sign in" in value or "login" in value or "cookies" in value:
        detail = "Источник требует авторизацию. Проверьте актуальность cookies.txt."
    elif "too large" in value or "file size" in value or "превышает" in value:
        detail = "Файл больше доступного лимита Telegram. Выберите качество ниже."
    elif "ffmpeg" in value:
        detail = "FFmpeg недоступен или не смог обработать медиа."
    elif "cancel" in value or "отмен" in value:
        detail = "Загрузка отменена."
    elif "timeout" in value or "no progress" in value:
        detail = "Источник отвечал слишком долго. Попробуйте ещё раз позже."
    else:
        detail = "Не удалось обработать медиа. Проверьте ссылку и повторите попытку."
    return panel("Ошибка", [detail], icon="❌")
