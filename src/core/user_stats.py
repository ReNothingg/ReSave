from __future__ import annotations

import json
import logging
import sqlite3
import threading
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

import config

logger = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class UserStats:
    user_id: int
    downloads_count: int = 0
    total_videos: int = 0
    total_audios: int = 0
    total_other_downloads: int = 0
    failed_downloads: int = 0
    total_size_mb: float = 0.0
    first_download_date: str | None = None
    last_download_date: str | None = None

    @classmethod
    def from_row(cls, row: sqlite3.Row) -> UserStats:
        return cls(**dict(row))


class UserStatsManager:
    def __init__(
        self, db_path: str | Path | None = None, legacy_stats_file: str | Path = "user_stats.json"
    ):
        self.db_path = Path(db_path or config.STATS_DB_PATH)
        self.legacy_stats_file = Path(legacy_stats_file)
        self._lock = threading.RLock()
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._connection = sqlite3.connect(
            self.db_path,
            check_same_thread=False,
            timeout=15,
        )
        self._connection.row_factory = sqlite3.Row
        self._connection.execute("PRAGMA journal_mode=WAL")
        self._connection.execute("PRAGMA busy_timeout=15000")
        self._initialize()
        self._migrate_legacy()

    def _initialize(self) -> None:
        with self._lock, self._connection:
            self._connection.execute(
                """
                CREATE TABLE IF NOT EXISTS user_stats (
                    user_id INTEGER PRIMARY KEY,
                    downloads_count INTEGER NOT NULL DEFAULT 0,
                    total_videos INTEGER NOT NULL DEFAULT 0,
                    total_audios INTEGER NOT NULL DEFAULT 0,
                    total_other_downloads INTEGER NOT NULL DEFAULT 0,
                    failed_downloads INTEGER NOT NULL DEFAULT 0,
                    total_size_mb REAL NOT NULL DEFAULT 0,
                    first_download_date TEXT,
                    last_download_date TEXT
                )
                """
            )

    def _migrate_legacy(self) -> None:
        if not self.legacy_stats_file.exists():
            return
        with self._lock:
            count = self._connection.execute("SELECT COUNT(*) FROM user_stats").fetchone()[0]
        if count:
            return
        try:
            data = json.loads(self.legacy_stats_file.read_text(encoding="utf-8"))
        except (OSError, ValueError) as exc:
            logger.warning("Cannot read legacy stats %s: %s", self.legacy_stats_file, exc)
            return

        with self._lock, self._connection:
            for raw_user_id, item in data.items():
                try:
                    self._connection.execute(
                        """
                        INSERT OR IGNORE INTO user_stats VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                        """,
                        (
                            int(raw_user_id),
                            int(item.get("downloads_count", 0)),
                            int(item.get("total_videos", 0)),
                            int(item.get("total_audios", 0)),
                            int(item.get("total_other_downloads", 0)),
                            int(item.get("failed_downloads", 0)),
                            float(item.get("total_size_mb", 0.0)),
                            item.get("first_download_date"),
                            item.get("last_download_date"),
                        ),
                    )
                except (TypeError, ValueError) as exc:
                    logger.warning("Skipping malformed legacy stats for %s: %s", raw_user_id, exc)

    def ensure_user(self, user_id: int) -> None:
        if user_id <= 0:
            return
        with self._lock, self._connection:
            self._connection.execute(
                "INSERT OR IGNORE INTO user_stats (user_id) VALUES (?)",
                (user_id,),
            )

    def record_download(self, user_id: int, action: str, file_size_mb: float = 0.0) -> None:
        if user_id <= 0:
            return
        now = datetime.now(UTC).isoformat()
        video = 1 if action == "video" else 0
        audio = 1 if action == "audio" else 0
        other = 1 if action not in {"video", "audio"} else 0
        with self._lock, self._connection:
            self._connection.execute(
                """
                INSERT INTO user_stats (
                    user_id, downloads_count, total_videos, total_audios,
                    total_other_downloads, total_size_mb, first_download_date,
                    last_download_date
                ) VALUES (?, 1, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(user_id) DO UPDATE SET
                    downloads_count = downloads_count + 1,
                    total_videos = total_videos + excluded.total_videos,
                    total_audios = total_audios + excluded.total_audios,
                    total_other_downloads = total_other_downloads + excluded.total_other_downloads,
                    total_size_mb = total_size_mb + excluded.total_size_mb,
                    first_download_date = COALESCE(first_download_date, excluded.first_download_date),
                    last_download_date = excluded.last_download_date
                """,
                (user_id, video, audio, other, max(0.0, file_size_mb), now, now),
            )

    def record_failed_download(self, user_id: int) -> None:
        if user_id <= 0:
            return
        with self._lock, self._connection:
            self._connection.execute(
                """
                INSERT INTO user_stats (user_id, failed_downloads) VALUES (?, 1)
                ON CONFLICT(user_id) DO UPDATE SET failed_downloads = failed_downloads + 1
                """,
                (user_id,),
            )

    def get_user_stats(self, user_id: int) -> UserStats:
        with self._lock:
            row = self._connection.execute(
                "SELECT * FROM user_stats WHERE user_id = ?", (user_id,)
            ).fetchone()
        return UserStats(user_id) if row is None else UserStats.from_row(row)

    def get_all_stats(self) -> dict[int, UserStats]:
        with self._lock:
            rows = self._connection.execute(
                "SELECT * FROM user_stats ORDER BY downloads_count DESC, user_id ASC"
            ).fetchall()
        return {int(row["user_id"]): UserStats.from_row(row) for row in rows}

    def clear_all_stats(self) -> None:
        with self._lock, self._connection:
            self._connection.execute("DELETE FROM user_stats")

    def close(self) -> None:
        with self._lock:
            self._connection.close()


_stats_manager: UserStatsManager | None = None


def get_stats_manager() -> UserStatsManager:
    global _stats_manager
    if _stats_manager is None:
        _stats_manager = UserStatsManager()
    return _stats_manager


def set_stats_manager(manager: UserStatsManager | None) -> None:
    global _stats_manager
    _stats_manager = manager
