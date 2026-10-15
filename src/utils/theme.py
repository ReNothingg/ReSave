from __future__ import annotations

import re
from dataclasses import dataclass

from aiogram.types import DisabledButton, InlineKeyboardButton, InlineKeyboardMarkup

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
    "fire": Emoji("🔥", "5312241539987020022"),
    "bolt": Emoji("⚡", "5312016608254762256"),
    "party": Emoji("🎉", "5310228579009699834"),
    "trophy": Emoji("🏆", "5312315739842026755"),
    "crown": Emoji("👑", "5357107601584693888"),
    "megaphone": Emoji("📣", "5309984423003823246"),
    "chat": Emoji("💬", "5417915203100613993"),
    "search": Emoji("🔎", "5309965701241379366"),
    "calendar": Emoji("📆", "5433614043006903194"),
    "star": Emoji("⭐", "5235579393115438657"),
    "robot": Emoji("🤖", "5309832892262654231"),
    "idea": Emoji("💡", "5312536423851630001"),
    "eyes": Emoji("👀", "5357121491508928442"),
    "finish": Emoji("🏁", "5408906741125490282"),
    "phone": Emoji("📱", "5409357944619802453"),
    "download": Emoji("⬇️", "5406745015365943482"),
    "link": Emoji("🔗", "5271604874419647061"),
    "settings": Emoji("⚙️", "5341715473882955310"),
    "trash": Emoji("🗑", "5445267414562389170"),
    "stop": Emoji("⛔", "5260293700088511294"),
    "cross": Emoji("❌", "5210952531676504517"),
    "warning": Emoji("⚠️", "5447644880824181073"),
    "bell": Emoji("🔔", "5458603043203327669"),
    "refresh": Emoji("🔄", "5375338737028841420"),
    "shield": Emoji("🛡", "5251203410396458957"),
    "pause": Emoji("⏸", "5359543311897998264"),
    "play": Emoji("▶️", "5264919878082509254"),
    "hourglass": Emoji("⌛", "5386367538735104399"),
    "chart": Emoji("📊", "5231200819986047254"),
    "blocked": Emoji("🚫", "5240241223632954241"),
    "check": Emoji("✔️", "5206607081334906820"),
    "pin": Emoji("📌", "5397782960512444700"),
    "globe": Emoji("🌐", "5447410659077661506"),
    "info": Emoji("ℹ️", "5334544901428229844"),
    "sparkles": Emoji("✨", "5325547803936572038"),
    "lock": Emoji("🔒", "5296369303661067030"),
    "new": Emoji("🆕", "5382357040008021292"),
    "siren": Emoji("🚨", "5395695537687123235"),
    "arrow": Emoji("➡️", "5416117059207572332"),
    "rocket": Emoji("🚀", "5445284980978621387"),
    "gift": Emoji("🎁", "5199749070830197566"),
    "timer": Emoji("⏳", "5451732530048802485"),
    "picture": Emoji("🖼", "5375074927252621134"),
    "camera": Emoji("📹", "5375309569905938163"),
    "handshake": Emoji("🤝", "5357080225463149588"),
    "wave": Emoji("👋", "5472055112702629499"),
    "cool": Emoji("😎", "5373141891321699086"),
    "upload": Emoji("📤", "5433614747381538714"),
    "inbox": Emoji("📥", "5433811242135331842"),
    "hundred": Emoji("💯", "5188208446461188962"),
}

EFFECTS = {
    "fire": "5104841245755180586",
    "like": "5107584321108051014",
    "heart": "5159385139981059251",
    "party": "5046509860389126442",
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
    copy_text: str | None = None,
    disabled: bool = False,
) -> InlineKeyboardButton:
    params: dict = {
        "text": text,
        "style": style,
        "icon_custom_emoji_id": EMOJIS[icon].custom_id if icon else None,
    }
    if disabled:
        params["disabled"] = DisabledButton()
    elif copy_text is not None:
        params["copy_text"] = {"text": copy_text}
    elif url is not None:
        params["url"] = url
    else:
        params["callback_data"] = callback_data
    return InlineKeyboardButton(**params)


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
