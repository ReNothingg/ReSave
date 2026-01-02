"""
Безопасный форматер сообщений для Telegram Bot API
Предотвращает ошибки "Can't parse entities" путем использования простого текста
БЕЗ ЭКРАНИРОВАНИЯ - Telegram API справляется с обычным текстом
"""
import logging

logger = logging.getLogger(__name__)


class SafeFormatter:
    """
    Безопасный форматер для сообщений Telegram
    
    Философия:
    - НЕ экранировать текст - просто отправляем как есть
    - Эмодзи безопасны и не требуют экранирования
    - Обычный текст ВСЕГДА безопаснее, чем Markdown
    - НЕ использовать parse_mode='Markdown' вообще
    """
    
    @staticmethod
    def safe_send_message(bot, chat_id: int, text: str, **kwargs):
        """
        Безопасно отправляет сообщение БЕЗ форматирования.
        
        Args:
            bot: Объект TeleBot
            chat_id: ID чата
            text: Текст сообщения (НЕ экранировать!)
            **kwargs: Дополнительные параметры для send_message
        
        Returns:
            Результат bot.send_message или None при ошибке
        """
        try:
            # Убедимся, что НЕТ parse_mode
            kwargs_copy = kwargs.copy()
            kwargs_copy.pop('parse_mode', None)
            
            return bot.send_message(chat_id, text, **kwargs_copy)
        except Exception as e:
            logger.error(f"Ошибка отправки сообщения: {e}")
            raise e
    
    @staticmethod
    def safe_edit_message_text(bot, text: str, chat_id: int, message_id: int, **kwargs):
        """
        Безопасно редактирует сообщение БЕЗ форматирования.
        
        Args:
            bot: Объект TeleBot
            text: Новый текст (НЕ экранировать!)
            chat_id: ID чата
            message_id: ID сообщения
            **kwargs: Дополнительные параметры для edit_message_text
        
        Returns:
            Результат bot.edit_message_text или None при ошибке
        """
        try:
            kwargs_copy = kwargs.copy()
            kwargs_copy.pop('parse_mode', None)
            
            return bot.edit_message_text(text, chat_id, message_id, **kwargs_copy)
        except Exception as e:
            logger.error(f"Ошибка редактирования сообщения: {e}")
            raise e
    
    @staticmethod
    def safe_reply_to(bot, message, text: str, **kwargs):
        """
        Безопасно отвечает на сообщение БЕЗ форматирования.
        
        Args:
            bot: Объект TeleBot
            message: Исходное сообщение
            text: Текст ответа (НЕ экранировать!)
            **kwargs: Дополнительные параметры для reply_to
        
        Returns:
            Результат bot.reply_to или None при ошибке
        """
        try:
            kwargs_copy = kwargs.copy()
            kwargs_copy.pop('parse_mode', None)
            
            return bot.reply_to(message, text, **kwargs_copy)
        except Exception as e:
            logger.error(f"Ошибка ответа на сообщение: {e}")
            raise e


# Глобальный экземпляр
_safe_formatter = SafeFormatter()


def get_safe_formatter():
    """Получить глобальный экземпляр SafeFormatter"""
    return _safe_formatter
