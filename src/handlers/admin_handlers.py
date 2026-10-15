from __future__ import annotations

import asyncio
import contextlib
import logging
import re
import secrets
import time
from html import escape

from aiogram import Bot, F, Router
from aiogram.exceptions import TelegramAPIError
from aiogram.filters import Command, StateFilter
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import CallbackQuery, InlineKeyboardMarkup, Message

from config import Settings

from ..core.broadcast import BroadcastEngine, BroadcastStore, Campaign, LinkButton, Status
from ..core.telegram_gateway import TelegramGateway
from ..core.user_stats import UserStatsManager
from ..utils.cards import card, metrics_table
from ..utils.presentation import MessageContent
from ..utils.theme import button, emoji
from .broadcast_views import (
    buttons_prompt,
    compose_card,
    draft_card,
    draft_controls,
    history_card,
    history_controls,
    next_effect,
    recall_confirm,
    report_card,
    report_controls,
)

logger = logging.getLogger(__name__)
ALBUM_WAIT_SECONDS = 1.2
BUTTON_LINE = re.compile(r"^(?P<text>.+?)\s+[-—–]\s+(?P<url>(?:https?|tg)://\S+)$")
SENDABLE = (
    "text",
    "photo",
    "video",
    "audio",
    "document",
    "animation",
    "voice",
    "video_note",
    "sticker",
    "rich_message",
)


class AdminStates(StatesGroup):
    compose = State()
    buttons = State()
    reset_confirmation = State()


def admin_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                button("Статистика", icon="chart", style="primary", callback_data="admin:stats"),
                button("Пользователи", icon="eyes", style="primary", callback_data="admin:users"),
            ],
            [
                button(
                    "Рассылка", icon="megaphone", style="success", callback_data="admin:broadcast"
                )
            ],
            [button("История рассылок", icon="calendar", callback_data="admin:history")],
            [
                button(
                    "Сбросить счётчики", icon="trash", style="danger", callback_data="admin:clear"
                )
            ],
        ]
    )


def back_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[[button("Назад", icon="arrow", callback_data="admin:back")]]
    )


def parse_buttons(text: str) -> list[list[LinkButton]]:
    rows: list[list[LinkButton]] = []
    for line in text.strip().splitlines():
        if not line.strip():
            continue
        row = []
        for part in line.split("|"):
            match = BUTTON_LINE.match(part.strip())
            if not match:
                raise ValueError(f"Не понял строку: {part.strip()[:60]}")
            row.append(LinkButton(match["text"].strip()[:64], match["url"]))
        if len(row) > 4:
            raise ValueError("В одном ряду — не больше 4 кнопок")
        rows.append(row)
    if len(rows) > 8:
        raise ValueError("Не больше 8 рядов кнопок")
    return rows


