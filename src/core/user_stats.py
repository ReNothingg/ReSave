import json
import os
import threading
from datetime import datetime
from pathlib import Path
import logging

logger = logging.getLogger(__name__)


class UserStats:
    def __init__(self, user_id: int):
        self.user_id = user_id
        self.downloads_count = 0
        self.total_videos = 0
        self.total_audios = 0
        self.failed_downloads = 0
        self.total_size_mb = 0.0
        self.first_download_date = None
        self.last_download_date = None

    def to_dict(self):
        return {
            'user_id': self.user_id,
            'downloads_count': self.downloads_count,
            'total_videos': self.total_videos,
            'total_audios': self.total_audios,
            'failed_downloads': self.failed_downloads,
            'total_size_mb': round(self.total_size_mb, 2),
            'first_download_date': self.first_download_date,
            'last_download_date': self.last_download_date
        }

    @classmethod
    def from_dict(cls, data):
        stats = cls(data['user_id'])
        stats.downloads_count = data.get('downloads_count', 0)
        stats.total_videos = data.get('total_videos', 0)
        stats.total_audios = data.get('total_audios', 0)
        stats.failed_downloads = data.get('failed_downloads', 0)
        stats.total_size_mb = data.get('total_size_mb', 0.0)
        stats.first_download_date = data.get('first_download_date')
        stats.last_download_date = data.get('last_download_date')
        return stats

class UserStatsManager:
    def __init__(self, stats_file='user_stats.json'):
        self.stats_file = stats_file
        self.stats = {}
        self.lock = threading.Lock()
        self._load_stats()

    def _load_stats(self):
        try:
            if os.path.exists(self.stats_file):
                with open(self.stats_file, 'r', encoding='utf-8') as f:
                    data = json.load(f)
                    for user_id_str, stats_data in data.items():
                        user_id = int(user_id_str)
                        self.stats[user_id] = UserStats.from_dict(stats_data)
                logger.info(f"Загружена статистика {len(self.stats)} пользователей")
        except Exception as e:
            logger.error(f"Ошибка при загрузке статистики: {e}")

    def _save_stats(self):
        try:
            with self.lock:
                data = {str(uid): stats.to_dict() for uid, stats in self.stats.items()}
                with open(self.stats_file, 'w', encoding='utf-8') as f:
                    json.dump(data, f, indent=2, ensure_ascii=False)
        except Exception as e:
            logger.error(f"Ошибка при сохранении статистики: {e}")

    def get_user_stats(self, user_id: int) -> UserStats:
        with self.lock:
            if user_id not in self.stats:
                self.stats[user_id] = UserStats(user_id)
            return self.stats[user_id]

    def record_download(self, user_id: int, action: str, file_size_mb: float = 0.0):
        stats = self.get_user_stats(user_id)

        with self.lock:
            stats.downloads_count += 1

            if action == "video":
                stats.total_videos += 1
            elif action == "audio":
                stats.total_audios += 1

            stats.total_size_mb += file_size_mb
            stats.last_download_date = datetime.now().isoformat()

            if stats.first_download_date is None:
                stats.first_download_date = datetime.now().isoformat()

        self._save_stats()

    def record_failed_download(self, user_id: int):
        stats = self.get_user_stats(user_id)

        with self.lock:
            stats.failed_downloads += 1

        self._save_stats()

    def get_all_stats(self):
        with self.lock:
            return dict(self.stats)

_stats_manager = None


def get_stats_manager():
    global _stats_manager
    if _stats_manager is None:
        _stats_manager = UserStatsManager()
    return _stats_manager


def set_stats_manager(manager):
    global _stats_manager
    _stats_manager = manager
