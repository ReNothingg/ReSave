from __future__ import annotations

import asyncio
import tempfile
import time
import unittest
from pathlib import Path

from aiogram.types import InlineQueryResultCachedVideo

from src.core.inline_media import (
    InlineMediaRecord,
    InlineMediaService,
    InlineMediaStore,
    canonicalize_inline_url,
    inline_cache_key,
)
from src.handlers.inline_handlers import build_cached_video_result


def make_record(url: str, *, updated_at: float | None = None) -> InlineMediaRecord:
    normalized = canonicalize_inline_url(url)
    return InlineMediaRecord(
        cache_key=inline_cache_key(normalized),
        url=normalized,
        file_id="telegram-file-id",
        title="Example video",
        uploader="Uploader",
        duration=65,
        width=1280,
        height=720,
        file_size=1024,
        updated_at=updated_at or time.time(),
    )


class InlineMediaStoreTests(unittest.TestCase):
    def test_canonical_url_removes_tracking_and_fragment(self):
        self.assertEqual(
            canonicalize_inline_url(
                "HTTPS://YOUTU.BE/abc/?si=tracking&utm_source=test&t=10#fragment"
            ),
            "https://youtu.be/abc?t=10",
        )

    def test_canonical_url_keeps_unknown_query_parameters(self):
        self.assertEqual(
            canonicalize_inline_url("https://example.com/video?si=required&utm_medium=share"),
            "https://example.com/video?si=required",
        )

    def test_ready_cache_survives_store_reopen(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "stats.db"
            first = InlineMediaStore(path)
            first.save(make_record("https://example.com/video"))
            first.close()

            second = InlineMediaStore(path)
            record = second.get("https://example.com/video/")
            self.assertIsNotNone(record)
            self.assertEqual(record.file_id, "telegram-file-id")
            second.close()

    def test_request_token_survives_store_reopen(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "stats.db"
            first = InlineMediaStore(path)
            token = first.create_request("https://example.com/video", 42)
            first.close()

            second = InlineMediaStore(path)
            self.assertEqual(
                second.resolve_request(token),
                ("https://example.com/video", 42),
            )
            second.close()

    def test_expired_cache_is_deleted(self):
        with tempfile.TemporaryDirectory() as directory:
            store = InlineMediaStore(Path(directory) / "stats.db")
            store.save(
                make_record(
                    "https://example.com/old",
                    updated_at=time.time() - 120,
                )
            )
            self.assertIsNone(store.get("https://example.com/old", max_age=60))
            self.assertIsNone(store.get("https://example.com/old"))
            store.close()

    def test_cached_result_uses_telegram_file_id(self):
        result = build_cached_video_result(make_record("https://example.com/video"))
        self.assertIsInstance(result, InlineQueryResultCachedVideo)
        self.assertEqual(result.video_file_id, "telegram-file-id")
        self.assertIn("720p", result.description)


class FakeInlineMediaService(InlineMediaService):
    def __init__(self, store: InlineMediaStore, temp_dir: str):
        super().__init__(
            sync_bot=None,
            store=store,
            cache_chat_id=1,
            temp_dir=temp_dir,
            max_concurrent=1,
        )
        self.prepare_calls = 0

    def _prepare_sync(self, url: str, user_id: int) -> InlineMediaRecord:
        self.prepare_calls += 1
        time.sleep(0.05)
        return make_record(url)


class FailingInlineMediaService(FakeInlineMediaService):
    def _prepare_sync(self, url: str, user_id: int) -> InlineMediaRecord:
        self.prepare_calls += 1
        raise RuntimeError("expected failure")


class InlineMediaServiceTests(unittest.IsolatedAsyncioTestCase):
    async def test_duplicate_requests_share_one_background_job(self):
        with tempfile.TemporaryDirectory() as directory:
            store = InlineMediaStore(Path(directory) / "stats.db")
            service = FakeInlineMediaService(store, directory)
            url = "https://example.com/video?utm_source=one"

            self.assertEqual(service.ensure_started(url, 42), "preparing")
            self.assertEqual(service.ensure_started(url, 42), "preparing")
            record = await service.wait_until_ready(url, 42, timeout=2)

            self.assertIsNotNone(record)
            self.assertEqual(service.prepare_calls, 1)
            self.assertEqual(service.get_state(url).status, "ready")
            store.close()

    async def test_failure_is_throttled_until_forced(self):
        with tempfile.TemporaryDirectory() as directory:
            store = InlineMediaStore(Path(directory) / "stats.db")
            service = FailingInlineMediaService(store, directory)
            url = "https://example.com/broken"

            service.ensure_started(url, 42)
            self.assertIsNone(await service.wait_until_ready(url, 42, timeout=1))
            self.assertEqual(service.get_state(url).status, "error")
            self.assertEqual(service.ensure_started(url, 42), "error")
            self.assertEqual(service.prepare_calls, 1)

            self.assertEqual(service.ensure_started(url, 42, force=True), "preparing")
            self.assertIsNone(await service.wait_until_ready(url, 42, timeout=1))
            self.assertEqual(service.prepare_calls, 2)
            store.close()


if __name__ == "__main__":
    unittest.main()
