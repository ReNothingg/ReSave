from __future__ import annotations

import logging
from pathlib import Path

from .user_stats import get_stats_manager

logger = logging.getLogger(__name__)


def ensure_task_work_dir(task, temp_dir: str) -> Path:
    if task.work_dir:
        work_dir = Path(task.work_dir)
    else:
        work_dir = Path(temp_dir) / f"task_{task.task_id}"
        task.work_dir = str(work_dir)

    work_dir.mkdir(parents=True, exist_ok=True)
    return work_dir


def find_completed_files(work_dir: Path) -> list[Path]:
    ignored_suffixes = {".part", ".tmp", ".ytdl"}
    return [
        path
        for path in work_dir.iterdir()
        if path.is_file() and path.suffix.lower() not in ignored_suffixes
    ]


def describe_work_dir(work_dir: Path) -> str:
    if not work_dir.exists():
        return "<missing>"

    items = sorted(path.name for path in work_dir.iterdir())
    return ", ".join(items) if items else "<empty>"


def format_file_size(size_bytes: int) -> str:
    if size_bytes < 1024:
        return f"{size_bytes} B"
    if size_bytes < 1024 * 1024:
        return f"{size_bytes / 1024:.1f} KB"
    return f"{size_bytes / (1024 * 1024):.1f} MB"


def record_download_success(chat_id: int, action: str, file_size_mb: float):
    try:
        get_stats_manager().record_download(chat_id, action=action, file_size_mb=file_size_mb)
    except Exception as exc:
        logger.error("Ошибка при записи статистики загрузки: %s", exc)


def record_failed_download(chat_id: int):
    try:
        get_stats_manager().record_failed_download(chat_id)
    except Exception as exc:
        logger.error("Ошибка при записи неуспешной загрузки: %s", exc)


class ProgressHook:
    def __init__(self, task):
        self.task = task

    def __call__(self, data):
        if data["status"] == "downloading":
            total = data.get("total_bytes") or data.get("total_bytes_estimate")
            if total:
                self.task.progress = data.get("downloaded_bytes", 0) / total
        elif data["status"] == "finished":
            self.task.progress = 1.0

        if self.task.cancel_event.is_set():
            raise Exception("Загрузка отменена пользователем")
