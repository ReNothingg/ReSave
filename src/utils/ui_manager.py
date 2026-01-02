from telebot import types
from enum import Enum

class UITheme(Enum):
    MODERN = "modern"
    CLASSIC = "classic"
    MINIMAL = "minimal"

class UIManager:
    def __init__(self, theme=UITheme.MODERN):
        self.theme = theme
        self.emojis = {
            # Основные эмодзи
            "loading": "⏳",
            "success": "✅",
            "error": "❌",
            "warning": "⚠️",
            "info": "ℹ️",
            "arrow_right": "→",
            "separator": "├",
            "end_separator": "└",

            # Медиа
            "video": "📹",
            "audio": "🎵",
            "photo": "🖼️",
            "document": "📄",
            "gif": "✨",
            "thumbnail": "📸",

            # Действия
            "download": "⬇️",
            "upload": "📤",
            "delete": "🗑️",
            "edit": "✏️",
            "cancel": "🚫",
            "retry": "🔄",
            "pause": "⏸️",
            "play": "▶️",

            # Качество
            "best": "🎬",
            "medium": "📹",
            "low": "📱",
            "hd": "🔷",
            "sd": "🔸",

            # Состояния
            "pending": "⏰",
            "downloading": "⬇️",
            "uploading": "⬆️",
            "processing": "⚙️",
            "completed": "✅",
            "failed": "❌",
            "cancelled": "🚫",

            # Информация
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
            separator = "─" * 40
        elif style == "success":
            emoji = self.emojis["success"]
            separator = "─" * 40
        elif style == "warning":
            emoji = self.emojis["warning"]
            separator = "─" * 40
        else:
            emoji = self.emojis["info"]
            separator = "─" * 40

        return f"{emoji} *{title}* {emoji}\n{separator}\n{content}"

    def create_progress_bar(self, progress: float, length: int = 10, filled_char: str = "▰",
                          empty_char: str = "▱") -> str:
        filled = int(progress * length)
        percentage = int(progress * 100)
        bar = f"{filled_char * filled}{empty_char * (length - filled)}"
        return f"{bar} {percentage}%"

    def create_status_message(self, title: str, status: str, progress: float = None,
                            details: dict = None) -> str:
        message = f"{self.emojis['video']} *{title}*\n\n"

        # Статус
        status_emoji = self._get_status_emoji(status)
        message += f"{status_emoji} {status}\n"

        # Прогресс
        if progress is not None:
            progress_bar = self.create_progress_bar(progress)
            message += f"{self.emojis['download']} {progress_bar}\n"

        # Детали
        if details:
            message += "\n"
            for key, value in details.items():
                emoji = self.emojis.get(key.lower(), "•")
                message += f"{emoji} {key}: {value}\n"

        return message

    def create_quality_selector(self, message_id: int) -> types.InlineKeyboardMarkup:
        markup = types.InlineKeyboardMarkup(row_width=1)

        buttons = [
            (f"{self.emojis['best']} Лучшее качество (авто)", "dl_best"),
            (f"{self.emojis['medium']} Среднее качество (720p)", "dl_medium"),
            (f"{self.emojis['low']} Низкое качество (480p)", "dl_low"),
            (f"{self.emojis['audio']} Только аудио (MP3)", "dl_audio"),
        ]

        for button_text, callback in buttons:
            markup.add(types.InlineKeyboardButton(button_text, callback_data=f"{callback}_{message_id}"))

        markup.add(types.InlineKeyboardButton(
            f"{self.emojis['cancel']} Отмена",
            callback_data=f"cancel_{message_id}"
        ))

        return markup

    def create_download_options(self, message_id: int, has_gif: bool = False,
                               has_subtitles: bool = False, has_thumbnail: bool = False) -> types.InlineKeyboardMarkup:
        markup = types.InlineKeyboardMarkup(row_width=1)

        options = []
        if has_gif:
            options.append((f"{self.emojis['gif']} Создать GIF", f"dl_gif_{message_id}"))
        if has_subtitles:
            options.append((f"📝 Скачать субтитры (.srt)", f"dl_subtitles_{message_id}"))
        if has_thumbnail:
            options.append((f"{self.emojis['thumbnail']} Скачать превью", f"dl_thumbnail_{message_id}"))

        for button_text, callback in options:
            markup.add(types.InlineKeyboardButton(button_text, callback_data=callback))

        return markup

    def format_file_size(self, size_bytes: int) -> str:
        if size_bytes < 1024:
            return f"{size_bytes} B"
        elif size_bytes < 1024 * 1024:
            return f"{size_bytes / 1024:.1f} KB"
        elif size_bytes < 1024 * 1024 * 1024:
            return f"{size_bytes / (1024 * 1024):.1f} MB"
        else:
            return f"{size_bytes / (1024 * 1024 * 1024):.2f} GB"

    def format_time(self, seconds: float) -> str:
        if seconds < 60:
            return f"{seconds:.0f}s"
        elif seconds < 3600:
            minutes = seconds / 60
            return f"{minutes:.1f}m"
        else:
            hours = seconds / 3600
            return f"{hours:.1f}h"

    def _get_status_emoji(self, status: str) -> str:
        status_lower = status.lower()

        if "pending" in status_lower or "queue" in status_lower:
            return self.emojis["pending"]
        elif "download" in status_lower:
            return self.emojis["downloading"]
        elif "upload" in status_lower:
            return self.emojis["uploading"]
        elif "process" in status_lower:
            return self.emojis["processing"]
        elif "complete" in status_lower or "finish" in status_lower:
            return self.emojis["completed"]
        elif "fail" in status_lower or "error" in status_lower:
            return self.emojis["failed"]
        elif "cancel" in status_lower:
            return self.emojis["cancelled"]
        else:
            return self.emojis["info"]

    def create_table_row(self, columns: list, widths: list = None) -> str:
        if widths:
            row = " | ".join(f"{str(col):<{w}}" for col, w in zip(columns, widths))
        else:
            row = " | ".join(str(col) for col in columns)
        return row

    def format_duration(self, seconds: int) -> str:
        minutes, secs = divmod(seconds, 60)
        hours, minutes = divmod(minutes, 60)

        if hours > 0:
            return f"{hours:02d}:{minutes:02d}:{secs:02d}"
        else:
            return f"{minutes:02d}:{secs:02d}"


# Глобальный экземпляр UI Manager
_ui_manager = None


def get_ui_manager(theme=UITheme.MODERN) -> UIManager:
    global _ui_manager
    if _ui_manager is None:
        _ui_manager = UIManager(theme)
    return _ui_manager
