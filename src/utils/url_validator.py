import ipaddress
import logging
import re
from typing import Tuple
from urllib.parse import urlsplit, urlunsplit

logger = logging.getLogger(__name__)

class URLValidator:
    def __init__(self):
        self.url_pattern = re.compile(
            r"^https?://",
            re.IGNORECASE
        )
        self.http_url_finder = re.compile(r"https?://[^\s<>\"'`]+", re.IGNORECASE)
        self.bare_url_finder = re.compile(
            r"(?<![@\w])"
            r"(?:[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\.)+"
            r"[a-z]{2,63}"
            r"(?::\d+)?"
            r"(?:/[^\s<>\"'`]*)?",
            re.IGNORECASE,
        )

    def validate(self, url: str) -> Tuple[bool, str, dict]:
        if not url or not isinstance(url, str):
            return False, None, {"error": "Пустая ссылка"}

        url = self._clean_url(url.strip())
        if not url:
            return False, None, {"error": "Пустая ссылка"}

        if not self.url_pattern.match(url):
            if any(char.isspace() for char in url):
                return False, None, {"error": "Ссылка содержит пробелы"}

            corrected = f"https://{url}"
            if not self._looks_like_url(corrected):
                return False, None, {"error": "Это не похоже на ссылку"}

            logger.info("Ссылка исправлена: добавлен https:// к %s", url)
            return True, corrected, {
                "fixed": True,
                "fix_type": "added_protocol",
                "original": url,
                "platform": self._detect_platform(corrected),
            }

        if not self._looks_like_url(url):
            return False, None, {"error": "Это не похоже на ссылку"}

        return True, url, {
            "platform": self._detect_platform(url),
            "valid": True
        }

    def extract_url(self, text: str) -> str | None:
        if not text or not isinstance(text, str):
            return None

        match = self.http_url_finder.search(text)
        if match:
            return self._clean_url(match.group(0))

        stripped = text.strip()
        if any(char.isspace() for char in stripped):
            match = self.bare_url_finder.search(stripped)
            return self._clean_url(match.group(0)) if match else None

        if self.url_pattern.match(stripped):
            return self._clean_url(stripped)

        match = self.bare_url_finder.search(stripped)
        if match:
            return self._clean_url(match.group(0))

        return None

    def is_valid_url(self, url: str) -> bool:
        is_valid, _, _ = self.validate(url)
        return is_valid

    def _looks_like_url(self, url: str) -> bool:
        if any(char.isspace() for char in url):
            return False

        try:
            parsed = urlsplit(url)
        except ValueError:
            return False

        if parsed.scheme.lower() not in {"http", "https"}:
            return False

        host = parsed.hostname
        if not host or not self._host_is_valid(host):
            return False

        return True

    @staticmethod
    def _host_is_valid(host: str) -> bool:
        try:
            ipaddress.ip_address(host.strip("[]"))
            return True
        except ValueError:
            pass

        if "." not in host or host.startswith(".") or host.endswith(".") or ".." in host:
            return False

        try:
            ascii_host = host.encode("idna").decode("ascii")
        except UnicodeError:
            return False

        labels = ascii_host.split(".")
        if any(not label or len(label) > 63 for label in labels):
            return False

        top_level = labels[-1]
        if len(top_level) < 2 or top_level.isdigit():
            return False

        return bool(re.fullmatch(r"[a-z0-9.-]+", ascii_host, re.IGNORECASE))

    @staticmethod
    def _clean_url(url: str) -> str:
        url = url.strip()
        url = url.rstrip(".,;:!?)»”’]")
        try:
            parsed = urlsplit(url)
        except ValueError:
            return url

        if parsed.scheme and parsed.netloc:
            return urlunsplit(parsed)
        return url

    def _detect_platform(self, url: str) -> str:
        try:
            return urlsplit(url).hostname or "generic"
        except ValueError:
            return "generic"


_url_validator = None


def get_url_validator() -> URLValidator:
    global _url_validator
    if _url_validator is None:
        _url_validator = URLValidator()
    return _url_validator
