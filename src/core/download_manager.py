import logging
import queue
import threading
import time

import config
from .models import DownloadTask
from ..utils.ui_manager import get_ui_manager

logger = logging.getLogger(__name__)


class DownloadManager:
    def __init__(self, max_concurrent_downloads=3, max_retries=3):
        self.tasks = {}
        self.task_queue = queue.Queue()
        self.max_concurrent_downloads = max_concurrent_downloads
        self.max_retries = max_retries
        self.active_count = 0
        self.lock = threading.Lock()
        self.worker_threads = []
        self.status_thread = None
        self.bot = None
        self._started = False

    def set_bot(self, bot):
        self.bot = bot
        self._start_workers_once()

    def _start_workers_once(self):
        with self.lock:
            if self._started:
                return
            self._started = True

        for _ in range(self.max_concurrent_downloads):
            worker = threading.Thread(target=self._worker, daemon=True)
            worker.start()
            self.worker_threads.append(worker)

        self.status_thread = threading.Thread(target=self._status_updater, daemon=True)
        self.status_thread.start()

    def get_user_task_count(self, chat_id):
        if chat_id <= 0:
            return 0

        with self.lock:
            return sum(
                1
                for task in self.tasks.values()
                if task.chat_id == chat_id and task.status in {"pending", "downloading"}
            )

    def can_add_task(self, chat_id):
        if chat_id <= 0 or config.MAX_DOWNLOADS_PER_USER <= 0:
            return True

        return self.get_user_task_count(chat_id) < config.MAX_DOWNLOADS_PER_USER

    def add_task(
        self,
        url,
        chat_id,
        message_id,
        info,
        action,
        format_param=None,
        reply_to_id=None,
        silent_mode=False,
    ):
        task = DownloadTask(
            url=url,
            chat_id=chat_id,
            message_id=message_id,
            info=info,
            action=action,
            format_param=format_param,
            cancel_event=threading.Event(),
            reply_to_id=reply_to_id,
            silent_mode=silent_mode,
        )

        with self.lock:
            if chat_id > 0 and config.MAX_DOWNLOADS_PER_USER > 0:
                active_tasks = sum(
                    1
                    for existing_task in self.tasks.values()
                    if existing_task.chat_id == chat_id
                    and existing_task.status in {"pending", "downloading"}
                )
                if active_tasks >= config.MAX_DOWNLOADS_PER_USER:
                    raise ValueError("too many active downloads for user")

            self.tasks[task.task_id] = task
            self.task_queue.put(task)

        return task.task_id

    def cancel_task(self, task_id):
        with self.lock:
            if task_id in self.tasks:
                task = self.tasks[task_id]
                task.cancel_event.set()
                task.status = "cancelled"
                return True
        return False

    def get_task(self, task_id):
        with self.lock:
            return self.tasks.get(task_id)

    def _worker(self):
        while True:
            task = self.task_queue.get()

            with self.lock:
                self.active_count += 1

            try:
                if task.cancel_event.is_set():
                    continue

                task.status = "downloading"
                task.started_at = time.time()
                self._download_and_send_task(task)

                if not task.cancel_event.is_set():
                    task.status = "completed"
                    task.completed_at = time.time()

            except Exception as exc:
                logger.exception("Ошибка при выполнении задачи %s: %s", task.task_id, exc)
                task.status = "failed"
                task.error = str(exc)

            finally:
                with self.lock:
                    self.active_count -= 1
                    self.tasks.pop(task.task_id, None)
                self.task_queue.task_done()

    def _download_and_send_task(self, task):
        from .download_handler import handle_download_task

        handle_download_task(task, self.bot, config.TEMP_DIR)

    def _status_updater(self):
        while True:
            try:
                with self.lock:
                    active_tasks = {
                        task_id: task
                        for task_id, task in self.tasks.items()
                        if task.status == "downloading"
                        and task.progress < 1.0
                        and not task.silent_mode
                    }

                for task_id, task in active_tasks.items():
                    try:
                        if task.progress > 0 and task.progress < 1.0:
                            ui_manager = get_ui_manager()
                            progress_bar = ui_manager.create_progress_bar(task.progress)

                            if task.started_at and task.progress > 0.05:
                                elapsed = time.time() - task.started_at
                                total_estimated = elapsed / task.progress
                                remaining = total_estimated - elapsed
                                if remaining > 0:
                                    remaining_str = f"Осталось: {self._format_time(remaining)}"
                                else:
                                    remaining_str = ""
                            else:
                                remaining_str = ""

                            self.bot.edit_message_text(
                                ui_manager.format_panel(
                                    "Скачивание",
                                    [
                                        f"⬇️ {progress_bar}",
                                        f"⏱️ {remaining_str}" if remaining_str else "⏱️ Время уточняется",
                                        "",
                                        "Файл будет отправлен сразу после обработки.",
                                    ],
                                    icon="📦",
                                ),
                                task.chat_id,
                                task.message_id,
                            )
                    except Exception as exc:
                        logger.debug("Ошибка при обновлении статуса задачи %s: %s", task_id, exc)

            except Exception as exc:
                logger.error("Ошибка в потоке обновления статуса: %s", exc)

            time.sleep(2)

    @staticmethod
    def _generate_progress_bar(progress, length=10):
        filled = int(progress * length)
        return f"{'▓' * filled}{'░' * (length - filled)}"

    @staticmethod
    def _format_time(seconds):
        if seconds < 60:
            return f"{seconds:.0f} сек"
        if seconds < 3600:
            return f"{seconds / 60:.1f} мин"
        return f"{seconds / 3600:.1f} ч"
