from __future__ import annotations

import asyncio
import json
import logging
import sqlite3
import threading
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from enum import StrEnum
from pathlib import Path

from aiogram import Bot
from aiogram.exceptions import (
    TelegramAPIError,
    TelegramBadRequest,
    TelegramForbiddenError,
    TelegramNetworkError,
    TelegramRetryAfter,
    TelegramServerError,
)
from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup

logger = logging.getLogger(__name__)
SEND_INTERVAL = 1 / 25
REPORT_INTERVAL = 2.5
COPY_ERRORS = (
    "message to copy not found",
    "messages to copy not found",
    "can't be copied",
    "message_id_invalid",
)
GONE_ERRORS = ("chat not found", "user is deactivated", "peer_id_invalid")


class Status(StrEnum):
    DRAFT = "draft"
    RUNNING = "running"
    PAUSED = "paused"
    DONE = "done"
    STOPPED = "stopped"
    FAILED = "failed"
    RECALLING = "recalling"
    RECALLED = "recalled"


class Delivery(StrEnum):
    PENDING = "pending"
    SENT = "sent"
    BLOCKED = "blocked"
    FAILED = "failed"
    UNCERTAIN = "uncertain"
    DELETED = "deleted"


class PostUnavailable(RuntimeError):
    pass


@dataclass(frozen=True, slots=True)
class LinkButton:
    text: str
    url: str


@dataclass(slots=True)
class Campaign:
    id: int
    owner_id: int
    chat_id: int
    message_ids: list[int]
    buttons: list[list[LinkButton]] = field(default_factory=list)
    effect: str | None = None
    silent: bool = False
    control_id: int | None = None
    preview_ids: list[int] = field(default_factory=list)
    status: Status = Status.DRAFT
    created_at: float = 0.0
    started_at: float | None = None
    finished_at: float | None = None
    error: str | None = None
    counts: dict[str, int] = field(default_factory=dict)
    pause_until: float = 0.0

    @property
    def is_album(self) -> bool:
        return len(self.message_ids) > 1

    @property
    def total(self) -> int:
        return sum(self.counts.values())

    def count(self, delivery: Delivery) -> int:
        return self.counts.get(delivery.value, 0)

    @property
    def processed(self) -> int:
        return self.total - self.count(Delivery.PENDING)

    @property
    def markup(self) -> InlineKeyboardMarkup:
        return InlineKeyboardMarkup(
            inline_keyboard=[
                [InlineKeyboardButton(text=item.text, url=item.url) for item in row]
                for row in self.buttons
            ]
        )


