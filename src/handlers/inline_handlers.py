from __future__ import annotations

import asyncio
import logging
from collections.abc import Callable

from aiogram import Bot, Router
from aiogram.exceptions import TelegramBadRequest
from aiogram.types import (
    CallbackQuery,
    ChosenInlineResult,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    InlineQuery,
    InlineQueryResultArticle,
    InlineQueryResultCachedVideo,
    InlineQueryResultsButton,
    InputTextMessageContent,
)

import config
from ..core.access import build_duration_limit_error, build_file_size_limit_error
from ..core.inline_media import InlineMediaRecord, InlineMediaService, InlineMediaStore
from ..core.tiktok_photo_handler import is_tiktok_photo_url
from ..utils.message_templates import MessageTemplate
from ..utils.ui_manager import get_ui_manager
from ..utils.url_validator import get_url_validator

logger = logging.getLogger(__name__)

INLINE_THUMBNAIL_URL = (
    "https://raw.githubusercontent.com/ReNothingg/ReNothingg/refs/heads/main/main.jpg"
)
PREPARING_RESULT_PREFIX = "inline_prepare_"
CHECK_CALLBACK_PREFIX = "inline_check:"


def _expired_query_error(exc: Exception) -> bool:
    text = str(exc).lower()
    return (
        "query is too old" in text
        or "response timeout expired" in text
        or "query id is invalid" in text
    )


def _invalid_cached_file_error(exc: Exception) -> bool:
    text = str(exc).lower()
    return "wrong file identifier" in text or "failed to get http url content" in text


def build_cached_video_result(
    record: InlineMediaRecord,
    *,
    reply_markup: InlineKeyboardMarkup | None = None,
) -> InlineQueryResultCachedVideo:
    details = []
    if record.uploader:
        details.append(record.uploader)
    if record.height:
        details.append(f"{record.height}p")
    if record.duration:
        minutes, seconds = divmod(record.duration, 60)
        details.append(f"{minutes}:{seconds:02d}")
    return InlineQueryResultCachedVideo(
        id=f"cached_{record.cache_key[:32]}",
        video_file_id=record.file_id,
        title=record.title,
        description=" · ".join(details) or "Видео готово",
        caption=MessageTemplate.format_inline_caption(record.title, record.url),
        parse_mode="HTML",
        reply_markup=reply_markup,
    )


