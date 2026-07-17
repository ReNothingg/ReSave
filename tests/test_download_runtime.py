import threading
from types import SimpleNamespace

import config

from src.core import download_handler
from src.core.download_handler import (
    DownloadVariant,
    _base_ydl_params,
    _download_with_timeout,
)


def test_download_runtime_is_bounded_for_shared_hosting():
    variant = DownloadVariant(
        label="1080p",
        format="bv*[height=1080]+ba/b[height=1080]",
        postprocessors=(),
        output_template="/tmp/media.%(ext)s",
    )

    params = _base_ydl_params(variant)

    assert params["noprogress"] is True
    assert params["buffersize"] == 64 * 1024
    assert params["noresizebuffer"] is True
    if config.DOWNLOAD_RATE_LIMIT_BYTES > 0:
        assert params["ratelimit"] == config.DOWNLOAD_RATE_LIMIT_BYTES


def test_download_stall_timeout_is_shorter_than_total_timeout():
    assert 30 <= config.DOWNLOAD_STALL_TIMEOUT_SECONDS
    assert config.DOWNLOAD_STALL_TIMEOUT_SECONDS < config.DOWNLOAD_TIMEOUT_SECONDS


def test_download_runs_inline_and_reports_progress(monkeypatch):
    observed = {}

    class FakeYoutubeDL:
        def __init__(self, params):
            observed["params"] = params

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

        def download(self, urls):
            observed["urls"] = urls
            hook = observed["params"]["progress_hooks"][0]
            hook({"status": "downloading", "downloaded_bytes": 50, "total_bytes": 100})
            hook({"status": "finished"})

    monkeypatch.setattr(download_handler.yt_dlp, "YoutubeDL", FakeYoutubeDL)
    task = SimpleNamespace(cancel_event=threading.Event(), progress=0.0)

    _download_with_timeout("https://example.com/video", {}, 60, task)

    assert observed["urls"] == ["https://example.com/video"]
    assert task.progress == 1.0


def test_cancelled_inline_download_stops_from_progress_hook(monkeypatch):
    class FakeYoutubeDL:
        def __init__(self, params):
            self.params = params

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

        def download(self, _urls):
            self.params["progress_hooks"][0]({"status": "downloading"})

    monkeypatch.setattr(download_handler.yt_dlp, "YoutubeDL", FakeYoutubeDL)
    cancel_event = threading.Event()
    cancel_event.set()
    task = SimpleNamespace(cancel_event=cancel_event, progress=0.0)

    try:
        _download_with_timeout("https://example.com/video", {}, 60, task)
    except RuntimeError as exc:
        assert "отменена" in str(exc)
    else:
        raise AssertionError("cancelled download must stop")
