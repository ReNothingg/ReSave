from __future__ import annotations

import subprocess
import time
from threading import Event

from .errors import DownloadCancelled


def run_command(
    command: list[str],
    *,
    deadline_seconds: float,
    cancel_event: Event | None = None,
) -> subprocess.CompletedProcess[str]:
    if cancel_event is not None and cancel_event.is_set():
        raise DownloadCancelled("Загрузка отменена")
    deadline = time.monotonic() + deadline_seconds
    with subprocess.Popen(
        command,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    ) as process:
        try:
            while True:
                if cancel_event is not None and cancel_event.is_set():
                    raise DownloadCancelled("Загрузка отменена")
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise TimeoutError("Media operation timeout")
                try:
                    stdout, stderr = process.communicate(timeout=min(0.25, remaining))
                    return subprocess.CompletedProcess(command, process.returncode, stdout, stderr)
                except subprocess.TimeoutExpired:
                    continue
        finally:
            if process.poll() is None:
                process.terminate()
                try:
                    process.communicate(timeout=2)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.communicate()
