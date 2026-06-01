from dataclasses import dataclass, field
from typing import Optional, Dict, Any
import threading
from uuid import uuid4
from datetime import datetime

@dataclass
class DownloadTask:
    task_id: str = field(default_factory=lambda: str(uuid4()))
    url: str = ""
    chat_id: int = 0
    message_id: int = 0
    info: dict = field(default_factory=dict)
    action: str = "best"
    format_param: Optional[str] = None
    status: str = "pending"
    progress: float = 0.0
    file_path: Optional[str] = None
    file_id: Optional[str] = None
    started_at: Optional[float] = None
    completed_at: Optional[float] = None
    error: Optional[str] = None
    work_dir: Optional[str] = None
    cancel_event: Optional[threading.Event] = None
    is_inline: bool = False
    inline_query_id: Optional[str] = None
    inline_result_id: Optional[str] = None
    reply_to_id: Optional[int] = None
    silent_mode: bool = False


@dataclass
class InlineQuery:
    query_id: str = ""
    url: str = ""
    user_id: int = 0
    result_id: str = ""
    info: Optional[Dict[str, Any]] = None
    task_id: Optional[str] = None
    file_id: Optional[str] = None
    timestamp: float = 0.0
    status: str = "pending"
    error: Optional[str] = None