class BroadcastStore:
    def __init__(self, db_path: Path):
        self._lock = threading.RLock()
        self._db = sqlite3.connect(db_path, check_same_thread=False, timeout=15)
        self._db.row_factory = sqlite3.Row
        self._db.execute("PRAGMA busy_timeout=15000")
        with self._lock, self._db:
            self._db.execute(
                """
                CREATE TABLE IF NOT EXISTS broadcasts (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    owner_id INTEGER NOT NULL,
                    chat_id INTEGER NOT NULL,
                    message_ids TEXT NOT NULL,
                    buttons TEXT NOT NULL DEFAULT '[]',
                    effect TEXT,
                    silent INTEGER NOT NULL DEFAULT 0,
                    control_id INTEGER,
                    preview_ids TEXT NOT NULL DEFAULT '[]',
                    status TEXT NOT NULL,
                    created_at REAL NOT NULL,
                    started_at REAL,
                    finished_at REAL,
                    error TEXT
                )
                """
            )
            self._db.execute(
                """
                CREATE TABLE IF NOT EXISTS broadcast_recipients (
                    broadcast_id INTEGER NOT NULL,
                    user_id INTEGER NOT NULL,
                    status TEXT NOT NULL DEFAULT 'pending',
                    message_ids TEXT,
                    PRIMARY KEY (broadcast_id, user_id)
                )
                """
            )

    def close(self) -> None:
        with self._lock:
            self._db.close()

    def create(self, owner_id: int, chat_id: int, message_ids: list[int]) -> Campaign:
        now = time.time()
        with self._lock, self._db:
            cursor = self._db.execute(
                """
                INSERT INTO broadcasts (owner_id, chat_id, message_ids, status, created_at)
                VALUES (?, ?, ?, ?, ?)
                """,
                (owner_id, chat_id, json.dumps(message_ids), Status.DRAFT, now),
            )
        return self.get(int(cursor.lastrowid))

    def save(self, campaign: Campaign) -> None:
        with self._lock, self._db:
            self._db.execute(
                """
                UPDATE broadcasts SET buttons = ?, effect = ?, silent = ?, control_id = ?,
                    preview_ids = ?, status = ?, started_at = ?, finished_at = ?, error = ?
                WHERE id = ?
                """,
                (
                    json.dumps([[[b.text, b.url] for b in row] for row in campaign.buttons]),
                    campaign.effect,
                    int(campaign.silent),
                    campaign.control_id,
                    json.dumps(campaign.preview_ids),
                    campaign.status,
                    campaign.started_at,
                    campaign.finished_at,
                    campaign.error,
                    campaign.id,
                ),
            )

    def get(self, campaign_id: int) -> Campaign:
        with self._lock:
            row = self._db.execute(
                "SELECT * FROM broadcasts WHERE id = ?", (campaign_id,)
            ).fetchone()
        if row is None:
            raise KeyError(campaign_id)
        campaign = Campaign(
            id=int(row["id"]),
            owner_id=int(row["owner_id"]),
            chat_id=int(row["chat_id"]),
            message_ids=list(json.loads(row["message_ids"])),
            buttons=[
                [LinkButton(text, url) for text, url in line] for line in json.loads(row["buttons"])
            ],
            effect=row["effect"],
            silent=bool(row["silent"]),
            control_id=row["control_id"],
            preview_ids=list(json.loads(row["preview_ids"])),
            status=Status(row["status"]),
            created_at=float(row["created_at"]),
            started_at=row["started_at"],
            finished_at=row["finished_at"],
            error=row["error"],
        )
        campaign.counts = self.counts(campaign.id)
        return campaign

    def counts(self, campaign_id: int) -> dict[str, int]:
        with self._lock:
            rows = self._db.execute(
                """
                SELECT status, COUNT(*) FROM broadcast_recipients
                WHERE broadcast_id = ? GROUP BY status
                """,
                (campaign_id,),
            ).fetchall()
        return {str(row[0]): int(row[1]) for row in rows}

    def add_recipients(self, campaign_id: int, user_ids: list[int]) -> None:
        with self._lock, self._db:
            self._db.executemany(
                "INSERT OR IGNORE INTO broadcast_recipients (broadcast_id, user_id) VALUES (?, ?)",
                [(campaign_id, user_id) for user_id in user_ids],
            )

    def pending(self, campaign_id: int, limit: int = 500) -> list[int]:
        with self._lock:
            rows = self._db.execute(
                """
                SELECT user_id FROM broadcast_recipients
                WHERE broadcast_id = ? AND status = 'pending' LIMIT ?
                """,
                (campaign_id, limit),
            ).fetchall()
        return [int(row[0]) for row in rows]

    def delivered(self, campaign_id: int, limit: int = 500) -> list[tuple[int, list[int]]]:
        with self._lock:
            rows = self._db.execute(
                """
                SELECT user_id, message_ids FROM broadcast_recipients
                WHERE broadcast_id = ? AND status = 'sent' AND message_ids IS NOT NULL LIMIT ?
                """,
                (campaign_id, limit),
            ).fetchall()
        return [(int(row[0]), list(json.loads(row[1]))) for row in rows]

    def mark(
        self, campaign_id: int, user_id: int, delivery: Delivery, message_ids: list[int] | None
    ) -> None:
        with self._lock, self._db:
            self._db.execute(
                """
                UPDATE broadcast_recipients SET status = ?, message_ids = ?
                WHERE broadcast_id = ? AND user_id = ?
                """,
                (
                    delivery,
                    json.dumps(message_ids) if message_ids else None,
                    campaign_id,
                    user_id,
                ),
            )

    def by_status(self, *statuses: Status) -> list[Campaign]:
        marks = ",".join("?" for _ in statuses)
        with self._lock:
            rows = self._db.execute(
                f"SELECT id FROM broadcasts WHERE status IN ({marks}) ORDER BY id",
                tuple(statuses),
            ).fetchall()
        return [self.get(int(row[0])) for row in rows]

    def history(self, limit: int = 6) -> list[Campaign]:
        with self._lock:
            rows = self._db.execute(
                "SELECT id FROM broadcasts WHERE status != 'draft' ORDER BY id DESC LIMIT ?",
                (limit,),
            ).fetchall()
        return [self.get(int(row[0])) for row in rows]

    def discard_drafts(self, owner_id: int) -> None:
        with self._lock, self._db:
            self._db.execute(
                "DELETE FROM broadcasts WHERE owner_id = ? AND status = 'draft'", (owner_id,)
            )


