from __future__ import annotations

import threading
import time
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any
from uuid import uuid4


class DownloadAction(StrEnum):
    BEST = "best"
    MEDIUM = "medium"
    LOW = "low"
    RESOLUTION = "res"
    AUDIO = "audio"
    GIF = "gif"
    SUBTITLES = "subtitles"
    THUMBNAIL = "thumbnail"
    TIKTOK_PHOTO = "tiktok_photo"


class TaskStatus(StrEnum):
    PENDING = "pending"
    DOWNLOADING = "downloading"
    PROCESSING = "processing"
    UPLOADING = "uploading"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"


ACTIVE_STATUSES = {
    TaskStatus.PENDING,
    TaskStatus.DOWNLOADING,
    TaskStatus.PROCESSING,
    TaskStatus.UPLOADING,
}


@dataclass(slots=True)
class DownloadTask:
    url: str
    chat_id: int
    user_id: int
    status_message_id: int
    reply_to_message_id: int | None
    info: dict[str, Any]
    action: DownloadAction
    requested_height: int | None = None
    silent: bool = False
    task_id: str = field(default_factory=lambda: uuid4().hex)
    status: TaskStatus = TaskStatus.PENDING
    phase: str = "В очереди"
    progress: float = 0.0
    speed: str | None = None
    eta: int | None = None
    error: str | None = None
    work_dir: str | None = None
    created_at: float = field(default_factory=time.time)
    started_at: float | None = None
    completed_at: float | None = None
    cancel_event: threading.Event = field(default_factory=threading.Event, repr=False)

    @property
    def title(self) -> str:
        return str(self.info.get("title") or self.info.get("id") or "video")

    def cancel(self) -> None:
        self.cancel_event.set()
        self.status = TaskStatus.CANCELLED
        self.phase = "Отменено"


@dataclass(frozen=True, slots=True)
class MediaSelection:
    token: str
    user_id: int
    chat_id: int
    reply_to_message_id: int
    url: str
    info: dict[str, Any]
    resolutions: tuple[int, ...]
    expires_at: float


@dataclass(frozen=True, slots=True)
class DownloadVariant:
    label: str
    format_selector: str
    output_template: str
    postprocessors: tuple[dict[str, Any], ...] = ()
