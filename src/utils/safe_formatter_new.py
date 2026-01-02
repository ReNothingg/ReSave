import logging

logger = logging.getLogger(__name__)


class SafeFormatter:
    @staticmethod
    def safe_send_message(bot, chat_id: int, text: str, **kwargs):
        try:
            kwargs_copy = kwargs.copy()
            kwargs_copy.pop('parse_mode', None)

            return bot.send_message(chat_id, text, **kwargs_copy)
        except Exception as e:
            logger.error(f"Ошибка отправки сообщения: {e}")
            raise e

    @staticmethod
    def safe_edit_message_text(bot, text: str, chat_id: int, message_id: int, **kwargs):
        try:
            kwargs_copy = kwargs.copy()
            kwargs_copy.pop('parse_mode', None)

            return bot.edit_message_text(text, chat_id, message_id, **kwargs_copy)
        except Exception as e:
            logger.error(f"Ошибка редактирования сообщения: {e}")
            raise e

    @staticmethod
    def safe_reply_to(bot, message, text: str, **kwargs):
        try:
            kwargs_copy = kwargs.copy()
            kwargs_copy.pop('parse_mode', None)

            return bot.reply_to(message, text, **kwargs_copy)
        except Exception as e:
            logger.error(f"Ошибка ответа на сообщение: {e}")
            raise e


_safe_formatter = SafeFormatter()


def get_safe_formatter():
    return _safe_formatter
