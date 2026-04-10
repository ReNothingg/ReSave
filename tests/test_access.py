import unittest
from unittest.mock import patch

import config
from src.core import access


class AccessTests(unittest.TestCase):
    def test_free_user_duration_limit_error(self):
        with patch.object(config, "VIP_USERS", ()), patch.object(config, "ADMIN_IDS", ()):
            error = access.build_duration_limit_error(123, 901)

        self.assertIn("Максимум 15 минут", error)

    def test_premium_user_has_extended_limits(self):
        with patch.object(config, "VIP_USERS", (42,)), patch.object(config, "ADMIN_IDS", ()):
            limits = access.get_user_limits(42)

        self.assertEqual(limits.tier, "premium")
        self.assertEqual(limits.max_video_duration, config.MAX_VIDEO_DURATION["premium"])

    def test_playlist_limit_error_for_free_user(self):
        with patch.object(config, "VIP_USERS", ()), patch.object(config, "ADMIN_IDS", ()):
            error = access.build_playlist_limit_error(100, 3)

        self.assertIn("premium", error.lower())


if __name__ == "__main__":
    unittest.main()
