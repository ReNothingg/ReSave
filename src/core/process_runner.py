"""Disposable workers: cancellation and deadlines also stop ffmpeg/gallery-dl children."""

from __future__ import annotations

import asyncio
import multiprocessing
import os
import shutil
import signal
import time
from collections.abc import Callable
from typing import Any

_progress_connection = None
_last_progress = 0.0


def report_progress(value):
    global _last_progress
    if _progress_connection is not None and time.monotonic() - _last_progress >= 0.5:
        _last_progress = time.monotonic()
        _progress_connection.send(("progress", value))


def _entry(connection, function, args):
    global _progress_connection
    _progress_connection = connection
    if os.name == "posix":
        os.setsid()
    connection.send(("ready", None))
    try:
        connection.send(("result", function(*args)))
    except Exception as exc:
        # Do not pickle extractor exceptions with unpicklable response objects.
        connection.send(("error", (type(exc).__name__, str(exc))))
    finally:
        # Keep session leader alive until the parent cleans up its process group.
        # This also avoids signalling an already-reaped/reused PID on macOS.
        try:
            connection.recv()
        except EOFError:
            pass
        connection.close()


async def run_isolated(
    function: Callable,
    *args: Any,
    timeout: float,  # noqa: ASYNC109 -- wall deadline must also reap the OS process group
    cancel_event=None,
    work_dir=None,
    max_bytes: int | None = None,
    on_progress=None,
):
    from .media_downloader import DownloadCancelled, FileTooLarge

    if cancel_event and cancel_event.is_set():
        raise DownloadCancelled("Загрузка отменена")
    context = multiprocessing.get_context("spawn")
    receiver, sender = context.Pipe(duplex=True)
    process = context.Process(target=_entry, args=(sender, function, args))
    process.start()
    sender.close()
    ready = False
    deadline = time.monotonic() + timeout
    next_size_check = 0.0
    try:
        while True:
            if cancel_event and cancel_event.is_set():
                raise DownloadCancelled("Загрузка отменена")
            now = time.monotonic()
            if now >= deadline:
                raise TimeoutError("Media operation timeout")
            if work_dir and max_bytes and now >= next_size_check:
                next_size_check = now + 1
                if shutil.disk_usage(work_dir).free < 64 * 1024 * 1024:
                    raise OSError("No space left for media processing")
                size = 0
                for path in work_dir.rglob("*"):
                    try:
                        if path.is_file():
                            size += path.stat().st_size
                    except FileNotFoundError:
                        pass
                # Allow input + output during merging, but bound total disk use.
                if size > max_bytes * 3:
                    raise FileTooLarge("Temporary file size exceeds processing limit")
            if receiver.poll():
                try:
                    kind, value = receiver.recv()
                except EOFError as exc:
                    raise RuntimeError("Media worker exited without a result") from exc
                if kind == "ready":
                    ready = True
                elif kind == "result":
                    return value
                elif kind == "progress":
                    if on_progress:
                        on_progress(value)
                else:
                    name, message = value
                    errors = {
                        "DownloadCancelled": DownloadCancelled,
                        "FileTooLarge": FileTooLarge,
                        "TimeoutError": TimeoutError,
                    }
                    raise errors.get(name, RuntimeError)(message)
            elif not process.is_alive():
                raise RuntimeError(f"Media worker exited with code {process.exitcode}")
            await asyncio.sleep(0.1)
    finally:
        # Kill the whole private session, including orphaned ffmpeg processes.
        if ready and os.name == "posix":
            try:
                os.killpg(process.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
            except PermissionError:
                # macOS can report EPERM for the group of an already-dead
                # session leader. Never hide a permission failure for a live worker.
                if process.is_alive():
                    raise
        elif process.is_alive():
            process.kill()
        process.join(timeout=2)
        receiver.close()
        if not process.is_alive():
            process.close()
