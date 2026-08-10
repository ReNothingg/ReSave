from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Callable
from pathlib import Path
from typing import Any, TypeVar

from aiogram import Bot
from aiogram.exceptions import (
    TelegramBadRequest,
    TelegramNetworkError,
    TelegramRetryAfter,
)
from aiogram.types import FSInputFile, InputMediaPhoto, ReplyParameters

logger = logging.getLogger(__name__)
T = TypeVar("T")


class TelegramGateway:
    def __init__(
        self,
        bot: Bot,
        *,
        cloud_bot: Bot | None = None,
        local_api: bool = False,
        use_file_uri: bool = False,
        cloud_upload_limit: int = 50 * 1024 * 1024,
    ):
        self.bot = bot
        self.cloud_bot = cloud_bot
        self.local_api = local_api
        self.use_file_uri = use_file_uri
        self.cloud_upload_limit = cloud_upload_limit

    async def _retry(self, operation: Callable[[], Awaitable[T]], attempts: int = 3) -> T:
        for attempt in range(1, attempts + 1):
            try:
                return await operation()
            except TelegramRetryAfter as exc:
                if attempt == attempts:
                    raise
                await asyncio.sleep(float(exc.retry_after) + 0.25)
            except TelegramNetworkError:
                if attempt == attempts:
                    raise
                await asyncio.sleep(min(2 ** (attempt - 1), 5))
        raise RuntimeError("unreachable")

    def _file(self, path: str | Path, *, local: bool = True, filename: str | None = None):
        resolved = Path(path).resolve()
        if (
            local
            and self.local_api
            and self.use_file_uri
            and (filename is None or filename == resolved.name)
        ):
            return resolved.as_uri()
        return FSInputFile(resolved, filename=filename or resolved.name)

    @staticmethod
    def _reply(message_id: int | None) -> ReplyParameters | None:
        return ReplyParameters(message_id=message_id) if message_id else None

    async def edit_status(self, chat_id: int, message_id: int, text: str, **kwargs) -> bool:
        try:
            await self._retry(
                lambda: self.bot.edit_message_text(
                    chat_id=chat_id,
                    message_id=message_id,
                    text=text,
                    parse_mode="HTML",
                    **kwargs,
                )
            )
            return True
        except TelegramBadRequest as exc:
            if "message is not modified" not in str(exc).lower():
                logger.debug("Cannot edit status %s/%s: %s", chat_id, message_id, exc)
            return False

    async def delete_status(self, chat_id: int, message_id: int) -> None:
        try:
            await self.bot.delete_message(chat_id=chat_id, message_id=message_id)
        except TelegramBadRequest as exc:
            logger.debug("Cannot delete status %s/%s: %s", chat_id, message_id, exc)

    async def send_message(self, chat_id: int, text: str, **kwargs):
        return await self._retry(
            lambda: self.bot.send_message(chat_id=chat_id, text=text, parse_mode="HTML", **kwargs)
        )

    def _can_cloud_fallback(self, path: Path) -> bool:
        return bool(
            self.cloud_bot
            and self.local_api
            and path.is_file()
            and path.stat().st_size <= self.cloud_upload_limit
        )

    @staticmethod
    def _is_transport_error(exc: BaseException) -> bool:
        value = str(exc).lower()
        return any(
            marker in value
            for marker in (
                "clientdecodeerror",
                "failed to decode object",
                "connection reset",
                "server disconnected",
                "request timeout",
                "timeout error",
                "invalid file http url",
            )
        )

    async def _upload_with_fallback(
        self,
        path: Path,
        local_call: Callable[[Any], Awaitable[T]],
        cloud_call: Callable[[Bot, Any], Awaitable[T]],
        filename: str | None = None,
    ) -> T:
        try:
            return await self._retry(lambda: local_call(self._file(path, filename=filename)))
        except Exception as exc:
            if not self._can_cloud_fallback(path) or not self._is_transport_error(exc):
                raise
            logger.warning("Local Bot API upload failed; using cloud fallback: %s", exc)
            assert self.cloud_bot is not None
            return await self._retry(
                lambda: cloud_call(self.cloud_bot, self._file(path, local=False))
            )

    async def send_video(
        self,
        chat_id: int,
        path: Path,
        *,
        caption: str,
        reply_to: int | None,
        metadata: dict[str, int],
    ):
        kwargs = {
            "chat_id": chat_id,
            "caption": caption,
            "parse_mode": "HTML",
            "supports_streaming": True,
            "reply_parameters": self._reply(reply_to),
            "request_timeout": 600,
            **metadata,
        }
        return await self._upload_with_fallback(
            path,
            lambda media: self.bot.send_video(video=media, **kwargs),
            lambda bot, media: bot.send_video(video=media, **kwargs),
        )

    async def send_audio(
        self,
        chat_id: int,
        path: Path,
        *,
        caption: str,
        title: str,
        performer: str | None,
        reply_to: int | None,
        duration: int | None,
    ):
        kwargs = {
            "chat_id": chat_id,
            "caption": caption,
            "parse_mode": "HTML",
            "title": title,
            "performer": performer,
            "reply_parameters": self._reply(reply_to),
            "request_timeout": 600,
        }
        if duration:
            kwargs["duration"] = duration
        return await self._upload_with_fallback(
            path,
            lambda media: self.bot.send_audio(audio=media, **kwargs),
            lambda bot, media: bot.send_audio(audio=media, **kwargs),
        )

    async def send_document(
        self,
        chat_id: int,
        path: Path,
        *,
        caption: str | None,
        reply_to: int | None,
        filename: str | None = None,
    ):
        kwargs = {
            "chat_id": chat_id,
            "caption": caption,
            "parse_mode": "HTML" if caption else None,
            "reply_parameters": self._reply(reply_to),
            "request_timeout": 600,
        }
        return await self._upload_with_fallback(
            path,
            lambda media: self.bot.send_document(document=media, **kwargs),
            lambda bot, media: bot.send_document(
                document=FSInputFile(path, filename=filename or path.name), **kwargs
            ),
            filename=filename,
        )

    async def send_animation(
        self,
        chat_id: int,
        path: Path,
        *,
        caption: str,
        reply_to: int | None,
    ):
        kwargs = {
            "chat_id": chat_id,
            "caption": caption,
            "parse_mode": "HTML",
            "reply_parameters": self._reply(reply_to),
            "request_timeout": 600,
        }
        return await self._upload_with_fallback(
            path,
            lambda media: self.bot.send_animation(animation=media, **kwargs),
            lambda bot, media: bot.send_animation(animation=media, **kwargs),
        )

    async def send_photo(
        self,
        chat_id: int,
        path: Path,
        *,
        caption: str | None,
        reply_to: int | None,
    ):
        kwargs = {
            "chat_id": chat_id,
            "caption": caption,
            "parse_mode": "HTML" if caption else None,
            "reply_parameters": self._reply(reply_to),
            "request_timeout": 180,
        }
        return await self._upload_with_fallback(
            path,
            lambda media: self.bot.send_photo(photo=media, **kwargs),
            lambda bot, media: bot.send_photo(photo=media, **kwargs),
        )

    async def send_photo_group(
        self,
        chat_id: int,
        paths: list[Path],
        *,
        caption: str | None,
        reply_to: int | None,
    ):
        media = [
            InputMediaPhoto(
                media=self._file(path),
                caption=caption if index == 0 else None,
                parse_mode="HTML" if index == 0 and caption else None,
            )
            for index, path in enumerate(paths)
        ]
        kwargs = {
            "chat_id": chat_id,
            "reply_parameters": self._reply(reply_to),
            "request_timeout": 300,
        }
        try:
            return await self._retry(lambda: self.bot.send_media_group(media=media, **kwargs))
        except Exception as exc:
            if not all(
                self._can_cloud_fallback(path) for path in paths
            ) or not self._is_transport_error(exc):
                raise
            assert self.cloud_bot is not None
            cloud_media = [
                InputMediaPhoto(
                    media=self._file(path, local=False),
                    caption=caption if index == 0 else None,
                    parse_mode="HTML" if index == 0 and caption else None,
                )
                for index, path in enumerate(paths)
            ]
            return await self._retry(
                lambda: self.cloud_bot.send_media_group(media=cloud_media, **kwargs)
            )
