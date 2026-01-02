"""
Обработчик ошибок парсинга Markdown в Telegram API
Автоматически исправляет ошибки "Can't parse entities"
"""
import logging
from functools import wraps
from .markdown_escape import escape_markdown

logger = logging.getLogger(__name__)


class TelegramBotWrapper:
    """
    Обертка для telebot.TeleBot, которая автоматически обрабатывает
    ошибки парсинга Markdown в сообщениях
    """
    
    def __init__(self, bot):
        self.bot = bot
        self._original_send_message = bot.send_message
        self._original_edit_message_text = bot.edit_message_text
        self._original_reply_to = bot.reply_to
        self._original_send_photo = bot.send_photo
        self._original_send_video = bot.send_video
        
        # Заменяем методы на обернутые версии
        self._wrap_methods()
    
    def _wrap_methods(self):
        """Оборачивает методы отправки сообщений"""
        self.bot.send_message = self._wrapped_send_message
        self.bot.edit_message_text = self._wrapped_edit_message_text
        self.bot.reply_to = self._wrapped_reply_to
        self.bot.send_photo = self._wrapped_send_photo
        self.bot.send_video = self._wrapped_send_video
    
    def _handle_parse_error(self, error, text, parse_mode):
        """Обрабатывает ошибку парсинга и пытается исправить"""
        error_msg = str(error)
        
        if "Can't parse entities" in error_msg or "parse_mode" in error_msg.lower():
            logger.warning(f"Ошибка парсинга Markdown обнаружена: {error_msg[:100]}")
            logger.debug(f"Проблемный текст: {text[:200]}")
            
            # Экранируем текст и возвращаем (None, escaped_text)
            # чтобы вызывающий код мог повторить попытку
            escaped = escape_markdown(text, safe=True)
            return True, escaped
        
        return False, text
    
    def _wrapped_send_message(self, chat_id, text, **kwargs):
        """Безопасная отправка сообщения"""
        try:
            return self._original_send_message(chat_id, text, **kwargs)
        except Exception as e:
            is_parse_error, escaped_text = self._handle_parse_error(e, text, kwargs.get('parse_mode'))
            
            if is_parse_error:
                # Повторяем отправку без parse_mode
                kwargs_copy = kwargs.copy()
                kwargs_copy.pop('parse_mode', None)
                try:
                    return self._original_send_message(chat_id, escaped_text, **kwargs_copy)
                except Exception as e2:
                    logger.error(f"Ошибка отправки даже после экранирования: {e2}")
                    raise e2
            
            raise e
    
    def _wrapped_edit_message_text(self, text, chat_id, message_id, **kwargs):
        """Безопасное редактирование сообщения"""
        try:
            return self._original_edit_message_text(text, chat_id, message_id, **kwargs)
        except Exception as e:
            is_parse_error, escaped_text = self._handle_parse_error(e, text, kwargs.get('parse_mode'))
            
            if is_parse_error:
                kwargs_copy = kwargs.copy()
                kwargs_copy.pop('parse_mode', None)
                try:
                    return self._original_edit_message_text(escaped_text, chat_id, message_id, **kwargs_copy)
                except Exception as e2:
                    logger.error(f"Ошибка редактирования даже после экранирования: {e2}")
                    raise e2
            
            raise e
    
    def _wrapped_reply_to(self, message, text, **kwargs):
        """Безопасный ответ на сообщение"""
        try:
            return self._original_reply_to(message, text, **kwargs)
        except Exception as e:
            is_parse_error, escaped_text = self._handle_parse_error(e, text, kwargs.get('parse_mode'))
            
            if is_parse_error:
                kwargs_copy = kwargs.copy()
                kwargs_copy.pop('parse_mode', None)
                try:
                    return self._original_reply_to(message, escaped_text, **kwargs_copy)
                except Exception as e2:
                    logger.error(f"Ошибка ответа даже после экранирования: {e2}")
                    raise e2
            
            raise e
    
    def _wrapped_send_photo(self, chat_id, photo, **kwargs):
        """Безопасная отправка фото с подписью"""
        try:
            return self._original_send_photo(chat_id, photo, **kwargs)
        except Exception as e:
            caption = kwargs.get('caption')
            if caption:
                is_parse_error, escaped_caption = self._handle_parse_error(
                    e, caption, kwargs.get('parse_mode')
                )
                
                if is_parse_error:
                    kwargs_copy = kwargs.copy()
                    kwargs_copy['caption'] = escaped_caption
                    kwargs_copy.pop('parse_mode', None)
                    try:
                        return self._original_send_photo(chat_id, photo, **kwargs_copy)
                    except Exception as e2:
                        logger.error(f"Ошибка отправки фото даже после экранирования: {e2}")
                        raise e2
            
            raise e
    
    def _wrapped_send_video(self, chat_id, video, **kwargs):
        """Безопасная отправка видео с подписью"""
        try:
            return self._original_send_video(chat_id, video, **kwargs)
        except Exception as e:
            caption = kwargs.get('caption')
            if caption:
                is_parse_error, escaped_caption = self._handle_parse_error(
                    e, caption, kwargs.get('parse_mode')
                )
                
                if is_parse_error:
                    kwargs_copy = kwargs.copy()
                    kwargs_copy['caption'] = escaped_caption
                    kwargs_copy.pop('parse_mode', None)
                    try:
                        return self._original_send_video(chat_id, video, **kwargs_copy)
                    except Exception as e2:
                        logger.error(f"Ошибка отправки видео даже после экранирования: {e2}")
                        raise e2
            
            raise e
    
    def __getattr__(self, name):
        """Проксирует все остальные атрибуты к оригинальному боту"""
        return getattr(self.bot, name)
