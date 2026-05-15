from __future__ import annotations

from enum import Enum

from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup


class UITheme(Enum):
    MODERN = "modern"
    CLASSIC = "classic"
    MINIMAL = "minimal"


class UIManager:
    def __init__(self, theme=UITheme.MODERN):
        self.theme = theme
        self.emojis = {
            "loading": "⏳",
            "success": "✅",
            "error": "❌",
            "warning": "⚠️",
            "info": "ℹ️",
            "arrow_right": "→",
            "separator": "├",
            "end_separator": "└",
            "video": "📹",
            "audio": "🎵",
            "photo": "🖼️",
            "document": "📄",
            "gif": "✨",
            "thumbnail": "📸",
            "download": "⬇️",
            "upload": "📤",
            "delete": "🗑️",
            "edit": "✏️",
            "cancel": "🚫",
            "retry": "🔄",
            "pause": "⏸️",
            "play": "▶️",
            "best": "🎬",
            "medium": "📹",
            "low": "📱",
            "hd": "🔷",
            "sd": "🔸",
            "pending": "⏰",
            "downloading": "⬇️",
            "uploading": "⬆️",
            "processing": "⚙️",
            "completed": "✅",
            "failed": "❌",
            "cancelled": "🚫",
            "user": "👤",
            "stats": "📊",
            "time": "⏱️",
            "size": "📁",
            "speed": "⚡",
            "url": "🔗",
            "settings": "⚙️",
            "help": "📖",
            "menu": "📋",
        }

    def format_message(self, title: str, content: str, style="info") -> str:
        if style == "error":
            emoji = self.emojis["error"]
        elif style == "success":
            emoji = self.emojis["success"]
        elif style == "warning":
            emoji = self.emojis["warning"]
        else:
            emoji = self.emojis["info"]

        separator = "─" * 40
        return f"{emoji} *{title}* {emoji}\n{separator}\n{content}"

    def format_panel(
        self,
        title: str,
        lines: list[str] | None = None,
        *,
        icon: str | None = None,
        footer: str | None = None,
    ) -> str:
        header_icon = f"{icon} " if icon else ""
        message_lines = [f"{header_icon}{title}", "━━━━━━━━━━━━━━━━━━━━"]

        if lines:
            message_lines.extend(lines)

        if footer:
            message_lines.extend(["", footer])

        return "\n".join(message_lines).strip()

    def format_key_value_list(self, items: list[tuple[str, str]]) -> list[str]:
        lines: list[str] = []
        for label, value in items:
            lines.append(f"• {label}: {value}")
        return lines

    def create_progress_bar(
        self,
        progress: float,
        length: int = 12,
        filled_char: str = "█",
        empty_char: str = "░",
    ) -> str:
        progress = max(0.0, min(progress, 1.0))
        filled = int(progress * length)
        percentage = int(progress * 100)
        bar = f"{filled_char * filled}{empty_char * (length - filled)}"
        return f"{bar} {percentage}%"

    def create_status_message(
        self,
        title: str,
        status: str,
        progress: float = None,
        details: dict = None,
    ) -> str:
        message = f"{self.emojis['video']} *{title}*\n\n"
        message += f"{self._get_status_emoji(status)} {status}\n"

        if progress is not None:
            message += f"{self.emojis['download']} {self.create_progress_bar(progress)}\n"

        if details:
            message += "\n"
            for key, value in details.items():
                emoji = self.emojis.get(key.lower(), "•")
                message += f"{emoji} {key}: {value}\n"

        return message

    def create_quality_selector(self, message_id: int) -> InlineKeyboardMarkup:
        rows = [
            [
                InlineKeyboardButton(
                    text=f"{self.emojis['best']} Лучшее",
                    callback_data=f"dl_best_{message_id}",
                )
            ],
            [
                InlineKeyboardButton(
                    text=f"{self.emojis['medium']} 720p",
                    callback_data=f"dl_medium_{message_id}",
                ),
                InlineKeyboardButton(
                    text=f"{self.emojis['low']} 480p",
                    callback_data=f"dl_low_{message_id}",
                )
            ],
            [
                InlineKeyboardButton(
                    text=f"{self.emojis['audio']} MP3",
                    callback_data=f"dl_audio_{message_id}",
                )
            ],
            [
                InlineKeyboardButton(
                    text=f"{self.emojis['cancel']} Отмена",
                    callback_data=f"cancel_{message_id}",
                )
            ],
        ]
        return InlineKeyboardMarkup(inline_keyboard=rows)

    def create_download_options(
        self,
        message_id: int,
        has_gif: bool = False,
        has_subtitles: bool = False,
        has_thumbnail: bool = False,
    ) -> InlineKeyboardMarkup:
        rows = []

        if has_gif:
            rows.append(
                [
                    InlineKeyboardButton(
                        text=f"{self.emojis['gif']} Создать GIF",
                        callback_data=f"dl_gif_{message_id}",
                    )
                ]
            )
        if has_subtitles:
            rows.append(
                [
                    InlineKeyboardButton(
                        text="📝 Скачать субтитры (.srt)",
                        callback_data=f"dl_subtitles_{message_id}",
                    )
                ]
            )
        if has_thumbnail:
            rows.append(
                [
                    InlineKeyboardButton(
                        text=f"{self.emojis['thumbnail']} Скачать превью",
                        callback_data=f"dl_thumbnail_{message_id}",
                    )
                ]
            )

        return InlineKeyboardMarkup(inline_keyboard=rows)

    def format_file_size(self, size_bytes: int) -> str:
        if size_bytes < 1024:
            return f"{size_bytes} B"
        if size_bytes < 1024 * 1024:
            return f"{size_bytes / 1024:.1f} KB"
        if size_bytes < 1024 * 1024 * 1024:
            return f"{size_bytes / (1024 * 1024):.1f} MB"
        return f"{size_bytes / (1024 * 1024 * 1024):.2f} GB"

    def format_time(self, seconds: float) -> str:
        if seconds < 60:
            return f"{seconds:.0f}s"
        if seconds < 3600:
            return f"{seconds / 60:.1f}m"
        return f"{seconds / 3600:.1f}h"

    def _get_status_emoji(self, status: str) -> str:
        status_lower = status.lower()

        if "pending" in status_lower or "queue" in status_lower:
            return self.emojis["pending"]
        if "download" in status_lower:
            return self.emojis["downloading"]
        if "upload" in status_lower:
            return self.emojis["uploading"]
        if "process" in status_lower:
            return self.emojis["processing"]
        if "complete" in status_lower or "finish" in status_lower:
            return self.emojis["completed"]
        if "fail" in status_lower or "error" in status_lower:
            return self.emojis["failed"]
        if "cancel" in status_lower:
            return self.emojis["cancelled"]
        return self.emojis["info"]

    @staticmethod
    def create_table_row(columns: list, widths: list = None) -> str:
        if widths:
            return " | ".join(f"{str(column):<{width}}" for column, width in zip(columns, widths))
        return " | ".join(str(column) for column in columns)

    @staticmethod
    def format_duration(seconds: int) -> str:
        minutes, secs = divmod(seconds, 60)
        hours, minutes = divmod(minutes, 60)
        if hours > 0:
            return f"{hours:02d}:{minutes:02d}:{secs:02d}"
        return f"{minutes:02d}:{secs:02d}"


_ui_manager = None


def get_ui_manager(theme=UITheme.MODERN) -> UIManager:
    global _ui_manager
    if _ui_manager is None:
        _ui_manager = UIManager(theme)
    return _ui_manager
