from __future__ import annotations

import logging

from aiogram.exceptions import TelegramAPIError
from aiogram.types import ErrorEvent, Message

from ..core.telegram_gateway import TelegramGateway

logger = logging.getLogger(__name__)


class ErrorHandler:
    def __init__(self, telegram: TelegramGateway):
        self.telegram = telegram

    async def __call__(self, event: ErrorEvent) -> bool:
        exc = event.exception
        logger.error(
            "Update %s failed",
            event.update.update_id,
            exc_info=(type(exc), exc, exc.__traceback__),
        )
        if isinstance(exc, TelegramAPIError):
            return True
        call = event.update.callback_query
        message = event.update.message
        if call:
            await self.telegram.answer_callback(
                call, "Не удалось выполнить действие. Попробуйте ещё раз."
            )
        elif isinstance(message, Message) and message.chat.type == "private":
            try:
                await self.telegram.send_message(
                    message.chat.id,
                    "Не удалось обработать запрос. Попробуйте ещё раз.",
                )
            except (TelegramAPIError, TimeoutError):
                logger.warning("Could not report failed update %s", event.update.update_id)
        return True