class AdminHandlers:
    def __init__(
        self,
        settings: Settings,
        stats: UserStatsManager,
        telegram: TelegramGateway,
        store: BroadcastStore,
    ):
        self.settings = settings
        self.stats = stats
        self.telegram = telegram
        self.store = store
        self.engine = BroadcastEngine(telegram.bot, store, report=self.report, blocked=self.blocked)
        self._albums: dict[str, list[Message]] = {}
        self._album_tasks: set[asyncio.Task] = set()

    def router(self) -> Router:
        router = Router(name="admin")
        router.message.filter(F.from_user.id.in_(self.settings.admin_ids), F.chat.type == "private")
        router.callback_query.filter(
            F.from_user.id.in_(self.settings.admin_ids),
            F.message.chat.type == "private",
        )
        router.message.register(self.open, Command("admin"))
        router.message.register(self.begin, Command("broadcast"))
        router.message.register(self.global_stats, Command("stats_global"))
        router.message.register(
            self.cancel,
            Command("cancel"),
            StateFilter(AdminStates.compose, AdminStates.buttons, AdminStates.reset_confirmation),
        )
        router.message.register(self.receive_buttons, StateFilter(AdminStates.buttons))
        router.message.register(
            self.receive, StateFilter(AdminStates.compose), ~F.text.startswith("/")
        )
        router.callback_query.register(self.navigate, F.data.startswith("admin:"))
        router.callback_query.register(self.broadcast, F.data.startswith("bc:"))
        return router

    async def stats_text(self) -> MessageContent:
        audience = await asyncio.to_thread(self.stats.audience_summary)
        items = await asyncio.to_thread(self.stats.get_all_stats)
        values = list(items.values())
        downloads = sum(item.downloads_count for item in values)
        failed = sum(item.failed_downloads for item in values)
        volume = sum(item.total_size_mb for item in values)
        success = downloads / (downloads + failed) if downloads + failed else 0
        people = [
            ("Всего", str(audience.total)),
            ("Активны за сутки", str(audience.active_day)),
            ("Активны за неделю", str(audience.active_week)),
            ("Новых за неделю", str(audience.new_week)),
            ("Заблокировали бота", str(audience.blocked)),
        ]
        files = [
            ("Скачано", str(downloads)),
            ("Объём", f"{volume / 1024:.2f} ГБ" if volume >= 1024 else f"{volume:.1f} МБ"),
            ("Успешных", f"{success:.0%}" if downloads + failed else "—"),
        ]
        body = f"<h3>{emoji('eyes')} Аудитория</h3>" + metrics_table(people)
        body += f"<h3>{emoji('download')} Загрузки</h3>" + metrics_table(files)
        fallback = [f"{label}: {value}" for label, value in people + files]
        return card("Статистика бота", body, fallback, icon="chart")

    async def users_text(self) -> MessageContent:
        items = await asyncio.to_thread(self.stats.get_all_stats)
        top = [(user_id, value) for user_id, value in items.items() if value.downloads_count][:15]
        if not top:
            return card(
                "Топ пользователей",
                "<blockquote>Пока никто ничего не скачал.</blockquote>",
                ["Пока никто ничего не скачал."],
                icon="trophy",
            )
        medals = ["🥇", "🥈", "🥉"]
        rows = "".join(
            f"<tr><td>{medals[index] if index < 3 else index + 1}</td>"
            f'<td><a href="tg://user?id={user_id}">{user_id}</a></td>'
            f"<td><b>{value.downloads_count}</b></td><td>{value.total_size_mb:.0f} МБ</td></tr>"
            for index, (user_id, value) in enumerate(top)
        )
        body = (
            "<table striped compact><tr><th></th><th>Пользователь</th><th>Файлов</th><th>Объём</th></tr>"
            f"{rows}</table>"
        )
        fallback = [f"{user_id}: {value.downloads_count}" for user_id, value in top]
        return card("Топ пользователей", body, fallback, icon="trophy")

    async def open(self, message: Message, state: FSMContext) -> None:
        await state.clear()
        await self.telegram.send_message(
            message.chat.id, await self.home_text(), reply_markup=admin_keyboard()
        )

    async def home_text(self) -> MessageContent:
        audience = await asyncio.to_thread(self.stats.audience_summary)
        active = self.engine.active_id
        body = metrics_table(
            [
                ("Пользователей", str(audience.total)),
                ("Активны за сутки", str(audience.active_day)),
                ("Получат рассылку", str(audience.reachable)),
            ]
        )
        if active:
            body += f"<blockquote>{emoji('rocket')} Сейчас идёт рассылка #{active}.</blockquote>"
        return card(
            "Управление ботом",
            body,
            [f"Пользователей: {audience.total}", f"Получат рассылку: {audience.reachable}"],
            icon="crown",
        )

    async def global_stats(self, message: Message) -> None:
        await self.telegram.send_message(
            message.chat.id, await self.stats_text(), reply_markup=back_keyboard()
        )

    async def begin(self, message: Message, state: FSMContext) -> None:
        active = self.engine.active_id
        if active:
            campaign = await asyncio.to_thread(self.store.get, active)
            await self.telegram.send_message(
                message.chat.id, report_card(campaign), reply_markup=report_controls(campaign)
            )
            return
        await state.clear()
        await state.set_state(AdminStates.compose)
        audience = len(await asyncio.to_thread(self.stats.audience))
        await self.telegram.send_message(
            message.chat.id, compose_card(audience), reply_markup=back_keyboard()
        )

    async def cancel(self, message: Message, state: FSMContext) -> None:
        current = await state.get_state()
        data = await state.get_data()
        await state.clear()
        if current == AdminStates.buttons.state and data.get("campaign"):
            await self.telegram.reply(message, "Кнопки не изменены.")
            return
        await self.telegram.send_message(
            message.chat.id,
            card(
                "Отменено",
                "<p>Никому ничего не отправлено.</p>",
                ["Никому ничего не отправлено."],
                icon="check",
            ),
            reply_markup=admin_keyboard(),
        )

    async def receive(self, message: Message, state: FSMContext, bot: Bot) -> None:
        if message.media_group_id:
            group = self._albums.setdefault(message.media_group_id, [])
            group.append(message)
            if len(group) == 1:
                task = asyncio.create_task(self._finish_album(message.media_group_id, state))
                self._album_tasks.add(task)
                task.add_done_callback(self._album_tasks.discard)
            return
        if not any(getattr(message, kind, None) for kind in SENDABLE):
            await self.telegram.reply(
                message, "Такой тип сообщения нельзя разослать. Пришлите текст, медиа или альбом."
            )
            return
        await self._create_draft(message.chat.id, message.from_user.id, [message.message_id], state)

    async def _finish_album(self, group_id: str, state: FSMContext) -> None:
        await asyncio.sleep(ALBUM_WAIT_SECONDS)
        messages = sorted(self._albums.pop(group_id, []), key=lambda item: item.message_id)
        if not messages:
            return
        first = messages[0]
        try:
            await self._create_draft(
                first.chat.id,
                first.from_user.id,
                [item.message_id for item in messages[:10]],
                state,
            )
        except Exception:
            logger.exception("Cannot prepare album broadcast")

    async def _create_draft(
        self, chat_id: int, owner_id: int, message_ids: list[int], state: FSMContext
    ) -> None:
        if await state.get_state() != AdminStates.compose.state:
            return
        await state.clear()
        await asyncio.to_thread(self.store.discard_drafts, owner_id)
        campaign = await asyncio.to_thread(self.store.create, owner_id, chat_id, message_ids)
        if not await self.render_draft(campaign):
            await state.set_state(AdminStates.compose)

    async def render_draft(self, campaign: Campaign) -> bool:
        await self._drop_preview(campaign)
        bot = self.telegram.bot
        try:
            if campaign.is_album:
                result = await bot.copy_messages(
                    campaign.chat_id,
                    from_chat_id=campaign.chat_id,
                    message_ids=campaign.message_ids,
                    request_timeout=30,
                )
                campaign.preview_ids = [item.message_id for item in result]
            else:
                result = await bot.copy_message(
                    campaign.chat_id,
                    from_chat_id=campaign.chat_id,
                    message_id=campaign.message_ids[0],
                    reply_markup=campaign.markup,
                    message_effect_id=campaign.effect,
                    request_timeout=30,
                )
                campaign.preview_ids = [result.message_id]
        except (TelegramAPIError, TimeoutError) as exc:
            if campaign.effect and "effect" in str(exc).lower():
                campaign.effect = None
                return await self.render_draft(campaign)
            await self.telegram.send_message(
                campaign.chat_id,
                card(
                    "Не получилось показать пост",
                    f"<p>{escape(str(exc)[:300])}</p><p>Пришлите пост ещё раз.</p>",
                    ["Не получилось скопировать пост. Пришлите его ещё раз."],
                    icon="warning",
                ),
            )
            return False
        audience = len(await asyncio.to_thread(self.stats.audience))
        control = await self.telegram.send_message(
            campaign.chat_id,
            draft_card(campaign, audience),
            reply_markup=draft_controls(campaign, audience),
        )
        campaign.control_id = control.message_id
        await asyncio.to_thread(self.store.save, campaign)
        return True

    async def _drop_preview(self, campaign: Campaign) -> None:
        ids = [*campaign.preview_ids, *([campaign.control_id] if campaign.control_id else [])]
        if ids:
            with contextlib.suppress(TelegramAPIError, TimeoutError):
                await self.telegram.bot.delete_messages(campaign.chat_id, ids)
        campaign.preview_ids = []
        campaign.control_id = None

    async def receive_buttons(self, message: Message, state: FSMContext) -> None:
        data = await state.get_data()
        try:
            campaign = await asyncio.to_thread(self.store.get, int(data["campaign"]))
        except (KeyError, ValueError):
            await state.clear()
            return
        text = (message.text or "").strip()
        try:
            campaign.buttons = [] if text == "/skip" else parse_buttons(text)
        except ValueError as exc:
            await self.telegram.reply(message, f"{exc}. Формат: Текст - https://ссылка")
            return
        await state.clear()
        await self.render_draft(campaign)

    async def navigate(self, call: CallbackQuery, state: FSMContext) -> None:
        if not isinstance(call.message, Message):
            await self.telegram.answer_callback(call, "Сообщение недоступно.")
            return
        action = (call.data or "").split(":")[1]
        await self.telegram.answer_callback(call)
        if action == "broadcast":
            await self.begin(call.message, state)
            return
        if action == "clear":
            await self.confirm_reset(call, state)
            return
        if action == "clear-confirm":
            await self.reset(call, state)
            return
        await state.clear()
        markup = back_keyboard()
        if action == "stats":
            text = await self.stats_text()
        elif action == "users":
            text = await self.users_text()
        elif action == "history":
            campaigns = await asyncio.to_thread(self.store.history)
            text, markup = history_card(campaigns), history_controls(campaigns)
        else:
            text, markup = await self.home_text(), admin_keyboard()
        await self.telegram.edit_status(
            call.message.chat.id, call.message.message_id, text, reply_markup=markup
        )

    async def confirm_reset(self, call: CallbackQuery, state: FSMContext) -> None:
        token = secrets.token_urlsafe(6)
        await state.set_data({"reset": token, "expires": time.monotonic() + 600})
        await state.set_state(AdminStates.reset_confirmation)
        await self.telegram.edit_status(
            call.message.chat.id,
            call.message.message_id,
            card(
                "Сбросить счётчики?",
                "<p>Обнулю загрузки и ошибки. Пользователи и рассылки останутся.</p>",
                ["Обнулю загрузки и ошибки. Пользователи останутся."],
                icon="warning",
            ),
            reply_markup=InlineKeyboardMarkup(
                inline_keyboard=[
                    [
                        button(
                            "Сбросить",
                            icon="trash",
                            style="danger",
                            callback_data=f"admin:clear-confirm:{token}",
                        ),
                        button("Отмена", callback_data="admin:back"),
                    ]
                ]
            ),
        )

    async def reset(self, call: CallbackQuery, state: FSMContext) -> None:
        data = await state.get_data()
        parts = (call.data or "").split(":")
        valid = (
            await state.get_state() == AdminStates.reset_confirmation.state
            and len(parts) == 3
            and parts[2] == data.get("reset")
            and data.get("expires", 0) > time.monotonic()
        )
        await state.clear()
        if valid:
            await asyncio.to_thread(self.stats.clear_all_stats)
        title = "Счётчики сброшены" if valid else "Подтверждение устарело"
        await self.telegram.edit_status(
            call.message.chat.id,
            call.message.message_id,
            card(title, "", [], icon="check" if valid else "warning"),
            reply_markup=admin_keyboard(),
        )

    async def broadcast(self, call: CallbackQuery, state: FSMContext) -> None:
        if not isinstance(call.message, Message):
            return
        parts = (call.data or "").split(":")
        try:
            action, campaign = parts[1], await asyncio.to_thread(self.store.get, int(parts[2]))
        except (IndexError, ValueError, KeyError):
            await self.telegram.answer_callback(call, "Кнопка устарела. Откройте /broadcast.")
            return
        if campaign.owner_id != call.from_user.id and action not in {"report", "stop"}:
            await self.telegram.answer_callback(call, "Это рассылка другого администратора.")
            return
        handler = {
            "send": self.on_send,
            "buttons": self.on_buttons,
            "effect": self.on_effect,
            "silent": self.on_silent,
            "replace": self.on_replace,
            "cancel": self.on_cancel,
            "stop": self.on_stop,
            "resume": self.on_resume,
            "finish": self.on_finish,
            "recall": self.on_recall,
            "recall-yes": self.on_recall_confirmed,
            "report": self.on_report,
        }.get(action)
        if handler is None:
            await self.telegram.answer_callback(call, "Действие недоступно.")
            return
        await handler(call, state, campaign)

    async def _draft_only(self, call: CallbackQuery, campaign: Campaign) -> bool:
        if campaign.status != Status.DRAFT or campaign.control_id != call.message.message_id:
            await self.telegram.answer_callback(call, "Этот черновик устарел. Откройте /broadcast.")
            return False
        return True

    async def on_send(self, call: CallbackQuery, state: FSMContext, campaign: Campaign) -> None:
        if not await self._draft_only(call, campaign):
            return
        if self.engine.active_id:
            await self.telegram.answer_callback(call, "Уже идёт другая рассылка.")
            return
        recipients = await asyncio.to_thread(self.stats.audience)
        if not recipients:
            await self.telegram.answer_callback(call, "Пока некому отправлять.")
            return
        await state.clear()
        await asyncio.to_thread(self.store.add_recipients, campaign.id, recipients)
        campaign.counts = await asyncio.to_thread(self.store.counts, campaign.id)
        self.engine.start(campaign)
        await self.telegram.answer_callback(call, "Поехали 🚀")
        await self.report(campaign)

    async def on_buttons(self, call: CallbackQuery, state: FSMContext, campaign: Campaign) -> None:
        if not await self._draft_only(call, campaign):
            return
        await self.telegram.answer_callback(call)
        await state.set_state(AdminStates.buttons)
        await state.set_data({"campaign": campaign.id})
        await self.telegram.send_message(call.message.chat.id, buttons_prompt())

    async def on_effect(self, call: CallbackQuery, state: FSMContext, campaign: Campaign) -> None:
        if not await self._draft_only(call, campaign):
            return
        await self.telegram.answer_callback(call)
        campaign.effect = next_effect(campaign.effect)
        await self.render_draft(campaign)

    async def on_silent(self, call: CallbackQuery, state: FSMContext, campaign: Campaign) -> None:
        if not await self._draft_only(call, campaign):
            return
        campaign.silent = not campaign.silent
        await asyncio.to_thread(self.store.save, campaign)
        await self.telegram.answer_callback(
            call, "Придёт без звука" if campaign.silent else "Придёт со звуком"
        )
        audience = len(await asyncio.to_thread(self.stats.audience))
        await self.telegram.edit_status(
            call.message.chat.id,
            call.message.message_id,
            draft_card(campaign, audience),
            reply_markup=draft_controls(campaign, audience),
        )

    async def on_replace(self, call: CallbackQuery, state: FSMContext, campaign: Campaign) -> None:
        if not await self._draft_only(call, campaign):
            return
        await self.telegram.answer_callback(call)
        await self._drop_preview(campaign)
        await asyncio.to_thread(self.store.discard_drafts, campaign.owner_id)
        await self.begin(call.message, state)

    async def on_cancel(self, call: CallbackQuery, state: FSMContext, campaign: Campaign) -> None:
        if not await self._draft_only(call, campaign):
            return
        await self.telegram.answer_callback(call, "Черновик удалён")
        await state.clear()
        await self._drop_preview(campaign)
        await asyncio.to_thread(self.store.discard_drafts, campaign.owner_id)
        await self.telegram.send_message(
            call.message.chat.id, await self.home_text(), reply_markup=admin_keyboard()
        )

    async def on_stop(self, call: CallbackQuery, state: FSMContext, campaign: Campaign) -> None:
        stopped = self.engine.stop(campaign.id)
        await self.telegram.answer_callback(
            call, "Останавливаю…" if stopped else "Рассылка уже не идёт."
        )

    async def on_resume(self, call: CallbackQuery, state: FSMContext, campaign: Campaign) -> None:
        if campaign.status != Status.PAUSED:
            await self.telegram.answer_callback(call, "Рассылка уже не на паузе.")
            return
        campaign.control_id = call.message.message_id
        if not self.engine.start(campaign):
            await self.telegram.answer_callback(call, "Уже идёт другая рассылка.")
            return
        await self.telegram.answer_callback(call, "Продолжаю")

    async def on_finish(self, call: CallbackQuery, state: FSMContext, campaign: Campaign) -> None:
        if campaign.status == Status.PAUSED:
            campaign.status = Status.STOPPED
            campaign.finished_at = time.time()
            await asyncio.to_thread(self.store.save, campaign)
        await self.telegram.answer_callback(call)
        await self.on_report(call, state, campaign, answered=True)

    async def on_recall(self, call: CallbackQuery, state: FSMContext, campaign: Campaign) -> None:
        await self.telegram.answer_callback(call)
        content, markup = recall_confirm(campaign)
        await self.telegram.edit_status(
            call.message.chat.id, call.message.message_id, content, reply_markup=markup
        )

    async def on_recall_confirmed(
        self, call: CallbackQuery, state: FSMContext, campaign: Campaign
    ) -> None:
        campaign.control_id = call.message.message_id
        if not self.engine.recall(campaign):
            await self.telegram.answer_callback(call, "Дождитесь окончания текущей рассылки.")
            return
        await self.telegram.answer_callback(call, "Отзываю")

    async def on_report(
        self, call: CallbackQuery, state: FSMContext, campaign: Campaign, *, answered: bool = False
    ) -> None:
        if not answered:
            await self.telegram.answer_callback(call)
        await self.telegram.edit_status(
            call.message.chat.id,
            call.message.message_id,
            report_card(campaign),
            reply_markup=report_controls(campaign),
        )

    async def report(self, campaign: Campaign) -> None:
        if campaign.control_id is None:
            return
        await self.telegram.edit_status(
            campaign.chat_id,
            campaign.control_id,
            report_card(campaign),
            reply_markup=report_controls(campaign),
        )

    async def blocked(self, user_ids: list[int]) -> None:
        await asyncio.to_thread(self.stats.mark_blocked, user_ids)

    async def notify_interrupted(self) -> None:
        for campaign in await asyncio.to_thread(self.engine.interrupted):
            try:
                message = await self.telegram.send_message(
                    campaign.chat_id,
                    report_card(campaign),
                    reply_markup=report_controls(campaign),
                )
            except (TelegramAPIError, TimeoutError, RuntimeError) as exc:
                logger.warning("Cannot notify about broadcast %s: %s", campaign.id, exc)
                continue
            campaign.control_id = message.message_id
            await asyncio.to_thread(self.store.save, campaign)

    async def shutdown(self) -> None:
        await self.engine.shutdown()
