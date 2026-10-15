from __future__ import annotations

from aiogram import Bot
from aiogram.methods import EditMessageText, SendRichMessage
from aiogram.types import InputRichMessage, Message

__all__ = ["InputRichMessage", "send_rich"]


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
        method = EditMessageText(chat_id=chat_id, message_id=message_id, **params)
    return await bot(method, request_timeout=request_timeout)
