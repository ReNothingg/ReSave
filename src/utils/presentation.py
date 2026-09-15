from __future__ import annotations

import math
from html import escape

ERROR_DETAILS = (
    (("проверка ссылок занята",), "Ещё проверяю другую ссылку. Попробуйте через минуту."),
    (("cancel", "отмен"), "Загрузка отменена."),
    (("timeout", "timed out", "no progress"), "Сайт не ответил вовремя. Попробуйте позже."),
    (("disk quota", "no space"), "На сервере закончилось место. Попробуйте позже."),
    (
        ("too large", "file size", "exceed", "превыш"),
        "Файл слишком большой. Выберите качество ниже.",
    ),
    (("private", "sign in", "login", "cookies"), "Публикация закрыта или требует входа в аккаунт."),
    (
        ("unsupported url", "not a valid url"),
        "Не удалось найти медиа. Пришлите ссылку на саму публикацию.",
    ),
    (("video unavailable", "404", "not found"), "Публикация удалена или недоступна."),
    (("requested format", "format is not available"), "Это качество недоступно. Выберите другое."),
    (
        ("403", "forbidden", "unexpected response", "tiktok fallback"),
        "Сайт не разрешил скачать публикацию. Попробуйте позже.",
    ),
    (("ffmpeg", "worker exited"), "Не удалось обработать файл. Попробуйте другое качество."),
)


def clip(value: object, limit: int = 200) -> str:
    text = " ".join(str(value or "").split())
    return text if len(text) <= limit else text[: limit - 1] + "…"


def panel(title: str, lines: list[str] | tuple[str, ...] = ()) -> str:
    heading = f"<b>{escape(clip(title, 300))}</b>"
    body = "\n".join(escape(str(line)) for line in lines).strip()
    return f"{heading}\n\n{body}" if body else heading


def format_duration(value: object) -> str:
    try:
        seconds = max(0, round(float(value)))
    except (OverflowError, TypeError, ValueError):
        return ""
    hours, remainder = divmod(seconds, 3600)
    minutes, seconds = divmod(remainder, 60)
    return f"{hours}:{minutes:02}:{seconds:02}" if hours else f"{minutes}:{seconds:02}"


def progress_bar(progress: float, width: int = 10) -> str:
    value = min(1.0, max(0.0, progress)) if math.isfinite(progress) else 0.0
    filled = round(value * width)
    return f"{'▰' * filled}{'▱' * (width - filled)} {value:.0%}"


def media_caption(title: str, url: str, *, kind: str, size_mb: float | None = None) -> str:
    lines = [f"<b>{escape(clip(title or 'Файл', 240))}</b>"]
    details = [f"{size_mb:.1f} МБ"] if size_mb is not None else []
    details.append(f'<a href="{escape(url, quote=True)}">Источник</a>')
    lines.append(" · ".join(details))
    return "\n\n".join(lines)


def user_error(exc: BaseException) -> str:
    value = str(exc).lower()
    detail = next(
        (
            message
            for markers, message in ERROR_DETAILS
            if any(marker in value for marker in markers)
        ),
        "Не удалось скачать файл. Попробуйте ещё раз или пришлите другую ссылку.",
    )
    return panel("Не получилось скачать", [detail])
