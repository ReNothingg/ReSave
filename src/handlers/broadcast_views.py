from __future__ import annotations

import time
from html import escape

from aiogram.types import InlineKeyboardMarkup

from ..core.broadcast import Campaign, Delivery, Status
from ..utils.cards import card, metrics_table
from ..utils.presentation import MessageContent, format_duration, progress_bar, tg_time
from ..utils.theme import EFFECTS, button, emoji

EFFECT_LABELS = {None: "нет", "party": "🎉", "fire": "🔥", "heart": "❤️", "like": "👍"}
EFFECT_ORDER = [None, "party", "fire", "heart", "like"]


def effect_name(effect_id: str | None) -> str | None:
    return next((name for name, value in EFFECTS.items() if value == effect_id), None)


def next_effect(effect_id: str | None) -> str | None:
    current = EFFECT_ORDER.index(effect_name(effect_id)) if effect_name(effect_id) else 0
    name = EFFECT_ORDER[(current + 1) % len(EFFECT_ORDER)]
    return EFFECTS[name] if name else None


def eta(seconds: float) -> str:
    return format_duration(max(1, round(seconds))) if seconds else "—"


def compose_card(audience: int) -> MessageContent:
    body = (
        f"<p>Получат <b>{audience}</b> пользователей — все, кто запускал бота и не заблокировал его.</p>"
        "<ul>"
        '<li><input type="checkbox" checked>Текст с любым форматированием и премиум-эмодзи</li>'
        '<li><input type="checkbox" checked>Фото, видео, GIF, файл, голосовое или стикер</li>'
        '<li><input type="checkbox" checked>Альбом до 10 медиа</li>'
        '<li><input type="checkbox" checked>Кнопки-ссылки и эффект — на следующем шаге</li>'
        "</ul>"
        "<blockquote>Сначала покажу точную копию поста. Ничего не уйдёт без подтверждения.</blockquote>"
        "<p><code>/cancel</code> — выйти</p>"
    )
    fallback = [
        f"Получат: {audience}",
        "Пришлите текст, медиа или альбом до 10 файлов.",
        "Сначала покажу копию поста. /cancel — выйти.",
    ]
    return card("Новая рассылка", body, fallback, icon="megaphone")


def buttons_prompt() -> MessageContent:
    body = (
        "<p>Каждая строка — ряд кнопок. Кнопки в одном ряду разделяйте <code>|</code>.</p>"
        "<pre>Наш канал - https://t.me/example\n"
        "Сайт - https://example.com | Чат - https://t.me/chat</pre>"
        "<p><code>/skip</code> — убрать все кнопки, <code>/cancel</code> — вернуться без изменений</p>"
    )
    fallback = [
        "Каждая строка — ряд кнопок, в ряду через |.",
        "Наш канал - https://t.me/example",
        "/skip — убрать кнопки",
    ]
    return card("Кнопки под постом", body, fallback, icon="link")


def draft_card(campaign: Campaign, audience: int) -> MessageContent:
    buttons = sum(len(row) for row in campaign.buttons)
    kind = f"Альбом · {len(campaign.message_ids)}" if campaign.is_album else "Пост"
    rows = [
        ("Получателей", str(audience)),
        ("Займёт", f"≈ {eta(audience / 25)}"),
        ("Формат", kind),
        ("Кнопки", str(buttons) if buttons else "нет"),
        ("Эффект", EFFECT_LABELS[effect_name(campaign.effect)]),
        ("Звук", "без звука" if campaign.silent else "обычный"),
    ]
    body = "<p>Выше — <b>точная копия</b> того, что получат пользователи.</p>"
    body += metrics_table(rows)
    if campaign.is_album:
        body += "<blockquote>У альбомов Telegram не поддерживает кнопки и эффекты.</blockquote>"
    body += "<p>Список получателей зафиксируется в момент отправки.</p>"
    fallback = [f"{label}: {value}" for label, value in rows]
    return card("Пост готов", body, fallback, icon="eyes")


