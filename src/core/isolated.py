from __future__ import annotations

import asyncio
import contextlib
import json
import logging
import os
import signal
import sys
import threading
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any

from .errors import DownloadCancelled, FileTooLarge

logger = logging.getLogger(__name__)
PROJECT_ROOT = Path(__file__).resolve().parents[2]


class JobFailed(RuntimeError):
    pass


def worker_env(extra_path: Path | None = None) -> dict[str, str]:
    env = dict(os.environ)
    paths = [item for item in env.get("PYTHONPATH", "").split(os.pathsep) if item]
    if extra_path is not None:
        paths.insert(0, str(extra_path))
    if str(PROJECT_ROOT) not in paths:
        paths.append(str(PROJECT_ROOT))
    env["PYTHONPATH"] = os.pathsep.join(paths)
    env["PYTHONUNBUFFERED"] = "1"
    env.setdefault("MALLOC_ARENA_MAX", "2")
    return env


def _kill(process: asyncio.subprocess.Process) -> None:
    if process.returncode is not None:
        return
    with contextlib.suppress(ProcessLookupError, PermissionError):
        os.killpg(process.pid, signal.SIGKILL)
    with contextlib.suppress(ProcessLookupError):
        process.kill()


async def _read_events(stream: asyncio.StreamReader, queue: asyncio.Queue) -> None:
    while line := await stream.readline():
        try:
            event = json.loads(line)
        except ValueError:
            continue
        if isinstance(event, dict):
            await queue.put(event)
    await queue.put(None)


async def _read_tail(stream: asyncio.StreamReader, buffer: bytearray, limit: int = 8192) -> None:
    while chunk := await stream.read(4096):
        buffer.extend(chunk)
        if len(buffer) > limit:
            del buffer[:-limit]


def _failure(event: dict[str, Any]) -> Exception:
    message = str(event.get("message") or "неизвестная ошибка")
    kind = event.get("kind")
    if kind == "too_large":
        return FileTooLarge(message)
    if kind == "not_found":
        return FileNotFoundError(message)
    return JobFailed(message)


class _Watch:
    def __init__(
        self,
        deadline: float,
        stall_timeout: float | None,
        cancel_event: threading.Event | None,
    ):
        self.started = time.monotonic()
        self.deadline = deadline
        self.stall_timeout = stall_timeout
        self.cancel_event = cancel_event
        self.last_activity = self.started
        self.stall_enabled = stall_timeout is not None

    def check(self) -> None:
        if self.cancel_event is not None and self.cancel_event.is_set():
            raise DownloadCancelled("Загрузка отменена пользователем")
        now = time.monotonic()
        if now - self.started >= self.deadline:
            raise TimeoutError("Download timeout")
        if (
            self.stall_enabled
            and self.stall_timeout
            and now - self.last_activity >= self.stall_timeout
        ):
            raise TimeoutError("Download made no progress")

    def observe(self, event: dict[str, Any]) -> None:
        self.last_activity = time.monotonic()
        kind = event.get("event")
        if kind == "phase" and event.get("stage") == "postprocess":
            self.stall_enabled = False
        elif kind == "progress":
            self.stall_enabled = self.stall_timeout is not None


async def _spawn(job: dict[str, Any]) -> asyncio.subprocess.Process:
    process = await asyncio.create_subprocess_exec(
        sys.executable,
        "-m",
        "src.core.ytdlp_worker",
        stdin=asyncio.subprocess.PIPE,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
        cwd=PROJECT_ROOT,
        env=worker_env(),
        start_new_session=True,
        limit=4 * 1024 * 1024,
    )
    assert process.stdin is not None
    process.stdin.write(json.dumps(job, ensure_ascii=False).encode())
    await process.stdin.drain()
    process.stdin.close()
    return process


async def run_job(
    job: dict[str, Any],
    *,
    deadline: float,
    stall_timeout: float | None = None,
    cancel_event: threading.Event | None = None,
    on_event: Callable[[dict[str, Any]], None] | None = None,
) -> dict[str, Any]:
    watch = _Watch(deadline, stall_timeout, cancel_event)
    watch.check()
    process = await _spawn(job)
    assert process.stdout is not None and process.stderr is not None
    events: asyncio.Queue[dict[str, Any] | None] = asyncio.Queue()
    stderr = bytearray()
    readers = [
        asyncio.create_task(_read_events(process.stdout, events)),
        asyncio.create_task(_read_tail(process.stderr, stderr)),
    ]
    try:
        while True:
            watch.check()
            try:
                event = await asyncio.wait_for(events.get(), timeout=0.5)
            except TimeoutError:
                continue
            if event is None:
                break
            watch.observe(event)
            if event.get("event") == "result":
                return event
            if event.get("event") == "error":
                raise _failure(event)
            if on_event is not None:
                on_event(event)
        await asyncio.wait_for(process.wait(), timeout=10)
        detail = stderr.decode("utf-8", errors="replace").strip()[-500:]
        raise JobFailed(
            f"worker exited with code {process.returncode}" + (f": {detail}" if detail else "")
        )
    finally:
        _kill(process)
        with contextlib.suppress(Exception):
            await asyncio.wait_for(process.wait(), timeout=10)
        for reader in readers:
            reader.cancel()
        await asyncio.gather(*readers, return_exceptions=True)
