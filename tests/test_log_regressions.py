from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from aiogram.types import FSInputFile

from src.core.download_handler import _get_format_options
from src.core.tiktok_photo_handler import is_tiktok_photo_url
from src.utils.aiogram_bot_adapter import AiogramSyncBotAdapter
from src.utils.url_validator import URLValidator
from main import acquire_instance_lock


class LogRegressionTests(unittest.TestCase):
    def test_plain_text_is_not_promoted_to_url(self):
        validator = URLValidator()
        for value in (".", "Заебут...", "34", "обычный текст Powerd by @ReSafeBot"):
            with self.subTest(value=value):
                self.assertIsNone(validator.extract_url(value))
                self.assertFalse(validator.validate(value)[0])

    def test_url_is_extracted_from_message(self):
        validator = URLValidator()
        self.assertEqual(
            validator.extract_url("Посмотреть: https://youtu.be/abc123!"),
            "https://youtu.be/abc123",
        )

    def test_gif_format_has_generic_fallback(self):
        task = type("Task", (), {"action": "gif", "format_param": None})()
        format_selector, _, _ = _get_format_options(task, "/tmp/media")
        self.assertIn("/best", format_selector)

    def test_tiktok_photo_detection(self):
        self.assertTrue(
            is_tiktok_photo_url("https://www.tiktok.com/@user/photo/7635293174612856082")
        )
        self.assertFalse(
            is_tiktok_photo_url("https://www.tiktok.com/@user/video/7635293174612856082")
        )

    def test_file_is_uploaded_instead_of_sent_as_local_uri(self):
        adapter = object.__new__(AiogramSyncBotAdapter)
        with tempfile.NamedTemporaryFile() as file_obj:
            prepared = adapter._prepare_file(file_obj.name)
        self.assertIsInstance(prepared, FSInputFile)

    def test_second_bot_instance_is_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            lock_path = Path(directory) / "bot.lock"
            first_lock = acquire_instance_lock(lock_path)
            self.assertIsNotNone(first_lock)
            try:
                self.assertIsNone(acquire_instance_lock(lock_path))
            finally:
                first_lock.close()


if __name__ == "__main__":
    unittest.main()
