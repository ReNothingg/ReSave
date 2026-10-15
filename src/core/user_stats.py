from __future__ import annotations

import json
import logging
import sqlite3
import threading
import time
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


@dataclass(frozen=True, slots=True)
class AudienceSummary:
    total: int = 0
    reachable: int = 0
    blocked: int = 0
    active_day: int = 0
    active_week: int = 0
    new_week: int = 0


def _now() -> str:
    return datetime.now(UTC).isoformat()


class UserStatsManager:
    def __init__(
        self, db_path: str | Path | None = None, legacy_stats_file: str | Path = "user_stats.json"
    ):
        self.db_path = Path(db_path or config.SETTINGS.stats_db_path)
        legacy_path = Path(legacy_stats_file).expanduser()
        self.legacy_stats_file = (
            legacy_path if legacy_path.is_absolute() else config.BASE_DIR / legacy_path
        ).resolve()
        self._lock = threading.RLock()
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._connection = sqlite3.connect(
            self.db_path,
            check_same_thread=False,
            timeout=15,
        )
        self._connection.row_factory = sqlite3.Row
        self._connection.execute("PRAGMA journal_mode=DELETE")
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
            self._connection.execute(
                """
                CREATE TABLE IF NOT EXISTS users (
                    user_id INTEGER PRIMARY KEY,
                    first_seen TEXT NOT NULL,
                    last_seen TEXT NOT NULL,
                    blocked_at TEXT
                )
                """
            )
            self._connection.execute(
                """
                INSERT OR IGNORE INTO users (user_id, first_seen, last_seen)
                SELECT user_id,
                    COALESCE(first_download_date, ?),
                    COALESCE(last_download_date, first_download_date, ?)
                FROM user_stats WHERE user_id > 0
                """,
                (_now(), _now()),
            )
            self._connection.execute(
                """
                CREATE TABLE IF NOT EXISTS app_meta (
                    key TEXT PRIMARY KEY,
                    value TEXT NOT NULL
                )
                """
            )

    def _migrate_legacy(self) -> None:
        with self._lock:
            migrated = self._connection.execute(
                "SELECT 1 FROM app_meta WHERE key = 'legacy_stats_migrated'"
            ).fetchone()
        if migrated:
            return
        if not self.legacy_stats_file.exists():
            return
        with self._lock:
            count = self._connection.execute("SELECT COUNT(*) FROM user_stats").fetchone()[0]
        if count:
            self._mark_legacy_migrated()
            return
        try:
            data = json.loads(self.legacy_stats_file.read_text(encoding="utf-8"))
        except (OSError, ValueError) as exc:
            logger.warning("Cannot read legacy stats %s: %s", self.legacy_stats_file, exc)
            return
        if not isinstance(data, dict):
            logger.warning("Legacy stats %s must contain a JSON object", self.legacy_stats_file)
            return

        with self._lock, self._connection:
            for raw_user_id, item in data.items():
                if not isinstance(item, dict):
                    logger.warning("Skipping malformed legacy stats for %s", raw_user_id)
                    continue
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
            self._connection.execute(
                "INSERT OR REPLACE INTO app_meta (key, value) VALUES ('legacy_stats_migrated', '1')"
            )

    def _mark_legacy_migrated(self) -> None:
        with self._lock, self._connection:
            self._connection.execute(
                "INSERT OR REPLACE INTO app_meta (key, value) VALUES ('legacy_stats_migrated', '1')"
            )

    def ensure_user(self, user_id: int) -> None:
        self.touch_user(user_id)

    def touch_user(self, user_id: int) -> None:
        if user_id <= 0:
            return
        now = _now()
        with self._lock, self._connection:
            self._connection.execute(
                """
                INSERT INTO users (user_id, first_seen, last_seen) VALUES (?, ?, ?)
                ON CONFLICT(user_id) DO UPDATE SET last_seen = excluded.last_seen, blocked_at = NULL
                """,
                (user_id, now, now),
            )

    def mark_blocked(self, user_ids: list[int]) -> None:
        if not user_ids:
            return
        now = _now()
        with self._lock, self._connection:
            self._connection.executemany(
                "UPDATE users SET blocked_at = ? WHERE user_id = ?",
                [(now, user_id) for user_id in user_ids],
            )

    def audience(self) -> list[int]:
        with self._lock:
            rows = self._connection.execute(
                "SELECT user_id FROM users WHERE blocked_at IS NULL ORDER BY last_seen DESC"
            ).fetchall()
        return [int(row[0]) for row in rows]

    def audience_summary(self) -> AudienceSummary:
        week_ago = datetime.fromtimestamp(time.time() - 7 * 86400, UTC).isoformat()
        day_ago = datetime.fromtimestamp(time.time() - 86400, UTC).isoformat()
        with self._lock:
            row = self._connection.execute(
                """
                SELECT COUNT(*),
                    SUM(blocked_at IS NULL),
                    SUM(blocked_at IS NOT NULL),
                    SUM(last_seen >= ?),
                    SUM(last_seen >= ?),
                    SUM(first_seen >= ?)
                FROM users
                """,
                (day_ago, week_ago, week_ago),
            ).fetchone()
        values = [int(value or 0) for value in row]
        return AudienceSummary(*values)

    def record_download(self, user_id: int, action: str, file_size_mb: float = 0.0) -> int:
        if user_id <= 0:
            return 0
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
            row = self._connection.execute(
                "SELECT downloads_count FROM user_stats WHERE user_id = ?", (user_id,)
            ).fetchone()
        return int(row[0]) if row else 0

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
            self._connection.execute(
                """
                UPDATE user_stats SET downloads_count=0, total_videos=0,
                    total_audios=0, total_other_downloads=0, failed_downloads=0,
                    total_size_mb=0, first_download_date=NULL, last_download_date=NULL
                """
            )

    def close(self) -> None:
        with self._lock:
            self._connection.close()
