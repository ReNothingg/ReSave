from __future__ import annotations

from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup

from ..core.models import DownloadAction
from ..utils.presentation import clip, format_duration, panel


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
    rows = []
    for action, height, label in available_choices(
        info, resolutions, ffmpeg_available=ffmpeg_available
    ):
        data = f"media|{token}|{action}"
        if height is not None:
            data += f"|{height}"
        button = InlineKeyboardButton(text=label, callback_data=data)
        if action == DownloadAction.BEST:
            rows.append([button])
        elif rows and len(rows[-1]) < 2 and rows[-1][0].text != "Лучшее качество":
            rows[-1].append(button)
        else:
            rows.append([button])
    rows.append([InlineKeyboardButton(text="Отмена", callback_data=f"media|{token}|cancel")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def media_text(info: dict) -> str:
    details = [clip(info.get("uploader"), 100), format_duration(info.get("duration"))]
    lines = [" · ".join(item for item in details if item), "", "Выберите формат."]
    if info.get("has_video", True):
        lines.append("Если файл слишком большой, попробую качество ниже.")
    return panel(clip(info.get("title") or "Публикация", 200), lines)