def draft_controls(campaign: Campaign, audience: int) -> InlineKeyboardMarkup:
    token = campaign.id
    effect = EFFECT_LABELS[effect_name(campaign.effect)]
    rows = [
        [
            button(
                f"Отправить · {audience}",
                icon="rocket",
                style="success",
                callback_data=f"bc:send:{token}",
                disabled=not audience,
            )
        ],
        [
            button(
                "Кнопки",
                icon="link",
                callback_data=f"bc:buttons:{token}",
                disabled=campaign.is_album,
            ),
            button(
                f"Эффект: {effect}",
                icon="sparkles",
                callback_data=f"bc:effect:{token}",
                disabled=campaign.is_album,
            ),
        ],
        [
            button(
                "Без звука: да" if campaign.silent else "Без звука: нет",
                icon="bell",
                callback_data=f"bc:silent:{token}",
            ),
            button("Заменить пост", icon="refresh", callback_data=f"bc:replace:{token}"),
        ],
        [
            button(
                "Удалить черновик", icon="trash", style="danger", callback_data=f"bc:cancel:{token}"
            )
        ],
    ]
    return InlineKeyboardMarkup(inline_keyboard=rows)


def _result_rows(campaign: Campaign) -> list[tuple[str, str]]:
    rows = [("Доставлено", f"{campaign.count(Delivery.SENT)} из {campaign.total}")]
    for label, delivery in (
        ("Заблокировали бота", Delivery.BLOCKED),
        ("Ошибки", Delivery.FAILED),
        ("Без ответа Telegram", Delivery.UNCERTAIN),
        ("Отозвано", Delivery.DELETED),
    ):
        if campaign.count(delivery):
            rows.append((label, str(campaign.count(delivery))))
    return rows


def report_card(campaign: Campaign) -> MessageContent:
    titles = {
        Status.RUNNING: ("Рассылка идёт", "rocket"),
        Status.PAUSED: ("Рассылка на паузе", "pause"),
        Status.DONE: ("Рассылка завершена", "party"),
        Status.STOPPED: ("Рассылка остановлена", "stop"),
        Status.FAILED: ("Рассылка прервана", "warning"),
        Status.RECALLING: ("Отзываю рассылку", "trash"),
        Status.RECALLED: ("Рассылка отозвана", "trash"),
    }
    title, icon = titles.get(campaign.status, ("Рассылка", "megaphone"))
    rows = _result_rows(campaign)
    elapsed = (campaign.finished_at or time.time()) - (campaign.started_at or time.time())
    body = ""
    fallback: list[str] = []
    if campaign.status in {Status.RUNNING, Status.RECALLING}:
        share = campaign.processed / max(1, campaign.total)
        if campaign.status == Status.RECALLING:
            share = campaign.count(Delivery.DELETED) / max(
                1, campaign.count(Delivery.SENT) + campaign.count(Delivery.DELETED)
            )
        bar = progress_bar(share, width=14)
        body += f"<p><code>{bar}</code></p>"
        fallback.append(bar)
        speed = campaign.processed / elapsed if elapsed > 1 else 0
        left = campaign.total - campaign.processed
        if campaign.status == Status.RUNNING and speed:
            rows.append(("Скорость", f"{speed:.0f} сообщ./с"))
            rows.append(("Осталось", f"≈ {eta(left / speed)}"))
    body += metrics_table(rows)
    fallback += [f"{label}: {value}" for label, value in rows]
    if campaign.pause_until > time.time():
        body += (
            "<blockquote>Telegram попросил паузу, продолжу "
            f"{tg_time(campaign.pause_until, 'r')}.</blockquote>"
        )
        fallback.append("Telegram попросил паузу.")
    if campaign.started_at and campaign.status not in {Status.RUNNING, Status.RECALLING}:
        body += f"<p>Старт: {tg_time(campaign.started_at, 'wDT')} · заняло {eta(elapsed)}</p>"
        fallback.append(f"Заняло: {eta(elapsed)}")
    if campaign.count(Delivery.UNCERTAIN):
        body += "<p><i>«Без ответа» — сообщение могло дойти, повторно не отправлял.</i></p>"
    if campaign.error:
        body += f"<blockquote>{emoji('warning')} {escape(campaign.error)}</blockquote>"
        fallback.append(campaign.error)
    if campaign.status == Status.PAUSED:
        body += "<p>Продолжу только тем, кто ещё не получил пост.</p>"
    return card(title, body, fallback, icon=icon)


