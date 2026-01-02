import re


def escape_markdown(text: str, safe: bool = True) -> str:
    if not text:
        return text

    text = str(text)

    if safe:
        text = text.replace('\\', '\\\\')
        text = re.sub(r'([_*\[\]()~`>#+\-=|{}.!])', r'\\\1', text)
        text = text.replace('\\.', '.')
    else:
        text = text.replace('\\', '\\\\')
        text = text.replace('_', '\\_')
        text = text.replace('*', '\\*')
        text = text.replace('[', '\\[')
        text = text.replace('`', '\\`')

    return text


def escape_markdown_v2(text: str) -> str:
    if not text:
        return text

    special_chars = r'_*[]()~`>#+-=|{}.!\\'
    text = re.sub(f'([{re.escape(special_chars)}])', r'\\\1', text)

    return text


def format_safe_title(title: str, max_length: int = None) -> str:
    return escape_markdown(title, safe=True)


def format_safe_message(message: str, parse_mode: str = 'Markdown') -> str:
    if not message:
        return message

    return escape_markdown(message, safe=True)


def safe_send_message(bot, chat_id: int, text: str, parse_mode: str = 'Markdown',
                     max_retries: int = 1, **kwargs):
    try:
        return bot.send_message(chat_id, text, parse_mode=parse_mode, **kwargs)
    except Exception as e:
        error_msg = str(e)

        if "Can't parse entities" in error_msg and max_retries > 0:
            escaped_text = escape_markdown(text, safe=True)
            try:
                return bot.send_message(chat_id, escaped_text, parse_mode=None, **kwargs)
            except Exception as e2:
                raise e2

        raise e


def safe_edit_message_text(bot, text: str, chat_id: int, message_id: int,
                          parse_mode: str = 'Markdown', max_retries: int = 1, **kwargs):
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
