from __future__ import annotations

from html import escape

from aiogram.types import InlineKeyboardMarkup

from ..core.models import DownloadAction
from ..utils.cards import card, metrics_table
from ..utils.presentation import (
    MessageContent,
    clip,
    format_count,
    format_duration,
    format_size,
    tg_time,
)
from ..utils.theme import button, emoji

SIZE_MARGIN = 0.97
SOURCES = {
    "youtube": "YouTube",
    "youtubetab": "YouTube",
    "tiktok": "TikTok",
    "instagram": "Instagram",
    "twitter": "X",
    "vk": "VK",
    "vkvideo": "VK Видео",
    "reddit": "Reddit",
    "vimeo": "Vimeo",
    "soundcloud": "SoundCloud",
    "twitch": "Twitch",
    "facebook": "Facebook",
    "pinterest": "Pinterest",
}


def available_choices(
    info: dict,
    resolutions: list[int] | tuple[int, ...],
    *,
    ffmpeg_available: bool,
) -> list[tuple[DownloadAction, int | None, str]]:
    choices = []
    if info.get("has_video", True):
        choices.append((DownloadAction.BEST, None, "Лучшее качество"))
        choices.extend(
            (DownloadAction.RESOLUTION, height, f"{height}p") for height in resolutions[:12]
        )
        if not resolutions:
            choices.extend(
                [
                    (DownloadAction.MEDIUM, None, "До 720p"),
                    (DownloadAction.LOW, None, "До 480p"),
                ]
            )
    if ffmpeg_available and info.get("has_audio", True):
        choices.append((DownloadAction.AUDIO, None, "MP3"))
    duration = info.get("duration")
    if (
        ffmpeg_available
        and info.get("has_video", True)
        and isinstance(duration, (int, float))
        and 0 < duration <= 30
    ):
        choices.append((DownloadAction.GIF, None, "GIF"))
    if info.get("subtitles") or info.get("automatic_captions"):
        choices.append((DownloadAction.SUBTITLES, None, "Субтитры"))
    if info.get("thumbnail"):
        choices.append((DownloadAction.THUMBNAIL, None, "Обложка"))
    return choices


def estimated_size(info: dict, height: int) -> int:
    candidates = [
        item
        for item in info.get("formats") or []
        if isinstance(item, dict) and item.get("height") == height
    ]
    if not candidates:
        return 0
    preferred = [item for item in candidates if str(item.get("vcodec", "")).startswith("avc1")]
    pool = preferred or candidates
    explicit = [float(item["filesize"]) for item in pool if item.get("filesize")]
    duration = float(info.get("duration") or 0)
    estimated = [
        float(item["tbr"]) * 1000 / 8 * duration for item in pool if item.get("tbr") and duration
    ]
    sizes = explicit or estimated
    if not sizes:
        return 0
    return round(max(sizes) + float(info.get("audio_size") or 0))


def media_keyboard(
    token: str,
    info: dict,
    resolutions: list[int],
    *,
    ffmpeg_available: bool,
    upload_limit: int | None = None,
) -> InlineKeyboardMarkup:
    choices = available_choices(info, resolutions, ffmpeg_available=ffmpeg_available)
    rows = []
    formats = []
    resolutions_row = []
    icons = {
        DownloadAction.BEST: "fire",
        DownloadAction.RESOLUTION: "video",
        DownloadAction.MEDIUM: "video",
        DownloadAction.LOW: "video",
        DownloadAction.AUDIO: "audio",
        DownloadAction.GIF: "camera",
        DownloadAction.SUBTITLES: "subtitle",
        DownloadAction.THUMBNAIL: "picture",
    }
    for action, height, label in choices:
        data = f"media|{token}|{action}" + (f"|{height}" if height else "")
        disabled = False
        if action == DownloadAction.RESOLUTION and height:
            size = estimated_size(info, height)
            if size and upload_limit and size > upload_limit * SIZE_MARGIN:
                label, disabled = f"{height}p · больше лимита", True
            elif size:
                label = f"{height}p · ≈{format_size(size)}"
        item = button(
            label,
            icon=icons[action],
            style="success"
            if action == DownloadAction.BEST
            else ("primary" if action == DownloadAction.AUDIO else None),
            callback_data=data,
            disabled=disabled,
        )
        if action == DownloadAction.BEST:
            rows.append([item])
        elif action in {DownloadAction.RESOLUTION, DownloadAction.MEDIUM, DownloadAction.LOW}:
            resolutions_row.append(item)
        else:
            formats.append(item)
    rows.extend(
        resolutions_row[offset : offset + 2] for offset in range(0, len(resolutions_row), 2)
    )
    rows.extend(formats[offset : offset + 3] for offset in range(0, len(formats), 3))
    rows.append(
        [button("Отмена", icon="cross", style="danger", callback_data=f"media|{token}|cancel")]
    )
    return InlineKeyboardMarkup(inline_keyboard=rows)


