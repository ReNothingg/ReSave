"""
Безопасный форматер сообщений для Telegram Bot API
Предотвращает ошибки "Can't parse entities" путем использования простого текста
БЕЗ ЭКРАНИРОВАНИЯ - Telegram API справляется с обычным текстом
"""
import logging

# Настройка логгера
logger = logging.getLogger(__name__)

class SafeFormatter:
    """
    Безопасный форматер для сообщений Telegram.

    Философия:
    - Лучшая защита от ошибок Markdown — не использовать Markdown.
    - Эмодзи безопасны и не требуют экранирования.
    - Если нужно форматирование, методы возвращают кортеж (текст, None),
      чтобы явно указать отсутствие parse_mode.
    """

    @staticmethod
    def clean_text(text) -> str:
        """
        Приводит входные данные к строке без лишних преобразований.
        """
        if text is None:
            return ""
        return str(text)

    # --- Методы форматирования контента ---

    @staticmethod
    def format_title_message(title: str, metadata: dict = None) -> tuple:
        """
        Форматирует сообщение с названием видео.

        Returns:
            (text, parse_mode)
        """
        clean_title = SafeFormatter.clean_text(title)
        message = f"📹 {clean_title}\n"

        if metadata:
            if metadata.get('uploader'):
                uploader = SafeFormatter.clean_text(metadata['uploader'])
                message += f"\n👤 {uploader}"

            if metadata.get('duration'):
                try:
                    duration = int(metadata['duration'])
                    minutes, seconds = divmod(duration, 60)
                    message += f"\n⏱️ {minutes:02d}:{seconds:02d}"
                except (ValueError, TypeError):
                    pass

            if metadata.get('views'):
                views = SafeFormatter.clean_text(metadata['views'])
                message += f"\n👁️ {views}"

        message += "\n\nВыберите качество для скачивания: 🎛️\n"

        # Возвращаем None в качестве parse_mode
        return message, None

    @staticmethod
    def format_error_message(error_text: str) -> tuple:
        """Форматирует сообщение об ошибке."""
        clean_err = SafeFormatter.clean_text(error_text)
        message = f"❌ {clean_err}"
        return message, None

    @staticmethod
    def format_success_message(message_text: str) -> tuple:
        """Форматирует сообщение об успехе."""
        clean_msg = SafeFormatter.clean_text(message_text)
        message = f"✅ {clean_msg}"
        return message, None

    @staticmethod
    def format_info_message(title: str, lines: list) -> tuple:
        """Форматирует информационное сообщение со списком строк."""
        clean_title = SafeFormatter.clean_text(title)
        message = f"ℹ️ {clean_title}\n\n"

        for item in lines:
            # Поддержка и кортежей (emoji, text), и просто строк
            if isinstance(item, (list, tuple)) and len(item) >= 2:
                emoji, line = item[0], item[1]
                message += f"{emoji} {SafeFormatter.clean_text(line)}\n"
            else:
                message += f"• {SafeFormatter.clean_text(item)}\n"

        return message, None

    # --- Методы безопасной отправки (Wrapper methods) ---

    @staticmethod
    def safe_send_message(bot, chat_id: int, text: str, **kwargs):
        """
        Безопасно отправляет сообщение.
        Если передан parse_mode, но происходит ошибка — пробует отправить без форматирования.
        """
        try:
            return bot.send_message(chat_id, text, **kwargs)
        except Exception as e:
            error_str = str(e)
            if "Can't parse entities" in error_str or "formatting" in error_str.lower():
                logger.warning(f"Ошибка форматирования, отправляем как обычный текст: {error_str[:100]}")

                # Убираем parse_mode и отправляем чистый текст
                kwargs_copy = kwargs.copy()
                kwargs_copy.pop('parse_mode', None)

                try:
                    return bot.send_message(chat_id, text, **kwargs_copy)
                except Exception as e2:
                    logger.error(f"Не удалось отправить даже обычный текст: {e2}")
                    return None
            else:
                # Если ошибка не связана с парсингом (например, сеть), пробрасываем её
                logger.error(f"Ошибка отправки сообщения: {e}")
                raise e

    @staticmethod
    def safe_edit_message_text(bot, text: str, chat_id: int, message_id: int, **kwargs):
        """Безопасное редактирование сообщения."""
        try:
            return bot.edit_message_text(text, chat_id, message_id, **kwargs)
        except Exception as e:
            error_str = str(e)
            if "Can't parse entities" in error_str or "formatting" in error_str.lower():
                logger.warning("Ошибка форматирования при редактировании, убираем Markdown.")

                kwargs_copy = kwargs.copy()
                kwargs_copy.pop('parse_mode', None)

                try:
                    return bot.edit_message_text(text, chat_id, message_id, **kwargs_copy)
                except Exception as e2:
                    logger.error(f"Критическая ошибка редактирования: {e2}")
                    return None

            logger.error(f"Ошибка редактирования: {e}")
            raise e

    @staticmethod
    def safe_reply_to(bot, message, text: str, **kwargs):
        """Безопасный ответ на сообщение."""
        try:
            return bot.reply_to(message, text, **kwargs)
        except Exception as e:
            error_str = str(e)
            if "Can't parse entities" in error_str or "formatting" in error_str.lower():
                logger.warning("Ошибка форматирования при ответе, убираем Markdown.")

                kwargs_copy = kwargs.copy()
                kwargs_copy.pop('parse_mode', None)

                try:
                    return bot.reply_to(message, text, **kwargs_copy)
                except Exception as e2:
                    logger.error(f"Критическая ошибка ответа: {e2}")
                    return None

            logger.error(f"Ошибка reply_to: {e}")
            raise e


# Глобальный экземпляр для удобного импорта
_safe_formatter = SafeFormatter()

def get_safe_formatter():
    """Получить глобальный экземпляр SafeFormatter"""
    return _safe_formatter