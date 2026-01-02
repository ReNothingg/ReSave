import logging

logger = logging.getLogger(__name__)

class SafeFormatter:
    @staticmethod
    def clean_text(text) -> str:
        if text is None:
            return ""
        return str(text)

    @staticmethod
    def format_title_message(title: str, metadata: dict = None) -> tuple:
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

        return message, None

    @staticmethod
    def format_error_message(error_text: str) -> tuple:
        clean_err = SafeFormatter.clean_text(error_text)
        message = f"❌ {clean_err}"
        return message, None

    @staticmethod
    def format_success_message(message_text: str) -> tuple:
        clean_msg = SafeFormatter.clean_text(message_text)
        message = f"✅ {clean_msg}"
        return message, None

    @staticmethod
    def format_info_message(title: str, lines: list) -> tuple:
        clean_title = SafeFormatter.clean_text(title)
        message = f"ℹ️ {clean_title}\n\n"

        for item in lines:
            if isinstance(item, (list, tuple)) and len(item) >= 2:
                emoji, line = item[0], item[1]
                message += f"{emoji} {SafeFormatter.clean_text(line)}\n"
            else:
                message += f"• {SafeFormatter.clean_text(item)}\n"

        return message, None

    @staticmethod
    def safe_send_message(bot, chat_id: int, text: str, **kwargs):
        try:
            return bot.send_message(chat_id, text, **kwargs)
        except Exception as e:
            error_str = str(e)
            if "Can't parse entities" in error_str or "formatting" in error_str.lower():
                logger.warning(f"Ошибка форматирования, отправляем как обычный текст: {error_str[:100]}")

                kwargs_copy = kwargs.copy()
                kwargs_copy.pop('parse_mode', None)

                try:
                    return bot.send_message(chat_id, text, **kwargs_copy)
                except Exception as e2:
                    logger.error(f"Не удалось отправить даже обычный текст: {e2}")
                    return None
            else:
                logger.error(f"Ошибка отправки сообщения: {e}")
                raise e

    @staticmethod
    def safe_edit_message_text(bot, text: str, chat_id: int, message_id: int, **kwargs):
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


_safe_formatter = SafeFormatter()

def get_safe_formatter():
    return _safe_formatter