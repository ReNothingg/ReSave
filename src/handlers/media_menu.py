from __future__ import annotations

from html import escape

from aiogram.types import InlineKeyboardMarkup

from ..core.models import DownloadAction
from ..utils.cards import card
from ..utils.presentation import MessageContent, clip, format_duration
from ..utils.theme import button, emoji


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


def media_keyboard(
    token: str,
    info: dict,
    resolutions: list[int],
    *,
    ffmpeg_available: bool,
) -> InlineKeyboardMarkup:
    choices = available_choices(info, resolutions, ffmpeg_available=ffmpeg_available)
    rows = []
    formats = []
    resolutions_row = []
    icons = {
        DownloadAction.BEST: "brand",
        DownloadAction.RESOLUTION: "video",
        DownloadAction.MEDIUM: "video",
        DownloadAction.LOW: "video",
        DownloadAction.AUDIO: "audio",
        DownloadAction.GIF: "video",
        DownloadAction.SUBTITLES: "subtitle",
        DownloadAction.THUMBNAIL: "downloads",
    }
    for action, height, label in choices:
        data = f"media|{token}|{action}" + (f"|{height}" if height else "")
        item = button(
            label,
            icon=icons[action],
            style="success"
            if action == DownloadAction.BEST
            else (
                "primary"
                if action in {DownloadAction.RESOLUTION, DownloadAction.MEDIUM, DownloadAction.LOW}
                else None
            ),
            callback_data=data,
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
    rows.extend(formats[offset : offset + 2] for offset in range(0, len(formats), 2))
    rows.append([button("✕ Отмена", style="danger", callback_data=f"media|{token}|cancel")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def media_text(
    info: dict,
    *,
    upload_limit: int | None = None,
    ffmpeg_available: bool = True,
) -> MessageContent:
    facts = []
    if info.get("uploader"):
        facts.append(("Автор", clip(info["uploader"], 80)))
    duration = format_duration(info.get("duration"))
    if duration:
        facts.append(("Длительность", duration))
    body = (
        "<table><tr>"
        + "".join(f"<td>{label}<br><b>{escape(value)}</b></td>" for label, value in facts)
        + "</tr></table>"
        if facts
        else ""
    )
    body += (
        "<blockquote>Выберите качество видео или сохраните только то, что нужно.</blockquote>"
        if info.get("has_video", True)
        else "<blockquote>Выберите формат сохранения.</blockquote>"
    )
    options = []
    if ffmpeg_available and info.get("has_audio", True):
        options.append(("audio", "MP3 — только аудио"))
    if info.get("subtitles") or info.get("automatic_captions"):
        options.append(("subtitle", "Субтитры"))
    if info.get("thumbnail"):
        options.append(("downloads", "Обложка без сжатия"))
    if options:
        body += "<details><summary>Дополнительные форматы</summary><ul>"
        body += (
            "".join(f"<li>{emoji(icon)} {text}</li>" for icon, text in options) + "</ul></details>"
        )
    note = (
        "Если файл слишком большой, попробую качество ниже." if info.get("has_video", True) else ""
    )
    if upload_limit is not None:
        note = f"Лимит файла: {upload_limit / 1048576:g} МБ. " + note
    if note:
        body += f"<p>{escape(note.strip())}</p>"
    fallback = [f"{label}: {value}" for label, value in facts] + ["", "Выберите формат."]
    fallback += [text for _, text in options] + [note.strip()]
    return card(
        clip(info.get("title") or "Публикация", 200),
        body,
        fallback,
        icon="video" if info.get("has_video", True) else "audio",
    )
