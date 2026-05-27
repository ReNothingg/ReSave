from html import escape
from typing import Optional

class MessageTemplate:
    @staticmethod
    def _escape_text(value: Optional[str]) -> str:
        return escape(str(value or ""))

    @staticmethod
    def _format_original_link(url: str) -> str:
        safe_url = escape(str(url), quote=True)
        return f'🔗 <a href="{safe_url}">Открыть оригинал</a>'

    @staticmethod
    def format_caption(title: str, url: str, action: str = "video", file_size: Optional[float] = None) -> str:
        icons = {
            "video": "🎬",
            "audio": "🎵",
            "gif": "✨",
            "photo": "🖼️",
            "subtitles": "📝",
            "thumbnail": "📸",
        }
        
        icon = icons.get(action, "📁")
        
        safe_title = MessageTemplate._escape_text(title)
        caption = f"{icon} <b>{safe_title}</b>\n\n"
        
        if file_size:
            caption += f"📦 Размер: {file_size:.1f} MB\n"
        
        caption += f"{MessageTemplate._format_original_link(url)}\n\n"
        
        caption += "⚡ @ReSafeBot"
        
        return caption
    
    @staticmethod
    def format_inline_caption(title: str, url: str) -> str:
        safe_title = MessageTemplate._escape_text(title)
        return f"🎬 <b>{safe_title}</b>\n\n{MessageTemplate._format_original_link(url)}\n\n⚡ @ReSafeBot"
    
    @staticmethod
    def format_thumbnail_caption(title: str, url: str, width: Optional[int] = None, height: Optional[int] = None) -> str:
        safe_title = MessageTemplate._escape_text(title)
        caption = "🖼️ <b>Превью видео</b>\n"
        caption += f"📹 {safe_title}\n"
        
        if width and height:
            caption += f"📐 Разрешение: {width}×{height}\n"
        
        caption += f"\n{MessageTemplate._format_original_link(url)}\n\n"
        
        caption += "⚡ @ReSafeBot"
        return caption
    
    @staticmethod
    def format_gif_caption(title: str, url: str) -> str:
        safe_title = MessageTemplate._escape_text(title)
        return f"✨ <b>{safe_title}</b>\n\n{MessageTemplate._format_original_link(url)}\n\n⚡ @ReSafeBot"
    
    @staticmethod
    def format_subtitles_caption(title: str, url: str, language: str = "en/ru") -> str:
        safe_title = MessageTemplate._escape_text(title)
        safe_language = MessageTemplate._escape_text(language)
        caption = f"📝 <b>Субтитры</b>\n🎬 {safe_title}\n"
        caption += f"🗣️ Языки: {safe_language}\n"
        caption += f"\n{MessageTemplate._format_original_link(url)}\n\n"
        
        caption += "⚡ @ReSafeBot"
        return caption
    
    @staticmethod
    def format_tiktok_photo_caption(url: str, count: Optional[int] = None) -> str:
        if count and count > 1:
            caption = f"🖼️ <b>Фото из TikTok</b> ({count} шт)\n"
        else:
            caption = "🖼️ <b>Фото из TikTok</b>\n"
        
        caption += f"\n{MessageTemplate._format_original_link(url)}\n\n"
        
        caption += "⚡ @ReSafeBot"
        return caption


