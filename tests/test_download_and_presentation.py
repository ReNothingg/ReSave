from dataclasses import replace
from pathlib import Path

import config
from src.core import media_downloader
from src.core.media_downloader import MediaDownloader
from src.core.models import DownloadAction, DownloadTask
from src.handlers.command_handlers import _main_keyboard
from src.handlers.download_handlers import _keyboard
from src.utils.presentation import panel, rich_panel, user_error


def _settings(tmp_path: Path):
    return replace(
        config.SETTINGS,
        temp_dir=tmp_path,
        cookies_file=tmp_path / "missing-cookies.txt",
    )


def test_downloader_uses_ffmpeg_for_hls_and_ipv4(tmp_path, monkeypatch):
    monkeypatch.setattr(
        media_downloader,
        "_media_tool",
        lambda name: f"/tools/{name}",
    )

    options = MediaDownloader(_settings(tmp_path))._common_options()

    assert options["source_address"] == "0.0.0.0"
    assert options["external_downloader"] == {"m3u8": "ffmpeg"}
    assert "User-Agent" not in options["http_headers"]


def test_video_variants_end_with_compatible_fallback(tmp_path, monkeypatch):
    monkeypatch.setattr(
        media_downloader,
        "_media_tool",
        lambda name: f"/tools/{name}",
    )
    task = DownloadTask(
        url="https://example.com/video",
        chat_id=1,
        user_id=1,
        status_message_id=1,
        reply_to_message_id=None,
        info={},
        action=DownloadAction.RESOLUTION,
        requested_height=414,
    )

    variants = MediaDownloader(_settings(tmp_path)).variants(task, tmp_path / "media")

    assert variants[0].label == "414p"
    assert variants[-1].format_selector == "bv*+ba/b"


def test_rich_and_classic_panels_escape_user_content():
    classic = panel("<title>", ["A & B"], icon="⚡")
    rich = rich_panel(
        "<title>",
        lead="A & B",
        sections=(("Formats", ("<video>",)),),
    )

    assert "&lt;title&gt;" in classic
    assert "A &amp; B" in classic
    assert "<h1>&lt;title&gt;</h1>" in rich
    assert "<li>&lt;video&gt;</li>" in rich


def test_user_errors_explain_common_downloader_failures():
    assert "Источник отклонил" in user_error(RuntimeError("HTTP Error 403: Forbidden"))
    assert "качество недоступно" in user_error(RuntimeError("Requested format is not available"))


def test_keyboards_use_native_button_styles():
    media = _keyboard("token", {"thumbnail": "url"}, [1080])
    styles = {button.text: button.style for row in media.inline_keyboard for button in row}
    menu_styles = {
        button.text: button.style for row in _main_keyboard().inline_keyboard for button in row
    }

    assert styles["🎥 1080p"] == "primary"
    assert styles["🎬 Лучшее"] == "success"
    assert styles["✕ Отмена"] == "danger"
    assert menu_styles["📊 Моя статистика"] == "success"