def media_facts(info: dict) -> list[tuple[str, str]]:
    candidates = [
        ("Автор", clip(info.get("uploader"), 60)),
        ("Длительность", format_duration(info.get("duration"))),
        ("Просмотры", format_count(info.get("view_count")) if info.get("view_count") else ""),
        ("Лайки", format_count(info.get("like_count")) if info.get("like_count") else ""),
        (
            "Источник",
            SOURCES.get(str(info.get("source") or "").lower(), clip(info.get("source"), 30)),
        ),
    ]
    return [(label, value) for label, value in candidates if value]


def media_extras(info: dict, ffmpeg_available: bool) -> list[str]:
    extras = []
    if ffmpeg_available and info.get("has_audio", True):
        extras.append(f"{emoji('audio')} MP3 — только звук")
    if info.get("subtitles") or info.get("automatic_captions"):
        extras.append(f"{emoji('subtitle')} Субтитры")
    if info.get("thumbnail"):
        extras.append(f"{emoji('picture')} Обложка в оригинале")
    return extras


def preview_block(info: dict, title: str) -> str | None:
    preview = info.get("thumbnail_url")
    if not preview:
        return None
    cite = f"<cite>{escape(clip(info['uploader'], 60))}</cite>" if info.get("uploader") else ""
    return (
        f'<figure><img src="{escape(preview, quote=True)}"/>'
        f"<figcaption>{escape(title)}{cite}</figcaption></figure>"
    )


def media_text(
    info: dict,
    *,
    upload_limit: int | None = None,
    ffmpeg_available: bool = True,
) -> MessageContent:
    title = clip(info.get("title") or "Публикация", 200)
    has_video = info.get("has_video", True)
    facts = media_facts(info)
    body = metrics_table(facts)
    if info.get("timestamp"):
        body += f"<p>{emoji('calendar')} Опубликовано {tg_time(info['timestamp'], 'D')}</p>"
    hint = (
        "Выберите качество. Если файл не влезет, возьму качество пониже."
        if has_video
        else "Выберите формат."
    )
    body += f"<blockquote>{emoji('arrow')} {escape(hint)}</blockquote>"
    extras = media_extras(info, ffmpeg_available)
    if extras:
        body += "<p>" + " · ".join(extras) + "</p>"
    if upload_limit and upload_limit <= 50 * 1024 * 1024 and (info.get("duration") or 0) > 300:
        body += (
            f"<p>{emoji('warning')} <i>Лимит файла — {format_size(upload_limit)}: "
            "длинное видео в высоком качестве не влезет, кнопки выше лимита неактивны.</i></p>"
        )
    elif upload_limit:
        body += f"<p><i>Лимит файла — {format_size(upload_limit)}</i></p>"
    fallback = [f"{label}: {value}" for label, value in facts] + ["", hint]
    content = card(title, body, fallback, icon="video" if has_video else "audio")
    preview = preview_block(info, title)
    if preview is None:
        return content
    return MessageContent(
        content.html, content.rich_html.replace("</h3>", "</h3>" + preview, 1), content.rich_html
    )
