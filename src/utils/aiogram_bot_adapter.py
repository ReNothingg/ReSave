from __future__ import annotations

import asyncio
import os
from pathlib import Path
from typing import Any

from aiogram import Bot
from aiogram.types import (
    BufferedInputFile,
    FSInputFile,
    InlineQueryResultsButton,
    ReplyParameters,
)


class AiogramSyncBotAdapter:
    def __init__(self, bot: Bot, loop: asyncio.AbstractEventLoop):
        self.bot = bot
        self.loop = loop

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

    @staticmethod
    def _should_use_local_file_uri() -> bool:
        try:
            import config

            return bool(config.BOT_API_IS_LOCAL and config.BOT_API_BASE_URL)
        except Exception:
            return False

    def _prepare_file(self, file_obj: Any, *, filename: str | None = None):
        if isinstance(file_obj, (FSInputFile, BufferedInputFile)):
            return file_obj

        if isinstance(file_obj, (str, os.PathLike)):
            path = str(file_obj)
            if os.path.exists(path):
                if self._should_use_local_file_uri():
                    return Path(path).resolve().as_uri()
                return FSInputFile(path, filename=filename)
            return path

        if isinstance(file_obj, bytes):
            return BufferedInputFile(file_obj, filename=filename or "file")

        if hasattr(file_obj, "read"):
            data = file_obj.read()
            file_name = filename or Path(getattr(file_obj, "name", "file")).name
            return BufferedInputFile(data, filename=file_name)

        return file_obj

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
        photo_input = self._prepare_file(photo)
        return self._call(self.bot.send_photo(chat_id=chat_id, photo=photo_input, **payload))

    def send_video(self, chat_id, video, **kwargs):
        payload = self._normalize_kwargs(kwargs)
        video_input = self._prepare_file(video)
        return self._call(self.bot.send_video(chat_id=chat_id, video=video_input, **payload))

    def send_document(self, chat_id, document, **kwargs):
        payload = self._normalize_kwargs(kwargs)
        visible_file_name = payload.pop("_visible_file_name", None)
        document_input = self._prepare_file(document, filename=visible_file_name)
        return self._call(
            self.bot.send_document(chat_id=chat_id, document=document_input, **payload)
        )

    def send_audio(self, chat_id, audio, **kwargs):
        payload = self._normalize_kwargs(kwargs)
        audio_input = self._prepare_file(audio)
        return self._call(self.bot.send_audio(chat_id=chat_id, audio=audio_input, **payload))

    def send_animation(self, chat_id, animation, **kwargs):
        payload = self._normalize_kwargs(kwargs)
        animation_input = self._prepare_file(animation)
        return self._call(
            self.bot.send_animation(chat_id=chat_id, animation=animation_input, **payload)
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

    def answer_inline_query(self, inline_query_id, results, **kwargs):
        payload = self._normalize_kwargs(kwargs)
        switch_pm_text = payload.pop("switch_pm_text", None)
        switch_pm_parameter = payload.pop("switch_pm_parameter", None)
        if switch_pm_text and switch_pm_parameter and "button" not in payload:
            payload["button"] = InlineQueryResultsButton(
                text=switch_pm_text,
                start_parameter=switch_pm_parameter,
            )

        return self._call(
            self.bot.answer_inline_query(
                inline_query_id=inline_query_id,
                results=results,
                **payload,
            )
        )

    def set_my_commands(self, commands, **kwargs):
        payload = self._normalize_kwargs(kwargs)
        return self._call(self.bot.set_my_commands(commands=commands, **payload))

    def __getattr__(self, name: str):
        return getattr(self.bot, name)
