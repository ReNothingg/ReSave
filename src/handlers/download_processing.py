from __future__ import annotations

import logging

import config
from ..core.access import (
    build_duration_limit_error,
    build_playlist_limit_error,
    collect_playlist_entries,
)
from ..utils.ui_manager import get_ui_manager

logger = logging.getLogger(__name__)


def queue_playlist_downloads(
    *,
    download_manager,
    chat_id: int,
    message_id: int,
    reply_to_id: int,
    info: dict,
    silent_mode: bool,
) -> int:
    playlist_entries = collect_playlist_entries(info)
    queued = 0

    for entry in playlist_entries:
        download_manager.add_task(
            url=entry["resolved_url"],
            chat_id=chat_id,
            message_id=message_id,
            info={
                "id": entry.get("id"),
                "title": entry.get("title") or "video",
                "uploader": entry.get("uploader") or info.get("uploader"),
                "duration": entry.get("duration"),
            },
            action="medium",
            reply_to_id=reply_to_id,
            silent_mode=silent_mode,
        )
        queued += 1

    return queued


def build_playlist_queued_text(info: dict, queued_count: int) -> str:
    ui_manager = get_ui_manager()
    playlist_title = info.get("title") or "Плейлист"
    return ui_manager.format_panel(
        "Плейлист в очереди",
        [
            f"Название: {playlist_title}",
            f"Видео в очереди: {queued_count}",
            "Качество: 720p",
            "",
            "Файлы будут приходить по мере готовности.",
        ],
        icon="🎶",
    )


def handle_group_download(url: str, chat_id: int, message_id: int, download_manager):
    try:
        import yt_dlp

        ydl_opts = {
            "quiet": True,
            "no_warnings": True,
            "skip_download": True,
            "socket_timeout": 30,
            "cookiefile": config.COOKIES_FILE,
            "nocheckcertificate": True,
        }

        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            info = ydl.extract_info(url, download=False)

        if not info:
            logger.warning("Не удалось получить информацию для %s в группе %s", url, chat_id)
            return

        if info.get("_type") == "playlist":
            playlist_entries = collect_playlist_entries(info)
            playlist_error = build_playlist_limit_error(chat_id, len(playlist_entries))
            if playlist_error:
                logger.info("Плейлист %s в группе %s отклонен: %s", url, chat_id, playlist_error)
                return
            queue_playlist_downloads(
                download_manager=download_manager,
                chat_id=chat_id,
                message_id=message_id,
                reply_to_id=message_id,
                info=info,
                silent_mode=True,
            )
            return

        duration_error = build_duration_limit_error(chat_id, info.get("duration"))
        if duration_error:
            logger.info("Ссылка %s в группе %s отклонена: %s", url, chat_id, duration_error)
            return

        download_manager.add_task(
            url=url,
            chat_id=chat_id,
            message_id=message_id,
            info=info,
            action="medium",
            reply_to_id=message_id,
            silent_mode=True,
        )
    except Exception as exc:
        logger.error("Ошибка при обработке ссылки из группы %s: %s", chat_id, exc)


def extract_video_info(
    bot,
    chat_id: int,
    user_message_id: int,
    url: str,
    status_message_id: int,
    cache: dict[int, dict],
    *,
    download_manager,
    build_download_limit_text,
    build_download_markup,
):
    ui_manager = get_ui_manager()
    try:
        from ..core.video_info import fetch_video_info

        if "tiktok.com" in url and "/photo/" in url:
            if not download_manager.can_add_task(chat_id):
                bot.edit_message_text(
                    build_download_limit_text(chat_id),
                    chat_id,
                    status_message_id,
                )
                return

            bot.edit_message_text(
                ui_manager.format_panel(
                    "TikTok фото",
                    ["Пост распознан. Добавляю фото в очередь."],
                    icon="🖼️",
                ),
                chat_id,
                status_message_id,
            )
            try:
                download_manager.add_task(
                    url=url,
                    chat_id=chat_id,
                    message_id=status_message_id,
                    info={"title": "TikTok Photo"},
                    action="tiktok_photo",
                    reply_to_id=user_message_id,
                    silent_mode=False,
                )
            except ValueError:
                bot.edit_message_text(
                    build_download_limit_text(chat_id),
                    chat_id,
                    status_message_id,
                )
            return

        info = fetch_video_info(url)
        if not info:
            bot.edit_message_text(
                ui_manager.format_panel(
                    "Не удалось прочитать ссылку",
                    ["Проверьте доступность видео и отправьте ссылку еще раз."],
                    icon="❌",
                ),
                chat_id,
                status_message_id,
            )
            return

        if info.get("_type") == "playlist":
            playlist_entries = collect_playlist_entries(info)
            playlist_error = build_playlist_limit_error(chat_id, len(playlist_entries))
            if playlist_error:
                bot.edit_message_text(
                    playlist_error,
                    chat_id,
                    status_message_id,
                )
                return

            try:
                queued_count = queue_playlist_downloads(
                    download_manager=download_manager,
                    chat_id=chat_id,
                    message_id=status_message_id,
                    reply_to_id=user_message_id,
                    info=info,
                    silent_mode=True,
                )
            except ValueError:
                bot.edit_message_text(
                    build_download_limit_text(chat_id),
                    chat_id,
                    status_message_id,
                )
                return

            if queued_count == 0:
                bot.edit_message_text(
                    "🎶 Не удалось извлечь элементы плейлиста.",
                    chat_id,
                    status_message_id,
                )
                return

            bot.edit_message_text(
                build_playlist_queued_text(info, queued_count),
                chat_id,
                status_message_id,
            )
            return

        duration_error = build_duration_limit_error(chat_id, info.get("duration"))
        if duration_error:
            bot.edit_message_text(
                duration_error,
                chat_id,
                status_message_id,
            )
            return

        resolutions: dict[int, dict] = {}
        for item in info.get("formats", []):
            height = item.get("height")
            if height and item.get("vcodec", "none") != "none":
                existing = resolutions.get(height)
                if existing is None or (item.get("filesize") or 0) > (existing.get("filesize") or 0):
                    resolutions[height] = item

        cache[user_message_id] = {
            "url": url,
            "info": info,
            "resolutions": resolutions,
            "chat_id": chat_id,
        }

        markup = build_download_markup(user_message_id, info, resolutions)
        lines = [f"🎬 {info.get('title', 'video')}"]

        if info.get("uploader"):
            lines.append(f"👤 {info['uploader']}")

        if info.get("duration"):
            minutes, seconds = divmod(int(info["duration"]), 60)
            lines.append(f"⏱️ {minutes:02d}:{seconds:02d}")

        lines.extend(["", "Выберите формат ниже."])
        bot.edit_message_text(
            ui_manager.format_panel("Видео найдено", lines, icon="✅"),
            chat_id,
            status_message_id,
            reply_markup=markup,
        )
    except Exception as exc:
        error_message = str(exc)
        if "This video is unavailable" in error_message:
            user_text = "❌ Это видео недоступно."
        elif "Private video" in error_message:
            user_text = "❌ Это приватное видео."
        else:
            user_text = ui_manager.format_panel(
                "Ошибка обработки",
                [error_message],
                icon="❌",
            )

        bot.edit_message_text(user_text, chat_id, status_message_id)
        logger.error("Ошибка при получении информации о видео %s: %s", url, exc)