def register_inline_handlers(
    router: Router,
    sync_bot,
    *,
    create_start_parameter: Callable[[str], str],
) -> InlineMediaService:
    ui_manager = get_ui_manager()
    store = InlineMediaStore(config.STATS_DB_PATH)
    cache_chat_id = config.INLINE_CACHE_CHAT_ID or (
        config.ADMIN_IDS[0] if config.ADMIN_IDS else 0
    )
    service = InlineMediaService(
        sync_bot=sync_bot,
        store=store,
        cache_chat_id=cache_chat_id,
        temp_dir=config.TEMP_DIR,
        max_concurrent=config.INLINE_MAX_CONCURRENT,
        cache_ttl=config.INLINE_CACHE_TTL,
        error_ttl=config.INLINE_ERROR_TTL,
    )
    if not cache_chat_id:
        logger.warning(
            "Inline cache is disabled until INLINE_CACHE_CHAT_ID or ADMIN_IDS is configured"
        )
    debounce_tasks: dict[int, asyncio.Task] = {}

    def schedule_preparation(url: str, user_id: int) -> None:
        previous = debounce_tasks.get(user_id)
        if previous and not previous.done():
            previous.cancel()

        async def delayed_start() -> None:
            try:
                await asyncio.sleep(config.INLINE_DEBOUNCE_MS / 1000)
                service.ensure_started(url, user_id)
            except asyncio.CancelledError:
                return
            finally:
                if debounce_tasks.get(user_id) is asyncio.current_task():
                    debounce_tasks.pop(user_id, None)

        debounce_tasks[user_id] = asyncio.create_task(
            delayed_start(),
            name=f"inline-debounce:{user_id}",
        )

    def open_bot_results_button(url: str | None = None) -> InlineQueryResultsButton:
        return InlineQueryResultsButton(
            text="Открыть ReSave",
            start_parameter=create_start_parameter(url) if url else "start",
        )

    def open_bot_url(url: str, token: str | None = None) -> str:
        start_token = f"i_{token}" if token else create_start_parameter(url)
        return f"https://t.me/ReSafeBot?start={start_token}"

    def ready_markup(url: str) -> InlineKeyboardMarkup:
        return InlineKeyboardMarkup(
            inline_keyboard=[
                [
                    InlineKeyboardButton(text="Открыть оригинал", url=url),
                    InlineKeyboardButton(text="Открыть ReSave", url=open_bot_url(url)),
                ]
            ]
        )

    def status_markup(token: str, url: str, *, ready: bool = False) -> InlineKeyboardMarkup:
        if ready:
            return InlineKeyboardMarkup(
                inline_keyboard=[
                    [
                        InlineKeyboardButton(
                            text="Отправить видео",
                            switch_inline_query_current_chat=url,
                        )
                    ],
                    [InlineKeyboardButton(text="Открыть ReSave", url=open_bot_url(url))],
                ]
            )
        return InlineKeyboardMarkup(
            inline_keyboard=[
                [
                    InlineKeyboardButton(
                        text="Проверить готовность",
                        callback_data=f"{CHECK_CALLBACK_PREFIX}{token}",
                    )
                ],
                [
                    InlineKeyboardButton(
                        text="Обновить результаты",
                        switch_inline_query_current_chat=url,
                    )
                ],
                [InlineKeyboardButton(text="Открыть ReSave", url=open_bot_url(url, token))],
            ]
        )

    def article(
        *,
        result_id: str,
        title: str,
        description: str,
        text: str,
        markup: InlineKeyboardMarkup | None = None,
    ) -> InlineQueryResultArticle:
        return InlineQueryResultArticle(
            id=result_id[:64],
            title=title,
            description=description,
            input_message_content=InputTextMessageContent(message_text=text),
            thumbnail_url=INLINE_THUMBNAIL_URL,
            reply_markup=markup,
        )

    def preparing_result(url: str, user_id: int, status: str) -> InlineQueryResultArticle:
        token = store.create_request(url, user_id, ttl=config.INLINE_REQUEST_TTL)
        if status == "error":
            title = "Не удалось подготовить · повторить"
            description = "Нажмите результат, затем кнопку повторной проверки"
            panel_title = "Подготовка не удалась"
            lines = [
                "Источник временно не отдал видео.",
                "Нажмите «Проверить готовность», чтобы повторить.",
            ]
            icon = "⚠️"
        else:
            title = "Видео готовится"
            description = "Откройте результат снова через несколько секунд"
            panel_title = "Готовлю видео"
            lines = [
                "Ссылка уже в очереди.",
                "Когда видео будет готово, нажмите «Отправить видео».",
            ]
            icon = "⏳"
        return article(
            result_id=f"{PREPARING_RESULT_PREFIX}{token}",
            title=title,
            description=description,
            text=ui_manager.format_panel(panel_title, lines, icon=icon),
            markup=status_markup(token, url),
        )

    async def answer_query(
        inline_query: InlineQuery,
        *,
        results: list,
        cache_time: int,
        button: InlineQueryResultsButton | None = None,
    ) -> bool:
        try:
            await inline_query.answer(
                results=results,
                cache_time=cache_time,
                is_personal=True,
                button=button,
            )
            return True
        except TelegramBadRequest as exc:
            if _expired_query_error(exc):
                logger.info("Inline query expired before answer: %s", exc)
                return False
            raise

    async def edit_status_ready(bot: Bot, inline_message_id: str, url: str) -> None:
        try:
            await bot.edit_message_text(
                inline_message_id=inline_message_id,
                text=ui_manager.format_panel(
                    "Видео готово",
                    ["Нажмите кнопку ниже и выберите готовое видео."],
                    icon="✅",
                ),
                reply_markup=status_markup("", url, ready=True),
            )
        except TelegramBadRequest as exc:
            logger.info("Inline ready status was not editable: %s", exc)

    async def edit_status_error(bot: Bot, inline_message_id: str, token: str, url: str) -> None:
        try:
            await bot.edit_message_text(
                inline_message_id=inline_message_id,
                text=ui_manager.format_panel(
                    "Не удалось подготовить видео",
                    ["Повторите проверку или откройте ссылку напрямую в ReSave."],
                    icon="❌",
                ),
                reply_markup=status_markup(token, url),
            )
        except TelegramBadRequest as exc:
            logger.info("Inline error status was not editable: %s", exc)

    async def wait_and_update(
        bot: Bot,
        *,
        token: str,
        inline_message_id: str,
        force: bool = False,
    ) -> None:
        request = store.resolve_request(token, ttl=config.INLINE_REQUEST_TTL)
        if not request:
            return
        url, user_id = request
        if force:
            service.ensure_started(url, user_id, force=True)
        record = await service.wait_until_ready(
            url,
            user_id,
            timeout=config.INLINE_PREPARE_TIMEOUT,
        )
        if record:
            await edit_status_ready(bot, inline_message_id, url)
        else:
            await edit_status_error(bot, inline_message_id, token, url)

    async def handle_inline_query(inline_query: InlineQuery) -> None:
        raw_query = (inline_query.query or "").strip()
        if not raw_query:
            result = article(
                result_id="inline_help",
                title="Вставьте ссылку на видео",
                description="YouTube, TikTok, Instagram и другие источники",
                text=ui_manager.format_panel(
                    "ReSave inline",
                    ["Введите ссылку после @ReSafeBot."],
                    icon="⚡",
                ),
            )
            await answer_query(
                inline_query,
                results=[result],
                cache_time=5,
                button=open_bot_results_button(),
            )
            return

        validator = get_url_validator()
        extracted = validator.extract_url(raw_query)
        valid, corrected, _ = validator.validate(extracted or raw_query)
        if not valid:
            result = article(
                result_id="inline_invalid",
                title="Это не ссылка на видео",
                description="Вставьте полный URL после @ReSafeBot",
                text="Вставьте корректную ссылку после @ReSafeBot.",
            )
            await answer_query(
                inline_query,
                results=[result],
                cache_time=2,
                button=open_bot_results_button(),
            )
            return

        url = corrected or extracted or raw_query
        user_id = inline_query.from_user.id
        if is_tiktok_photo_url(url):
            result = article(
                result_id="inline_tiktok_photo",
                title="TikTok photo · открыть в ReSave",
                description="Фото-посты скачиваются в личном чате с ботом",
                text=f"Откройте @ReSafeBot и отправьте ссылку:\n{url}",
            )
            await answer_query(
                inline_query,
                results=[result],
                cache_time=30,
                button=open_bot_results_button(url),
            )
            return

        record = service.get_ready(url)
        if record:
            limit_error = build_duration_limit_error(user_id, record.duration)
            limit_error = limit_error or (
                build_file_size_limit_error(user_id, record.file_size)
                if record.file_size
                else None
            )
            if limit_error:
                result = article(
                    result_id="inline_limit",
                    title="Видео превышает лимит",
                    description="Откройте ReSave для подробностей",
                    text=limit_error,
                )
                await answer_query(
                    inline_query,
                    results=[result],
                    cache_time=30,
                    button=open_bot_results_button(url),
                )
                return

            cached_result = build_cached_video_result(
                record,
                reply_markup=ready_markup(record.url),
            )
            try:
                await answer_query(inline_query, results=[cached_result], cache_time=300)
                return
            except TelegramBadRequest as exc:
                if not _invalid_cached_file_error(exc):
                    raise
                logger.warning("Invalid inline file_id, rebuilding %s: %s", url, exc)
                service.invalidate(url)

        status = service.get_state(url).status
        if status == "missing":
            status = "preparing"
            schedule_preparation(url, user_id)
        result = preparing_result(url, user_id, status)
        await answer_query(inline_query, results=[result], cache_time=0)

    async def inline_query_handler(inline_query: InlineQuery) -> None:
        try:
            await handle_inline_query(inline_query)
        except Exception as exc:
            logger.exception("Inline query failed: %s", exc)
            fallback = article(
                result_id="inline_failure",
                title="Открыть ReSave",
                description="Inline временно недоступен",
                text="Откройте @ReSafeBot и отправьте ссылку напрямую.",
            )
            try:
                await answer_query(
                    inline_query,
                    results=[fallback],
                    cache_time=0,
                    button=open_bot_results_button(),
                )
            except TelegramBadRequest as answer_error:
                if not _expired_query_error(answer_error):
                    logger.warning("Inline fallback answer failed: %s", answer_error)

    async def chosen_result_handler(chosen: ChosenInlineResult, bot: Bot) -> None:
        if not chosen.result_id.startswith(PREPARING_RESULT_PREFIX):
            return
        if not chosen.inline_message_id:
            return
        token = chosen.result_id.removeprefix(PREPARING_RESULT_PREFIX)
        asyncio.create_task(
            wait_and_update(
                bot,
                token=token,
                inline_message_id=chosen.inline_message_id,
            )
        )

    async def check_callback_handler(call: CallbackQuery, bot: Bot) -> None:
        if not call.data or not call.inline_message_id:
            await call.answer("Inline-сообщение недоступно.", show_alert=True)
            return
        token = call.data.removeprefix(CHECK_CALLBACK_PREFIX)
        request = store.resolve_request(token, ttl=config.INLINE_REQUEST_TTL)
        if not request:
            await call.answer("Запрос устарел. Повторите inline-поиск.", show_alert=True)
            return
        url, user_id = request
        record = service.get_ready(url)
        if record:
            await call.answer("Видео готово.")
            await edit_status_ready(bot, call.inline_message_id, url)
            return

        force = service.get_state(url).status == "error"
        service.ensure_started(url, user_id, force=force)
        await call.answer("Готовлю видео. Статус обновится автоматически.")
        asyncio.create_task(
            wait_and_update(
                bot,
                token=token,
                inline_message_id=call.inline_message_id,
                force=force,
            )
        )

    router.inline_query.register(inline_query_handler)
    router.chosen_inline_result.register(chosen_result_handler)
    router.callback_query.register(
        check_callback_handler,
        lambda call: bool(call.data and call.data.startswith(CHECK_CALLBACK_PREFIX)),
    )
    return service
