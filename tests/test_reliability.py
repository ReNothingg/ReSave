import asyncio
import multiprocessing
import os
import time
from threading import Event
from unittest.mock import AsyncMock

import pytest
from aiogram.exceptions import TelegramNetworkError, TelegramRetryAfter
from aiogram.methods import SendMessage

from src.core.media_downloader import DownloadCancelled
from src.core.process_runner import run_isolated
from src.core.telegram_gateway import TelegramGateway
from src.core.video_info import VideoInfoError, VideoInfoService


@pytest.mark.parametrize(
    ("target", "allowed"),
    [
        ("https://www.tiktok.com/@user/photo/1234567890123", True),
        ("https://127.0.0.1/private", False),
        ("https://tiktok.com.evil.example/video/123", False),
        ("http://www.tiktok.com/video/123", False),
    ],
)
def test_tiktok_redirect_is_validated_before_request(tmp_path, monkeypatch, target, allowed):
    from unittest.mock import MagicMock

    redirect = MagicMock(status=302, headers={"Location": target})
    final = MagicMock(status=200)
    session = MagicMock()
    session.get.side_effect = [
        AsyncMock(__aenter__=AsyncMock(return_value=response)) for response in (redirect, final)
    ]
    monkeypatch.setattr(
        "aiohttp.ClientSession",
        lambda: AsyncMock(__aenter__=AsyncMock(return_value=session)),
    )
    service = VideoInfoService(tmp_path / "cookies.txt")
    if allowed:
        assert asyncio.run(service.resolve_url("https://vt.tiktok.com/example/")) == target
        assert session.get.call_count == 2
    else:
        with pytest.raises(VideoInfoError):
            asyncio.run(service.resolve_url("https://vt.tiktok.com/example/"))
        assert session.get.call_count == 1


def test_crashed_worker_does_not_poison_next_job():
    async def scenario():
        with pytest.raises(RuntimeError, match="worker exited"):
            await run_isolated(os._exit, 9, timeout=10)
        assert await run_isolated(sum, [3, 4], timeout=10) == 7

    before = {p.pid for p in multiprocessing.active_children()}
    asyncio.run(scenario())
    assert {p.pid for p in multiprocessing.active_children()} == before


def test_low_disk_space_stops_worker(tmp_path, monkeypatch):
    from collections import namedtuple

    usage = namedtuple("usage", "total used free")
    monkeypatch.setattr("shutil.disk_usage", lambda path: usage(100, 99, 1))
    with pytest.raises(OSError, match="No space"):
        asyncio.run(run_isolated(time.sleep, 30, timeout=10, work_dir=tmp_path, max_bytes=100))


def test_isolated_worker_returns_result_and_is_reaped():
    before = {p.pid for p in multiprocessing.active_children()}
    assert asyncio.run(run_isolated(sum, [1, 2, 3], timeout=10)) == 6
    assert {p.pid for p in multiprocessing.active_children()} == before


def test_hung_worker_has_real_deadline():
    before = {p.pid for p in multiprocessing.active_children()}
    start = time.monotonic()
    with pytest.raises(TimeoutError):
        asyncio.run(run_isolated(time.sleep, 30, timeout=0.5))
    assert time.monotonic() - start < 5
    assert {p.pid for p in multiprocessing.active_children()} == before


def test_user_cancel_stops_worker():
    async def scenario():
        cancelled = Event()
        task = asyncio.create_task(run_isolated(time.sleep, 30, timeout=10, cancel_event=cancelled))
        await asyncio.sleep(0.3)
        cancelled.set()
        with pytest.raises(DownloadCancelled):
            await task

    before = {p.pid for p in multiprocessing.active_children()}
    asyncio.run(scenario())
    assert {p.pid for p in multiprocessing.active_children()} == before


def test_network_error_does_not_duplicate_send():
    operation = AsyncMock(
        side_effect=TelegramNetworkError(
            method=SendMessage(chat_id=1, text="test"), message="response lost"
        )
    )
    gateway = TelegramGateway(None)
    with pytest.raises(TelegramNetworkError):
        asyncio.run(gateway._retry(operation))
    assert operation.await_count == 1


def test_rate_limit_is_safe_to_retry(monkeypatch):
    operation = AsyncMock(
        side_effect=[
            TelegramRetryAfter(
                method=SendMessage(chat_id=1, text="test"), message="slow down", retry_after=1
            ),
            "ok",
        ]
    )
    monkeypatch.setattr(asyncio, "sleep", AsyncMock())
    assert asyncio.run(TelegramGateway(None)._retry(operation)) == "ok"
    assert operation.await_count == 2
