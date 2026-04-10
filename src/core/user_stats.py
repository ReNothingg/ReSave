from __future__ import annotations

import json
import logging
import sqlite3
import threading
from datetime import datetime
from pathlib import Path

import config

logger = logging.getLogger(__name__)


class UserStats:
    def __init__(self, user_id: int):
        self.user_id = user_id
        self.downloads_count = 0
        self.total_videos = 0
        self.total_audios = 0
        self.total_other_downloads = 0
        self.failed_downloads = 0
        self.total_size_mb = 0.0
        self.first_download_date = None
        self.last_download_date = None

    def to_dict(self):
        return {
            "user_id": self.user_id,
            "downloads_count": self.downloads_count,
            "total_videos": self.total_videos,
            "total_audios": self.total_audios,
            "total_other_downloads": self.total_other_downloads,
            "failed_downloads": self.failed_downloads,
            "total_size_mb": round(self.total_size_mb, 2),
            "first_download_date": self.first_download_date,
            "last_download_date": self.last_download_date,
        }

    @classmethod
    def from_dict(cls, data):
        stats = cls(int(data["user_id"]))
        stats.downloads_count = data.get("downloads_count", 0)
        stats.total_videos = data.get("total_videos", 0)
        stats.total_audios = data.get("total_audios", 0)
        stats.total_other_downloads = data.get("total_other_downloads", 0)
        stats.failed_downloads = data.get("failed_downloads", 0)
        stats.total_size_mb = data.get("total_size_mb", 0.0)
        stats.first_download_date = data.get("first_download_date")
        stats.last_download_date = data.get("last_download_date")
        return stats


class UserStatsManager:
    def __init__(self, db_path: str | None = None, legacy_stats_file: str = "user_stats.json"):
        self.db_path = Path(db_path or config.STATS_DB_PATH)
        self.legacy_stats_file = Path(legacy_stats_file)
        self.lock = threading.Lock()
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self.connection = sqlite3.connect(self.db_path, check_same_thread=False)
        self.connection.row_factory = sqlite3.Row
        self._initialize_db()
        self._migrate_legacy_stats_if_needed()

    def _initialize_db(self):
        with self.lock:
            self.connection.execute(
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
            self.connection.commit()

    def _migrate_legacy_stats_if_needed(self):
        if not self.legacy_stats_file.exists():
            return

        with self.lock:
            existing_rows = self.connection.execute(
                "SELECT COUNT(*) FROM user_stats"
            ).fetchone()[0]

        if existing_rows:
            return

        try:
            with self.legacy_stats_file.open("r", encoding="utf-8") as file:
                data = json.load(file)
        except Exception as exc:
            logger.error(
                "Ошибка при загрузке legacy-статистики %s: %s",
                self.legacy_stats_file,
                exc,
            )
            return

        migrated = 0
        with self.lock:
            for user_id_str, stats_data in data.items():
                try:
                    user_stats = UserStats.from_dict(
                        {"user_id": int(user_id_str), **stats_data}
                    )
                    self._save_user_stats_locked(user_stats)
                    migrated += 1
                except Exception as exc:
                    logger.warning(
                        "Не удалось мигрировать статистику пользователя %s: %s",
                        user_id_str,
                        exc,
                    )

        if migrated:
            logger.info(
                "Мигрирована legacy-статистика %s пользователей в SQLite",
                migrated,
            )

    def _load_user_stats_locked(self, user_id: int) -> UserStats:
        row = self.connection.execute(
            """
            SELECT
                user_id,
                downloads_count,
                total_videos,
                total_audios,
                total_other_downloads,
                failed_downloads,
                total_size_mb,
                first_download_date,
                last_download_date
            FROM user_stats
            WHERE user_id = ?
            """,
            (user_id,),
        ).fetchone()
        if row is None:
            return UserStats(user_id)
        return UserStats.from_dict(dict(row))

    def _save_user_stats_locked(self, stats: UserStats):
        self.connection.execute(
            """
            INSERT INTO user_stats (
                user_id,
                downloads_count,
                total_videos,
                total_audios,
                total_other_downloads,
                failed_downloads,
                total_size_mb,
                first_download_date,
                last_download_date
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(user_id) DO UPDATE SET
                downloads_count = excluded.downloads_count,
                total_videos = excluded.total_videos,
                total_audios = excluded.total_audios,
                total_other_downloads = excluded.total_other_downloads,
                failed_downloads = excluded.failed_downloads,
                total_size_mb = excluded.total_size_mb,
                first_download_date = excluded.first_download_date,
                last_download_date = excluded.last_download_date
            """,
            (
                stats.user_id,
                stats.downloads_count,
                stats.total_videos,
                stats.total_audios,
                stats.total_other_downloads,
                stats.failed_downloads,
                stats.total_size_mb,
                stats.first_download_date,
                stats.last_download_date,
            ),
        )
        self.connection.commit()

    def get_user_stats(self, user_id: int) -> UserStats:
        with self.lock:
            return self._load_user_stats_locked(user_id)

    def record_download(self, user_id: int, action: str, file_size_mb: float = 0.0):
        if user_id <= 0:
            return

        with self.lock:
            stats = self._load_user_stats_locked(user_id)
            stats.downloads_count += 1

            if action == "video":
                stats.total_videos += 1
            elif action == "audio":
                stats.total_audios += 1
            else:
                stats.total_other_downloads += 1

            stats.total_size_mb += file_size_mb
            stats.last_download_date = datetime.now().isoformat()

            if stats.first_download_date is None:
                stats.first_download_date = datetime.now().isoformat()

            self._save_user_stats_locked(stats)

    def record_failed_download(self, user_id: int):
        if user_id <= 0:
            return

        with self.lock:
            stats = self._load_user_stats_locked(user_id)
            stats.failed_downloads += 1
            self._save_user_stats_locked(stats)

    def get_all_stats(self):
        with self.lock:
            rows = self.connection.execute(
                """
                SELECT
                    user_id,
                    downloads_count,
                    total_videos,
                    total_audios,
                    total_other_downloads,
                    failed_downloads,
                    total_size_mb,
                    first_download_date,
                    last_download_date
                FROM user_stats
                ORDER BY downloads_count DESC, user_id ASC
                """
            ).fetchall()
        return {
            int(row["user_id"]): UserStats.from_dict(dict(row))
            for row in rows
        }

    def clear_all_stats(self):
        with self.lock:
            self.connection.execute("DELETE FROM user_stats")
            self.connection.commit()

    def close(self):
        with self.lock:
            self.connection.close()


_stats_manager = None


def get_stats_manager():
    global _stats_manager
    if _stats_manager is None:
        _stats_manager = UserStatsManager()
    return _stats_manager


def set_stats_manager(manager):
    global _stats_manager
    _stats_manager = manager
