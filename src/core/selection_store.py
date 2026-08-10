from __future__ import annotations

import secrets
import time

from .models import MediaSelection


class SelectionStore:
    def __init__(self, ttl_seconds: int = 900, max_entries: int = 2_000):
        self.ttl_seconds = ttl_seconds
        self.max_entries = max_entries
        self._items: dict[str, MediaSelection] = {}

    def put(
        self,
        *,
        user_id: int,
        chat_id: int,
        reply_to_message_id: int,
        url: str,
        info: dict,
        resolutions: list[int] | tuple[int, ...],
    ) -> MediaSelection:
        self.prune()
        if len(self._items) >= self.max_entries:
            oldest = min(self._items.values(), key=lambda item: item.expires_at)
            self._items.pop(oldest.token, None)

        token = secrets.token_urlsafe(6)
        selection = MediaSelection(
            token=token,
            user_id=user_id,
            chat_id=chat_id,
            reply_to_message_id=reply_to_message_id,
            url=url,
            info=info,
            resolutions=tuple(resolutions),
            expires_at=time.monotonic() + self.ttl_seconds,
        )
        self._items[token] = selection
        return selection

    def get(self, token: str, *, user_id: int, chat_id: int) -> MediaSelection | None:
        selection = self._items.get(token)
        if selection is None:
            return None
        if selection.expires_at <= time.monotonic():
            self._items.pop(token, None)
            return None
        if selection.user_id != user_id or selection.chat_id != chat_id:
            return None
        return selection

    def pop(self, token: str) -> None:
        self._items.pop(token, None)

    def prune(self) -> None:
        now = time.monotonic()
        expired = [token for token, item in self._items.items() if item.expires_at <= now]
        for token in expired:
            self._items.pop(token, None)
