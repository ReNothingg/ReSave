from __future__ import annotations

import asyncio
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from enum import StrEnum

from aiogram import Bot
from aiogram.exceptions import (
    TelegramAPIError,
    TelegramBadRequest,
    TelegramForbiddenError,
    TelegramNetworkError,
    TelegramRetryAfter,
    TelegramServerError,
)
from aiogram.types import InlineKeyboardMarkup


class Delivery(StrEnum):
    SENT = "sent"
    UNREACHABLE = "unreachable"
    FAILED = "failed"
    UNCERTAIN = "uncertain"


class PostUnavailable(RuntimeError):
    pass


@dataclass(slots=True)
class Campaign:
    owner_id: int
    token: str
    chat_id: int
    source_id: int
    control_id: int
    recipients: tuple[int, ...]
    stop: asyncio.Event = field(default_factory=asyncio.Event)
    sent: int = 0
    unreachable: int = 0
    failed: int = 0
    uncertain: int = 0
    started_at: float = field(default_factory=time.monotonic)
    finished: bool = False
    pause_seconds: float = 0
    error: str | None = None

    @property
    def processed(self) -> int:
        return self.sent + self.unreachable + self.failed + self.uncertain

    @property
    def remaining(self) -> int:
        return len(self.recipients) - self.processed


Report = Callable[[Campaign], Awaitable[None]]


async def deliver_campaign(bot: Bot, campaign: Campaign, report: Report) -> None:
    await report(campaign)
    last_report = time.monotonic()
    for user_id in campaign.recipients:
        if campaign.stop.is_set():
            break
        try:
            result = await copy_post(bot, user_id, campaign, report)
        except PostUnavailable:
            campaign.error = "Пост недоступен для копирования. Подготовьте новую рассылку."
            break
        if result is None:
            break
        setattr(campaign, result.value, getattr(campaign, result.value) + 1)
        if time.monotonic() - last_report >= 2:
            await report(campaign)
            last_report = time.monotonic()
        await wait_or_stop(campaign.stop, 0.05)
    campaign.finished = True
    await report(campaign)


async def wait_or_stop(stop: asyncio.Event, seconds: float) -> bool:
    try:
        await asyncio.wait_for(stop.wait(), timeout=seconds)
        return True
    except TimeoutError:
        return False


async def copy_post(
    bot: Bot,
    user_id: int,
    campaign: Campaign,
    report: Report,
) -> Delivery | None:
    for attempt in range(3):
        if campaign.stop.is_set():
            return None
        try:
            await bot.copy_message(
                user_id,
                from_chat_id=campaign.chat_id,
                message_id=campaign.source_id,
                reply_markup=InlineKeyboardMarkup(inline_keyboard=[]),
                request_timeout=30,
            )
            return Delivery.SENT
        except TelegramRetryAfter as exc:
            if attempt == 2:
                return Delivery.FAILED
            campaign.pause_seconds = float(exc.retry_after)
            await report(campaign)
            stopped = await wait_or_stop(campaign.stop, campaign.pause_seconds + 0.25)
            campaign.pause_seconds = 0
            if stopped:
                return None
        except TelegramForbiddenError:
            return Delivery.UNREACHABLE
        except TelegramBadRequest as exc:
            return rejected_post(exc)
        except (TelegramNetworkError, TelegramServerError, TimeoutError):
            return Delivery.UNCERTAIN
        except TelegramAPIError:
            return Delivery.FAILED
    return Delivery.FAILED


def rejected_post(exc: TelegramBadRequest) -> Delivery:
    value = str(exc).lower()
    if any(
        marker in value
        for marker in (
            "message to copy not found",
            "message can't be copied",
            "message_id_invalid",
        )
    ):
        raise PostUnavailable from exc
    return Delivery.UNREACHABLE if "chat not found" in value else Delivery.FAILED
