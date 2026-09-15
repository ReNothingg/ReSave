from __future__ import annotations

import math
from dataclasses import dataclass
from html import escape

from .theme import emoji, footer


@dataclass(frozen=True, slots=True)
class MessageContent:
    html: str
    rich_html: str


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


def panel(
    title: str,
    lines: list[str] | tuple[str, ...] = (),
    *,
    icon: str = "brand",
    table: bool = False,
) -> MessageContent:
    safe_title = escape(clip(title, 200))
    heading = f"{emoji(icon)} <b>{safe_title}</b>"
    body = "\n".join(escape(str(line)) for line in lines).strip()
    classic = f"{heading}\n\n{body}\n\n{footer()}" if body else f"{heading}\n\n{footer()}"
    rich_body = render_body(lines, table=table)
    rich = f"<h2>{emoji(icon)} {safe_title}</h2>{rich_body}<hr/><footer>{footer()}</footer>"
    return MessageContent(classic, rich)


def render_body(lines: list[str] | tuple[str, ...], *, table: bool = False) -> str:
    if table:
        rows = []
        for line in lines:
            key, separator, value = str(line).partition(":")
            if separator:
                rows.append(
                    f"<tr><td>{escape(key)}</td><td><b>{escape(value.strip())}</b></td></tr>"
                )
            elif line:
                rows.append(f'<tr><td colspan="2">{escape(str(line))}</td></tr>')
        return "<table>" + "".join(rows) + "</table>"
    paragraphs = "\n".join(str(line) for line in lines).strip().split("\n\n")
    return "".join(
        "<p>" + escape(part).replace("\n", "<br>") + "</p>" for part in paragraphs if part
    )


def as_content(value: str | MessageContent) -> MessageContent:
    return value if isinstance(value, MessageContent) else panel("ReSave", [value])


def home_card(
    *,
    upload_limit: int | None = None,
    playlist_limit: int = 25,
    user_limit: int = 10,
) -> MessageContent:
    formats = [
        ("video", "Видео", "Качество на выбор"),
        ("audio", "Музыка", "MP3"),
        ("downloads", "Фото", "Альбомы и обложки"),
    ]
    sources = "YouTube · TikTok · Instagram · X / Twitter"
    extras = [
        "Ещё: GIF до 30 секунд, субтитры и обложки.",
        "В группах — автоматическое скачивание, с ориентиром на 720p.",
        f"Видео из плейлиста: до {playlist_limit}. Ваш лимит задач: {user_limit}.",
    ]
    if upload_limit is not None:
        extras.append(f"Размер файла — до {upload_limit / 1048576:g} МБ.")
    text = panel(
        "ReSave",
        [
            "Видео, музыка и фото по ссылке.",
            "",
            "Видео · Музыка · Фото",
            "",
            "Пришлите ссылку — выберите формат — получите файл.",
            "",
            sources,
            *extras,
        ],
    )
    rich = f"<h1>{emoji('brand')} ReSave</h1><p>Видео, музыка и фото по ссылке.</p>"
    rich += (
        "<table><tr>"
        + "".join(
            f"<td>{emoji(icon)} <b>{label}</b><br>{detail}</td>" for icon, label, detail in formats
        )
        + "</tr></table>"
    )
    rich += "<blockquote>Пришлите ссылку на публикацию.<br>Дальше — выбор качества и готовый файл.</blockquote>"
    rich += f"<p>{sources}</p>"
    rich += "<details><summary>Что ещё умеет бот</summary><ul>"
    rich += "".join(f"<li>{escape(line)}</li>" for line in extras)
    rich += "</ul></details>"
    rich += f"<hr/><footer>{footer()}</footer>"
    return MessageContent(text.html, rich)


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
    icon = {"video": "video", "audio": "audio", "subtitles": "subtitle", "gif": "video"}.get(
        kind, "downloads"
    )
    lines = [f"{emoji(icon)} <b>{escape(clip(title or 'Файл', 200))}</b>"]
    details = [f"{size_mb:.1f} МБ"] if size_mb is not None else []
    details.append(f'<a href="{escape(url, quote=True)}">Оригинал</a>')
    lines.append(" · ".join(details))
    lines.append(footer())
    return "\n\n".join(lines)


def user_error(exc: BaseException) -> MessageContent:
    value = str(exc).lower()
    detail = next(
        (
            message
            for markers, message in ERROR_DETAILS
            if any(marker in value for marker in markers)
        ),
        "Не удалось скачать файл. Попробуйте ещё раз или пришлите другую ссылку.",
    )
    return panel("Не получилось скачать", [detail], icon="help")
