from __future__ import annotations

import asyncio
import logging
import os
from pathlib import Path
from typing import Any

import config
from aiogram import Bot
from aiogram.types import (
    BufferedInputFile,
    FSInputFile,
    ReplyParameters,
)


logger = logging.getLogger(__name__)


class AiogramSyncBotAdapter:
    def __init__(
        self,
        bot: Bot,
        loop: asyncio.AbstractEventLoop,
        cloud_upload_bot: Bot | None = None,
    ):
        self.bot = bot
        self.loop = loop
        self.cloud_upload_bot = cloud_upload_bot

    def _call(self, coro):
        try:
            running_loop = asyncio.get_running_loop()
        except RuntimeError:
            running_loop = None

        if running_loop is self.loop:
            raise RuntimeError(
                "AiogramSyncBotAdapter cannot be used from the bot event loop. "
                "Use the async aiogram Bot instance directly."
            )

        future = asyncio.run_coroutine_threadsafe(coro, self.loop)
        return future.result()

    def _is_local_api(self) -> bool:
        api = getattr(getattr(self.bot, "session", None), "api", None)
        return bool(getattr(api, "is_local", False))

    def _can_fallback_to_cloud(self, file_obj: Any) -> bool:
        if not self.cloud_upload_bot or not self._is_local_api():
            return False

        if isinstance(file_obj, (str, os.PathLike)) and os.path.exists(file_obj):
            return os.path.getsize(file_obj) <= config.CLOUD_BOT_API_UPLOAD_LIMIT

        return isinstance(file_obj, bytes) and len(file_obj) <= config.CLOUD_BOT_API_UPLOAD_LIMIT

    @staticmethod
    def _is_local_upload_transport_error(exc: Exception) -> bool:
        error_text = str(exc).lower()
        transport_markers = (
            "clientdecodeerror",
            "failed to decode object",
            "connection reset",
            "server disconnected",
            "clientoserror",
            "request timeout",
            "timeout error",
            "invalid file http url specified: url host is empty",
        )
        return any(marker in error_text for marker in transport_markers)

    def _prepare_file(
        self,
        file_obj: Any,
        *,
        filename: str | None = None,
        local_upload: bool = False,
    ):
        if isinstance(file_obj, (FSInputFile, BufferedInputFile)):
            return file_obj

        if isinstance(file_obj, (str, os.PathLike)):
            path = str(file_obj)
            if os.path.exists(path):
                if (
                    local_upload
                    and config.BOT_API_USE_LOCAL_FILE_PATHS
                    and self._is_local_api()
                ):
                    return path
                return FSInputFile(path, filename=filename or Path(path).name)
            return path

        if isinstance(file_obj, bytes):
            return BufferedInputFile(file_obj, filename=filename or "file")

        if hasattr(file_obj, "read"):
            data = file_obj.read()
            file_name = filename or Path(getattr(file_obj, "name", "file")).name
            return BufferedInputFile(data, filename=file_name)

        return file_obj

    def _call_with_cloud_fallback(
        self,
        *,
        local_coro_factory,
        cloud_coro_factory,
        file_obj: Any,
        method_name: str,
    ):
        try:
            return self._call(local_coro_factory())
        except Exception as exc:
            if not (
                self._can_fallback_to_cloud(file_obj)
                and self._is_local_upload_transport_error(exc)
            ):
                raise

            logger.warning(
                "Local Bot API %s failed; retrying through cloud Bot API: %s",
                method_name,
                exc,
            )
            return self._call(cloud_coro_factory())

    @staticmethod
    def _normalize_kwargs(kwargs: dict[str, Any]) -> dict[str, Any]:
        normalized = dict(kwargs)

        timeout = normalized.pop("timeout", None)
        if timeout is not None:
            normalized["request_timeout"] = timeout

        visible_file_name = normalized.pop("visible_file_name", None)
        reply_to_message_id = normalized.pop("reply_to_message_id", None)

        if reply_to_message_id is not None and "reply_parameters" not in normalized:
            normalized["reply_parameters"] = ReplyParameters(message_id=reply_to_message_id)

        if visible_file_name is not None:
            normalized["_visible_file_name"] = visible_file_name

        return normalized

    def send_message(self, chat_id, text, **kwargs):
        payload = self._normalize_kwargs(kwargs)
        return self._call(self.bot.send_message(chat_id=chat_id, text=text, **payload))

    def edit_message_text(self, text, chat_id, message_id, **kwargs):
        payload = self._normalize_kwargs(kwargs)
        return self._call(
            self.bot.edit_message_text(
                text=text,
                chat_id=chat_id,
                message_id=message_id,
                **payload,
            )
        )

    def reply_to(self, message, text, **kwargs):
        payload = dict(kwargs)
        payload.setdefault("reply_to_message_id", message.message_id)
        return self.send_message(message.chat.id, text, **payload)

    def send_photo(self, chat_id, photo, **kwargs):
        payload = self._normalize_kwargs(kwargs)
        photo_input = self._prepare_file(photo, local_upload=True)
        return self._call(self.bot.send_photo(chat_id=chat_id, photo=photo_input, **payload))

    def send_video(self, chat_id, video, **kwargs):
        payload = self._normalize_kwargs(kwargs)
        video_input = self._prepare_file(video, local_upload=True)
        return self._call_with_cloud_fallback(
            local_coro_factory=lambda: self.bot.send_video(
                chat_id=chat_id,
                video=video_input,
                **payload,
            ),
            cloud_coro_factory=lambda: self.cloud_upload_bot.send_video(
                chat_id=chat_id,
                video=self._prepare_file(video),
                **payload,
            ),
            file_obj=video,
            method_name="sendVideo",
        )

    def send_document(self, chat_id, document, **kwargs):
        payload = self._normalize_kwargs(kwargs)
        visible_file_name = payload.pop("_visible_file_name", None)
        document_input = self._prepare_file(
            document,
            filename=visible_file_name,
            local_upload=True,
        )
        return self._call_with_cloud_fallback(
            local_coro_factory=lambda: self.bot.send_document(
                chat_id=chat_id,
                document=document_input,
                **payload,
            ),
            cloud_coro_factory=lambda: self.cloud_upload_bot.send_document(
                chat_id=chat_id,
                document=self._prepare_file(document, filename=visible_file_name),
                **payload,
            ),
            file_obj=document,
            method_name="sendDocument",
        )

    def send_audio(self, chat_id, audio, **kwargs):
        payload = self._normalize_kwargs(kwargs)
        audio_input = self._prepare_file(audio, local_upload=True)
        return self._call_with_cloud_fallback(
            local_coro_factory=lambda: self.bot.send_audio(
                chat_id=chat_id,
                audio=audio_input,
                **payload,
            ),
            cloud_coro_factory=lambda: self.cloud_upload_bot.send_audio(
                chat_id=chat_id,
                audio=self._prepare_file(audio),
                **payload,
            ),
            file_obj=audio,
            method_name="sendAudio",
        )

    def send_animation(self, chat_id, animation, **kwargs):
        payload = self._normalize_kwargs(kwargs)
        animation_input = self._prepare_file(animation, local_upload=True)
        return self._call_with_cloud_fallback(
            local_coro_factory=lambda: self.bot.send_animation(
                chat_id=chat_id,
                animation=animation_input,
                **payload,
            ),
            cloud_coro_factory=lambda: self.cloud_upload_bot.send_animation(
                chat_id=chat_id,
                animation=self._prepare_file(animation),
                **payload,
            ),
            file_obj=animation,
            method_name="sendAnimation",
        )

    def send_media_group(self, chat_id, media, **kwargs):
        payload = self._normalize_kwargs(kwargs)
        return self._call(self.bot.send_media_group(chat_id=chat_id, media=media, **payload))

    def delete_message(self, chat_id, message_id, **kwargs):
        payload = self._normalize_kwargs(kwargs)
        return self._call(
            self.bot.delete_message(chat_id=chat_id, message_id=message_id, **payload)
        )

    def answer_callback_query(self, callback_query_id, text=None, **kwargs):
        payload = self._normalize_kwargs(kwargs)
        return self._call(
            self.bot.answer_callback_query(
                callback_query_id=callback_query_id,
                text=text,
                **payload,
            )
        )

    def set_my_commands(self, commands, **kwargs):
        payload = self._normalize_kwargs(kwargs)
        return self._call(self.bot.set_my_commands(commands=commands, **payload))

    def __getattr__(self, name: str):
        return getattr(self.bot, name)
