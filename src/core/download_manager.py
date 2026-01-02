import time
import queue
import logging
import threading
from concurrent.futures import ThreadPoolExecutor
import aiohttp

from .models import DownloadTask
import config

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
        self.session = None
        self.bot = None
        self._started = False


        self.thread_pool = ThreadPoolExecutor(max_workers=max(10, max_concurrent_downloads * 2))

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

    async def create_session(self):
        if self.session is None:
            self.session = aiohttp.ClientSession()
        return self.session

    async def close_session(self):
        if self.session:
            await self.session.close()
            self.session = None

    def add_task(self, url, chat_id, message_id, info, action, format_param=None,
                 is_inline=False, inline_query_id=None, inline_result_id=None,
                 reply_to_id=None, silent_mode=False):
        task = DownloadTask(
            url=url,
            chat_id=chat_id,
            message_id=message_id,
            info=info,
            action=action,
            format_param=format_param,
            cancel_event=threading.Event(),
            is_inline=is_inline,
            inline_query_id=inline_query_id,
            inline_result_id=inline_result_id,
            reply_to_id=reply_to_id,
            silent_mode=silent_mode
        )

        with self.lock:
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

            except Exception as e:
                logger.exception(f"Ошибка при выполнении задачи {task.task_id}: {str(e)}")
                task.status = "failed"
                task.error = str(e)

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
                    active_tasks = {k: v for k, v in self.tasks.items()
                                   if v.status == "downloading" and v.progress < 1.0 and not v.is_inline and not v.silent_mode}

                for task_id, task in active_tasks.items():
                    try:
                        if task.progress > 0 and task.progress < 1.0:
                            progress_bar = self._generate_progress_bar(task.progress)

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
                                f"⏬ Скачивание в процессе: {int(task.progress * 100)}%\n"
                                f"{progress_bar}{remaining_str}\n\n💬 Скоро будет готово!",
                                task.chat_id,
                                task.message_id
                            )
                    except Exception as e:
                        logger.debug(f"Ошибка при обновлении статуса задачи {task_id}: {e}")

            except Exception as e:
                logger.error(f"Ошибка в потоке обновления статуса: {e}")

            time.sleep(2)

    @staticmethod
    def _generate_progress_bar(progress, length=10):
        filled = int(progress * length)
        return f"{'▰' * filled}{'▱' * (length - filled)}"

    @staticmethod
    def _format_time(seconds):
        if seconds < 60:
            return f"{seconds:.0f} сек"
        elif seconds < 3600:
            return f"{seconds / 60:.1f} мин"
        else:
            return f"{seconds / 3600:.1f} ч"
