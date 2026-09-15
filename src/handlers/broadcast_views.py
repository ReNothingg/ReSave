from __future__ import annotations

import time
from html import escape

from aiogram.types import InlineKeyboardMarkup

from ..core.broadcast import Campaign
from ..utils.presentation import MessageContent, format_duration, progress_bar
from ..utils.theme import button, emoji, footer


def notice(title: str, lines: list[str]) -> MessageContent:
    html = f"{emoji('subtitle')} <b>{escape(title)}</b>\n\n"
    html += "\n".join(escape(line) for line in lines)
    html += f"\n\n{footer()}"
    rich = f"<h3>{emoji('subtitle')} {escape(title)}</h3>"
    rich += "<p>" + "<br>".join(escape(line) for line in lines) + "</p>"
    rich += f"<footer>{footer()}</footer>"
    return MessageContent(html, rich)


def draft_controls(token: str, count: int) -> InlineKeyboardMarkup:
    rows = []
    if count:
        rows.append(
            [
                button(
                    f"Отправить · {count}",
                    icon="done",
                    style="success",
                    callback_data=f"broadcast:confirm:{token}",
                )
            ]
        )
    rows.append(
        [
            button("Изменить пост", icon="subtitle", callback_data=f"broadcast:edit:{token}"),
            button("Отмена", callback_data=f"broadcast:cancel:{token}"),
        ]
    )
    return InlineKeyboardMarkup(inline_keyboard=rows)


def report_card(campaign: Campaign) -> MessageContent:
    if campaign.finished:
        title = "Рассылка остановлена" if campaign.remaining else "Рассылка завершена"
    else:
        title = "Рассылка · отправка"
    lines = [
        f"Отправлено: {campaign.sent} из {len(campaign.recipients)}",
        f"Осталось: {campaign.remaining}",
    ]
    for label, value in (
        ("Нет доступа к чату", campaign.unreachable),
        ("Не отправлено", campaign.failed),
        ("Без подтверждения Telegram", campaign.uncertain),
    ):
        if value:
            lines.append(f"{label}: {value}")
    if campaign.pause_seconds:
        lines.append(f"Пауза Telegram: {format_duration(campaign.pause_seconds)}")
    if campaign.finished:
        lines.append(f"Время: {format_duration(time.monotonic() - campaign.started_at)}")
    else:
        lines.append(progress_bar(campaign.processed / max(1, len(campaign.recipients))))
    if campaign.uncertain:
        lines.append("Нет ответа Telegram: сообщение могло дойти. Повторно не отправлял.")
    if campaign.error:
        title = "Рассылка прервана"
        lines.append(campaign.error)
    return notice(title, lines)


def report_controls(campaign: Campaign) -> InlineKeyboardMarkup:
    if campaign.finished:
        return InlineKeyboardMarkup(
            inline_keyboard=[
                [
                    button("Новая рассылка", icon="subtitle", callback_data="admin:broadcast"),
                    button("В админку", callback_data="admin:back"),
                ]
            ]
        )
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                button(
                    "Останавливаю…" if campaign.stop.is_set() else "Остановить",
                    style="danger",
                    callback_data=f"broadcast:stop:{campaign.token}",
                ),
            ]
        ]
    )
