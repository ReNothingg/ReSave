import logging
import yt_dlp

import config

logger = logging.getLogger(__name__)

cookie_path = config.COOKIES_FILE


def fetch_video_info(url):
    try:
        ydl_opts = {
            "quiet": True,
            "no_warnings": True,
            "extract_flat": False,
            "skip_download": True,
            "socket_timeout": 10,
            "retries": 2,
            "extractor_retries": 2,
            "cookiefile": cookie_path,
            "nocheckcertificate": True,
        }

        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            info = ydl.extract_info(url, download=False)
            return info

    except Exception as e:
        logger.info("Ссылка не поддерживается или недоступна: %s", e)
        return None


def check_subtitles_available(url):
    try:
        ydl_opts = {
            "quiet": True,
            "no_warnings": True,
            "extract_flat": False,
            "skip_download": True,
            "socket_timeout": 5,
            "retries": 2,
            "extractor_retries": 2,
            "cookiefile": cookie_path,
            "nocheckcertificate": True,
        }

        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            info = ydl.extract_info(url, download=False)

            if not info:
                return False

            subtitles = info.get('subtitles', {})
            auto_captions = info.get('automatic_captions', {})

            has_subtitles = bool(subtitles or auto_captions)

            return has_subtitles

    except Exception as e:
        logger.warning(f"Ошибка при проверке субтитров: {e}")
        return False