class ErrorMessages:    
    # Ошибки, связанные с платформой/видео
    UNAVAILABLE_VIDEO = "❌ Это видео недоступно или было удалено с платформы."
    PRIVATE_VIDEO = "❌ Это приватное видео. Доступ запрещён."
    BLOCKED_VIDEO = "❌ Это видео заблокировано в вашей стране или недоступно."
    RESTRICTED_VIDEO = "❌ Это видео ограничено в доступе. Требуется авторизация."
    REMOVED_VIDEO = "❌ Это видео было удалено или больше недоступно."
    
    # Ошибки со скачиванием
    DOWNLOAD_FAILED = "❌ Не удалось скачать видео. Попробуйте позже."
    DOWNLOAD_TIMEOUT = "⏱️ Время скачивания истекло. Ссылка может быть неправильной или видео слишком большое."
    DOWNLOAD_ERROR = "❌ Ошибка при скачивании видео. Проверьте ссылку и попробуйте снова."
    NETWORK_ERROR = "🌐 Ошибка подключения. Проверьте интернет и повторите попытку."
    
    # Ошибки с отправкой
    UPLOAD_FAILED = "📤 Не удалось отправить файл. Попробуйте позже."
    UPLOAD_TIMEOUT = "⏱️ Время отправки истекло. Попробуйте файл меньшего размера."
    FILE_TOO_LARGE = "📦 Файл слишком большой для отправки через текущий Bot API."
    
    # Ошибки с форматом
    UNSUPPORTED_FORMAT = "🎯 Этот формат не поддерживается."
    CONVERSION_ERROR = "⚙️ Ошибка при конвертации файла. Попробуйте другое качество."
    QUALITY_NOT_AVAILABLE = "🎯 Для этого видео нет выбранного качества. Попробуйте другое качество."
    
    # Ошибки с лимитами
    FILE_SIZE_LIMIT = "📦 Файл слишком большой. Попробуйте скачать в более низком качестве (480p, MP3)."
    VIDEO_DURATION_LIMIT = "⏱️ Видео слишком длинное. Максимум {max_duration} минут для free-пользователей."
    PLAYLIST_LIMIT = "🎶 Плейлист слишком большой. Максимум {max_items} видео для free-пользователей."
    CONCURRENT_LIMIT = "⏸️ Слишком много одновременных загрузок. Подождите завершения текущих."
    RATE_LIMIT = "⚠️ Слишком много запросов. Пожалуйста, подождите немного и попробуйте снова."
    
    # Ошибки с субтитрами
    SUBTITLES_NOT_FOUND = "📝 Для этого видео нет доступных субтитров."
    SUBTITLES_DOWNLOAD_FAILED = "📝 Не удалось скачать субтитры. Возможно, сервер их заблокировал."
    
    # Ошибки с превью
    THUMBNAIL_NOT_FOUND = "🖼️ Превью для этого видео недоступно."
    THUMBNAIL_DOWNLOAD_FAILED = "🖼️ Не удалось скачать превью видео."
    
    # Ошибки с GIF
    GIF_CONVERSION_FAILED = "✨ Не удалось создать GIF. Видео может быть повреждено."
    GIF_FFMPEG_MISSING = "✨ FFmpeg не установлен. GIF недоступен."
    
    # Ошибки с TikTok
    TIKTOK_PHOTO_NOT_FOUND = "🖼️ Не удалось найти фото в этом посте. Возможно, это не пост с фото."
    TIKTOK_DOWNLOAD_FAILED = "🖼️ Ошибка при скачивании фото из TikTok."
    
    # Ошибки с URL/ссылкой
    INVALID_URL = "🔗 Это не похоже на корректную ссылку на видео."
    MALFORMED_URL = "🔗 Ссылка повреждена или неправильного формата."
    UNSUPPORTED_PLATFORM = "🌐 Эта платформа не поддерживается. Попробуйте другой сервис."
    
    # Общие ошибки
    UNKNOWN_ERROR = "⚠️ Неизвестная ошибка при обработке. Попробуйте позже."
    INTERNAL_ERROR = "⚠️ Ошибка на сервере бота. Пожалуйста, попробуйте позже."
    
    @staticmethod
    def get_user_message(error_str: str) -> str:
        error_lower = error_str.lower()
        
        checks = [
            ("unavailable", ErrorMessages.UNAVAILABLE_VIDEO),
            ("private", ErrorMessages.PRIVATE_VIDEO),
            ("blocked", ErrorMessages.BLOCKED_VIDEO),
            ("restricted", ErrorMessages.RESTRICTED_VIDEO),
            ("removed", ErrorMessages.REMOVED_VIDEO),
            ("404", ErrorMessages.UNAVAILABLE_VIDEO),
            ("file not found", ErrorMessages.UNAVAILABLE_VIDEO),
            
            ("timeout", ErrorMessages.DOWNLOAD_TIMEOUT),
            ("429", ErrorMessages.RATE_LIMIT),
            ("too many requests", ErrorMessages.RATE_LIMIT),
            ("connection", ErrorMessages.NETWORK_ERROR),
            ("network", ErrorMessages.NETWORK_ERROR),
            ("refused", ErrorMessages.NETWORK_ERROR),
            ("failed to establish a new connection", ErrorMessages.NETWORK_ERROR),
            ("winerror 10061", ErrorMessages.NETWORK_ERROR),
            ("timed out", ErrorMessages.DOWNLOAD_TIMEOUT),
            ("failed to decode object", ErrorMessages.UPLOAD_FAILED),
            ("clientdecodeerror", ErrorMessages.UPLOAD_FAILED),
            
            ("subtitles", ErrorMessages.SUBTITLES_NOT_FOUND),
            ("thumbnail", ErrorMessages.THUMBNAIL_NOT_FOUND),
            ("превью", ErrorMessages.THUMBNAIL_DOWNLOAD_FAILED),
            
            ("tiktok", ErrorMessages.TIKTOK_DOWNLOAD_FAILED),
            ("gif", ErrorMessages.GIF_CONVERSION_FAILED),
            
            ("invalid", ErrorMessages.INVALID_URL),
            ("malformed", ErrorMessages.MALFORMED_URL),
            
            ("file too large", ErrorMessages.FILE_TOO_LARGE),
            ("request entity too large", ErrorMessages.FILE_TOO_LARGE),
            ("entity too large", ErrorMessages.FILE_TOO_LARGE),
            ("file is too big", ErrorMessages.FILE_TOO_LARGE),
            ("requested format is not available", ErrorMessages.QUALITY_NOT_AVAILABLE),
            ("requested format not available", ErrorMessages.QUALITY_NOT_AVAILABLE),
            ("no video formats found", ErrorMessages.QUALITY_NOT_AVAILABLE),
            ("ffmpeg is not installed", ErrorMessages.CONVERSION_ERROR),
        ]
        
        for keyword, message in checks:
            if keyword in error_lower:
                return message
        
        return ErrorMessages.UNKNOWN_ERROR
    
    @staticmethod
    def format_error_with_suggestion(error_str: str, suggestion: str = None) -> str:
        message = ErrorMessages.get_user_message(error_str)
        
        if suggestion:
            message += f"\n\n💡 {suggestion}"
        else:
            if "timeout" in error_str.lower():
                message += "\n\n💡 Попробуйте позже или выберите более низкое качество."
            elif "too many requests" in error_str.lower():
                message += "\n\n💡 Подождите 1-2 минуты и повторите попытку."
            elif "invalid" in error_str.lower():
                message += "\n\n💡 Убедитесь, что ссылка полная (начинается с https://)."
            elif "requested format is not available" in error_str.lower():
                message += "\n\n💡 Откройте кнопки качества и выберите другой вариант."
            elif "entity too large" in error_str.lower() or "file is too big" in error_str.lower():
                message += "\n\n💡 Выберите более низкое качество (480p или MP3), чтобы уложиться в лимит Telegram."
        
        return message
