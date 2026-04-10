import json
import tempfile
import unittest
from pathlib import Path

from src.core.user_stats import UserStatsManager


class UserStatsTests(unittest.TestCase):
    def test_record_download_persists_to_sqlite(self):
        with tempfile.TemporaryDirectory() as tmp_dir:
            db_path = Path(tmp_dir) / "stats.sqlite3"
            manager = UserStatsManager(db_path=str(db_path))
            manager.record_download(123, action="video", file_size_mb=12.5)

            reloaded_manager = UserStatsManager(db_path=str(db_path))
            stats = reloaded_manager.get_user_stats(123)

        self.assertEqual(stats.downloads_count, 1)
        self.assertEqual(stats.total_videos, 1)
        self.assertEqual(stats.total_size_mb, 12.5)

    def test_other_download_type_is_tracked(self):
        with tempfile.TemporaryDirectory() as tmp_dir:
            db_path = Path(tmp_dir) / "stats.sqlite3"
            manager = UserStatsManager(db_path=str(db_path))
            manager.record_download(321, action="thumbnail", file_size_mb=1.2)
            manager.record_failed_download(321)

            stats = manager.get_user_stats(321)

        self.assertEqual(stats.total_other_downloads, 1)
        self.assertEqual(stats.failed_downloads, 1)

    def test_migrates_legacy_json_if_database_is_empty(self):
        with tempfile.TemporaryDirectory() as tmp_dir:
            legacy_path = Path(tmp_dir) / "user_stats.json"
            legacy_path.write_text(
                json.dumps(
                    {
                        "777": {
                            "user_id": 777,
                            "downloads_count": 2,
                            "total_videos": 1,
                            "total_audios": 1,
                            "failed_downloads": 0,
                            "total_size_mb": 9.5,
                            "first_download_date": "2025-01-01T10:00:00",
                            "last_download_date": "2025-01-02T10:00:00",
                        }
                    }
                ),
                encoding="utf-8",
            )

            manager = UserStatsManager(
                db_path=str(Path(tmp_dir) / "stats.sqlite3"),
                legacy_stats_file=str(legacy_path),
            )
            stats = manager.get_user_stats(777)

        self.assertEqual(stats.downloads_count, 2)
        self.assertEqual(stats.total_videos, 1)
        self.assertEqual(stats.total_audios, 1)


if __name__ == "__main__":
    unittest.main()
