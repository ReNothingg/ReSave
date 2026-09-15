from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Callable
from pathlib import Path
from typing import Any, TypeVar

from aiogram import Bot
from aiogram.exceptions import (
    TelegramAPIError,
    TelegramBadRequest,
    TelegramNetworkError,
    TelegramNotFound,
    TelegramRetryAfter,
)
from aiogram.types import (
    CallbackQuery,
    FSInputFile,
    InlineKeyboardMarkup,
    InputMediaPhoto,
    LinkPreviewOptions,
    Message,
    ReplyParameters,
)

from ..utils.presentation import MessageContent, as_content
from ..utils.theme import plain_icons, without_custom_emoji
from .rich_api import InputRichMessage, send_rich

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
        rich_messages: bool = True,
        custom_emoji: bool = True,
    ):
        self.bot = bot
        self.cloud_bot = cloud_bot
        self.local_api = local_api
        self.use_file_uri = use_file_uri
        self.cloud_upload_limit = cloud_upload_limit
        self.rich_messages = rich_messages
        self.custom_emoji = custom_emoji

    async def _retry(
        self,
        operation: Callable[[], Awaitable[T]],
        attempts: int = 3,
        *,
        retry_network: bool = False,
    ) -> T:
        for attempt in range(1, attempts + 1):
            try:
                return await operation()
            except TelegramRetryAfter as exc:
                if attempt == attempts:
                    raise
                await asyncio.sleep(float(exc.retry_after) + 0.25)
            except TelegramNetworkError:
                if not retry_network or attempt == attempts:
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
        return (
            ReplyParameters(message_id=message_id, allow_sending_without_reply=True)
            if message_id
            else None
        )

    async def edit_status(
        self,
        chat_id: int,
        message_id: int,
        text: str | MessageContent,
        **kwargs,
    ) -> bool:
        try:
            async with asyncio.timeout(8):
                await self._present(
                    chat_id, text, message_id=message_id, request_timeout=7, **kwargs
                )
            return True
        except (TelegramAPIError, TimeoutError) as exc:
            if "message is not modified" not in str(exc).lower():
                logger.debug("Cannot edit status %s/%s: %s", chat_id, message_id, exc)
            return False

    async def delete_status(self, chat_id: int, message_id: int) -> None:
        try:
            await self.bot.delete_message(chat_id=chat_id, message_id=message_id)
        except TelegramBadRequest as exc:
            logger.debug("Cannot delete status %s/%s: %s", chat_id, message_id, exc)

    async def send_message(self, chat_id: int, text: str | MessageContent, **kwargs):
        return await self._present(chat_id, text, **kwargs)

    async def reply(self, message: Message, text: str | MessageContent, **kwargs):
        return await self.send_message(
            message.chat.id,
            text,
            reply_parameters=self._reply(message.message_id),
            **kwargs,
        )

    async def _present(
        self,
        chat_id: int,
        value: str | MessageContent,
        *,
        message_id: int | None = None,
        **kwargs,
    ):
        content = as_content(value)
        for _ in range(3):
            try:
                return await self._retry(
                    lambda: self._send_content(chat_id, message_id, content, kwargs)
                )
            except (TelegramBadRequest, TelegramNotFound) as exc:
                if not self._downgrade(exc):
                    raise
        raise RuntimeError("Telegram rejected message formatting")

    async def _send_content(
        self, chat_id: int, message_id: int | None, content: MessageContent, kwargs: dict
    ):
        params = dict(kwargs)
        params.pop("parse_mode", None)
        markup = params.get("reply_markup")
        if isinstance(markup, InlineKeyboardMarkup) and not self.custom_emoji:
            params["reply_markup"] = plain_icons(markup)
        params.setdefault("request_timeout", 30)
        if self.rich_messages:
            html = (
                content.rich_html if self.custom_emoji else without_custom_emoji(content.rich_html)
            )
            params["rich_message"] = InputRichMessage(html=html)
            return await send_rich(self.bot, chat_id=chat_id, message_id=message_id, **params)
        else:
            params["text"] = (
                content.html if self.custom_emoji else without_custom_emoji(content.html)
            )
            params["parse_mode"] = "HTML"
            params["link_preview_options"] = LinkPreviewOptions(is_disabled=True)
        if message_id is not None:
            return await self.bot.edit_message_text(
                chat_id=chat_id, message_id=message_id, **params
            )
        return await self.bot.send_message(chat_id=chat_id, **params)

    def _downgrade(self, exc: TelegramAPIError) -> bool:
        value = str(exc).lower()
        if self.custom_emoji and self._emoji_error(value):
            self.custom_emoji = False
            logger.warning(
                "Custom emoji unavailable; keeping Rich Messages and colored buttons: %s", exc
            )
            return True
        if self.rich_messages and self._rich_error(exc, value):
            self.rich_messages = False
            logger.warning("Rich Messages unavailable; using formatted HTML: %s", exc)
            return True
        return False

    @staticmethod
    def _emoji_error(value: str) -> bool:
        return any(
            marker in value
            for marker in (
                "custom emoji",
                "custom_emoji",
                "emoji-id",
                "emoji_id",
                "emoji id",
                "document_invalid",
            )
        )

    @staticmethod
    def _rich_error(exc: TelegramAPIError, value: str) -> bool:
        if any(
            marker in value
            for marker in ("chat not found", "message to edit not found", "message can't be edited")
        ):
            return False
        return isinstance(exc, TelegramNotFound) or any(
            marker in value
            for marker in (
                "rich message",
                "rich_message",
                "unknown method",
                "unsupported method",
                "method not found",
                "can't parse",
                "unsupported start tag",
            )
        )

    def _media_kwargs(self, kwargs: dict) -> dict:
        result = dict(kwargs)
        if not self.custom_emoji and isinstance(result.get("caption"), str):
            result["caption"] = without_custom_emoji(result["caption"])
        return result

    async def _media_request(self, operation: Callable[[], Awaitable[T]]) -> T:
        try:
            return await self._retry(operation)
        except TelegramBadRequest as exc:
            if not self.custom_emoji or not self._emoji_error(str(exc).lower()):
                raise
            self.custom_emoji = False
            logger.warning("Custom emoji unavailable in media captions: %s", exc)
            return await self._retry(operation)

    async def answer_callback(self, call: CallbackQuery, text: str | None = None) -> None:
        try:
            await call.answer(text=text, request_timeout=5)
        except (TelegramAPIError, TimeoutError) as exc:
            logger.debug("Cannot answer callback: %s", exc)

    def _can_cloud_fallback(self, path: Path) -> bool:
        return bool(
            self.cloud_bot
            and self.local_api
            and path.is_file()
            and path.stat().st_size <= self.cloud_upload_limit
        )

    @staticmethod
    def _is_transport_error(exc: BaseException) -> bool:
        return isinstance(exc, TelegramBadRequest) and "invalid file http url" in str(exc).lower()

    async def _upload_with_fallback(
        self,
        path: Path,
        local_call: Callable[[Any], Awaitable[T]],
        cloud_call: Callable[[Bot, Any], Awaitable[T]],
        filename: str | None = None,
    ) -> T:
        try:
            return await self._media_request(
                lambda: local_call(self._file(path, filename=filename))
            )
        except Exception as exc:
            if not self._can_cloud_fallback(path) or not self._is_transport_error(exc):
                raise
            logger.warning("Local Bot API upload failed; using cloud fallback: %s", exc)
            assert self.cloud_bot is not None
            return await self._media_request(
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
            lambda media: self.bot.send_video(video=media, **self._media_kwargs(kwargs)),
            lambda bot, media: bot.send_video(video=media, **self._media_kwargs(kwargs)),
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
            lambda media: self.bot.send_audio(audio=media, **self._media_kwargs(kwargs)),
            lambda bot, media: bot.send_audio(audio=media, **self._media_kwargs(kwargs)),
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
            lambda media: self.bot.send_document(document=media, **self._media_kwargs(kwargs)),
            lambda bot, media: bot.send_document(
                document=FSInputFile(path, filename=filename or path.name),
                **self._media_kwargs(kwargs),
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
            lambda media: self.bot.send_animation(animation=media, **self._media_kwargs(kwargs)),
            lambda bot, media: bot.send_animation(animation=media, **self._media_kwargs(kwargs)),
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
            lambda media: self.bot.send_photo(photo=media, **self._media_kwargs(kwargs)),
            lambda bot, media: bot.send_photo(photo=media, **self._media_kwargs(kwargs)),
        )

    async def send_photo_group(
        self,
        chat_id: int,
        paths: list[Path],
        *,
        caption: str | None,
        reply_to: int | None,
    ):
        def media_items(local: bool = True):
            caption_text = self._media_kwargs({"caption": caption})["caption"]
            return [
                InputMediaPhoto(
                    media=self._file(path, local=local),
                    caption=caption_text if index == 0 else None,
                    parse_mode="HTML" if index == 0 and caption_text else None,
                )
                for index, path in enumerate(paths)
            ]

        kwargs = {
            "chat_id": chat_id,
            "reply_parameters": self._reply(reply_to),
            "request_timeout": 300,
        }
        try:
            return await self._media_request(
                lambda: self.bot.send_media_group(media=media_items(), **kwargs)
            )
        except Exception as exc:
            if not all(
                self._can_cloud_fallback(path) for path in paths
            ) or not self._is_transport_error(exc):
                raise
            assert self.cloud_bot is not None
            return await self._media_request(
                lambda: self.cloud_bot.send_media_group(media=media_items(local=False), **kwargs)
            )
