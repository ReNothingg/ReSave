import logging
from typing import Optional

import config

logger = logging.getLogger(__name__)


def notify_admins(bot, text: Optional[str]) -> None:
    if bot is None or not text:
        return

    for admin_id in config.ADMIN_IDS:
        try:
            bot.send_message(admin_id, text)
        except Exception as exc:
            logger.error(f"Failed to notify admin {admin_id}: {exc}")
