from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import UTC, datetime
from html import escape

from .theme import emoji, footer


@dataclass(frozen=True, slots=True)
class MessageContent:
    html: str
    rich_html: str
    rich_lite: str | None = None


ERROR_DETAILS = (
    (
        ("проверка ссылок занята",),
        "hourglass",
        "Сейчас много запросов",
        "Пришлите ссылку ещё раз через минуту.",
    ),
    (("cancel", "отмен"), "stop", "Загрузка отменена", "Пришлите ссылку снова, если передумали."),
    (
        ("timeout", "timed out", "no progress"),
        "timer",
        "Сайт отвечает слишком медленно",
        "Попробуйте позже или выберите качество пониже.",
    ),
    (
        ("disk quota", "no space"),
        "warning",
        "На сервере закончилось место",
        "Попробуйте через пару минут.",
    ),
    (
        ("too large", "file size", "exceed", "превыш"),
        "warning",
        "Файл больше лимита Telegram",
        "Выберите качество ниже или MP3.",
    ),
    (
        ("private", "sign in", "login", "cookies", "age"),
        "lock",
        "Публикация закрыта",
        "Нужен вход в аккаунт или возрастное подтверждение — такие ссылки я не открою.",
    ),
    (
        ("unsupported url", "not a valid url"),
        "search",
        "Здесь нет видео",
        "Пришлите ссылку на саму публикацию, а не на профиль или главную.",
    ),
    (
        ("video unavailable", "404", "not found", "removed"),
        "cross",
        "Публикация недоступна",
        "Похоже, её удалили или скрыли.",
    ),
    (
        ("requested format", "format is not available"),
        "video",
        "Этого качества нет",
        "Выберите другое качество.",
    ),
    (
        ("not available in your country", "geo"),
        "globe",
        "Недоступно в регионе сервера",
        "Сайт ограничил показ этой публикации.",
    ),
    (
        ("403", "forbidden", "unexpected response", "tiktok fallback", "rate"),
        "shield",
        "Сайт не пустил",
        "Сайт временно ограничил скачивание. Попробуйте позже.",
    ),
    (
        ("ffmpeg", "worker exited"),
        "settings",
        "Не получилось обработать файл",
        "Попробуйте другое качество.",
    ),
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
    rich = f"<h3>{emoji(icon)} {safe_title}</h3>{rich_body}<footer>{footer()}</footer>"
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
        return "<table compact>" + "".join(rows) + "</table>"
    paragraphs = "\n".join(str(line) for line in lines).strip().split("\n\n")
    return "".join(
        "<p>" + escape(part).replace("\n", "<br>") + "</p>" for part in paragraphs if part
    )


def as_content(value: str | MessageContent) -> MessageContent:
    return value if isinstance(value, MessageContent) else panel("ReSave", [value])


def tg_time(timestamp: float | None, fmt: str = "D") -> str:
    if not timestamp:
        return "—"
    moment = datetime.fromtimestamp(float(timestamp), UTC)
    fallback = moment.strftime("%d.%m.%Y %H:%M UTC")
    return f'<tg-time unix="{int(timestamp)}" format="{fmt}">{fallback}</tg-time>'


def parse_iso(value: str | None) -> float | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(value).timestamp()
    except ValueError:
        return None


def format_duration(value: object) -> str:
    try:
        seconds = max(0, round(float(value)))
    except (OverflowError, TypeError, ValueError):
        return ""
    hours, remainder = divmod(seconds, 3600)
    minutes, seconds = divmod(remainder, 60)
    return f"{hours}:{minutes:02}:{seconds:02}" if hours else f"{minutes}:{seconds:02}"


def format_size(size_bytes: float) -> str:
    size = float(size_bytes or 0)
    if size >= 1024**3:
        return f"{size / 1024**3:.2f} ГБ"
    if size >= 1024**2:
        return f"{size / 1024**2:.1f} МБ"
    return f"{max(1, round(size / 1024))} КБ"


def format_count(value: object) -> str:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return ""
    for limit, suffix in ((1e9, " млрд"), (1e6, " млн"), (1e3, " тыс.")):
        if number >= limit:
            return f"{number / limit:.1f}".rstrip("0").rstrip(".") + suffix
    return str(round(number))


def progress_bar(progress: float, width: int = 12) -> str:
    value = min(1.0, max(0.0, progress)) if math.isfinite(progress) else 0.0
    filled = round(value * width)
    return f"{'▰' * filled}{'▱' * (width - filled)} {value:.0%}"


def home_card(
    *,
    upload_limit: int | None = None,
    playlist_limit: int = 25,
    user_limit: int = 10,
    first_name: str | None = None,
) -> MessageContent:
    greeting = f"Привет, {clip(first_name, 40)}!" if first_name else "Привет!"
    limit = format_size(upload_limit) if upload_limit else None
    extras = [
        ("video", "GIF из роликов до 30 секунд"),
        ("subtitle", "Субтитры и обложки в оригинале"),
        ("playlist", f"Плейлисты — до {playlist_limit} видео за раз"),
        ("chat", "В группах качаю сам, без лишних вопросов"),
    ]
    if limit:
        extras.append(("download", f"Файлы до {limit}"))
    rich = f"<h1>{emoji('brand')} ReSave</h1>"
    rich += f"<p><b>{escape(greeting)}</b> Я скачиваю видео, музыку и фото по ссылке.</p>"
    rich += (
        "<table compact><tr>"
        f"<td>{emoji('video')} <b>Видео</b><br>до 4K</td>"
        f"<td>{emoji('audio')} <b>Музыка</b><br>MP3</td>"
        f"<td>{emoji('picture')} <b>Фото</b><br>альбомы</td>"
        "</tr></table>"
    )
    rich += (
        f"<blockquote>{emoji('arrow')} <b>Просто пришлите ссылку.</b><br>"
        "Покажу превью, а вы выберете качество.</blockquote>"
    )
    rich += (
        "<p><b>YouTube · TikTok · Instagram · X · VK · Reddit · Vimeo</b> и ещё 1000+ сайтов</p>"
    )
    rich += "<details><summary>Что ещё умею</summary><ul>"
    rich += "".join(f"<li>{emoji(icon)} {escape(text)}</li>" for icon, text in extras)
    rich += f"<li>{emoji('downloads')} Одновременно — до {user_limit} задач</li>"
    rich += "</ul></details>"
    rich += f"<footer>{footer()}</footer>"
    classic = panel(
        "ReSave",
        [
            greeting,
            "Скачиваю видео, музыку и фото по ссылке.",
            "",
            "Просто пришлите ссылку — покажу превью и форматы.",
            "",
            "YouTube · TikTok · Instagram · X · VK · Reddit · Vimeo",
            *[text for _, text in extras],
        ],
    )
    return MessageContent(classic.html, rich)


def media_caption(
    title: str,
    url: str,
    *,
    kind: str,
    size_mb: float | None = None,
    details: list[str] | None = None,
    bot_username: str | None = None,
    note: str | None = None,
) -> str:
    icon = {
        "video": "video",
        "audio": "audio",
        "subtitles": "subtitle",
        "gif": "camera",
        "thumbnail": "picture",
        "tiktok_photo": "picture",
    }.get(kind, "downloads")
    lines = [f"{emoji(icon)} <b>{escape(clip(title or 'Файл', 180))}</b>"]
    facts = [escape(clip(item, 60)) for item in details or [] if item]
    if size_mb is not None:
        facts.append(format_size(size_mb * 1024 * 1024))
    if facts:
        lines.append(" · ".join(facts))
    source = f'{emoji("link")} <a href="{escape(url, quote=True)}">Оригинал</a>'
    if bot_username:
        source += f" · {emoji('download')} @{escape(bot_username)}"
    lines.append(source)
    if note:
        lines.append(f"<blockquote>{note}</blockquote>")
    return "\n".join(lines)


def user_error(exc: BaseException) -> MessageContent:
    value = str(exc).lower()
    icon, title, hint = next(
        (
            (icon, title, hint)
            for markers, icon, title, hint in ERROR_DETAILS
            if any(marker in value for marker in markers)
        ),
        ("warning", "Не получилось скачать", "Попробуйте ещё раз или пришлите другую ссылку."),
    )
    rich = f"<h3>{emoji(icon)} {escape(title)}</h3><p>{escape(hint)}</p>"
    rich += (
        "<details><summary>Что ещё можно попробовать</summary><ul>"
        "<li>Проверить, открывается ли ссылка без входа в аккаунт</li>"
        "<li>Выбрать качество ниже или MP3</li>"
        "<li>Повторить через пару минут — сайты иногда ограничивают скачивание</li>"
        "</ul></details>"
    )
    rich += f"<footer>{footer()}</footer>"
    classic = panel(title, [hint], icon=icon)
    return MessageContent(classic.html, rich)
