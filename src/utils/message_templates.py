"""
Единые шаблоны сообщений и централизованные тексты для ошибок
"""
from typing import Optional
from urllib.parse import quote

class MessageTemplate:
    """Шаблоны сообщений для всех режимов отправки"""
    
    @staticmethod
    def format_caption(title: str, url: str, action: str = "video", file_size: Optional[float] = None) -> str:
        """
        Единый шаблон капшена для всех файлов (видео, аудио, фото, GIF, субтитры и т.д.)
        
        Args:
            title: Название видео (полное, без сокращений)
            url: Ссылка на оригинальное видео
            action: Тип загружаемого файла (video, audio, gif, photo, subtitles, thumbnail)
            file_size: Размер файла в MB (опционально)
        
        Returns:
            Форматированный текст капшена
        """
        
        # Иконки для разных типов файлов
        icons = {
            "video": "🎬",
            "audio": "🎵",
            "gif": "✨",
            "photo": "🖼️",
            "subtitles": "📝",
            "thumbnail": "📸",
        }
        
        icon = icons.get(action, "📁")
        
        # Основная информация
        caption = f"{icon} {title}\n\n"
        
        # Размер файла (если передан)
        if file_size:
            caption += f"📁 Размер: {file_size:.1f} MB\n"
        
        # Ссылка на оригинал
        try:
            # Экранируем спецсимволы для Markdown
            safe_url = url.replace("[", r"\[").replace("]", r"\]")
            caption += f"👉 [Оригинал]({safe_url})\n\n"
        except:
            caption += f"👉 Оригинал: {url}\n\n"
        
        caption += "@ReSafeBot"
        
        return caption
    
    @staticmethod
    def format_inline_caption(title: str, url: str) -> str:
        """Капшен для inline-режима (более компактный)"""
        try:
            safe_url = url.replace("[", r"\[").replace("]", r"\]")
            return f"🎬 {title}\n\n👉 [Оригинал]({safe_url})\n\n@ReSafeBot"
        except:
            return f"🎬 {title}\n\n👉 Оригинал: {url}\n\n@ReSafeBot"
    
    @staticmethod
    def format_thumbnail_caption(title: str, url: str, width: Optional[int] = None, height: Optional[int] = None) -> str:
        """Специальный капшен для превью"""
        caption = f"🖼️ Превью видео (без сжатия)\n"
        caption += f"📹 {title}\n"
        
        if width and height:
            caption += f"📐 Разрешение: {width}×{height}\n"
        
        try:
            safe_url = url.replace("[", r"\[").replace("]", r"\]")
            caption += f"\n👉 [Оригинал]({safe_url})\n\n"
        except:
            caption += f"\n👉 Оригинал: {url}\n\n"
        
        caption += "@ReSafeBot"
        return caption
    
    @staticmethod
    def format_gif_caption(title: str, url: str) -> str:
        """Специальный капшен для GIF"""
        try:
            safe_url = url.replace("[", r"\[").replace("]", r"\]")
            return f"✨ {title}\n\n👉 [Оригинал]({safe_url})\n\n@ReSafeBot"
        except:
            return f"✨ {title}\n\n👉 Оригинал: {url}\n\n@ReSafeBot"
    
    @staticmethod
    def format_subtitles_caption(title: str, url: str, language: str = "en/ru") -> str:
        """Специальный капшен для субтитров"""
        caption = f"📝 Субтитры для: {title}\n"
        caption += f"🗣️ Языки: {language}\n"
        
        try:
            safe_url = url.replace("[", r"\[").replace("]", r"\]")
            caption += f"\n👉 [Оригинал]({safe_url})\n\n"
        except:
            caption += f"\n👉 Оригинал: {url}\n\n"
        
        caption += "@ReSafeBot"
        return caption
    
    @staticmethod
    def format_tiktok_photo_caption(url: str, count: Optional[int] = None) -> str:
        """Специальный капшен для TikTok фото"""
        if count and count > 1:
            caption = f"🖼️ Фото из TikTok ({count} шт)\n"
        else:
            caption = f"🖼️ Фото из TikTok\n"
        
        try:
            safe_url = url.replace("[", r"\[").replace("]", r"\]")
            caption += f"\n👉 [Оригинал]({safe_url})\n\n"
        except:
            caption += f"\n👉 Оригинал: {url}\n\n"
        
        caption += "@ReSafeBot"
        return caption


class ErrorMessages:
    """Централизованные дружелюбные сообщения об ошибках"""
    
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
    FILE_TOO_LARGE = "📦 Файл слишком большой для отправки (макс. 2GB для Telegram)."
    
    # Ошибки с форматом
    UNSUPPORTED_FORMAT = "🎯 Этот формат не поддерживается."
    CONVERSION_ERROR = "⚙️ Ошибка при конвертации файла. Попробуйте другое качество."
    
    # Ошибки с лимитами
    FILE_SIZE_LIMIT = "📦 Файл слишком большой. Спробуйте скачать в более низком качестве (480p, MP3)."
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
        """
        Преобразует техническую ошибку в дружелюбное пользовательское сообщение
        
        Args:
            error_str: Строка технической ошибки
        
        Returns:
            Дружелюбное сообщение для пользователя
        """
        error_lower = error_str.lower()
        
        # Проверка различных типов ошибок
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
            
            ("subtitles", ErrorMessages.SUBTITLES_NOT_FOUND),
            ("thumbnail", ErrorMessages.THUMBNAIL_NOT_FOUND),
            
            ("tiktok", ErrorMessages.TIKTOK_DOWNLOAD_FAILED),
            ("gif", ErrorMessages.GIF_CONVERSION_FAILED),
            
            ("invalid", ErrorMessages.INVALID_URL),
            ("malformed", ErrorMessages.MALFORMED_URL),
            
            ("file too large", ErrorMessages.FILE_TOO_LARGE),
        ]
        
        for keyword, message in checks:
            if keyword in error_lower:
                return message
        
        return ErrorMessages.UNKNOWN_ERROR
    
    @staticmethod
    def format_error_with_suggestion(error_str: str, suggestion: str = None) -> str:
        """
        Форматирует ошибку с рекомендацией
        
        Args:
            error_str: Основное сообщение об ошибке
            suggestion: Рекомендация по решению (опционально)
        
        Returns:
            Отформатированное сообщение об ошибке с рекомендацией
        """
        message = ErrorMessages.get_user_message(error_str)
        
        if suggestion:
            message += f"\n\n💡 {suggestion}"
        else:
            # Стандартные рекомендации
            if "timeout" in error_str.lower():
                message += "\n\n💡 Попробуйте позже или выберите более низкое качество."
            elif "too many requests" in error_str.lower():
                message += "\n\n💡 Подождите 1-2 минуты и повторите попытку."
            elif "invalid" in error_str.lower():
                message += "\n\n💡 Убедитесь, что ссылка полная (начинается с https://)."
        
        return message