def report_controls(campaign: Campaign) -> InlineKeyboardMarkup:
    token = campaign.id
    if campaign.status in {Status.RUNNING, Status.RECALLING}:
        return InlineKeyboardMarkup(
            inline_keyboard=[
                [
                    button(
                        "Остановить", icon="pause", style="danger", callback_data=f"bc:stop:{token}"
                    )
                ]
            ]
        )
    rows = []
    if campaign.status == Status.PAUSED:
        rows.append(
            [
                button(
                    "Продолжить", icon="play", style="success", callback_data=f"bc:resume:{token}"
                ),
                button("Завершить", icon="stop", callback_data=f"bc:finish:{token}"),
            ]
        )
    if campaign.count(Delivery.SENT) and campaign.status != Status.PAUSED:
        rows.append(
            [
                button(
                    "Отозвать у всех",
                    icon="trash",
                    style="danger",
                    callback_data=f"bc:recall:{token}",
                )
            ]
        )
    rows.append(
        [
            button(
                "Новая рассылка", icon="megaphone", style="primary", callback_data="admin:broadcast"
            ),
            button("Админка", icon="settings", callback_data="admin:back"),
        ]
    )
    return InlineKeyboardMarkup(inline_keyboard=rows)


def recall_confirm(campaign: Campaign) -> tuple[MessageContent, InlineKeyboardMarkup]:
    sent = campaign.count(Delivery.SENT)
    body = (
        f"<p>Удалю пост у <b>{sent}</b> пользователей.</p>"
        "<blockquote>Telegram позволяет удалять сообщения бота в течение 48 часов. Отменить удаление нельзя.</blockquote>"
    )
    content = card(
        "Отозвать рассылку?", body, [f"Удалю пост у {sent} пользователей."], icon="warning"
    )
    markup = InlineKeyboardMarkup(
        inline_keyboard=[
            [
                button(
                    "Да, отозвать",
                    icon="trash",
                    style="danger",
                    callback_data=f"bc:recall-yes:{campaign.id}",
                ),
                button("Не надо", callback_data=f"bc:report:{campaign.id}"),
            ]
        ]
    )
    return content, markup


def history_card(campaigns: list[Campaign]) -> MessageContent:
    if not campaigns:
        return card(
            "История рассылок",
            "<blockquote>Рассылок ещё не было.</blockquote>",
            ["Рассылок ещё не было."],
            icon="calendar",
        )
    labels = {
        Status.RUNNING: "идёт",
        Status.PAUSED: "пауза",
        Status.DONE: "завершена",
        Status.STOPPED: "остановлена",
        Status.FAILED: "прервана",
        Status.RECALLING: "отзывается",
        Status.RECALLED: "отозвана",
    }
    rows = "".join(
        f"<tr><td>#{item.id}</td><td>{tg_time(item.started_at or item.created_at, 'dt')}</td>"
        f"<td><b>{item.count(Delivery.SENT)}</b> / {item.total}</td><td>{labels.get(item.status, item.status)}</td></tr>"
        for item in campaigns
    )
    body = (
        "<table striped compact><tr><th>№</th><th>Когда</th><th>Доставлено</th><th>Статус</th></tr>"
        f"{rows}</table>"
    )
    fallback = [
        f"#{item.id}: {item.count(Delivery.SENT)}/{item.total} · {labels.get(item.status, item.status)}"
        for item in campaigns
    ]
    return card("История рассылок", body, fallback, icon="calendar")


def history_controls(campaigns: list[Campaign]) -> InlineKeyboardMarkup:
    rows = [
        [button(f"Отчёт #{item.id}", icon="chart", callback_data=f"bc:report:{item.id}")]
        for item in campaigns[:4]
    ]
    rows.append([button("Назад", icon="arrow", callback_data="admin:back")])
    return InlineKeyboardMarkup(inline_keyboard=rows)
