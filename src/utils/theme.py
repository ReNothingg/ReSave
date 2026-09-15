from __future__ import annotations

import re
from dataclasses import dataclass

from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup

DEVELOPER_URL = "https://t.me/daich"
GITHUB_URL = "https://github.com/ReNothingg/ReSave"


@dataclass(frozen=True, slots=True)
class Emoji:
    symbol: str
    custom_id: str

    def html(self) -> str:
        return f'<tg-emoji emoji-id="{self.custom_id}">{self.symbol}</tg-emoji>'


EMOJIS = {
    "brand": Emoji("💎", "5309958691854754293"),
    "video": Emoji("🎬", "5368653135101310687"),
    "audio": Emoji("🎵", "5310045076531978942"),
    "playlist": Emoji("🎶", "5377317729109811382"),
    "downloads": Emoji("📁", "5357315181649076022"),
    "stats": Emoji("📈", "5350305691942788490"),
    "help": Emoji("❓", "5377316857231450742"),
    "subtitle": Emoji("📝", "5373251851074415873"),
    "developer": Emoji("💻", "5350554349074391003"),
    "done": Emoji("✅", "5237699328843200968"),
}


def emoji(name: str) -> str:
    return EMOJIS[name].html()


def button(
    text: str,
    *,
    icon: str | None = None,
    style: str | None = None,
    callback_data: str | None = None,
    url: str | None = None,
) -> InlineKeyboardButton:
    return InlineKeyboardButton(
        text=text,
        callback_data=callback_data,
        url=url,
        style=style,
        icon_custom_emoji_id=EMOJIS[icon].custom_id if icon else None,
    )


def footer() -> str:
    return f'Разработчик: <a href="{DEVELOPER_URL}">@daich</a> · <a href="{GITHUB_URL}">GitHub</a>'


def without_custom_emoji(html: str) -> str:
    return re.sub(r"<tg-emoji\b[^>]*>(.*?)</tg-emoji>", r"\1", html, flags=re.DOTALL)


def plain_icons(markup: InlineKeyboardMarkup | None) -> InlineKeyboardMarkup | None:
    if markup is None:
        return None
    result = markup.model_copy(deep=True)
    symbols = {item.custom_id: item.symbol for item in EMOJIS.values()}
    for row in result.inline_keyboard:
        for item in row:
            if item.icon_custom_emoji_id:
                symbol = symbols.get(item.icon_custom_emoji_id, "")
                item.text = f"{symbol} {item.text}".strip()
                item.icon_custom_emoji_id = None
    return result
