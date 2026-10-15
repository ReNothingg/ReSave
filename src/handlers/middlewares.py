from __future__ import annotations

import asyncio
import logging
import time
from collections.abc import Awaitable, Callable
from typing import Any

from aiogram import BaseMiddleware
from aiogram.types import CallbackQuery, Message, TelegramObject

from ..core.user_stats import UserStatsManager

logger = logging.getLogger(__name__)
TOUCH_INTERVAL = 600


class UserRegistry(BaseMiddleware):
    def __init__(self, stats: UserStatsManager):
        self.stats = stats
        self._seen: dict[int, float] = {}

    async def __call__(
        self,
        handler: Callable[[TelegramObject, dict[str, Any]], Awaitable[Any]],
        event: TelegramObject,
        data: dict[str, Any],
    ) -> Any:
        user = getattr(event, "from_user", None)
        chat = (
            event.chat
            if isinstance(event, Message)
            else (
                event.message.chat if isinstance(event, CallbackQuery) and event.message else None
            )
        )
        if user and not user.is_bot and chat is not None and chat.type == "private":
            now = time.monotonic()
            if now - self._seen.get(user.id, 0) >= TOUCH_INTERVAL:
                self._seen[user.id] = now
                if len(self._seen) > 50_000:
                    self._seen.clear()
                try:
                    await asyncio.to_thread(self.stats.touch_user, user.id)
                except Exception as exc:
                    logger.warning("Cannot register user %s: %s", user.id, exc)
        return await handler(event, data)
