from __future__ import annotations

from aiogram import Bot, methods
from aiogram.methods.base import TelegramMethod
from aiogram.types import InlineKeyboardMarkup, Message, ReplyParameters
from aiogram.types.base import TelegramObject

try:
    from aiogram.types import InputRichMessage
except ImportError:

    class InputRichMessage(TelegramObject):
        html: str


class _SendRichMessage(TelegramMethod[Message]):
    __returning__ = Message
    __api_method__ = "sendRichMessage"

    chat_id: int | str
    rich_message: InputRichMessage
    reply_parameters: ReplyParameters | None = None
    reply_markup: InlineKeyboardMarkup | None = None


class _EditRichMessage(TelegramMethod[Message | bool]):
    __returning__ = Message | bool
    __api_method__ = "editMessageText"

    chat_id: int | str
    message_id: int
    rich_message: InputRichMessage
    reply_markup: InlineKeyboardMarkup | None = None


SendRichMessage = getattr(methods, "SendRichMessage", _SendRichMessage)
EditRichMessage = (
    methods.EditMessageText
    if "rich_message" in methods.EditMessageText.model_fields
    else _EditRichMessage
)


async def send_rich(
    bot: Bot,
    *,
    chat_id: int | str,
    message_id: int | None = None,
    **params,
) -> Message | bool:
    request_timeout = params.pop("request_timeout", None)
    if message_id is None:
        method = SendRichMessage(chat_id=chat_id, **params)
    else:
        method = EditRichMessage(chat_id=chat_id, message_id=message_id, **params)
    return await bot(method, request_timeout=request_timeout)
