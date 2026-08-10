from __future__ import annotations

import asyncio
import logging
import time
from collections.abc import Awaitable, Callable

from ..utils.presentation import panel, user_error
from .media_downloader import DownloadCancelled
from .models import ACTIVE_STATUSES, DownloadTask, TaskStatus
from .telegram_gateway import TelegramGateway
from .user_stats import UserStatsManager

logger = logging.getLogger(__name__)


class QueueCapacityError(RuntimeError):
    pass


class UserTaskLimitError(RuntimeError):
    pass


class DownloadManager:
    def __init__(
        self,
        *,
        processor: Callable[[DownloadTask], Awaitable[None]],
        progress_reporter: Callable[[DownloadTask], Awaitable[None]],
        telegram: TelegramGateway,
        stats: UserStatsManager,
        max_concurrent_downloads: int,
        max_queue_size: int,
        max_tasks_per_user: int,
        progress_update_seconds: int,
    ):
        self.processor = processor
        self.progress_reporter = progress_reporter
        self.telegram = telegram
        self.stats = stats
        self.max_concurrent_downloads = max_concurrent_downloads
        self.max_tasks_per_user = max_tasks_per_user
        self.progress_update_seconds = progress_update_seconds
        self._queue: asyncio.Queue[DownloadTask] = asyncio.Queue(maxsize=max_queue_size)
        self._tasks: dict[str, DownloadTask] = {}
        self._workers: list[asyncio.Task] = []
        self._progress_task: asyncio.Task | None = None
        self._started = False

    @property
    def queue_size(self) -> int:
        return self._queue.qsize()

    @property
    def active_count(self) -> int:
        return sum(task.status != TaskStatus.PENDING for task in self._tasks.values())

    async def start(self) -> None:
        if self._started:
            return
        self._started = True
        self._workers = [
            asyncio.create_task(self._worker(index), name=f"download-worker-{index}")
            for index in range(self.max_concurrent_downloads)
        ]
        self._progress_task = asyncio.create_task(self._progress_loop(), name="download-progress")
        logger.info("Download manager started with %s workers", len(self._workers))

    async def stop(self) -> None:
        if not self._started:
            return
        self._started = False
        for task in self._tasks.values():
            task.cancel()
        if self._progress_task:
            self._progress_task.cancel()
            await asyncio.gather(self._progress_task, return_exceptions=True)
        try:
            await asyncio.wait_for(self._queue.join(), timeout=15)
        except TimeoutError:
            logger.warning("Timed out while waiting for download workers to stop")
        for worker in self._workers:
            worker.cancel()
        if self._workers:
            await asyncio.gather(*self._workers, return_exceptions=True)
        self._workers.clear()
        self._progress_task = None
        self._tasks.clear()
        logger.info("Download manager stopped")

    async def enqueue(self, task: DownloadTask) -> int:
        if not self._started:
            raise RuntimeError("Менеджер загрузок ещё не запущен")
        active_for_user = sum(
            1
            for current in self._tasks.values()
            if current.user_id == task.user_id and current.status in ACTIVE_STATUSES
        )
        if active_for_user >= self.max_tasks_per_user:
            raise UserTaskLimitError(
                f"У вас уже {active_for_user} активных загрузок. Дождитесь завершения или отмените их."
            )
        if self._queue.full():
            raise QueueCapacityError("Очередь заполнена. Попробуйте через несколько минут.")
        self._tasks[task.task_id] = task
        self._queue.put_nowait(task)
        return self._queue.qsize()

    def snapshot(
        self, *, user_id: int | None = None, chat_id: int | None = None
    ) -> list[DownloadTask]:
        tasks = list(self._tasks.values())
        if user_id is not None:
            tasks = [task for task in tasks if task.user_id == user_id]
        if chat_id is not None:
            tasks = [task for task in tasks if task.chat_id == chat_id]
        return sorted(tasks, key=lambda task: task.created_at)

    def cancel_for_user(self, user_id: int, *, chat_id: int | None = None) -> int:
        cancelled = 0
        for task in self.snapshot(user_id=user_id, chat_id=chat_id):
            if (
                task.status
                in {
                    TaskStatus.PENDING,
                    TaskStatus.DOWNLOADING,
                    TaskStatus.PROCESSING,
                }
                and not task.cancel_event.is_set()
            ):
                task.cancel()
                cancelled += 1
        return cancelled

    async def _worker(self, worker_id: int) -> None:
        while True:
            task = await self._queue.get()
            try:
                if task.cancel_event.is_set():
                    raise DownloadCancelled("Загрузка отменена пользователем")
                task.status = TaskStatus.DOWNLOADING
                task.phase = "Подготовка загрузки"
                task.started_at = time.time()
                await self.processor(task)
                if task.cancel_event.is_set():
                    raise DownloadCancelled("Загрузка отменена пользователем")
                task.status = TaskStatus.COMPLETED
                task.phase = "Готово"
                task.completed_at = time.time()
                if not task.silent:
                    try:
                        await self.telegram.delete_status(task.chat_id, task.status_message_id)
                    except Exception as exc:
                        logger.debug("Cannot delete completed task status: %s", exc)
            except asyncio.CancelledError:
                task.cancel()
                raise
            except DownloadCancelled:
                task.status = TaskStatus.CANCELLED
                task.phase = "Отменено"
                if not task.silent and self._started:
                    try:
                        await self.telegram.edit_status(
                            task.chat_id,
                            task.status_message_id,
                            panel("Загрузка отменена", icon="✕"),
                        )
                    except Exception as exc:
                        logger.debug("Cannot edit cancelled task status: %s", exc)
            except Exception as exc:
                task.status = TaskStatus.FAILED
                task.error = str(exc)
                task.completed_at = time.time()
                logger.exception("Task %s failed in worker %s", task.task_id, worker_id)
                try:
                    await asyncio.to_thread(self.stats.record_failed_download, task.user_id)
                except Exception as stats_exc:
                    logger.error("Cannot record failed task: %s", stats_exc)
                if not task.silent:
                    try:
                        await self.telegram.edit_status(
                            task.chat_id,
                            task.status_message_id,
                            user_error(exc),
                        )
                    except Exception as status_exc:
                        logger.debug("Cannot report failed task status: %s", status_exc)
            finally:
                self._tasks.pop(task.task_id, None)
                self._queue.task_done()

    async def _progress_loop(self) -> None:
        last_progress: dict[str, float] = {}
        while True:
            await asyncio.sleep(self.progress_update_seconds)
            for task in list(self._tasks.values()):
                if task.silent or task.status not in ACTIVE_STATUSES:
                    continue
                previous = last_progress.get(task.task_id, -1.0)
                if task.progress == previous and task.status == TaskStatus.DOWNLOADING:
                    continue
                last_progress[task.task_id] = task.progress
                try:
                    await self.progress_reporter(task)
                except Exception as exc:
                    logger.debug("Cannot report task %s progress: %s", task.task_id, exc)
            active_ids = set(self._tasks)
            for task_id in set(last_progress) - active_ids:
                last_progress.pop(task_id, None)

    @staticmethod
    def format_time(seconds: float) -> str:
        if seconds < 60:
            return f"{seconds:.0f} сек"
        if seconds < 3600:
            return f"{seconds / 60:.1f} мин"
        return f"{seconds / 3600:.1f} ч"