Report = Callable[[Campaign], Awaitable[None]]
Blocked = Callable[[list[int]], Awaitable[None]]


class BroadcastEngine:
    def __init__(self, bot: Bot, store: BroadcastStore, *, report: Report, blocked: Blocked):
        self.bot = bot
        self.store = store
        self.report = report
        self.blocked = blocked
        self._task: asyncio.Task | None = None
        self._campaign_id: int | None = None
        self._stop = asyncio.Event()

    @property
    def active_id(self) -> int | None:
        return self._campaign_id if self._task and not self._task.done() else None

    def interrupted(self) -> list[Campaign]:
        campaigns = self.store.by_status(Status.RUNNING, Status.RECALLING, Status.PAUSED)
        for campaign in campaigns:
            if campaign.status == Status.RUNNING:
                campaign.status = Status.PAUSED
            elif campaign.status == Status.RECALLING:
                campaign.status = Status.DONE
            self.store.save(campaign)
        return [item for item in campaigns if item.status == Status.PAUSED]

    def start(self, campaign: Campaign) -> bool:
        if self.active_id is not None:
            return False
        campaign.status = Status.RUNNING
        campaign.started_at = campaign.started_at or time.time()
        campaign.error = None
        self.store.save(campaign)
        self._launch(campaign, self._deliver(campaign))
        return True

    def recall(self, campaign: Campaign) -> bool:
        if self.active_id is not None:
            return False
        campaign.status = Status.RECALLING
        self.store.save(campaign)
        self._launch(campaign, self._recall(campaign))
        return True

    def stop(self, campaign_id: int) -> bool:
        if self.active_id != campaign_id:
            return False
        self._stop.set()
        return True

    async def shutdown(self) -> None:
        if self._task and not self._task.done():
            self._task.cancel()
            await asyncio.gather(self._task, return_exceptions=True)

    def _launch(self, campaign: Campaign, coroutine: Awaitable[None]) -> None:
        self._stop = asyncio.Event()
        self._campaign_id = campaign.id
        self._task = asyncio.create_task(coroutine, name=f"broadcast-{campaign.id}")

    async def _publish(self, campaign: Campaign) -> None:
        campaign.counts = await asyncio.to_thread(self.store.counts, campaign.id)
        try:
            await self.report(campaign)
        except Exception as exc:
            logger.debug("Cannot report broadcast %s: %s", campaign.id, exc)

    async def _deliver(self, campaign: Campaign) -> None:
        blocked: list[int] = []
        try:
            await self._deliver_all(campaign, blocked)
            campaign.status = Status.STOPPED if self._stop.is_set() else Status.DONE
        except PostUnavailable:
            campaign.status = Status.FAILED
            campaign.error = "Исходный пост удалён или его нельзя скопировать."
        except asyncio.CancelledError:
            campaign.status = Status.PAUSED
            raise
        except Exception as exc:
            logger.exception("Broadcast %s crashed", campaign.id)
            campaign.status = Status.PAUSED
            campaign.error = f"Сбой: {exc}"
        finally:
            campaign.pause_until = 0.0
            if campaign.status != Status.PAUSED:
                campaign.finished_at = time.time()
            await asyncio.to_thread(self.store.save, campaign)
            if blocked:
                await self.blocked(blocked)
            await self._publish(campaign)

    async def _deliver_all(self, campaign: Campaign, blocked: list[int]) -> None:
        last_report = 0.0
        while not self._stop.is_set():
            batch = await asyncio.to_thread(self.store.pending, campaign.id)
            if not batch:
                return
            for user_id in batch:
                if self._stop.is_set():
                    return
                started = time.monotonic()
                result, ids = await self._send(campaign, user_id)
                if result is None:
                    return
                await asyncio.to_thread(self.store.mark, campaign.id, user_id, result, ids)
                if result == Delivery.BLOCKED:
                    blocked.append(user_id)
                if time.monotonic() - last_report >= REPORT_INTERVAL:
                    last_report = time.monotonic()
                    await self._publish(campaign)
                await asyncio.sleep(max(0.0, SEND_INTERVAL - (time.monotonic() - started)))

    async def _wait_retry(self, campaign: Campaign, seconds: float) -> None:
        campaign.pause_until = time.time() + seconds
        await self._publish(campaign)
        try:
            await asyncio.wait_for(self._stop.wait(), timeout=seconds + 0.5)
        except TimeoutError:
            pass
        campaign.pause_until = 0.0

    async def _rejected(
        self, campaign: Campaign, user_id: int, exc: TelegramBadRequest
    ) -> Delivery | None:
        value = str(exc).lower()
        if any(marker in value for marker in COPY_ERRORS):
            raise PostUnavailable from exc
        if "effect" in value and campaign.effect:
            campaign.effect = None
            await asyncio.to_thread(self.store.save, campaign)
            return None
        if any(marker in value for marker in GONE_ERRORS):
            return Delivery.BLOCKED
        logger.info("Broadcast %s to %s rejected: %s", campaign.id, user_id, exc)
        return Delivery.FAILED

    async def _send(self, campaign: Campaign, user_id: int) -> tuple[Delivery | None, list[int]]:
        server_errors = 0
        while not self._stop.is_set():
            try:
                return Delivery.SENT, await self._copy(campaign, user_id)
            except TelegramRetryAfter as exc:
                await self._wait_retry(campaign, float(exc.retry_after))
            except TelegramForbiddenError:
                return Delivery.BLOCKED, []
            except TelegramBadRequest as exc:
                result = await self._rejected(campaign, user_id, exc)
                if result is not None:
                    return result, []
            except TelegramServerError:
                server_errors += 1
                if server_errors >= 3:
                    return Delivery.FAILED, []
                await asyncio.sleep(2 * server_errors)
            except (TelegramNetworkError, TimeoutError):
                return Delivery.UNCERTAIN, []
            except TelegramAPIError as exc:
                logger.info("Broadcast %s to %s failed: %s", campaign.id, user_id, exc)
                return Delivery.FAILED, []
        return None, []

    async def _copy(self, campaign: Campaign, user_id: int) -> list[int]:
        if campaign.is_album:
            result = await self.bot.copy_messages(
                user_id,
                from_chat_id=campaign.chat_id,
                message_ids=campaign.message_ids,
                disable_notification=campaign.silent,
                request_timeout=30,
            )
            return [item.message_id for item in result]
        result = await self.bot.copy_message(
            user_id,
            from_chat_id=campaign.chat_id,
            message_id=campaign.message_ids[0],
            reply_markup=campaign.markup,
            message_effect_id=campaign.effect,
            disable_notification=campaign.silent,
            request_timeout=30,
        )
        return [result.message_id]

    async def _recall(self, campaign: Campaign) -> None:
        last_report = 0.0
        try:
            while not self._stop.is_set():
                batch = await asyncio.to_thread(self.store.delivered, campaign.id)
                if not batch:
                    break
                for user_id, ids in batch:
                    if self._stop.is_set():
                        break
                    try:
                        await self.bot.delete_messages(user_id, ids, request_timeout=30)
                    except TelegramRetryAfter as exc:
                        await asyncio.sleep(float(exc.retry_after) + 0.5)
                        continue
                    except (TelegramAPIError, TimeoutError) as exc:
                        logger.debug("Cannot recall broadcast for %s: %s", user_id, exc)
                    await asyncio.to_thread(
                        self.store.mark, campaign.id, user_id, Delivery.DELETED, None
                    )
                    if time.monotonic() - last_report >= REPORT_INTERVAL:
                        last_report = time.monotonic()
                        await self._publish(campaign)
                    await asyncio.sleep(SEND_INTERVAL)
            campaign.status = Status.DONE if self._stop.is_set() else Status.RECALLED
        except asyncio.CancelledError:
            campaign.status = Status.DONE
            raise
        finally:
            await asyncio.to_thread(self.store.save, campaign)
            await self._publish(campaign)
