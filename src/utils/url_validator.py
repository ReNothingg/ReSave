import re
import logging
from typing import Tuple

logger = logging.getLogger(__name__)

class URLValidator:
    # Поддерживаемые платформы
    SUPPORTED_PLATFORMS = {
        "youtube": [r"youtube\.com", r"youtu\.be"],
        "tiktok": [r"tiktok\.com"],
        "instagram": [r"instagram\.com"],
        "twitter": [r"twitter\.com", r"x\.com"],
        "facebook": [r"facebook\.com"],
        "vimeo": [r"vimeo\.com"],
        "twitch": [r"twitch\.tv"],
        "reddit": [r"reddit\.com"],
        "dailymotion": [r"dailymotion\.com"],
        "pinterest": [r"pinterest\.com"],
        "linkedin": [r"linkedin\.com"],
        "soundcloud": [r"soundcloud\.com"],
    }

    def __init__(self):
        self.url_pattern = re.compile(
            r"^https?://",
            re.IGNORECASE
        )

    def validate(self, url: str) -> Tuple[bool, str, dict]:
        if not url or not isinstance(url, str):
            return False, None, {"error": "Пустая ссылка"}

        url = url.strip()

        if not self.url_pattern.match(url):
            corrected = f"https://{url}"

            if self._looks_like_url(corrected):
                logger.info(f"Ссылка исправлена: добавлен https:// к {url}")
                return True, corrected, {
                    "fixed": True,
                    "fix_type": "added_protocol",
                    "original": url
                }

        if not self._looks_like_url(url):
            return False, None, {"error": "Это не похоже на ссылку"}

        platform = self._detect_platform(url)

        return True, url, {
            "platform": platform,
            "valid": True
        }

    def fix_common_mistakes(self, url: str) -> str:
        url = url.strip()

        fixes = [
            # YouTube
            (r"youtube\.com/watch\?v=([^&]+)&.*", r"https://youtube.com/watch?v=\1"),
            (r"youtu\.be/([^?]+)", r"https://youtu.be/\1"),

            # TikTok
            (r"tiktok\.com/@([^/]+)/video/(\d+)", r"https://www.tiktok.com/@\1/video/\2"),

            # Instagram
            (r"instagram\.com/p/([a-zA-Z0-9_-]+)", r"https://www.instagram.com/p/\1/"),
            (r"instagram\.com/reel/([a-zA-Z0-9_-]+)", r"https://www.instagram.com/reel/\1/"),

            # Twitter
            (r"twitter\.com/([^/]+)/status/(\d+)", r"https://twitter.com/\1/status/\2"),
            (r"x\.com/([^/]+)/status/(\d+)", r"https://x.com/\1/status/\2"),
        ]

        for pattern, replacement in fixes:
            url = re.sub(pattern, replacement, url, flags=re.IGNORECASE)

        return url

    def is_valid_url(self, url: str) -> bool:
        is_valid, _, _ = self.validate(url)
        return is_valid

    def _looks_like_url(self, url: str) -> bool:
        # Должна быть точка и косые черты
        if "." not in url or "//" not in url:
            return False

        # Должна начинаться с http
        if not url.lower().startswith("http"):
            return False

        # Не должна содержать недопустимые символы в начале ссылки
        if any(c in url[:20] for c in [" ", "\n", "\r", "\t"]):
            return False

        return True

    def _detect_platform(self, url: str) -> str:
        url_lower = url.lower()

        for platform, patterns in self.SUPPORTED_PLATFORMS.items():
            for pattern in patterns:
                if re.search(pattern, url_lower):
                    return platform

        return "unknown"

    def suggest_fixes(self, invalid_url: str) -> list:
        suggestions = []

        # Попытка 1: добавить https://
        if not invalid_url.lower().startswith("http"):
            suggestion = f"https://{invalid_url}"
            if self._looks_like_url(suggestion):
                suggestions.append({
                    "url": suggestion,
                    "reason": "Добавлен протокол https://"
                })

        # Попытка 2: добавить www.
        if "www." not in invalid_url:
            suggestion = invalid_url.replace("://", "://www.", 1)
            if self._looks_like_url(suggestion):
                suggestions.append({
                    "url": suggestion,
                    "reason": "Добавлено www."
                })

        return suggestions

    def extract_video_id(self, url: str, platform: str = None) -> str:
        if not platform:
            platform = self._detect_platform(url)

        if platform == "youtube":
            # youtube.com/watch?v=ID или youtu.be/ID
            match = re.search(r"(?:youtube\.com/watch\?v=|youtu\.be/)([^&?\s]+)", url)
            if match:
                return match.group(1)

        elif platform == "tiktok":
            # tiktok.com/@username/video/ID
            match = re.search(r"/video/(\d+)", url)
            if match:
                return match.group(1)

        elif platform == "instagram":
            # instagram.com/p/ID/ или /reel/ID/
            match = re.search(r"(?:/p/|/reel/)([a-zA-Z0-9_-]+)", url)
            if match:
                return match.group(1)

        elif platform == "twitter" or platform == "x":
            # twitter.com/.../status/ID
            match = re.search(r"/status/(\d+)", url)
            if match:
                return match.group(1)

        elif platform == "vimeo":
            # vimeo.com/ID
            match = re.search(r"vimeo\.com/(\d+)", url)
            if match:
                return match.group(1)

        return None


_url_validator = None


def get_url_validator() -> URLValidator:
    global _url_validator
    if _url_validator is None:
        _url_validator = URLValidator()
    return _url_validator
