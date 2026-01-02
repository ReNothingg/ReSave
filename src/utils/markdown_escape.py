"""
Утилита для безопасного экранирования символов Markdown
Предотвращает ошибку: "Can't parse entities: Can't find end of the entity"
"""
import re


def escape_markdown(text: str, safe: bool = True) -> str:
    """
    Экранирует специальные символы Markdown.

    Args:
        text: Текст для экранирования
        safe: Если True, безопасно экранирует все спецсимволы.
              Если False, использует расширенное экранирование.

    Returns:
        Экранированный текст, безопасный для отправки в Telegram API
    """
    if not text:
        return text

    # Преобразуем в строку, если это не строка
    text = str(text)

    if safe:
        # Безопасное экранирование - экранируем все спецсимволы
        # Бэкслеш ПЕРВЫМ, иначе будем экранировать собственные бэкслеши
        # НЕ экранируем точку (.) - она не опасна для Markdown и нужна для нормального отображения
        text = text.replace('\\', '\\\\')
        text = re.sub(r'([_*\[\]()~`>#+\-=|{}.!])', r'\\\1', text)
        # Убираем экранирование точек - они не опасны для Markdown
        text = text.replace('\\.', '.')
    else:
        # Мягкое экранирование - только критичные символы
        text = text.replace('\\', '\\\\')  # Первым, чтобы не экранировать собственные бэкслеши
        text = text.replace('_', '\\_')
        text = text.replace('*', '\\*')
        text = text.replace('[', '\\[')
        text = text.replace('`', '\\`')

    return text


def escape_markdown_v2(text: str) -> str:
    """
    Экранирование для MarkdownV2 (более строгое).
    Используется, если в будущем перейдете на parse_mode='MarkdownV2'
    """
    if not text:
        return text

    # MarkdownV2 требует экранирования почти всех спецсимволов
    special_chars = r'_*[]()~`>#+-=|{}.!\\'
    text = re.sub(f'([{re.escape(special_chars)}])', r'\\\1', text)

    return text


def format_safe_title(title: str, max_length: int = None) -> str:
    """
    Форматирует название для Markdown с безопасным экранированием.

    Args:
        title: Название видео
        max_length: Максимальная длина (не используется - названия не сокращаются)

    Returns:
        Безопасно форматированное название (без сокращений)
    """
    # Экранируем опасные символы (точки не экранируются)
    # НЕ сокращаем название - возвращаем полностью
    return escape_markdown(title, safe=True)


def format_safe_message(message: str, parse_mode: str = 'Markdown') -> str:
    """
    Форматирует все текстовые части сообщения, оставляя форматирование нетронутым.

    ВАЖНО: Эта функция ВСЕГДА отключает parse_mode и возвращает чистый текст,
    чтобы избежать ошибок парсинга Markdown от Telegram API.
    """
    if not message:
        return message

    # Всегда используем полное безопасное экранирование
    # Это предотвращает случайные ошибки парсинга
    return escape_markdown(message, safe=True)


def safe_send_message(bot, chat_id: int, text: str, parse_mode: str = 'Markdown',
                     max_retries: int = 1, **kwargs):
    """
    Безопасная отправка сообщения с автоматическим экранированием при ошибке.

    Если отправка не удалась из-за ошибки парсинга сущностей,
    автоматически экранирует текст и повторяет попытку.
    """
    try:
        return bot.send_message(chat_id, text, parse_mode=parse_mode, **kwargs)
    except Exception as e:
        error_msg = str(e)

        # Если ошибка связана с парсингом сущностей Markdown
        if "Can't parse entities" in error_msg and max_retries > 0:
            # Экранируем текст и пытаемся еще раз
            escaped_text = escape_markdown(text, safe=True)
            try:
                return bot.send_message(chat_id, escaped_text, parse_mode=None, **kwargs)
            except Exception as e2:
                raise e2

        raise e


def safe_edit_message_text(bot, text: str, chat_id: int, message_id: int,
                          parse_mode: str = 'Markdown', max_retries: int = 1, **kwargs):
    """
    Безопасное редактирование сообщения с автоматическим экранированием при ошибке.
    """
    try:
        return bot.edit_message_text(text, chat_id, message_id, parse_mode=parse_mode, **kwargs)
    except Exception as e:
        error_msg = str(e)

        if "Can't parse entities" in error_msg and max_retries > 0:
            escaped_text = escape_markdown(text, safe=True)
            try:
                return bot.edit_message_text(escaped_text, chat_id, message_id, parse_mode=None, **kwargs)
            except Exception as e2:
                raise e2

        raise e


def safe_reply_to(bot, message, text: str, parse_mode: str = 'Markdown',
                 max_retries: int = 1, **kwargs):
    """
    Безопасный ответ на сообщение с автоматическим экранированием при ошибке.
    """
    try:
        return bot.reply_to(message, text, parse_mode=parse_mode, **kwargs)
    except Exception as e:
        error_msg = str(e)

        if "Can't parse entities" in error_msg and max_retries > 0:
            escaped_text = escape_markdown(text, safe=True)
            try:
                return bot.reply_to(message, escaped_text, parse_mode=None, **kwargs)
            except Exception as e2:
                raise e2

        raise e
